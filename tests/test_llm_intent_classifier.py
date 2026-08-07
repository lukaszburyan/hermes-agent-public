from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXECUTION = ROOT / "execution"
if str(EXECUTION) not in sys.path:
    sys.path.insert(0, str(EXECUTION))

import llm_intent_classifier as lic
from llm_intent_classifier import FixtureLLMClient


GOOD = {
    "intent": "product_fit_inquiry",
    "confidence": "high",
    "risk": "low",
    "sender_identity": "free_email_unverified",
    "provided_information": {"business_type": "handel wielobranżowy"},
    "missing_information": ["company_name_or_website", "current_process", "inquiry_channels"],
    "recommended_action": "ask_discovery_questions",
    "decision_reasons": ["business_interest_detected", "company_context_missing"],
    "language": "pl",
    "salutation_name": "Krzysztof",
    "salutation_form": "Panie Krzysztofie",
}


def test_schema_and_prompt_files_exist():
    assert lic.SYSTEM_PROMPT_PATH.exists()
    assert lic.SCHEMA_PATH.exists()
    assert lic.load_system_prompt()
    assert lic.load_schema()


def test_validate_classification_accepts_spec_example():
    lic.validate_classification(GOOD)


def test_validate_classification_rejects_unknown_intent():
    import jsonschema
    bad = dict(GOOD)
    bad["intent"] = "totally_made_up"
    try:
        lic.validate_classification(bad)
        raise AssertionError("schema should reject unknown intent")
    except jsonschema.ValidationError:
        pass


def test_decide_action_low_risk_missing_data_asks_discovery_questions():
    assert lic.decide_action(GOOD) == "ask_discovery_questions"


def test_decide_action_low_risk_complete_data_prepares_offer():
    complete = dict(GOOD)
    complete["missing_information"] = []
    complete["recommended_action"] = "prepare_offer"
    assert lic.decide_action(complete) == "prepare_offer"


def test_decide_action_medium_risk_drafts_for_human():
    medium = dict(GOOD)
    medium["risk"] = "medium"
    assert lic.decide_action(medium) == "draft_for_human"


def test_decide_action_high_risk_blocks_security():
    high = dict(GOOD)
    high["risk"] = "high"
    assert lic.decide_action(high) == "block_security"


def test_decide_action_automated_message_is_ignored():
    automated = dict(GOOD)
    automated["intent"] = "automated_message"
    assert lic.decide_action(automated) == "ignore_automated"


def test_decide_action_spam_blocks_security():
    spam = dict(GOOD)
    spam["intent"] = "spam_or_security_risk"
    assert lic.decide_action(spam) == "block_security"


def test_classify_with_fixture_returns_validated_classification():
    result = lic.classify(
        subject="Czy ten system pasuje do naszej firmy?",
        body="Prowadzę firmę handlową i chciałbym dowiedzieć się więcej.",
        sender="krzysztof@example.com",
        recipient="rfq-mailbox@example.invalid",
        tenant_id="orchesta",
        llm_client=FixtureLLMClient(response=json.dumps(GOOD)),
        run_id="test-1",
    )
    assert result["action"] == "ask_discovery_questions"
    assert result["classification"]["intent"] == "product_fit_inquiry"
    assert result["attempts"] == 1


def test_classify_retries_once_on_bad_json_then_succeeds():
    client = FixtureLLMClient(responses=["not json", json.dumps(GOOD)])
    result = lic.classify(
        subject="x",
        body="y",
        sender="a@b.c",
        recipient="rfq-mailbox@example.invalid",
        tenant_id="orchesta",
        llm_client=client,
    )
    assert result.get("attempts") == 2
    assert result["classification"]["intent"] == "product_fit_inquiry"


def test_classify_two_bad_parses_routes_to_awaiting_human():
    client = FixtureLLMClient(responses=["not json", "{still not json"])
    result = lic.classify(
        subject="x",
        body="y",
        sender="a@b.c",
        recipient="rfq-mailbox@example.invalid",
        tenant_id="orchesta",
        llm_client=client,
    )
    assert result["state"] == "awaiting_human"
    assert result["decision_reason"] == "llm_classification_failed"
    assert result["classification"] is None


def test_classify_unknown_tenant_routes_to_tenant_not_resolved():
    result = lic.classify(
        subject="x",
        body="y",
        sender="a@b.c",
        recipient="unknown@nowhere.test",
        tenant_id="nonexistent_tenant",
        llm_client=FixtureLLMClient(),
    )
    assert result["decision_reason"] == "tenant_not_resolved"


def test_build_user_payload_never_includes_notifications_or_other_tenants():
    payload = lic.build_user_payload(
        subject="x",
        body="y",
        sender="a@b.c",
        recipient="rfq-mailbox@example.invalid",
        safety_check={"thread_headers_valid": True},
        thread_history="",
        saved_deal_data={"mailbox_count": 4},
        tenant_id="orchesta",
    )
    assert "internal_notifications" not in payload
    assert "telegram" not in json.dumps(payload).lower()
    assert "firma_abc" not in json.dumps(payload)
    assert "mailbox_count" not in payload["product_knowledge"]  # product facts only
    assert "company_name_or_website" in payload["tenant_fields"]["offer"]
