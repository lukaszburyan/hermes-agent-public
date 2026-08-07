#!/usr/bin/env python3
"""Deterministic transport policy for Orchesta RFQ customer messages.

The language model may propose copy, but it cannot decide whether the copy is
sent.  This module owns the stable message types and may only raise risk: an
operational type can become ``final_offer`` or ``manual_review`` after content,
attachment and deal-stage checks; a final offer can never be downgraded.
"""

from __future__ import annotations

import html
import os
import re
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

ACKNOWLEDGEMENT = "acknowledgement"
CLARIFICATION_REQUEST = "clarification_request"
MISSING_DATA_REQUEST = "missing_data_request"
FOLLOW_UP = "follow_up"
READY_FOR_OFFER_NOTICE = "ready_for_offer_notice"
FINAL_OFFER = "final_offer"
MANUAL_REVIEW = "manual_review"

MESSAGE_TYPES = frozenset({
    ACKNOWLEDGEMENT,
    CLARIFICATION_REQUEST,
    MISSING_DATA_REQUEST,
    FOLLOW_UP,
    READY_FOR_OFFER_NOTICE,
    FINAL_OFFER,
})
AUTO_SEND_TYPES = frozenset({
    ACKNOWLEDGEMENT,
    CLARIFICATION_REQUEST,
    MISSING_DATA_REQUEST,
    FOLLOW_UP,
    READY_FOR_OFFER_NOTICE,
})
DRAFT_ONLY_TYPES = frozenset({FINAL_OFFER})

LEGACY_TYPE_ALIASES = {
    "first_response": ACKNOWLEDGEMENT,
    "discovery": CLARIFICATION_REQUEST,
    "missing_data": MISSING_DATA_REQUEST,
    "context_reply": CLARIFICATION_REQUEST,
    "customer_update": READY_FOR_OFFER_NOTICE,
}

FINAL_DEAL_STAGES = frozenset({
    "final_offer",
    "offer_ready",
    "ready_for_final_offer",
    "offer_draft_created",
})
READY_NOTICE_DEAL_STAGES = frozenset({"offer_ready", "ready_for_final_offer"})

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")
_NUMBER = r"(?:\d{1,3}(?:[ .,'\u00a0]\d{3})+|\d{4,}|\d+(?:[.,]\d{1,2})?)"
_CURRENCY = r"(?:zł|pln|eur|euro|usd|dolar(?:y|ów)?|gbp|funt(?:y|ów)?|€|\$|£)"

_FINANCIAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("currency_amount", re.compile(rf"(?:{_CURRENCY}\s*{_NUMBER}|{_NUMBER}\s*{_CURRENCY})", re.I)),
    ("price_amount", re.compile(rf"\b(?:cena|koszt|inwestycja|wycena)\w*\b.{{0,40}}?{_NUMBER}", re.I)),
    ("recurring_amount", re.compile(rf"{_NUMBER}\s*(?:miesięcznie|miesiecznie|/\s*miesiąc|/\s*miesiac|rocznie|/\s*rok)\b", re.I)),
    ("discount", re.compile(r"\b(?:rabat|upust|discount)\w*\b.{0,30}?\d+(?:[.,]\d+)?\s*%", re.I)),
    ("payment_terms", re.compile(r"\b(?:warunki|termin|harmonogram)\s+płatno\w*\b|\b(?:zaliczka|przedpłata|płatność\s+w\s+ratach|platnosc\s+w\s+ratach)\b", re.I)),
    ("offer_validity", re.compile(r"\b(?:oferta|wycena)\s+(?:jest\s+)?ważna\b|\btermin\s+ważności\s+(?:oferty|wyceny)\b", re.I)),
    ("amount_in_words", re.compile(
        r"\b(?:zero|jeden|jedna|jedno|dwa|dwie|trzy|cztery|pięć|piec|sześć|szesc|siedem|osiem|"
        r"dziewięć|dziewiec|dziesięć|dziesiec|jedenaście|jedenascie|dwanaście|dwanascie|"
        r"trzynaście|trzynascie|czternaście|czternascie|piętnaście|pietnascie|szesnaście|"
        r"szesnascie|siedemnaście|siedemnascie|osiemnaście|osiemnascie|dziewiętnaście|"
        r"dziewietnascie|dwadzieścia|dwadziescia|trzydzieści|trzydziesci|czterdzieści|"
        r"czterdziesci|pięćdziesiąt|piecdziesiat|sześćdziesiąt|szescdziesiat|siedemdziesiąt|"
        r"siedemdziesiat|osiemdziesiąt|osiemdziesiat|dziewięćdziesiąt|dziewiecdziesiat|"
        r"sto|dwieście|dwiescie|trzysta|czterysta|pięćset|piecset|sześćset|szescset|"
        r"siedemset|osiemset|dziewięćset|dziewiecset|tysiąc|tysiac|tysiące|tysiace|"
        r"milion|miliony)"
        r"(?:[ -]+[a-ząćęłńóśźż]+){0,6}\s+(?:złotych|zł|euro|dolarów|dolarow)\b",
        re.I,
    )),
)

_FINAL_OFFER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("explicit_final_offer", re.compile(r"\b(?:finalna|końcowa|koncowa)\s+(?:oferta|wycena)\b", re.I)),
    ("commercial_terms", re.compile(r"\bwarunki\s+handlowe\b", re.I)),
    ("acceptance_request", re.compile(
        r"\b(?:proszę|prosze)\s+o\s+akceptacj\w*\s+(?:finalnej\s+)?oferty\b|"
        r"\b(?:zaakceptuj|akceptuję|akceptuje)\s+(?:finalną\s+|finalna\s+)?ofertę\b|"
        r"\blink\w*\s+do\s+akceptacj\w*\s+(?:płatnej\s+)?realizacj\w*\b",
        re.I,
    )),
    ("final_scope_with_terms", re.compile(r"\bkońcowy\s+zakres\b.{0,160}\b(?:cena|płatność|platnosc|warunki)\b", re.I | re.S)),
)

_OFFER_ATTACHMENT_RE = re.compile(
    r"(?:^|[ _.-])(?:oferta|offer|quotation|quote|proposal|wycena|cennik)(?:[ _.-]|$)",
    re.I,
)
_OFFER_ATTACHMENT_SUFFIXES = frozenset({".pdf", ".doc", ".docx", ".odt", ".xlsx"})


@dataclass(frozen=True)
class PolicyDecision:
    requested_type: str
    effective_type: str
    transport_mode: str
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["reasons"] = list(self.reasons)
        return data


def normalize_message_type(value: str) -> str:
    raw = str(value or "").strip().lower()
    return LEGACY_TYPE_ALIASES.get(raw, raw if raw in MESSAGE_TYPES else MANUAL_REVIEW)


def operational_autosend_enabled(environ: dict[str, str] | None = None) -> bool:
    """Return the deployment gate; CLI flags cannot enable customer transport."""
    source = os.environ if environ is None else environ
    return str(source.get("HERMES_OPERATIONAL_AUTOSEND_ENABLED", "0")).strip() == "1"


def visible_text(value: str) -> str:
    text = html.unescape(_HTML_TAG_RE.sub(" ", str(value or "")))
    return _SPACE_RE.sub(" ", unicodedata.normalize("NFKC", text)).strip()


def financial_signals(value: str) -> list[str]:
    text = visible_text(value)
    return [name for name, pattern in _FINANCIAL_PATTERNS if pattern.search(text)]


def has_financial_terms(value: str) -> bool:
    return bool(financial_signals(value))


def _attachment_names(attachments: Iterable[Any]) -> list[str]:
    names: list[str] = []
    for item in attachments:
        if isinstance(item, dict):
            name = str(
                item.get("attachmentName")
                or item.get("fileName")
                or item.get("filename")
                or item.get("name")
                or ""
            )
        else:
            name = str(item or "")
        if name:
            names.append(Path(name).name)
    return names


def offer_attachment_signals(attachments: Iterable[Any]) -> list[str]:
    signals: list[str] = []
    materialized = list(attachments)
    for name in _attachment_names(materialized):
        if Path(name).suffix.lower() in _OFFER_ATTACHMENT_SUFFIXES and _OFFER_ATTACHMENT_RE.search(name):
            signals.append(f"offer_attachment:{name.lower()}")
    for item in materialized:
        if not isinstance(item, dict):
            continue
        extracted = visible_text("\n".join(
            str(item.get(key) or "")
            for key in ("safe_text", "extracted_text", "text", "summary")
        ))
        if not extracted:
            continue
        if financial_signals(extracted) or any(pattern.search(extracted) for _, pattern in _FINAL_OFFER_PATTERNS):
            signals.append("offer_attachment_content")
    return signals


def decide_message_policy(
    requested_type: str,
    *,
    body_text: str = "",
    subject: str = "",
    attachments: Iterable[Any] = (),
    process_stage: str = "",
    deal_stage: str = "",
) -> PolicyDecision:
    """Return the transport policy without trusting a model-provided type."""
    normalized = normalize_message_type(requested_type)
    reasons: list[str] = []
    combined = visible_text(f"{subject}\n{body_text}")

    if normalized == FINAL_OFFER:
        reasons.append("durable_type_final_offer")
    if str(process_stage or "").strip().lower() in FINAL_DEAL_STAGES:
        reasons.append("process_stage_final_offer")
    normalized_deal_stage = str(deal_stage or "").strip().lower()
    if normalized_deal_stage in FINAL_DEAL_STAGES and not (
        normalized == READY_FOR_OFFER_NOTICE and normalized_deal_stage in READY_NOTICE_DEAL_STAGES
    ):
        reasons.append("deal_stage_final_offer")

    reasons.extend(financial_signals(combined))
    reasons.extend(name for name, pattern in _FINAL_OFFER_PATTERNS if pattern.search(combined))
    reasons.extend(offer_attachment_signals(attachments))

    if reasons:
        return PolicyDecision(str(requested_type or ""), FINAL_OFFER, "draft_only", tuple(dict.fromkeys(reasons)))
    if normalized == MANUAL_REVIEW:
        return PolicyDecision(str(requested_type or ""), MANUAL_REVIEW, "manual_review", ("unknown_message_type",))
    if normalized in AUTO_SEND_TYPES:
        return PolicyDecision(str(requested_type or ""), normalized, "auto_send", ("approved_operational_type",))
    return PolicyDecision(str(requested_type or ""), MANUAL_REVIEW, "manual_review", ("policy_default_deny",))


def operational_type_for_flow(*, is_follow_up: bool = False, has_questions: bool = False, ready_for_offer: bool = False, first_contact: bool = False) -> str:
    if ready_for_offer:
        return READY_FOR_OFFER_NOTICE
    if is_follow_up:
        return FOLLOW_UP
    if has_questions:
        return MISSING_DATA_REQUEST if first_contact else CLARIFICATION_REQUEST
    return ACKNOWLEDGEMENT
