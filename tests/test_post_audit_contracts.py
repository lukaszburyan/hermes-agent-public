from __future__ import annotations

import json
import os
import sqlite3
import sys
from argparse import Namespace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXECUTION = ROOT / "execution"
if str(EXECUTION) not in sys.path:
    sys.path.insert(0, str(EXECUTION))

from behavior_manifest import build_manifest
from google_sheets_lead_poller import ensure_sheet_source_id, sheet_source_key
from hermes_release_monitor import collect_snapshot, registry_snapshot
from legacy_source_reconciliation import apply_reconciliation, inventory as legacy_inventory
from llm_reply_writer import load_schema, load_system_prompt
from message_policy import FINAL_OFFER, decide_message_policy
from reply_contract import DEFAULT_REPLY_CONTRACT, REPLY_CONTRACT_DIGEST, REPLY_CONTRACT_VERSION
from reply_sanitizer import sanitize_reply
from reply_validation import validate_reply
from restore_gate import RestoreReconciliationRequired
from unified_lead_registry import UnifiedLeadRegistry
from zoho_mail_poller import (
    FakeZohoClient,
    poll,
    resolve_reply_recipient,
    select_account,
    select_folder,
    split_message_parts,
)
from zoho_pre_offer_send import _durable_transport_state, build_pre_offer_payload, create_pre_offer_message


NOW = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)


def seed_operation(tmp_path: Path, *, source_key: str = "source-1"):
    registry = UnifiedLeadRegistry(tmp_path / f"{source_key}.sqlite3")
    event = registry.register_event(
        source_type="mail",
        source_key=source_key,
        email="reply@example.com",
        company="Anonymous GmbH",
        contact_name="Alex",
        content="Potrzebujemy automatyzacji zapytań.",
        relation="reply",
        thread_id="thread-1",
        source_metadata={
            "provider": "zoho",
            "account_id": "acc-1",
            "source_message_id": "message-1",
            "subject": "RFQ",
            "resolved_reply_recipient": "reply@example.com",
            "recipient_resolution_evidence": {
                "method": "reply_to", "reply_to": ["reply@example.com"], "from": ["sender@example.com"],
                "to": ["rfq@example.com"], "cc": ["observer@example.com"], "reply_all": False,
            },
        },
    )
    registry.persist_message_policy(
        "mail", source_key,
        requested_type="follow_up", effective_type="follow_up", transport_mode="auto_send",
        reasons=["test"],
    )
    operation_id = f"response:{source_key}"
    assert registry.claim_response(
        event["deal_id"], operation_id, content_hash="input-v1", operation_id=operation_id,
        owner="contract-test", lease_seconds=300, message_type="follow_up",
        recipient="reply@example.com", source_type="mail", source_key=source_key,
        thread_id="thread-1", marker=operation_id,
    )
    return registry, event, operation_id


def test_reply_contract_is_shared_by_prompt_schema_and_validator():
    prompt = load_system_prompt()
    schema = load_schema()
    assert REPLY_CONTRACT_VERSION in prompt
    assert REPLY_CONTRACT_DIGEST in prompt
    assert schema["properties"]["contract_version"]["const"] == REPLY_CONTRACT_VERSION
    assert DEFAULT_REPLY_CONTRACT.hard_words_max == 120
    assert DEFAULT_REPLY_CONTRACT.max_questions == 2


def test_behavior_manifest_contains_every_behavior_surface():
    manifest = build_manifest(ROOT, commit="test-commit", tag="test-tag", image_digest="sha256:" + "1" * 64)
    for category in (
        "dependency_locks", "prompts", "schemas", "validator", "message_policy", "rfq_skills",
        "tenant_config", "wrappers", "systemd", "runtime",
    ):
        assert manifest["components"][category], category
    assert set(manifest["components"]["rfq_skills"]) == {
        "skills/mail-lead-pipeline/SKILL.md", "skills/rfq-final-offer/SKILL.md",
    }
    compose = (ROOT / "infrastructure/docker/docker-compose.yml").read_text(encoding="utf-8")
    assert ":/run/secrets/rclone/rclone.conf" not in compose
    assert ":/run/secrets/rclone" in compose
    assert "RCLONE_CONFIG: /run/secrets/rclone/rclone.conf" in compose


def test_known_incident_is_sanitized_then_fully_validated():
    raw = (
        "Dzień dobry Panie Alex,\n\n"
        "dziękuję za wiadomość — Jak dziś wygląda proces obsługi zapytań?"
    )
    initial = validate_reply(
        body=raw, approved_action="ask_discovery_questions",
        conversation_state={"is_first_agent_reply": False, "acknowledgement_already_sent": True},
        saved_data={}, tenant_id="orchesta", missing_data=["current_process"],
    )
    assert {"em_dash_present", "followup_re_greets"}.issubset(initial["errors"])
    sanitized = sanitize_reply(raw, is_first_agent_reply=False)
    repaired = validate_reply(
        body=sanitized["body"], approved_action="ask_discovery_questions",
        conversation_state={"is_first_agent_reply": False, "acknowledgement_already_sent": True},
        saved_data={}, tenant_id="orchesta", missing_data=["current_process"],
    )
    assert repaired["ok"] is True
    assert {"em_dash_removed", "followup_greeting_removed", "repeated_thanks_removed"}.issubset(sanitized["actions"])


def test_validation_failure_atomically_converges_every_business_projection(tmp_path: Path):
    registry, event, operation_id = seed_operation(tmp_path)
    assert registry.finalize_response_attempt(
        operation_id, outcome="validation_failed",
        reason_codes=["em_dash_present", "followup_re_greets"],
        rejected_body="Dzień dobry — ponownie", validation_errors=["em_dash_present", "followup_re_greets"],
    )
    operation = registry.outbox_operation(operation_id)
    artifact = registry.artifact(event["deal_id"], operation_id)
    disposition = registry.source_disposition("mail", "source-1")
    assert operation["status"] == "validation_failed" and not operation["owner"] and not operation["lease_expires_at"]
    assert artifact["status"] == "failed" and artifact["phase"] == "validation_failed"
    assert registry.get_deal(event["deal_id"])["status"] == "review_required"
    assert registry.review_tasks()[0]["operation_id"] == operation_id
    assert disposition["disposition"] == "manual_action_required"
    assert not registry.invariant_violations(now=NOW.isoformat())
    registry.close()


def test_reply_to_wins_over_from_and_transport_rechecks_durable_recipient(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    resolution = resolve_reply_recipient(
        {"from": "sender@example.com"},
        {
            "From": "Sender <sender@example.com>", "Reply-To": "RFQ Desk <reply@example.com>",
            "To": "rfq@example.com", "Cc": "observer@example.com",
        },
    )
    assert resolution["resolved_reply_recipient"] == "reply@example.com"
    assert resolution["evidence"]["reply_all"] is False
    registry, event, operation_id = seed_operation(tmp_path)
    payload = build_pre_offer_payload(
        account_email="rfq@example.com", to_address="reply@example.com", inbound_subject="RFQ",
        body_text="Jak dziś wygląda proces obsługi zapytań?", message_kind="follow_up", threaded=True,
    )
    context = {
        "registry": registry, "source_type": "mail", "source_key": "source-1",
        "deal_id": event["deal_id"], "operation_id": operation_id,
        "process_stage": operation_id, "thread_id": "thread-1",
    }
    class Poster:
        calls: list[tuple[str, str, dict]] = []

        def send_reply(self, account_id: str, source_message_id: str, outbound: dict):
            self.calls.append((account_id, source_message_id, outbound))
            return 200, {"data": {"messageId": "sent-reply-to-1"}}

    monkeypatch.setenv("HERMES_ALLOW_PRE_OFFER_SEND", "1")
    poster = Poster()
    sent = create_pre_offer_message(
        "acc-1", source_message_id="message-1", payload=payload, poster=poster,
        approval="AUTOMATED PRE-OFFER CUSTOMER SEND APPROVED", message_kind="follow_up",
        threaded=True, durable_context=context,
    )
    assert sent["action"] == "sent"
    assert poster.calls[0][2]["toAddress"] == "reply@example.com"
    assert registry.outbox_operation(operation_id)["external_message_id"] == "sent-reply-to-1"
    registry.close()


def test_multi_account_and_multi_folder_selection_fail_closed(tmp_path: Path):
    accounts = [
        {"accountId": "1", "primaryEmailAddress": "rfq@example.com"},
        {"accountId": "2", "mailboxAddress": "rfq@example.com"},
    ]
    with pytest.raises(RuntimeError, match="matches=2"):
        select_account(accounts, "rfq@example.com")
    with pytest.raises(RuntimeError, match="matches=0"):
        select_account(accounts, "missing@example.com")
    folders = [
        {"folderId": "a", "folderName": "Inbox"},
        {"folderId": "b", "folderName": "Inbox"},
    ]
    with pytest.raises(RuntimeError, match="matches=2"):
        select_folder(folders, "Inbox")

    account_registry = UnifiedLeadRegistry(tmp_path / "account.sqlite3")
    with pytest.raises(RuntimeError, match="zoho_account_resolution_failed"):
        poll(
            FakeZohoClient({"accounts": accounts, "folders": folders, "messages": []}),
            object(), object(), target_email="rfq@example.com", unified_registry=account_registry,
        )
    assert account_registry.connection.execute("SELECT COUNT(*) FROM unified_events").fetchone()[0] == 0
    assert account_registry.connection.execute("SELECT COUNT(*) FROM unified_outbox").fetchone()[0] == 0
    assert account_registry.security_events("zoho_account_folder_mismatch")
    account_registry.close()

    folder_registry = UnifiedLeadRegistry(tmp_path / "folder.sqlite3")
    with pytest.raises(RuntimeError, match="zoho_inbox_resolution_failed"):
        poll(
            FakeZohoClient({
                "accounts": [{"accountId": "1", "primaryEmailAddress": "rfq@example.com"}],
                "folders": folders, "messages": [],
            }),
            object(), object(), target_email="rfq@example.com", unified_registry=folder_registry,
        )
    assert folder_registry.connection.execute("SELECT COUNT(*) FROM unified_events").fetchone()[0] == 0
    assert folder_registry.connection.execute("SELECT COUNT(*) FROM unified_outbox").fetchone()[0] == 0
    assert folder_registry.security_events("zoho_account_folder_mismatch")
    folder_registry.close()


def test_sheet_row_identity_survives_content_edit_timeout_and_restart(tmp_path: Path):
    logical_id = ensure_sheet_source_id()
    before = sheet_source_key("spreadsheet-1", logical_id)
    after_edit = sheet_source_key("spreadsheet-1", ensure_sheet_source_id(logical_id))
    assert before == after_edit
    assert "content" not in before
    registry = UnifiedLeadRegistry(tmp_path / "sheet-identity.sqlite3")
    event = registry.register_event(
        source_type="google_sheets", source_key=before, email="sheet@example.com",
        company="Anonymous", contact_name="Alex", content="first content", relation="new",
        source_metadata={"content_version": "v1"},
    )
    registry.persist_message_policy(
        "google_sheets", before, requested_type="acknowledgement", effective_type="acknowledgement",
        transport_mode="auto_send", reasons=["test"],
    )
    operation_id = f"response:{before}"
    assert registry.claim_response(
        event["deal_id"], operation_id, content_hash="v1", operation_id=operation_id,
        owner="sheet-test", message_type="acknowledgement", recipient="sheet@example.com",
        source_type="google_sheets", source_key=before, marker=operation_id,
    )
    assert registry.mark_content_validated(operation_id)
    assert registry.mark_transport_starting(operation_id)
    assert registry.mark_outbox_in_progress(operation_id)
    assert registry.mark_outbox_outcome_unknown(operation_id, error="timeout_after_post")
    edited = registry.register_event(
        source_type="google_sheets", source_key=after_edit, email="sheet@example.com",
        company="Anonymous", contact_name="Alex", content="edited content", relation="new",
        source_metadata={"content_version": "v2"},
    )
    assert edited["duplicate"] is True and edited["deal_id"] == event["deal_id"]
    assert not registry.claim_response(
        event["deal_id"], operation_id, content_hash="v2", operation_id=operation_id,
        owner="restart", message_type="acknowledgement", recipient="sheet@example.com",
        source_type="google_sheets", source_key=before, marker=operation_id,
    )
    assert registry.connection.execute("SELECT COUNT(*) FROM unified_events").fetchone()[0] == 1
    assert registry.connection.execute("SELECT COUNT(*) FROM unified_outbox").fetchone()[0] == 1
    registry.close()


def test_quoted_old_deal_id_cannot_route_newest_message(tmp_path: Path):
    registry = UnifiedLeadRegistry(tmp_path / "quoted.sqlite3")
    first = registry.register_event(
        source_type="mail", source_key="old", email="buyer@example.com", company="Buyer",
        contact_name="A", content="Projekt CRM", relation="new",
        source_metadata={"subject": "RFQ CRM"},
    )
    old_id = first["deal_id"]
    body = f"To nowa sprawa: potrzebujemy obiegu faktur.\n\nOn wrote:\n> Stara sprawa {old_id} dotyczyła CRM."
    parts = split_message_parts(body)
    second = registry.register_event(
        source_type="mail", source_key="new", email="buyer@example.com", company="Buyer",
        contact_name="A", content=parts["newest_customer_text"], relation="new",
        source_metadata={"subject": "Nowa sprawa"},
    )
    assert second["routing_action"] == "create_new"
    assert second["deal_id"] != old_id
    registry.close()


def test_same_subject_can_open_second_rfq_for_same_customer(tmp_path: Path):
    registry = UnifiedLeadRegistry(tmp_path / "subject.sqlite3")
    first = registry.register_event(
        source_type="mail", source_key="one", email="buyer@example.com", company="Buyer",
        contact_name="A", content="Potrzebujemy projektu CRM.", relation="new",
        source_metadata={"subject": "Prośba o ofertę"},
    )
    second = registry.register_event(
        source_type="mail", source_key="two", email="buyer@example.com", company="Buyer",
        contact_name="A", content="To nowa sprawa: potrzebujemy OCR faktur.", relation="new",
        source_metadata={"subject": "Prośba o ofertę"},
    )
    assert second["routing_action"] == "create_new"
    assert second["deal_id"] != first["deal_id"]
    assert "hard:exact_normalized_subject" not in second["routing_decision"]["evidence"]
    registry.close()


@pytest.mark.parametrize(
    "language,body",
    [
        ("pl", "Finalna oferta: cena 7 200 PLN, płatność w dwóch ratach, ważna 14 dni."),
        ("en", "Final quote: price EUR 7,200, payment in two installments, valid for 14 days."),
        ("de", "Finales Angebot: Preis 7.200 EUR, Ratenzahlung, gültig für 14 Tage."),
    ],
)
def test_multilingual_final_offer_is_always_draft_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, language: str, body: str,
):
    decision = decide_message_policy("follow_up", body_text=body, message_language=language)
    assert decision.effective_type == FINAL_OFFER
    assert decision.transport_mode == "draft_only"
    registry, event, operation_id = seed_operation(tmp_path, source_key=f"final-{language}")
    payload = build_pre_offer_payload(
        account_email="rfq@example.com", to_address="reply@example.com", inbound_subject="RFQ",
        body_text=body, message_kind="follow_up", threaded=True, route_final_offer_to_policy=True,
    )

    class NeverPost:
        calls = 0

        def send_reply(self, *_args, **_kwargs):
            self.calls += 1
            raise AssertionError("final offer reached provider POST")

    monkeypatch.setenv("HERMES_ALLOW_PRE_OFFER_SEND", "1")
    poster = NeverPost()
    result = create_pre_offer_message(
        "acc-1", source_message_id="message-1", payload=payload, poster=poster,
        approval="AUTOMATED PRE-OFFER CUSTOMER SEND APPROVED", message_kind="follow_up", threaded=True,
        durable_context={
            "registry": registry, "source_type": "mail", "source_key": f"final-{language}",
            "deal_id": event["deal_id"], "operation_id": operation_id,
            "process_stage": operation_id, "thread_id": "thread-1",
        },
    )
    assert result["action"] == "blocked" and result["reason"] == "final_offer_autosend_forbidden"
    assert poster.calls == 0
    assert registry.outbox_operation(operation_id)["status"] == "manual_review"
    assert registry.review_tasks()
    registry.close()


@pytest.mark.parametrize(
    "body",
    [
        "Oferujemy 10% rabatu przy akceptacji w tym tygodniu.",
        "We offer 10% off if you accept this week.",
        "Wir bieten 10% Rabatt bei Annahme in dieser Woche.",
        "Payment is Net 30 days.",
        "Wir akzeptieren Ihr Angebot.",
    ],
)
def test_commercial_terms_word_order_never_bypasses_final_offer_guard(body: str):
    decision = decide_message_policy("follow_up", body_text=body)
    assert decision.effective_type == FINAL_OFFER
    assert decision.transport_mode == "draft_only"


@pytest.mark.parametrize("body", ["", "OK", "はい"])
def test_unknown_or_empty_reply_language_requires_manual_review(body: str):
    decision = decide_message_policy("follow_up", body_text=body)
    assert decision.effective_type == "manual_review"
    assert decision.transport_mode == "manual_review"
    assert "unsupported_or_uncertain_language" in decision.reasons


def test_pre_post_failure_is_retryable_but_post_started_failure_is_unknown(tmp_path: Path):
    registry, event, operation_id = seed_operation(tmp_path, source_key="prepost")
    assert registry.finalize_response_attempt(
        operation_id, outcome="retry_scheduled", reason_codes=["pre_post_crash"],
    )
    assert registry.outbox_operation(operation_id)["status"] == "retry_scheduled"
    assert registry.claim_response(
        event["deal_id"], operation_id, content_hash="input-v1", operation_id=operation_id,
        owner="restart", message_type="follow_up", recipient="reply@example.com",
        source_type="mail", source_key="prepost", thread_id="thread-1", marker=operation_id,
    )
    assert registry.mark_content_validated(operation_id)
    assert registry.mark_transport_starting(operation_id)
    assert registry.mark_outbox_in_progress(operation_id)
    assert registry.mark_outbox_outcome_unknown(operation_id, error="crash_after_post_started")
    assert registry.outbox_operation(operation_id)["status"] == "outcome_unknown"
    assert not registry.claim_response(
        event["deal_id"], operation_id, content_hash="input-v1", operation_id=operation_id,
        owner="blind-resend", message_type="follow_up", recipient="reply@example.com",
        source_type="mail", source_key="prepost", thread_id="thread-1", marker=operation_id,
    )
    registry.close()


@pytest.mark.parametrize("phase", ["claimed", "content_bound", "validated", "transport_starting", "post_started", "provider_accept"])
def test_fault_injection_reaper_never_blind_resends(tmp_path: Path, phase: str):
    registry, _event, operation_id = seed_operation(tmp_path, source_key=f"fault-{phase}")
    if phase in {"content_bound", "validated", "transport_starting", "post_started", "provider_accept"}:
        assert registry.bind_outbox_content(operation_id, content_hash="rendered")
    if phase in {"validated", "transport_starting", "post_started", "provider_accept"}:
        assert registry.mark_content_validated(operation_id)
    if phase in {"transport_starting", "post_started", "provider_accept"}:
        assert registry.mark_transport_starting(operation_id)
    if phase in {"post_started", "provider_accept"}:
        assert registry.mark_outbox_in_progress(operation_id)
    registry.connection.execute(
        "UPDATE unified_outbox SET lease_expires_at=? WHERE operation_id=?",
        ((NOW - timedelta(minutes=10)).isoformat(), operation_id),
    )
    result = registry.reap_expired_claims(now=NOW.isoformat())
    expected = "outcome_unknown" if phase in {"post_started", "provider_accept"} else "retry_scheduled"
    assert result[0]["outcome"] == expected
    operation = registry.outbox_operation(operation_id)
    assert operation["status"] == expected
    assert not operation["owner"]
    registry.close()


def test_expired_terminal_failure_converges_atomically_to_durable_review(tmp_path: Path):
    registry, event, operation_id = seed_operation(tmp_path, source_key="expired-terminal")
    registry.connection.execute(
        "UPDATE unified_outbox SET status='permanent_failed',phase='validation_failed',last_error=?,lease_expires_at=? "
        "WHERE operation_id=?",
        ("em_dash_present,followup_re_greets", (NOW - timedelta(minutes=10)).isoformat(), operation_id),
    )
    registry.connection.execute(
        "UPDATE unified_artifacts SET status='creating',phase='validation_failed' WHERE operation_id=?",
        (operation_id,),
    )

    result = registry.reap_expired_claims(now=NOW.isoformat())

    assert result == [{"operation_id": operation_id, "previous_phase": "validation_failed", "outcome": "manual_review"}]
    operation = registry.outbox_operation(operation_id)
    artifact = registry.artifact(event["deal_id"], operation_id)
    assert operation["status"] == "manual_review"
    assert operation["phase"] == "manual_review"
    assert not operation["owner"] and not operation["lease_expires_at"]
    assert artifact["status"] == "manual_review" and artifact["phase"] == "manual_review"
    tasks = registry.review_tasks()
    assert len(tasks) == 1 and tasks[0]["operation_id"] == operation_id
    disposition = registry.source_disposition("mail", "expired-terminal")
    assert disposition["disposition"] == "manual_action_required"
    assert registry.invariant_violations(now=NOW.isoformat()) == []
    registry.close()


def test_expired_terminal_sent_claim_requires_provider_evidence_and_clears_atomically(tmp_path: Path):
    registry, event, operation_id = seed_operation(tmp_path, source_key="expired-terminal-sent")
    assert registry.mark_content_validated(operation_id)
    assert registry.mark_transport_starting(operation_id)
    assert registry.mark_outbox_in_progress(operation_id)
    assert registry.finalize_response_attempt(
        operation_id,
        outcome="sent",
        external_message_id="provider-sent-terminal-1",
        provider_evidence={"provider": "zoho", "provider_message_id": "provider-sent-terminal-1"},
    )
    expired = (NOW - timedelta(minutes=10)).isoformat()
    registry.connection.execute(
        "UPDATE unified_outbox SET owner='legacy-worker',started_at=?,lease_expires_at=? WHERE operation_id=?",
        (expired, expired, operation_id),
    )
    registry.connection.execute(
        "UPDATE unified_artifacts SET owner='legacy-worker',started_at=?,lease_expires_at=? WHERE operation_id=?",
        (expired, expired, operation_id),
    )
    assert any(item["code"] == "expired_lease" for item in registry.invariant_violations(now=NOW.isoformat()))

    result = registry.reap_expired_claims(now=NOW.isoformat())

    assert result == [{"operation_id": operation_id, "previous_phase": "sent", "outcome": "sent"}]
    operation = registry.outbox_operation(operation_id)
    artifact = registry.artifact(event["deal_id"], operation_id)
    assert operation["status"] == "sent" and operation["phase"] == "sent"
    assert operation["external_message_id"] == "provider-sent-terminal-1"
    assert not operation["owner"] and not operation["lease_expires_at"]
    assert artifact["status"] == "sent" and artifact["phase"] == "sent"
    assert not artifact["owner"] and not artifact["lease_expires_at"]
    assert registry.source_disposition("mail", "expired-terminal-sent")["disposition"] == "customer_succeeded"
    assert registry.invariant_violations(now=NOW.isoformat()) == []
    registry.close()


def test_expired_terminal_sent_claim_without_provider_id_stays_critical(tmp_path: Path):
    registry, _event, operation_id = seed_operation(tmp_path, source_key="expired-terminal-unverified")
    assert registry.mark_content_validated(operation_id)
    assert registry.mark_transport_starting(operation_id)
    assert registry.mark_outbox_in_progress(operation_id)
    assert registry.finalize_response_attempt(
        operation_id,
        outcome="sent",
        external_message_id="provider-sent-to-remove",
        provider_evidence={"provider": "zoho", "provider_message_id": "provider-sent-to-remove"},
    )
    expired = (NOW - timedelta(minutes=10)).isoformat()
    registry.connection.execute(
        "UPDATE unified_outbox SET external_message_id=NULL,provider_evidence_json='{}',owner='legacy-worker',"
        "started_at=?,lease_expires_at=? WHERE operation_id=?",
        (expired, expired, operation_id),
    )

    assert registry.reap_expired_claims(now=NOW.isoformat()) == []
    operation = registry.outbox_operation(operation_id)
    assert operation["status"] == "sent" and operation["owner"] == "legacy-worker"
    assert any(item["code"] == "expired_lease" for item in registry.invariant_violations(now=NOW.isoformat()))
    registry.close()


def test_existing_review_required_deal_gets_one_durable_task_on_registry_open(tmp_path: Path):
    registry, event, operation_id = seed_operation(tmp_path, source_key="legacy-review-state")
    assert registry.finalize_response_attempt(
        operation_id,
        outcome="manual_review",
        reason_codes=["temporary-review"],
        rejected_body="temporary rejected body",
        validation_errors=["temporary-review"],
    )
    registry.connection.execute("DELETE FROM unified_review_tasks WHERE deal_id=?", (event["deal_id"],))
    registry.close()

    registry = UnifiedLeadRegistry(tmp_path / "legacy-review-state.sqlite3")
    tasks = registry.review_tasks()
    assert len(tasks) == 1
    task = tasks[0]
    assert task["deal_id"] == event["deal_id"]
    assert task["source_type"] == "mail" and task["source_key"] == "legacy-review-state"
    assert task["reason_codes"] == ["legacy_review_state_backfill"]
    assert task["rejected_body"] == "[legacy rejected body unavailable]"
    assert task["validation_errors"] == ["legacy_review_state_backfill"]
    assert task["recipient"] == "reply@example.com"
    assert task["notification_status"] == "pending" and task["status"] == "open"
    assert task["provider_evidence"] == {
        "bound_unresolved_operation": False, "legacy_migration": True,
        "provider_evidence": "unavailable", "send_capability": False,
    }
    assert registry.outbox_operation(task["operation_id"]) is None
    assert registry.connection.execute(
        "SELECT COUNT(*) FROM unified_response_events WHERE operation_id=? AND new_phase='manual_review'",
        (task["operation_id"],),
    ).fetchone()[0] == 1
    assert registry.invariant_violations(now=NOW.isoformat()) == []
    registry.close()

    registry = UnifiedLeadRegistry(tmp_path / "legacy-review-state.sqlite3")
    assert len(registry.review_tasks()) == 1
    assert registry.connection.execute(
        "SELECT COUNT(*) FROM unified_response_events WHERE operation_id LIKE 'legacy-review-state:%'"
    ).fetchone()[0] == 1
    registry.close()


def test_legacy_review_backfill_binds_unresolved_operation_then_converges_once(tmp_path: Path):
    registry, event, operation_id = seed_operation(tmp_path, source_key="legacy-review-active-operation")
    expired = (NOW - timedelta(minutes=10)).isoformat()
    registry.connection.execute(
        "UPDATE unified_deals SET status='review_required' WHERE deal_id=?",
        (event["deal_id"],),
    )
    registry.connection.execute(
        "UPDATE unified_outbox SET lease_expires_at=? WHERE operation_id=?",
        (expired, operation_id),
    )
    registry.connection.execute(
        "UPDATE unified_artifacts SET lease_expires_at=? WHERE operation_id=?",
        (expired, operation_id),
    )
    registry.close()

    registry = UnifiedLeadRegistry(tmp_path / "legacy-review-active-operation.sqlite3")
    tasks = registry.review_tasks()
    assert len(tasks) == 1
    assert tasks[0]["operation_id"] == operation_id
    assert tasks[0]["reason_codes"] == ["legacy_review_state_backfill"]
    assert tasks[0]["provider_evidence"]["bound_unresolved_operation"] is True
    assert registry.mark_review_task_notified(
        tasks[0]["task_id"], channel="test", provider_evidence={"notification_id": "notice-1"},
    )
    assert registry.reap_expired_claims(now=NOW.isoformat()) == [
        {"operation_id": operation_id, "previous_phase": "preparing", "outcome": "retry_scheduled"}
    ]
    assert registry.review_tasks() == []
    assert registry.finalize_response_attempt(
        operation_id,
        outcome="manual_review",
        reason_codes=["known_incident_validation_failed"],
        rejected_body="[historical rejected body unavailable]",
        validation_errors=["em_dash_present", "followup_re_greets"],
        provider_evidence={"historical_incident": True, "post_started": False, "send_capability": False},
    )
    tasks = registry.review_tasks()
    assert len(tasks) == 1
    assert tasks[0]["operation_id"] == operation_id
    assert tasks[0]["reason_codes"] == ["known_incident_validation_failed"]
    assert tasks[0]["validation_errors"] == ["em_dash_present", "followup_re_greets"]
    assert tasks[0]["notification_status"] == "pending"
    assert registry.connection.execute(
        "SELECT COUNT(*) FROM unified_review_tasks WHERE deal_id=?", (event["deal_id"],)
    ).fetchone()[0] == 1
    assert registry.invariant_violations(now=NOW.isoformat()) == []
    registry.close()


def test_duplicate_open_review_tasks_are_critical_business_invariant(tmp_path: Path):
    registry, event, operation_id = seed_operation(tmp_path, source_key="duplicate-review-tasks")
    now = NOW.isoformat()
    operation = {
        "source_type": "mail", "source_key": "duplicate-review-tasks",
        "deal_id": event["deal_id"], "recipient": "reply@example.com",
    }
    for suffix in ("a", "b"):
        registry._create_review_task_tx(
            operation={"operation_id": f"{operation_id}:{suffix}", **operation},
            reason_codes=["review_required"], rejected_body="[anonymous rejected body]",
            validation_errors=["review_required"], provider_evidence={"send_capability": False}, now=now,
        )
    assert [item["code"] for item in registry.invariant_violations(now=now)].count(
        "duplicate_open_review_tasks"
    ) == 1
    registry.close()

    alarms: list[str] = []
    snapshot = registry_snapshot(tmp_path / "duplicate-review-tasks.sqlite3", alarms, now=NOW)
    assert snapshot["invariants"]["duplicate_open_review_tasks"] == 1
    assert "duplicate_open_review_tasks" in alarms


def test_legacy_source_reconciliation_requires_complete_business_evidence(tmp_path: Path):
    state_file = tmp_path / "state.sqlite3"
    state = sqlite3.connect(state_file)
    state.executescript(
        "CREATE TABLE messages(message_id TEXT PRIMARY KEY,status TEXT,record_json TEXT,updated_at TEXT);"
        "CREATE TABLE state_transitions(id INTEGER PRIMARY KEY AUTOINCREMENT,entity_type TEXT,entity_id TEXT,"
        "previous_status TEXT,new_status TEXT,reason_code TEXT,timestamp TEXT,run_id TEXT);"
    )
    fixtures = [
        ("sent", "sent"), ("draft", "created"), ("review", "auto_send_failed"),
        ("discard", "blocked_human_takeover"),
    ]
    for message_id, action in fixtures:
        state.execute(
            "INSERT INTO messages VALUES(?,'done',?,?)",
            (message_id, json.dumps({"draft_action": action, "classification": "anonymous"}), NOW.isoformat()),
        )
    state.commit()
    pending = legacy_inventory(state)
    state.close()

    registry_file = tmp_path / "registry.sqlite3"
    registry = sqlite3.connect(registry_file)
    registry.execute("CREATE TABLE unified_review_tasks(task_id TEXT PRIMARY KEY,status TEXT)")
    registry.execute("INSERT INTO unified_review_tasks VALUES('review-1','open')")
    registry.commit()
    registry.close()
    by_action = {item["draft_action"]: item for item in pending}
    evidence = {
        "schema_version": 1,
        "run_id": "test-reconcile",
        "items": [
            {**by_action["sent"], "outcome": "customer_succeeded", "provider_message_id": "provider-sent-1"},
            {**by_action["created"], "outcome": "draft_verified", "provider_draft_id": "provider-draft-1"},
            {**by_action["auto_send_failed"], "outcome": "manual_action_required", "review_task_id": "review-1"},
            {**by_action["blocked_human_takeover"], "outcome": "terminal_discard", "discard_reason": "human_takeover"},
        ],
    }
    changed = apply_reconciliation(state_file, evidence, registry_file=registry_file)
    assert len(changed) == 4
    state = sqlite3.connect(state_file)
    assert legacy_inventory(state) == []
    statuses = dict(state.execute("SELECT message_id,status FROM messages"))
    assert statuses == {
        "sent": "done", "draft": "done", "review": "manual_review", "discard": "precheck_skipped",
    }
    records = {row[0]: json.loads(row[1]) for row in state.execute("SELECT message_id,record_json FROM messages")}
    assert records["sent"]["business_outcome"] == "customer_succeeded"
    assert records["draft"]["business_outcome"] == "draft_verified"
    assert records["review"]["legacy_reconciliation"]["review_task_id"] == "review-1"
    assert state.execute("SELECT COUNT(*) FROM state_transitions").fetchone()[0] == 4
    state.close()


def test_legacy_manual_review_creates_one_durable_terminal_projection(tmp_path: Path):
    state_file = tmp_path / "state.sqlite3"
    state = sqlite3.connect(state_file)
    state.executescript(
        "CREATE TABLE messages(message_id TEXT PRIMARY KEY,status TEXT,record_json TEXT,updated_at TEXT);"
        "CREATE TABLE state_transitions(id INTEGER PRIMARY KEY AUTOINCREMENT,entity_type TEXT,entity_id TEXT,"
        "previous_status TEXT,new_status TEXT,reason_code TEXT,timestamp TEXT,run_id TEXT);"
    )
    state.execute(
        "INSERT INTO messages VALUES(?,'done',?,?)",
        (
            "legacy-review-source",
            json.dumps({
                "draft_action": "auto_send_failed",
                "classification": "anonymous",
                "source_from": "Anonymous Reviewer <review@example.invalid>",
            }),
            NOW.isoformat(),
        ),
    )
    state.commit()
    pending = legacy_inventory(state)
    state.close()

    registry_file = tmp_path / "registry.sqlite3"
    registry = UnifiedLeadRegistry(registry_file)
    registry.close()
    evidence = {
        "schema_version": 1,
        "run_id": "legacy-review-contract",
        "items": [{
            **pending[0],
            "outcome": "manual_action_required",
            "review_payload": {
                "reason_codes": ["legacy_provider_evidence_unavailable"],
                "rejected_body": "[legacy rejected body unavailable]",
                "validation_errors": ["legacy_provider_evidence_unavailable"],
                "provider_evidence": {"provider": "zoho", "post_started": False},
            },
        }],
    }

    changed = apply_reconciliation(state_file, evidence, registry_file=registry_file)
    assert changed == [{
        "message_sha256": pending[0]["message_sha256"],
        "outcome": "manual_action_required",
        "status": "manual_review",
    }]

    state = sqlite3.connect(state_file)
    source_status, source_record = state.execute(
        "SELECT status,record_json FROM messages WHERE message_id='legacy-review-source'"
    ).fetchone()
    state.close()
    source_record = json.loads(source_record)
    assert source_status == "manual_review"
    assert source_record["business_outcome"] == "manual_action_required"
    task_id = source_record["legacy_reconciliation"]["review_task_id"]

    registry = UnifiedLeadRegistry(registry_file)
    task = registry.review_tasks()[0]
    assert task["task_id"] == task_id
    assert task["source_key"] == "legacy-review-source"
    assert task["reason_codes"] == ["legacy_provider_evidence_unavailable"]
    assert task["rejected_body"] == "[legacy rejected body unavailable]"
    assert task["validation_errors"] == ["legacy_provider_evidence_unavailable"]
    assert task["recipient"] == "review@example.invalid"
    operation = registry.outbox_operation(task["operation_id"])
    assert operation["status"] == "manual_review" and operation["phase"] == "manual_review"
    assert not operation["owner"] and not operation["lease_expires_at"]
    artifact = registry.artifact(task["deal_id"], "legacy_reconciliation")
    assert artifact["status"] == "manual_review" and artifact["phase"] == "manual_review"
    event = registry.connection.execute(
        "SELECT source_type,source_key,deal_id,resolved_reply_recipient FROM unified_events"
    ).fetchone()
    assert tuple(event) == ("legacy_mail", "legacy-review-source", task["deal_id"], "review@example.invalid")
    assert registry.get_deal(task["deal_id"])["status"] == "review_required"
    assert registry.source_disposition("legacy_mail", "legacy-review-source")["disposition"] == "manual_action_required"
    assert registry.connection.execute(
        "SELECT COUNT(*) FROM unified_response_events WHERE operation_id=? AND new_phase='manual_review'",
        (task["operation_id"],),
    ).fetchone()[0] == 1
    assert registry.connection.execute(
        "SELECT COUNT(*) FROM unified_deal_identities WHERE deal_id=?", (task["deal_id"],)
    ).fetchone()[0] == 0
    assert registry.invariant_violations(now=NOW.isoformat()) == []

    result = registry.ensure_legacy_review_task(
        source_key="legacy-review-source",
        reason_codes=["legacy_provider_evidence_unavailable"],
        rejected_body="[legacy rejected body unavailable]",
        validation_errors=["legacy_provider_evidence_unavailable"],
        recipient="review@example.invalid",
        provider_evidence={"provider": "zoho", "post_started": False},
    )
    assert result["task_id"] == task_id
    assert registry.connection.execute("SELECT COUNT(*) FROM unified_review_tasks").fetchone()[0] == 1
    assert registry.connection.execute("SELECT COUNT(*) FROM unified_response_events").fetchone()[0] == 1
    registry.close()


def test_provider_accept_without_locator_is_not_sent(tmp_path: Path):
    registry, _event, operation_id = seed_operation(tmp_path, source_key="provider")
    assert registry.mark_content_validated(operation_id)
    assert registry.mark_transport_starting(operation_id)
    assert registry.mark_outbox_in_progress(operation_id)
    assert not registry.complete_outbox(operation_id, external_message_id="")
    assert registry.mark_outbox_outcome_unknown(operation_id, error="provider_accept_without_message_id")
    assert registry.outbox_operation(operation_id)["status"] == "outcome_unknown"
    registry.close()


def test_manual_final_offer_send_converges_once_across_restart(tmp_path: Path):
    registry = UnifiedLeadRegistry(tmp_path / "manual-final.sqlite3")
    event = registry.register_event(
        source_type="mail", source_key="manual-final", email="reply@example.com",
        company="Anonymous GmbH", contact_name="Alex", content="Proszę o finalną ofertę.",
        relation="reply", thread_id="thread-1", facts={},
        source_metadata={"provider": "zoho", "account_id": "acc-1", "subject": "RFQ"},
    )
    operation_id = "response:manual-final"
    marker = "final-offer:anon:1"
    assert registry.claim_response(
        event["deal_id"], "final_offer", content_hash="input-v1", operation_id=operation_id,
        owner="contract-test", lease_seconds=300, message_type="final_offer",
        recipient="reply@example.com", source_type="mail", source_key="manual-final",
        thread_id="thread-1", marker=marker,
    )
    assert registry.finalize_response_attempt(
        operation_id, outcome="draft_created", external_draft_id="draft-1",
        provider_evidence={
            "provider": "zoho", "provider_draft_id": "draft-1", "pdf_hash": "a" * 64,
            "marker": marker, "recipient": "reply@example.com", "thread_id": "thread-1",
        },
    )
    evidence = {"provider": "zoho", "draft_id": "draft-1"}
    assert registry.record_manual_final_offer_sent(
        event["deal_id"], external_message_id="sent-manual-1", sent_at=NOW.isoformat(),
        marker=marker, pdf_hash="a" * 64, recipient="reply@example.com",
        thread_id="thread-1", evidence=evidence,
    )
    operation = registry.outbox_operation(operation_id)
    artifact = registry.artifact(event["deal_id"], "final_offer")
    assert operation["status"] == operation["phase"] == "sent_manually"
    assert not operation["owner"] and not operation["lease_expires_at"]
    assert operation["external_message_id"] == "sent-manual-1"
    assert artifact["status"] == artifact["phase"] == artifact["outcome"] == "sent_manually"
    assert artifact["provider_evidence"]["marker"] == marker
    assert artifact["provider_evidence"]["pdf_hash"] == "a" * 64
    assert registry.source_disposition("mail", "manual-final")["disposition"] == "draft_verified"
    assert registry.get_deal(event["deal_id"])["status"] == "completed"
    assert registry.invariant_violations(now=NOW.isoformat()) == []

    # Reproduce RC9's partial projection and prove an idempotent restart repairs it
    # from the already durable manual-send evidence without creating a new send.
    registry.connection.execute(
        "UPDATE unified_outbox SET status='draft_created',phase='draft_created' WHERE operation_id=?",
        (operation_id,),
    )
    registry.connection.execute(
        "UPDATE unified_artifacts SET status='sent_manually',phase='sent',provider_evidence_json='{}' "
        "WHERE operation_id=?",
        (operation_id,),
    )
    registry.connection.commit()
    registry.close()
    registry = UnifiedLeadRegistry(tmp_path / "manual-final.sqlite3")
    assert registry.record_manual_final_offer_sent(
        event["deal_id"], external_message_id="sent-manual-1", sent_at=NOW.isoformat(),
        marker=marker, pdf_hash="a" * 64, recipient="reply@example.com",
        thread_id="thread-1", evidence=evidence,
    )
    assert not registry.record_manual_final_offer_sent(
        event["deal_id"], external_message_id="sent-manual-2", sent_at=NOW.isoformat(),
        marker=marker, pdf_hash="a" * 64, recipient="reply@example.com",
        thread_id="thread-1", evidence=evidence,
    )
    assert registry.outbox_operation(operation_id)["phase"] == "sent_manually"
    assert registry.artifact(event["deal_id"], "final_offer")["phase"] == "sent_manually"
    assert registry.invariant_violations(now=NOW.isoformat()) == []
    assert registry.connection.execute(
        "SELECT COUNT(*) FROM unified_manual_sends WHERE deal_id=? AND stage='final_offer'",
        (event["deal_id"],),
    ).fetchone()[0] == 1
    assert registry.connection.execute(
        "SELECT COUNT(*) FROM unified_response_events WHERE operation_id=? AND new_phase='sent_manually'",
        (operation_id,),
    ).fetchone()[0] == 2
    registry.close()
    alarms: list[str] = []
    monitor = registry_snapshot(tmp_path / "manual-final.sqlite3", alarms, now=NOW)
    assert monitor["invariants"]["projection_drift"] == 0
    assert monitor["invariants"]["sent_without_provider_evidence"] == 0
    assert alarms == []


def test_restore_gate_blocks_direct_mail_and_sheets_poller_invocation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    marker = tmp_path / "RESTORE_RECONCILIATION_REQUIRED"
    marker.write_text("blocked\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_RESTORE_GATE_PATH", str(marker))
    with pytest.raises(RestoreReconciliationRequired):
        poll(object(), object(), object())
    from google_sheets_lead_poller import process
    with pytest.raises(RestoreReconciliationRequired):
        process(Namespace())


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_known_incident_stuck_fixture_is_critical_in_monitor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    registry, event, operation_id = seed_operation(tmp_path, source_key="incident")
    registry.connection.execute(
        "UPDATE unified_outbox SET lease_expires_at=?,last_error='em_dash_present,followup_re_greets' WHERE operation_id=?",
        ((NOW - timedelta(minutes=10)).isoformat(), operation_id),
    )
    registry.close()
    root = tmp_path / "release"
    root.mkdir()
    (root / "HERMES_RELEASE_COMMIT").write_text("commit-1\n", encoding="utf-8")
    (root / "HERMES_RELEASE_TAG").write_text("v-test\n", encoding="utf-8")
    skill = root / "skills" / "mail-lead-pipeline" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# anonymous skill\n", encoding="utf-8")
    image_digest = "sha256:" + "1" * 64
    manifest = build_manifest(root, commit="commit-1", tag="v-test", image_digest=image_digest)
    write_json(root / "BEHAVIOR_MANIFEST.json", manifest)
    health = tmp_path / "health.json"
    write_json(health, {"mailbox": {"last_ok_at": NOW.isoformat()}, "google_sheets": {"last_ok_at": NOW.isoformat()}})
    backup = tmp_path / "backup.json"
    write_json(backup, {
        "created_at": NOW.isoformat(), "offsite_verified": True, "offsite_retention_days": 30,
        "offsite_retention_applied_at": NOW.isoformat(),
    })
    schedulers = tmp_path / "schedulers.json"
    write_json(schedulers, {
        "mailbox": {"active_count": 1, "active_process_count": 0, "entrypoint": "/opt/data/scripts/orchesta-rfq-mail-poller.sh", "lock": "/opt/data/.tmp/hermes-rfq-mail-poller.lock"},
        "google_sheets": {"active_count": 1, "active_process_count": 0, "entrypoint": "/opt/data/scripts/google-sheets-lead-poller.sh", "lock": "/opt/data/.tmp/google-sheets-lead-poller.lock"},
        "claim_reaper": {"active_count": 1, "active_process_count": 0, "entrypoint": "/opt/data/scripts/hermes-rfq-claim-reaper.sh", "lock": "/opt/data/.tmp/hermes-rfq-claim-reaper.lock"},
        "release_container": {"active_count": 1, "running": True, "exact_image_match": True},
    })
    monkeypatch.setenv("HERMES_RELEASE_DIGEST", image_digest)
    report = collect_snapshot(
        registry_file=tmp_path / "incident.sqlite3", source_health_file=health,
        backup_metadata_file=backup, scheduler_inventory_file=schedulers, release_root=root,
        behavior_manifest_file=root / "BEHAVIOR_MANIFEST.json", active_app_root=root, now=NOW,
    )
    assert report["status"] == "critical"
    assert {"stale_claimed", "expired_lease"}.issubset(report["alarms"])


def test_anonymous_conversation_corpus_covers_required_boundaries():
    corpus_path = Path(__file__).parent / "fixtures" / "hermes-rfq" / "anonymous_conversations.json"
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    covered = {tag for case in corpus for tag in case["tags"]}
    required = {
        "yes", "short_reply", "multi_message_facts", "fact_change", "quoted_history", "signature",
        "phone", "signature_amount", "html_only", "attachment", "forward", "reply_to", "cc",
        "same_subject", "pl", "en", "de", "two_rfqs_same_customer", "parallel_mail",
        "reply_after_restart", "english_final_quote", "quoted_old_deal_id",
    }
    assert required.issubset(covered)
    assert all("example.invalid" in case["from"] for case in corpus)
