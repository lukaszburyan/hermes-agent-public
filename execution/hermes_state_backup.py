#!/usr/bin/env python3
"""Selective encrypted backup and fail-closed restore for Hermes RFQ state."""

from __future__ import annotations

import argparse
from fnmatch import fnmatch
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class BackupError(RuntimeError):
    pass


@dataclass(frozen=True)
class StateSpec:
    name: str
    relative_path: str
    kind: str
    required: bool = False


STATE_SPECS = (
    StateSpec("mail_state", "rfq-state/state.sqlite3", "sqlite", True),
    StateSpec("unified_registry", ".tmp/orchesta-rfq-unified.sqlite3", "sqlite", True),
    StateSpec("sheets_state", ".tmp/google-sheets-leads-state.json", "json", True),
    StateSpec("sheets_previous", ".tmp/google-sheets-leads-state.json.previous", "json"),
    # Historical deployments use both JSON and SQLite behind this filename.
    StateSpec("mail_cursor", ".tmp/zoho_mail_poller_state.json", "sqlite_or_json"),
    StateSpec("source_health", ".tmp/orchesta-rfq-source-health.json", "json"),
    StateSpec("notification_ledger", ".tmp/orchesta-rfq-notification-ledger.json", "json"),
    StateSpec("notification_outbox", ".tmp/orchesta-rfq-notify-pending", "json_dir"),
)

RECONCILIATION_FLAG = "rfq-state/RESTORE_RECONCILIATION_REQUIRED"
RECONCILIATION_CHECKS = (
    "zoho_sent_reconciled",
    "zoho_drafts_reconciled",
    "google_sheets_reconciled",
    "recipient_bindings_reconciled",
    "lifecycle_inventory_reconciled",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fsync_path(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def sqlite_integrity(path: Path) -> None:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        result = connection.execute("PRAGMA integrity_check").fetchone()
    finally:
        connection.close()
    if not result or result[0] != "ok":
        raise BackupError(f"sqlite_integrity_failed:{path.name}")


def is_sqlite_file(path: Path) -> bool:
    with path.open("rb") as handle:
        return handle.read(16) == b"SQLite format 3\x00"


def copy_sqlite(source: Path, destination: Path) -> None:
    source_connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=30)
    destination_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(destination_connection)
        destination_connection.commit()
        integrity = destination_connection.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise BackupError(f"sqlite_integrity_failed:{destination.name}")
        # A source in WAL mode can transfer that persistent journal setting.
        # Normalize the offline snapshot so the encrypted archive is a complete,
        # standalone database rather than silently depending on -wal/-shm files.
        journal_mode = destination_connection.execute("PRAGMA journal_mode=DELETE").fetchone()
        if not journal_mode or str(journal_mode[0]).lower() != "delete":
            raise BackupError(f"sqlite_snapshot_not_standalone:{destination.name}")
    finally:
        destination_connection.close()
        source_connection.close()
    sqlite_integrity(destination)
    destination.chmod(0o600)
    fsync_path(destination)


def copy_json(source: Path, destination: Path) -> None:
    payload = json.loads(source.read_text(encoding="utf-8"))
    destination.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    destination.chmod(0o600)
    fsync_path(destination)


def manifest_for(payload_root: Path, metadata: dict[str, Any]) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    for path in sorted(payload_root.rglob("*")):
        if path.is_file() and path.name != "manifest.json":
            files.append({
                "path": path.relative_to(payload_root).as_posix(),
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
            })
    return {"schema_version": 1, "metadata": metadata, "files": files}


def write_json(path: Path, payload: dict[str, Any], mode: int = 0o600) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(mode)
    fsync_path(path)


def run_checked(command: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=True,
            text=True,
            input=input_text,
            capture_output=True,
        )
    except FileNotFoundError as exc:
        raise BackupError(f"required_command_missing:{command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "command_failed").strip().splitlines()[-1]
        raise BackupError(f"{command[0]}_failed:{detail[:200]}") from exc


def create_backup(
    *,
    state_root: Path,
    output: Path,
    recipient: str,
    commit: str,
    tag: str,
    image_digest: str,
    skill_version: str,
) -> dict[str, Any]:
    if not recipient.startswith("age1"):
        raise BackupError("invalid_age_recipient")
    if output.suffix != ".age":
        raise BackupError("backup_output_must_end_in_age")
    if output.exists():
        raise BackupError("backup_output_already_exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.parent.chmod(0o700)

    metadata = {
        "created_at": utc_now(),
        "commit": commit,
        "tag": tag,
        "image_digest": image_digest,
        "skill_version": skill_version,
        "state_schema": 1,
    }
    with tempfile.TemporaryDirectory(prefix="hermes-state-backup-") as temporary:
        temporary_root = Path(temporary)
        payload_root = temporary_root / "payload"
        payload_root.mkdir(mode=0o700)
        included: list[str] = []
        for spec in STATE_SPECS:
            source = state_root / spec.relative_path
            if not source.exists():
                if spec.required:
                    raise BackupError(f"required_state_missing:{spec.relative_path}")
                continue
            destination = payload_root / spec.relative_path
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if spec.kind == "sqlite":
                copy_sqlite(source, destination)
            elif spec.kind == "json":
                copy_json(source, destination)
            elif spec.kind == "sqlite_or_json":
                if is_sqlite_file(source):
                    copy_sqlite(source, destination)
                else:
                    copy_json(source, destination)
            elif spec.kind == "json_dir":
                if not source.is_dir() or source.is_symlink():
                    raise BackupError(f"invalid_state_directory:{spec.relative_path}")
                destination.mkdir(parents=True, exist_ok=True, mode=0o700)
                for item in sorted(source.iterdir()):
                    if item.is_symlink() or not item.is_file() or item.suffix.lower() != ".json":
                        raise BackupError(f"unexpected_state_file:{spec.relative_path}/{item.name}")
                    copy_json(item, destination / item.name)
            else:
                raise BackupError(f"unsupported_state_kind:{spec.kind}")
            included.append(spec.name)

        manifest = manifest_for(payload_root, metadata)
        write_json(payload_root / "manifest.json", manifest)
        fsync_directory(payload_root)

        archive = temporary_root / "hermes-state.tar.gz"
        with tarfile.open(archive, "w:gz") as bundle:
            bundle.add(payload_root, arcname="payload", recursive=True)
        archive.chmod(0o600)
        fsync_path(archive)

        encrypted_temp = output.with_name(f".{output.name}.partial")
        if encrypted_temp.exists():
            raise BackupError("partial_backup_already_exists")
        run_checked(["age", "--recipient", recipient, "--output", str(encrypted_temp), str(archive)])
        encrypted_temp.chmod(0o600)
        fsync_path(encrypted_temp)
        os.replace(encrypted_temp, output)
        fsync_directory(output.parent)

    encrypted_sha256 = sha256_file(output)
    checksum = output.with_name(output.name + ".sha256")
    checksum_temp = checksum.with_name(f".{checksum.name}.partial")
    checksum_temp.write_text(f"{encrypted_sha256}  {output.name}\n", encoding="ascii")
    checksum_temp.chmod(0o600)
    fsync_path(checksum_temp)
    os.replace(checksum_temp, checksum)
    fsync_directory(output.parent)

    evidence = {
        **metadata,
        "backup": str(output),
        "encrypted_sha256": encrypted_sha256,
        "encrypted_bytes": output.stat().st_size,
        "included": included,
        "offsite_verified": False,
    }
    write_json(output.with_name(output.name + ".metadata.json"), evidence)
    return evidence


def upload_offsite(*, backup: Path, remote_directory: str) -> dict[str, Any]:
    if not remote_directory or ":" not in remote_directory:
        raise BackupError("invalid_rclone_remote")
    checksum = backup.with_name(backup.name + ".sha256")
    metadata_path = backup.with_name(backup.name + ".metadata.json")
    for path in (backup, checksum, metadata_path):
        if not path.is_file():
            raise BackupError(f"offsite_source_missing:{path.name}")
    remote_root = remote_directory.rstrip("/")
    remote_backup = f"{remote_root}/{backup.name}"
    run_checked(["rclone", "copyto", str(backup), remote_backup])
    verification_method = "provider_hashsum"
    try:
        hash_output = run_checked(["rclone", "hashsum", "SHA-256", remote_backup]).stdout.split()
        if not hash_output:
            raise BackupError("offsite_checksum_missing")
        remote_hash = hash_output[0]
    except BackupError as hash_error:
        downloaded = subprocess.run(
            ["rclone", "cat", remote_backup],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if downloaded.returncode != 0:
            raise BackupError("offsite_checksum_unavailable") from hash_error
        remote_hash = hashlib.sha256(downloaded.stdout).hexdigest()
        verification_method = "download_and_local_sha256"
    local_hash = sha256_file(backup)
    if remote_hash.lower() != local_hash:
        raise BackupError("offsite_checksum_mismatch")
    run_checked(["rclone", "copyto", str(checksum), f"{remote_root}/{checksum.name}"])

    evidence = json.loads(metadata_path.read_text(encoding="utf-8"))
    evidence["offsite_verified"] = True
    evidence["offsite_remote"] = remote_backup
    evidence["offsite_verified_at"] = utc_now()
    evidence["offsite_verification_method"] = verification_method
    evidence["offsite_download_sha256"] = remote_hash.lower()
    write_json(metadata_path, evidence)
    run_checked(["rclone", "copyto", str(metadata_path), f"{remote_root}/{metadata_path.name}"])
    return evidence


def apply_offsite_retention(
    *,
    remote_directory: str,
    retention_days: int,
    deletion_approved: bool,
) -> dict[str, Any]:
    """Delete only expired Hermes encrypted-backup objects from a dedicated remote directory."""
    if retention_days < 1:
        raise BackupError("invalid_backup_retention_days")
    if not deletion_approved:
        raise BackupError("backup_retention_deletion_not_approved")
    remote_name, separator, remote_path = remote_directory.partition(":")
    if not separator or not remote_name.strip() or not remote_path.strip("/"):
        raise BackupError("backup_retention_requires_dedicated_remote_directory")
    remote_root = remote_directory.rstrip("/")
    patterns = (
        "/hermes-state-*.tar.gz.age",
        "/hermes-state-*.tar.gz.age.sha256",
        "/hermes-state-*.tar.gz.age.metadata.json",
    )
    command = ["rclone", "delete", remote_root, "--min-age", f"{retention_days}d"]
    for pattern in patterns:
        command.extend(["--filter", f"+ {pattern}"])
    command.extend(["--filter", "- *"])
    run_checked(command)
    return {
        "offsite_retention_days": retention_days,
        "offsite_retention_applied_at": utc_now(),
        "offsite_retention_action": "deleted_expired_objects",
        "offsite_retention_patterns": list(patterns),
    }


def evaluate_offsite_retention(*, remote_directory: str, retention_days: int) -> dict[str, Any]:
    """Inventory expired backup objects without deleting them."""
    if retention_days < 1:
        raise BackupError("invalid_backup_retention_days")
    remote_name, separator, remote_path = remote_directory.partition(":")
    if not separator or not remote_name.strip() or not remote_path.strip("/"):
        raise BackupError("backup_retention_requires_dedicated_remote_directory")
    remote_root = remote_directory.rstrip("/")
    patterns = (
        "/hermes-state-*.tar.gz.age",
        "/hermes-state-*.tar.gz.age.sha256",
        "/hermes-state-*.tar.gz.age.metadata.json",
    )
    command = [
        "rclone", "lsjson", remote_root, "--max-depth", "1", "--files-only",
        "--min-age", f"{retention_days}d",
    ]
    try:
        raw = json.loads(run_checked(command).stdout or "[]")
    except json.JSONDecodeError as exc:
        raise BackupError("backup_retention_inventory_invalid") from exc
    if not isinstance(raw, list):
        raise BackupError("backup_retention_inventory_invalid")
    candidate_names = (
        str(item.get("Path") or item.get("Name") or "").strip()
        for item in raw
        if isinstance(item, dict)
    )
    names = sorted(
        name for name in candidate_names
        if name and any(fnmatch(name, pattern.lstrip("/")) for pattern in patterns)
    )
    evaluated_at = utc_now()
    result: dict[str, Any] = {
        "offsite_retention_days": retention_days,
        "offsite_retention_evaluated_at": evaluated_at,
        "offsite_retention_patterns": list(patterns),
        "offsite_expired_objects": len(names),
        "offsite_expired_object_names": names,
        "offsite_retention_action": (
            "evaluated_no_expired_objects" if not names else "expired_objects_pending_approval"
        ),
    }
    if not names:
        result["offsite_retention_applied_at"] = evaluated_at
    return result


def _safe_extract(bundle: tarfile.TarFile, destination: Path) -> None:
    root = destination.resolve()
    for member in bundle.getmembers():
        if member.issym() or member.islnk() or member.isdev():
            raise BackupError(f"unsafe_archive_member:{member.name}")
        resolved = (destination / member.name).resolve()
        if resolved != root and root not in resolved.parents:
            raise BackupError(f"archive_path_escape:{member.name}")
    bundle.extractall(destination, filter="data")


def validate_payload(payload_root: Path) -> dict[str, Any]:
    manifest_path = payload_root / "manifest.json"
    if not manifest_path.is_file():
        raise BackupError("manifest_missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1 or not isinstance(manifest.get("files"), list):
        raise BackupError("manifest_invalid")
    for entry in manifest["files"]:
        relative = str(entry.get("path") or "")
        path = payload_root / relative
        if not path.is_file() or sha256_file(path) != entry.get("sha256") or path.stat().st_size != entry.get("bytes"):
            raise BackupError(f"manifest_mismatch:{relative}")
        if is_sqlite_file(path):
            sqlite_integrity(path)
        elif path.suffix == ".json":
            json.loads(path.read_text(encoding="utf-8"))
    return manifest


def decrypt_payload(*, backup: Path, identity: str, temporary_root: Path) -> tuple[Path, dict[str, Any]]:
    expected_checksum = backup.with_name(backup.name + ".sha256")
    if not expected_checksum.is_file():
        raise BackupError("encrypted_checksum_missing")
    expected = expected_checksum.read_text(encoding="ascii").split()[0]
    if sha256_file(backup) != expected:
        raise BackupError("encrypted_checksum_mismatch")
    archive = temporary_root / "hermes-state.tar.gz"
    run_checked(["age", "--decrypt", "--identity", "-", "--output", str(archive), str(backup)], input_text=identity)
    extracted = temporary_root / "extracted"
    extracted.mkdir(mode=0o700)
    with tarfile.open(archive, "r:gz") as bundle:
        _safe_extract(bundle, extracted)
    payload_root = extracted / "payload"
    manifest = validate_payload(payload_root)
    return payload_root, manifest


def verify_backup(*, backup: Path, identity: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="hermes-state-verify-") as temporary:
        _, manifest = decrypt_payload(backup=backup, identity=identity, temporary_root=Path(temporary))
        return manifest


def restore_backup(*, backup: Path, identity: str, target: Path) -> dict[str, Any]:
    if target.exists() and any(target.iterdir()):
        raise BackupError("restore_target_not_empty")
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix="hermes-state-restore-") as temporary:
        payload_root, manifest = decrypt_payload(backup=backup, identity=identity, temporary_root=Path(temporary))
        for source in sorted(payload_root.rglob("*")):
            if source.name == "manifest.json":
                continue
            relative = source.relative_to(payload_root)
            destination = target / relative
            if source.is_dir():
                destination.mkdir(parents=True, exist_ok=True, mode=0o700)
            elif source.is_file():
                destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                shutil.copy2(source, destination)
                destination.chmod(0o600)
                fsync_path(destination)
        flag = target / RECONCILIATION_FLAG
        flag.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        write_json(
            flag,
            {
                "status": "reconciliation_required",
                "restored_at": utc_now(),
                "backup_metadata": manifest.get("metadata") or {},
                "required_checks": list(RECONCILIATION_CHECKS),
            },
        )
        write_json(target / "restore-manifest.json", manifest)
        fsync_directory(target)
        return manifest


def acknowledge_restore(*, target: Path, actor: str, evidence: dict[str, Any]) -> dict[str, Any]:
    """Archive the fail-closed restore gate after explicit reconciliation evidence."""
    reviewer = str(actor or "").strip()
    if not reviewer:
        raise BackupError("reconciliation_actor_required")
    missing = [name for name in RECONCILIATION_CHECKS if evidence.get(name) is not True]
    if missing:
        raise BackupError("reconciliation_checks_missing:" + ",".join(missing))
    flag = target / RECONCILIATION_FLAG
    if not flag.is_file():
        raise BackupError("reconciliation_flag_missing")
    flag_payload = json.loads(flag.read_text(encoding="utf-8"))

    registry = target / ".tmp" / "orchesta-rfq-unified.sqlite3"
    sqlite_integrity(registry)
    connection = sqlite3.connect(f"file:{registry}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        unresolved = (
            int(connection.execute("SELECT COUNT(*) FROM unified_outbox WHERE status='outcome_unknown'").fetchone()[0])
            if "unified_outbox" in tables
            else -1
        )
        active_outbox = (
            int(connection.execute(
                "SELECT COUNT(*) FROM unified_outbox WHERE status IN ('claimed','in_progress','outcome_unknown')"
            ).fetchone()[0])
            if "unified_outbox" in tables else -1
        )
        artifact_creating = (
            int(connection.execute(
                "SELECT COUNT(*) FROM unified_artifacts WHERE status='creating'"
            ).fetchone()[0])
            if "unified_artifacts" in tables else -1
        )
        terminal_with_claim = (
            int(connection.execute(
                "SELECT COUNT(*) FROM unified_outbox WHERE status IN "
                "('sent','sent_manually','draft_created','validation_failed','manual_review','permanent_failed') "
                "AND (COALESCE(owner,'')!='' OR COALESCE(lease_expires_at,'')!='')"
            ).fetchone()[0])
            if "unified_outbox" in tables else -1
        )
        review_without_task = (
            int(connection.execute(
                "SELECT COUNT(*) FROM unified_deals d WHERE d.status='review_required' "
                "AND NOT EXISTS (SELECT 1 FROM unified_review_tasks t "
                "WHERE t.deal_id=d.deal_id AND t.status='open')"
            ).fetchone()[0])
            if {"unified_deals", "unified_review_tasks"}.issubset(tables) else -1
        )
    finally:
        connection.close()
    lifecycle_inventory = {
        "outcome_unknown": unresolved,
        "active_outbox": active_outbox,
        "artifact_creating": artifact_creating,
        "terminal_with_claim": terminal_with_claim,
        "review_without_task": review_without_task,
    }
    blockers = {name: count for name, count in lifecycle_inventory.items() if count != 0}
    if blockers:
        raise BackupError("reconciliation_lifecycle_blockers:" + json.dumps(blockers, sort_keys=True))

    sheets = target / ".tmp" / "google-sheets-leads-state.json"
    json.loads(sheets.read_text(encoding="utf-8"))
    acknowledged_at = utc_now()
    record = {
        **flag_payload,
        "status": "reconciled",
        "acknowledged_at": acknowledged_at,
        "acknowledged_by": reviewer,
        "evidence": evidence,
        "outcome_unknown_count": unresolved,
        "lifecycle_inventory": lifecycle_inventory,
    }
    temporary = flag.with_name(f".{flag.name}.reconciled.partial")
    write_json(temporary, record)
    os.replace(temporary, flag)
    history = target / "rfq-state" / "reconciliation-history"
    history.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    archived = history / f"reconciled-{stamp}.json"
    os.replace(flag, archived)
    fsync_directory(history)
    fsync_directory(flag.parent)
    return {"status": "reconciled", "record": str(archived), "acknowledged_at": acknowledged_at}


def identity_from_args(args: argparse.Namespace) -> str:
    if args.identity_file:
        return Path(args.identity_file).read_text(encoding="utf-8")
    identity = os.environ.get("HERMES_BACKUP_AGE_IDENTITY", "")
    if not identity:
        raise BackupError("age_identity_missing")
    return identity


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create")
    create.add_argument("--state-root", type=Path, required=True)
    create.add_argument("--output", type=Path, required=True)
    create.add_argument("--recipient", required=True)
    create.add_argument("--commit", required=True)
    create.add_argument("--tag", required=True)
    create.add_argument("--image-digest", required=True)
    create.add_argument("--skill-version", required=True)
    create.add_argument("--offsite-remote", default="")
    create.add_argument("--require-offsite", action="store_true")
    create.add_argument("--retention-days", type=int, default=0)
    create.add_argument("--retention-delete-approved", action="store_true")

    for name in ("verify", "restore"):
        command = subparsers.add_parser(name)
        command.add_argument("--backup", type=Path, required=True)
        command.add_argument("--identity-file")
        if name == "restore":
            command.add_argument("--target", type=Path, required=True)
    acknowledge = subparsers.add_parser("acknowledge-restore")
    acknowledge.add_argument("--target", type=Path, required=True)
    acknowledge.add_argument("--actor", required=True)
    acknowledge.add_argument("--evidence-file", type=Path, required=True)

    args = parser.parse_args()
    try:
        if args.command == "create":
            if args.require_offsite and not args.offsite_remote:
                raise BackupError("offsite_remote_required")
            result = create_backup(
                state_root=args.state_root,
                output=args.output,
                recipient=args.recipient,
                commit=args.commit,
                tag=args.tag,
                image_digest=args.image_digest,
                skill_version=args.skill_version,
            )
            if args.offsite_remote:
                result = upload_offsite(backup=args.output, remote_directory=args.offsite_remote)
            if args.retention_days:
                if not args.offsite_remote:
                    raise BackupError("backup_retention_requires_offsite_remote")
                retention = evaluate_offsite_retention(
                    remote_directory=args.offsite_remote, retention_days=args.retention_days,
                )
                if retention["offsite_expired_objects"] and args.retention_delete_approved:
                    retention.update(apply_offsite_retention(
                        remote_directory=args.offsite_remote,
                        retention_days=args.retention_days,
                        deletion_approved=True,
                    ))
                result.update(retention)
                metadata_path = args.output.with_name(args.output.name + ".metadata.json")
                write_json(metadata_path, result)
                run_checked(
                    [
                        "rclone",
                        "copyto",
                        str(metadata_path),
                        f"{args.offsite_remote.rstrip('/')}/{metadata_path.name}",
                    ]
                )
        elif args.command == "verify":
            result = verify_backup(backup=args.backup, identity=identity_from_args(args))
        elif args.command == "restore":
            result = restore_backup(backup=args.backup, identity=identity_from_args(args), target=args.target)
        else:
            evidence = json.loads(args.evidence_file.read_text(encoding="utf-8"))
            result = acknowledge_restore(target=args.target, actor=args.actor, evidence=evidence)
    except (BackupError, OSError, ValueError, json.JSONDecodeError, tarfile.TarError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps({"status": "ok", "result": result}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
