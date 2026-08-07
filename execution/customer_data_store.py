#!/usr/bin/env python3
"""Save customer-provided data from a reply (spec section 11).

The LLM reads data out of every customer message and returns it as
``provided_information`` in the classification. This module:

  - extracts ``provided_information`` / ``missing_information`` from a
    classification,
  - hands the data to the registry's controlled-save method (no silent
    overwrite; provenance row per field with source, message_id, timestamp),
  - filters the still-missing fields so the reply writer never re-asks for
    data that is already saved.

The registry owns the storage and conflict policy; this module is the
thin orchestration layer used by the poller.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

EXECUTION_DIR = Path(__file__).resolve().parent
if str(EXECUTION_DIR) not in sys.path:
    sys.path.insert(0, str(EXECUTION_DIR))


def provided_information_from_classification(classification: dict[str, Any]) -> dict[str, Any]:
    """Return the ``provided_information`` dict from a classification."""
    provided = classification.get("provided_information") or {}
    return dict(provided) if isinstance(provided, dict) else {}


def missing_information_from_classification(classification: dict[str, Any]) -> list[str]:
    """Return the ``missing_information`` list from a classification."""
    missing = classification.get("missing_information") or []
    return [str(field) for field in missing] if isinstance(missing, list) else []


def still_missing_fields(
    deal_facts: dict[str, Any] | None,
    missing_information: list[str],
) -> list[str]:
    """Filter ``missing_information`` to fields not already saved on the deal.

    Spec section 11: the LLM must not re-ask for data that was saved earlier.
    """
    facts = deal_facts or {}
    return [
        field
        for field in missing_information
        if facts.get(field) is None or facts.get(field) == "" or facts.get(field) == {}
    ]


def question_fields_from_text(text: str, *, tenant_id: str = "orchesta") -> list[str]:
    """Resolve fields asked in the last Hermes reply without using an LLM."""
    import re
    import unicodedata
    import tenant_config

    normalized = unicodedata.normalize("NFKD", str(text or "").replace("ł", "l").replace("Ł", "L")).encode("ascii", "ignore").decode("ascii").lower()
    question_context = "\n".join(
        clause.strip()
        for clause in re.split(r"(?<=[.!?])\s+|[\r\n]+", normalized)
        if clause.strip()
        and (
            "?" in clause
            or re.match(r"^(?:czy|ile|jak|jaki|jaka|jakie|ktora|ktory|which|how|what|do|does|is|are)\b", clause.strip())
        )
    )
    fields: list[str] = []
    questions = tenant_config.load_discovery(tenant_id).get("questions", {}) or {}
    for field, variants in questions.items():
        for raw in (variants or {}).values():
            candidate = unicodedata.normalize("NFKD", str(raw or "")).encode("ascii", "ignore").decode("ascii").lower()
            candidate = candidate.rstrip("?. ")
            if candidate and candidate in normalized:
                fields.append(str(field))
                break
    fallback_patterns = {
        "mailbox_count": r"(?:ile|how many).{0,80}(?:kont|skrzyn|inbox|mailbox)",
        "crm": r"\bcrm\b",
        "company_name_or_website": r"(?:jaka|ktora|which).{0,80}(?:firma|company)|(?:stron|website|www)",
        "current_process": r"(?:jak|how).{0,80}(?:obecn|dzis|current).{0,80}(?:proces|obslug|handling)",
        "inquiry_channels": r"(?:skad|jakich|which|through).{0,80}(?:kanal|channel|zapyt|rfq)",
        "monthly_volume": r"(?:ile|how many).{0,80}(?:miesie|month).{0,80}(?:zapyt|rfq)|(?:zapyt|rfq).{0,80}(?:miesie|month)",
    }
    for field, pattern in fallback_patterns.items():
        if field not in fields and re.search(pattern, question_context):
            fields.append(field)
    return fields


def contextual_short_reply_facts(text: str, expected_fields: list[str] | None) -> dict[str, Any]:
    """Interpret a terse answer only when its previous-question context is safe."""
    import re
    import unicodedata

    normalized = unicodedata.normalize("NFKD", str(text or "").replace("ł", "l").replace("Ł", "L")).encode("ascii", "ignore").decode("ascii").lower()
    normalized = re.sub(r"[\s.,!?;:]+", " ", normalized).strip()
    fields = list(dict.fromkeys(str(field) for field in (expected_fields or []) if str(field)))
    if not normalized or not fields:
        return {}

    affirmative = {"tak", "yes", "ok", "okay", "zgadza sie", "correct", "correcto"}
    negative = {"nie", "no"}
    boolean_fields = [field for field in fields if field == "crm"]
    if normalized in affirmative and len(boolean_fields) == 1:
        return {boolean_fields[0]: True}
    if normalized in negative and len(boolean_fields) == 1:
        return {boolean_fields[0]: False}

    number = re.fullmatch(r"(\d{1,5})", normalized)
    numeric_fields = [field for field in fields if field in {"mailbox_count", "monthly_volume"}]
    if number and len(numeric_fields) == 1:
        value = int(number.group(1))
        if numeric_fields[0] == "mailbox_count" and not 1 <= value <= 99:
            return {}
        return {numeric_fields[0]: value}
    return {}


def extract_facts_from_customer_text(
    text: str,
    *,
    deal: dict[str, Any] | None = None,
    known_offer_facts_fn: Any | None = None,
    expected_fields: list[str] | None = None,
) -> dict[str, Any]:
    """Heuristic extraction of offer.yaml fields from a customer reply.

    Used when the follow-up path needs ``saved_data`` / ``missing_data`` without
    waiting on a separate classifier round-trip. Conservative: only fills fields
    with clear signals.
    """
    import re
    import unicodedata

    deal = deal or {}
    raw = str(text or "")
    # Drop quoted history so we only read the newest customer part.
    newest = re.split(
        r"(?ims)^\s*(?:on .{0,180} wrote:|w dniu .{0,180} napisa[łl][a]?:|-----original message-----)",
        raw,
        maxsplit=1,
    )[0]
    normalized = unicodedata.normalize("NFKD", newest).encode("ascii", "ignore").decode("ascii").lower()
    facts: dict[str, Any] = {}

    company = str(deal.get("company") or "").strip()
    if company:
        facts["company_name_or_website"] = company

    if known_offer_facts_fn is not None:
        known = known_offer_facts_fn(newest) or {}
        mailbox = known.get("mailbox_count") or {}
        if mailbox.get("state") == "known" and mailbox.get("value") is not None:
            facts["mailbox_count"] = mailbox.get("value")
        inquiry = known.get("inquiry_source") or {}
        if inquiry.get("state") == "known" and inquiry.get("value"):
            facts["inquiry_channels"] = inquiry.get("value")
        crm = known.get("crm") or {}
        if crm.get("state") == "known" and crm.get("value") is not None:
            facts["crm"] = crm.get("value")

    volume = re.search(
        r"\b(\d{1,5})\s*(?:zapyta\w*|wiadom\w*|mail(?:i|e|y)?|rfq|dokument\w*)\b",
        normalized,
    )
    if volume:
        facts["monthly_volume"] = volume.group(1)

    people = re.search(r"\b(\d{1,3})\s*(?:osob\w*|handlow\w*|pracownik\w*)\b", normalized)
    if people:
        facts["users_count"] = people.group(1)

    if any(token in normalized for token in ("excel", "arkusz")):
        facts["current_process"] = "skrzynki mailowe + Excel"
    elif any(token in normalized for token in ("recznie", "reczna")):
        facts["current_process"] = "proces ręczny"
    elif "skrzyn" in normalized:
        facts["current_process"] = "skrzynki mailowe"
    # A mailbox-count answer ("dwa konta pocztowe") describes implementation
    # scope, not the customer's inquiry channel. Infer the channel only from a
    # clause that actually says where inquiries arrive.
    if re.search(
        r"\b(?:zapyt\w*|rfq|wiadom\w*)\b.{0,80}\b(?:mail\w*|skrzyn\w*|poczt\w*)\b"
        r"|\b(?:mail\w*|skrzyn\w*|poczt\w*)\b.{0,80}\b(?:wpada\w*|wplyw\w*|trafia\w*|przychodz\w*)\b",
        normalized,
    ):
        facts.setdefault("inquiry_channels", "mail")

    for field, value in contextual_short_reply_facts(newest, expected_fields).items():
        facts.setdefault(field, value)

    return facts


def save_customer_reply(
    *,
    registry: Any,
    deal_id: str,
    classification: dict[str, Any] | None,
    source_message_id: str,
    source_type: str = "mail",
    occurred_at: str = "",
) -> dict[str, Any]:
    """Persist customer-provided data from a classification to the deal.

    Delegates the controlled merge + provenance write to
    ``registry.record_provided_information``. Returns the save summary plus the
    still-missing fields (so the caller can hand them to the reply writer).
    """
    classification = classification or {}
    provided = provided_information_from_classification(classification)
    missing = missing_information_from_classification(classification)
    if not deal_id:
        raise ValueError("deal_id is required")
    if not provided:
        return {
            "added": [],
            "updated": [],
            "conflict_kept": [],
            "unchanged": [],
            "facts": registry.get_deal(deal_id).get("facts") if registry.get_deal(deal_id) else {},
            "still_missing": missing,
        }
    summary = registry.record_provided_information(
        deal_id,
        provided,
        source_message_id=source_message_id,
        source_type=source_type,
        occurred_at=occurred_at,
    )
    summary["still_missing"] = still_missing_fields(summary.get("facts"), missing)
    return summary


def self_test() -> int:
    # Pure-function checks (no DB needed).
    provided = provided_information_from_classification(
        {"provided_information": {"company_name": "ABC", "monthly_volume": 300}}
    )
    if provided != {"company_name": "ABC", "monthly_volume": 300}:
        print("customer_data_store self-test FAIL: provided extraction")
        return 1
    missing = missing_information_from_classification(
        {"missing_information": ["inquiry_channels", "current_process"]}
    )
    if missing != ["inquiry_channels", "current_process"]:
        print("customer_data_store self-test FAIL: missing extraction")
        return 1
    # already-saved fields are filtered out of "still missing"
    still = still_missing_fields({"inquiry_channels": "mail"}, ["inquiry_channels", "current_process"])
    if still != ["current_process"]:
        print("customer_data_store self-test FAIL: still_missing filter")
        return 1
    print("customer_data_store self-test: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(self_test())
