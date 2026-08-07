from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXECUTION = ROOT / "execution"
if str(EXECUTION) not in sys.path:
    sys.path.insert(0, str(EXECUTION))

import conversation_continuity as cc
from unified_lead_registry import UnifiedLeadRegistry


def _seed_deal(registry: UnifiedLeadRegistry, email: str = "krzysztof@example.com") -> str:
    event = registry.register_event(
        source_type="mail",
        source_key=f"acc-1:seed-{email}",
        email=email,
        company="",
        contact_name="Krzysztof",
        content="seed",
        relation="new",
        thread_id="thread-1",
        tenant_id="orchesta",
    )
    return str(event.get("deal_id") or "")


def test_default_state_is_first_agent_reply():
    s = cc.default_state("deal-1")
    assert s["agent_reply_count"] == 0
    assert s["customer_message_count"] == 0
    assert s["is_first_agent_reply"] is True
    assert s["acknowledgement_already_sent"] is False
    assert s["conversation_stage"] == "discovery"


def test_derive_is_first_agent_reply_from_count():
    assert cc.derive_is_first_agent_reply(0) is True
    assert cc.derive_is_first_agent_reply(1) is False
    assert cc.derive_is_first_agent_reply(5) is False


def test_on_customer_message_bumps_count_and_keeps_first_reply_flag():
    s = cc.on_customer_message(cc.default_state("d"), occurred_at="2026-08-03T11:30:00+02:00")
    assert s["customer_message_count"] == 1
    assert s["is_first_agent_reply"] is True
    assert s["last_customer_reply_at"] == "2026-08-03T11:30:00+02:00"


def test_on_agent_reply_sets_first_reply_postconditions():
    s = cc.on_agent_reply(cc.default_state("d"), occurred_at="2026-08-03T11:31:00+02:00")
    assert s["agent_reply_count"] == 1
    assert s["is_first_agent_reply"] is False
    assert s["acknowledgement_already_sent"] is True
    assert s["last_agent_reply_at"] == "2026-08-03T11:31:00+02:00"


def test_subsequent_customer_message_does_not_re_enable_first_reply():
    s0 = cc.default_state("d")
    s1 = cc.on_agent_reply(s0, occurred_at="t1")
    s2 = cc.on_customer_message(s1, occurred_at="t2")
    assert s2["is_first_agent_reply"] is False
    assert s2["customer_message_count"] == 1
    assert s2["agent_reply_count"] == 1


def test_is_new_thread_cases():
    assert cc.is_new_thread(
        has_prior_thread_headers=False, same_deal_id=True, same_topic=True, previous_deal_closed=False
    ) is True
    assert cc.is_new_thread(
        has_prior_thread_headers=True, same_deal_id=True, same_topic=True, previous_deal_closed=False
    ) is False
    assert cc.is_new_thread(
        has_prior_thread_headers=True, same_deal_id=False, same_topic=True, previous_deal_closed=False
    ) is True
    assert cc.is_new_thread(
        has_prior_thread_headers=True, same_deal_id=True, same_topic=True, previous_deal_closed=True
    ) is True
    assert cc.is_new_thread(
        has_prior_thread_headers=True, same_deal_id=True, same_topic=False, previous_deal_closed=False
    ) is True


def test_conversation_stage_for():
    assert cc.conversation_stage_for(missing_qualification_fields=["x"], missing_final_offer_fields=[]) == "discovery"
    assert cc.conversation_stage_for(missing_qualification_fields=[], missing_final_offer_fields=["y"]) == "qualification"
    assert cc.conversation_stage_for(missing_qualification_fields=[], missing_final_offer_fields=[]) == "final_offer"


def test_registry_conversation_state_roundtrip(tmp_path: Path):
    registry = UnifiedLeadRegistry(tmp_path / "reg.sqlite3")
    deal_id = _seed_deal(registry)
    state = registry.get_conversation_state(deal_id)
    assert state["agent_reply_count"] == 0
    assert state["is_first_agent_reply"] is True

    after_customer = registry.record_customer_message(deal_id, occurred_at="2026-08-03T11:30:00+02:00")
    assert after_customer["customer_message_count"] == 1
    assert after_customer["is_first_agent_reply"] is True

    after_reply = registry.record_agent_reply(deal_id, occurred_at="2026-08-03T11:31:00+02:00")
    assert after_reply["agent_reply_count"] == 1
    assert after_reply["is_first_agent_reply"] is False
    assert after_reply["acknowledgement_already_sent"] is True

    # Reload to confirm persistence.
    reloaded = registry.get_conversation_state(deal_id)
    assert reloaded["agent_reply_count"] == 1
    assert reloaded["is_first_agent_reply"] is False
    assert reloaded["acknowledgement_already_sent"] is True
    assert reloaded["customer_message_count"] == 1
