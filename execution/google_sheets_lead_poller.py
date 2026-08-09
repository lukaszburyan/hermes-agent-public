#!/usr/bin/env python3
"""Google Sheets lead poller for Orchesta RFQ.

Reads campaign leads from a configured Google Sheet, classifies each new row with
the existing Orchesta RFQ deterministic classifier, writes Hermes status columns
back to the sheet, sends high-confidence pre-offer Zoho messages and emits the
same internal-notification JSON shape as the mailbox poller. Prices, offers and
attachments are rejected by the transport; final offers are handled only as
mailbox drafts with PDF by the final-offer stage.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

EXECUTION_DIR = Path(__file__).resolve().parent
ROOT_DIR = EXECUTION_DIR.parent
if str(EXECUTION_DIR) not in sys.path:
    sys.path.insert(0, str(EXECUTION_DIR))

try:
    from google.oauth2.credentials import Credentials  # type: ignore
    from google.oauth2 import service_account as sa_module  # type: ignore
    from google.auth.transport.requests import Request  # type: ignore
    from googleapiclient.discovery import build  # type: ignore
except ImportError as exc:  # pragma: no cover - exercised in lightweight tests without Google deps
    Credentials = None  # type: ignore[assignment]
    sa_module = None  # type: ignore[assignment]
    Request = None  # type: ignore[assignment]
    build = None  # type: ignore[assignment]
    GOOGLE_IMPORT_ERROR = exc
else:
    GOOGLE_IMPORT_ERROR = None

from unified_lead_registry import STATUS_TO_SHEET, UnifiedLeadRegistry, normalize_email  # noqa: E402
import tenant_config  # noqa: E402
from message_policy import operational_autosend_enabled, operational_type_for_flow  # noqa: E402
from zoho_mail_poller import (  # noqa: E402
    HttpZohoClient,
    discovery_questions_for_message,
    extract_crm_decision,
    extract_has_sample_requests,
    extract_inquiry_source,
    extract_mailbox_count,
    find_sent_folder,
    folder_id_of,
    load_classifier,
    lookup_rfc_message_id,
    known_offer_facts,
    normalize_envelope,
    orchesta_fit,
    relationship,
    select_account,
    sent_message_recipients,
)
from restore_gate import assert_restore_reconciled  # noqa: E402
from send_reconciliation import append_marker as append_customer_send_marker, reconcile_sent_rows  # noqa: E402
from zoho_pre_offer_send import (  # noqa: E402
    APPROVAL_PHRASE as PRE_OFFER_SEND_APPROVAL,
    HttpPreOfferPoster,
    build_pre_offer_payload,
    create_pre_offer_message,
)
from zoho_reply_draft import (  # noqa: E402
    APPROVAL_PHRASE as DRAFT_APPROVAL,
    build_draft_payload,
    create_reply_draft,
    ensure_campaign_origin_context,
    generate_draft_body_from_env,
    validate_generated_body,
)

# Rebuilt LLM pipeline (spec sections 9-17). Imports are guarded so the poller
# still runs when the LLM modules are absent or disabled (kill-switches off).
try:
    import llm_intent_classifier as _lic  # noqa: E402
    import llm_reply_writer as _lrw   # noqa: E402
    import reply_validation as _rv  # noqa: E402
    import conversation_continuity as _cc   # noqa: E402
    from hermes_rfq_core import SafetySwitches as _SafetySwitches  # noqa: E402
except Exception as _llm_import_exc:  # pragma: no cover
    _lic = None  # type: ignore[assignment]
    _lrw = None  # type: ignore[assignment]
    _rv = None  # type: ignore[assignment]
    _cc = None  # type: ignore[assignment]
    _SafetySwitches = None  # type: ignore[assignment]
    _LLM_IMPORT_ERROR = _llm_import_exc
else:
    _LLM_IMPORT_ERROR = None


class ReplyValidationError(RuntimeError):
    """Raised when an LLM-generated reply fails script-side validation (spec 17).

    The poller catches this and routes the deal to ``awaiting_human`` /
    ``reply_validation_failed`` instead of auto-sending.
    """


def _llm_enabled() -> bool:
    """True only when the rebuilt LLM modules imported AND the deploy flag is on."""
    return _lic is not None and _lic.is_enabled()


def _get_llm_client():
    """Return the GatewayLLMClient (codex/GPT-Plus). Never raises; on missing
    codex the subprocess returns empty stdout and the classifier routes to
    ``awaiting_human`` (safe graceful degradation, Guardrail 4)."""
    if _lic is None:
        return None
    try:
        return _lic.GatewayLLMClient()
    except Exception:
        return None


def _default_tenant_id() -> str:
    return os.environ.get("HERMES_DEFAULT_TENANT", "orchesta").strip() or "orchesta"


def _llm_shadow_classify(
    lead: dict[str, str],
    result: dict[str, Any],
    *,
    tenant_id: str,
    run_id: str,
) -> dict[str, Any] | None:
    """Run the LLM intent classifier in shadow alongside the deterministic one.

    Always returns a dict (success or failed outcome) for shadow logging; never
    raises. The deterministic ``result`` still drives the flow (spec section 25,
    E1 shadow mode).
    """
    if not _llm_enabled():
        return None
    try:
        deterministic_risk = str(result.get("risk") or "low").strip().lower()
        classification = str(result.get("classification") or "")
        if classification == "human_review_only" or (
            classification == "new_quote_request" and not draftable_from_sheet(result, lead)
        ):
            deterministic_risk = "high"
        elif classification in REVIEW_CLASSES:
            deterministic_risk = "medium"
        return _lic.classify(
            subject=f"Lead z Google Sheets — {lead.get('Firma') or lead.get('Imię') or lead.get('Email')}",
            body=synthesize_body(lead),
            sender=lead.get("Email", ""),
            recipient="",
            safety_check={"script_risk": deterministic_risk},
            tenant_id=tenant_id,
            llm_client=_get_llm_client(),
            run_id=run_id,
        )
    except Exception as exc:  # pragma: no cover - defensive
        return {**_lic.classification_failed_outcome(run_id=run_id), "error": str(exc)[:200]}


def make_llm_body_generator(
    *,
    lead: dict[str, str],
    result: dict[str, Any],
    tenant_id: str,
    run_id: str,
    is_first_agent_reply: bool = True,
):
    """Build a ``body_generator`` for ``send_sheet_zoho_response`` that authors the
    reply with the LLM reply writer and validates it with the script-side checks.

    On any validation failure raises ``ReplyValidationError`` so the caller routes
    the deal to ``awaiting_human`` (spec section 17). Only invoked when the
    kill-switch ``auto_reply_allowed`` is on (spec section 25, E2).
    """
    saved_data = facts_from_lead(lead)
    missing_data = list(
        dict.fromkeys(
            tenant_config.missing_fields(tenant_id, saved_data, stage="qualification")
            + tenant_config.missing_fields(tenant_id, saved_data, stage="final_offer")
        )
    )

    def _gen(context: dict[str, Any]) -> dict[str, Any]:
        if not _llm_enabled() or _lrw is None or _rv is None:
            raise ReplyValidationError("llm_disabled")
        from reply_contract import REPLY_CONTRACT_DIGEST
        from reply_sanitizer import sanitize_reply
        registry = context.get("registry")
        operation_id = str(context.get("operation_id") or f"response:{context.get('source_key') or run_id}")
        deal_id = str(context.get("deal_id") or "")
        last_error = "llm_write_failed"
        previous_body = ""
        previous_errors: list[str] = []
        previous_spans: list[dict[str, Any]] = []
        for attempt_number in (1, 2):
            outcome = _lrw.write_reply(
                approved_action="ask_discovery_questions",
                customer_message=synthesize_body(lead),
                thread_history="",
                saved_data=saved_data,
                missing_data=missing_data,
                conversation_stage="discovery",
                tenant_id=tenant_id,
                is_first_agent_reply=is_first_agent_reply,
                llm_client=_get_llm_client(),
                run_id=run_id,
                repair_context=(
                    {
                        "previous_body": previous_body,
                        "validation_error_codes": previous_errors,
                        "offending_spans": previous_spans,
                    }
                    if attempt_number == 2 else None
                ),
            )
            raw_body = ensure_campaign_origin_context(str(outcome.get("body") or "").strip())
            sanitized = sanitize_reply(raw_body, is_first_agent_reply=is_first_agent_reply)
            body = str(sanitized.get("body") or "").strip()
            if not body or outcome.get("needs_human_review"):
                last_error = str(outcome.get("review_reason") or "llm_write_failed")
                previous_errors = [last_error]
                continue
            verdict = _rv.validate_reply(
                body=body,
                approved_action="ask_discovery_questions",
                conversation_state={"is_first_agent_reply": is_first_agent_reply, "acknowledgement_already_sent": False},
                saved_data=saved_data,
                tenant_id=tenant_id,
                missing_data=missing_data,
            )
            if not verdict.get("ok"):
                previous_body = body
                previous_errors = list(verdict.get("errors") or ["validation_failed"])
                previous_spans = list(sanitized.get("offending_spans") or [])
                last_error = ",".join(previous_errors)
                if registry is not None and deal_id:
                    registry.record_validation_attempt(
                        operation_id, deal_id=deal_id, attempt_number=attempt_number,
                        prompt_version=str(outcome.get("prompt_version") or ""),
                        contract_digest=REPLY_CONTRACT_DIGEST, body=body, error_codes=previous_errors,
                        offending_spans=previous_spans,
                        sanitizer_actions=list(sanitized.get("actions") or []),
                    )
                continue
            return {
                "body_text": body,
                "generator": "llm_reply_writer",
                "model": "gpt-plus",
                "prompt_version": outcome.get("prompt_version", ""),
                "questions": [
                    tenant_config.discovery_questions(tenant_id, field)
                    for field in missing_data[:2]
                    if tenant_config.discovery_questions(tenant_id, field)
                ],
                "assumptions": [],
                "safety_notes": [
                    f"sanitized:{','.join(sanitized.get('actions') or []) or 'none'}",
                    f"missing:{','.join(missing_data) or 'none'}",
                ],
                "validated": True,
            }
        if registry is not None and deal_id:
            registry.finalize_response_attempt(
                operation_id, outcome="validation_failed",
                reason_codes=[f"reply_validation_failed_after_retry:{last_error}"],
                rejected_body=previous_body, validation_errors=previous_errors or [last_error],
            )
        raise ReplyValidationError(f"reply_validation_failed_after_retry:{last_error}")

    return _gen


# Deliberately empty: a deployment must provide its own sheet ID via CLI/env.
DEFAULT_SPREADSHEET_ID = ""
DEFAULT_SHEET_NAME = "Arkusz1"
DEFAULT_TOKEN_FILE = "/opt/data/google_token.json"
DEFAULT_STATE_FILE = "/opt/data/.tmp/google-sheets-leads-state.json"
DEFAULT_REGISTRY_FILE = "/opt/data/.tmp/orchesta-rfq-unified.sqlite3"
DEFAULT_ZOHO_TOKEN_FILE = "/opt/data/.tmp/zoho_mail_tokens.json"
DEFAULT_ENV_FILE = "/opt/data/.env"
DEFAULT_RANGE = "A1:Z1000"

BASE_HEADERS = ["Data", "Imię", "Email", "Telefon", "Firma", "Wiadomość", "Źródło"]
HERMES_HEADERS = [
    "Hermes status",
    "Hermes klasyfikacja",
    "Hermes uwagi",
    "Hermes sprawdzono",
    "Hermes hash",
    "Hermes draft id",
    "Hermes sent id",
    "Hermes source id",
]
REQUIRED_HEADERS = BASE_HEADERS + HERMES_HEADERS

GOOD_DRAFT_CLASSES = {"new_quote_request", "quote_draft_ready", "new_general_business_inquiry"}
REVIEW_CLASSES = {"unknown_review_needed", "related_non_rfq_topic", "human_review_only", "weak_fit_review_only"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def col_letter(index_1_based: int) -> str:
    result = ""
    n = index_1_based
    while n:
        n, rem = divmod(n - 1, 26)
        result = chr(65 + rem) + result
    return result


def normalize_header(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def load_state(path: str) -> dict[str, Any]:
    p = Path(path)
    backup = p.with_name(p.name + ".previous")

    def validated(candidate: Path) -> dict[str, Any]:
        data = json.loads(candidate.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("rows", {}), dict):
            raise ValueError("google_sheets_state_schema_invalid")
        data.setdefault("rows", {})
        return data

    if not p.exists() and not backup.exists():
        return {"rows": {}}
    if p.exists():
        try:
            return validated(p)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            quarantine = p.with_name(f"{p.name}.corrupt-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}")
            try:
                os.replace(p, quarantine)
            except OSError as move_exc:
                raise RuntimeError("google_sheets_state_corrupt_quarantine_failed") from move_exc
            if backup.exists():
                try:
                    recovered = validated(backup)
                except (OSError, ValueError, json.JSONDecodeError) as backup_exc:
                    raise RuntimeError("google_sheets_state_and_backup_corrupt") from backup_exc
                recovered["_hermes_state_alert"] = {
                    "reason": "primary_state_corrupt_recovered_from_previous",
                    "quarantine": str(quarantine),
                    "error": exc.__class__.__name__,
                }
                return recovered
            raise RuntimeError("google_sheets_state_corrupt_without_safe_backup") from exc
    recovered = validated(backup)
    recovered["_hermes_state_alert"] = {"reason": "primary_state_missing_recovered_from_previous"}
    return recovered


def save_state(path: str, state: dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state, ensure_ascii=False, indent=2) + "\n"
    parsed = json.loads(payload)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("rows", {}), dict):
        raise ValueError("google_sheets_state_schema_invalid")
    lock_path = p.with_name(p.name + ".lock")
    backup = p.with_name(p.name + ".previous")
    with lock_path.open("a+b") as lock_file:
        os.chmod(lock_path, 0o600)
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        temp_name = ""
        backup_temp_name = ""
        try:
            if p.exists():
                current = p.read_bytes()
                current_parsed = json.loads(current.decode("utf-8"))
                if not isinstance(current_parsed, dict) or not isinstance(current_parsed.get("rows", {}), dict):
                    raise RuntimeError("refusing_to_overwrite_corrupt_google_sheets_state")
                with tempfile.NamedTemporaryFile(
                    mode="wb", dir=p.parent, prefix=f".{p.name}.previous.", delete=False
                ) as backup_temp:
                    backup_temp_name = backup_temp.name
                    backup_temp.write(current)
                    backup_temp.flush()
                    os.fsync(backup_temp.fileno())
                os.chmod(backup_temp_name, 0o600)
                os.replace(backup_temp_name, backup)
                backup_temp_name = ""
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=p.parent, prefix=f".{p.name}.", delete=False
            ) as temp_file:
                temp_name = temp_file.name
                temp_file.write(payload)
                temp_file.flush()
                os.fsync(temp_file.fileno())
            json.loads(Path(temp_name).read_text(encoding="utf-8"))
            os.chmod(temp_name, 0o600)
            os.replace(temp_name, p)
            temp_name = ""
            directory_fd = os.open(p.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            # Failed temp files are retained as evidence and never treated as state.
            if temp_name:
                failed = Path(temp_name).with_name(Path(temp_name).name + ".failed")
                try:
                    os.replace(temp_name, failed)
                except OSError:
                    pass
            if backup_temp_name:
                failed = Path(backup_temp_name).with_name(Path(backup_temp_name).name + ".failed")
                try:
                    os.replace(backup_temp_name, failed)
                except OSError:
                    pass
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def load_google_services(token_file: str):
    if GOOGLE_IMPORT_ERROR is not None or Credentials is None or Request is None or build is None:
        raise RuntimeError(f"Google API dependencies unavailable: {GOOGLE_IMPORT_ERROR}")
    scopes = ["https://www.googleapis.com/auth/drive", "https://www.googleapis.com/auth/spreadsheets"]
    # Service-account path (spec deploy): when HERMES_GOOGLE_SA_FILE is set, authenticate
    # with the service account instead of an OAuth user token. The sheet must be
    # shared with the SA client_email. No refresh-token expiry.
    sa_file = os.environ.get("HERMES_GOOGLE_SA_FILE", "").strip()
    if sa_file and sa_module is not None and Path(sa_file).exists():
        creds = sa_module.Credentials.from_service_account_file(sa_file, scopes=scopes)
        creds.refresh(Request())
        return build("sheets", "v4", credentials=creds), build("drive", "v3", credentials=creds)
    creds = Credentials.from_authorized_user_file(token_file, scopes=scopes)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        Path(token_file).write_text(json.dumps(json.loads(creds.to_json()), indent=2), encoding="utf-8")
        os.chmod(token_file, 0o600)
    return build("sheets", "v4", credentials=creds), build("drive", "v3", credentials=creds)


def values_get(sheets, spreadsheet_id: str, sheet_name: str, a1_range: str) -> list[list[str]]:
    resp = sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!{a1_range}",
    ).execute()
    return resp.get("values", [])


def update_values(sheets, spreadsheet_id: str, sheet_name: str, cell_range: str, values: list[list[Any]]) -> dict[str, Any]:
    return sheets.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!{cell_range}",
        valueInputOption="USER_ENTERED",
        body={"values": values},
    ).execute()


def ensure_headers(sheets, spreadsheet_id: str, sheet_name: str, rows: list[list[str]], *, write: bool) -> tuple[list[str], dict[str, int], bool]:
    header = list(rows[0]) if rows else []
    changed = False
    if not header:
        header = list(REQUIRED_HEADERS)
        changed = True
    existing_norm = {normalize_header(h): i for i, h in enumerate(header)}
    for required in REQUIRED_HEADERS:
        if normalize_header(required) not in existing_norm:
            header.append(required)
            existing_norm[normalize_header(required)] = len(header) - 1
            changed = True
    if changed and write:
        end = col_letter(len(header))
        update_values(sheets, spreadsheet_id, sheet_name, f"A1:{end}1", [header])
    index = {str(h): i for i, h in enumerate(header)}
    return header, index, changed


def cell(row: list[str], header_index: dict[str, int], name: str) -> str:
    idx = header_index.get(name)
    if idx is None or idx >= len(row):
        return ""
    return str(row[idx]).strip()


def row_to_lead(row: list[str], header_index: dict[str, int]) -> dict[str, str]:
    return {name: cell(row, header_index, name) for name in BASE_HEADERS + HERMES_HEADERS}


def lead_hash(lead: dict[str, str]) -> str:
    payload = {key: lead.get(key, "") for key in BASE_HEADERS}
    return sha256_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def ensure_sheet_source_id(existing: str = "") -> str:
    value = str(existing or "").strip()
    return value or ("gsr-" + uuid.uuid4().hex)


def sheet_source_key(spreadsheet_id: str, logical_source_id: str) -> str:
    source_id = str(logical_source_id or "").strip()
    if not source_id:
        raise ValueError("logical_sheet_source_id_required")
    return f"sheet:{spreadsheet_id}:{source_id}"


def lead_has_content(lead: dict[str, str]) -> bool:
    return bool(lead.get("Email") or lead.get("Telefon") or lead.get("Firma") or lead.get("Wiadomość"))


def controlled_selection(args: argparse.Namespace) -> dict[str, Any] | None:
    values = {
        "row": getattr(args, "controlled_row_number", None),
        "email": str(getattr(args, "controlled_email", "") or "").strip(),
        "test_id": str(getattr(args, "controlled_test_id", "") or "").strip(),
        "expected_status": str(getattr(args, "controlled_expected_status", "") or "").strip(),
    }
    if not any(value not in (None, "") for value in values.values()):
        return None
    missing = [name for name, value in values.items() if value in (None, "")]
    if missing:
        raise RuntimeError(f"controlled_sheet_selection_incomplete:{','.join(missing)}")
    try:
        values["row"] = int(values["row"])
    except (TypeError, ValueError) as exc:
        raise RuntimeError("controlled_sheet_row_invalid") from exc
    if values["row"] < 2:
        raise RuntimeError("controlled_sheet_row_must_be_at_least_2")
    if values["expected_status"] == "__EMPTY__":
        values["expected_status"] = ""
    return values


def validate_controlled_row(
    rows: list[list[str]],
    header_index: dict[str, int],
    selection: dict[str, Any],
) -> dict[str, str]:
    row_number = int(selection["row"])
    if row_number > len(rows):
        raise RuntimeError("controlled_sheet_row_missing")
    lead = row_to_lead(rows[row_number - 1], header_index)
    if lead.get("Email", "").strip().casefold() != str(selection["email"]).casefold():
        raise RuntimeError("controlled_sheet_email_mismatch")
    if lead.get("Hermes status", "").strip() != str(selection["expected_status"]):
        raise RuntimeError("controlled_sheet_status_mismatch")
    message = lead.get("Wiadomość", "").strip()
    if not message.startswith("[TEST HERMES]"):
        raise RuntimeError("controlled_sheet_test_prefix_missing")
    if str(selection["test_id"]) not in synthesize_body(lead):
        raise RuntimeError("controlled_sheet_test_id_mismatch")
    return lead


def controlled_test_subject(lead: dict[str, str]) -> str:
    message = str(lead.get("Wiadomość") or "").strip()
    match = re.match(r"^\[TEST HERMES\]\s+([A-Za-z0-9._-]+)", message)
    if not match:
        return ""
    return f"[TEST HERMES] {match.group(1).rstrip('.')} | Orchesta RFQ"


def synthesize_body(lead: dict[str, str]) -> str:
    parts = []
    for key in ["Data", "Imię", "Email", "Telefon", "Firma", "Źródło", "Wiadomość"]:
        value = lead.get(key, "")
        if value:
            parts.append(f"{key}: {value}")
    return "\n".join(parts)


def facts_from_lead(lead: dict[str, str]) -> dict[str, Any]:
    text = synthesize_body(lead)
    facts: dict[str, Any] = {}
    company = str(lead.get("Firma") or "").strip()
    if company:
        facts["company_name_or_website"] = company
    extracted = {
        "mailbox_count": extract_mailbox_count(text),
        "crm": extract_crm_decision(text),
        "inquiry_source": extract_inquiry_source(text),
        "has_sample_requests": extract_has_sample_requests(text, []),
    }
    for key, value in extracted.items():
        if value is not None and value != "":
            facts[key] = value
    if facts.get("inquiry_source"):
        facts["inquiry_channels"] = facts["inquiry_source"]
    return facts


def classify_lead(classifier: Any, lead: dict[str, str], row_number: int, digest: str) -> dict[str, Any]:
    email = lead.get("Email") or "unknown-from-sheet@example.invalid"
    company = lead.get("Firma", "")
    subject = f"Lead z Google Sheets META ADS: {company or lead.get('Imię') or email}"
    body = synthesize_body(lead)
    case = {
        "id": f"sheets:{row_number}:{digest[:12]}",
        "message": {
            "from": email,
            "subject": subject,
            "body": body,
            "headers": {"Message-ID": f"<sheets-{row_number}-{digest[:12]}@hermes.local>"},
            "attachments": [],
        },
        "context": {"source_type": "google_sheets", "campaign_source": lead.get("Źródło", ""), "mac_bridge_available": False},
    }
    return classifier.classify(case)


def status_for(result: dict[str, Any]) -> str:
    classification = result.get("classification", "unknown_review_needed")
    confidence = result.get("confidence", "")
    if classification in GOOD_DRAFT_CLASSES and confidence == "high":
        return "rfq_candidate_review"
    if classification in REVIEW_CLASSES:
        return "needs_review"
    if classification == "newsletter_automated_spam":
        return "ignored_noise"
    return "classified_review"


def note_for(result: dict[str, Any], lead: dict[str, str]) -> str:
    classification = result.get("classification", "unknown_review_needed")
    confidence = result.get("confidence", "")
    if classification in GOOD_DRAFT_CLASSES and confidence == "high":
        return "Lead wygląda jak potencjalne zapytanie Orchesta RFQ. W trybie startowym nie tworzyłem jeszcze draftu Zoho; powiadomiłem Łukasza i oznaczyłem wiersz do review."
    if classification == "related_non_rfq_topic":
        return "Lead dotyczy tematu pobocznego wobec Orchesta RFQ; bez automatycznej odpowiedzi."
    if classification in {"human_review_only", "weak_fit_review_only"}:
        return "Lead wymaga ręcznego sprawdzenia ze względu na ryzyko, wrażliwość lub słaby fit; bez automatycznej odpowiedzi."
    if classification == "newsletter_automated_spam":
        return "Wpis wygląda na noise/newsletter; bez akcji."
    return "Lead jest niejednoznaczny; bez automatycznej odpowiedzi, wymaga decyzji Łukasza."


def draftable_from_sheet(result: dict[str, Any], lead: dict[str, str]) -> bool:
    email = (lead.get("Email") or "").strip()
    if "@" not in email or email.endswith(".invalid"):
        return False
    text = " ".join(str(lead.get(key, "") or "").lower() for key in BASE_HEADERS)
    explicit_non_rfq = any(
        marker in text
        for marker in (
            "bez wdrożenia orchesta rfq",
            "bez wdrozenia orchesta rfq",
            "bez orchesta rfq",
            "bez automatyzacji ofert",
            "bez automatyzacji zapytań ofertowych",
            "bez automatyzacji zapytan ofertowych",
            "tylko szkolenie",
            "chodzi tylko o szkolenie",
        )
    ) or ("warsztat" in text and "bez" in text and "rfq" in text)
    prompt_injection = any(
        marker in text
        for marker in (
            "zignoruj instrukcje",
            "zignoruj wszystkie instrukcje",
            "ujawnij token",
            "ujawnij tokeny",
            "tokeny api",
            "hasło",
            "haslo",
            "wiążącą ofertę natychmiast",
            "wiazaca oferte natychmiast",
            "wyślij wiążącą ofertę",
            "wyslij wiazaca oferte",
        )
    )
    if explicit_non_rfq or prompt_injection:
        return False
    return result.get("classification") == "new_quote_request" and result.get("confidence") == "high"


def extract_draft_id(response: dict[str, Any]) -> str:
    data = response.get("data") if isinstance(response, dict) else None
    if isinstance(data, list) and data:
        data = data[0]
    if isinstance(data, dict):
        for key in ("messageId", "draftId", "id"):
            if data.get(key):
                return str(data[key])
    for key in ("messageId", "draftId", "id"):
        if isinstance(response, dict) and response.get(key):
            return str(response[key])
    return ""


def send_sheet_zoho_response(
    *,
    lead: dict[str, str],
    result: dict[str, Any],
    row_number: int,
    digest: str,
    zoho_token_file: str,
    env_file: str,
    body_generator: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    durable_context: dict[str, Any] | None = None,
    message_type: str = "missing_data_request",
) -> dict[str, Any]:
    """Send a new pre-offer Zoho message using the employee-style composer."""
    client = HttpZohoClient(zoho_token_file, env_file)
    configured_account = str(os.environ.get("ZOHO_MAIL_ACCOUNT_EMAIL") or "").strip()
    if not configured_account:
        raise RuntimeError("zoho_account_resolution_failed:configuration_missing")
    account = select_account(client.list_accounts(), configured_account)
    account_id = str(account["accountId"])
    account_email = str(account.get("primaryEmailAddress") or "rfq-mailbox@example.invalid")
    body_text = synthesize_body(lead)
    envelope = {
        "from": lead.get("Email", ""),
        "from_raw": f"{lead.get('Imię', '')} <{lead.get('Email', '')}>",
        "subject": "Lead z Google Sheets",
        "domain": str(lead.get("Email") or "").rsplit("@", 1)[-1],
        "attachments": [],
    }
    questions = discovery_questions_for_message(body_text, envelope)
    context = {
        "sender": lead.get("Email", ""),
        "subject": f"Zapytanie o Orchesta RFQ — {lead.get('Firma') or lead.get('Imię') or lead.get('Email')}",
        "body": body_text,
        "classification": result.get("classification", "new_quote_request"),
        "confidence": result.get("confidence", ""),
        "draft_kind": "first_response",
        "source_type": "google_sheets",
        "company": lead.get("Firma", ""),
        "contact_name": lead.get("Imię", ""),
        "known_facts": known_offer_facts(body_text, envelope),
        "default_questions": questions,
        "attachment_routes": [],
        "attachment_summaries": [],
    }
    context.update(dict(durable_context or {}))
    generated = (body_generator or generate_draft_body_from_env)(context)
    generated_body_text = ensure_campaign_origin_context(str(generated.get("body_text") or ""))
    if generated.get("validated"):
        # LLM body already passed reply_validation; do not re-apply the
        # deterministic question whitelist (it rejects paraphrased discovery Qs).
        body = generated_body_text
    else:
        body = validate_generated_body(
            generated_body_text,
            has_attachments=False,
            approved_questions=questions,
        )
    payload = build_pre_offer_payload(
        account_email=account_email,
        to_address=lead.get("Email", ""),
        inbound_subject=f"Lead z Google Sheets META ADS — row {row_number}",
        body_text=body,
        message_kind=message_type,
        threaded=False,
        subject_override=controlled_test_subject(lead) or "Dziękuję za zgłoszenie dotyczące Orchesta RFQ",
        source_sender=f"google-sheets-row-{row_number}@hermes.local",
        route_final_offer_to_policy=True,
    )
    operation_id = str((durable_context or {}).get("operation_id") or "")
    if operation_id:
        payload = append_customer_send_marker(payload, operation_id)
    poster = HttpPreOfferPoster(zoho_token_file)

    def final_offer_draft_fallback(**_kwargs: Any) -> dict[str, Any]:
        draft_payload = build_draft_payload(
            account_email=account_email,
            to_address=lead.get("Email", ""),
            inbound_subject=f"Lead z Google Sheets META ADS — row {row_number}",
            rfc_message_id="",
            body_text=body,
            draft_kind="final_offer",
            threaded=False,
            subject_override=controlled_test_subject(lead) or "Oferta Orchesta RFQ — do weryfikacji",
            source_sender=account_email,
        )
        return create_reply_draft(
            account_id,
            draft_payload,
            poster=poster,
            approval=DRAFT_APPROVAL,
        )

    def blocked_operational_draft_fallback(**_kwargs: Any) -> dict[str, Any]:
        draft_payload = build_draft_payload(
            account_email=account_email,
            to_address=lead.get("Email", ""),
            inbound_subject=f"Lead z Google Sheets META ADS — row {row_number}",
            rfc_message_id="",
            body_text=body,
            draft_kind="first_response",
            threaded=False,
            subject_override=controlled_test_subject(lead) or "Dziękuję za zgłoszenie dotyczące Orchesta RFQ",
            source_sender=account_email,
        )
        return create_reply_draft(
            account_id,
            draft_payload,
            poster=poster,
            approval=DRAFT_APPROVAL,
        )

    sent = create_pre_offer_message(
        account_id,
        source_message_id="",
        payload=payload,
        poster=poster,
        approval=PRE_OFFER_SEND_APPROVAL,
        message_kind=message_type,
        threaded=False,
        durable_context=durable_context,
        draft_fallback=final_offer_draft_fallback,
        blocked_draft_fallback=blocked_operational_draft_fallback,
    )
    raw_response = sent.get("response")
    response: dict[str, Any] = raw_response if isinstance(raw_response, dict) else {}
    sent["sent_id"] = str(sent.get("external_message_id") or extract_draft_id(response))
    sent["account_id"] = account_id
    sent["thread_id"] = sent["sent_id"]
    sent["body_text"] = body
    sent["rfc_message_id"] = ""
    try:
        sent_folder = find_sent_folder(client.list_folders(account_id))
        if sent_folder and sent["sent_id"]:
            sent["rfc_message_id"] = lookup_rfc_message_id(
                client, account_id, folder_id_of(sent_folder), sent["sent_id"]
            )
    except Exception:
        # The Zoho thread binding and internal message id are already durable;
        # missing RFC headers remain a weaker, but still deterministic anchor.
        pass
    sent["draft_generation"] = {
        key: generated.get(key)
        for key in (
            "generator",
            "model",
            "salutation",
            "attachment_summary_used",
            "questions",
            "assumptions",
            "answered_customer_need",
            "safety_notes",
        )
        if generated.get(key) not in (None, "", [])
    }
    sent["draft_generation"].update({"source": "google_sheets", "row": row_number, "hash": digest[:16]})
    return sent


def reconcile_sheet_send(
    *,
    lead: dict[str, str],
    operation: dict[str, Any],
    zoho_token_file: str,
    env_file: str,
) -> dict[str, Any]:
    """Resolve an uncertain Sheets send only by its exact marker in Zoho Sent."""
    client = HttpZohoClient(zoho_token_file, env_file)
    configured_account = str(os.environ.get("ZOHO_MAIL_ACCOUNT_EMAIL") or "").strip()
    if not configured_account:
        return {"resolved": False, "status": "account_configuration_missing", "retryable": False}
    account = select_account(client.list_accounts(), configured_account)
    account_id = str(account["accountId"])
    sent_folder = find_sent_folder(client.list_folders(account_id))
    sent_folder_id = folder_id_of(sent_folder or {})
    if not sent_folder_id:
        return {"resolved": False, "status": "sent_folder_unavailable", "retryable": True}
    sent_rows = client.list_messages(account_id, sent_folder_id, 200)
    recipient = normalize_email(lead.get("Email", ""))

    def normalized(raw: dict[str, Any]) -> dict[str, Any]:
        return normalize_envelope(raw, default_folder_id=sent_folder_id)

    result = reconcile_sent_rows(
        operation,
        marker=str(operation.get("marker") or operation.get("operation_id") or ""),
        sent_rows=sent_rows,
        row_matches=lambda raw: recipient in sent_message_recipients(raw),
        fetch_content=lambda raw: {
            "content": client.get_content(
                account_id,
                sent_folder_id,
                str(normalized(raw).get("message_id") or ""),
            )
        },
        row_id=lambda raw: str(normalized(raw).get("message_id") or ""),
    )
    result["account_id"] = account_id
    return result


def send_telegram_notification(text: str) -> bool:
    """Internal alarm ping to Lukasz (Orchesta tenant only, never customer-facing).

    Sends only when HERMES_TELEGRAM_BOT_TOKEN + HERMES_TELEGRAM_CHAT_ID are set.
    Uses stdlib urllib so no new dependency is required. Failures are swallowed
    and logged via the briefing's ``telegram.sent`` flag — they never abort the run.
    """
    import urllib.request
    import urllib.error
    token = os.environ.get("HERMES_TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("HERMES_TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return False
    payload = json.dumps({"chat_id": chat_id, "text": text[:4096]}).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def build_briefing(
    row_number: int,
    lead: dict[str, str],
    result: dict[str, Any],
    digest: str,
    status: str,
    note: str,
    response_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    email = lead.get("Email") or "unknown-from-sheet@example.invalid"
    domain = email.split("@", 1)[1].lower() if "@" in email else ""
    classification = result.get("classification", "unknown_review_needed")
    raw_action = str((response_result or {}).get("action") or "")
    final_draft_id = str((response_result or {}).get("draft_id") or "")
    final_draft_created = (
        raw_action == "blocked"
        and (response_result or {}).get("reason") == "final_offer_autosend_forbidden"
        and bool(final_draft_id)
    )
    response_action = "sent" if raw_action == "sent" else "none"
    sent_id = str((response_result or {}).get("sent_id") or (response_result or {}).get("external_message_id") or "")
    briefing = {
        "message_id": f"sheets-row-{row_number}",
        "thread_id": "",
        "correlation_id": f"sheets:{row_number}:{digest[:16]}",
        "classification": classification,
        "classification_axes": {
            key: result.get(key)
            for key in ("source_type", "intent", "conversation_relation", "risk", "fit", "offer_status", "action")
            if result.get(key) is not None
        },
        "confidence": result.get("confidence", "low"),
        "runtime_mode": result.get("runtime_mode", "normal"),
        "wake_agent": True,
        "sender": {"email": email, "domain": domain, "relationship": relationship(classification)},
        "fit": {"orchesta_fit": orchesta_fit(classification)},
        "deal": {"action": result.get("crm_action", "sheet_lead")},
        "research": {"mode": "sheet_only", "sources_used": ["google_sheets"]},
        "attachments": [],
        "draft": {
            "action": "created" if final_draft_created else response_action,
            "kind": "final_offer" if final_draft_created else ("first_response" if response_action == "sent" else "none"),
            "thread_action": "new_message" if response_action == "sent" or final_draft_created else "none",
            "source_message_id": f"sheets-row-{row_number}",
            "recipient": email,
            "notify_only": response_action != "sent",
            "external_message_id": sent_id,
            "external_draft_id": final_draft_id,
        },
        "message": {"action": response_action, "external_message_id": sent_id, "recipient": email},
        "telegram": {"action": "internal_email_notify", "sent": False},
        "sheet": {
            "row": row_number,
            "status": status,
            "source": lead.get("Źródło", ""),
            "company": lead.get("Firma", ""),
        },
        "next_step": note,
    }
    if result.get("_llm_shadow") is not None:
        briefing["llm_shadow"] = result.get("_llm_shadow")
    if response_result and response_result.get("action") != "sent":
        briefing["draft"]["error"] = str(
            response_result.get("error")
            or response_result.get("reason")
            or response_result.get("response")
            or "send_failed"
        )[:300]
    if response_result and response_result.get("draft_generation"):
        briefing["draft"]["generation"] = response_result.get("draft_generation")
    return briefing


def process(args: argparse.Namespace) -> dict[str, Any]:
    assert_restore_reconciled()
    selection = controlled_selection(args)
    sheets, drive = load_google_services(args.token_file)
    meta = drive.files().get(fileId=args.spreadsheet_id, fields="id,name,capabilities,webViewLink").execute()
    if not meta.get("capabilities", {}).get("canEdit"):
        raise RuntimeError("Google account does not have edit access to the sheet")

    rows = values_get(sheets, args.spreadsheet_id, args.sheet_name, DEFAULT_RANGE)
    header, header_index, headers_changed = ensure_headers(
        sheets,
        args.spreadsheet_id,
        args.sheet_name,
        rows,
        write=not args.dry_run and selection is None,
    )
    if selection is not None and headers_changed:
        raise RuntimeError("controlled_sheet_required_headers_missing")
    if headers_changed:
        rows = values_get(sheets, args.spreadsheet_id, args.sheet_name, DEFAULT_RANGE)
    if selection is not None:
        validate_controlled_row(rows, header_index, selection)

    state = load_state(args.state_file)
    state_alert = state.pop("_hermes_state_alert", None)
    state_rows = state.setdefault("rows", {})
    from deal_routing_llm import build_gateway_router

    registry = UnifiedLeadRegistry(
        getattr(args, "registry_file", DEFAULT_REGISTRY_FILE),
        routing_llm=build_gateway_router(),
    )
    classifier = load_classifier()
    processed: list[dict[str, Any]] = []
    skipped = 0
    empty = 0
    drafts_created = 0
    draft_failures = 0
    responses_sent = 0
    send_failures = 0
    send_reconciled = 0
    send_outcome_unknown = 0
    final_offer_drafts_created = 0
    updates: list[tuple[int, str, str, str, str, str, str, str, str]] = []
    controlled_unselected_rows = 0

    for offset, row in enumerate(rows[1:], start=2):
        if selection is not None and offset != selection["row"]:
            controlled_unselected_rows += 1
            continue
        lead = row_to_lead(row, header_index)
        if not lead_has_content(lead):
            empty += 1
            continue
        digest = lead_hash(lead)
        current_status = lead.get("Hermes status", "").strip()
        current_hash = lead.get("Hermes hash", "").strip()
        logical_source_id = str(lead.get("Hermes source id") or "").strip()
        if not logical_source_id:
            logical_source_id = ensure_sheet_source_id()
            if not args.dry_run:
                source_col = header_index["Hermes source id"] + 1
                update_values(
                    sheets, args.spreadsheet_id, args.sheet_name,
                    f"{col_letter(source_col)}{offset}", [[logical_source_id]],
                )
            lead["Hermes source id"] = logical_source_id
        key = logical_source_id
        state_hash = str(state_rows.get(key, {}).get("hash", ""))
        source_key = sheet_source_key(args.spreadsheet_id, logical_source_id)
        if args.dry_run:
            registry_event = {
                "deal_id": "",
                "is_new_event": True,
                "is_cross_source_duplicate": False,
                "requires_review": not bool(normalize_email(lead.get("Email", ""))),
                "reason_code": "invalid_email" if not normalize_email(lead.get("Email", "")) else "",
            }
        else:
            registry_event = registry.register_event(
                source_type="google_sheets",
                source_key=source_key,
                email=lead.get("Email", ""),
                company=lead.get("Firma", ""),
                contact_name=lead.get("Imię", ""),
                content=synthesize_body(lead),
                relation="new",
                facts=facts_from_lead(lead),
                tenant_id=_default_tenant_id(),
                source_metadata={
                    "spreadsheet_id": args.spreadsheet_id,
                    "sheet_name": args.sheet_name,
                    "row": offset,
                    "content_hash": digest,
                    "content_version": digest,
                    "logical_source_id": logical_source_id,
                    "resolved_reply_recipient": normalize_email(lead.get("Email", "")),
                    "recipient_resolution_evidence": {
                        "method": "sheet_email", "reply_all": False,
                        "resolved_reply_recipient": normalize_email(lead.get("Email", "")),
                    },
                },
            )

        process_test = args.process_test_status and current_status == "test_created"
        changed_since = current_hash and current_hash != digest
        pending_operation = registry.outbox_operation(f"response:{source_key}")
        pending_reconciliation = str((pending_operation or {}).get("status") or "") in {
            "in_progress", "outcome_unknown"
        }
        should_process = (
            (not current_status)
            or process_test
            or changed_since
            or (not current_hash and state_hash != digest and not current_status)
            or pending_reconciliation
        )
        if not should_process:
            deal = registry.get_deal(str(registry_event.get("deal_id") or "")) if registry_event.get("deal_id") else None
            desired = STATUS_TO_SHEET.get(str((deal or {}).get("status") or ""), "")
            if desired and desired != current_status and str((deal or {}).get("status")) in {"draft_ready", "waiting_for_customer", "offer_ready", "review_required"}:
                draft_id = str((deal or {}).get("final_draft_id") or (deal or {}).get("current_draft_id") or "")
                sent_id = str((deal or {}).get("last_response_id") or "")
                note = "Status zsynchronizowany ze wspólnym procesem skrzynki i Arkusza."
                updates.append((offset, desired, lead.get("Hermes klasyfikacja", ""), note, now_iso(), digest, draft_id, sent_id, logical_source_id))
            skipped += 1
            continue

        result = classify_lead(classifier, lead, offset, digest)
        # Rebuilt LLM intent classifier — shadow alongside deterministic (spec 25 E1).
        # The deterministic result still drives the flow; the LLM verdict is only
        # recorded for shadow analysis until auto_reply_allowed flips on (E2).
        if _llm_enabled():
            result["_llm_shadow"] = _llm_shadow_classify(
                lead, result, tenant_id=_default_tenant_id(), run_id=source_key
            )
        status = "w analizie"
        note = note_for(result, lead)
        eligible_for_response = draftable_from_sheet(result, lead)
        deal_id = str(registry_event.get("deal_id") or "")
        linked_response_id = ""
        if registry_event.get("requires_review"):
            eligible_for_response = False
            status = "wymaga sprawdzenia"
            reason = str(registry_event.get("reason_code") or "invalid_or_conflicting_identity")
            note = f"Rekord ma niepoprawny adres e-mail albo sprzeczne dane ({reason}); nie utworzyłem wiadomości."
        elif registry_event.get("is_cross_source_duplicate") or (deal_id and registry.has_sent_pre_offer(deal_id)):
            eligible_for_response = False
            deal = registry.get_deal(deal_id) if deal_id else None
            status = STATUS_TO_SHEET.get(str((deal or {}).get("status") or ""), "w analizie")
            linked_response_id = str((deal or {}).get("last_response_id") or "")
            if registry_event.get("is_cross_source_duplicate"):
                note = "Ten sam lead istnieje już w procesie z innego źródła; nie wysłałem duplikatu odpowiedzi."
            else:
                note = "Dla tego deala wysłano już wiadomość przedofertową; nie wysłałem kolejnej odpowiedzi."
        elif result.get("classification") == "new_quote_request" and result.get("confidence") == "high" and not eligible_for_response:
            status = "wymaga sprawdzenia"
            note = "Lead zawiera sygnały blokujące (np. non-RFQ, prompt-injection, sekret albo żądanie wiążącej oferty); zatrzymałem proces."
            if deal_id and not args.dry_run:
                registry.mark_review(deal_id, note)
        elif result.get("classification") not in GOOD_DRAFT_CLASSES or result.get("confidence") != "high":
            eligible_for_response = False
            status = "wymaga sprawdzenia"
            note = "Lead jest ryzykowny, niejasny albo nie pasuje do Orchesta RFQ; zatrzymałem proces do sprawdzenia."
            if deal_id and not args.dry_run:
                registry.mark_review(deal_id, note)

        response_result: dict[str, Any] | None = None
        draft_id = ""
        sent_id = linked_response_id
        auto_send = bool(getattr(args, "auto_send", False))
        # Kill-switch gate (spec 24/25): LLM-authored replies are auto-sent only
        # when auto_reply_low_risk=1 AND reply_kill_switch=0. Otherwise the LLM
        # runs in shadow and the deterministic composer drives any send.
        _switches = _SafetySwitches.from_env() if _SafetySwitches is not None else None
        use_llm_send = _llm_enabled() and bool(_switches and _switches.auto_reply_allowed)
        response_stage = f"response:{source_key}"
        if auto_send and eligible_for_response and args.dry_run:
            note = "Dry-run: wiadomość przedofertowa kwalifikuje się do wysyłki, ale transport Zoho nie został wywołany."
        elif auto_send and eligible_for_response:
            planned_questions = discovery_questions_for_message(
                synthesize_body(lead),
                {"from": lead.get("Email", ""), "attachments": []},
            )
            planned_message_type = operational_type_for_flow(
                has_questions=bool(planned_questions),
                first_contact=True,
            )
            registry.persist_message_policy(
                "google_sheets",
                source_key,
                requested_type=planned_message_type,
                effective_type=planned_message_type,
                transport_mode="auto_send",
                reasons=["planned_sheet_operational_response"],
            )
            response_claimed = registry.claim_response(
                deal_id,
                response_stage,
                content_hash=digest,
                operation_id=response_stage,
                owner=f"google-sheets:{now_iso()}",
                message_type=planned_message_type,
                recipient=lead.get("Email", ""),
                source_type="google_sheets",
                source_key=source_key,
                thread_id="",
                marker=response_stage,
            )
            if not response_claimed:
                outbox = registry.outbox_operation(response_stage)
                if str((outbox or {}).get("status") or "") in {"in_progress", "outcome_unknown"}:
                    reconciliation = reconcile_sheet_send(
                        lead=lead,
                        operation=outbox or {},
                        zoho_token_file=args.zoho_token_file,
                        env_file=args.env_file,
                    )
                    if reconciliation.get("resolved"):
                        sent_id = str(reconciliation.get("external_message_id") or "")
                        registry.record_response(
                            deal_id,
                            response_stage,
                            message_id=sent_id,
                            content_hash=digest,
                            operation_id=response_stage,
                        )
                        send_reconciled += 1
                        status = STATUS_TO_SHEET["waiting_for_customer"]
                        note = "Potwierdziłem wcześniejszą wysyłkę w Zoho Sent po markerze operacji; nie wysłałem duplikatu."
                        response_result = {
                            "action": "already_sent_reconciled",
                            "external_message_id": sent_id,
                            "reconciliation": reconciliation,
                        }
                    else:
                        send_outcome_unknown += 1
                        status = "wymaga sprawdzenia"
                        note = (
                            "Wynik wcześniejszej wysyłki nadal jest niejednoznaczny; "
                            "sprawdziłem Zoho Sent i nie ponowiłem wiadomości."
                        )
                        response_result = {
                            "action": "outcome_unknown",
                            "error": str(reconciliation.get("status") or "not_resolved"),
                            "reconciliation": reconciliation,
                        }
                else:
                    deal = registry.get_deal(deal_id) if deal_id else None
                    sent_id = str((deal or {}).get("last_response_id") or "")
                    status = STATUS_TO_SHEET.get(str((deal or {}).get("status") or ""), "w analizie")
                    note = "Odpowiedź dla tego zdarzenia została już obsłużona; nie wysłałem duplikatu."
            else:
                llm_body_gen = (
                    make_llm_body_generator(
                        lead=lead, result=result, tenant_id=_default_tenant_id(),
                        run_id=source_key, is_first_agent_reply=True,
                    )
                    if use_llm_send
                    else None
                )
                def _send_once(body_gen):
                    return send_sheet_zoho_response(
                        lead=lead,
                        result=result,
                        row_number=offset,
                        digest=digest,
                        zoho_token_file=args.zoho_token_file,
                        env_file=args.env_file,
                        body_generator=body_gen,
                        message_type=planned_message_type,
                        durable_context={
                            "registry": registry,
                            "source_type": "google_sheets",
                            "source_key": source_key,
                            "deal_id": deal_id,
                            "thread_id": "",
                            "operation_id": response_stage,
                            "process_stage": response_stage,
                        },
                    )

                def _content_reject(exc: BaseException) -> bool:
                    text = str(exc).lower()
                    return isinstance(exc, ReplyValidationError) or any(
                        token in text
                        for token in (
                            "llm_body",
                            "unapproved",
                            "price_content",
                            "validation",
                            "draftgeneration",
                            "replyvalidation",
                        )
                    )

                try:
                    response_result = _send_once(llm_body_gen)
                    response_action = str(response_result.get("action") or "")
                    if response_action == "blocked":
                        reason = str(response_result.get("reason") or "transport_blocked")
                        draft_id = str(response_result.get("draft_id") or "")
                        status = "wymaga sprawdzenia"
                        if reason == "final_offer_autosend_forbidden" and draft_id:
                            drafts_created += 1
                            final_offer_drafts_created += 1
                            note = (
                                "Wykryłem warunki finalnej oferty, zablokowałem wysyłkę i utworzyłem "
                                "draft do ręcznej oceny."
                            )
                        else:
                            send_failures += 1
                            note = f"Transport został bezpiecznie zablokowany ({reason}); wymagana jest ręczna ocena."
                        registry.mark_review(deal_id, reason)
                    elif response_action == "sent":
                        sent_id = str(response_result.get("sent_id") or response_result.get("external_message_id") or "")
                        if not sent_id:
                            raise RuntimeError("external_message_id_missing_after_send")
                        responses_sent += 1
                        status = STATUS_TO_SHEET["waiting_for_customer"]
                        note = "Wysłałem bezpieczną wiadomość przedofertową i oczekuję na odpowiedź klienta. Finalna oferta nie została wysłana."
                        registry.record_response(
                            deal_id, response_stage, message_id=sent_id,
                            content_hash=digest, operation_id=response_stage,
                        )
                        account_id = str(response_result.get("account_id") or "")
                        thread_id = str(response_result.get("thread_id") or sent_id)
                        registry.bind_thread(deal_id, provider="zoho", account_id=account_id, thread_id=thread_id)
                        registry.observe_conversation_message(
                            deal_id,
                            account_id=account_id,
                            thread_id=thread_id,
                            message_id=sent_id,
                            direction="outbound",
                            origin="hermes_automatic",
                            occurred_at=now_iso(),
                            metadata={
                                "stage": response_stage,
                                "source": "google_sheets",
                                "rfc_message_id": str(response_result.get("rfc_message_id") or ""),
                                "body": str(response_result.get("body_text") or "")[:1200],
                            },
                        )
                    else:
                        raise RuntimeError(
                            str(
                                response_result.get("error")
                                or response_result.get("reason")
                                or response_result.get("response")
                                or "pre_offer_send_error"
                            )
                        )
                except Exception as first_exc:
                    # The LLM generator already retries once. A second content
                    # failure must stop automation; do not replace it with a
                    # different customer-facing template.
                    if use_llm_send and llm_body_gen is not None and _content_reject(first_exc):
                        send_failures += 1
                        response_result = {
                            "action": "awaiting_human",
                            "error": str(first_exc)[:300],
                            "validation_failed": True,
                        }
                        status = "wymaga sprawdzenia"
                        note = f"Odpowiedź nie przeszła dwóch prób walidacji ({first_exc}); przekazuję do człowieka, nie wysyłam."
                        if deal_id and not args.dry_run:
                            registry.fail_response(deal_id, response_stage, error=str(first_exc))
                            registry.mark_review(deal_id, f"reply_validation_failed: {first_exc}")
                    elif _content_reject(first_exc):
                        send_failures += 1
                        response_result = {"action": "awaiting_human", "error": str(first_exc)[:300], "validation_failed": True}
                        status = "wymaga sprawdzenia"
                        note = f"Odpowiedź nie przeszła walidacji ({first_exc}); przekazuję do człowieka, nie wysyłam."
                        if deal_id and not args.dry_run:
                            registry.fail_response(deal_id, response_stage, error=str(first_exc))
                            registry.mark_review(deal_id, f"reply_validation_failed: {first_exc}")
                    else:
                        send_failures += 1
                        response_result = {"action": "error", "error": str(first_exc)[:300], "outcome_unknown": True}
                        status = "wymaga sprawdzenia"
                        note = "Nie potwierdziłem bezpiecznie wyniku transportu wiadomości; zablokowałem automatyczne ponowienie, aby uniknąć duplikatu."
                        if deal_id and not args.dry_run:
                            registry.fail_response(deal_id, response_stage, error=str(first_exc))
                            registry.mark_review(deal_id, f"pre_offer_send_outcome_unknown: {first_exc}")
        checked = now_iso()
        briefing = build_briefing(offset, lead, result, digest, status, note, response_result=response_result)
        briefing["deal_id"] = deal_id
        briefing["client"] = {"company": lead.get("Firma", ""), "contact_name": lead.get("Imię", ""), "email": lead.get("Email", "")}
        briefing["telegram_text"] = (
            f"Lead z Arkusza Google: {lead.get('Firma') or 'firma nieustalona'}, kontakt {lead.get('Imię') or lead.get('Email') or 'nieustalony'}. "
            f"Status: {status}. {note}"
        )
        # Internal alarm ping to Lukasz (Orchesta tenant only). Sent only when
        # HERMES_TELEGRAM_BOT_TOKEN + HERMES_TELEGRAM_CHAT_ID are set; otherwise
        # the briefing still records the would-be text for the log.
        tg_sent = send_telegram_notification(briefing["telegram_text"]) if not args.dry_run else False
        briefing["telegram"] = {"action": "internal_email_notify", "sent": tg_sent}
        processed.append(briefing)
        updates.append((offset, status, str(result.get("classification", "unknown_review_needed")), note, checked, digest, draft_id, sent_id, logical_source_id))
        state_rows[key] = {"hash": digest, "status": status, "classification": result.get("classification"), "processed_at": checked, "draft_id": draft_id, "sent_id": sent_id}

    if updates and not args.dry_run:
        max_col = max(header_index[h] for h in HERMES_HEADERS) + 1
        min_col = min(header_index[h] for h in HERMES_HEADERS) + 1
        # Update each row's Hermes columns in one compact range. Assumes Hermes headers are contiguous;
        # if user rearranges columns, update cells individually.
        contiguous = [header[i] for i in range(min_col - 1, max_col)] == HERMES_HEADERS
        for row_number, status, classification, note, checked, digest, draft_id, sent_id, logical_source_id in updates:
            if contiguous:
                update_values(
                    sheets,
                    args.spreadsheet_id,
                    args.sheet_name,
                    f"{col_letter(min_col)}{row_number}:{col_letter(max_col)}{row_number}",
                    [[status, classification, note, checked, digest, draft_id, sent_id, logical_source_id]],
                )
            else:
                for name, value in zip(HERMES_HEADERS, [status, classification, note, checked, digest, draft_id, sent_id, logical_source_id]):
                    col = header_index[name] + 1
                    update_values(sheets, args.spreadsheet_id, args.sheet_name, f"{col_letter(col)}{row_number}", [[value]])
        save_state(args.state_file, state)
    elif updates and args.dry_run:
        pass
    else:
        save_state(args.state_file, state)

    registry.close()
    summary = {
        "run_id": f"google-sheets-leads:{now_iso()}",
        "source": "google_sheets",
        "spreadsheet_id": args.spreadsheet_id,
        "spreadsheet_name": meta.get("name"),
        "sheet_name": args.sheet_name,
        "listed_rows": max(0, len(rows) - 1),
        "empty_rows": empty,
        "already_processed": skipped,
        "woken": len(processed),
        "sheet_rows_updated": len(updates) if not args.dry_run else 0,
        "drafts_created": drafts_created,
        "draft_failures": draft_failures,
        "responses_sent": responses_sent,
        "send_failures": send_failures,
        "send_reconciled": send_reconciled,
        "send_outcome_unknown": send_outcome_unknown,
        "final_offer_drafts_created": final_offer_drafts_created,
        "headers_changed": headers_changed,
        "dry_run": bool(args.dry_run),
        "controlled_selection": {
            "enabled": selection is not None,
            "row": selection["row"] if selection else None,
            "email": selection["email"] if selection else "",
            "test_id": selection["test_id"] if selection else "",
            "expected_status": selection["expected_status"] if selection else "",
            "unselected_rows_untouched": controlled_unselected_rows,
        },
        "state_alert": state_alert,
        "briefings": processed,
    }
    return summary


def self_test() -> int:
    fake = {"Email": "anna@example.com", "Firma": "Example", "Wiadomość": "Proszę o ofertę na Orchesta RFQ dla maila i formularza", "Źródło": "test"}
    digest = lead_hash(fake)
    if digest != lead_hash(dict(fake)):
        print("FAIL: hash unstable")
        return 1
    if not lead_has_content(fake):
        print("FAIL: content detection")
        return 1
    print("google_sheets_lead_poller self-test: ok")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Google Sheets lead poller for Orchesta RFQ")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--spreadsheet-id", default=DEFAULT_SPREADSHEET_ID)
    parser.add_argument("--sheet-name", default=DEFAULT_SHEET_NAME)
    parser.add_argument("--token-file", default=DEFAULT_TOKEN_FILE)
    parser.add_argument("--state-file", default=DEFAULT_STATE_FILE)
    parser.add_argument("--registry-file", default=DEFAULT_REGISTRY_FILE)
    parser.add_argument("--zoho-token-file", default=DEFAULT_ZOHO_TOKEN_FILE)
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--auto-send", action="store_true", help="Deprecated compatibility flag; cannot enable transport. Use the durable message policy and HERMES_OPERATIONAL_AUTOSEND_ENABLED=1.")
    parser.add_argument("--auto-draft", action="store_true", help="Legacy compatibility flag; customer response drafts are no longer created")
    parser.add_argument("--process-test-status", action="store_true", help="Process rows marked test_created once for validation")
    parser.add_argument("--controlled-row-number", type=int, help="Fail-closed live test: process only this exact Sheet row")
    parser.add_argument("--controlled-email", help="Fail-closed live test: require this exact email in the selected row")
    parser.add_argument("--controlled-test-id", help="Fail-closed live test: require this unique test ID in the selected row")
    parser.add_argument("--controlled-expected-status", help="Fail-closed live test: require this exact Hermes status; use __EMPTY__ for blank")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if args.auto_send:
        print("warning: --auto-send is deprecated and cannot enable transport", file=sys.stderr)
    args.auto_send = operational_autosend_enabled()
    summary = process(args)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
