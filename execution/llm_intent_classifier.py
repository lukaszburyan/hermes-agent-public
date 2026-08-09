#!/usr/bin/env python3
"""LLM intent classifier for incoming mail (spec sections 7-10).

This module is **shadow by default**: it never replaces the deterministic
classifier unless a deployment flag explicitly enables it. It is designed so
that all tests run on fixtures — no paid token-billed API is ever called from
here (Guardrail 4). The real LLM is reached only through a pluggable client
that routes via the Hermes gateway / Codex CLI on the GPT Plus plan.

Files (spec section 7):
  - execution/llm_intent_classifier.py        (this module)
  - prompts/incoming_mail_classifier_system.md (system prompt)
  - schemas/incoming_mail_classification.schema.json (output schema)

Flow:
  1. Build the user payload (subject, body, sender, recipient, safety check,
     thread history, saved deal data, allowed intents, tenant fields, product
     knowledge fragments) — never secrets / Telegram / alarm settings / other
     tenants' data.
  2. Call the LLM client at temperature 0.
  3. Parse JSON. On failure, retry once. On second failure -> awaiting_human /
     llm_classification_failed (never mark done).
  4. Validate against the JSON schema.
  5. The script (``decide_action``) makes the final decision per spec section
     10 — the LLM only recommends.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Protocol

EXECUTION_DIR = Path(__file__).resolve().parent
ROOT_DIR = EXECUTION_DIR.parent
if str(EXECUTION_DIR) not in sys.path:
    sys.path.insert(0, str(EXECUTION_DIR))

import jsonschema  # noqa: E402
from hermes_rfq_core import REASON_LLM_CLASSIFICATION_FAILED  # noqa: E402
import tenant_config  # noqa: E402
from tenant_config import STATE_AWAITING_HUMAN  # noqa: E402

PROMPTS_DIR = ROOT_DIR / "prompts"
SCHEMAS_DIR = ROOT_DIR / "schemas"
SYSTEM_PROMPT_PATH = PROMPTS_DIR / "incoming_mail_classifier_system.md"
SCHEMA_PATH = SCHEMAS_DIR / "incoming_mail_classification.schema.json"

# Spec section 8: intents the classifier must handle.
ALLOWED_INTENTS = (
    "product_fit_inquiry",
    "product_information_request",
    "demo_request",
    "pricing_request",
    "implementation_question",
    "support_request",
    "existing_customer_reply",
    "partnership_or_vendor",
    "job_or_recruitment",
    "unclear_business_inquiry",
    "automated_message",
    "spam_or_security_risk",
    "unrelated",
)

# Spec section 10: script-side final actions.
SCRIPT_ACTIONS = (
    "ask_discovery_questions",
    "reply_directly",
    "prepare_offer",
    "draft_for_human",
    "block_security",
    "ignore_automated",
    "awaiting_human",
)

# Kill switch (spec section 24): the LLM classifier is off by default. It is
# gated by HERMES_LLM_INTENT_ENABLED (spec) and runs in shadow mode
# (HERMES_LLM_SHADOW_MODE, default on) alongside the deterministic
# classifier until explicitly promoted.
LLM_CLASSIFIER_ENABLED_FLAG = "HERMES_LLM_INTENT_ENABLED"

# Spec section 23: prompt version recorded with every classification decision
# so the prompt file loaded in production is unambiguous and auditable.
PROMPT_VERSION = "incoming-classifier-v1"
PROMPT_FILE = "prompts/incoming_mail_classifier_system.md"
RISK_LEVEL = {"low": 0, "medium": 1, "high": 2}


class LLMClient(Protocol):
    def complete(self, system_prompt: str, user_prompt: str, *, temperature: float = 0.0) -> str: ...


class FixtureLLMClient:
    """Deterministic client used by tests and shadow mode when no gateway is
    configured. Returns a canned valid classification so the pipeline can be
    exercised without any paid API call (Guardrail 4).

    The fixture response is supplied via ``response`` or, for tests that need
    retry/failure behaviour, via ``responses`` (a list consumed in order).
    """

    def __init__(self, response: str | None = None, responses: list[str] | None = None) -> None:
        self._responses = list(responses) if responses is not None else None
        self._single = response
        self.calls: list[dict[str, Any]] = []

    def complete(self, system_prompt: str, user_prompt: str, *, temperature: float = 0.0) -> str:
        self.calls.append({"system_prompt": system_prompt, "user_prompt": user_prompt, "temperature": temperature})
        if self._responses is not None:
            if not self._responses:
                return ""
            return self._responses.pop(0)
        return self._single or ""


class GatewayLLMClient:
    """Route structured completion through Hermes' GPT Plus runtime.

    On the Hermes VPS the default transport is the host-owned auxiliary bridge,
    which reuses the active ``openai-codex`` provider and ChatGPT authentication.
    A local Codex CLI is only an automatic fallback when that bridge is absent.
    No token-billed OpenAI API transport exists here.
    """

    def __init__(
        self,
        *,
        command: list[str] | None = None,
        env: dict[str, str] | None = None,
        bridge_path: Path | None = None,
        python_path: Path | None = None,
    ) -> None:
        self.env = env
        self.bridge_path = bridge_path or (EXECUTION_DIR / "hermes_llm_bridge.py")
        self.python_path = python_path or Path(
            os.environ.get("HERMES_LLM_PYTHON", "/opt/hermes/.venv/bin/python")
        ).expanduser()
        configured = os.environ.get("HERMES_LLM_GATEWAY", "").strip()
        transport = os.environ.get("HERMES_LLM_TRANSPORT", "auto").strip().lower() or "auto"

        if command is not None:
            self.transport = "command"
            self.command = list(command)
        elif configured and configured.lower() not in {"unset", "none", "off"}:
            self.transport = "command"
            self.command = configured.split()
        elif transport in {"auto", "hermes"} and self.bridge_path.is_file() and self.python_path.is_file():
            self.transport = "hermes"
            self.command = []
        elif transport == "hermes":
            raise RuntimeError("Hermes auxiliary LLM runtime unavailable")
        elif transport in {"auto", "codex"}:
            model = os.environ.get("HERMES_LLM_MODEL", "gpt-5.6-luna").strip() or "gpt-5.6-luna"
            self.transport = "command"
            self.command = [
                "codex", "exec", "--skip-git-repo-check", "-s", "read-only",
                "--model", model,
            ]
        else:
            raise RuntimeError(f"unsupported HERMES_LLM_TRANSPORT: {transport}")

    def complete_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        response_schema: dict[str, Any],
        schema_name: str,
        mode: str,
        temperature: float = 0.0,
    ) -> str:
        if self.transport != "hermes":
            return self.complete(system_prompt, user_prompt, temperature=temperature)
        try:
            input_payload: Any = json.loads(user_prompt)
        except json.JSONDecodeError:
            input_payload = {"message": user_prompt}
        request = {
            "mode": mode,
            "schema_name": schema_name,
            "system_prompt": system_prompt,
            "input": input_payload,
            "temperature": temperature,
            "response_schema": response_schema,
        }
        timeout = max(5, int(os.environ.get("HERMES_LLM_TIMEOUT_SECONDS", "60")))
        bridge_env = dict(os.environ if self.env is None else self.env)
        bridge_env.setdefault("HERMES_AGENT_ROOT", "/opt/hermes")
        completed = subprocess.run(
            [str(self.python_path), str(self.bridge_path)],
            input=json.dumps(request, ensure_ascii=False),
            capture_output=True,
            text=True,
            env=bridge_env,
            timeout=timeout + 10,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or "").strip().splitlines()[-1:] or ["unknown"]
            raise RuntimeError(f"Hermes LLM bridge failed: {detail[0][:120]}")
        envelope = json.loads(completed.stdout or "{}")
        result = envelope.get("result") if isinstance(envelope, dict) else None
        if not isinstance(result, str) or not result.strip():
            raise ValueError("Hermes LLM bridge returned empty result")
        return result

    def complete(self, system_prompt: str, user_prompt: str, *, temperature: float = 0.0) -> str:
        payload = json.dumps(
            {"system_prompt": system_prompt, "user_prompt": user_prompt, "temperature": temperature},
            ensure_ascii=False,
        )
        if self.transport == "hermes":
            return self.complete_structured(
                system_prompt,
                user_prompt,
                response_schema={"type": "object", "additionalProperties": True},
                schema_name="incoming_mail_result",
                mode="CLASSIFY",
                temperature=temperature,
            )
        result = subprocess.run(
            [*self.command, payload],
            capture_output=True,
            text=True,
            env=self.env,
            check=False,
        )
        return result.stdout or ""


def is_enabled() -> bool:
    """True only when the deployment flag explicitly enables the LLM classifier."""
    return os.environ.get(LLM_CLASSIFIER_ENABLED_FLAG, "0").strip() == "1"


def load_system_prompt() -> str:
    return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8") if SYSTEM_PROMPT_PATH.exists() else ""


def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8")) if SCHEMA_PATH.exists() else {}


def _validate_schema_path() -> Path:
    if not SCHEMA_PATH.exists():
        raise FileNotFoundError(f"classification schema missing: {SCHEMA_PATH}")
    return SCHEMA_PATH


def validate_classification(payload: dict[str, Any]) -> None:
    """Raise jsonschema.ValidationError if the payload does not match the schema."""
    schema = load_schema()
    if schema:
        jsonschema.validate(payload, schema)


def script_risk_level(safety_check: dict[str, Any] | None) -> str:
    """Translate deterministic safety evidence into the model risk vocabulary."""
    safety = safety_check or {}
    explicit = str(safety.get("script_risk") or safety.get("risk") or "").strip().lower()
    aliases = {"safe": "low", "review": "medium", "blocked": "high"}
    if explicit in RISK_LEVEL:
        return explicit
    if explicit in aliases:
        return aliases[explicit]
    if any(safety.get(key) is True for key in ("hard_block", "blocked", "prompt_injection_detected")):
        return "high"
    if safety.get("attachments_safe") is False:
        return "high"
    if any(safety.get(key) is True for key in ("review_required", "ambiguous", "commercial_exception")):
        return "medium"
    return "low"


def apply_script_risk_floor(
    classification: dict[str, Any],
    safety_check: dict[str, Any] | None,
) -> dict[str, Any]:
    """Allow the model to raise risk, but never lower deterministic risk."""
    result = dict(classification or {})
    model_risk = str(result.get("risk") or "low").strip().lower()
    if model_risk not in RISK_LEVEL:
        model_risk = "medium"
    floor = script_risk_level(safety_check)
    final_risk = max((model_risk, floor), key=lambda item: RISK_LEVEL[item])
    result["risk"] = final_risk
    reasons = list(result.get("decision_reasons") or [])
    if RISK_LEVEL[final_risk] > RISK_LEVEL[model_risk]:
        reasons.append(f"script_risk_floor:{floor}")
    result["decision_reasons"] = list(dict.fromkeys(str(item) for item in reasons))
    return result


def _strip_json_fence(text: str) -> str:
    """Extract a JSON object from a possibly fenced / noisy LLM response."""
    match = re.search(r"\{.*\}", text, re.S)
    return match.group(0) if match else text.strip()


def _parse_json(text: str) -> dict[str, Any]:
    return json.loads(_strip_json_fence(text))


def classification_failed_outcome(*, run_id: str = "") -> dict[str, Any]:
    """Return the awaiting_human outcome used after two failed LLM parses."""
    return {
        "state": STATE_AWAITING_HUMAN,
        "decision_reason": REASON_LLM_CLASSIFICATION_FAILED,
        "action": "awaiting_human",
        "run_id": run_id,
        "classification": None,
        "prompt_version": PROMPT_VERSION,
        "prompt_file": PROMPT_FILE,
    }


def build_user_payload(
    *,
    subject: str,
    body: str,
    sender: str,
    recipient: str,
    safety_check: dict[str, Any] | None,
    thread_history: str,
    saved_deal_data: dict[str, Any] | None,
    tenant_id: str,
) -> dict[str, Any]:
    """Build the user-facing payload sent to the LLM (spec section 7).

    Includes only the current tenant's discovery/offer fields and product
    knowledge fragments. Never secrets, Telegram, alarm settings, or other
    tenants' data.
    """
    offer_fields = tenant_config.offer_fields(tenant_id)
    discovery = tenant_config.load_discovery(tenant_id).get("questions", {})
    products = tenant_config.load_products(tenant_id)
    # Only the public product facts the LLM needs; drop nothing sensitive.
    product_facts = {
        "pricing_id": products.get("pricing_id"),
        "currency": products.get("currency"),
        "base_offer": products.get("base_offer"),
    }
    return {
        "subject": subject,
        "body": body,
        "sender": sender,
        "recipient": recipient,
        "safety_check": safety_check or {},
        "thread_history": thread_history,
        "saved_deal_data": saved_deal_data or {},
        "allowed_intents": list(ALLOWED_INTENTS),
        "tenant_fields": {
            "discovery": list(discovery.keys()),
            "offer": list(offer_fields.keys()),
        },
        "product_knowledge": product_facts,
    }


def classify(
    *,
    subject: str,
    body: str,
    sender: str,
    recipient: str,
    tenant_id: str,
    safety_check: dict[str, Any] | None = None,
    thread_history: str = "",
    saved_deal_data: dict[str, Any] | None = None,
    llm_client: LLMClient | None = None,
    run_id: str = "",
) -> dict[str, Any]:
    """Classify one incoming message.

    Returns a dict with ``classification`` (the validated LLM output) and
    ``action`` (the script's final decision, spec section 10). On two failed
    JSON parses returns ``classification_failed_outcome()``.

    ``llm_client`` defaults to ``FixtureLLMClient`` so this function is safe to
    call in tests and shadow mode without any paid API (Guardrail 4).
    """
    if not tenant_config.tenant_exists(tenant_id):
        return {
            **classification_failed_outcome(run_id=run_id),
            "decision_reason": tenant_config.REASON_TENANT_NOT_RESOLVED,
        }
    _validate_schema_path()
    client = llm_client or FixtureLLMClient()
    system_prompt = load_system_prompt()
    user_payload = build_user_payload(
        subject=subject,
        body=body,
        sender=sender,
        recipient=recipient,
        safety_check=safety_check,
        thread_history=thread_history,
        saved_deal_data=saved_deal_data,
        tenant_id=tenant_id,
    )
    user_prompt = json.dumps(user_payload, ensure_ascii=False, indent=2)

    last_error: str = ""
    for attempt in (1, 2):
        try:
            if hasattr(client, "complete_structured"):
                raw = client.complete_structured(
                    system_prompt,
                    user_prompt,
                    response_schema=load_schema(),
                    schema_name="incoming_mail_classification",
                    mode="CLASSIFY",
                    temperature=0.0,
                )
            else:
                raw = client.complete(system_prompt, user_prompt, temperature=0.0)
            parsed = _parse_json(raw)
            validate_classification(parsed)
            parsed = apply_script_risk_floor(parsed, safety_check)
            return {
                "classification": parsed,
                "action": decide_action(parsed),
                "run_id": run_id,
                "attempts": attempt,
                "prompt_version": PROMPT_VERSION,
                "prompt_file": PROMPT_FILE,
            }
        except (json.JSONDecodeError, jsonschema.ValidationError, ValueError) as exc:
            last_error = f"attempt {attempt}: {exc.__class__.__name__}: {exc}"
            if attempt == 1:
                continue
    outcome = classification_failed_outcome(run_id=run_id)
    outcome["error"] = last_error
    return outcome


def decide_action(classification: dict[str, Any]) -> str:
    """Apply the script-side policy (spec section 10) to a classification.

    The LLM only recommends; this function makes the final decision and can
    override the recommendation.
    """
    risk = str(classification.get("risk") or "").lower()
    intent = str(classification.get("intent") or "")
    missing = classification.get("missing_information") or []
    recommended = str(classification.get("recommended_action") or "")

    if intent == "automated_message":
        return "ignore_automated"
    if intent in {"spam_or_security_risk"} or risk == "high":
        return "block_security"
    if risk == "medium":
        return "draft_for_human"
    # low risk
    if intent in {"unrelated", "job_or_recruitment", "partnership_or_vendor"}:
        return "draft_for_human"
    if intent == "existing_customer_reply":
        return "reply_directly"
    # business intent with low risk
    if missing:
        return "ask_discovery_questions"
    if recommended == "prepare_offer":
        return "prepare_offer"
    return "reply_directly"


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
    else:
        try:
            jsonschema.validate({"intent": "bogus"}, schema)
            failures.append("schema should reject bogus intent")
        except jsonschema.ValidationError:
            pass

    good = {
        "intent": "product_fit_inquiry",
        "confidence": "high",
        "risk": "low",
        "sender_identity": "free_email_unverified",
        "provided_information": {"business_type": "handel"},
        "missing_information": ["company_name_or_website", "current_process"],
        "recommended_action": "ask_discovery_questions",
        "decision_reasons": ["business_interest_detected"],
        "language": "pl",
        "salutation_name": "Krzysztof",
        "salutation_form": "Panie Krzysztofie",
    }
    validate_classification(good)
    if decide_action(good) != "ask_discovery_questions":
        failures.append("low risk + missing data should ask_discovery_questions")

    complete = dict(good)
    complete["missing_information"] = []
    complete["recommended_action"] = "prepare_offer"
    if decide_action(complete) != "prepare_offer":
        failures.append("low risk + complete data + prepare_offer recommendation should prepare_offer")

    automated = dict(good)
    automated["intent"] = "automated_message"
    if decide_action(automated) != "ignore_automated":
        failures.append("automated_message should ignore_automated")

    high_risk = dict(good)
    high_risk["risk"] = "high"
    if decide_action(high_risk) != "block_security":
        failures.append("high risk should block_security")

    medium_risk = dict(good)
    medium_risk["risk"] = "medium"
    if decide_action(medium_risk) != "draft_for_human":
        failures.append("medium risk should draft_for_human")

    # Fixture classification with a canned valid response.
    fixture = FixtureLLMClient(response=json.dumps(good))
    result = classify(
        subject="Czy ten system pasuje do naszej firmy?",
        body="Prowadzę firmę handlową i chciałbym dowiedzieć się więcej.",
        sender="krzysztof@example.com",
        recipient="rfq-mailbox@example.invalid",
        tenant_id="orchesta",
        llm_client=fixture,
        run_id="selftest-1",
    )
    if result.get("action") != "ask_discovery_questions":
        failures.append(f"fixture classify action wrong: {result.get('action')}")
    if not result.get("classification"):
        failures.append("fixture classify returned no classification")

    # Two bad JSON parses -> awaiting_human / llm_classification_failed.
    bad = FixtureLLMClient(responses=["not json at all", "{still not json"])
    failed = classify(
        subject="x",
        body="y",
        sender="a@b.c",
        recipient="rfq-mailbox@example.invalid",
        tenant_id="orchesta",
        llm_client=bad,
        run_id="selftest-2",
    )
    if failed.get("state") != STATE_AWAITING_HUMAN:
        failures.append("two bad parses should reach awaiting_human")
    if failed.get("decision_reason") != REASON_LLM_CLASSIFICATION_FAILED:
        failures.append("two bad parses should set llm_classification_failed")

    # Unknown tenant -> awaiting_human / tenant_not_resolved.
    unknown = classify(
        subject="x",
        body="y",
        sender="a@b.c",
        recipient="unknown@nowhere.test",
        tenant_id="nonexistent_tenant",
        llm_client=FixtureLLMClient(),
    )
    if unknown.get("decision_reason") != tenant_config.REASON_TENANT_NOT_RESOLVED:
        failures.append("unknown tenant should set tenant_not_resolved")

    if failures:
        for failure in failures:
            print(f"llm_intent_classifier self-test FAIL: {failure}")
        return 1
    print("llm_intent_classifier self-test: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(self_test())
