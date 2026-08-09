from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXECUTION = ROOT / "execution"
if str(EXECUTION) not in sys.path:
    sys.path.insert(0, str(EXECUTION))

import llm_intent_classifier as intent
import orchesta_rfq_email_notify as notify
import pipeline_state
import send_reconciliation
import tenant_config
import unified_lead_registry
import zoho_mail_poller as mail
import zoho_reply_draft as drafts


def load_final_offer_module():
    path = ROOT / "skills" / "rfq-final-offer" / "scripts" / "rfq_final_offer.py"
    spec = importlib.util.spec_from_file_location("rfq_final_offer_business_readiness", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_unknown_pricing_inputs_create_an_assumption_ready_offer():
    readiness = tenant_config.resolve_offer_readiness(
        "orchesta",
        {
            "company_name_or_website": "Example Sp. z o.o.",
            "monthly_volume": {"state": "unknown_confirmed"},
            "mailbox_count": {"state": "unknown_confirmed"},
            "crm": {"state": "unknown_confirmed"},
        },
    )

    assert readiness["status"] == "ready_with_assumptions"
    assert readiness["resolved_facts"]["mailbox_count"] == 1
    assert readiness["resolved_facts"]["crm"] is False
    assert readiness["fact_states"]["monthly_volume"]["state"] == "unknown_confirmed"
    assert {item["field"] for item in readiness["assumptions"]} == {"mailbox_count", "crm"}


def test_conflicting_offer_fact_blocks_without_guessing():
    readiness = tenant_config.resolve_offer_readiness(
        "orchesta",
        {
            "company_name_or_website": "Example Sp. z o.o.",
            "mailbox_count": {"state": "conflicting"},
            "crm": False,
        },
    )
    assert readiness["status"] == "blocked"
    assert readiness["blocking_fields"] == ["mailbox_count"]


def test_missing_company_still_requires_one_clarification():
    readiness = tenant_config.resolve_offer_readiness(
        "orchesta",
        {"mailbox_count": {"state": "unknown_confirmed"}, "crm": {"state": "unknown_confirmed"}},
    )
    assert readiness["status"] == "needs_clarification"
    assert readiness["clarification_fields"] == ["company_name_or_website"]


def test_customer_unknown_statements_are_persistable_states():
    facts = mail.known_offer_facts(
        "Nie znamy jeszcze miesięcznego wolumenu, liczby skrzynek ani decyzji o CRM."
    )
    assert facts["monthly_volume"]["state"] == "unknown_confirmed"
    assert facts["mailbox_count"]["state"] == "unknown_confirmed"
    assert facts["crm"]["state"] == "unknown_confirmed"


def test_final_offer_helper_applies_starting_variant_instead_of_blocking():
    helper = load_final_offer_module()
    data = helper.sample_data(1, False)
    data["scope"].pop("mailbox_count")
    data["scope"].pop("crm")
    data["scope"]["mailbox_count_state"] = "unknown_confirmed"
    data["scope"]["crm_state"] = "unknown_confirmed"

    resolved, readiness = helper.apply_offer_readiness(data)
    context = helper.build_offer_context(resolved, helper.load_json(helper.DEFAULT_PRICING_PATH))

    assert helper.validate_required_data(resolved) == []
    assert readiness["status"] == "ready_with_assumptions"
    assert context["scope"]["mailbox_count"] == 1
    assert context["scope"]["crm"] is False
    assert context["scope"]["crm_label"] == "opcja do potwierdzenia"
    html = helper.render_offer_html(context)
    assert "Założenia do potwierdzenia" in html
    assert f"razem {context['pricing']['optional_variants'][0]['total_if_selected_display']} netto" in html


def test_script_risk_is_a_floor_for_model_risk():
    classification = {
        "intent": "product_fit_inquiry",
        "confidence": "high",
        "risk": "low",
        "sender_identity": "known",
        "provided_information": {},
        "missing_information": [],
        "recommended_action": "reply_directly",
        "decision_reasons": [],
        "language": "pl",
        "salutation_name": "",
        "salutation_form": "",
    }
    raised = intent.apply_script_risk_floor(classification, {"script_risk": "high"})
    assert raised["risk"] == "high"
    assert intent.decide_action(raised) == "block_security"

    kept = intent.apply_script_risk_floor({**classification, "risk": "high"}, {"script_risk": "low"})
    assert kept["risk"] == "high"


def test_approved_signature_is_exact_and_visible_marker_is_gone():
    assert drafts.SIGNATURE == (
        "--\nŁukasz Buryan\n\n"
        "tel. +48 000 000 000\n"
        "LinkedIn. example.invalid/orchesta-rfq"
    )
    payload = drafts.build_draft_payload(
        account_email="identity-004@customer-004.example.com",
        to_address="client@example.com",
        inbound_subject="Oferta",
        rfc_message_id="<message@example.com>",
        body_text="Dzień dobry,\n\ndziękuję za wiadomość.",
        draft_kind="final_offer",
    )
    assert "Łukasz Buryan" in payload["content"]
    assert "Orchesta RFQ Team" not in payload["content"]

    marked = send_reconciliation.append_marker(payload, "operation-1")
    assert "HERMESREF" not in marked["content"]
    assert "Reference:" not in marked["content"]


def test_one_business_change_produces_one_plain_notification():
    summary = {
        "source": "mailbox",
        "briefings": [{
            "message_id": "message-1",
            "rfq_id": "RFQ-20990101-0001",
            "classification": "existing_thread_reply",
            "sender": {"email": "client@example.com", "relationship": "active_thread"},
            "draft": {"action": "blocked_human_takeover", "source_message_id": "message-1"},
            "routing_action": "review",
            "next_step": "Przeczytaj odpowiedź klienta i zdecyduj, czy kontynuować rozmowę ręcznie.",
        }],
    }
    built = notify.build_notifications(summary)
    assert len(built) == 1
    subject, body = built[0][2], built[0][3]
    assert "RFQ-20990101-0001" in subject
    assert "Sprawa: RFQ-20990101-0001" in body
    assert "existing_thread_reply" not in body
    assert "manual review" not in body.lower()
    assert "human takeover" not in body.lower()
    assert "Każde powiadomienie" not in body


def test_current_briefing_and_its_durable_task_still_produce_one_notification():
    summary = {
        "source": "mailbox",
        "briefings": [{
            "message_id": "message-1",
            "deal_id": "deal-1",
            "rfq_id": "RFQ-20990101-0001",
            "classification": "existing_thread_reply",
            "sender": {"email": "client@example.com", "relationship": "active_thread"},
            "draft": {"action": "would_create_pending_approval", "source_message_id": "message-1"},
            "next_step": "Sprawdź wiadomość i zdecyduj o dalszej odpowiedzi.",
        }],
    }
    task = {
        "task_id": "review-1",
        "deal_id": "deal-1",
        "source_key": "account-1:message-1",
        "operation_id": "review:deal-1",
        "reason_codes": ["customer_message_requires_action"],
    }
    enriched = notify.append_durable_review_tasks(summary, [task])
    assert len(enriched["briefings"]) == 1
    assert len(notify.build_notifications(enriched)) == 1


def test_noop_summary_does_not_select_historical_review_backlog():
    tasks = [{
        "task_id": "review-old",
        "deal_id": "deal-old",
        "source_key": "account-1:message-old",
        "operation_id": "review:deal-old",
        "notification_status": "pending",
    }]
    assert notify.review_tasks_for_summary({"source": "mailbox", "briefings": []}, tasks) == []


def test_notification_selects_only_current_pending_review_task():
    summary = {
        "source": "mailbox",
        "briefings": [{
            "message_id": "message-current",
            "deal_id": "deal-current",
            "classification": "human_review_only",
            "routing_action": "review",
            "draft": {"action": "would_create_pending_approval"},
        }],
    }
    tasks = [
        {
            "task_id": "review-current",
            "deal_id": "deal-current",
            "source_key": "account-1:message-current",
            "operation_id": "review:deal-current",
            "notification_status": "pending",
        },
        {
            "task_id": "review-old",
            "deal_id": "deal-old",
            "source_key": "account-1:message-old",
            "operation_id": "review:deal-old",
            "notification_status": "pending",
        },
        {
            "task_id": "review-already-sent",
            "deal_id": "deal-current",
            "source_key": "account-1:message-current",
            "operation_id": "review:deal-current-sent",
            "notification_status": "sent",
        },
    ]
    selected = notify.review_tasks_for_summary(summary, tasks)
    assert [task["task_id"] for task in selected] == ["review-current"]


def test_existing_thread_reply_cannot_be_removed_by_legacy_class_override(monkeypatch):
    monkeypatch.setenv("HERMES_AUTO_SEND_CLASSES", "new_quote_request")
    assert "existing_thread_reply" in mail.configured_auto_send_classes()


def test_manual_approval_is_terminal_and_does_not_loop_or_duplicate_review_task():
    dataset = {
        "accounts": [{"accountId": "acc-1", "primaryEmailAddress": mail.DEFAULT_TARGET_EMAIL}],
        "folders": [{"folderId": "inbox-1", "folderName": "Inbox"}],
        "messages": [{
            "messageId": "manual-approval-1",
            "folderId": "inbox-1",
            "threadId": "manual-thread-1",
            "fromAddress": "Anna <anna@example.com>",
            "subject": "Prośba o ofertę Orchesta RFQ",
            "hasAttachment": "0",
            "receivedTime": "1800000000000",
            "_content": "<p>Proszę o przygotowanie oferty dla naszej firmy.</p>",
            "_header": "Message-ID: <manual-approval-1@example.com>\n",
        }],
    }

    with tempfile.TemporaryDirectory() as tmp:
        state = pipeline_state.PipelineState(Path(tmp) / "mail.sqlite3")
        registry = unified_lead_registry.UnifiedLeadRegistry(Path(tmp) / "registry.sqlite3")
        first = mail.poll(
            mail.FakeZohoClient(dataset),
            state,
            mail.load_classifier(),
            unified_registry=registry,
            run_id="manual-run-1",
        )
        second = mail.poll(
            mail.FakeZohoClient(dataset),
            state,
            mail.load_classifier(),
            unified_registry=registry,
            run_id="manual-run-2",
        )
        third = mail.poll(
            mail.FakeZohoClient(dataset),
            state,
            mail.load_classifier(),
            unified_registry=registry,
            run_id="manual-run-3",
        )
        review_tasks = registry.connection.execute(
            "SELECT COUNT(*) AS count FROM unified_review_tasks"
        ).fetchone()["count"]

    record = state.get("manual-approval-1")
    assert first["briefings"][0]["draft"]["action"] == "would_create_pending_approval"
    assert record["status"] == "manual_review"
    assert record["business_outcome"] == "manual_action_required"
    assert second["already_processed"] == 1 and second["briefings"] == []
    assert third["already_processed"] == 1 and third["briefings"] == []
    assert review_tasks == 1


def test_only_explicit_technical_failures_are_retryable():
    assert mail.fallback_business_outcome("would_create_pending_approval") == "manual_action_required"
    assert mail.fallback_business_outcome("blocked_human_takeover") == "manual_action_required"
    assert mail.fallback_business_outcome("none") == "terminal_discard"
    assert mail.fallback_business_outcome(
        "auto_draft_failed", operation_statuses=["retryable_failed"]
    ) == "retry_scheduled"
    assert mail.fallback_business_outcome("send_outcome_unknown") == "outcome_unknown"
