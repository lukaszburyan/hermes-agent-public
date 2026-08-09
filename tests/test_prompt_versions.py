from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXECUTION = ROOT / "execution"
if str(EXECUTION) not in sys.path:
    sys.path.insert(0, str(EXECUTION))

import llm_intent_classifier as lic
import llm_reply_writer as lrw


class _OKClient:
    def complete(self, system_prompt, user_prompt, temperature=0.0):
        return """{
          "intent": "product_fit_inquiry",
          "confidence": "high",
          "risk": "low",
          "sender_identity": "free_email_unverified",
          "recommended_action": "ask_discovery_questions",
          "provided_information": {},
          "missing_information": ["company_name_or_website"]
        }"""


class _ReplyOKClient:
    def complete(self, system_prompt, user_prompt, temperature=0.0):
        return """{
          "subject": "Re: zapytanie",
          "body": "Dzień dobry, dziękuję za wiadomość.",
          "needs_human_review": false,
          "review_reason": null
        }"""


def test_classifier_prompt_version_constant_matches_spec():
    assert lic.PROMPT_VERSION == "incoming-classifier-v1"
    assert lic.PROMPT_FILE == "prompts/incoming_mail_classifier_system.md"
    assert (ROOT / lic.PROMPT_FILE).exists()


def test_classifier_outcome_records_prompt_version():
    out = lic.classify(
        subject="zapytanie",
        body="chciałem zapytać o system",
        sender="klient@example.com",
        recipient="rfq-mailbox@example.invalid",
        tenant_id="orchesta",
        llm_client=_OKClient(),
        run_id="r1",
    )
    assert out["prompt_version"] == "incoming-classifier-v1"
    assert out["prompt_file"] == "prompts/incoming_mail_classifier_system.md"


def test_classifier_failed_outcome_records_prompt_version():
    class _Bad:
        def complete(self, system_prompt, user_prompt, temperature=0.0):
            return "not json"
    out = lic.classify(
        subject="x", body="y", sender="a@b.c", recipient="rfq-mailbox@example.invalid",
        tenant_id="orchesta", llm_client=_Bad(), run_id="r2",
    )
    assert out["prompt_version"] == "incoming-classifier-v1"
    assert out["classification"] is None


def test_reply_prompt_version_constant_matches_spec():
    assert lrw.PROMPT_VERSION == (
        f"incoming-reply-v3:{lrw.REPLY_CONTRACT_VERSION}:{lrw.REPLY_CONTRACT_DIGEST[:12]}"
    )
    assert lrw.PROMPT_FILE == "prompts/incoming_mail_reply_system.md"
    assert (ROOT / lrw.PROMPT_FILE).exists()


def test_reply_outcome_records_prompt_version():
    out = lrw.write_reply(
        approved_action="ask_discovery_questions",
        customer_message="Prowadzę firmę handlową.",
        thread_history="",
        saved_data={},
        missing_data=["company_name_or_website"],
        conversation_stage="discovery",
        tenant_id="orchesta",
        is_first_agent_reply=True,
        llm_client=_ReplyOKClient(),
        run_id="r3",
    )
    assert out["prompt_version"] == lrw.PROMPT_VERSION
    assert out["prompt_file"] == "prompts/incoming_mail_reply_system.md"


def test_reply_failed_outcome_records_prompt_version():
    class _Bad:
        def complete(self, system_prompt, user_prompt, temperature=0.0):
            return "not json"
    out = lrw.write_reply(
        approved_action="ask_discovery_questions",
        customer_message="x",
        thread_history="",
        saved_data={},
        missing_data=[],
        conversation_stage="discovery",
        tenant_id="orchesta",
        is_first_agent_reply=False,
        llm_client=_Bad(),
        run_id="r4",
    )
    assert out["prompt_version"] == lrw.PROMPT_VERSION
    assert out["reply"] is None
