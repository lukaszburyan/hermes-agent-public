#!/usr/bin/env python3
"""Send internal prose email notifications for Orchesta RFQ mailbox events.

This helper is intentionally separate from customer-facing draft creation. It
sends only an internal notification to Lukasz's work address and never replies to
inbound senders. It reads the poller JSON summary, redacts sensitive values, and
uses Zoho Mail's message API in send mode for the fixed internal recipient.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EXECUTION_DIR = Path(__file__).resolve().parent
if str(EXECUTION_DIR) not in sys.path:
    sys.path.insert(0, str(EXECUTION_DIR))

from zoho_mail_poller import HttpZohoClient, select_account  # noqa: E402
from unified_lead_registry import UnifiedLeadRegistry  # noqa: E402
from zoho_reply_draft import HttpDraftPoster  # noqa: E402
import tenant_config  # noqa: E402

DEFAULT_TOKEN_FILE = "/opt/data/.tmp/zoho_mail_tokens.json"
DEFAULT_ENV_FILE = "/opt/data/.env"
DEFAULT_RECIPIENT = tenant_config.internal_notification_email("orchesta")
DEFAULT_DELIVERY_STATE_FILE = "/opt/data/.tmp/orchesta-rfq-notification-ledger.json"
DEFAULT_HERMES_BIN = "/opt/hermes/.venv/bin/hermes"
DEFAULT_REGISTRY_FILE = "/opt/data/.tmp/orchesta-rfq-unified.sqlite3"
INTERNAL_RECIPIENTS = frozenset({DEFAULT_RECIPIENT})
SECRET_RE = re.compile(r"(?i)(token|secret|password|refresh_token|access_token|client_secret|api_key)([\"\s:=]+)[A-Za-z0-9_.-]{8,}")
SUCCESS_DRAFT_ACTIONS = frozenset({"created", "sent", "already_sent", "already_sent_reconciled"})
ATTENTION_CLASSIFICATIONS = frozenset({
    "unknown_review_needed",
    "related_non_rfq_topic",
    "human_review_only",
    "weak_fit_review_only",
})
ATTENTION_ACTIONS = frozenset({
    "auto_draft_failed",
    "auto_send_failed",
    "deferred_content_unavailable",
    "final_offer_blocked",
    "final_offer_failed",
    "send_outcome_unknown",
    "send_reconcile_pending",
    "sent_guard_failed",
    "would_create_pending_approval",
})


class DeliveryNotAttemptedError(RuntimeError):
    """Internal notification failed before the provider POST began."""


class DeliveryOutcomeUnknownError(RuntimeError):
    """Provider POST began but acceptance could not be confirmed."""


def _successful_briefing(briefing: dict[str, Any]) -> bool:
    """Return true only when a customer-facing action actually completed."""
    draft = briefing.get("draft") if isinstance(briefing.get("draft"), dict) else {}
    response = briefing.get("response") if isinstance(briefing.get("response"), dict) else {}
    final_offer = briefing.get("final_offer") if isinstance(briefing.get("final_offer"), dict) else {}
    if draft.get("notify_only") is True:
        return False
    if str(draft.get("action") or "") in SUCCESS_DRAFT_ACTIONS:
        return True
    if str(response.get("action") or "") in {"sent", "already_sent", "already_sent_reconciled"}:
        return True
    return str(final_offer.get("action") or "") == "created"


def _attention_briefing(briefing: dict[str, Any]) -> bool:
    """Return true for a real lead/RFQ that needs Lukasz's intervention."""
    classification = str(briefing.get("classification") or briefing.get("class") or "")
    if classification == "newsletter_automated_spam":
        return False
    draft = briefing.get("draft") if isinstance(briefing.get("draft"), dict) else {}
    response = briefing.get("response") if isinstance(briefing.get("response"), dict) else {}
    message = briefing.get("message") if isinstance(briefing.get("message"), dict) else {}
    final_offer = briefing.get("final_offer") if isinstance(briefing.get("final_offer"), dict) else {}
    telegram = briefing.get("telegram") if isinstance(briefing.get("telegram"), dict) else {}
    sheet = briefing.get("sheet") if isinstance(briefing.get("sheet"), dict) else {}
    draft_action = str(draft.get("action") or "")
    if draft_action.startswith("blocked_") or draft_action in ATTENTION_ACTIONS:
        return True
    if str(sheet.get("status") or "").strip().lower() == "wymaga sprawdzenia":
        return True
    if str(response.get("action") or message.get("action") or "") in {"blocked", "error", "failed", "outcome_unknown"}:
        return True
    if str(final_offer.get("action") or "") in {"blocked", "error", "failed"}:
        return True
    if draft.get("error") or response.get("error") or message.get("error") or final_offer.get("error"):
        return True
    if str(telegram.get("action") or "") == "escalate_review_only":
        return True
    return classification in ATTENTION_CLASSIFICATIONS


def _notifiable_briefing(briefing: dict[str, Any]) -> bool:
    return _successful_briefing(briefing) or _attention_briefing(briefing)


def validate_internal_recipient(recipient: str) -> str:
    normalized = str(recipient or "").strip().lower()
    if normalized not in INTERNAL_RECIPIENTS:
        raise PermissionError("internal notification recipient is not allowlisted")
    return normalized


def redact(text: str) -> str:
    text = SECRET_RE.sub(r"\1\2[REDACTED]", text or "")
    text = re.sub(r"\b(?:\d[ -]?){13,19}\b", "[CARD_REDACTED]", text)
    return text


def relation_label(sender: dict[str, Any]) -> str:
    rel = str(sender.get("relationship") or "").lower()
    if rel in {"new_or_unknown", "unknown", "new_domain", ""}:
        return "nieznana osoba"
    if "client" in rel or "known" in rel or "existing" in rel:
        return "znana osoba"
    return rel.replace("_", " ")


def reason_for(classification: str, draft_action: str, final_offer: dict[str, Any] | None, response_action: str = "none") -> str:
    if response_action == "sent":
        return "odpowiedziałem autonomicznie pierwszą wiadomością z Zoho, ponieważ lead wyglądał na bezpieczne zapytanie RFQ o wysokiej pewności."
    if draft_action == "created" and final_offer and final_offer.get("action") == "created":
        price = final_offer.get("price_net_display")
        if price:
            return f"przygotowałem szkic ostatecznej oferty z plikiem PDF. W ofercie jest kwota {price} netto. Nic nie zostało wysłane automatycznie."
        return "przygotowałem szkic ostatecznej oferty z plikiem PDF. Nic nie zostało wysłane automatycznie."
    if draft_action == "created":
        return "przygotowałem szkic odpowiedzi, ale go nie wysłałem. Wymaga Twojego sprawdzenia przed wysyłką."
    if draft_action == "deferred_content_unavailable":
        return "nie mogłem odczytać treści wiadomości z Zoho, więc niczego nie wysłałem i pozostawiłem sprawę do bezpiecznego ponowienia oraz Twojej kontroli."
    if draft_action == "sent_guard_failed":
        return "zablokowałem automatyczną odpowiedź, ponieważ nie udało się potwierdzić stanu folderu Wysłane; chroni to klienta przed duplikatem."
    if draft_action == "blocked_human_takeover":
        return "wykryłem Twoją ręczną odpowiedź w tej rozmowie, dlatego zatrzymałem automatykę tylko dla tej sprawy."
    if draft_action.startswith("blocked_"):
        return "nie odpowiedziałem automatycznie, ponieważ wiadomość wymaga sprawdzenia lub brakuje ważnej informacji. Nic nie zostało wysłane do klienta."
    if draft_action in {"auto_draft_failed", "final_offer_failed", "final_offer_blocked", "send_reconcile_pending"}:
        return "nie zakończyłem automatycznej obsługi tej sprawy; niczego nie wysłałem ponownie i potrzebna jest Twoja kontrola."
    if classification == "related_non_rfq_topic":
        return "nie odpisywałem, ponieważ wiadomość dotyczy tematu obok Orchesta RFQ i wymaga Twojej decyzji."
    if classification in {"human_review_only", "weak_fit_review_only"}:
        return "nie odpisywałem, ponieważ wiadomość wygląda na wrażliwą, ryzykowną albo słaby fit i wymaga ręcznego sprawdzenia."
    if classification == "newsletter_automated_spam":
        return "nie odpisywałem, ponieważ wygląda to na newsletter albo automatyczną wiadomość."
    if classification == "unknown_review_needed":
        return "nie odpisywałem, ponieważ wiadomość jest niejednoznaczna i wymaga Twojej decyzji."
    return "nie wysłałem żadnej odpowiedzi automatycznie; zostawiam temat do Twojej kontroli."


def prose_for_briefing(briefing: dict[str, Any]) -> str:
    sender = briefing.get("sender") if isinstance(briefing.get("sender"), dict) else {}
    sender_email = str(sender.get("email") or briefing.get("from") or "nieznany nadawca")
    label = relation_label(sender)
    classification = str(briefing.get("classification") or briefing.get("class") or "unknown_review_needed")
    draft = briefing.get("draft") if isinstance(briefing.get("draft"), dict) else {}
    draft_action = str(draft.get("action") or "none")
    response = dict(briefing.get("response") or {}) if isinstance(briefing.get("response"), dict) else {}
    message = dict(briefing.get("message") or {}) if isinstance(briefing.get("message"), dict) else {}
    response_action = str(response.get("action") or message.get("action") or ("sent" if draft_action == "sent" else "none"))
    final_offer = briefing.get("final_offer") if isinstance(briefing.get("final_offer"), dict) else None
    next_step = str(briefing.get("next_step") or "")

    text = (
        f"Napisała do Ciebie {label}: {sender_email}. "
        f"{reason_for(classification, draft_action, final_offer, response_action)}"
    )
    if next_step:
        text += f" Następny krok: {next_step}"
    if final_offer and final_offer.get("action") == "created":
        offer_details = dict(final_offer)
        client = briefing.get("client") if isinstance(briefing.get("client"), dict) else {}
        sheet = briefing.get("sheet") if isinstance(briefing.get("sheet"), dict) else {}
        company = str(client.get("company") or sheet.get("company") or "firma nieustalona")
        contact = str(client.get("contact_name") or client.get("full_name") or sender_email)
        scope = str(offer_details.get("scope_display") or "zakres w załączonej ofercie")
        price = str(offer_details.get("price_net_display") or "cena w ofercie")
        location = str(offer_details.get("draft_location") or "Zoho Mail > Drafts")
        text += (
            f" Firma: {company}. Kontakt: {contact}. Zakres: {scope}. "
            f"Cena netto: {price}. Szkic znajdziesz tutaj: {location}. "
            "Oferta nie została wysłana klientowi."
        )
    return redact(text)


def _event_types(briefing: dict[str, Any]) -> list[str]:
    draft = briefing.get("draft") if isinstance(briefing.get("draft"), dict) else {}
    response = briefing.get("response") if isinstance(briefing.get("response"), dict) else {}
    message = briefing.get("message") if isinstance(briefing.get("message"), dict) else {}
    final_offer = briefing.get("final_offer") if isinstance(briefing.get("final_offer"), dict) else {}
    action = str(draft.get("action") or "")
    is_manual_review = (
        str(briefing.get("routing_action") or "") == "review"
        or str(briefing.get("classification") or "") in ATTENTION_CLASSIFICATIONS
    )
    reason = " ".join(
        str(value or "")
        for value in (
            briefing.get("reason_code"),
            draft.get("error"),
            final_offer.get("error"),
            final_offer.get("status_name"),
        )
    ).lower()
    events: list[str] = []
    outbound_action = str(response.get("action") or message.get("action") or action)
    if outbound_action in {"sent", "already_sent", "already_sent_reconciled"}:
        events.append("automatic pre-offer reply")
    if str(final_offer.get("action") or "") == "created":
        if final_offer.get("pdf_attached"):
            events.append("PDF prepared")
        events.append("final offer draft ready")
    elif action in {"final_offer_failed", "final_offer_blocked"} or str(final_offer.get("action") or "") in {"blocked", "error", "failed"}:
        events.append("PDF control failed" if "pdf" in reason else "final offer error")
    if "reply_validation_failed_after_retry" in reason:
        events.append("LLM error after two attempts")
    if briefing.get("customer_fact_conflicts"):
        events.append("stored data conflict")
    if briefing.get("commercial_exceptions"):
        events.append("commercial exception")
    if action == "blocked_human_takeover":
        events.append("human takeover")
    elif action == "blocked_conversation_handoff":
        events.append("conversation handoff")
    elif action == "blocked_commercial_exception" and not briefing.get("commercial_exceptions"):
        events.append("commercial exception")
    elif (action.startswith("blocked_") or action == "sent_guard_failed") and not is_manual_review:
        events.append("safety block")
    elif action in {"auto_draft_failed", "auto_send_failed"}:
        events.append("automation error")
    elif action == "deferred_content_unavailable":
        events.append("mailbox read error")
    elif action in {"send_outcome_unknown", "send_reconcile_pending"}:
        events.append("uncertain send outcome")
    elif action == "would_create_pending_approval":
        events.append("manual review")
    if is_manual_review:
        events.append("manual review")
    if action == "created" and not final_offer:
        events.append("draft prepared")
    return list(dict.fromkeys(events))


def build_notifications(
    summary: dict[str, Any],
    selected_briefings: list[dict[str, Any]] | None = None,
) -> list[tuple[str, dict[str, Any], str, str]]:
    candidates = selected_briefings if selected_briefings is not None else summary.get("briefings", [])
    result: list[tuple[str, dict[str, Any], str, str]] = []
    for briefing in candidates:
        if not isinstance(briefing, dict) or not _notifiable_briefing(briefing):
            continue
        final_offer = briefing.get("final_offer") if isinstance(briefing.get("final_offer"), dict) else {}
        case_id = str(
            briefing.get("rfq_id")
            or briefing.get("deal_id")
            or final_offer.get("offer_number")
            or briefing.get("message_id")
            or "deal-unknown"
        )
        base_key = _briefing_event_key(summary, briefing)
        event_types = _event_types(briefing)
        if not event_types:
            continue
        action = "Oferta gotowa" if "final offer draft ready" in event_types else "Wymaga sprawdzenia"
        if "automatic pre-offer reply" in event_types and len(event_types) == 1:
            action = "Odpowiedź wysłana"
        event_key = hashlib.sha256(f"{base_key}|{action}".encode("utf-8")).hexdigest()
        subject = f"Orchesta RFQ | {case_id} | {action}"
        text = "\n".join([
            "Cześć Łukasz,",
            "",
            f"Sprawa: {case_id}",
            prose_for_briefing(briefing),
            "",
            "Hermes / Orchesta RFQ",
        ])
        result.append((event_key, briefing, subject, text))
    return result


def build_notification(
    summary: dict[str, Any],
    selected_briefings: list[dict[str, Any]] | None = None,
) -> tuple[str, str] | None:
    """Compatibility helper returning the first independently deliverable event."""
    built = build_notifications(summary, selected_briefings)
    return (built[0][2], built[0][3]) if built else None


def build_email_payload(*, from_address: str, recipient: str, subject: str, text: str) -> dict[str, str]:
    """Build a fresh-message payload with no reply/thread headers."""
    return {
        "mode": "send",
        "fromAddress": from_address,
        "toAddress": recipient,
        "subject": subject,
        "content": html.escape(text).replace("\n", "<br>"),
        "mailFormat": "html",
    }


def send_email(*, token_file: str, env_file: str, recipient: str, subject: str, text: str) -> tuple[int, dict[str, Any]]:
    try:
        client = HttpZohoClient(token_file, env_file)
        poster = HttpDraftPoster(token_file)
        accounts = client.list_accounts()
        configured_account = str(os.environ.get("ZOHO_MAIL_ACCOUNT_EMAIL") or "").strip()
        if not configured_account:
            raise RuntimeError("zoho_account_resolution_failed:configuration_missing")
        account = select_account(accounts, configured_account)
        account_id = str(account["accountId"])
        from_addr = str(account.get("primaryEmailAddress") or "rfq-mailbox@example.invalid")
        payload = build_email_payload(
            from_address=from_addr,
            recipient=recipient,
            subject=subject,
            text=text,
        )
        url = f"{poster.api_base}/accounts/{account_id}/messages"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Zoho-oauthtoken {poster._access_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
    except Exception as exc:
        raise DeliveryNotAttemptedError(exc.__class__.__name__) from exc

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"error": redact(raw[:500])}
    except Exception as exc:
        raise DeliveryOutcomeUnknownError(exc.__class__.__name__) from exc


def send_telegram_cli(*, subject: str, text: str, hermes_bin: str = DEFAULT_HERMES_BIN) -> tuple[int, dict[str, Any]]:
    """Send one fresh internal event through Hermes' configured Telegram target."""
    executable = Path(hermes_bin)
    if not executable.is_file():
        raise DeliveryNotAttemptedError("hermes_send_unavailable")
    try:
        completed = subprocess.run(
            [str(executable), "send", "--to", "telegram", "--json", "--subject", subject, "--file", "-"],
            input=text,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise DeliveryOutcomeUnknownError("telegram_send_timeout") from exc
    try:
        response = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        response = {"error": redact((completed.stderr or completed.stdout or "invalid_json")[:300])}
    if not isinstance(response, dict):
        response = {"error": "invalid_response"}
    return (200 if completed.returncode == 0 else 500), response


def _briefing_event_key(summary: dict[str, Any], briefing: dict[str, Any]) -> str:
    """Stable identity for one source event; deliberately excludes run_id."""
    source = str(summary.get("source") or "mailbox")
    draft = briefing.get("draft") if isinstance(briefing.get("draft"), dict) else {}
    response = briefing.get("response") if isinstance(briefing.get("response"), dict) else {}
    message = briefing.get("message") if isinstance(briefing.get("message"), dict) else {}
    final_offer = briefing.get("final_offer") if isinstance(briefing.get("final_offer"), dict) else {}
    sheet = briefing.get("sheet") if isinstance(briefing.get("sheet"), dict) else {}
    is_sheet_event = source.lower() in {"google_sheets", "google-sheets", "sheets"} or bool(sheet)
    if is_sheet_event:
        # A material row edit is a new source event. Sheets correlation_id
        # contains both the stable row number and the content/version digest.
        identifiers = [
            briefing.get("correlation_id"),
            briefing.get("message_id"),
            draft.get("source_message_id"),
            response.get("external_message_id"),
            message.get("external_message_id"),
        ]
    else:
        # One customer thread may contain several independent inbound events.
        identifiers = [
            briefing.get("task_id"),
            briefing.get("message_id"),
            draft.get("source_message_id"),
            response.get("external_message_id"),
            message.get("external_message_id"),
            draft.get("external_draft_id"),
            final_offer.get("offer_number"),
            briefing.get("correlation_id"),
        ]
    identity = next((str(value).strip() for value in identifiers if str(value or "").strip()), "")
    if not identity:
        sender = briefing.get("sender") if isinstance(briefing.get("sender"), dict) else {}
        identity_payload = {
            "classification": briefing.get("classification") or briefing.get("class"),
            "sender": sender.get("email"),
            "sheet_row": sheet.get("row"),
            "draft_action": draft.get("action"),
            "next_step": briefing.get("next_step"),
        }
        identity = json.dumps(identity_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{source}|{identity}".encode("utf-8")).hexdigest()


def append_durable_review_tasks(summary: dict[str, Any], tasks: list[dict[str, Any]]) -> dict[str, Any]:
    """Project the durable review queue into notifier input; tasks remain truth."""
    enriched = dict(summary)
    briefings = [item for item in list(summary.get("briefings") or []) if isinstance(item, dict)]
    represented = {str(item.get("task_id") or "") for item in briefings}
    represented_deals = {str(item.get("deal_id") or "") for item in briefings if str(item.get("deal_id") or "")}
    represented_messages = {str(item.get("message_id") or "") for item in briefings if str(item.get("message_id") or "")}
    for task in tasks:
        task_id = str(task.get("task_id") or "")
        task_deal = str(task.get("deal_id") or "")
        task_source = str(task.get("source_key") or "")
        source_is_current_message = any(
            task_source == message_id or task_source.endswith(f":{message_id}")
            for message_id in represented_messages
        )
        if (
            not task_id
            or task_id in represented
            or (task_deal and task_deal in represented_deals)
            or source_is_current_message
        ):
            continue
        reasons = list(task.get("reason_codes") or task.get("validation_errors") or [])
        briefings.append({
            "task_id": task_id,
            "deal_id": str(task.get("deal_id") or ""),
            "correlation_id": str(task.get("operation_id") or task_id),
            "classification": "human_review_only",
            "routing_action": "review",
            "reason_code": ",".join(str(reason) for reason in reasons),
            "sender": {"email": str(task.get("recipient") or ""), "relationship": "known"},
            "response": {"action": "blocked", "error": ",".join(str(reason) for reason in reasons)},
            "next_step": "Otwórz sprawę, przeczytaj wiadomość i zdecyduj o dalszej odpowiedzi.",
        })
    enriched["briefings"] = briefings
    return enriched


def review_tasks_for_summary(summary: dict[str, Any], tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return only pending review tasks represented by the current poll summary.

    A normal no-op poll must never drain the historical review queue. The
    original summary (or its pending retry file) remains the notification
    trigger; the durable task only confirms which current case may be marked
    as notified after provider delivery.
    """
    briefings = [item for item in list(summary.get("briefings") or []) if isinstance(item, dict)]
    if not briefings:
        return []
    task_ids = {str(item.get("task_id") or "") for item in briefings if str(item.get("task_id") or "")}
    deal_ids = {str(item.get("deal_id") or "") for item in briefings if str(item.get("deal_id") or "")}
    operation_ids = {
        str(item.get("correlation_id") or "")
        for item in briefings
        if str(item.get("correlation_id") or "")
    }
    message_ids: set[str] = set()
    for item in briefings:
        draft = item.get("draft") if isinstance(item.get("draft"), dict) else {}
        for value in (item.get("message_id"), draft.get("source_message_id")):
            if str(value or "").strip():
                message_ids.add(str(value).strip())

    selected: list[dict[str, Any]] = []
    for task in tasks:
        if str(task.get("notification_status") or "pending") != "pending":
            continue
        source_key = str(task.get("source_key") or "")
        if (
            str(task.get("task_id") or "") in task_ids
            or str(task.get("deal_id") or "") in deal_ids
            or str(task.get("operation_id") or "") in operation_ids
            or any(source_key == message_id or source_key.endswith(f":{message_id}") for message_id in message_ids)
        ):
            selected.append(task)
    return selected


def _notification_key(event_keys: list[str], subject: str) -> str:
    payload = {"event_keys": sorted(set(event_keys)), "subject": subject}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _empty_delivery_state() -> dict[str, Any]:
    return {"version": 3, "delivered": {}, "events": {}, "telegram_delivered": {}, "telegram_events": {}}


def _load_delivery_state(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return _empty_delivery_state()
    if not isinstance(loaded, dict):
        return _empty_delivery_state()
    if not isinstance(loaded.get("delivered"), dict):
        loaded["delivered"] = {}
    if not isinstance(loaded.get("events"), dict):
        loaded["events"] = {}
    if not isinstance(loaded.get("telegram_delivered"), dict):
        loaded["telegram_delivered"] = {}
    if not isinstance(loaded.get("telegram_events"), dict):
        loaded["telegram_events"] = {}
    loaded["version"] = 3
    return loaded


def _save_delivery_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.chmod(0o600)
    os.replace(tmp, path)
    path.chmod(0o600)


@contextmanager
def _delivery_lock(path: Path):
    """Serialize the shared mail/Sheets ledger across both poller processes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f"{path.name}.lock")
    with lock_path.open("a+", encoding="utf-8") as handle:
        lock_path.chmod(0o600)
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def deliver_notification(
    summary: dict[str, Any],
    *,
    token_file: str,
    env_file: str,
    recipient: str,
    state_file: Path | str = DEFAULT_DELIVERY_STATE_FILE,
    sender: Any = send_email,
) -> dict[str, Any]:
    """Deliver each source event as its own fresh internal email."""
    recipient = validate_internal_recipient(recipient)
    events = build_notifications(summary)
    if not events:
        return {"status": "skipped", "reason": "nothing_interesting"}

    path = Path(state_file)
    with _delivery_lock(path):
        state = _load_delivery_state(path)
        pending: list[tuple[str, dict[str, Any], str, str]] = []
        uncertain: list[str] = []
        for event in events:
            event_key = event[0]
            event_state = state["events"].get(event_key) if isinstance(state["events"].get(event_key), dict) else {}
            status = str(event_state.get("status") or "")
            if status == "delivered":
                continue
            if status == "in_progress":
                uncertain.append(event_key)
                continue
            pending.append(event)

        if not pending:
            if uncertain:
                return {
                    "status": "outcome_unknown",
                    "reason": "prior_delivery_in_progress_not_retried",
                    "event_keys": uncertain,
                    "recipient": recipient,
                }
            return {
                "status": "already_delivered",
                "event_keys": [event[0] for event in events],
                "recipient": recipient,
            }

        sent_keys: list[str] = []
        failed: list[dict[str, Any]] = []
        for event_key, _briefing, subject, body in pending:
            notification_key = _notification_key([event_key], subject)
            started_at = datetime.now(timezone.utc).isoformat()
            previous = state["events"].get(event_key) if isinstance(state["events"].get(event_key), dict) else {}
            state["events"][event_key] = {
                "status": "in_progress",
                "recipient": recipient,
                "started_at": started_at,
                "attempts": int(previous.get("attempts") or 0) + 1,
                "notification_key": notification_key,
                "subject": subject,
            }
            _save_delivery_state(path, state)
            try:
                status_code, response = sender(
                    token_file=token_file,
                    env_file=env_file,
                    recipient=recipient,
                    subject=subject,
                    text=body,
                )
            except DeliveryNotAttemptedError as exc:
                state["events"][event_key].update({
                    "status": "failed",
                    "failed_at": datetime.now(timezone.utc).isoformat(),
                    "status_code": 0,
                    "error": "delivery_not_attempted",
                })
                _save_delivery_state(path, state)
                failed.append({"event_key": event_key, "status_code": 0, "error": redact(str(exc)[:100])})
                continue
            except Exception as exc:
                _save_delivery_state(path, state)
                return {
                    "status": "outcome_unknown",
                    "event_keys": [event_key],
                    "sent_event_keys": sent_keys,
                    "recipient": recipient,
                    "error": redact(str(exc)[:300]),
                }

            if status_code in (None, 0, 599):
                return {
                    "status": "outcome_unknown",
                    "event_keys": [event_key],
                    "sent_event_keys": sent_keys,
                    "recipient": recipient,
                    "status_code": status_code,
                    "response": response,
                }
            if status_code not in (200, 201, 202):
                state["events"][event_key].update({
                    "status": "failed",
                    "failed_at": datetime.now(timezone.utc).isoformat(),
                    "status_code": status_code,
                })
                _save_delivery_state(path, state)
                failed.append({"event_key": event_key, "status_code": status_code, "response": response})
                continue

            response_data = response.get("data") if isinstance(response, dict) else {}
            if not isinstance(response_data, dict):
                response_data = {}
            external_message_id = str(response_data.get("messageId") or "")
            if not external_message_id:
                _save_delivery_state(path, state)
                return {
                    "status": "outcome_unknown",
                    "event_keys": [event_key],
                    "sent_event_keys": sent_keys,
                    "recipient": recipient,
                    "status_code": status_code,
                    "response": {"error": "provider_message_id_missing"},
                }
            delivered_at = datetime.now(timezone.utc).isoformat()
            state["events"][event_key].update({
                "status": "delivered",
                "delivered_at": delivered_at,
                "external_message_id": external_message_id,
            })
            state["delivered"][notification_key] = {
                "recipient": recipient,
                "delivered_at": delivered_at,
                "external_message_id": external_message_id,
                "event_keys": [event_key],
            }
            _save_delivery_state(path, state)
            sent_keys.append(event_key)

        if failed:
            return {
                "status": "failed",
                "event_keys": [item["event_key"] for item in failed],
                "sent_event_keys": sent_keys,
                "recipient": recipient,
                "status_code": failed[0].get("status_code"),
                "response": failed[0],
            }
        return {
            "status": "sent",
            "event_keys": sent_keys,
            "recipient": recipient,
            "sent_count": len(sent_keys),
            "status_code": 202,
        }


def _telegram_message_id(response: dict[str, Any]) -> str:
    for key in ("message_id", "messageId"):
        if str(response.get(key) or "").strip():
            return str(response[key])
    for value in response.values():
        if isinstance(value, dict):
            found = _telegram_message_id(value)
            if found:
                return found
    return ""


def deliver_telegram_notifications(
    summary: dict[str, Any],
    *,
    state_file: Path | str = DEFAULT_DELIVERY_STATE_FILE,
    sender: Any = send_telegram_cli,
) -> dict[str, Any]:
    """Deliver every notifiable event as a separate, exactly-once Telegram message."""
    events = build_notifications(summary)
    if not events:
        return {"status": "skipped", "reason": "nothing_interesting"}

    path = Path(state_file)
    with _delivery_lock(path):
        state = _load_delivery_state(path)
        pending: list[tuple[str, dict[str, Any], str, str]] = []
        uncertain: list[str] = []
        for event in events:
            event_key = event[0]
            event_state = state["telegram_events"].get(event_key)
            event_state = event_state if isinstance(event_state, dict) else {}
            status = str(event_state.get("status") or "")
            if status == "delivered":
                continue
            if status == "in_progress":
                uncertain.append(event_key)
                continue
            pending.append(event)
        if not pending:
            if uncertain:
                return {"status": "outcome_unknown", "event_keys": uncertain}
            return {"status": "already_delivered", "event_keys": [event[0] for event in events]}

        sent: list[dict[str, str]] = []
        failed: list[dict[str, Any]] = []
        for event_key, _briefing, subject, body in pending:
            previous = state["telegram_events"].get(event_key)
            previous = previous if isinstance(previous, dict) else {}
            state["telegram_events"][event_key] = {
                "status": "in_progress",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "attempts": int(previous.get("attempts") or 0) + 1,
                "subject": subject,
            }
            _save_delivery_state(path, state)
            try:
                status_code, response = sender(subject=subject, text=body)
            except DeliveryNotAttemptedError as exc:
                state["telegram_events"][event_key].update({
                    "status": "failed",
                    "failed_at": datetime.now(timezone.utc).isoformat(),
                    "error": redact(str(exc)[:100]),
                })
                _save_delivery_state(path, state)
                failed.append({"event_key": event_key, "status_code": 0})
                continue
            except Exception as exc:
                _save_delivery_state(path, state)
                return {
                    "status": "outcome_unknown",
                    "event_keys": [event_key],
                    "sent": sent,
                    "error": redact(str(exc)[:200]),
                }
            message_id = _telegram_message_id(response)
            if status_code not in (200, 201, 202) or not message_id:
                state["telegram_events"][event_key].update({
                    "status": "failed",
                    "failed_at": datetime.now(timezone.utc).isoformat(),
                    "status_code": status_code,
                    "error": "provider_message_id_missing" if status_code in (200, 201, 202) else "provider_rejected",
                })
                _save_delivery_state(path, state)
                failed.append({"event_key": event_key, "status_code": status_code})
                continue
            delivered_at = datetime.now(timezone.utc).isoformat()
            state["telegram_events"][event_key].update({
                "status": "delivered",
                "delivered_at": delivered_at,
                "external_message_id": message_id,
            })
            state["telegram_delivered"][event_key] = {
                "delivered_at": delivered_at,
                "external_message_id": message_id,
                "subject": subject,
            }
            _save_delivery_state(path, state)
            sent.append({"event_key": event_key, "external_message_id": message_id})
        if failed:
            return {"status": "failed", "failed": failed, "sent": sent}
        return {"status": "sent", "sent": sent, "sent_count": len(sent)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("summary_json")
    parser.add_argument("--token-file", default=DEFAULT_TOKEN_FILE)
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE)
    parser.add_argument("--recipient", default=DEFAULT_RECIPIENT)
    parser.add_argument("--state-file", default=DEFAULT_DELIVERY_STATE_FILE)
    parser.add_argument("--registry-file", default=DEFAULT_REGISTRY_FILE)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    recipient = validate_internal_recipient(args.recipient)
    path = Path(args.summary_json)
    summary = json.loads(path.read_text(encoding="utf-8"))
    registry: UnifiedLeadRegistry | None = None
    review_tasks: list[dict[str, Any]] = []
    try:
        registry = UnifiedLeadRegistry(args.registry_file)
        review_tasks = review_tasks_for_summary(summary, registry.review_tasks())
    except Exception:
        if registry is not None:
            registry.close()
        registry = None
    built_events = build_notifications(summary)
    if not built_events:
        if registry is not None:
            registry.close()
        return 0
    if args.dry_run:
        print(json.dumps([
            {"subject": subject, "recipient": recipient, "text": text}
            for _event_key, _briefing, subject, text in built_events
        ], ensure_ascii=False, indent=2))
        if registry is not None:
            registry.close()
        return 0
    email_result = deliver_notification(
        summary,
        token_file=args.token_file,
        env_file=args.env_file,
        recipient=recipient,
        state_file=args.state_file,
    )
    telegram_result = deliver_telegram_notifications(summary, state_file=args.state_file)
    failed = False
    if email_result["status"] == "failed":
        print(f"Orchesta RFQ email notify: BŁĄD status={email_result.get('status_code')}")
        print(redact(json.dumps(email_result.get("response") or {}, ensure_ascii=False)[:1000]))
        failed = True
    elif email_result["status"] == "already_delivered":
        print(f"Orchesta RFQ email notify: pominięto duplikat do {recipient}")
    elif email_result["status"] == "outcome_unknown":
        print("Orchesta RFQ email notify: wynik wcześniejszej próby jest niepewny; nie ponowiono wysyłki, aby uniknąć duplikatu")
        failed = True
    elif email_result["status"] == "sent":
        print(f"Orchesta RFQ email notify: wysłano powiadomienie do {recipient}")
    if telegram_result["status"] == "failed":
        print("Orchesta RFQ Telegram notify: BŁĄD dostarczenia")
        failed = True
    elif telegram_result["status"] == "outcome_unknown":
        print("Orchesta RFQ Telegram notify: wynik wcześniejszej próby jest niepewny; nie ponowiono wysyłki")
        failed = True
    elif telegram_result["status"] == "already_delivered":
        print("Orchesta RFQ Telegram notify: pominięto duplikat")
    elif telegram_result["status"] == "sent":
        print(f"Orchesta RFQ Telegram notify: wysłano {telegram_result.get('sent_count', 0)} osobnych powiadomień")
    if registry is not None:
        if email_result.get("status") in {"sent", "already_delivered"} or telegram_result.get("status") in {"sent", "already_delivered"}:
            for task in review_tasks:
                registry.mark_review_task_notified(
                    str(task.get("task_id") or ""),
                    channel="internal_notifier",
                    provider_evidence={
                        "email_status": email_result.get("status"),
                        "telegram_status": telegram_result.get("status"),
                    },
                )
        registry.close()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
