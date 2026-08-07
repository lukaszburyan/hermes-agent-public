from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXECUTION = ROOT / "execution"
if str(EXECUTION) not in sys.path:
    sys.path.insert(0, str(EXECUTION))

import customer_data_store as cds
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


def test_provided_and_missing_extraction():
    cls = {
        "provided_information": {"company_name": "ABC", "monthly_volume": 300, "team_size": 2},
        "missing_information": ["inquiry_channels", "current_process"],
    }
    assert cds.provided_information_from_classification(cls) == {
        "company_name": "ABC",
        "monthly_volume": 300,
        "team_size": 2,
    }
    assert cds.missing_information_from_classification(cls) == ["inquiry_channels", "current_process"]


def test_still_missing_filters_already_saved_fields(tmp_path: Path):
    facts = {"inquiry_channels": "mail", "company_name": "ABC"}
    still = cds.still_missing_fields(facts, ["inquiry_channels", "current_process", "company_name"])
    assert still == ["current_process"]


def test_still_missing_keeps_saved_false_boolean():
    assert cds.still_missing_fields({"crm": False}, ["crm", "monthly_volume"]) == ["monthly_volume"]


def test_question_fields_detects_discovery_questions():
    body = (
        "Czy uwzględnić integrację z CRM?\n"
        "Ile kont pocztowych ma śledzić system?\n"
        "Jak obecnie wygląda obsługa takich zapytań?\n"
        "Ile zapytań trafia do firmy miesięcznie?"
    )
    assert set(cds.question_fields_from_text(body)) == {
        "crm",
        "mailbox_count",
        "current_process",
        "monthly_volume",
    }


def test_declarative_fact_is_not_mistaken_for_a_previous_question():
    body = (
        "Obsługa obejmuje 40 zapytań miesięcznie. "
        "Czy uwzględnić integrację z CRM w ofercie? "
        "Ile kont pocztowych ma śledzić system?"
    )
    assert set(cds.question_fields_from_text(body)) == {"crm", "mailbox_count"}


def test_contextual_short_boolean_replies_require_single_expected_field():
    assert cds.contextual_short_reply_facts("tak", ["crm"]) == {"crm": True}
    assert cds.contextual_short_reply_facts("OK", ["crm"]) == {"crm": True}
    assert cds.contextual_short_reply_facts("zgadza się", ["crm"]) == {"crm": True}
    assert cds.contextual_short_reply_facts("nie", ["crm"]) == {"crm": False}
    assert cds.contextual_short_reply_facts("tak", ["crm", "mailbox_count"]) == {"crm": True}
    assert cds.contextual_short_reply_facts("tak", []) == {}


def test_contextual_short_number_requires_unambiguous_previous_question():
    assert cds.contextual_short_reply_facts("2", ["mailbox_count"]) == {"mailbox_count": 2}
    assert cds.contextual_short_reply_facts("2", ["monthly_volume"]) == {"monthly_volume": 2}
    assert cds.contextual_short_reply_facts("2", ["mailbox_count", "monthly_volume"]) == {}
    assert cds.contextual_short_reply_facts("2", []) == {}


def test_extract_facts_uses_short_reply_context_only_when_safe():
    assert cds.extract_facts_from_customer_text("2", expected_fields=["mailbox_count"])["mailbox_count"] == 2
    assert "mailbox_count" not in cds.extract_facts_from_customer_text("2")
    assert cds.extract_facts_from_customer_text("nie", expected_fields=["crm"])["crm"] is False


def test_mailbox_count_does_not_invent_or_conflict_with_inquiry_channel():
    facts = cds.extract_facts_from_customer_text(
        "Dwa konta pocztowe. CRM tak.",
        deal={"company": "Example"},
        known_offer_facts_fn=lambda _text: {
            "mailbox_count": {"state": "known", "value": 2},
            "crm": {"state": "known", "value": True},
            "inquiry_source": {"state": "unknown"},
        },
    )
    assert facts["mailbox_count"] == 2
    assert facts["crm"] is True
    assert "inquiry_channels" not in facts


def test_save_customer_reply_adds_new_fields_with_provenance(tmp_path: Path):
    registry = UnifiedLeadRegistry(tmp_path / "reg.sqlite3")
    deal_id = _seed_deal(registry)
    summary = cds.save_customer_reply(
        registry=registry,
        deal_id=deal_id,
        classification={"provided_information": {"company_name": "ABC", "monthly_volume": 300}},
        source_message_id="msg-1",
        source_type="mail",
        occurred_at="2026-08-03T11:30:00+02:00",
    )
    assert summary["added"] == ["company_name", "monthly_volume"]
    assert summary["conflict_kept"] == []
    deal = registry.get_deal(deal_id)
    assert deal["facts"]["company_name"] == "ABC"
    assert deal["facts"]["monthly_volume"] == 300
    prov = registry.fact_provenance(deal_id)
    assert len(prov) == 2
    assert {row["field"] for row in prov} == {"company_name", "monthly_volume"}
    for row in prov:
        assert row["source_message_id"] == "msg-1"
        assert row["action"] == "added"


def test_save_customer_reply_does_not_silently_overwrite(tmp_path: Path):
    registry = UnifiedLeadRegistry(tmp_path / "reg.sqlite3")
    deal_id = _seed_deal(registry)
    cds.save_customer_reply(
        registry=registry,
        deal_id=deal_id,
        classification={"provided_information": {"monthly_volume": 300}},
        source_message_id="msg-1",
        occurred_at="2026-08-03T11:30:00+02:00",
    )
    # A later reply gives a DIFFERENT monthly_volume. The previous value must
    # be preserved (controlled update) and a conflict recorded.
    summary = cds.save_customer_reply(
        registry=registry,
        deal_id=deal_id,
        classification={"provided_information": {"monthly_volume": 999}},
        source_message_id="msg-2",
        occurred_at="2026-08-04T09:00:00+02:00",
    )
    assert summary["conflict_kept"] == ["monthly_volume"]
    deal = registry.get_deal(deal_id)
    assert deal["facts"]["monthly_volume"] == 300  # previous value preserved
    prov = registry.fact_provenance(deal_id)
    conflict_rows = [row for row in prov if row["action"] == "conflict_kept"]
    assert len(conflict_rows) == 1
    assert conflict_rows[0]["previous_value"] == "300"
    assert conflict_rows[0]["new_value"] == "999"
    assert conflict_rows[0]["source_message_id"] == "msg-2"


def test_save_customer_reply_records_unchanged_for_same_value(tmp_path: Path):
    registry = UnifiedLeadRegistry(tmp_path / "reg.sqlite3")
    deal_id = _seed_deal(registry)
    cds.save_customer_reply(
        registry=registry,
        deal_id=deal_id,
        classification={"provided_information": {"company_name": "ABC"}},
        source_message_id="msg-1",
    )
    summary = cds.save_customer_reply(
        registry=registry,
        deal_id=deal_id,
        classification={"provided_information": {"company_name": "ABC"}},
        source_message_id="msg-2",
    )
    assert summary["unchanged"] == ["company_name"]
    assert summary["added"] == []
    assert summary["conflict_kept"] == []


def test_save_customer_reply_reports_still_missing(tmp_path: Path):
    registry = UnifiedLeadRegistry(tmp_path / "reg.sqlite3")
    deal_id = _seed_deal(registry)
    summary = cds.save_customer_reply(
        registry=registry,
        deal_id=deal_id,
        classification={
            "provided_information": {"company_name": "ABC"},
            "missing_information": ["company_name", "current_process", "inquiry_channels"],
        },
        source_message_id="msg-1",
    )
    # company_name was just saved -> not still missing
    assert summary["still_missing"] == ["current_process", "inquiry_channels"]


def test_save_customer_reply_empty_provided_is_noop(tmp_path: Path):
    registry = UnifiedLeadRegistry(tmp_path / "reg.sqlite3")
    deal_id = _seed_deal(registry)
    summary = cds.save_customer_reply(
        registry=registry,
        deal_id=deal_id,
        classification={"provided_information": {}, "missing_information": ["current_process"]},
        source_message_id="msg-1",
    )
    assert summary["added"] == []
    assert summary["still_missing"] == ["current_process"]


def test_registry_returns_latest_automatic_reply_for_exact_thread(tmp_path: Path):
    registry = UnifiedLeadRegistry(tmp_path / "reg.sqlite3")
    deal_id = _seed_deal(registry)
    registry.observe_conversation_message(
        deal_id,
        account_id="acc-1",
        thread_id="thread-1",
        message_id="auto-1",
        direction="outbound",
        origin="hermes_automatic",
        occurred_at="2026-08-04T09:00:00+00:00",
        metadata={"body": "Czy uwzględnić integrację z CRM?"},
    )
    registry.observe_conversation_message(
        deal_id,
        account_id="acc-1",
        thread_id="thread-other",
        message_id="auto-2",
        direction="outbound",
        origin="hermes_automatic",
        occurred_at="2026-08-04T10:00:00+00:00",
        metadata={"body": "Ile kont pocztowych ma śledzić system?"},
    )
    reply = registry.last_automatic_reply(deal_id, account_id="acc-1", thread_id="thread-1")
    assert reply is not None
    assert reply["message_id"] == "auto-1"
    assert reply["metadata"]["body"].startswith("Czy uwzględnić")
