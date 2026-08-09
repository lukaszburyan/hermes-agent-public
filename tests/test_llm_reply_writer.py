from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXECUTION = ROOT / "execution"
if str(EXECUTION) not in sys.path:
    sys.path.insert(0, str(EXECUTION))

import llm_reply_writer as lrw
from llm_intent_classifier import FixtureLLMClient


GOOD = {
    "subject": "Re: Pytanie o system",
    "body": "Dzień dobry Panie Krzysztofie,\n\ndziękuję za wiadomość. Żeby ocenić dopasowanie systemu do Państwa firmy, proszę podać nazwę firmy lub stronę internetową oraz opisać, jak dziś obsługują Państwo wiadomości od klientów.",
    "needs_human_review": False,
    "review_reason": None,
}


def test_schema_and_prompt_files_exist():
    assert lrw.SYSTEM_PROMPT_PATH.exists()
    assert lrw.SCHEMA_PATH.exists()
    assert lrw.load_system_prompt()
    assert lrw.load_schema()


def test_validate_reply_accepts_spec_example():
    lrw.validate_reply(GOOD)


def test_validate_reply_rejects_missing_body():
    import jsonschema
    bad = {"subject": "Re: x", "needs_human_review": False}
    try:
        lrw.validate_reply(bad)
        raise AssertionError("schema should reject missing body")
    except jsonschema.ValidationError:
        pass


def test_write_reply_returns_validated_reply_and_keeps_action():
    result = lrw.write_reply(
        approved_action="ask_discovery_questions",
        customer_message="Czy ten system pasuje do naszej firmy?",
        thread_history="",
        saved_data={},
        missing_data=["company_name_or_website", "current_process"],
        conversation_stage="discovery",
        tenant_id="orchesta",
        is_first_agent_reply=True,
        llm_client=FixtureLLMClient(response=json.dumps(GOOD)),
        run_id="t-1",
    )
    assert result["approved_action"] == "ask_discovery_questions"
    assert result["body"] == GOOD["body"]
    assert result["needs_human_review"] is False
    assert result["attempts"] == 1


def test_write_reply_never_repeats_the_same_prompt_and_repair_gets_context():
    client = FixtureLLMClient(responses=["not json", json.dumps(GOOD)])
    first = lrw.write_reply(
        approved_action="reply_directly",
        customer_message="x",
        thread_history="",
        saved_data={"company_name": "ABC"},
        missing_data=["current_process"],
        conversation_stage="discovery",
        tenant_id="orchesta",
        is_first_agent_reply=False,
        llm_client=client,
    )
    assert first["state"] == "awaiting_human"
    result = lrw.write_reply(
        approved_action="reply_directly",
        customer_message="x",
        thread_history="",
        saved_data={"company_name": "ABC"},
        missing_data=["current_process"],
        conversation_stage="discovery",
        tenant_id="orchesta",
        is_first_agent_reply=False,
        llm_client=client,
        repair_context={
            "previous_body": "invalid",
            "validation_error_codes": ["invalid_json"],
            "offending_spans": [],
        },
    )
    assert result["attempts"] == 1
    assert result["body"] == GOOD["body"]


def test_write_reply_bad_parse_routes_to_awaiting_human_without_identical_retry():
    client = FixtureLLMClient(responses=["not json", "{still not json"])
    result = lrw.write_reply(
        approved_action="reply_directly",
        customer_message="x",
        thread_history="",
        saved_data={},
        missing_data=[],
        conversation_stage="discovery",
        tenant_id="orchesta",
        is_first_agent_reply=False,
        llm_client=client,
    )
    assert result["state"] == "awaiting_human"
    assert result["decision_reason"] == "llm_reply_failed"
    assert result["reply"] is None


def test_write_reply_unknown_tenant_routes_to_tenant_not_resolved():
    result = lrw.write_reply(
        approved_action="reply_directly",
        customer_message="x",
        thread_history="",
        saved_data={},
        missing_data=[],
        conversation_stage="discovery",
        tenant_id="nonexistent_tenant",
        is_first_agent_reply=False,
        llm_client=FixtureLLMClient(),
    )
    assert result["decision_reason"] == "tenant_not_resolved"


def test_build_reply_payload_propagates_first_reply_flag_and_excludes_secrets():
    payload = lrw.build_reply_payload(
        approved_action="ask_discovery_questions",
        customer_message="x",
        thread_history="",
        saved_data={"company_name": "ABC"},
        missing_data=["current_process"],
        conversation_stage="discovery",
        tenant_id="orchesta",
        is_first_agent_reply=True,
    )
    assert payload["is_first_agent_reply"] is True
    assert payload["approved_action"] == "ask_discovery_questions"
    assert "internal_notifications" not in payload
    assert "telegram" not in json.dumps(payload).lower()


def test_build_reply_payload_normalizes_unknown_stage_to_discovery():
    payload = lrw.build_reply_payload(
        approved_action="reply_directly",
        customer_message="x",
        thread_history="",
        saved_data={},
        missing_data=[],
        conversation_stage="bogus_stage",
        tenant_id="orchesta",
        is_first_agent_reply=False,
    )
    assert payload["conversation_stage"] == "discovery"
