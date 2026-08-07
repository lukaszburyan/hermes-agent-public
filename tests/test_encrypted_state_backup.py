from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXECUTION = ROOT / "execution"
if str(EXECUTION) not in sys.path:
    sys.path.insert(0, str(EXECUTION))

import hermes_state_backup as backup_module
from hermes_state_backup import (
    RECONCILIATION_FLAG,
    BackupError,
    apply_offsite_retention,
    acknowledge_restore,
    create_backup,
    restore_backup,
    upload_offsite,
    verify_backup,
)
from hermes_load_test import run_load_test
from unified_lead_registry import UnifiedLeadRegistry


@pytest.fixture(scope="module")
def age_identity() -> tuple[str, str]:
    result = subprocess.run(
        ["age-keygen"],
        check=True,
        text=True,
        capture_output=True,
    )
    identity = result.stdout
    match = re.search(r"public key:\s*(age1\S+)", identity, flags=re.IGNORECASE)
    assert match is not None
    return identity, match.group(1)


def build_state(root: Path) -> tuple[str, str]:
    mail_path = root / "rfq-state" / "state.sqlite3"
    mail_path.parent.mkdir(parents=True)
    mail = sqlite3.connect(mail_path)
    mail.execute("CREATE TABLE messages(message_id TEXT PRIMARY KEY,status TEXT NOT NULL)")
    mail.execute("INSERT INTO messages VALUES('mail-finished-1','done')")
    mail.commit()
    mail.close()

    registry_path = root / ".tmp" / "orchesta-rfq-unified.sqlite3"
    registry_path.parent.mkdir(parents=True)
    registry = UnifiedLeadRegistry(registry_path)
    event = registry.register_event(
        source_type="mail",
        source_key="backup-source-1",
        email="controlled-backup@example.com",
        company="Controlled Backup",
        contact_name="Anna",
        content="Controlled backup state",
        relation="reply",
        thread_id="thread-backup-1",
        source_metadata={"provider": "zoho", "account_id": "acc-1", "source_message_id": "msg-1"},
    )
    registry.persist_message_policy(
        "mail",
        "backup-source-1",
        requested_type="clarification_request",
        effective_type="clarification_request",
        transport_mode="auto_send",
        reasons=["backup_test"],
    )
    assert registry.claim_response(
        event["deal_id"],
        "response:backup-source-1",
        content_hash="input-hash-1",
        operation_id="operation-backup-1",
        owner="backup-test",
        message_type="clarification_request",
        recipient="controlled-backup@example.com",
        source_type="mail",
        source_key="backup-source-1",
        thread_id="thread-backup-1",
        marker="marker-backup-1",
    )
    assert registry.mark_outbox_in_progress("operation-backup-1")
    assert registry.complete_outbox("operation-backup-1", external_message_id="sent-backup-1")
    registry.close()

    sheets = root / ".tmp" / "google-sheets-leads-state.json"
    sheets.write_text('{"processed":{"row-2":"hash-2"},"version":1}\n', encoding="utf-8")
    mail_cursor_path = root / ".tmp" / "zoho_mail_poller_state.json"
    mail_cursor = sqlite3.connect(mail_cursor_path)
    mail_cursor.execute("CREATE TABLE cursor_state(folder TEXT PRIMARY KEY,last_message_id TEXT NOT NULL)")
    mail_cursor.execute("INSERT INTO cursor_state VALUES('Inbox','mail-finished-1')")
    mail_cursor.commit()
    mail_cursor.close()
    notification_outbox = root / ".tmp" / "orchesta-rfq-notify-pending"
    notification_outbox.mkdir()
    (notification_outbox / "final-offer-notice.json").write_text(
        '{"event":"final_offer_draft_ready","draft_id":"controlled-draft-1"}\n',
        encoding="utf-8",
    )
    return event["deal_id"], "response:backup-source-1"


def test_encrypted_backup_restore_preserves_terminal_outbox_and_requires_reconciliation(
    tmp_path: Path,
    age_identity: tuple[str, str],
):
    identity, recipient = age_identity
    state_root = tmp_path / "source"
    deal_id, stage = build_state(state_root)
    backup = tmp_path / "offsite" / "hermes-state-test.tar.gz.age"

    evidence = create_backup(
        state_root=state_root,
        output=backup,
        recipient=recipient,
        commit="8935b97",
        tag="v0.0.0-test",
        image_digest="sha256:" + "1" * 64,
        skill_version="orchesta-rfq-test",
    )

    assert evidence["included"] == [
        "mail_state",
        "unified_registry",
        "sheets_state",
        "mail_cursor",
        "notification_outbox",
    ]
    assert backup.is_file()
    assert backup.with_name(backup.name + ".sha256").is_file()
    assert b"controlled-backup@example.com" not in backup.read_bytes()
    verified = verify_backup(backup=backup, identity=identity)
    assert verified["metadata"]["commit"] == "8935b97"

    restored = tmp_path / "restored-clean"
    restore_backup(backup=backup, identity=identity, target=restored)
    assert (restored / RECONCILIATION_FLAG).is_file()
    assert (restored / ".tmp" / "google-sheets-leads-state.json").read_text(encoding="utf-8").startswith('{"processed"')
    pending_notice = restored / ".tmp" / "orchesta-rfq-notify-pending" / "final-offer-notice.json"
    assert json.loads(pending_notice.read_text(encoding="utf-8"))["draft_id"] == "controlled-draft-1"

    mail = sqlite3.connect(restored / "rfq-state" / "state.sqlite3")
    assert mail.execute("SELECT status FROM messages WHERE message_id='mail-finished-1'").fetchone() == ("done",)
    mail.close()

    mail_cursor = sqlite3.connect(restored / ".tmp" / "zoho_mail_poller_state.json")
    assert mail_cursor.execute("SELECT last_message_id FROM cursor_state WHERE folder='Inbox'").fetchone() == (
        "mail-finished-1",
    )
    mail_cursor.close()

    registry = UnifiedLeadRegistry(restored / ".tmp" / "orchesta-rfq-unified.sqlite3")
    operation = registry.outbox_operation("operation-backup-1")
    assert operation is not None
    assert operation["status"] == "sent"
    assert operation["marker"] == "marker-backup-1"
    assert operation["external_message_id"] == "sent-backup-1"
    assert not registry.claim_response(
        deal_id,
        stage,
        content_hash="input-hash-1",
        operation_id="operation-backup-1",
        owner="retry-after-restore",
        message_type="clarification_request",
        recipient="controlled-backup@example.com",
        source_type="mail",
        source_key="backup-source-1",
        thread_id="thread-backup-1",
        marker="marker-backup-1",
    )
    registry.close()

    acknowledgement = acknowledge_restore(
        target=restored,
        actor="controlled-test-reviewer",
        evidence={
            "zoho_sent_reconciled": True,
            "zoho_drafts_reconciled": True,
            "google_sheets_reconciled": True,
            "recipient_bindings_reconciled": True,
            "note": "controlled restore fixture compared to source manifest",
        },
    )
    assert acknowledgement["status"] == "reconciled"
    assert not (restored / RECONCILIATION_FLAG).exists()
    history = list((restored / "rfq-state" / "reconciliation-history").glob("reconciled-*.json"))
    assert len(history) == 1
    assert json.loads(history[0].read_text(encoding="utf-8"))["acknowledged_by"] == "controlled-test-reviewer"

    post_restore = run_load_test(
        event_count=10,
        workers=4,
        database=restored / ".tmp" / "orchesta-rfq-unified.sqlite3",
    )
    assert post_restore["status"] == "ok", post_restore
    assert post_restore["operational_sent"] == 8
    assert post_restore["final_offer_drafts_created"] == 2
    assert post_restore["final_offer_sent"] == 0
    assert post_restore["duplicate_replays_blocked"] == 10
    reloaded = UnifiedLeadRegistry(restored / ".tmp" / "orchesta-rfq-unified.sqlite3")
    assert reloaded.outbox_operation("operation-backup-1")["external_message_id"] == "sent-backup-1"
    reloaded.close()


def test_restore_refuses_nonempty_target_and_corrupt_ciphertext(
    tmp_path: Path,
    age_identity: tuple[str, str],
):
    identity, recipient = age_identity
    state_root = tmp_path / "source"
    build_state(state_root)
    backup = tmp_path / "backup.tar.gz.age"
    create_backup(
        state_root=state_root,
        output=backup,
        recipient=recipient,
        commit="8935b97",
        tag="v0.0.0-test",
        image_digest="sha256:" + "2" * 64,
        skill_version="orchesta-rfq-test",
    )

    nonempty = tmp_path / "nonempty"
    nonempty.mkdir()
    (nonempty / "existing").write_text("do not overwrite", encoding="utf-8")
    with pytest.raises(BackupError, match="restore_target_not_empty"):
        restore_backup(backup=backup, identity=identity, target=nonempty)

    ciphertext = bytearray(backup.read_bytes())
    ciphertext[len(ciphertext) // 2] ^= 0x01
    backup.write_bytes(ciphertext)
    with pytest.raises(BackupError, match="encrypted_checksum_mismatch"):
        verify_backup(backup=backup, identity=identity)


def test_offsite_upload_verifies_remote_sha256(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    backup = tmp_path / "state.age"
    backup.write_bytes(b"encrypted-controlled-state")
    digest = backup_module.sha256_file(backup)
    backup.with_name(backup.name + ".sha256").write_text(f"{digest}  {backup.name}\n", encoding="ascii")
    backup.with_name(backup.name + ".metadata.json").write_text(
        '{"offsite_verified":false}\n', encoding="utf-8"
    )
    calls: list[list[str]] = []

    def fake_run(command: list[str], *, input_text: str | None = None):
        calls.append(command)
        stdout = f"{digest}  {backup.name}\n" if command[1:3] == ["hashsum", "SHA-256"] else ""
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(backup_module, "run_checked", fake_run)
    evidence = upload_offsite(backup=backup, remote_directory="controlled:hermes/backups")

    assert evidence["offsite_verified"] is True
    assert evidence["offsite_remote"] == "controlled:hermes/backups/state.age"
    assert any(command[:2] == ["rclone", "hashsum"] for command in calls)
    assert len([command for command in calls if command[:2] == ["rclone", "copyto"]]) == 3


def test_offsite_retention_is_approval_gated_and_scoped(monkeypatch: pytest.MonkeyPatch):
    calls: list[list[str]] = []

    def fake_run(command: list[str], *, input_text: str | None = None):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(backup_module, "run_checked", fake_run)
    with pytest.raises(BackupError, match="deletion_not_approved"):
        apply_offsite_retention(
            remote_directory="controlled:hermes/rfq-state",
            retention_days=30,
            deletion_approved=False,
        )
    with pytest.raises(BackupError, match="dedicated_remote_directory"):
        apply_offsite_retention(
            remote_directory="controlled:",
            retention_days=30,
            deletion_approved=True,
        )

    result = apply_offsite_retention(
        remote_directory="controlled:hermes/rfq-state",
        retention_days=30,
        deletion_approved=True,
    )

    assert result["offsite_retention_days"] == 30
    assert len(calls) == 1
    command = calls[0]
    assert command[:5] == [
        "rclone",
        "delete",
        "controlled:hermes/rfq-state",
        "--min-age",
        "30d",
    ]
    assert command[-2:] == ["--exclude", "*"]
    assert all("hermes-state-*" in command[index + 1] for index, value in enumerate(command) if value == "--include")


def test_scheduled_backup_is_not_blocked_when_retention_deletion_is_unapproved():
    wrapper = (ROOT / "scripts" / "hermes-state-backup.sh").read_text(encoding="utf-8")
    approval_check = 'if [[ "$RETENTION_APPROVED" == "1" ]]'
    assert approval_check in wrapper
    assert 'if [[ "$RETENTION_APPROVED" != "1" ]]' not in wrapper
    assert "creating verified off-site backup without deletion" in wrapper
    assert 'BACKUP_ARGS+=(--retention-days "$RETENTION_DAYS" --retention-delete-approved)' in wrapper


def test_restore_acknowledgement_requires_every_check(tmp_path: Path):
    flag = tmp_path / RECONCILIATION_FLAG
    flag.parent.mkdir(parents=True)
    flag.write_text('{"status":"reconciliation_required"}\n', encoding="utf-8")
    with pytest.raises(BackupError, match="reconciliation_checks_missing"):
        acknowledge_restore(target=tmp_path, actor="reviewer", evidence={})
