from __future__ import annotations

import pytest
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXECUTION = ROOT / "execution"
if str(EXECUTION) not in sys.path:
    sys.path.insert(0, str(EXECUTION))

from pipeline_state import PipelineState  # noqa: E402
from zoho_mail_poller import (  # noqa: E402
    FakeZohoClient,
    build_pre_offer_send_creator,
    load_classifier,
    poll,
)
from zoho_pre_offer_send import APPROVAL_PHRASE  # noqa: E402
from unified_lead_registry import UnifiedLeadRegistry  # noqa: E402


def dataset() -> dict:
    return {
        "accounts": [{"accountId": "acc-1", "primaryEmailAddress": "rfq-mailbox@example.invalid"}],
        "folders": [
            {"folderId": "inbox-1", "folderName": "Inbox", "folderType": "Inbox"},
            {"folderId": "drafts-1", "folderName": "Drafts", "folderType": "Drafts"},
            {"folderId": "sent-1", "folderName": "Sent", "folderType": "Sent"},
        ],
        "messages": [
            {
                "messageId": "m-pre-offer-1",
                "folderId": "inbox-1",
                "threadId": "thread-pre-offer-1",
                "fromAddress": "Anna Example <anna@example.com>",
                "subject": "Zapytanie ofertowe o Orchesta RFQ",
                "hasAttachment": "0",
                "receivedTime": "1750001000000",
                "_content": "<p>Proszę o informacje o systemie do obsługi zapytań ofertowych.</p>",
                "_header": "Message-ID: <pre-offer-1@example.com>\n",
                "_attachmentinfo": [],
            }
        ],
    }


def follow_up_dataset() -> dict:
    data = dataset()
    data["messages"][0].update({
        "messageId": "m-follow-up-1",
        "threadId": "thread-follow-up-1",
        "receivedTime": "4102444800000",
        "subject": "Re: Zapytanie ofertowe o Orchesta RFQ",
        "_content": "<p>Dziękuję. Proszę o informację, czego jeszcze Państwo potrzebują.</p>",
        "_header": (
            "Message-ID: <follow-up-1@example.com>\n"
            "In-Reply-To: <identity-091@customer-004.example.com>\n"
            "References: <identity-091@customer-004.example.com>\n"
        ),
    })
    return data


def ready_for_offer_dataset() -> dict:
    data = dataset()
    data["messages"][0].update({
        "messageId": "m-ready-offer-1",
        "threadId": "thread-ready-offer-1",
        "receivedTime": "4102444800000",
        "fromAddress": "Tomasz Nowak <tomasz@example.com>",
        "subject": "Komplet danych do Orchesta RFQ",
        "_content": (
            "<p>Dzień dobry, reprezentuję firmę Example Sp. z o.o.</p>"
            "<p>Liczba skrzynek pocztowych: 3. CRM: tak. Zapytania trafiają mailem i przez formularz. "
            "Obecnie proces prowadzimy ręcznie w Excelu. Obsługujemy 40 zapytań miesięcznie. "
            "Przesyłam przykładowe zapytania.</p>"
        ),
        "_header": "Message-ID: <ready-offer-1@example.com>\n",
    })
    return data


def test_mail_poller_sends_pre_offer_response_exactly_once(tmp_path: Path):
    calls: list[str] = []

    def fake_sender(account_id, envelope, result, rfc_id, references, **kwargs):
        calls.append(envelope["message_id"])
        return {"action": "sent", "status": 200, "external_message_id": "sent-1", "response": {}}

    state_path = tmp_path / "state.sqlite3"
    first = poll(
        FakeZohoClient(dataset()),
        PipelineState(state_path),
        load_classifier(),
        run_id="test-pre-offer-send-1",
        auto_send=True,
        send_creator=fake_sender,
    )
    second = poll(
        FakeZohoClient(dataset()),
        PipelineState(state_path),
        load_classifier(),
        run_id="test-pre-offer-send-2",
        auto_send=True,
        send_creator=fake_sender,
    )

    assert calls == ["m-pre-offer-1"]
    assert first["responses_sent"] == 1
    assert first["briefings"][0]["draft"]["action"] == "sent"
    assert second["responses_sent"] == 0
    assert second["already_processed"] == 1


def test_real_mail_workflow_persists_and_sends_ready_notice_exactly_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    class Poster:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def send_reply(self, account_id: str, source_message_id: str, payload: dict):
            self.calls.append(payload)
            return 200, {"data": {"messageId": "sent-follow-up"}}

    monkeypatch.setenv("HERMES_ALLOW_PRE_OFFER_SEND", "1")

    state = PipelineState(tmp_path / "follow-up-state.sqlite3")
    registry = UnifiedLeadRegistry(tmp_path / "follow-up-registry.sqlite3")
    poster = Poster()
    sender = build_pre_offer_send_creator(
        "rfq-mailbox@example.invalid", poster, APPROVAL_PHRASE,
        body_generator=lambda _context: {
            "body_text": "Dziękuję. Proszę jeszcze o liczbę obsługiwanych zapytań miesięcznie.",
            "validated": True,
        },
        unified_registry=registry,
    )
    first = poll(
        FakeZohoClient(follow_up_dataset()), state, load_classifier(),
        run_id="follow-up-1", auto_send=True, send_creator=sender, unified_registry=registry,
    )
    second = poll(
        FakeZohoClient(follow_up_dataset()), state, load_classifier(),
        run_id="follow-up-2", auto_send=True, send_creator=sender, unified_registry=registry,
    )

    event = registry.get_event("mail", "acc-1:m-follow-up-1")
    outbox = registry.connection.execute(
        "SELECT message_type,status FROM unified_outbox WHERE source_key=?",
        ("acc-1:m-follow-up-1",),
    ).fetchone()
    assert len(poster.calls) == 1
    assert first["responses_sent"] == 1
    assert second["already_processed"] == 1
    assert event["metadata"]["message_policy"]["effective_type"] == "ready_for_offer_notice"
    assert dict(outbox)["message_type"] == "ready_for_offer_notice"
    registry.close()


def test_real_mail_workflow_sends_ready_notice_once_then_creates_final_offer_draft(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    class Poster:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def send_reply(self, account_id: str, source_message_id: str, payload: dict):
            self.calls.append(payload)
            return 200, {"data": {"messageId": "sent-ready"}}

    final_drafts: list[str] = []

    def final_offer(account_id, envelope, result, rfc_id, references, **kwargs):
        final_drafts.append(envelope["message_id"])
        return {
            "action": "created", "status": 201,
            "response": {"data": {"messageId": "draft-final-1"}},
            "price_net_display": "6 400 zł", "scope_display": "3 konta i CRM",
            "draft_location": "Zoho Mail > Drafts", "pdf_attached": True,
            "manifest": {"version": 1},
        }

    state = PipelineState(tmp_path / "ready-state.sqlite3")
    registry = UnifiedLeadRegistry(tmp_path / "ready-registry.sqlite3")
    poster = Poster()
    monkeypatch.setenv("HERMES_ALLOW_PRE_OFFER_SEND", "1")
    sender = build_pre_offer_send_creator(
        "rfq-mailbox@example.invalid", poster, APPROVAL_PHRASE,
        body_generator=lambda _context: (_ for _ in ()).throw(AssertionError("ready notice must be deterministic")),
        unified_registry=registry,
    )
    first = poll(
        FakeZohoClient(ready_for_offer_dataset()), state, load_classifier(),
        run_id="ready-1", auto_send=True, send_creator=sender,
        auto_final_offer=True, final_offer_creator=final_offer, unified_registry=registry,
    )
    second = poll(
        FakeZohoClient(ready_for_offer_dataset()), state, load_classifier(),
        run_id="ready-2", auto_send=True, send_creator=sender,
        auto_final_offer=True, final_offer_creator=final_offer, unified_registry=registry,
    )

    event = registry.get_event("mail", "acc-1:m-ready-offer-1")
    outbox = registry.connection.execute(
        "SELECT message_type,status FROM unified_outbox WHERE source_key=?",
        ("acc-1:m-ready-offer-1",),
    ).fetchone()
    assert len(poster.calls) == 1
    assert final_drafts == ["m-ready-offer-1"]
    assert first["responses_sent"] == 1
    assert first["final_offer_pdf_created"] == 1
    assert second["already_processed"] == 1
    assert event["metadata"]["message_policy"]["effective_type"] == "ready_for_offer_notice"
    assert dict(outbox)["message_type"] == "ready_for_offer_notice"
    registry.close()


def test_real_pre_offer_creator_posts_a_threaded_reply(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    class Poster:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, dict]] = []

        def send_reply(self, account_id: str, source_message_id: str, payload: dict):
            self.calls.append((account_id, source_message_id, payload))
            return 200, {"data": {"messageId": "sent-followup-1"}}

    poster = Poster()
    monkeypatch.setenv("HERMES_ALLOW_PRE_OFFER_SEND", "1")
    registry = UnifiedLeadRegistry(tmp_path / "unified.sqlite3")
    source_key = "acc-1:inbound-followup-1"
    event = registry.register_event(
        source_type="mail", source_key=source_key, email="customer@example.com",
        company="Example", contact_name="Anna", content="Nie", relation="reply",
        thread_id="thread-followup-1",
        source_metadata={
            "provider": "zoho", "account_id": "acc-1",
            "source_message_id": "inbound-followup-1",
        },
    )
    registry.persist_message_policy(
        "mail", source_key, requested_type="clarification_request",
        effective_type="clarification_request", transport_mode="auto_send",
        reasons=["test"],
    )
    assert registry.claim_response(
        event["deal_id"], "response:followup", content_hash="followup-hash",
        operation_id="operation-followup", owner="test-owner",
        message_type="clarification_request", recipient="customer@example.com",
        source_type="mail", source_key=source_key, thread_id="thread-followup-1",
        marker="operation-followup",
    )
    creator = build_pre_offer_send_creator(
        "rfq-mailbox@example.invalid",
        poster,
        APPROVAL_PHRASE,
        body_generator=lambda _context: {
            "body_text": "Ile zapytań ofertowych przyjmują Państwo miesięcznie?",
            "validated": True,
        },
        unified_registry=registry,
    )
    result = creator(
        "acc-1",
        {
            "message_id": "inbound-followup-1",
            "from": "customer@example.com",
            "from_raw": "Anna <customer@example.com>",
            "subject": "Re: Orchesta RFQ",
            "domain": "example.com",
            "thread_id": "thread-followup-1",
            "deal_id": event["deal_id"],
            "registry_source_key": source_key,
            "message_type": "clarification_request",
        },
        {"classification": "existing_thread_reply", "draft_kind": "context_reply"},
        "<inbound-followup-1@example.com>",
        "<prior@example.com>",
        classifier_message={"body": "Nie"},
        operation_marker="operation-followup",
        operation_stage="response:followup",
    )

    assert result["action"] == "sent"
    assert result["external_message_id"] == "sent-followup-1"
    assert len(poster.calls) == 1
    assert poster.calls[0][1] == "inbound-followup-1"
    assert poster.calls[0][2]["action"] == "reply"
    registry.close()


def test_controlled_live_selection_touches_only_exact_message_and_sender(tmp_path: Path):
    controlled = dataset()
    controlled["messages"].append({
        "messageId": "m-real-customer",
        "folderId": "inbox-1",
        "threadId": "thread-real-customer",
        "fromAddress": "Real Customer <real@example.com>",
        "subject": "Nowe zapytanie ofertowe",
        "hasAttachment": "0",
        "receivedTime": "1750001000001",
        "_content": "Proszę o informacje.",
        "_header": "Message-ID: <real-customer@example.com>\n",
        "_attachmentinfo": [],
    })
    calls: list[str] = []

    def fake_sender(account_id, envelope, result, rfc_id, references, **kwargs):
        calls.append(envelope["message_id"])
        return {"action": "sent", "status": 200, "external_message_id": "sent-controlled", "response": {}}

    state = PipelineState(tmp_path / "controlled-state.sqlite3")
    summary = poll(
        FakeZohoClient(controlled),
        state,
        load_classifier(),
        run_id="controlled-live-selection",
        controlled_source_message_id="m-pre-offer-1",
        controlled_sender="anna@example.com",
        auto_send=True,
        send_creator=fake_sender,
    )

    assert calls == ["m-pre-offer-1"]
    assert summary["controlled_selection_enabled"] is True
    assert summary["controlled_selection_matched"] == 1
    assert summary["controlled_selection_skipped"] == 1
    assert state.is_processed("m-pre-offer-1") is True
    assert state.is_processed("m-real-customer") is False


def test_controlled_live_selection_fails_closed_on_sender_mismatch(tmp_path: Path):
    calls: list[str] = []
    state = PipelineState(tmp_path / "controlled-mismatch.sqlite3")
    summary = poll(
        FakeZohoClient(dataset()),
        state,
        load_classifier(),
        controlled_source_message_id="m-pre-offer-1",
        controlled_sender="different@example.com",
        auto_send=True,
        send_creator=lambda *args, **kwargs: calls.append("called"),
    )

    assert calls == []
    assert summary["controlled_selection_matched"] == 0
    assert summary["controlled_selection_skipped"] == 1
    assert state.is_processed("m-pre-offer-1") is False


def test_controlled_live_selection_requires_both_identifiers(tmp_path: Path):
    with pytest.raises(ValueError, match="controlled_selection_requires_message_id_and_sender"):
        poll(
            FakeZohoClient(dataset()),
            PipelineState(tmp_path / "controlled-invalid.sqlite3"),
            load_classifier(),
            controlled_source_message_id="m-pre-offer-1",
        )


def test_in_progress_send_after_restart_is_not_repeated_automatically(tmp_path: Path):
    calls: list[str] = []

    def fake_sender(*args, **kwargs):
        calls.append("called")
        return {"action": "sent", "status": 200, "external_message_id": "sent-2", "response": {}}

    state = PipelineState(tmp_path / "state.sqlite3")
    state.plan_operation("m-pre-offer-1", "customer_send", input_hash="in", content_hash="out")
    state.update_operation("m-pre-offer-1", "customer_send", "in_progress")

    summary = poll(
        FakeZohoClient(dataset()),
        state,
        load_classifier(),
        run_id="test-pre-offer-unknown",
        auto_send=True,
        send_creator=fake_sender,
    )

    assert calls == []
    assert summary["send_outcome_unknown"] == 1
    assert summary["responses_sent"] == 0
    assert summary["briefings"][0]["draft"]["action"] == "send_outcome_unknown"
    assert state.operation("m-pre-offer-1", "customer_send")["status"] == "outcome_unknown"

    repeated = poll(
        FakeZohoClient(dataset()),
        state,
        load_classifier(),
        run_id="test-pre-offer-still-unknown",
        auto_send=True,
        send_creator=fake_sender,
    )
    assert calls == []
    assert repeated["send_outcome_unknown"] == 1
    assert state.operation("m-pre-offer-1", "customer_send")["status"] == "outcome_unknown"
