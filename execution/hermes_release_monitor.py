#!/usr/bin/env python3
"""Read-only production readiness snapshot for the Hermes RFQ release."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CANONICAL_SCHEDULERS = {
    "mailbox": {
        "entrypoint": "/opt/data/scripts/orchesta-rfq-mail-poller.sh",
        "lock": "/opt/data/.tmp/hermes-rfq-mail-poller.lock",
    },
    "google_sheets": {
        "entrypoint": "/opt/data/scripts/google-sheets-lead-poller.sh",
        "lock": "/opt/data/.tmp/google-sheets-lead-poller.lock",
    },
}

SECURITY_ALARMS = {
    "final_offer_autosend_attempt": "final_offer_autosend_attempt",
    "unknown_message_type": "unknown_message_type",
    "durable_recipient_mismatch": "recipient_mismatch",
    "recipient_mismatch": "recipient_mismatch",
    "recipient_contact_mismatch": "recipient_mismatch",
    "durable_source_message_mismatch": "recipient_or_thread_mismatch",
    "durable_thread_mismatch": "recipient_or_thread_mismatch",
    "durable_outbox_thread_mismatch": "recipient_or_thread_mismatch",
    "thread_mismatch": "recipient_or_thread_mismatch",
    "durable_outbox_recipient_mismatch": "recipient_mismatch",
    "durable_contact_identity_mismatch": "contact_identity_mismatch",
    "durable_process_stage_missing": "process_stage_mismatch",
    "durable_process_stage_mismatch": "process_stage_mismatch",
    "durable_transport_not_authorized": "transport_policy_not_authorized",
    "durable_policy_changed_before_send": "transport_policy_changed_before_send",
    "durable_deal_changed_before_send": "deal_changed_before_send",
    "durable_outbox_binding_mismatch": "durable_binding_mismatch",
    "durable_deal_mismatch": "durable_binding_mismatch",
    "durable_account_mismatch": "recipient_or_thread_mismatch",
    "durable_outbox_message_type_mismatch": "message_type_mismatch",
    "outbox_content_binding_failed": "content_binding_failed",
    "pre_transport_claim_lost": "pre_transport_claim_lost",
    "test_recipient_not_allowed": "test_recipient_not_allowed",
    "message_type_mismatch": "message_type_mismatch",
    "send_outcome_unknown": "outcome_unknown",
}


def parse_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def age_seconds(value: str, *, now: datetime) -> int | None:
    parsed = parse_time(value)
    return max(0, round((now - parsed).total_seconds())) if parsed else None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path, alarms: list[str], alarm: str) -> dict[str, Any]:
    if not path.is_file():
        alarms.append(alarm)
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        alarms.append(alarm)
        return {}
    if not isinstance(payload, dict):
        alarms.append(alarm)
        return {}
    return payload


def registry_snapshot(path: Path, alarms: list[str]) -> dict[str, Any]:
    if not path.is_file():
        alarms.append("registry_missing")
        return {}
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            alarms.append("registry_corrupt")
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        if "unified_outbox" not in tables or "unified_security_events" not in tables:
            alarms.append("registry_schema_incomplete")
            return {"integrity": integrity, "tables": sorted(tables)}
        outbox = {
            str(row["status"]): int(row["count"])
            for row in connection.execute(
                "SELECT status,COUNT(*) AS count FROM unified_outbox GROUP BY status"
            ).fetchall()
        }
        message_types = {
            str(row["message_type"]): int(row["count"])
            for row in connection.execute(
                "SELECT message_type,COUNT(*) AS count FROM unified_outbox GROUP BY message_type"
            ).fetchall()
        }
        sent_operational = connection.execute(
            "SELECT COUNT(*) FROM unified_outbox WHERE status='sent' AND message_type!='final_offer'"
        ).fetchone()[0]
        final_offer_sent = connection.execute(
            "SELECT COUNT(*) FROM unified_outbox WHERE status='sent' AND message_type='final_offer'"
        ).fetchone()[0]
        if final_offer_sent:
            alarms.append("final_offer_marked_sent")
        outcome_unknown = int(outbox.get("outcome_unknown", 0))
        if outcome_unknown:
            alarms.append("outcome_unknown")
        duplicate_claims = connection.execute(
            "SELECT COUNT(*) FROM ("
            "SELECT deal_id,source_type,source_key,stage,COUNT(*) AS count FROM unified_outbox "
            "WHERE status IN ('claimed','in_progress') "
            "GROUP BY deal_id,source_type,source_key,stage HAVING COUNT(*)>1)"
        ).fetchone()[0]
        if duplicate_claims:
            alarms.append("duplicate_active_claims")
        security_counts = {
            str(row["event_type"]): int(row["count"])
            for row in connection.execute(
                "SELECT event_type,COUNT(*) AS count FROM unified_security_events GROUP BY event_type"
            ).fetchall()
        }
        security_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(unified_security_events)").fetchall()
        }
        unresolved_filter = " WHERE resolved_at IS NULL" if "resolved_at" in security_columns else ""
        unresolved_security_counts = {
            str(row["event_type"]): int(row["count"])
            for row in connection.execute(
                "SELECT event_type,COUNT(*) AS count FROM unified_security_events"
                + unresolved_filter
                + " GROUP BY event_type"
            ).fetchall()
        }
        for event_type, alarm in SECURITY_ALARMS.items():
            if unresolved_security_counts.get(event_type):
                alarms.append(alarm)
        schema_versions: dict[str, int] = {}
        if "schema_versions" in tables:
            schema_versions = {
                str(row["namespace"]): int(row["version"])
                for row in connection.execute(
                    "SELECT namespace,MAX(version) AS version FROM schema_versions GROUP BY namespace"
                ).fetchall()
            }
        return {
            "integrity": integrity,
            "outbox_by_status": outbox,
            "outbox_by_message_type": message_types,
            "operational_auto_send_count": int(sent_operational),
            "final_offer_sent_count": int(final_offer_sent),
            "outcome_unknown_count": outcome_unknown,
            "duplicate_active_claim_groups": int(duplicate_claims),
            "security_events": security_counts,
            "unresolved_security_events": unresolved_security_counts,
            "schema_versions": schema_versions,
        }
    except sqlite3.Error as exc:
        alarms.append("registry_read_error")
        return {"error": exc.__class__.__name__}
    finally:
        connection.close()


def scheduler_snapshot(path: Path, alarms: list[str]) -> dict[str, Any]:
    inventory = read_json(path, alarms, "scheduler_inventory_missing_or_corrupt")
    result: dict[str, Any] = {}
    for source, expected in CANONICAL_SCHEDULERS.items():
        current = dict(inventory.get(source) or {})
        result[source] = current
        if int(current.get("active_count") or 0) != 1:
            alarms.append(f"{source}_scheduler_count_invalid")
        if current.get("entrypoint") != expected["entrypoint"]:
            alarms.append(f"{source}_old_or_unknown_entrypoint")
        if current.get("lock") != expected["lock"]:
            alarms.append(f"{source}_lock_mismatch")
        if int(current.get("active_process_count") or 0) > 1:
            alarms.append(f"{source}_duplicate_active_processes")
    legacy_hits = inventory.get("legacy_scheduler_hits") or []
    if legacy_hits:
        alarms.append("legacy_scheduler_active")
    result["legacy_scheduler_hits"] = legacy_hits if isinstance(legacy_hits, list) else ["invalid_inventory"]
    container = dict(inventory.get("release_container") or {})
    result["release_container"] = container
    if int(container.get("active_count") or 0) != 1 or container.get("running") is not True:
        alarms.append("release_container_count_or_state_invalid")
    if container.get("exact_image_match") is not True:
        alarms.append("active_image_digest_mismatch")
    return result


def collect_snapshot(
    *,
    registry_file: Path,
    source_health_file: Path,
    backup_metadata_file: Path,
    scheduler_inventory_file: Path,
    release_root: Path,
    heartbeat_ttl_seconds: int = 600,
    backup_ttl_seconds: int = 90000,
    now: datetime | None = None,
) -> dict[str, Any]:
    checked_at = now or datetime.now(timezone.utc)
    alarms: list[str] = []
    health = read_json(source_health_file, alarms, "source_health_missing_or_corrupt")
    heartbeat: dict[str, Any] = {}
    for source in CANONICAL_SCHEDULERS:
        current = dict(health.get(source) or {})
        last_ok = str(current.get("last_ok_at") or "")
        current_age = age_seconds(last_ok, now=checked_at)
        heartbeat[source] = {
            "last_success_at": last_ok,
            "last_success_age_seconds": current_age,
            "last_error_at": current.get("last_failure_at", ""),
            "last_error": current.get("last_error", ""),
            "consecutive_failures": int(current.get("consecutive_failures") or 0),
        }
        if current_age is None or current_age > heartbeat_ttl_seconds:
            alarms.append(f"{source}_heartbeat_stale")

    backup = read_json(backup_metadata_file, alarms, "backup_metadata_missing_or_corrupt")
    backup_age = age_seconds(str(backup.get("created_at") or ""), now=checked_at)
    if backup_age is None or backup_age > backup_ttl_seconds:
        alarms.append("backup_stale")
    if not backup.get("offsite_verified"):
        alarms.append("backup_offsite_unverified")
    try:
        retention_days = int(backup.get("offsite_retention_days") or 0)
    except (TypeError, ValueError):
        retention_days = 0
    if retention_days < 1 or not backup.get("offsite_retention_applied_at"):
        alarms.append("backup_retention_unverified")

    commit_file = release_root / "HERMES_RELEASE_COMMIT"
    tag_file = release_root / "HERMES_RELEASE_TAG"
    skills_root = release_root / "skills"
    skill_file = skills_root / "mail-lead-pipeline" / "SKILL.md"
    expected_skill_root = Path(
        os.environ.get("HERMES_EXPECTED_SKILL_ROOT") or str(skills_root.resolve(strict=False))
    )
    try:
        active_skill_root: Path | None = skills_root.resolve(strict=True)
    except OSError:
        active_skill_root = None
    skill_target_matches = active_skill_root is not None and (
        active_skill_root == expected_skill_root.resolve(strict=False)
    )
    release = {
        "commit": commit_file.read_text(encoding="utf-8").strip() if commit_file.is_file() else "",
        "tag": tag_file.read_text(encoding="utf-8").strip() if tag_file.is_file() else "",
        "image_digest": os.environ.get("HERMES_RELEASE_DIGEST", ""),
        "skill_sha256": sha256_file(skill_file) if skill_file.is_file() else "",
        "active_skill_root": str(active_skill_root or ""),
        "expected_skill_root": str(expected_skill_root.resolve(strict=False)),
        "skill_target_matches": skill_target_matches,
    }
    for field in ("commit", "tag", "image_digest", "skill_sha256"):
        if not release[field]:
            alarms.append(f"release_{field}_missing")
    if not skill_target_matches:
        alarms.append("active_skill_target_mismatch")

    reconciliation_flag = release_root / "rfq-state" / "RESTORE_RECONCILIATION_REQUIRED"
    if reconciliation_flag.exists():
        alarms.append("restore_reconciliation_required")

    schedulers = scheduler_snapshot(scheduler_inventory_file, alarms)
    registry = registry_snapshot(registry_file, alarms)
    unique_alarms = sorted(set(alarms))
    return {
        "status": "ok" if not unique_alarms else "alarm",
        "checked_at": checked_at.isoformat(),
        "alarms": unique_alarms,
        "release": release,
        "heartbeat": heartbeat,
        "schedulers": schedulers,
        "backup": {
            **backup,
            "age_seconds": backup_age,
        },
        "registry": registry,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry-file", type=Path, default=Path("/opt/data/.tmp/orchesta-rfq-unified.sqlite3"))
    parser.add_argument("--source-health", type=Path, default=Path("/opt/data/.tmp/orchesta-rfq-source-health.json"))
    parser.add_argument("--backup-metadata", type=Path, default=Path("/opt/data/backups/latest.metadata.json"))
    parser.add_argument("--scheduler-inventory", type=Path, default=Path("/opt/data/rfq-state/scheduler-inventory.json"))
    parser.add_argument("--release-root", type=Path, default=Path("/opt/data"))
    parser.add_argument("--heartbeat-ttl", type=int, default=600)
    parser.add_argument("--backup-ttl", type=int, default=90000)
    args = parser.parse_args()
    report = collect_snapshot(
        registry_file=args.registry_file,
        source_health_file=args.source_health,
        backup_metadata_file=args.backup_metadata,
        scheduler_inventory_file=args.scheduler_inventory,
        release_root=args.release_root,
        heartbeat_ttl_seconds=max(1, args.heartbeat_ttl),
        backup_ttl_seconds=max(1, args.backup_ttl),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
