#!/usr/bin/env python3
"""Dry-run routing tests for the Hermes mail lead pipeline.

This script uses synthetic fixtures only. It does not read Zoho Mail, write drafts,
touch OAuth tokens, inspect real calendars, call web search, or send messages.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from attachment_router import route_message_attachments


RFQ_PATTERNS = [
    r"\brfq\b",
    r"\brequest for quote\b",
    r"\bwycen[aeęy]\b",
    r"\bofert[aeęy]\b",
    r"\bproposal\b",
    r"\bpricing\b",
    r"\bquote\b",
    r"prosze o oferte",
    r"prosz[eę] o ofert[eę]",
    r"zapytan(?:ie|ia) ofertow",
    r"formularz(?:a|em)? kontaktow",
    r"szybk[aiąe] odpowied",
    r"mniej niz 5 minut",
    r"mniej ni[zż] 5 minut",
    r"<\s*5 minut",
    r"speed[- ]?to[- ]?lead",
    r"uzupelnia(?:nie)? crm",
    r"uzupe[lł]nia(?:nie)? crm",
    r"pierwsz[aeą] odpowied",
    r"automatyzacj[aeę] (?:obslugi|obs[lł]ugi )?zapytan",
    r"automatyzacj[aeę] (?:obslugi|obs[lł]ugi )?zapyta[nń]",
    r"obs[lł]ug[aeę] zapytan",
    r"obs[lł]ug[aeę] zapyta[nń]",
    r"zapytania z (?:maila|e-?maila|formularza|inboxa)",
    r"zapytania? przychodz[ąa]",
    r"system[^\n.!?]{0,120}(?:sledz|śledz)[^\n.!?]{0,80}kont",
    r"kont[oa]? pocztow[ey]",
    r"lead(?:y|ow|ów)? inbound",
    r"inbound lead",
    r"handoff do crm",
    r"agent do zapytan",
    r"agent do zapyta[nń]",
    r"pytania poglebiajace",
    r"pytania pog[lł][eę]biaj[aą]ce",
    r"automatyzacj[^\n.!?]{0,100}zapyt",
    r"agent[^\n.!?]{0,100}zapyt",
    r"crm[^\n.!?]{0,100}zapyt",
    r"inbound[^\n.!?]{0,100}zapyt",
    r"zapyt[^\n.!?]{0,100}(?:automatyzacj|agent|crm|inbound)",
    r"drafty? odpowiedzi[^\n.!?]{0,120}zapyt",
    r"drafty?[^\n.!?]{0,80}zapytan",
    r"drafty?[^\n.!?]{0,80}zapyta[nń]",
]

GENERAL_RFQ_PATTERNS = [
    r"\bautomatyzacj[aeę]\b",
    r"\bagent(?:a|em|y)?\b",
    r"\bcrm\b",
    r"\bhandlow(?:iec|cy|ca)\b",
    r"\binbound\b",
    r"\bleady\b",
]

RELATED_NON_RFQ_PATTERNS = [
    r"\bszkoleni[ae]\b",
    r"\bwarsztat(?:y|u)?\b",
    r"\bkonsulting\b",
    r"\bai dla zespolu\b",
    r"\bautomatyzacja agentow\b",
    r"\bautomatyzacja agent[oó]w\b",
    r"\bprelekcj[aeę]\b",
    r"\bai dla zespo[lł]u\b",
    r"\bmarketingu i hr\b",
    r"\bsales consulting\b",
]

NOT_RFQ_CONTEXT_PATTERNS = [
    r"bez wdro[zż]enia rfq",
    r"not quote request automation",
    r"nie dotyczy (?:rfq|zapytan|zapyta[nń]|wycen)",
    r"bez automatyzacji zapyta[nń]",
]

UNCLEAR_RECEIVED_OFFER_PATTERNS = [
    r"dosta[lł]em ofert[eę]",
    r"otrzyma[lł]em ofert[eę]",
]

WEAK_FIT_PATTERNS = [
    r"\bmedyczn[ayei]\b",
    r"\bklinika\b",
    r"\bfinansow[ayei]\b",
    r"\bbank(?:u|owy|owosc|owo[sś][cć])?\b",
    r"\bubezpieczeni(?:e|a|owy|owa|owe|owym|owych)\b",
    r"\bbroker ubezpieczeni",
    r"\bpoufne\b",
    r"\b1-2 zapytania\b",
    r"\bjedno zapytanie\b",
    r"sam(?:o|a)?dzielnie wycen",
    r"sam(?:o|a)?dzielnie wysy[lł]a(?:l|ć|c)? finalne",
    r"wysy[lł]a(?:l|ć|c)? finalne ofert",
    r"bez cz[lł]owieka",
    r"bez udzia[lł]u cz[lł]owieka",
    r"raz na kwarta[lł]",
    r"bez powtarzalnego [zź]r[oó]d[lł]a",
    r"niski wolumen",
    r"bardzo poufny",
    r"bardzo poufne",
]

HUMAN_REVIEW_PATTERNS = [
    r"\bhas[lł]o\b",
    r"\btoken\b",
    r"\bapi key\b",
    r"\bklucz prywatny\b",
    r"\bdane medyczne\b",
    r"przygotowywa[ćc].*przelew",
    r"wykon(?:a[ćc]|ywac|ywa[ćc]).*przelew",
    r"op[lł]aca[ćc]?.*faktur",
    r"p[lł]atno[sś]ci.*bez udzia[lł]u cz[lł]owieka",
    r"przelew.*bez udzia[lł]u cz[lł]owieka",
    r"\bpozew\b",
    r"\breklamacj[aeę]\b",
    r"\bsp[oó]r prawny\b",
    r"security report",
    r"podatno[sś]",
    r"usuni[eę]cie danych",
    r"dost[eę]p do danych",
]

# An override attempt is a verb aimed at our operating constraints. Matching the
# pair rather than either word alone keeps ordinary business wording ("zasady
# współpracy", "our internal rules") out of the review queue.
_OVERRIDE_VERB_PL = r"(?:zignoruj|ignoruj|pomi[nń]|zapomnij|nadpisz|obejd[zź]|odrzu[cć])"
_OVERRIDE_OBJECT_PL = r"(?:instrukcj\w*|regu[lł]\w*|zasad\w*|wytyczn\w*|polece[nń]\w*|ograniczen\w*)"
_OVERRIDE_VERB_EN = r"(?:ignore|disregard|forget|override|bypass|discard)"
_OVERRIDE_OBJECT_EN = r"(?:instructions?|rules?|guidelines?|directives?|constraints?|restrictions?|polic(?:y|ies)|prompt)"

PROMPT_INJECTION_PATTERNS = [
    rf"\b{_OVERRIDE_VERB_PL}\b[^.\n]{{0,40}}\b{_OVERRIDE_OBJECT_PL}",
    rf"\b{_OVERRIDE_VERB_EN}\b[^.\n]{{0,40}}\b{_OVERRIDE_OBJECT_EN}",
    # Role hijack: the sender tries to redefine who or what is answering.
    r"(?:^|\n)\s*#{0,4}\s*(?:system|assistant|developer)\s*[:>]",
    r"\b(?:you are now|act as|pretend to be|jeste[sś] teraz|dzia[lł]asz teraz jako)\b",
    # An instruction to skip the human approval step is an attack on the guardrail.
    r"\b(?:wy[sś]l\w*|wysy[lł]\w*|send|email)\b[^.\n]{0,60}\b(?:bez|without)\b[^.\n]{0,30}"
    r"\b(?:zatwierdzeni\w*|akceptacj\w*|approval|human)",
    r"ujawnij .*?(prompt|instrukcj|system)",
    r"reveal .*?(prompt|system)",
    r"wy[sś]lij .*?(sekret|token|has[lł]o|klucz)",
    r"send .*?(secret|token|password|private key)",
]

SUSPICIOUS_LINK_PATTERNS = [
    r"https?://(?:bit\.ly|tinyurl\.com|t\.co|goo\.gl|ow\.ly)/",
    r"https?://[^\s]+/(?:login|signin|verify|reset)[^\s]*",
    r"https?://[^\s]+\.(?:exe|scr|bat|cmd|ps1|js|vbs|jar)(?:\b|[?#])",
]

SUSPICIOUS_ATTACHMENT_EXTENSIONS = {
    ".7z",
    ".apk",
    ".bat",
    ".cmd",
    ".com",
    ".dmg",
    ".docm",
    ".exe",
    ".gz",
    ".iso",
    ".jar",
    ".js",
    ".msi",
    ".pkg",
    ".ps1",
    ".rar",
    ".scr",
    ".tar",
    ".vbs",
    ".xlsm",
    ".zip",
}

SUSPICIOUS_ATTACHMENT_MIMES = {
    "application/javascript",
    "application/java-archive",
    "application/vnd.microsoft.portable-executable",
    "application/x-7z-compressed",
    "application/x-dosexec",
    "application/x-msdownload",
    "application/x-msdos-program",
    "application/x-rar-compressed",
    "application/x-sh",
    "application/zip",
}

ATTACHMENT_EXTRACTION_CLASSES = {
    "new_quote_request",
    "quote_draft_ready",
    "existing_client_request",
    "existing_thread_reply",
    "same_domain_new_person",
}

ADMIN_PATTERNS = [
    r"\binvoice\b",
    r"\bfaktura\b",
    r"\bpayment\b",
    r"\bsubscription\b",
    r"\brenewal\b",
    r"\bbilling\b",
    r"\breceipt\b",
    r"legal notice",
    r"account notice",
    r"platform vendor",
    r"platform account",
    r"no action required",
]

AUTOMATED_PATTERNS = [
    r"\bnewsletter\b",
    r"\bunsubscribe\b",
    r"\bwebinar\b",
    r"\bmailer-daemon\b",
    r"\bdigest\b",
    r"mail delivery failed",
    r"delivery failed",
]

FORMAL_INTENT_PATTERNS = {
    "complaint": [r"\breklamacj", r"complaint", r"nie dziala", r"nie działa"],
    "security": [r"\bincydent", r"security report", r"naruszen", r"podatno[sś]"],
    "legal": [r"\bpozew\b", r"\bprawny\b", r"legal notice", r"wezwan"],
    "data_request": [r"usuni[eę]cie danych", r"delete my data", r"dost[eę]p do danych", r"data subject"],
    "billing": [r"\bfaktura\b", r"\bp[lł]atno", r"\bbilling\b", r"\binvoice\b"],
}


def domain_from_email(address: str) -> str:
    if "@" not in address:
        return ""
    return address.rsplit("@", 1)[1].strip().lower()


def has_any(patterns: list[str], text: str) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def header_value(headers: dict[str, Any], name: str) -> str:
    for key, value in headers.items():
        if key.lower() == name.lower():
            return str(value)
    return ""


def is_automated(message: dict[str, Any], combined: str) -> bool:
    sender = str(message.get("from", "")).lower()
    headers = message.get("headers", {}) or {}
    if sender.startswith(("noreply@", "no-reply@", "mailer-daemon@", "bounce@")):
        return True
    if header_value(headers, "List-Unsubscribe"):
        return True
    if header_value(headers, "Precedence").lower() in {"bulk", "junk", "list"}:
        return True
    if header_value(headers, "Auto-Submitted"):
        return True
    return has_any(AUTOMATED_PATTERNS, combined)


def is_reply(message: dict[str, Any], context: dict[str, Any] | None = None) -> bool:
    headers = message.get("headers", {}) or {}
    context = context or {}
    return bool(
        header_value(headers, "In-Reply-To")
        or header_value(headers, "References")
        or context.get("known_thread_id")
        or context.get("known_conversation")
    )


def attachment_requires_review(attachment_routes: list[dict[str, Any]]) -> bool:
    return any(route["safety"] in {"block", "review"} for route in attachment_routes)


def attachment_extraction_allowed(classification: str, confidence: str, attachment_routes: list[dict[str, Any]]) -> bool:
    if not attachment_routes:
        return False
    if attachment_requires_review(attachment_routes):
        return False
    if confidence == "low":
        return False
    return classification in ATTACHMENT_EXTRACTION_CLASSES


def reply_sender_domain_mismatch(message: dict[str, Any], context: dict[str, Any], domain: str) -> bool:
    expected_domain = str(context.get("expected_thread_domain", "")).strip().lower()
    return bool(expected_domain and is_reply(message, context) and domain and domain != expected_domain)


def has_minimum_offer_data(context: dict[str, Any]) -> bool:
    if context.get("minimum_offer_data") is True:
        return True
    fields = context.get("offer_data_fields", [])
    return isinstance(fields, list) and len({str(field) for field in fields}) >= 6


def draft_thread_action(should_draft: bool) -> str:
    return "reply_to_inbound" if should_draft else "none"


def classification_axes(
    *,
    message: dict[str, Any],
    context: dict[str, Any],
    result: dict[str, Any],
    attachment_routes: list[dict[str, Any]],
    minimum_offer_data: bool,
) -> dict[str, str]:
    """Produce independent dimensions; the legacy class remains for compatibility."""
    combined = f"{message.get('subject', '')}\n{message.get('body', '')}"
    normalized = combined.lower()
    source_type = "google_sheets" if context.get("source_type") == "google_sheets" else "email"

    intent = "rfq"
    for candidate, patterns in FORMAL_INTENT_PATTERNS.items():
        if has_any(patterns, normalized):
            intent = {"security": "security", "data_request": "data_request", "legal": "legal", "billing": "billing", "complaint": "complaint"}[candidate]
            break
    if intent == "rfq" and result.get("classification") in {"new_general_business_inquiry", "related_non_rfq_topic"}:
        intent = "general_business"
    elif intent == "rfq" and result.get("classification") in {"vendor_admin_billing"}:
        intent = "billing"
    elif intent == "rfq" and not result.get("should_draft") and result.get("classification") == "unknown_review_needed":
        intent = "unknown"

    known_client = str(result.get("classification")) == "existing_client_request" or bool(context.get("known_client_domains"))
    is_thread = is_reply(message, context)
    if is_thread:
        conversation_relation = "existing_thread"
    elif known_client:
        conversation_relation = "existing_client"
    elif result.get("classification") == "same_domain_new_person":
        conversation_relation = "same_domain_new_person"
    else:
        conversation_relation = "new"

    blocked_attachment = any(route.get("safety") in {"block", "blocked"} or route.get("status") == "blocked" for route in attachment_routes)
    review_attachment = any(route.get("safety") == "review" or route.get("status") in {"review", "unsupported"} for route in attachment_routes)
    if blocked_attachment or result.get("classification") in {"human_review_only"} and (has_any(PROMPT_INJECTION_PATTERNS, combined) or review_attachment):
        risk = "blocked"
    elif review_attachment or result.get("confidence") == "low" or result.get("classification") in {"weak_fit_review_only", "unknown_review_needed"} or intent in {"complaint", "security", "legal", "data_request", "billing"}:
        risk = "review"
    else:
        risk = "safe"

    if result.get("classification") == "weak_fit_review_only":
        fit = "weak"
    elif result.get("classification") in {"new_quote_request", "quote_draft_ready", "existing_client_request", "existing_thread_reply", "same_domain_new_person"} or intent == "rfq":
        fit = "good"
    else:
        fit = "unknown"

    offer_status = "ready" if minimum_offer_data else ("missing_data" if intent == "rfq" else "discovery")
    if risk in {"blocked", "review"} or intent != "rfq":
        action = "internal_review" if risk != "safe" or intent != "rfq" else "skip"
    elif offer_status == "ready" and result.get("draft_kind") == "final_offer":
        action = "offer_draft"
    elif result.get("should_draft"):
        action = "customer_draft"
    else:
        action = "skip"
    return {
        "source_type": source_type,
        "intent": intent,
        "conversation_relation": conversation_relation,
        "risk": risk,
        "fit": fit,
        "offer_status": offer_status,
        "action": action,
    }


def telegram_topic(classification: str) -> str:
    topics = {
        "new_quote_request": "wyglada jak zapytanie o Orchesta RFQ",
        "quote_draft_ready": "ma dosc danych na draft oferty do review",
        "new_general_business_inquiry": "jest ogolnym pytaniem o automatyzacje zapytan",
        "related_non_rfq_topic": "dotyczy tematu obok Orchesta RFQ",
        "weak_fit_review_only": "wyglada na slaby fit albo temat wrazliwy",
        "human_review_only": "wymaga recznego sprawdzenia przed jakakolwiek odpowiedzia",
        "existing_client_request": "wyglada na prosbe od istniejacego klienta",
        "existing_thread_reply": "jest odpowiedzia w istniejacym watku",
        "same_domain_new_person": "pochodzi od nowej osoby z domeny znanej w historii",
        "vendor_admin_billing": "wyglada na wiadomosc administracyjna albo fakture",
        "newsletter_automated_spam": "wyglada na automatyczna wiadomosc",
        "unknown_review_needed": "jest niejednoznaczna i wymaga decyzji",
    }
    return topics.get(classification, "wymaga sprawdzenia")


def telegram_summary(case: dict[str, Any], result: dict[str, Any]) -> str:
    if not result["wake_agent"]:
        return ""

    message = case["message"]
    sender = str(message.get("from", "")).strip()
    classification = result["classification"]
    topic = telegram_topic(classification)

    if result["should_draft"]:
        if result["draft_kind"] == "final_offer":
            decision = "Przygotowalbym draft oferty do review jako odpowiedz w tym samym watku."
        else:
            decision = "Przygotowalbym krotki draft odpowiedzi w tym samym watku."
    elif result["telegram_only"]:
        decision = "Nie przygotowuje draftu do klienta, bo potrzebna jest Twoja decyzja."
    else:
        decision = "Nie przygotowuje draftu do klienta; wystarczy briefing albo brak akcji."

    return f"Dostales wiadomosc od {sender}: {topic}. {decision}"


def sentence_count(text: str) -> int:
    if not text.strip():
        return 0
    return len([part for part in re.split(r"[.!?]+(?:\s+|$)", text.strip()) if part.strip()])


def text_tokens(text: str) -> set[str]:
    raw = re.findall(r"[a-zA-ZąćęłńóśźżĄĆĘŁŃÓŚŹŻ0-9]{4,}", text.lower())
    stop = {"dzien", "dobry", "prosze", "temat", "firma", "zapytanie", "oferta"}
    return {token for token in raw if token not in stop}


def deal_action(domain: str, combined: str, context: dict[str, Any], lead_like: bool) -> str:
    if not lead_like:
        return "not_applicable"

    known_deals = context.get("known_deals", []) or []
    current_tokens = text_tokens(combined)
    same_domain_seen = False
    matching_deals: list[dict[str, Any]] = []

    for deal in known_deals:
        if str(deal.get("domain", "")).lower() != domain:
            continue
        same_domain_seen = True
        keywords = {str(keyword).lower() for keyword in deal.get("topic_keywords", [])}
        if current_tokens & keywords:
            matching_deals.append(deal)

    if len(matching_deals) > 1:
        return "crm_conflict"
    if len(matching_deals) == 1 and domain not in {"gmail.com", "outlook.com", "hotmail.com", "onet.pl", "wp.pl", "interia.pl"}:
        return "merge_existing_deal"

    if same_domain_seen:
        return "separate_deal"
    if context.get("crm_note_exists") is True:
        return "append_history"
    return "new_deal"


def classify(case: dict[str, Any]) -> dict[str, Any]:
    message = case["message"]
    context = case.get("context", {}) or {}
    sender = str(message.get("from", "")).strip().lower()
    domain = domain_from_email(sender)
    subject = str(message.get("subject", ""))
    body = str(message.get("body", ""))
    combined = f"{subject}\n{body}"
    attachment_routes = route_message_attachments(message)
    rfq_signal = has_any(RFQ_PATTERNS, combined)
    related_non_rfq_signal = has_any(RELATED_NON_RFQ_PATTERNS, combined)
    explicit_not_rfq_signal = has_any(NOT_RFQ_CONTEXT_PATTERNS, combined)
    unclear_received_offer_signal = has_any(UNCLEAR_RECEIVED_OFFER_PATTERNS, combined)

    known_client_domains = {d.lower() for d in context.get("known_client_domains", [])}
    known_domains = {d.lower() for d in context.get("known_domains", [])}
    known_senders = {s.lower() for s in context.get("known_senders", [])}
    minimum_offer_data = has_minimum_offer_data(context)

    if (
        attachment_requires_review(attachment_routes)
        or has_any(PROMPT_INJECTION_PATTERNS, combined)
        or has_any(SUSPICIOUS_LINK_PATTERNS, combined)
    ):
        classification = "human_review_only"
        confidence = "high"
    elif reply_sender_domain_mismatch(message, context, domain):
        classification = "unknown_review_needed"
        confidence = "medium"
    elif is_automated(message, combined):
        classification = "newsletter_automated_spam"
        confidence = "high"
    elif related_non_rfq_signal and explicit_not_rfq_signal:
        classification = "related_non_rfq_topic"
        confidence = "high"
    elif unclear_received_offer_signal and not has_any([r"orchesta", r"rfq", r"zapytan", r"zapyta[nń]", r"wycen"], combined):
        classification = "unknown_review_needed"
        confidence = "medium"
    elif has_any(HUMAN_REVIEW_PATTERNS, combined):
        classification = "human_review_only"
        confidence = "high"
    elif has_any(WEAK_FIT_PATTERNS, combined):
        classification = "weak_fit_review_only"
        confidence = "medium"
    elif has_any(ADMIN_PATTERNS, combined) and not rfq_signal:
        classification = "vendor_admin_billing"
        confidence = "high"
    elif domain in known_client_domains:
        classification = "existing_client_request"
        confidence = "high"
    elif is_reply(message, context):
        classification = "existing_thread_reply"
        confidence = "high"
    elif related_non_rfq_signal and not rfq_signal:
        classification = "related_non_rfq_topic"
        confidence = "medium"
    elif sender not in known_senders and domain in known_domains:
        classification = "same_domain_new_person"
        confidence = "high"
    elif rfq_signal and minimum_offer_data:
        classification = "quote_draft_ready"
        confidence = "high"
    elif rfq_signal:
        classification = "new_quote_request"
        confidence = "high"
    elif has_any(GENERAL_RFQ_PATTERNS, combined):
        classification = "new_general_business_inquiry"
        confidence = "medium"
    else:
        classification = "unknown_review_needed"
        confidence = "low"

    draftable_classes = {
        "new_quote_request",
        "quote_draft_ready",
        "new_general_business_inquiry",
        "existing_client_request",
        "existing_thread_reply",
        "same_domain_new_person",
    }
    telegram_only_classes = {
        "related_non_rfq_topic",
        "weak_fit_review_only",
        "human_review_only",
        "unknown_review_needed",
    }

    should_draft = classification in draftable_classes
    telegram_only = classification in telegram_only_classes
    wake_agent = classification != "newsletter_automated_spam"

    if not should_draft:
        draft_kind = "none"
    elif classification == "quote_draft_ready" or (classification == "existing_thread_reply" and minimum_offer_data):
        draft_kind = "final_offer"
    elif classification in {"existing_client_request", "existing_thread_reply"}:
        draft_kind = "context_reply"
    else:
        draft_kind = "first_response"

    lead_like = classification in {
        "new_quote_request",
        "quote_draft_ready",
        "new_general_business_inquiry",
        "same_domain_new_person",
        "existing_client_request",
    } or (classification == "existing_thread_reply" and context.get("crm_note_exists") is True)
    runtime_mode = "degraded" if context.get("mac_bridge_available") is False else "normal"
    crm_action = "skipped_mac_bridge_unavailable" if runtime_mode == "degraded" and lead_like else deal_action(domain, combined, context, lead_like)

    result = {
        "classification": classification,
        "confidence": confidence,
        "should_draft": should_draft,
        "draft_kind": draft_kind,
        "draft_thread_action": draft_thread_action(should_draft),
        "telegram_only": telegram_only,
        "wake_agent": wake_agent,
        "runtime_mode": runtime_mode,
        "crm_action": crm_action,
        "attachment_routes": [route["route"] for route in attachment_routes],
        "attachment_safety": "review" if attachment_requires_review(attachment_routes) else "allow",
        "attachment_extraction_allowed": attachment_extraction_allowed(classification, confidence, attachment_routes),
        "contact_action": "new_contact" if classification == "same_domain_new_person" else "existing_contact_or_new_lead",
    }
    result.update(classification_axes(message=message, context=context, result=result, attachment_routes=attachment_routes, minimum_offer_data=minimum_offer_data))
    return result


def expected(case: dict[str, Any], result: dict[str, Any], key: str) -> Any:
    expected_key = f"expected_{key}"
    if expected_key in case:
        return case[expected_key]
    return result[key]


def run(fixtures_path: Path) -> int:
    cases = json.loads(fixtures_path.read_text(encoding="utf-8"))
    failures: list[str] = []
    rows: list[tuple[str, str, str, str, bool, str, str, str]] = []

    for case in cases:
        result = classify(case)
        checks = {
            "classification": expected(case, result, "classification"),
            "draft": bool(expected(case, result, "draft")),
            "wake_agent": bool(expected(case, result, "wake_agent")),
            "draft_kind": expected(case, result, "draft_kind"),
            "draft_thread_action": expected(case, result, "draft_thread_action"),
            "telegram_only": bool(expected(case, result, "telegram_only")),
            "runtime_mode": expected(case, result, "runtime_mode"),
            "crm_action": expected(case, result, "crm_action"),
        }
        if "expected_attachment_routes" in case:
            checks["attachment_routes"] = case["expected_attachment_routes"]
        if "expected_attachment_extraction_allowed" in case:
            checks["attachment_extraction_allowed"] = case["expected_attachment_extraction_allowed"]
        summary = telegram_summary(case, result)
        summary_sentences = sentence_count(summary)
        summary_ok = (
            summary_sentences == 0
            if not result["wake_agent"]
            else 2 <= summary_sentences <= 3 and "\n" not in summary
        )

        ok = (
            result["classification"] == checks["classification"]
            and result["should_draft"] == checks["draft"]
            and result["wake_agent"] == checks["wake_agent"]
            and result["draft_kind"] == checks["draft_kind"]
            and result["draft_thread_action"] == checks["draft_thread_action"]
            and result["telegram_only"] == checks["telegram_only"]
            and result["runtime_mode"] == checks["runtime_mode"]
            and result["crm_action"] == checks["crm_action"]
            and ("attachment_routes" not in checks or result["attachment_routes"] == checks["attachment_routes"])
            and (
                "attachment_extraction_allowed" not in checks
                or result["attachment_extraction_allowed"] == checks["attachment_extraction_allowed"]
            )
            and summary_ok
        )
        rows.append(
            (
                case["id"],
                result["classification"],
                result["draft_kind"],
                result["draft_thread_action"],
                result["telegram_only"],
                result["runtime_mode"],
                result["crm_action"],
                "ok" if ok else "FAIL",
            )
        )
        if not ok:
            failures.append(
                f"{case['id']}: got class={result['classification']} "
                f"draft={result['should_draft']} kind={result['draft_kind']} "
                f"thread={result['draft_thread_action']} "
                f"telegram={result['telegram_only']} wake={result['wake_agent']} "
                f"runtime={result['runtime_mode']} crm={result['crm_action']} "
                f"expected class={checks['classification']} draft={checks['draft']} "
                f"kind={checks['draft_kind']} thread={checks['draft_thread_action']} "
                f"telegram={checks['telegram_only']} "
                f"wake={checks['wake_agent']} runtime={checks['runtime_mode']} "
                f"crm={checks['crm_action']} attachments={result['attachment_routes']} "
                f"expected_attachments={checks.get('attachment_routes', 'not_checked')} "
                f"extract_allowed={result['attachment_extraction_allowed']} "
                f"expected_extract_allowed={checks.get('attachment_extraction_allowed', 'not_checked')} "
                f"telegram_summary_sentences={summary_sentences}"
            )

    print("mail-lead-pipeline dry-run")
    print("case | classification | draftKind | draftThread | telegramOnly | runtime | crmAction | status")
    print("--- | --- | --- | --- | --- | --- | --- | ---")
    for case_id, classification, draft_kind, draft_thread, telegram_only, runtime, crm_action, status in rows:
        print(f"{case_id} | {classification} | {draft_kind} | {draft_thread} | {str(telegram_only).lower()} | {runtime} | {crm_action} | {status}")

    if failures:
        print("\nFailures:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    print(f"\nAll {len(cases)} fixture cases passed.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--fixtures",
        default="tests/fixtures/mail-lead-pipeline/cases.json",
        help="Path to JSON fixture cases.",
    )
    args = parser.parse_args()
    return run(Path(args.fixtures))


if __name__ == "__main__":
    raise SystemExit(main())
