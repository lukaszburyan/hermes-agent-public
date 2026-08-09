#!/usr/bin/env python3
"""Narrow Zoho transport for automatic customer communication before an offer.

This module deliberately cannot send prices, offer-like message kinds or
attachments. Final offers keep using :mod:`zoho_reply_draft` and ``mode=draft``.
"""
from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from message_policy import (
    AUTO_SEND_TYPES,
    FINAL_OFFER,
    MANUAL_REVIEW,
    MESSAGE_TYPES,
    decide_message_policy,
    normalize_message_type,
)
from unified_lead_registry import contact_identity_for_email, normalize_email
from restore_gate import assert_restore_reconciled
from zoho_reply_draft import HttpDraftPoster, build_draft_payload, contains_price

APPROVAL_PHRASE = "AUTOMATED PRE-OFFER CUSTOMER SEND APPROVED"
ALLOWED_MESSAGE_KINDS = set(AUTO_SEND_TYPES)
FORBIDDEN_OFFER_KINDS = {"final_offer", "offer", "price_quote", "quote", "commercial_offer"}
SUCCESS_STATUSES = {200, 201, 202}


def _assert_pre_offer_kind(message_kind: str) -> str:
    raw = str(message_kind or "").strip().lower()
    kind = normalize_message_type(raw)
    if raw in FORBIDDEN_OFFER_KINDS or kind == FINAL_OFFER:
        raise PermissionError("final_offer_autosend_forbidden")
    if kind in ALLOWED_MESSAGE_KINDS:
        return kind
    if any(token in raw for token in ("offer", "quote", "price")):
        raise PermissionError("final_offer_autosend_forbidden")
    raise PermissionError("message_kind_not_allowed_for_autosend")


def _enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _transport_kill_switch_enabled() -> bool:
    marker = Path(os.environ.get(
        "HERMES_TRANSPORT_KILL_SWITCH_FILE",
        "/opt/data/rfq-state/TRANSPORT_KILL_SWITCH",
    ))
    return _enabled("HERMES_TRANSPORT_KILL_SWITCH") or marker.exists()


def _recipient_allowed_in_test_mode(recipient: str) -> bool:
    if not _enabled("HERMES_TEST_MODE"):
        return True
    allowed = {
        value.strip().lower()
        for value in os.environ.get("HERMES_TEST_RECIPIENT_ALLOWLIST", "").split(",")
        if value.strip()
    }
    return bool(allowed) and recipient.strip().lower() in allowed


def _record_security(registry: Any, event_type: str, context: dict[str, Any], **details: Any) -> int:
    if registry is None or not hasattr(registry, "record_security_event"):
        return 0
    return int(registry.record_security_event(
        event_type,
        deal_id=str(context.get("deal_id") or ""),
        source_type=str(context.get("source_type") or ""),
        source_key=str(context.get("source_key") or ""),
        operation_id=str(context.get("operation_id") or ""),
        details=details,
    ))


def _notify_human(notifier: Any | None, event: dict[str, Any]) -> tuple[bool, str]:
    if notifier is None:
        return False, "notifier_unavailable"
    try:
        notifier(event)
    except Exception as exc:
        return False, f"{exc.__class__.__name__}:{str(exc)[:160]}"
    return True, ""


def _durable_transport_state(
    context: dict[str, Any] | None,
    *,
    payload: dict[str, Any],
    account_id: str,
    source_message_id: str,
    threaded: bool,
) -> tuple[Any, dict[str, Any], dict[str, Any], str] | tuple[None, None, None, str]:
    context = dict(context or {})
    registry = context.get("registry")
    if registry is None or not hasattr(registry, "get_event") or not hasattr(registry, "get_deal"):
        return None, None, None, "durable_transport_context_required"
    source_type = str(context.get("source_type") or "")
    source_key = str(context.get("source_key") or "")
    event = registry.get_event(source_type, source_key)
    if not event:
        return None, None, None, "durable_event_not_found"
    deal_id = str(context.get("deal_id") or "")
    if not deal_id or str(event.get("deal_id") or "") != deal_id:
        return registry, event, None, "durable_deal_mismatch"
    deal = registry.get_deal(deal_id)
    if not deal:
        return registry, event, None, "durable_deal_not_found"

    operation_id = str(context.get("operation_id") or "")
    outbox = registry.outbox_operation(operation_id) if operation_id and hasattr(registry, "outbox_operation") else None
    if not outbox:
        return registry, event, deal, "durable_outbox_operation_missing"
    if str(outbox.get("status") or "") != "claimed":
        return registry, event, deal, "durable_outbox_not_claimed"
    if any((
        str(outbox.get("deal_id") or "") != deal_id,
        str(outbox.get("source_type") or "") != source_type,
        str(outbox.get("source_key") or "") != source_key,
    )):
        return registry, event, deal, "durable_outbox_binding_mismatch"
    process_stage = str(context.get("process_stage") or "")
    if not process_stage:
        return registry, event, deal, "durable_process_stage_missing"
    if str(outbox.get("stage") or "") != process_stage:
        return registry, event, deal, "durable_process_stage_mismatch"

    metadata = dict(event.get("metadata") or {})
    policy = dict(metadata.get("message_policy") or {})
    durable_type = str(policy.get("effective_type") or metadata.get("message_type") or "")
    if not durable_type:
        return registry, event, deal, "durable_message_type_missing"
    durable_mode = str(policy.get("transport_mode") or "")
    if normalize_message_type(durable_type) in AUTO_SEND_TYPES and durable_mode != "auto_send":
        return registry, event, deal, "durable_transport_not_authorized"

    recipient_raw = str(payload.get("toAddress") or "").strip().lower()
    recipient = normalize_email(recipient_raw)
    durable_recipient = normalize_email(str(
        event.get("resolved_reply_recipient")
        or metadata.get("resolved_reply_recipient")
        or event.get("email")
        or ""
    ))
    recipient_evidence = event.get("recipient_evidence_json") or metadata.get("recipient_resolution_evidence") or {}
    if isinstance(recipient_evidence, str):
        try:
            recipient_evidence = json.loads(recipient_evidence or "{}")
        except json.JSONDecodeError:
            recipient_evidence = {}
    if not recipient or not durable_recipient or recipient != durable_recipient or not recipient_evidence:
        return registry, event, deal, "durable_recipient_mismatch"
    durable_deal_recipient = normalize_email(str(deal.get("primary_email") or ""))
    if not durable_deal_recipient or durable_deal_recipient != durable_recipient:
        return registry, event, deal, "durable_recipient_mismatch"
    if normalize_email(str(outbox.get("recipient") or "")) != recipient:
        return registry, event, deal, "durable_outbox_recipient_mismatch"
    if str(outbox.get("contact_identity") or "") != contact_identity_for_email(recipient_raw):
        return registry, event, deal, "durable_contact_identity_mismatch"

    if threaded:
        durable_source_id = str(metadata.get("source_message_id") or "")
        if not source_message_id or (durable_source_id and source_message_id != durable_source_id):
            return registry, event, deal, "durable_source_message_mismatch"
        durable_account = str(metadata.get("account_id") or "")
        if durable_account and durable_account != str(account_id):
            return registry, event, deal, "durable_account_mismatch"
        expected_thread = str(event.get("thread_id") or deal.get("customer_thread_id") or "")
        context_thread = str(context.get("thread_id") or "")
        if expected_thread and context_thread != expected_thread:
            return registry, event, deal, "durable_thread_mismatch"
        if str(outbox.get("thread_id") or "") != context_thread:
            return registry, event, deal, "durable_outbox_thread_mismatch"
    if str(outbox.get("message_type") or "") != durable_type:
        return registry, event, deal, "durable_outbox_message_type_mismatch"
    return registry, event, deal, durable_type


def build_pre_offer_payload(
    *,
    account_email: str,
    to_address: str,
    inbound_subject: str,
    body_text: str,
    message_kind: str,
    threaded: bool,
    subject_override: str | None = None,
    source_sender: str = "",
    route_final_offer_to_policy: bool = False,
) -> dict[str, Any]:
    """Build a send payload while reusing the customer-content safety checks.

    Threaded replies are posted to ``/messages/{source_message_id}`` with
    ``action=reply``. Google Sheets leads are posted to ``/messages`` with
    ``mode=send``. Neither shape can carry an attachment.
    """
    _assert_pre_offer_kind(message_kind)
    if contains_price(body_text) and not route_final_offer_to_policy:
        raise PermissionError("price_content_autosend_forbidden")

    # Reuse the mature recipient, subject, signature and content validators from
    # the draft boundary. A synthetic RFC id is used only while validating a
    # threaded payload; transport-specific threading is provided by Zoho's reply
    # endpoint and these headers are removed below.
    draft_payload = build_draft_payload(
        account_email=account_email,
        to_address=to_address,
        inbound_subject=inbound_subject,
        rfc_message_id="<identity-012@customer-004.example.com>" if threaded else "",
        references="",
        body_text=body_text,
        draft_kind="first_response",
        threaded=threaded,
        subject_override=subject_override,
        source_sender=source_sender or account_email,
        allow_financial_policy_routing=route_final_offer_to_policy,
    )
    common = {
        "fromAddress": draft_payload["fromAddress"],
        "toAddress": draft_payload["toAddress"],
        "subject": draft_payload["subject"],
        "content": draft_payload["content"],
        "mailFormat": draft_payload.get("mailFormat", "html"),
        "askReceipt": "no",
    }
    if threaded:
        return {**common, "action": "reply"}
    return {**common, "mode": "send"}


def _external_message_id(response: dict[str, Any]) -> str:
    data: Any = response.get("data") if isinstance(response, dict) else None
    if isinstance(data, list) and data:
        data = data[0]
    candidates = [data, response]
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        for key in ("messageId", "messageID", "id", "sentMessageId"):
            value = str(candidate.get(key) or "").strip()
            if value:
                return value
    return ""


def _transport_content_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validate_transport_payload(payload: dict[str, Any], *, threaded: bool) -> None:
    attachment_keys = [key for key, value in payload.items() if "attach" in str(key).lower() and value]
    if attachment_keys:
        raise PermissionError("pre_offer_attachments_forbidden")
    content = str(payload.get("content") or "")
    if contains_price(content):
        raise PermissionError("price_content_autosend_forbidden")
    if threaded:
        if payload.get("action") != "reply" or "mode" in payload:
            raise PermissionError("invalid_pre_offer_reply_payload")
    elif payload.get("mode") != "send" or "action" in payload:
        raise PermissionError("invalid_pre_offer_send_payload")


def create_pre_offer_message(
    account_id: str,
    *,
    source_message_id: str,
    payload: dict[str, Any],
    poster: Any,
    approval: str,
    message_kind: str,
    threaded: bool,
    durable_context: dict[str, Any] | None = None,
    draft_fallback: Any | None = None,
    blocked_draft_fallback: Any | None = None,
    human_notifier: Any | None = None,
) -> dict[str, Any]:
    """Send one operational message after re-reading its durable event/deal."""
    assert_restore_reconciled()
    if not _enabled("HERMES_ALLOW_PRE_OFFER_SEND"):
        return {"action": "blocked", "reason": "pre_offer_send_gate_disabled"}
    if approval != APPROVAL_PHRASE:
        return {"action": "blocked", "reason": "approval_phrase_mismatch"}

    context = dict(durable_context or {})
    registry, event, deal, durable_or_error = _durable_transport_state(
        context,
        payload=payload,
        account_id=account_id,
        source_message_id=source_message_id,
        threaded=threaded,
    )
    if event is None or deal is None or durable_or_error not in MESSAGE_TYPES:
        security_id = _record_security(registry, durable_or_error, context, reason=durable_or_error)
        operation_id = str(context.get("operation_id") or "")
        if registry is not None and operation_id and hasattr(registry, "outbox_operation"):
            outbox = registry.outbox_operation(operation_id)
            current_type = str((outbox or {}).get("message_type") or "")
            registry.update_outbox_policy(
                operation_id,
                message_type=current_type if current_type in MESSAGE_TYPES else MANUAL_REVIEW,
            )
            registry.finalize_response_attempt(
                operation_id, outcome="manual_review", reason_codes=[durable_or_error],
            )
        notified, notification_error = _notify_human(
            human_notifier,
            {
                "event": "transport_manual_review_required",
                "deal_id": str(context.get("deal_id") or ""),
                "source_key": str(context.get("source_key") or ""),
                "reason": durable_or_error,
            },
        )
        return {
            "action": "blocked",
            "reason": durable_or_error,
            "security_event_id": security_id,
            "human_notified": notified,
            "human_notification_error": notification_error,
        }
    assert registry is not None
    durable_deal_version = str(deal.get("updated_at") or "")

    durable_type = normalize_message_type(durable_or_error)
    supplied_type = normalize_message_type(message_kind)
    if supplied_type != durable_type:
        security_id = _record_security(
            registry,
            "message_type_mismatch",
            context,
            durable_type=durable_type,
            supplied_type=supplied_type,
        )
        return {"action": "blocked", "reason": "message_type_mismatch", "security_event_id": security_id}

    operation_id = str(context.get("operation_id") or "")
    if not registry.bind_outbox_content(
        operation_id,
        content_hash=_transport_content_hash(payload),
    ):
        security_id = _record_security(registry, "outbox_content_binding_failed", context)
        return {
            "action": "blocked",
            "reason": "outbox_content_binding_failed",
            "security_event_id": security_id,
        }

    decision = decide_message_policy(
        durable_type,
        body_text=str(payload.get("content") or ""),
        subject=str(payload.get("subject") or ""),
        attachments=payload.get("attachments") or payload.get("attachment") or [],
        process_stage=str(context.get("process_stage") or ""),
        deal_stage=str(deal.get("status") or ""),
    )
    policy = registry.persist_message_policy(
        str(context.get("source_type") or ""),
        str(context.get("source_key") or ""),
        requested_type=durable_type,
        effective_type=decision.effective_type,
        transport_mode=decision.transport_mode,
        reasons=decision.reasons,
    )
    registry.update_outbox_policy(operation_id, message_type=decision.effective_type)

    if decision.effective_type == FINAL_OFFER:
        security_id = _record_security(
            registry,
            "final_offer_autosend_attempt",
            context,
            reasons=list(decision.reasons),
        )
        draft_result: dict[str, Any] = {"action": "not_created", "reason": "draft_fallback_unavailable"}
        if draft_fallback is not None:
            try:
                raw_draft = draft_fallback(payload=payload, context=context, policy=policy)
            except Exception as exc:
                draft_result = {"action": "error", "reason": f"{exc.__class__.__name__}:{str(exc)[:160]}"}
            else:
                if isinstance(raw_draft, dict):
                    draft_result = raw_draft
        draft_id = str(draft_result.get("draft_id") or "").strip()
        if not draft_id and isinstance(draft_result.get("response"), dict):
            draft_id = _external_message_id(draft_result["response"])
        if draft_result.get("action") == "created" and draft_id and hasattr(registry, "record_draft"):
            canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            registry.record_draft(
                str(context.get("deal_id") or ""),
                "final_offer_security_review",
                draft_id=draft_id,
                content_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            )
        registry.update_outbox_policy(operation_id, message_type=FINAL_OFFER)
        registry.finalize_response_attempt(
            operation_id,
            outcome="draft_created" if draft_result.get("action") == "created" and draft_id else "manual_review",
            external_draft_id=draft_id,
            provider_evidence={"provider_draft_id": draft_id} if draft_id else {},
            reason_codes=[] if draft_id else ["final_offer_draft_creation_failed"],
        )
        notified, notification_error = _notify_human(
            human_notifier,
            {
                "event": "final_offer_autosend_blocked",
                "deal_id": str(context.get("deal_id") or ""),
                "source_key": str(context.get("source_key") or ""),
                "draft": draft_result,
            },
        )
        return {
            "action": "blocked",
            "reason": "final_offer_autosend_forbidden",
            "message_type": FINAL_OFFER,
            "draft": draft_result,
            "draft_id": draft_id,
            "human_notified": notified,
            "human_notification_required": not notified,
            "human_notification_error": notification_error,
            "security_event_id": security_id,
        }
    if decision.effective_type == MANUAL_REVIEW:
        security_id = _record_security(registry, "unknown_message_type", context)
        registry.update_outbox_policy(operation_id, message_type=durable_type)
        registry.finalize_response_attempt(
            operation_id, outcome="manual_review", reason_codes=["manual_review_required"],
        )
        notified, notification_error = _notify_human(
            human_notifier,
            {"event": "message_manual_review_required", "deal_id": context.get("deal_id")},
        )
        return {
            "action": "blocked",
            "reason": "manual_review_required",
            "security_event_id": security_id,
            "human_notified": notified,
            "human_notification_error": notification_error,
        }

    if _transport_kill_switch_enabled():
        security_id = _record_security(registry, "transport_kill_switch_block", context)
        blocked_draft_result: dict[str, Any] = {"action": "not_created", "reason": "blocked_draft_fallback_unavailable"}
        if blocked_draft_fallback is not None:
            try:
                raw_draft = blocked_draft_fallback(payload=payload, context=context, policy=policy)
            except Exception as exc:
                blocked_draft_result = {"action": "error", "reason": f"{exc.__class__.__name__}:{str(exc)[:160]}"}
            else:
                if isinstance(raw_draft, dict):
                    blocked_draft_result = raw_draft
        draft_id = str(blocked_draft_result.get("draft_id") or "").strip()
        if not draft_id and isinstance(blocked_draft_result.get("response"), dict):
            draft_id = _external_message_id(blocked_draft_result["response"])
        draft_created = blocked_draft_result.get("action") == "created" and bool(draft_id)
        if draft_created and hasattr(registry, "record_draft"):
            registry.record_draft(
                str(context.get("deal_id") or ""),
                "transport_disabled_review",
                draft_id=draft_id,
                content_hash=_transport_content_hash(payload),
            )
        registry.update_outbox_policy(operation_id, message_type=decision.effective_type)
        registry.finalize_response_attempt(
            operation_id, outcome="draft_created" if draft_created else "manual_review",
            external_draft_id=draft_id,
            provider_evidence={"provider_draft_id": draft_id} if draft_id else {},
            reason_codes=[] if draft_created else ["transport_kill_switch_enabled"],
        )
        notified, notification_error = _notify_human(
            human_notifier,
            {
                "event": "transport_kill_switch_blocked",
                "deal_id": str(context.get("deal_id") or ""),
                "source_key": str(context.get("source_key") or ""),
                "draft": blocked_draft_result,
            },
        )
        return {
            "action": "blocked",
            "reason": "transport_kill_switch_enabled",
            "draft": blocked_draft_result,
            "draft_id": draft_id,
            "human_notified": notified,
            "human_notification_required": not notified,
            "human_notification_error": notification_error,
            "security_event_id": security_id,
        }
    recipient = str(payload.get("toAddress") or "").strip().lower()
    if not _recipient_allowed_in_test_mode(recipient):
        security_id = _record_security(registry, "test_recipient_not_allowed", context)
        registry.update_outbox_policy(operation_id, message_type=decision.effective_type)
        registry.finalize_response_attempt(
            operation_id, outcome="manual_review", reason_codes=["test_recipient_not_allowed"],
        )
        return {"action": "blocked", "reason": "test_recipient_not_allowed", "security_event_id": security_id}

    try:
        canonical_kind = _assert_pre_offer_kind(decision.effective_type)
        _validate_transport_payload(payload, threaded=threaded)
    except Exception as exc:
        reason = f"pre_post_validation:{exc.__class__.__name__}:{str(exc)[:160]}"
        registry.finalize_response_attempt(
            operation_id, outcome="validation_failed", reason_codes=[reason],
            rejected_body=str(payload.get("content") or ""), validation_errors=[str(exc)],
        )
        return {"action": "blocked", "reason": reason}
    if not registry.mark_content_validated(operation_id):
        security_id = _record_security(registry, "content_validation_transition_failed", context)
        return {"action": "blocked", "reason": "content_validation_transition_failed", "security_event_id": security_id}
    # Re-read once more immediately before changing the claim to in-progress.
    # This closes the race between initial validation/content classification and
    # the actual provider call if a deal, recipient, thread or durable policy is
    # changed concurrently.
    latest_registry, latest_event, latest_deal, latest_type_or_error = _durable_transport_state(
        context,
        payload=payload,
        account_id=account_id,
        source_message_id=source_message_id,
        threaded=threaded,
    )
    if (
        latest_registry is None
        or latest_event is None
        or latest_deal is None
        or normalize_message_type(latest_type_or_error) != canonical_kind
        or str(latest_deal.get("updated_at") or "") != durable_deal_version
    ):
        reason = (
            "durable_deal_changed_before_send"
            if latest_deal is not None
            and str(latest_deal.get("updated_at") or "") != durable_deal_version
            else (
                latest_type_or_error
                if latest_type_or_error not in MESSAGE_TYPES
                else "durable_policy_changed_before_send"
            )
        )
        security_id = _record_security(registry, reason, context, reason=reason)
        registry.update_outbox_policy(operation_id, message_type=decision.effective_type)
        registry.finalize_response_attempt(
            operation_id, outcome="manual_review", reason_codes=[str(reason)],
        )
        notified, notification_error = _notify_human(
            human_notifier,
            {
                "event": "transport_manual_review_required",
                "deal_id": str(context.get("deal_id") or ""),
                "source_key": str(context.get("source_key") or ""),
                "reason": reason,
            },
        )
        return {
            "action": "blocked",
            "reason": reason,
            "security_event_id": security_id,
            "human_notified": notified,
            "human_notification_error": notification_error,
        }
    if not registry.mark_transport_starting(operation_id):
        security_id = _record_security(registry, "pre_transport_transition_lost", context)
        return {"action": "blocked", "reason": "pre_transport_transition_lost", "security_event_id": security_id}
    assert_restore_reconciled()
    if not registry.mark_outbox_in_progress(operation_id):
        security_id = _record_security(registry, "pre_transport_claim_lost", context)
        return {"action": "blocked", "reason": "pre_transport_claim_lost", "security_event_id": security_id}
    try:
        if threaded:
            source_id = str(source_message_id or "").strip()
            if not source_id:
                raise ValueError("source_message_id_required")
            status, response = poster.send_reply(account_id, source_id, payload)
        else:
            status, response = poster.send_message(account_id, payload)
    except Exception as exc:
        registry.mark_outbox_outcome_unknown(
            operation_id,
            error=f"transport_exception:{exc.__class__.__name__}",
        )
        security_id = _record_security(
            registry,
            "send_outcome_unknown",
            context,
            error=exc.__class__.__name__,
        )
        return {
            "action": "error",
            "status": 599,
            "reason": "send_outcome_unknown",
            "outcome_unknown": True,
            "security_event_id": security_id,
            "message_kind": canonical_kind,
        }
    response = response if isinstance(response, dict) else {}
    external_id = _external_message_id(response)
    if status in SUCCESS_STATUSES:
        if not external_id:
            registry.mark_outbox_outcome_unknown(operation_id, error="provider_accept_without_message_id")
            security_id = _record_security(registry, "send_outcome_unknown", context, status=status)
            return {
                "action": "error", "status": status, "reason": "provider_evidence_missing",
                "outcome_unknown": True, "security_event_id": security_id, "message_kind": canonical_kind,
            }
        if not registry.complete_outbox(operation_id, external_message_id=external_id):
            registry.mark_outbox_outcome_unknown(operation_id, error="sent_but_outbox_commit_not_confirmed")
            security_id = _record_security(registry, "send_outcome_unknown", context)
            return {
                "action": "error",
                "status": 599,
                "reason": "send_outcome_unknown",
                "outcome_unknown": True,
                "security_event_id": security_id,
                "message_kind": canonical_kind,
            }
        return {
            "action": "sent",
            "status": status,
            "external_message_id": external_id,
            "response": response,
            "message_kind": canonical_kind,
        }
    if status in {0, 599}:
        registry.mark_outbox_outcome_unknown(operation_id, error="transport_timeout_or_unavailable")
        _record_security(registry, "send_outcome_unknown", context, status=status)
    else:
        retryable = status == 429 or status >= 500
        registry.finalize_response_attempt(
            operation_id,
            outcome="retry_scheduled" if retryable else "validation_failed",
            reason_codes=[str(response.get("error") or f"http_status_{status}")],
            provider_evidence={"provider_rejected": True, "http_status": status},
        )
    return {
        "action": "error",
        "status": status,
        "response": response,
        "retryable": status == 429 or status >= 500,
        "message_kind": canonical_kind,
    }


class HttpPreOfferPoster(HttpDraftPoster):
    """HTTP adapter exposing only the two Zoho send operations needed here."""

    def _post_json(self, url: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Zoho-oauthtoken {self._access_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read().decode("utf-8", errors="replace")
                return response.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                return exc.code, json.loads(raw)
            except json.JSONDecodeError:
                return exc.code, {"error": raw[:500]}
        except (urllib.error.URLError, TimeoutError):
            return 599, {"error": "transport_timeout_or_unavailable"}

    def send_reply(self, account_id: str, source_message_id: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        return self._post_json(f"{self.api_base}/accounts/{account_id}/messages/{source_message_id}", payload)

    def send_message(self, account_id: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        return self._post_json(f"{self.api_base}/accounts/{account_id}/messages", payload)
