#!/usr/bin/env python3
"""Pre-send control of the ready reply text (spec section 17).

The script — not the LLM — has the final word on whether a generated reply is
safe to send. This module runs the deterministic checks from spec section 17
and returns a verdict. On any failure the caller routes the deal to
``awaiting_human`` / ``reply_validation_failed`` and never auto-sends.

Checks (spec section 17):
  * length (target 40-100 words, hard max 120)
  * at most 2 questions
  * no emojis
  * no "--"
  * no en-dash (U+2013) / em-dash (U+2014)
  * greeting matches the conversation stage (first reply vs follow-up)
  * no second "dziękuję za wiadomość" once acknowledgement_already_sent
  * no questions about already-saved data
  * no Telegram / internal-alarm references
  * no other-tenant data
  * matches the approved action
"""
from __future__ import annotations

import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

EXECUTION_DIR = Path(__file__).resolve().parent
ROOT_DIR = EXECUTION_DIR.parent
if str(EXECUTION_DIR) not in sys.path:
    sys.path.insert(0, str(EXECUTION_DIR))

import tenant_config  # noqa: E402

# Spec section 15: target 40-100 words, hard max 120.
MIN_TARGET_WORDS = 40
MAX_TARGET_WORDS = 100
HARD_MAX_WORDS = 120
MAX_QUESTIONS = 2

# Telegram / internal-alarm terms must never appear in customer-facing replies.
FORBIDDEN_TERMS = (
    "telegram",
    "deal_id",
    "routing",
    "classifier",
    "prompt",
    "llm",
    "poller",
    "manual_review",
    "human_takeover",
    "conversation_handoff",
    "discovery_dead_end",
    "ready_for_final_offer",
    "skip_pre_offer_send",
    "commercial_exception",
    "wewnętrzne powiadomienie",
    "wewnetrzne powiadomienie",
    "automatyzacja rozmowy",
    "automatyczna rozmowa",
    "hermes",
)

# A reasonable emoji range + a few symbol blocks. Keeps the check cheap and
# deterministic without pulling a unicode table dependency.
_EMOJI_RANGES = (
    (0x1F300, 0x1FAFF),   # symbols & pictographs, emoticons, supplemental symbols
    (0x2600, 0x27BF),     # misc symbols & dingbats
    (0x2190, 0x21FF),     # arrows (used as decorative dashes by some models)
)

_DISCOVERY_FIELD_SIGNALS = {
    "company_name_or_website": ("firma", "strona", "www"),
    "current_process": ("proces", "obslug", "obsług", "dzis", "dziś", "obecnie"),
    "inquiry_channels": ("kanal", "kanał", "mail", "formularz"),
    "monthly_volume": ("wolumen", "miesie", "miesię", "ile zapyt"),
    "mailbox_count": ("konto poczt", "kont poczt", "skrzyn"),
    "crm": ("crm",),
}


def _word_count(text: str) -> int:
    return len(re.findall(r"\b[\w''-]+\b", text or ""))


def _contains_emoji(text: str) -> bool:
    for char in text or "":
        code = ord(char)
        for lo, hi in _EMOJI_RANGES:
            if lo <= code <= hi:
                return True
    return False


def _question_count(text: str) -> int:
    return (text or "").count("?")


def _greeting_present(text: str) -> bool:
    return bool(re.match(r"\s*Dzień\s+dobry", text or "", re.IGNORECASE))


def _thanks_present(text: str) -> bool:
    return "dziękuję za wiadomość" in (text or "").lower()


def _ascii_lower(text: Any) -> str:
    normalized = unicodedata.normalize("NFKD", str(text or "").replace("ł", "l").replace("Ł", "L"))
    return "".join(char for char in normalized if not unicodedata.combining(char)).lower()


def _repeated_saved_fields(body: str, saved: dict[str, Any]) -> list[str]:
    """Conservatively detect declarative restatement of stored customer data."""
    non_questions = "\n".join(
        part.strip()
        for part in re.split(r"(?<=[.!?])\s+|[\r\n]+", body or "")
        if part.strip() and "?" not in part
    )
    text = _ascii_lower(non_questions)
    if not text:
        return []
    repeated: list[str] = []

    company = _ascii_lower(saved.get("company_name_or_website"))
    if len(company) >= 4 and company in text:
        repeated.append("company_name_or_website")

    current = _ascii_lower(saved.get("current_process"))
    current_markers = ("excel", "arkusz", "reczn", "manual", "skrzyn")
    if current and any(marker in current and marker in text for marker in current_markers):
        repeated.append("current_process")

    volume = str(saved.get("monthly_volume") or "").strip()
    if volume and re.search(rf"\b{re.escape(volume)}\b.{{0,40}}(?:zapyt|wiadom|rfq|miesie)", text):
        repeated.append("monthly_volume")

    mailbox = str(saved.get("mailbox_count") or "").strip()
    if mailbox and re.search(rf"\b{re.escape(mailbox)}\b.{{0,30}}(?:kont|skrzyn|mailbox|inbox)", text):
        repeated.append("mailbox_count")

    if saved.get("crm") is not None and re.search(r"\bcrm\b", text):
        repeated.append("crm")

    channels = _ascii_lower(saved.get("inquiry_channels") or saved.get("inquiry_source"))
    if channels:
        has_channel_statement = bool(
            re.search(r"(?:zapyt|rfq|wiadom).{0,50}(?:mail|formularz|kanal)", text)
            or re.search(r"(?:mail|formularz|kanal).{0,50}(?:zapyt|rfq|wiadom)", text)
        )
        if has_channel_statement:
            repeated.append("inquiry_channels")
    return list(dict.fromkeys(repeated))


def _other_tenant_names(current_tenant: str) -> list[str]:
    names = []
    for name in ("orchesta", "firma_abc"):
        if name != current_tenant:
            names.append(name)
    return names


def validate_reply(
    *,
    body: str,
    approved_action: str,
    conversation_state: dict[str, Any] | None,
    saved_data: dict[str, Any] | None,
    tenant_id: str,
    missing_data: list[str] | None = None,
) -> dict[str, Any]:
    """Run the section-17 checks. Returns {"ok": bool, "errors": [code, ...]}.

    ``ok`` is True only when ``errors`` is empty. A non-empty ``errors`` list
    means the reply must NOT be auto-sent (route to awaiting_human /
    reply_validation_failed).
    """
    errors: list[str] = []
    body = body or ""
    state = conversation_state or {}
    saved = saved_data or {}
    missing = [str(item) for item in (missing_data or []) if str(item).strip()]
    is_first = bool(state.get("is_first_agent_reply", True))
    ack_sent = bool(state.get("acknowledgement_already_sent", False))

    words = _word_count(body)
    if words > HARD_MAX_WORDS:
        errors.append("too_long")
    # <40 is a soft target; surfaced as a warning, not a hard failure, so
    # that legitimate terse follow-ups (section 13 example) are not rejected.
    warning_short = words < MIN_TARGET_WORDS

    if _question_count(body) > MAX_QUESTIONS:
        errors.append("too_many_questions")
    if _contains_emoji(body):
        errors.append("emojis_present")
    if "--" in body:
        errors.append("double_hyphen_present")
    if "–" in body:
        errors.append("en_dash_present")
    if "—" in body:
        errors.append("em_dash_present")

    # Greeting must match the conversation stage.
    greeting = _greeting_present(body)
    if is_first and not greeting:
        errors.append("first_reply_missing_greeting")
    if (not is_first) and greeting:
        # A follow-up that re-greets is a re-greeting failure (spec section 13:
        # "nie powtarzaj pełnego powitania"). A new thread would have reset
        # is_first_agent_reply via conversation_continuity, so a non-first
        # reply is by definition a follow-up in the same thread.
        errors.append("followup_re_greets")
    # No second "dziękuję za wiadomość" once the acknowledgement was sent.
    if ack_sent and _thanks_present(body):
        errors.append("second_thank_you")
    if not is_first:
        errors.extend(f"repeats_saved_data:{field}" for field in _repeated_saved_fields(body, saved))

    # No questions about already-saved data: if the reply contains the
    # discovery question text for a field that is already saved, it is re-asking.
    discovery = tenant_config.load_discovery(tenant_id).get("questions", {}) or {}
    for field, question_text in discovery.items():
        if field not in saved:
            continue
        pl = str((question_text or {}).get("pl") or "")
        if pl and pl.rstrip("?.").strip() in body:
            errors.append(f"re_asks_saved_field:{field}")
            break

    # No Telegram / internal-alarm references; no other-tenant data.
    lower_body = body.lower()
    for term in FORBIDDEN_TERMS:
        if term in lower_body:
            errors.append(f"forbidden_term:{term}")
    for other in _other_tenant_names(tenant_id):
        if other in lower_body:
            errors.append(f"other_tenant_data:{other}")

    # Consistency with the approved action.
    if approved_action in {"draft_for_human", "block_security", "awaiting_human"}:
        # These actions must not auto-send; a generated body for them is not
        # directly sendable. Surface as a validation failure so the caller
        # routes to human review instead of sending.
        errors.append(f"action_not_sendable:{approved_action}")
    # A discovery reply must request at least one actually missing field, not
    # merely paraphrase the customer's message and stop. It may request at most
    # two missing fields even when the asks are phrased without question marks.
    if approved_action == "ask_discovery_questions" and missing:
        requested_missing = [
            field
            for field in missing
            if any(signal in lower_body for signal in _DISCOVERY_FIELD_SIGNALS.get(field, (field.lower(),)))
        ]
        dead_end = any(
            phrase in lower_body
            for phrase in (
                "wystarczające informacje",
                "wystarczajace informacje",
                "to wystarczy",
                "mam wystarczająco",
                "mam wystarczajaco",
                "to wystarczające",
                "to wystarczajace",
            )
        )
        if dead_end or not requested_missing:
            errors.append("discovery_dead_end")
        if len(requested_missing) > MAX_QUESTIONS:
            errors.append("too_many_data_requests")

    return {"ok": not errors, "errors": errors, "warnings": ["too_short"] if warning_short else [], "word_count": words}


def validation_failed_outcome(*, errors: list[str], run_id: str = "") -> dict[str, Any]:
    from hermes_rfq_core import REASON_REPLY_VALIDATION_FAILED  # noqa: E402
    from tenant_config import STATE_AWAITING_HUMAN  # noqa: E402

    return {
        "state": STATE_AWAITING_HUMAN,
        "decision_reason": REASON_REPLY_VALIDATION_FAILED,
        "action": "awaiting_human",
        "errors": list(errors),
        "run_id": run_id,
        "sent": False,
    }


def self_test() -> int:
    failures: list[str] = []
    base_state = {"is_first_agent_reply": True, "acknowledgement_already_sent": False}

    good_first = (
        "Dzień dobry Panie Krzysztofie,\n\n"
        "dziękuję za wiadomość. Żeby ocenić dopasowanie systemu do Państwa firmy, "
        "proszę podać nazwę firmy lub stronę internetową oraz opisać, jak dziś "
        "obsługują Państwo wiadomości od klientów."
    )
    res = validate_reply(
        body=good_first, approved_action="ask_discovery_questions",
        conversation_state=base_state, saved_data={}, tenant_id="orchesta",
    )
    if not res["ok"]:
        failures.append(f"good first reply should pass: {res['errors']}")

    # Too long
    long_body = "Dzień dobry Panie Krzysztofie,\n\n" + ("słowo " * 200)
    res = validate_reply(body=long_body, approved_action="ask_discovery_questions",
                          conversation_state=base_state, saved_data={}, tenant_id="orchesta")
    if "too_long" not in res["errors"]:
        failures.append("over 120 words should fail too_long")

    # Too many questions
    many_q = "Dzień dobry Panie,\n\na? b? c? d?"
    res = validate_reply(body=many_q, approved_action="ask_discovery_questions",
                         conversation_state=base_state, saved_data={}, tenant_id="orchesta")
    if "too_many_questions" not in res["errors"]:
        failures.append(">2 questions should fail")

    # Emoji
    res = validate_reply(body="Dzień dobry Panie 🎉", approved_action="reply_directly",
                         conversation_state=base_state, saved_data={}, tenant_id="orchesta")
    if "emojis_present" not in res["errors"]:
        failures.append("emoji should fail")

    # Dashes
    for dash_body, code in (("-- podpis", "double_hyphen_present"),
                             ("Dzień dobry – x", "en_dash_present"),
                             ("Dzień dobry — x", "em_dash_present")):
        res = validate_reply(body=dash_body, approved_action="reply_directly",
                             conversation_state=base_state, saved_data={}, tenant_id="orchesta")
        if code not in res["errors"]:
            failures.append(f"dash check missed {code}")

    # First reply missing greeting
    res = validate_reply(body="dziękuję za wiadomość. proszę o dane.",
                         approved_action="ask_discovery_questions",
                         conversation_state=base_state, saved_data={}, tenant_id="orchesta")
    if "first_reply_missing_greeting" not in res["errors"]:
        failures.append("first reply without greeting should fail")

    # Follow-up re-greets
    follow_state = {"is_first_agent_reply": False, "acknowledgement_already_sent": True}
    res = validate_reply(body="Dzień dobry Panie Krzysztofie,\n\nRozumiem, jak dziś wygląda obsługa?",
                         approved_action="ask_discovery_questions",
                         conversation_state=follow_state, saved_data={}, tenant_id="orchesta")
    if "followup_re_greets" not in res["errors"]:
        failures.append("follow-up that re-greets should fail")

    # Second thank-you
    res = validate_reply(body="dziękuję za wiadomość. Rozumiem.",
                         approved_action="reply_directly",
                         conversation_state=follow_state, saved_data={}, tenant_id="orchesta")
    if "second_thank_you" not in res["errors"]:
        failures.append("second thank-you after ack should fail")

    # Re-asks saved data (company_name already saved; reply contains its PL question)
    saved = {"company_name_or_website": "ABC"}
    res = validate_reply(
        body="Rozumiem. Na jaką firmę mam przygotować ofertę? Jak dziś wygląda obsługa?",
        approved_action="ask_discovery_questions",
        conversation_state=follow_state, saved_data=saved, tenant_id="orchesta",
    )
    if not any(e.startswith("re_asks_saved_field") for e in res["errors"]):
        failures.append("re-asking a saved field should fail")

    # Telegram mention
    res = validate_reply(body="Rozumiem. Telegram to kanał powiadomień.",
                         approved_action="reply_directly",
                         conversation_state=follow_state, saved_data={}, tenant_id="orchesta")
    if not any(e.startswith("forbidden_term") for e in res["errors"]):
        failures.append("telegram mention should fail")

    # Other tenant data
    res = validate_reply(body="Rozumiem. Orchesta to my.",
                         approved_action="reply_directly",
                         conversation_state={"is_first_agent_reply": False, "acknowledgement_already_sent": True},
                         saved_data={}, tenant_id="firma_abc")
    if not any(e.startswith("other_tenant_data") for e in res["errors"]):
        failures.append("other tenant name should fail")

    # Action not sendable
    res = validate_reply(body="Rozumiem.", approved_action="block_security",
                         conversation_state=follow_state, saved_data={}, tenant_id="orchesta")
    if not any(e.startswith("action_not_sendable") for e in res["errors"]):
        failures.append("block_security action should not be sendable")

    # ask_discovery without a literal "?" is allowed (spec example phrases the
    # ask as "proszę podać ...").
    res = validate_reply(body="Dzień dobry Panie Krzysztofie,\n\nproszę podać nazwę firmy oraz opisać, jak dziś wygląda obsługa wiadomości od klientów.",
                         approved_action="ask_discovery_questions",
                         conversation_state=base_state, saved_data={}, tenant_id="orchesta")
    if not res["ok"]:
        failures.append(f"ask_discovery without '?' should pass: {res['errors']}")

    if failures:
        for failure in failures:
            print(f"reply_validation self-test FAIL: {failure}")
        return 1
    print("reply_validation self-test: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(self_test())
