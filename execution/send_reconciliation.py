#!/usr/bin/env python3
"""Send-marker helpers for Zoho pre-offer auto-send reconciliation.

Zoho Mail rejects unknown top-level JSON keys with EXTRA_KEY_FOUND_IN_JSON
(HTTP 404). New messages therefore keep only a non-visible HTML comment. The
old visible reference is still recognized during reconciliation, but is never
added to new customer content.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Callable

DEFAULT_MAX_AGE_SECONDS = 7 * 24 * 3600
MARKER_PREFIX = "hermes-send-marker:"
VISIBLE_MARKER_PREFIX = "HERMESREF"


def build_send_marker(message_id: str, response_hash: str) -> dict[str, Any]:
    return {"message_id": str(message_id), "response_hash": str(response_hash)}


def marker_token(operation_marker: dict[str, Any] | str) -> str:
    if isinstance(operation_marker, str):
        text = operation_marker.strip()
        if text.startswith(MARKER_PREFIX):
            return text
        return f"{MARKER_PREFIX}{text}" if text else ""
    mid = str((operation_marker or {}).get("message_id") or "").strip()
    rh = str((operation_marker or {}).get("response_hash") or "").strip()
    if not mid or not rh:
        return ""
    return f"{MARKER_PREFIX}{mid}:{rh}"


def visible_marker_token(operation_marker: dict[str, Any] | str) -> str:
    """Return a compact provider-stable reference without exposing operation data."""
    token = marker_token(operation_marker)
    if not token:
        return ""
    suffix = hashlib.sha256(token.encode("utf-8")).hexdigest()[:20].upper()
    return f"{VISIBLE_MARKER_PREFIX}{suffix}"


def marker_matches_content(operation_marker: dict[str, Any] | str, content: str) -> bool:
    """Match either the legacy HTML comment or the provider-stable visible reference."""
    token = marker_token(operation_marker)
    visible = visible_marker_token(operation_marker)
    blob = str(content or "")
    return bool(token and (token in blob or visible in blob))


def append_marker(payload: dict[str, Any], operation_marker: dict[str, Any] | str) -> dict[str, Any]:
    """Embed a non-visible marker only; strip illegal top-level keys."""
    payload = dict(payload or {})
    token = marker_token(operation_marker)
    content = str(payload.get("content") or "")
    if token:
        if str(payload.get("mailFormat") or "").lower() == "html" and token not in content:
            payload["content"] = f"{content}<!-- {token} -->"
    # Zoho rejects unknown keys (EXTRA_KEY_FOUND_IN_JSON → 404).
    payload.pop("markers", None)
    return payload


def operation_age_seconds(operation: dict[str, Any]) -> int | None:
    created = str((operation or {}).get("created_at") or "").strip()
    if not created:
        return None
    try:
        ts = datetime.fromisoformat(created.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - ts).total_seconds()))


def _content_blob(
    raw: dict[str, Any],
    fetch_content: Callable[[dict[str, Any]], dict[str, Any]] | None,
) -> tuple[str, bool]:
    parts: list[str] = []
    visibility_error = False
    for key in ("content", "summary", "subject"):
        value = raw.get(key)
        if value:
            parts.append(str(value))
    if fetch_content is not None:
        try:
            fetched = fetch_content(raw) or {}
        except Exception:
            fetched = {}
            visibility_error = True
        if isinstance(fetched, dict):
            data = fetched.get("data") if isinstance(fetched.get("data"), dict) else fetched
            for key in ("content", "summary", "subject"):
                value = data.get(key) if isinstance(data, dict) else None
                if value:
                    parts.append(str(value))
    return "\n".join(parts), visibility_error


def reconcile_sent_rows(
    operation: dict[str, Any],
    *,
    marker: dict[str, Any] | str,
    sent_rows: list[dict[str, Any]],
    row_matches: Callable[[dict[str, Any]], bool],
    fetch_content: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    row_id: Callable[[dict[str, Any]], str] | None = None,
    row_time: Callable[[dict[str, Any]], Any] | None = None,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
) -> dict[str, Any]:
    """Resolve an uncertain send only when the exact marker is in Sent content."""
    age = operation_age_seconds(operation)
    expired = age is not None and age >= int(max_age_seconds)
    token = marker_token(marker)
    conversation_hits = 0
    provider_visibility_error = False
    for raw in sent_rows or []:
        try:
            matched = bool(row_matches(raw))
        except Exception:
            matched = False
        if not matched:
            continue
        conversation_hits += 1
        if token:
            blob, visibility_error = _content_blob(raw, fetch_content)
            provider_visibility_error = provider_visibility_error or visibility_error
            if not marker_matches_content(marker, blob):
                continue
        external_id = str(row_id(raw)) if row_id else ""
        return {
            "resolved": True,
            "status": "matched",
            "retryable": False,
            "external_message_id": external_id,
            "age_seconds": age,
        }
    if provider_visibility_error:
        return {
            "resolved": False,
            "status": "provider_visibility_error",
            "retryable": False,
            "reason": "sent_content_unavailable",
            "age_seconds": age,
        }
    if conversation_hits and not token:
        # Without a token we refuse to guess — an older outbound in the same
        # thread must not count as this send.
        return {
            "resolved": False,
            "status": "ambiguous",
            "retryable": False,
            "reason": "marker_missing",
            "age_seconds": age,
        }
    return {
        "resolved": False,
        "status": "expired_unverified" if expired else "not_found",
        "retryable": not expired,
        "age_seconds": age,
    }
