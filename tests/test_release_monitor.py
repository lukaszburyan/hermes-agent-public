from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXECUTION = ROOT / "execution"
if str(EXECUTION) not in sys.path:
    sys.path.insert(0, str(EXECUTION))

from hermes_release_monitor import collect_snapshot
from unified_lead_registry import UnifiedLeadRegistry

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def build_release_root(root: Path) -> None:
    (root / "HERMES_RELEASE_COMMIT").write_text("8935b97\n", encoding="utf-8")
    (root / "HERMES_RELEASE_TAG").write_text("v0.1.0-test\n", encoding="utf-8")
    skill = root / "skills" / "mail-lead-pipeline" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# Controlled Orchesta RFQ skill\n", encoding="utf-8")


def build_registry(path: Path, *, outcome_unknown: bool = False, resolved_security: bool = False) -> None:
    registry = UnifiedLeadRegistry(path)
    event = registry.register_event(
        source_type="mail",
        source_key="monitor-event-1",
        email="controlled-monitor@example.com",
        company="Controlled Monitor",
        contact_name="Anna",
        content="Monitor test",
        relation="reply",
        thread_id="thread-monitor-1",
        source_metadata={"provider": "zoho", "account_id": "acc-1", "source_message_id": "msg-1"},
    )
    registry.persist_message_policy(
        "mail",
        "monitor-event-1",
        requested_type="clarification_request",
        effective_type="clarification_request",
        transport_mode="auto_send",
        reasons=["monitor_test"],
    )
    operation_id = "operation-monitor-unknown" if outcome_unknown else "operation-monitor-sent"
    assert registry.claim_response(
        event["deal_id"],
        "response:monitor-event-1",
        content_hash="monitor-input-hash",
        operation_id=operation_id,
        owner="monitor-test",
        message_type="clarification_request",
        recipient="controlled-monitor@example.com",
        source_type="mail",
        source_key="monitor-event-1",
        thread_id="thread-monitor-1",
        marker="monitor-marker-1",
    )
    assert registry.mark_outbox_in_progress(operation_id)
    if outcome_unknown:
        assert registry.mark_outbox_outcome_unknown(operation_id, error="simulated_timeout_after_accept")
        registry.record_security_event("final_offer_autosend_attempt", operation_id=operation_id)
    else:
        assert registry.complete_outbox(operation_id, external_message_id="sent-monitor-1")
    if resolved_security:
        security_id = registry.record_security_event(
            "final_offer_autosend_attempt",
            operation_id=operation_id,
        )
        assert registry.resolve_security_event(
            security_id,
            resolved_by="controlled-reviewer",
            resolution="controlled blocked-attempt test reviewed; no send occurred",
        )
    registry.close()


def common_files(root: Path, *, recent: bool = True, canonical: bool = True) -> tuple[Path, Path, Path]:
    timestamp = NOW if recent else NOW - timedelta(days=3)
    health = root / "source-health.json"
    write_json(
        health,
        {
            "mailbox": {"last_ok_at": timestamp.isoformat(), "consecutive_failures": 0},
            "google_sheets": {"last_ok_at": timestamp.isoformat(), "consecutive_failures": 0},
        },
    )
    backup = root / "backup.metadata.json"
    write_json(
        backup,
        {
            "created_at": timestamp.isoformat(),
            "offsite_verified": recent,
            "encrypted_sha256": "a" * 64,
            "offsite_retention_days": 30,
            "offsite_retention_applied_at": timestamp.isoformat(),
        },
    )
    schedulers = root / "scheduler-inventory.json"
    write_json(
        schedulers,
        {
            "mailbox": {
                "active_count": 1,
                "active_process_count": 0,
                "entrypoint": (
                    "/opt/data/scripts/orchesta-rfq-mail-poller.sh" if canonical else "/opt/data/scripts/old-mail.sh"
                ),
                "lock": "/opt/data/.tmp/hermes-rfq-mail-poller.lock",
            },
            "google_sheets": {
                "active_count": 1,
                "active_process_count": 0,
                "entrypoint": "/opt/data/scripts/google-sheets-lead-poller.sh",
                "lock": "/opt/data/.tmp/google-sheets-lead-poller.lock",
            },
            "release_container": {
                "active_count": 1,
                "running": True,
                "exact_image_match": True,
                "config_image": "ghcr.io/example/hermes@sha256:" + "1" * 64,
                "image_id": "sha256:" + "2" * 64,
            },
        },
    )
    return health, backup, schedulers


def test_release_monitor_reports_healthy_verified_state(monkeypatch, tmp_path: Path):
    release_root = tmp_path / "release"
    release_root.mkdir()
    build_release_root(release_root)
    registry = tmp_path / "registry.sqlite3"
    build_registry(registry, resolved_security=True)
    health, backup, schedulers = common_files(tmp_path)
    monkeypatch.setenv("HERMES_RELEASE_DIGEST", "sha256:" + "1" * 64)

    report = collect_snapshot(
        registry_file=registry,
        source_health_file=health,
        backup_metadata_file=backup,
        scheduler_inventory_file=schedulers,
        release_root=release_root,
        now=NOW,
    )

    assert report["status"] == "ok"
    assert report["alarms"] == []
    assert report["registry"]["integrity"] == "ok"
    assert report["registry"]["operational_auto_send_count"] == 1
    assert report["registry"]["final_offer_sent_count"] == 0
    assert report["registry"]["outcome_unknown_count"] == 0
    assert report["registry"]["security_events"]["final_offer_autosend_attempt"] == 1
    assert report["registry"]["unresolved_security_events"] == {}
    assert report["schedulers"]["release_container"]["exact_image_match"] is True
    assert report["release"]["skill_target_matches"] is True


def test_release_monitor_alarms_when_backup_retention_is_not_verified(monkeypatch, tmp_path: Path):
    release_root = tmp_path / "release"
    release_root.mkdir()
    build_release_root(release_root)
    registry = tmp_path / "registry.sqlite3"
    build_registry(registry, resolved_security=True)
    health, backup, schedulers = common_files(tmp_path)
    metadata = json.loads(backup.read_text(encoding="utf-8"))
    metadata.pop("offsite_retention_applied_at")
    metadata["offsite_retention_days"] = "invalid"
    write_json(backup, metadata)
    monkeypatch.setenv("HERMES_RELEASE_DIGEST", "sha256:" + "1" * 64)

    report = collect_snapshot(
        registry_file=registry,
        source_health_file=health,
        backup_metadata_file=backup,
        scheduler_inventory_file=schedulers,
        release_root=release_root,
        now=NOW,
    )

    assert report["status"] == "alarm"
    assert "backup_retention_unverified" in report["alarms"]


def test_release_monitor_alarms_on_skill_target_and_contact_identity_violation(monkeypatch, tmp_path: Path):
    release_root = tmp_path / "release"
    release_root.mkdir()
    build_release_root(release_root)
    registry_path = tmp_path / "registry.sqlite3"
    build_registry(registry_path)
    registry = UnifiedLeadRegistry(registry_path)
    registry.record_security_event("durable_contact_identity_mismatch", operation_id="controlled-op")
    registry.close()
    health, backup, schedulers = common_files(tmp_path)
    monkeypatch.setenv("HERMES_RELEASE_DIGEST", "sha256:" + "1" * 64)
    monkeypatch.setenv("HERMES_EXPECTED_SKILL_ROOT", str(tmp_path / "immutable-skills"))

    report = collect_snapshot(
        registry_file=registry_path,
        source_health_file=health,
        backup_metadata_file=backup,
        scheduler_inventory_file=schedulers,
        release_root=release_root,
        now=NOW,
    )

    assert report["status"] == "alarm"
    assert "active_skill_target_mismatch" in report["alarms"]
    assert "contact_identity_mismatch" in report["alarms"]


def test_release_monitor_alarms_on_strict_production_blockers(monkeypatch, tmp_path: Path):
    release_root = tmp_path / "release"
    release_root.mkdir()
    build_release_root(release_root)
    reconciliation = release_root / "rfq-state" / "RESTORE_RECONCILIATION_REQUIRED"
    reconciliation.parent.mkdir()
    reconciliation.write_text("blocked\n", encoding="utf-8")
    registry = tmp_path / "registry.sqlite3"
    build_registry(registry, outcome_unknown=True)
    health, backup, schedulers = common_files(tmp_path, recent=False, canonical=False)
    scheduler_payload = json.loads(schedulers.read_text(encoding="utf-8"))
    scheduler_payload["legacy_scheduler_hits"] = ["legacy-hermes-rfq.timer"]
    scheduler_payload["mailbox"]["active_process_count"] = 2
    scheduler_payload["release_container"]["exact_image_match"] = False
    write_json(schedulers, scheduler_payload)
    monkeypatch.setenv("HERMES_RELEASE_DIGEST", "sha256:" + "2" * 64)

    report = collect_snapshot(
        registry_file=registry,
        source_health_file=health,
        backup_metadata_file=backup,
        scheduler_inventory_file=schedulers,
        release_root=release_root,
        now=NOW,
    )

    assert report["status"] == "alarm"
    assert {
        "mailbox_heartbeat_stale",
        "google_sheets_heartbeat_stale",
        "backup_stale",
        "backup_offsite_unverified",
        "mailbox_old_or_unknown_entrypoint",
        "legacy_scheduler_active",
        "mailbox_duplicate_active_processes",
        "active_image_digest_mismatch",
        "restore_reconciliation_required",
        "outcome_unknown",
        "final_offer_autosend_attempt",
    }.issubset(report["alarms"])
    assert report["registry"]["outcome_unknown_count"] == 1
