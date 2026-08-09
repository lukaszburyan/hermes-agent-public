#!/usr/bin/env python3
"""LLM reply writer for incoming mail (spec section 12).

Shadow by default: off unless ``HERMES_LLM_REPLY_WRITER_ENABLED=1``. All tests
run on fixtures — no token-billed API is called from here (Guardrail 4). The
real LLM is reached only through a pluggable client routing via the Hermes
gateway / Codex CLI on the GPT Plus plan.

Files (spec section 12):
  - execution/llm_reply_writer.py
  - prompts/incoming_mail_reply_system.md
  - schemas/incoming_mail_reply.schema.json

The reply writer receives the **approved action** (decided by the script per
spec section 10) and writes the reply text only. It never changes the action.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

EXECUTION_DIR = Path(__file__).resolve().parent
ROOT_DIR = EXECUTION_DIR.parent
if str(EXECUTION_DIR) not in sys.path:
    sys.path.insert(0, str(EXECUTION_DIR))

import jsonschema  # noqa: E402
from hermes_rfq_core import REASON_LLM_REPLY_FAILED  # noqa: E402
from llm_intent_classifier import FixtureLLMClient, GatewayLLMClient, LLMClient, _strip_json_fence  # noqa: E402
from tenant_config import STATE_AWAITING_HUMAN  # noqa: E402
import tenant_config  # noqa: E402
from reply_contract import DEFAULT_REPLY_CONTRACT, REPLY_CONTRACT_DIGEST, REPLY_CONTRACT_VERSION  # noqa: E402

PROMPTS_DIR = ROOT_DIR / "prompts"
SCHEMAS_DIR = ROOT_DIR / "schemas"
SYSTEM_PROMPT_PATH = PROMPTS_DIR / "incoming_mail_reply_system.md"
SCHEMA_PATH = SCHEMAS_DIR / "incoming_mail_reply.schema.json"

LLM_REPLY_WRITER_ENABLED_FLAG = "HERMES_LLM_REPLY_WRITER_ENABLED"

# Spec section 23: prompt version recorded with every reply decision so the
# prompt file loaded in production is unambiguous and auditable.
PROMPT_VERSION = f"incoming-reply-v3:{REPLY_CONTRACT_VERSION}:{REPLY_CONTRACT_DIGEST[:12]}"
PROMPT_FILE = "prompts/incoming_mail_reply_system.md"

CONVERSATION_STAGES = ("discovery", "qualification", "final_offer")


def is_enabled() -> bool:
    return os.environ.get(LLM_REPLY_WRITER_ENABLED_FLAG, "0").strip() == "1"


def load_system_prompt() -> str:
    base = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8") if SYSTEM_PROMPT_PATH.exists() else ""
    return base + DEFAULT_REPLY_CONTRACT.prompt_fragment()


def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8")) if SCHEMA_PATH.exists() else {}


def validate_reply(payload: dict[str, Any]) -> None:
    schema = load_schema()
    if schema:
        normalized = dict(payload)
        normalized.setdefault("contract_version", REPLY_CONTRACT_VERSION)
        normalized.setdefault("message_language", "pl")
        jsonschema.validate(normalized, schema)


def reply_failed_outcome(*, run_id: str = "") -> dict[str, Any]:
    return {
        "state": STATE_AWAITING_HUMAN,
        "decision_reason": REASON_LLM_REPLY_FAILED,
        "subject": "",
        "body": "",
        "needs_human_review": True,
        "review_reason": "llm_reply_failed",
        "run_id": run_id,
        "reply": None,
        "prompt_version": PROMPT_VERSION,
        "prompt_file": PROMPT_FILE,
    }


def build_reply_payload(
    *,
    approved_action: str,
    customer_message: str,
    thread_history: str,
    saved_data: dict[str, Any] | None,
    missing_data: list[str] | None,
    conversation_stage: str,
    tenant_id: str,
    is_first_agent_reply: bool,
    repair_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the user-facing payload for the reply writer (spec section 12).

    Only the current tenant's style rules and knowledge fragment are included.
    Never secrets, Telegram, alarm settings, or another tenant's data.
    """
    conversation = tenant_config.load_conversation(tenant_id)
    products = tenant_config.load_products(tenant_id)
    knowledge_fragment = {
        "pricing_id": products.get("pricing_id"),
        "base_offer": products.get("base_offer"),
    }
    missing_list = list(missing_data or [])
    missing_questions = {
        field: tenant_config.discovery_questions(tenant_id, field, conversation.get("language", "pl"))
        for field in missing_list
        if tenant_config.discovery_questions(tenant_id, field, conversation.get("language", "pl"))
    }
    public_contract = DEFAULT_REPLY_CONTRACT.as_dict()
    public_contract.pop("forbidden_terms", None)
    payload = {
        "approved_action": approved_action,
        "customer_message": customer_message,
        "thread_history": thread_history,
        "saved_data": saved_data or {},
        "missing_data": missing_list,
        "missing_field_questions": missing_questions,
        "conversation_stage": conversation_stage if conversation_stage in CONVERSATION_STAGES else "discovery",
        "style_rules": {
            "language": conversation.get("language", "pl"),
            "tone": conversation.get("tone", "professional_direct"),
            "max_questions_per_reply": conversation.get("max_questions_per_reply", 2),
        },
        "knowledge_fragment": knowledge_fragment,
        "is_first_agent_reply": bool(is_first_agent_reply),
        "reply_contract": public_contract,
        "reply_contract_digest": REPLY_CONTRACT_DIGEST,
    }
    if repair_context:
        payload["repair_context"] = dict(repair_context)
        payload["repair_instruction"] = (
            "Apply only the smallest repair needed for the exact validation errors. "
            "Do not change facts, numbers, commercial meaning or the approved action."
        )
    return payload


def write_reply(
    *,
    approved_action: str,
    customer_message: str,
    thread_history: str,
    saved_data: dict[str, Any] | None,
    missing_data: list[str] | None,
    conversation_stage: str,
    tenant_id: str,
    is_first_agent_reply: bool,
    llm_client: LLMClient | None = None,
    run_id: str = "",
    repair_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Author a reply. Returns the validated reply JSON + the approved action.

    On two failed JSON parses returns ``reply_failed_outcome()``.
    """
    if not tenant_config.tenant_exists(tenant_id):
        outcome = reply_failed_outcome(run_id=run_id)
        outcome["decision_reason"] = tenant_config.REASON_TENANT_NOT_RESOLVED
        return outcome
    if not SCHEMA_PATH.exists():
        raise FileNotFoundError(f"reply schema missing: {SCHEMA_PATH}")
    client = llm_client or FixtureLLMClient()
    system_prompt = load_system_prompt()
    user_payload = build_reply_payload(
        approved_action=approved_action,
        customer_message=customer_message,
        thread_history=thread_history,
        saved_data=saved_data,
        missing_data=missing_data,
        conversation_stage=conversation_stage,
        tenant_id=tenant_id,
        is_first_agent_reply=is_first_agent_reply,
        repair_context=repair_context,
    )
    user_prompt = json.dumps(user_payload, ensure_ascii=False, indent=2)

    try:
        if hasattr(client, "complete_structured"):
            raw = client.complete_structured(
                system_prompt,
                user_prompt,
                response_schema=load_schema(),
                schema_name="incoming_mail_reply",
                mode="REPAIR_REPLY" if repair_context else "WRITE_REPLY",
                temperature=0.0,
            )
        else:
            raw = client.complete(system_prompt, user_prompt, temperature=0.0)
        parsed = json.loads(_strip_json_fence(raw))
        if "review_reason" not in parsed or parsed.get("review_reason") is False:
            parsed["review_reason"] = None
        parsed.setdefault("contract_version", REPLY_CONTRACT_VERSION)
        parsed.setdefault("message_language", str(user_payload["style_rules"]["language"] or "pl"))
        validate_reply(parsed)
        return {
            "reply": parsed,
            "approved_action": approved_action,
            "subject": parsed.get("subject", ""),
            "body": parsed.get("body", ""),
            "needs_human_review": bool(parsed.get("needs_human_review", False)),
            "review_reason": parsed.get("review_reason"),
            "message_language": parsed.get("message_language"),
            "contract_version": parsed.get("contract_version"),
            "run_id": run_id,
            "attempts": 1,
            "prompt_version": PROMPT_VERSION,
            "prompt_file": PROMPT_FILE,
        }
    except (json.JSONDecodeError, jsonschema.ValidationError, ValueError) as exc:
        last_error = f"attempt 1: {exc.__class__.__name__}: {exc}"
    outcome = reply_failed_outcome(run_id=run_id)
    outcome["error"] = last_error
    return outcome


def self_test() -> int:
    failures: list[str] = []
    if not SYSTEM_PROMPT_PATH.exists():
        failures.append("system prompt missing")
    if not SCHEMA_PATH.exists():
        failures.append("schema missing")
    if not load_system_prompt():
        failures.append("system prompt empty")
    schema = load_schema()
    if not schema:
        failures.append("schema empty")

    good = {
        "subject": "Re: Pytanie o system",
        "body": "Dzień dobry Panie Krzysztofie,\n\ndziękuję za wiadomość...",
        "needs_human_review": False,
        "review_reason": None,
    }
    validate_reply(good)

    fixture = FixtureLLMClient(response=json.dumps(good))
    result = write_reply(
        approved_action="ask_discovery_questions",
        customer_message="Czy ten system pasuje do naszej firmy?",
        thread_history="",
        saved_data={},
        missing_data=["company_name_or_website", "current_process"],
        conversation_stage="discovery",
        tenant_id="orchesta",
        is_first_agent_reply=True,
        llm_client=fixture,
        run_id="selftest-1",
    )
    if result.get("approved_action") != "ask_discovery_questions":
        failures.append("reply writer must not change the approved action")
    if result.get("body") != good["body"]:
        failures.append("fixture reply body mismatch")
    if result.get("needs_human_review") is not False:
        failures.append("needs_human_review should be False for the good fixture")

    bad = FixtureLLMClient(responses=["not json", "{still not json"])
    failed = write_reply(
        approved_action="reply_directly",
        customer_message="x",
        thread_history="",
        saved_data={},
        missing_data=[],
        conversation_stage="discovery",
        tenant_id="orchesta",
        is_first_agent_reply=False,
        llm_client=bad,
        run_id="selftest-2",
    )
    if failed.get("state") != STATE_AWAITING_HUMAN:
        failures.append("two bad parses should reach awaiting_human")
    if failed.get("decision_reason") != REASON_LLM_REPLY_FAILED:
        failures.append("two bad parses should set llm_reply_failed")

    payload = build_reply_payload(
        approved_action="ask_discovery_questions",
        customer_message="x",
        thread_history="",
        saved_data={"company_name": "ABC"},
        missing_data=["current_process"],
        conversation_stage="discovery",
        tenant_id="orchesta",
        is_first_agent_reply=True,
    )
    if "internal_notifications" in payload:
        failures.append("reply payload must not include notifications")
    if "telegram" in json.dumps(payload).lower():
        failures.append("reply payload must not mention telegram")
    if payload["is_first_agent_reply"] is not True:
        failures.append("is_first_agent_reply not propagated")
    if payload["approved_action"] != "ask_discovery_questions":
        failures.append("approved_action not propagated")

    unknown = write_reply(
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
    if unknown.get("decision_reason") != tenant_config.REASON_TENANT_NOT_RESOLVED:
        failures.append("unknown tenant should set tenant_not_resolved")

    if failures:
        for failure in failures:
            print(f"llm_reply_writer self-test FAIL: {failure}")
        return 1
    print("llm_reply_writer self-test: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(self_test())
