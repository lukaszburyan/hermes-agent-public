from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / "execution" / "unified_lead_registry.py"
POLLER_PATH = ROOT / "execution" / "zoho_mail_poller.py"
STATE_PATH = ROOT / "execution" / "pipeline_state.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def register_mail(registry, *, key: str, thread: str, subject: str, relation: str = "new", facts=None):
    return registry.register_event(
        source_type="mail",
        source_key=key,
        email="anna@example.com",
        company="Example Sp. z o.o.",
        contact_name="Anna Example",
        content=f"Treść sprawy: {subject}",
        relation=relation,
        thread_id=thread,
        facts=facts or {},
        source_metadata={
            "account_id": "acc-1",
            "provider": "zoho",
            "thread_id": thread,
            "subject": subject,
            "occurred_at": "2026-07-20T10:00:00+00:00",
        },
    )


def mail_dataset(*, manual_sent: bool = False, body: str | None = None) -> dict:
    messages = [
        {
            "messageId": "in-1",
            "folderId": "inbox-1",
            "threadId": "thread-1",
            "fromAddress": "Anna Example <anna@example.com>",
            "subject": "Re: Orchesta RFQ dla Example",
            "hasAttachment": "0",
            "receivedTime": "1784541600000",
            "_content": body or "<p>Proszę o informacje o systemie Orchesta RFQ.</p>",
            "_header": "Message-ID: <in-1@example.com>\nIn-Reply-To: <identity-113@example.invalid>\nReferences: <identity-113@example.invalid>\n",
            "_attachmentinfo": [],
        }
    ]
    if manual_sent:
        messages.append(
            {
                "messageId": "human-sent-1",
                "folderId": "sent-1",
                "threadId": "thread-1",
                "fromAddress": "rfq-mailbox@example.invalid",
                "toAddress": "anna@example.com",
                "subject": "Re: Orchesta RFQ dla Example",
                "sentDateInGMT": "1784541660000",
                "hasAttachment": "0",
            }
        )
    return {
        "accounts": [{"accountId": "acc-1", "primaryEmailAddress": "rfq-mailbox@example.invalid"}],
        "folders": [
            {"folderId": "inbox-1", "folderName": "Inbox", "folderType": "Inbox"},
            {"folderId": "sent-1", "folderName": "Wysłane", "folderType": "Sent"},
            {"folderId": "drafts-1", "folderName": "Drafts", "folderType": "Drafts"},
        ],
        "messages": messages,
    }


def test_new_thread_with_distinct_subject_creates_separate_deal(tmp_path: Path):
    registry_mod = load_module("registry_new_thread_distinct", REGISTRY_PATH)
    registry = registry_mod.UnifiedLeadRegistry(tmp_path / "unified.sqlite3")
    first = register_mail(registry, key="m-1", thread="thread-1", subject="Wdrożenie RFQ w Example")
    second = register_mail(registry, key="m-2", thread="thread-2", subject="Osobny projekt serwisowy")

    assert second["correlation_outcome"] == "created"
    assert second["deal_id"] != first["deal_id"]
    assert "Wdrożenie RFQ w Example" not in registry.context_text(second["deal_id"])


def test_new_unanchored_unique_exact_subject_links_existing_deal(tmp_path: Path):
    registry_mod = load_module("registry_new_thread_exact_subject", REGISTRY_PATH)
    registry = registry_mod.UnifiedLeadRegistry(tmp_path / "unified.sqlite3")
    first = register_mail(registry, key="m-1", thread="thread-1", subject="Orchesta RFQ")
    second = register_mail(registry, key="m-2", thread="thread-2", subject="Re: Orchesta RFQ", relation="new")

    assert second["correlation_outcome"] == "linked"
    assert second["routing_action"] == "link_existing"
    assert second["deal_id"] == first["deal_id"]
    assert "hard:exact_normalized_subject" in second["routing_decision"]["evidence"]


def test_new_unanchored_no_thread_id_same_topic_links_to_existing_deal(tmp_path: Path):
    """Problem B: a fresh email with no threadId (Zoho withholds it on first
    contact) from an existing active customer with the same company and an
    overlapping topic links to the existing deal instead of creating a new one."""
    registry_mod = load_module("registry_unanchored_no_thread_links", REGISTRY_PATH)
    registry = registry_mod.UnifiedLeadRegistry(tmp_path / "unified.sqlite3")
    first = register_mail(registry, key="m-1", thread="thread-1", subject="Orchesta RFQ")
    # fresh email, no thread_id, same email/company/topic -> links (not a new deal)
    second = register_mail(registry, key="m-2", thread="", subject="Re: Orchesta RFQ", relation="new")

    assert second["correlation_outcome"] == "linked"
    assert second["deal_id"] == first["deal_id"]


def test_reply_in_bound_thread_stays_in_same_deal(tmp_path: Path):
    registry_mod = load_module("registry_known_thread_reply", REGISTRY_PATH)
    registry = registry_mod.UnifiedLeadRegistry(tmp_path / "unified.sqlite3")
    first = register_mail(registry, key="m-1", thread="thread-1", subject="Orchesta RFQ")
    reply = register_mail(registry, key="m-2", thread="thread-1", subject="Re: Orchesta RFQ", relation="reply")

    assert reply["correlation_outcome"] == "linked"
    assert reply["deal_id"] == first["deal_id"]


def test_poller_saves_terse_number_using_last_automatic_question(tmp_path: Path):
    poller = load_module("poller_terse_context", POLLER_PATH)
    registry_mod = load_module("registry_terse_context", REGISTRY_PATH)
    state_mod = load_module("state_terse_context", STATE_PATH)
    registry = registry_mod.UnifiedLeadRegistry(tmp_path / "unified.sqlite3")
    first = register_mail(
        registry,
        key="prior-terse",
        thread="thread-1",
        subject="Orchesta RFQ dla Example",
        facts={"crm": False, "inquiry_source": "mail"},
    )
    registry.observe_conversation_message(
        first["deal_id"],
        account_id="acc-1",
        thread_id="thread-1",
        message_id="auto-question-1",
        direction="outbound",
        origin="hermes_automatic",
        occurred_at="2026-07-20T10:01:00+00:00",
        metadata={"body": "Ile kont pocztowych ma śledzić system?"},
    )

    summary = poller.poll(
        poller.FakeZohoClient(mail_dataset(body="<p>2</p>")),
        state_mod.PipelineState(tmp_path / "mail.sqlite3"),
        poller.load_classifier(),
        unified_registry=registry,
    )

    assert summary["woken"] == 1
    assert registry.get_deal(first["deal_id"])["facts"]["mailbox_count"] == 2


def test_pause_resume_is_durable_and_audited(tmp_path: Path):
    registry_mod = load_module("registry_takeover_durable", REGISTRY_PATH)
    path = tmp_path / "unified.sqlite3"
    registry = registry_mod.UnifiedLeadRegistry(path)
    event = register_mail(registry, key="m-1", thread="thread-1", subject="Orchesta RFQ")
    deal_id = event["deal_id"]
    registry.pause_automation(
        deal_id, account_id="acc-1", thread_id="thread-1",
        reason="human_takeover", actor="sent_guard", evidence={"message_id": "human-sent-1"},
    )
    assert registry.get_deal(deal_id)["status"] == "review_required"
    assert registry.claim_response(deal_id, "response:blocked", content_hash="x") is False
    registry.close()

    reopened = registry_mod.UnifiedLeadRegistry(path)
    paused = reopened.automation_control(deal_id, account_id="acc-1", thread_id="thread-1")
    assert paused["state"] == "paused"
    assert paused["reason"] == "human_takeover"
    reopened.resume_automation(
        deal_id, account_id="acc-1", thread_id="thread-1",
        actor="lukasz", reason="manual_resume",
    )
    active = reopened.automation_control(deal_id, account_id="acc-1", thread_id="thread-1")
    assert active["state"] == "active"
    assert reopened.get_deal(deal_id)["status"] == "analysing"
    assert [item["new_state"] for item in reopened.automation_events(deal_id, "acc-1", "thread-1")] == ["paused", "active"]


def test_conversation_limits_and_scope_change_pause_automation(tmp_path: Path):
    registry_mod = load_module("registry_conversation_limits", REGISTRY_PATH)
    registry = registry_mod.UnifiedLeadRegistry(tmp_path / "unified.sqlite3")
    event = register_mail(
        registry, key="m-1", thread="thread-1", subject="Orchesta RFQ", facts={"mailbox_count": 2, "crm": False}
    )
    deal_id = event["deal_id"]
    start = datetime(2026, 7, 1, 10, tzinfo=timezone.utc)
    for index in range(7):
        registry.observe_conversation_message(
            deal_id, account_id="acc-1", thread_id="thread-1", message_id=f"msg-{index}",
            direction="inbound" if index % 2 == 0 else "outbound",
            origin="customer" if index % 2 == 0 else "hermes_automatic",
            occurred_at=(start + timedelta(hours=index)).isoformat(),
        )
    decision = registry.evaluate_automation(deal_id, account_id="acc-1", thread_id="thread-1", now=(start + timedelta(hours=9)).isoformat())
    assert decision["allowed"] is False
    assert decision["reason"] == "conversation_message_limit"

    registry.resume_automation(deal_id, account_id="acc-1", thread_id="thread-1", actor="test", reason="reset")
    changed = register_mail(
        registry, key="m-2", thread="thread-1", subject="Re: Orchesta RFQ", relation="reply",
        facts={"mailbox_count": 5, "crm": True},
    )
    assert changed["material_scope_change"] is True
    assert registry.get_deal(deal_id)["facts"] == {"crm": False, "mailbox_count": 2}
    decision = registry.evaluate_automation(
        deal_id, account_id="acc-1", thread_id="thread-1",
        material_scope_change=changed["material_scope_change"], now=(start + timedelta(hours=10)).isoformat(),
    )
    assert decision["allowed"] is False
    assert decision["reason"] == "material_scope_change"


def test_five_automatic_replies_block_pre_offer_but_not_final_offer(tmp_path: Path):
    registry_mod = load_module("registry_more_conversation_limits", REGISTRY_PATH)
    registry = registry_mod.UnifiedLeadRegistry(tmp_path / "unified.sqlite3")
    event = register_mail(registry, key="m-1", thread="thread-1", subject="Orchesta RFQ")
    deal_id = event["deal_id"]
    start = datetime(2026, 7, 1, 10, tzinfo=timezone.utc)
    for index in range(5):
        registry.observe_conversation_message(
            deal_id, account_id="acc-1", thread_id="thread-1", message_id=f"auto-{index}",
            direction="outbound", origin="hermes_automatic", occurred_at=(start + timedelta(hours=index)).isoformat(),
        )
    decision = registry.evaluate_automation(deal_id, account_id="acc-1", thread_id="thread-1", now=(start + timedelta(hours=4)).isoformat())
    assert decision["allowed"] is False
    assert decision["reason"] == "automatic_reply_limit"
    assert registry.automation_control(deal_id, account_id="acc-1", thread_id="thread-1")["state"] == "active"
    final_decision = registry.evaluate_automation(
        deal_id,
        account_id="acc-1",
        thread_id="thread-1",
        action_kind="final_offer",
        now=(start + timedelta(hours=4)).isoformat(),
    )
    assert final_decision["allowed"] is True


def test_six_messages_and_six_days_are_allowed_seven_messages_or_days_handoff(tmp_path: Path):
    registry_mod = load_module("registry_boundary_conversation_limits", REGISTRY_PATH)
    registry = registry_mod.UnifiedLeadRegistry(tmp_path / "unified.sqlite3")
    start = datetime(2026, 7, 1, 10, tzinfo=timezone.utc)

    six = register_mail(registry, key="six", thread="thread-six", subject="Orchesta RFQ")
    for index in range(6):
        registry.observe_conversation_message(
            six["deal_id"], account_id="acc-1", thread_id="thread-six", message_id=f"six-{index}",
            direction="inbound", origin="customer", occurred_at=(start + timedelta(hours=index)).isoformat(),
        )
    assert registry.evaluate_automation(
        six["deal_id"], account_id="acc-1", thread_id="thread-six", now=(start + timedelta(days=6)).isoformat(),
    )["allowed"] is True
    registry.observe_conversation_message(
        six["deal_id"], account_id="acc-1", thread_id="thread-six", message_id="seven",
        direction="outbound", origin="hermes_automatic", occurred_at=(start + timedelta(days=6)).isoformat(),
    )
    blocked = registry.evaluate_automation(
        six["deal_id"], account_id="acc-1", thread_id="thread-six", action_kind="final_offer",
        now=(start + timedelta(days=6)).isoformat(),
    )
    assert blocked["reason"] == "conversation_message_limit"
    assert registry.get_deal(six["deal_id"])["status"] == "conversation_handoff"

    aged = register_mail(registry, key="aged", thread="thread-aged", subject="Inny temat")
    registry.observe_conversation_message(
        aged["deal_id"], account_id="acc-1", thread_id="thread-aged", message_id="aged-1",
        direction="inbound", origin="customer", occurred_at=start.isoformat(),
    )
    assert registry.evaluate_automation(
        aged["deal_id"], account_id="acc-1", thread_id="thread-aged", now=(start + timedelta(days=6)).isoformat(),
    )["allowed"] is True
    aged_block = registry.evaluate_automation(
        aged["deal_id"], account_id="acc-1", thread_id="thread-aged", action_kind="final_offer",
        now=(start + timedelta(days=7)).isoformat(),
    )
    assert aged_block["reason"] == "conversation_age_limit"


def test_manual_sent_reply_blocks_auto_send_and_persists_takeover(tmp_path: Path):
    poller = load_module("poller_manual_sent_takeover", POLLER_PATH)
    registry_mod = load_module("registry_manual_sent_takeover", REGISTRY_PATH)
    state_mod = load_module("state_manual_sent_takeover", STATE_PATH)
    registry_path = tmp_path / "unified.sqlite3"
    registry = registry_mod.UnifiedLeadRegistry(registry_path)
    calls: list[str] = []

    summary = poller.poll(
        poller.FakeZohoClient(mail_dataset(manual_sent=True)),
        state_mod.PipelineState(tmp_path / "mail.sqlite3"), poller.load_classifier(),
        auto_send=True,
        send_creator=lambda *args, **kwargs: calls.append("sent") or {"action": "sent", "status": 200, "external_message_id": "auto-1"},
        unified_registry=registry,
    )

    assert calls == []
    assert summary["responses_sent"] == 0
    assert summary["human_takeovers"] == 1
    assert summary["briefings"][0]["draft"]["action"] == "blocked_human_takeover"
    deal_id = summary["briefings"][0]["deal_id"]
    registry.close()
    reopened = registry_mod.UnifiedLeadRegistry(registry_path)
    assert reopened.automation_control(deal_id, account_id="acc-1", thread_id="thread-1")["state"] == "paused"


def test_missing_sent_folder_fails_closed_before_customer_action(tmp_path: Path):
    poller = load_module("poller_sent_missing", POLLER_PATH)
    registry_mod = load_module("registry_sent_missing", REGISTRY_PATH)
    state_mod = load_module("state_sent_missing", STATE_PATH)
    data = mail_dataset()
    data["folders"] = [folder for folder in data["folders"] if folder["folderType"] != "Sent"]
    calls: list[str] = []

    summary = poller.poll(
        poller.FakeZohoClient(data), state_mod.PipelineState(tmp_path / "mail.sqlite3"), poller.load_classifier(),
        auto_send=True,
        send_creator=lambda *args, **kwargs: calls.append("sent") or {},
        unified_registry=registry_mod.UnifiedLeadRegistry(tmp_path / "unified.sqlite3"),
    )

    assert calls == []
    assert summary["sent_guard_failures"] == 1
    assert summary["briefings"][0]["draft"]["action"] == "sent_guard_failed"


def test_paused_conversation_blocks_final_offer_creator(tmp_path: Path):
    poller = load_module("poller_paused_final", POLLER_PATH)
    registry_mod = load_module("registry_paused_final", REGISTRY_PATH)
    state_mod = load_module("state_paused_final", STATE_PATH)
    registry = registry_mod.UnifiedLeadRegistry(tmp_path / "unified.sqlite3")
    initial = register_mail(
        registry, key="prior", thread="thread-1", subject="Orchesta RFQ",
        facts={"mailbox_count": 2, "crm": True, "inquiry_source": "mail"},
    )
    registry.pause_automation(
        initial["deal_id"], account_id="acc-1", thread_id="thread-1",
        reason="human_takeover", actor="lukasz", evidence={},
    )
    calls: list[str] = []

    summary = poller.poll(
        poller.FakeZohoClient(mail_dataset(body="<p>Dwa konta pocztowe. CRM tak.</p>")),
        state_mod.PipelineState(tmp_path / "mail.sqlite3"), poller.load_classifier(),
        auto_final_offer=True,
        final_offer_creator=lambda *args, **kwargs: calls.append("offer") or {"action": "created", "status": 201},
        unified_registry=registry,
    )

    assert calls == []
    assert summary["final_offer_attempted"] == 0
    assert summary["briefings"][0]["draft"]["action"] == "blocked_human_takeover"


class SentApiFailureClient:
    def __init__(self, base):
        self.base = base

    def __getattr__(self, name):
        return getattr(self.base, name)

    def list_messages(self, account_id: str, folder_id: str, limit: int):
        if folder_id == "sent-1":
            raise RuntimeError("simulated_sent_api_failure")
        return self.base.list_messages(account_id, folder_id, limit)


def test_sent_api_failure_is_fail_closed(tmp_path: Path):
    poller = load_module("poller_sent_api_failure", POLLER_PATH)
    registry_mod = load_module("registry_sent_api_failure", REGISTRY_PATH)
    state_mod = load_module("state_sent_api_failure", STATE_PATH)
    calls: list[str] = []
    client = SentApiFailureClient(poller.FakeZohoClient(mail_dataset()))

    summary = poller.poll(
        client,
        state_mod.PipelineState(tmp_path / "mail.sqlite3"),
        poller.load_classifier(),
        auto_send=True,
        send_creator=lambda *args, **kwargs: calls.append("sent") or {},
        unified_registry=registry_mod.UnifiedLeadRegistry(tmp_path / "unified.sqlite3"),
    )

    assert calls == []
    assert summary["sent_guard_failures"] == 1
    assert summary["briefings"][0]["draft"]["action"] == "sent_guard_failed"


def test_manual_sent_in_other_thread_does_not_take_over_current_thread(tmp_path: Path):
    poller = load_module("poller_sent_other_thread", POLLER_PATH)
    registry_mod = load_module("registry_sent_other_thread", REGISTRY_PATH)
    registry = registry_mod.UnifiedLeadRegistry(tmp_path / "unified.sqlite3")
    data = mail_dataset(manual_sent=True)
    for message in data["messages"]:
        if message.get("folderId") == "sent-1":
            message["threadId"] = "different-thread"
    client = poller.FakeZohoClient(data)
    event = register_mail(registry, key="current", thread="thread-1", subject="Orchesta RFQ")
    envelope = poller.normalize_envelope(data["messages"][0], default_folder_id="inbox-1")

    decision = poller.check_sent_before_action(
        client,
        "acc-1",
        data["folders"],
        envelope,
        registry,
        event["deal_id"],
    )

    assert decision["allowed"] is True
    control = registry.automation_control(event["deal_id"], account_id="acc-1", thread_id="thread-1")
    assert control["state"] == "active"
