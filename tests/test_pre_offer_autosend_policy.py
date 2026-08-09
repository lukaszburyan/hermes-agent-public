from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXECUTION = ROOT / "execution"
if str(EXECUTION) not in sys.path:
    sys.path.insert(0, str(EXECUTION))

import zoho_pre_offer_send as pre_offer_transport  # noqa: E402
from unified_lead_registry import UnifiedLeadRegistry  # noqa: E402
from zoho_pre_offer_send import (  # noqa: E402
    APPROVAL_PHRASE,
    build_pre_offer_payload,
    create_pre_offer_message,
)


class FakePoster:
    def __init__(self, status: int = 200, response: dict | None = None) -> None:
        self.status = status
        self.response = response or {"data": {"messageId": "sent-123"}}
        self.reply_calls: list[tuple[str, str, dict]] = []
        self.message_calls: list[tuple[str, dict]] = []

    def send_reply(self, account_id: str, source_message_id: str, payload: dict):
        self.reply_calls.append((account_id, source_message_id, payload))
        return self.status, self.response

    def send_message(self, account_id: str, payload: dict):
        self.message_calls.append((account_id, payload))
        return self.status, self.response


def reply_payload() -> dict:
    return build_pre_offer_payload(
        account_email="rfq-mailbox@example.invalid",
        to_address="client@example.com",
        inbound_subject="Zapytanie o Orchesta RFQ",
        body_text="Dzień dobry,\n\nDziękuję za wiadomość. Ile kont pocztowych ma obsługiwać system?\n\nPozdrawiam,\nOrchesta RFQ Team",
        message_kind="discovery",
        threaded=True,
    )


def durable_context(
    tmp_path: Path,
    message_type: str = "clarification_request",
    *,
    threaded: bool = True,
    email: str = "client@example.com",
):
    registry = UnifiedLeadRegistry(tmp_path / f"registry-{message_type}-{threaded}.sqlite3")
    source_type = "mail" if threaded else "google_sheets"
    source_key = "mail-source-1" if threaded else "sheet-source-1"
    event = registry.register_event(
        source_type=source_type,
        source_key=source_key,
        email=email,
        company="Example",
        contact_name="Anna",
        content="Zapytanie o Orchesta RFQ",
        relation="reply" if threaded else "new",
        thread_id="thread-1" if threaded else "",
        source_metadata={
            "provider": "zoho" if threaded else "google_sheets",
            "account_id": "acc-1" if threaded else "",
            "source_message_id": "source-1" if threaded else "",
        },
    )
    mode = "draft_only" if message_type == "final_offer" else "auto_send"
    registry.persist_message_policy(
        source_type,
        source_key,
        requested_type=message_type,
        effective_type=message_type,
        transport_mode=mode,
        reasons=["test_policy"],
    )
    assert registry.claim_response(
        event["deal_id"],
        "response:test",
        content_hash="test-content-hash",
        operation_id="operation-1",
        owner="test-owner",
        message_type=message_type,
        recipient=email,
        source_type=source_type,
        source_key=source_key,
        thread_id="thread-1" if threaded else "",
        marker="operation-1",
    )
    context = {
        "registry": registry,
        "source_type": source_type,
        "source_key": source_key,
        "deal_id": event["deal_id"],
        "thread_id": "thread-1" if threaded else "",
        "operation_id": "operation-1",
        "process_stage": "response:test",
    }
    return registry, context


def test_builds_threaded_reply_payload_without_draft_or_attachment_fields():
    payload = reply_payload()
    assert payload["action"] == "reply"
    assert payload["subject"] == "Re: Zapytanie o Orchesta RFQ"
    assert payload["toAddress"] == "client@example.com"
    assert payload["mailFormat"] == "html"
    assert "mode" not in payload
    assert "attachments" not in payload


def test_builds_new_message_send_payload_for_form_or_sheet_lead():
    payload = build_pre_offer_payload(
        account_email="rfq-mailbox@example.invalid",
        to_address="client@example.com",
        inbound_subject="Nowy lead z formularza",
        body_text="Dzień dobry,\n\nDziękuję za kontakt. Jak wygląda obecny proces obsługi zapytań?\n\nPozdrawiam,\nOrchesta RFQ Team",
        message_kind="first_response",
        threaded=False,
        subject_override="Dziękuję za zgłoszenie dotyczące Orchesta RFQ",
    )
    assert payload["mode"] == "send"
    assert payload["subject"] == "Dziękuję za zgłoszenie dotyczące Orchesta RFQ"
    assert "action" not in payload
    assert "attachments" not in payload


def test_ready_for_offer_notice_is_an_approved_operational_type():
    payload = build_pre_offer_payload(
        account_email="rfq-mailbox@example.invalid",
        to_address="client@example.com",
        inbound_subject="Komplet danych",
        body_text="Dane są kompletne. Przekazuję sprawę do przygotowania kolejnego kroku.",
        message_kind="ready_for_offer_notice",
        threaded=True,
    )
    assert payload["action"] == "reply"


@pytest.mark.parametrize("kind", ["final_offer", "offer", "price_quote"])
def test_final_offer_kinds_can_never_be_built_for_autosend(kind: str):
    with pytest.raises(PermissionError, match="final_offer_autosend_forbidden"):
        build_pre_offer_payload(
            account_email="rfq-mailbox@example.invalid",
            to_address="client@example.com",
            inbound_subject="Oferta",
            body_text="Dzień dobry,\n\nOferta jest gotowa.\n\nPozdrawiam,\nOrchesta RFQ Team",
            message_kind=kind,
            threaded=True,
        )


def test_price_in_customer_message_is_blocked_from_autosend():
    with pytest.raises(PermissionError, match="price_content_autosend_forbidden"):
        build_pre_offer_payload(
            account_email="rfq-mailbox@example.invalid",
            to_address="client@example.com",
            inbound_subject="Zapytanie",
            body_text="Dzień dobry, cena wynosi 5000 PLN netto. Pozdrawiam, Orchesta RFQ Team",
            message_kind="discovery",
            threaded=True,
        )


@pytest.mark.parametrize("field", ["attachments", "attachment", "attachmentIds", "uploadedAttachments"])
def test_any_attachment_shaped_payload_is_blocked_at_transport_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    field: str,
):
    monkeypatch.setenv("HERMES_ALLOW_PRE_OFFER_SEND", "1")
    registry, context = durable_context(tmp_path)
    payload = reply_payload()
    payload[field] = ["attachment-1"]
    poster = FakePoster()
    result = create_pre_offer_message(
        "acc-1",
        source_message_id="source-1",
        payload=payload,
        poster=poster,
        approval=APPROVAL_PHRASE,
        message_kind="clarification_request",
        threaded=True,
        durable_context=context,
    )
    assert poster.reply_calls == []
    assert result["action"] == "blocked"
    assert "pre_offer_attachments_forbidden" in result["reason"]
    assert registry.outbox_operation("operation-1")["status"] == "validation_failed"
    assert registry.review_tasks()[0]["validation_errors"] == ["pre_offer_attachments_forbidden"]
    registry.close()


def test_durable_manual_review_mode_cannot_be_sent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    monkeypatch.setenv("HERMES_ALLOW_PRE_OFFER_SEND", "1")
    registry, context = durable_context(tmp_path)
    event = registry.get_event("mail", "mail-source-1")
    assert event is not None
    metadata = event["metadata"]
    metadata["message_policy"]["transport_mode"] = "manual_review"
    registry.connection.execute(
        "UPDATE unified_events SET metadata_json=? WHERE source_type=? AND source_key=?",
        (json.dumps(metadata, sort_keys=True), "mail", "mail-source-1"),
    )
    poster = FakePoster()
    result = create_pre_offer_message(
        "acc-1",
        source_message_id="source-1",
        payload=reply_payload(),
        poster=poster,
        approval=APPROVAL_PHRASE,
        message_kind="clarification_request",
        threaded=True,
        durable_context=context,
    )
    assert result["action"] == "blocked"
    assert result["reason"] == "durable_transport_not_authorized"
    assert poster.reply_calls == []
    assert registry.security_events("durable_transport_not_authorized")
    registry.close()


def test_send_gate_must_be_enabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("HERMES_ALLOW_PRE_OFFER_SEND", raising=False)
    poster = FakePoster()
    result = create_pre_offer_message(
        "acc-1",
        source_message_id="source-1",
        payload=reply_payload(),
        poster=poster,
        approval=APPROVAL_PHRASE,
        message_kind="discovery",
        threaded=True,
    )
    assert result["action"] == "blocked"
    assert result["reason"] == "pre_offer_send_gate_disabled"
    assert poster.reply_calls == []


def test_threaded_pre_offer_message_is_sent_once_and_returns_external_id(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("HERMES_ALLOW_PRE_OFFER_SEND", "1")
    poster = FakePoster()
    registry, context = durable_context(tmp_path)
    result = create_pre_offer_message(
        "acc-1",
        source_message_id="source-1",
        payload=reply_payload(),
        poster=poster,
        approval=APPROVAL_PHRASE,
        message_kind="clarification_request",
        threaded=True,
        durable_context=context,
    )
    assert result["action"] == "sent"
    assert result["external_message_id"] == "sent-123"
    assert len(poster.reply_calls) == 1
    assert poster.message_calls == []
    registry.close()


def test_non_threaded_pre_offer_message_uses_send_endpoint(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("HERMES_ALLOW_PRE_OFFER_SEND", "1")
    payload = build_pre_offer_payload(
        account_email="rfq-mailbox@example.invalid",
        to_address="client@example.com",
        inbound_subject="Lead",
        body_text="Dzień dobry,\n\nDziękuję za kontakt. Proszę o informację o liczbie skrzynek.\n\nPozdrawiam,\nOrchesta RFQ Team",
        message_kind="first_response",
        threaded=False,
    )
    poster = FakePoster()
    registry, context = durable_context(tmp_path, "acknowledgement", threaded=False)
    result = create_pre_offer_message(
        "acc-1",
        source_message_id="",
        payload=payload,
        poster=poster,
        approval=APPROVAL_PHRASE,
        message_kind="acknowledgement",
        threaded=False,
        durable_context=context,
    )
    assert result["action"] == "sent"
    assert len(poster.message_calls) == 1
    assert poster.reply_calls == []
    registry.close()


def test_transport_kill_switch_blocks_post_but_preserves_durable_event(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("HERMES_ALLOW_PRE_OFFER_SEND", "1")
    monkeypatch.setenv("HERMES_TRANSPORT_KILL_SWITCH", "1")
    registry, context = durable_context(tmp_path)
    poster = FakePoster()
    draft_calls: list[dict] = []

    def save_blocked_draft(**kwargs):
        draft_calls.append(kwargs)
        return {"action": "created", "draft_id": "draft-kill-switch-1"}

    result = create_pre_offer_message(
        "acc-1", source_message_id="source-1", payload=reply_payload(), poster=poster,
        approval=APPROVAL_PHRASE, message_kind="clarification_request", threaded=True,
        durable_context=context, blocked_draft_fallback=save_blocked_draft,
    )
    assert result["reason"] == "transport_kill_switch_enabled"
    assert result["draft_id"] == "draft-kill-switch-1"
    assert len(draft_calls) == 1
    assert poster.reply_calls == []
    assert registry.get_event("mail", "mail-source-1") is not None
    assert registry.security_events("transport_kill_switch_block")
    assert registry.outbox_operation("operation-1")["status"] == "draft_created"
    registry.close()


def test_monitor_kill_switch_file_blocks_post_without_container_restart(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    marker = tmp_path / "TRANSPORT_KILL_SWITCH"
    marker.write_text("critical\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_ALLOW_PRE_OFFER_SEND", "1")
    monkeypatch.setenv("HERMES_TRANSPORT_KILL_SWITCH", "0")
    monkeypatch.setenv("HERMES_TRANSPORT_KILL_SWITCH_FILE", str(marker))
    registry, context = durable_context(tmp_path)
    poster = FakePoster()

    result = create_pre_offer_message(
        "acc-1", source_message_id="source-1", payload=reply_payload(), poster=poster,
        approval=APPROVAL_PHRASE, message_kind="clarification_request", threaded=True,
        durable_context=context,
    )

    assert result["reason"] == "transport_kill_switch_enabled"
    assert poster.reply_calls == []
    assert registry.outbox_operation("operation-1")["status"] == "manual_review"
    assert registry.review_tasks()[0]["reason_codes"] == ["transport_kill_switch_enabled"]
    registry.close()


def test_test_mode_requires_allowlisted_recipient(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("HERMES_ALLOW_PRE_OFFER_SEND", "1")
    monkeypatch.setenv("HERMES_TEST_MODE", "1")
    monkeypatch.setenv("HERMES_TEST_RECIPIENT_ALLOWLIST", "other@example.com")
    registry, context = durable_context(tmp_path)
    poster = FakePoster()
    blocked = create_pre_offer_message(
        "acc-1", source_message_id="source-1", payload=reply_payload(), poster=poster,
        approval=APPROVAL_PHRASE, message_kind="clarification_request", threaded=True,
        durable_context=context,
    )
    assert blocked["reason"] == "test_recipient_not_allowed"
    assert poster.reply_calls == []
    registry.close()


def test_transport_accepts_dotted_gmail_when_durable_identity_is_canonicalized(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    recipient = "identity-004@gmail.com"
    monkeypatch.setenv("HERMES_ALLOW_PRE_OFFER_SEND", "1")
    monkeypatch.setenv("HERMES_TEST_MODE", "1")
    monkeypatch.setenv("HERMES_TEST_RECIPIENT_ALLOWLIST", recipient)
    registry, context = durable_context(
        tmp_path, message_type="acknowledgement", threaded=False, email=recipient
    )
    poster = FakePoster()
    payload = build_pre_offer_payload(
        account_email="rfq-mailbox@example.invalid",
        to_address=recipient,
        inbound_subject="Kontrolowany test Gmail",
        body_text="Dziękuję za wiadomość. Potwierdzam jej otrzymanie.",
        message_kind="acknowledgement",
        threaded=False,
        subject_override="Kontrolowany test Gmail",
        source_sender="rfq-mailbox@example.invalid",
    )
    result = create_pre_offer_message(
        "acc-1", source_message_id="", payload=payload, poster=poster,
        approval=APPROVAL_PHRASE, message_kind="acknowledgement", threaded=False,
        durable_context=context,
    )
    assert result["action"] == "sent", result
    assert len(poster.message_calls) == 1
    registry.close()


def test_transport_refetches_recipient_and_blocks_mismatch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("HERMES_ALLOW_PRE_OFFER_SEND", "1")
    registry, context = durable_context(tmp_path)
    registry.register_event(
        source_type="mail",
        source_key="mail-source-other-deal",
        email="other-deal@example.com",
        company="Other controlled deal",
        contact_name="Jan",
        content="Unrelated controlled event",
        relation="reply",
        thread_id="thread-other-deal",
        source_metadata={"provider": "zoho", "account_id": "acc-1", "source_message_id": "source-other"},
    )
    payload = reply_payload()
    payload["toAddress"] = "other-deal@example.com"
    poster = FakePoster()
    notifications: list[dict] = []
    result = create_pre_offer_message(
        "acc-1", source_message_id="source-1", payload=payload, poster=poster,
        approval=APPROVAL_PHRASE, message_kind="clarification_request", threaded=True,
        durable_context=context,
        human_notifier=notifications.append,
    )
    assert result["reason"] == "durable_recipient_mismatch"
    assert poster.reply_calls == []
    assert registry.security_events("durable_recipient_mismatch")
    assert registry.outbox_operation(context["operation_id"])["status"] == "manual_review"
    assert result["human_notified"] is True
    assert notifications[0]["reason"] == "durable_recipient_mismatch"
    registry.close()


def test_transport_blocks_recipient_changed_after_generation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("HERMES_ALLOW_PRE_OFFER_SEND", "1")
    registry, context = durable_context(tmp_path)
    registry.connection.execute(
        "UPDATE unified_deals SET primary_email=? WHERE deal_id=?",
        ("changed-after-generation@example.com", context["deal_id"]),
    )
    poster = FakePoster()
    result = create_pre_offer_message(
        "acc-1", source_message_id="source-1", payload=reply_payload(), poster=poster,
        approval=APPROVAL_PHRASE, message_kind="clarification_request", threaded=True,
        durable_context=context,
    )
    assert result["reason"] == "durable_recipient_mismatch"
    assert poster.reply_calls == []
    assert registry.security_events("durable_recipient_mismatch")
    registry.close()


def test_transport_blocks_durable_process_stage_mismatch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("HERMES_ALLOW_PRE_OFFER_SEND", "1")
    registry, context = durable_context(tmp_path)
    context["process_stage"] = "response:wrong-stage"
    poster = FakePoster()
    result = create_pre_offer_message(
        "acc-1", source_message_id="source-1", payload=reply_payload(), poster=poster,
        approval=APPROVAL_PHRASE, message_kind="clarification_request", threaded=True,
        durable_context=context,
    )
    assert result["reason"] == "durable_process_stage_mismatch"
    assert poster.reply_calls == []
    assert registry.security_events("durable_process_stage_mismatch")
    registry.close()


def test_transport_blocks_tampered_durable_contact_identity(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("HERMES_ALLOW_PRE_OFFER_SEND", "1")
    registry, context = durable_context(tmp_path)
    registry.connection.execute(
        "UPDATE unified_outbox SET contact_identity=? WHERE operation_id=?",
        ("email:other-contact@example.com", context["operation_id"]),
    )
    poster = FakePoster()
    result = create_pre_offer_message(
        "acc-1", source_message_id="source-1", payload=reply_payload(), poster=poster,
        approval=APPROVAL_PHRASE, message_kind="clarification_request", threaded=True,
        durable_context=context,
    )
    assert result["reason"] == "durable_contact_identity_mismatch"
    assert poster.reply_calls == []
    assert registry.security_events("durable_contact_identity_mismatch")
    registry.close()


def test_transport_refetches_durable_state_immediately_before_provider_call(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    monkeypatch.setenv("HERMES_ALLOW_PRE_OFFER_SEND", "1")
    registry, context = durable_context(tmp_path)
    original = pre_offer_transport._durable_transport_state
    calls = 0

    def mutate_before_second_read(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            registry.connection.execute(
                "UPDATE unified_deals SET primary_email=? WHERE deal_id=?",
                ("changed-during-validation@example.com", context["deal_id"]),
            )
        return original(*args, **kwargs)

    monkeypatch.setattr(pre_offer_transport, "_durable_transport_state", mutate_before_second_read)
    poster = FakePoster()
    result = create_pre_offer_message(
        "acc-1",
        source_message_id="source-1",
        payload=reply_payload(),
        poster=poster,
        approval=APPROVAL_PHRASE,
        message_kind="clarification_request",
        threaded=True,
        durable_context=context,
    )
    assert calls == 2
    assert result["action"] == "blocked"
    assert result["reason"] == "durable_recipient_mismatch"
    assert poster.reply_calls == []
    assert registry.security_events("durable_recipient_mismatch")
    registry.close()


def test_transport_blocks_deal_stage_changed_immediately_before_provider_call(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    monkeypatch.setenv("HERMES_ALLOW_PRE_OFFER_SEND", "1")
    registry, context = durable_context(tmp_path)
    original = pre_offer_transport._durable_transport_state
    calls = 0

    def mutate_before_second_read(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            registry.connection.execute(
                "UPDATE unified_deals SET status='offer_ready',updated_at=? WHERE deal_id=?",
                ("2099-01-01T00:00:00+00:00", context["deal_id"]),
            )
        return original(*args, **kwargs)

    monkeypatch.setattr(pre_offer_transport, "_durable_transport_state", mutate_before_second_read)
    poster = FakePoster()
    result = create_pre_offer_message(
        "acc-1", source_message_id="source-1", payload=reply_payload(), poster=poster,
        approval=APPROVAL_PHRASE, message_kind="clarification_request", threaded=True,
        durable_context=context,
    )
    assert calls == 2
    assert result["reason"] == "durable_deal_changed_before_send"
    assert poster.reply_calls == []
    assert registry.security_events("durable_deal_changed_before_send")
    registry.close()


@pytest.mark.parametrize(
    ("source_message_id", "context_thread", "expected_reason"),
    [
        ("wrong-source-message", "thread-1", "durable_source_message_mismatch"),
        ("source-1", "wrong-thread", "durable_thread_mismatch"),
    ],
)
def test_transport_blocks_wrong_source_message_or_thread(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    source_message_id: str,
    context_thread: str,
    expected_reason: str,
):
    monkeypatch.setenv("HERMES_ALLOW_PRE_OFFER_SEND", "1")
    registry, context = durable_context(tmp_path)
    context["thread_id"] = context_thread
    poster = FakePoster()
    result = create_pre_offer_message(
        "acc-1", source_message_id=source_message_id, payload=reply_payload(), poster=poster,
        approval=APPROVAL_PHRASE, message_kind="clarification_request", threaded=True,
        durable_context=context,
    )
    assert result["reason"] == expected_reason
    assert poster.reply_calls == []
    assert registry.security_events(expected_reason)
    registry.close()


def test_misclassified_final_offer_is_blocked_drafted_and_notified(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("HERMES_ALLOW_PRE_OFFER_SEND", "1")
    registry, context = durable_context(tmp_path)
    payload = build_pre_offer_payload(
        account_email="rfq-mailbox@example.invalid",
        to_address="client@example.com",
        inbound_subject="Zapytanie o Orchesta RFQ",
        body_text="Cena końcowa: 7 200 PLN. Warunki płatności: 50% zaliczki.",
        message_kind="clarification_request",
        threaded=True,
        route_final_offer_to_policy=True,
    )
    poster = FakePoster()
    drafts: list[dict] = []
    notifications: list[dict] = []
    result = create_pre_offer_message(
        "acc-1", source_message_id="source-1", payload=payload, poster=poster,
        approval=APPROVAL_PHRASE, message_kind="clarification_request", threaded=True,
        durable_context=context,
        draft_fallback=lambda **kwargs: drafts.append(kwargs) or {"action": "created", "draft_id": "draft-1"},
        human_notifier=notifications.append,
    )
    assert result["action"] == "blocked"
    assert result["reason"] == "final_offer_autosend_forbidden"
    assert result["draft"]["draft_id"] == "draft-1"
    assert result["human_notified"] is True
    assert len(drafts) == len(notifications) == 1
    assert poster.reply_calls == []
    event = registry.get_event("mail", "mail-source-1")
    assert event["metadata"]["message_type"] == "final_offer"
    assert registry.security_events("final_offer_autosend_attempt")
    registry.close()
