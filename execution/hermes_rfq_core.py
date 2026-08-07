#!/usr/bin/env python3
"""Deterministic safety primitives shared by the Hermes Orchesta RFQ flows.

This module deliberately contains no mail transport and no model calls.  It is
the small, restart-safe boundary between orchestration and external adapters:
state transitions, operation idempotency, attachment limits,
draft edit detection, offer fingerprints, bridge health, retries, and redacted
structured logging.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sqlite3
import time
import unicodedata
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parseaddr
from pathlib import Path
from typing import Any, Callable, Iterable

from sqlite_migrations import Migration, add_column_if_missing, migration_lock, run_migrations


MESSAGE_STATES = {
    "received", "precheck_skipped", "analysis_pending", "identity_resolved",
    "classified", "safety_passed", "blocked", "action_planned", "done",
    "awaiting_human", "awaiting_customer",
}
# Spec section 18: deal-level states. The new deal state model.
DEAL_STATES = {
    "received", "security_checked", "classified", "awaiting_customer",
    "awaiting_human", "ready_for_offer", "offer_generated", "sent",
    "blocked", "closed",
}
# Reason code for unresolved tenant_id (spec section 2). Kept here as a
# constant so the poller and tests reference one source of truth.
REASON_TENANT_NOT_RESOLVED = "tenant_not_resolved"
# Reason code when the LLM classifier fails twice to return valid JSON
# (spec section 9). The case is never marked done.
REASON_LLM_CLASSIFICATION_FAILED = "llm_classification_failed"
# Reason code when the LLM reply writer fails twice to return valid JSON
# (spec section 12). The case is never marked done.
REASON_LLM_REPLY_FAILED = "llm_reply_failed"
# Spec section 18: generic LLM failure reason (classification or reply).
REASON_LLM_FAILED = "llm_failed"
# Spec section 18: offer generation failure reason.
REASON_OFFER_GENERATION_FAILED = "offer_generation_failed"
# Reason code when the ready-text pre-send control rejects the reply
# (spec section 17). The reply is never auto-sent.
REASON_REPLY_VALIDATION_FAILED = "reply_validation_failed"
DRAFT_STATES = {
    "not_needed", "planned", "creating", "created", "update_pending",
    "updated", "human_edited", "retryable_failed", "permanent_failed",
}
OFFER_STATES = {
    "not_applicable", "waiting_for_data", "waiting_for_review", "generating",
    "generated", "attachment_pending", "attached", "superseded", "sent_manually",
}
CRM_STATES = {
    "not_needed", "lookup_pending", "matched", "new_record_pending", "written",
    "deferred_offline", "conflict", "failed",
}
OPERATION_STATUSES = {
    "planned", "in_progress", "outcome_unknown", "succeeded", "retryable_failed", "permanent_failed"
}
PUBLIC_EMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com",
    "icloud.com", "me.com", "onet.pl", "wp.pl", "interia.pl", "o2.pl", "yahoo.com",
}


def _migration_001_operation_safety_fields(connection: sqlite3.Connection) -> None:
    for column, declaration in (
        ("message_type", "TEXT"),
        ("recipient", "TEXT"),
        ("owner", "TEXT"),
        ("lease_expires_at", "TEXT"),
        ("outcome", "TEXT"),
    ):
        add_column_if_missing(connection, "operations", column, declaration)


STATE_STORE_MIGRATIONS = (
    Migration(1, "operation_safety_fields", _migration_001_operation_safety_fields),
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run_id(prefix: str = "HRFQ") -> str:
    return f"{prefix}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: Any) -> str:
    if isinstance(value, bytes):
        return hashlib.sha256(value).hexdigest()
    raw = value if isinstance(value, str) else canonical_json(value)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "y"}


@dataclass(frozen=True)
class SafetySwitches:
    drafts_enabled: bool = False
    offer_generation_enabled: bool = False
    attachment_extraction_enabled: bool = True
    crm_write_enabled: bool = True
    shadow_mode: bool = True
    # Spec section 24 deployment flags (kill switches). Defaults match the
    # spec's recommended production values.
    tenant_required: bool = True
    llm_intent_enabled: bool = False
    llm_shadow_mode: bool = True
    auto_reply_low_risk: bool = False
    reply_kill_switch: bool = False

    @classmethod
    def from_env(cls) -> "SafetySwitches":
        return cls(
            drafts_enabled=env_flag("HERMES_DRAFTS_ENABLED", False),
            offer_generation_enabled=env_flag("HERMES_OFFER_GENERATION_ENABLED", False),
            attachment_extraction_enabled=env_flag("HERMES_ATTACHMENT_EXTRACTION_ENABLED", True),
            crm_write_enabled=env_flag("HERMES_CRM_WRITE_ENABLED", True),
            shadow_mode=env_flag("HERMES_SHADOW_MODE", True),
            tenant_required=env_flag("HERMES_TENANT_REQUIRED", True),
            llm_intent_enabled=env_flag("HERMES_LLM_INTENT_ENABLED", False),
            llm_shadow_mode=env_flag("HERMES_LLM_SHADOW_MODE", True),
            auto_reply_low_risk=env_flag("HERMES_AUTO_REPLY_LOW_RISK", False),
            reply_kill_switch=env_flag("HERMES_REPLY_KILL_SWITCH", False),
        )

    @property
    def auto_reply_allowed(self) -> bool:
        """True only when low-risk auto-reply is enabled AND the kill switch is off."""
        return self.auto_reply_low_risk and not self.reply_kill_switch


def _strip_invisible(value: str) -> str:
    return "".join(char for char in value if unicodedata.category(char) not in {"Cf", "Cc"} or char in "\n\r\t")


def visible_html_to_text(value: str) -> str:
    """Convert visible HTML to text while keeping table cell/row boundaries."""
    text = re.sub(r"(?is)<(?:blockquote|div\b[^>]*class=['\"][^'\"]*(?:gmail_quote|quoted)[^'\"]*['\"][^>]*)>.*?</(?:blockquote|div)>", " ", value or "")
    text = re.sub(r"(?is)<(script|style|noscript|template)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?is)<[^>]*(?:hidden|aria-hidden\s*=\s*['\"]true['\"]|display\s*:\s*none|visibility\s*:\s*hidden)[^>]*>.*?</[^>]+>", " ", text)
    text = re.sub(r"(?i)</?(?:br|p|div|li)\s*/?>", "\n", text)
    text = re.sub(r"(?i)<(?:td|th)\b[^>]*>", "\t", text)
    text = re.sub(r"(?i)</(?:td|th)\s*>", "\t", text)
    text = re.sub(r"(?i)</tr\s*>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", "", text)
    return _strip_invisible(unicodedata.normalize("NFKC", html.unescape(text)))


def newest_message_part(value: str, *, is_html: bool = False, max_chars: int = 12000) -> str:
    text = visible_html_to_text(value) if is_html else _strip_invisible(unicodedata.normalize("NFKC", value or ""))
    text = re.sub(r"(?m)^\s*>.*$", "", text)
    text = re.split(
        r"(?ims)^\s*(?:-----original message-----|original message|forwarded message|on .{0,180} wrote:|w dniu .{0,180} napisa[łl][a]?:|from\s*:|od\s*:)",
        text,
        maxsplit=1,
    )[0]
    return re.sub(r"\n{3,}", "\n\n", text).strip()[:max_chars]


def normalize_email(value: str) -> str:
    display, address = parseaddr(value or "")
    candidate = (address or value or "").strip().strip(".,:;()[]<>").lower()
    return candidate


def redact(value: Any, *, max_text: int = 220) -> Any:
    """Recursively redact secrets and keep logs to short previews."""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(word in lowered for word in ("token", "password", "secret", "cookie", "authorization", "api_key", "private_key")):
                result[key] = "[REDACTED]"
            else:
                result[key] = redact(item, max_text=max_text)
        return result
    if isinstance(value, list):
        return [redact(item, max_text=max_text) for item in value[:20]]
    if isinstance(value, str):
        text = re.sub(r"(?i)bearer\s+[A-Za-z0-9._~-]+", "Bearer [REDACTED]", value)
        text = re.sub(r"(?i)(password|token|secret|api[_ -]?key)\s*[:=]\s*[^\s,;]+", r"\1=[REDACTED]", text)
        text = re.sub(r"\b(?:\d[ -]?){13,19}\b", "[CARD_REDACTED]", text)
        return text[:max_text] + ("…" if len(text) > max_text else "")
    return value


def log_event(*, run_id_value: str, message_id: str = "", stage: str, action: str, status: str, reason_code: str = "", duration_ms: int = 0, error_type: str = "", **details: Any) -> dict[str, Any]:
    return redact({
        "run_id": run_id_value, "message_id": message_id, "stage": stage,
        "action": action, "status": status, "reason_code": reason_code,
        "duration_ms": duration_ms, "error_type": error_type, **details,
    })


class HermesStateStore:
    """SQLite state store with durable operations and append-only transitions."""

    def __init__(self, path: str | Path = ".tmp/hermes-rfq-state/state.sqlite3") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA busy_timeout=30000")
        with migration_lock(self.path):
            self.connection.execute("PRAGMA journal_mode=WAL")
            self._create_schema()
            self.schema_version = run_migrations(
                self.connection,
                namespace="hermes_state_store",
                migrations=STATE_STORE_MIGRATIONS,
            )

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS messages (
              message_id TEXT PRIMARY KEY, thread_id TEXT, correlation_id TEXT,
              status TEXT NOT NULL, record_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS operations (
              id INTEGER PRIMARY KEY AUTOINCREMENT, message_id TEXT NOT NULL,
              thread_id TEXT, correlation_id TEXT, action_type TEXT NOT NULL,
              status TEXT NOT NULL, external_draft_id TEXT, offer_version INTEGER,
              input_hash TEXT, content_hash TEXT, retry_count INTEGER NOT NULL DEFAULT 0,
              last_error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              UNIQUE(message_id, action_type)
            );
            CREATE TABLE IF NOT EXISTS state_transitions (
              id INTEGER PRIMARY KEY AUTOINCREMENT, entity_type TEXT NOT NULL,
              entity_id TEXT NOT NULL, previous_status TEXT, new_status TEXT NOT NULL,
              reason_code TEXT NOT NULL, timestamp TEXT NOT NULL, run_id TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS offers (
              offer_id TEXT PRIMARY KEY, deal_id TEXT NOT NULL, version INTEGER NOT NULL,
              pricing_version TEXT NOT NULL, pricing_hash TEXT NOT NULL, scope_hash TEXT NOT NULL,
              customer_data_hash TEXT NOT NULL, status TEXT NOT NULL, supersedes_version INTEGER,
              offer_json TEXT NOT NULL, created_at TEXT NOT NULL,
              UNIQUE(deal_id, version)
            );
            CREATE TABLE IF NOT EXISTS bridge_health (
              bridge_name TEXT PRIMARY KEY, last_success_at TEXT, last_failure_at TEXT,
              status TEXT NOT NULL, latency_ms INTEGER, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS metrics (
              name TEXT PRIMARY KEY, value INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_operations_lookup ON operations(message_id, action_type, status);
            CREATE INDEX IF NOT EXISTS idx_transitions_entity ON state_transitions(entity_type, entity_id, id);
            """
        )

    def close(self) -> None:
        self.connection.close()

    def transition(self, entity_type: str, entity_id: str, new_status: str, *, reason_code: str, run_id_value: str, allowed: set[str]) -> None:
        if new_status not in allowed:
            raise ValueError(f"invalid {entity_type} status: {new_status}")
        table = "messages" if entity_type == "message" else None
        previous = None
        if table:
            row = self.connection.execute("SELECT status FROM messages WHERE message_id = ?", (entity_id,)).fetchone()
            previous = row["status"] if row else None
            now = utc_now()
            self.connection.execute(
                "INSERT INTO messages(message_id,status,record_json,created_at,updated_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(message_id) DO UPDATE SET status=excluded.status,updated_at=excluded.updated_at",
                (entity_id, new_status, "{}", now, now),
            )
        self.connection.execute(
            "INSERT INTO state_transitions(entity_type,entity_id,previous_status,new_status,reason_code,timestamp,run_id) VALUES(?,?,?,?,?,?,?)",
            (entity_type, entity_id, previous, new_status, reason_code, utc_now(), run_id_value),
        )

    def get_message(self, message_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM messages WHERE message_id = ?", (str(message_id),)).fetchone()
        return dict(row) if row else None

    def is_processed(self, message_id: str) -> bool:
        row = self.get_message(message_id)
        return bool(row and row["status"] in {"done", "precheck_skipped"})

    def record_message(self, message_id: str, record: dict[str, Any], *, status: str = "done", run_id_value: str = "") -> None:
        now = utc_now()
        payload = redact(record)
        if not run_id_value:
            run_id_value = str(record.get("processed_run_id") or run_id("HRFQ"))
        previous_row = self.connection.execute("SELECT status FROM messages WHERE message_id = ?", (str(message_id),)).fetchone()
        previous = previous_row["status"] if previous_row else None
        self.connection.execute(
            "INSERT INTO messages(message_id,thread_id,correlation_id,status,record_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(message_id) DO UPDATE SET thread_id=excluded.thread_id,correlation_id=excluded.correlation_id,status=excluded.status,record_json=excluded.record_json,updated_at=excluded.updated_at",
            (str(message_id), str(record.get("thread_id") or ""), str(record.get("correlation_id") or ""), status, canonical_json(payload), now, now),
        )
        self.connection.execute(
            "INSERT INTO state_transitions(entity_type,entity_id,previous_status,new_status,reason_code,timestamp,run_id) VALUES(?,?,?,?,?,?,?)",
            ("message", str(message_id), previous, status, str(record.get("reason_code") or "message_processed"), utc_now(), run_id_value),
        )

    def mark_processed(self, message_id: str, record: dict[str, Any] | None = None) -> dict[str, Any]:
        record = dict(record or {})
        failed = str(record.get("draft_action") or "").endswith("failed") or str(record.get("status") or "").endswith("failed")
        status = "analysis_pending" if failed else "done"
        self.record_message(str(message_id), record, status=status, run_id_value=str(record.get("processed_run_id") or run_id()))
        record.setdefault("processed_at", utc_now())
        return record

    def plan_operation(self, message_id: str, action_type: str, *, thread_id: str = "", correlation_id: str = "", input_hash: str = "", content_hash: str = "") -> dict[str, Any]:
        now = utc_now()
        try:
            self.connection.execute(
                "INSERT INTO operations(message_id,thread_id,correlation_id,action_type,status,input_hash,content_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (str(message_id), thread_id, correlation_id, action_type, "planned", input_hash, content_hash, now, now),
            )
        except sqlite3.IntegrityError:
            pass
        row = self.connection.execute("SELECT * FROM operations WHERE message_id=? AND action_type=?", (str(message_id), action_type)).fetchone()
        return dict(row)

    def operation(self, message_id: str, action_type: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM operations WHERE message_id=? AND action_type=?", (str(message_id), action_type)).fetchone()
        return dict(row) if row else None

    def operation_succeeded(self, message_id: str, action_type: str) -> bool:
        row = self.operation(message_id, action_type)
        return bool(row and row["status"] == "succeeded" and row["external_draft_id"])

    def update_operation(self, message_id: str, action_type: str, status: str, *, external_draft_id: str = "", offer_version: int | None = None, last_error: str = "", increment_retry: bool = False) -> dict[str, Any]:
        if status not in OPERATION_STATUSES:
            raise ValueError(f"invalid operation status: {status}")
        retry_sql = ", retry_count = retry_count + 1" if increment_retry else ""
        self.connection.execute(
            f"UPDATE operations SET status=?, external_draft_id=COALESCE(NULLIF(?,''),external_draft_id), offer_version=COALESCE(?,offer_version), last_error=?, updated_at=?{retry_sql} WHERE message_id=? AND action_type=?",
            (status, external_draft_id, offer_version, last_error[:500], utc_now(), str(message_id), action_type),
        )
        row = self.operation(message_id, action_type)
        if row is None:
            raise KeyError(f"operation not planned: {message_id}/{action_type}")
        return row

    def list_transitions(self, entity_type: str, entity_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM state_transitions WHERE entity_type=? AND entity_id=? ORDER BY id", (entity_type, entity_id)).fetchall()
        return [dict(row) for row in rows]

    def metric_increment(self, name: str, amount: int = 1) -> int:
        now = utc_now()
        self.connection.execute("INSERT INTO metrics(name,value,updated_at) VALUES(?,?,?) ON CONFLICT(name) DO UPDATE SET value=value+excluded.value,updated_at=excluded.updated_at", (name, amount, now))
        return int(self.connection.execute("SELECT value FROM metrics WHERE name=?", (name,)).fetchone()[0])

    def save_bridge_health(self, bridge_name: str, health: "BridgeHealth") -> None:
        now = utc_now()
        self.connection.execute(
            "INSERT INTO bridge_health(bridge_name,last_success_at,last_failure_at,status,latency_ms,updated_at) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(bridge_name) DO UPDATE SET last_success_at=excluded.last_success_at,last_failure_at=excluded.last_failure_at,status=excluded.status,latency_ms=excluded.latency_ms,updated_at=excluded.updated_at",
            (bridge_name, datetime.fromtimestamp(health.last_success_at, timezone.utc).isoformat() if health.last_success_at else None, datetime.fromtimestamp(health.last_failure_at, timezone.utc).isoformat() if health.last_failure_at else None, health.status, health.latency_ms, now),
        )

    def save_offer(self, *, deal_id: str, offer_id: str, version: int, pricing_version: str, pricing_hash: str, scope_hash: str, customer_data_hash: str, status: str, offer_json: dict[str, Any], supersedes_version: int | None = None) -> dict[str, Any]:
        """Insert an immutable offer version; never overwrite an older version."""
        now = utc_now()
        self.connection.execute(
            "INSERT INTO offers(offer_id,deal_id,version,pricing_version,pricing_hash,scope_hash,customer_data_hash,status,supersedes_version,offer_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (offer_id, deal_id, int(version), pricing_version, pricing_hash, scope_hash, customer_data_hash, status, supersedes_version, canonical_json(offer_json), now),
        )
        row = self.connection.execute("SELECT * FROM offers WHERE offer_id=?", (offer_id,)).fetchone()
        return dict(row)

    def get_offer(self, offer_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM offers WHERE offer_id=?", (offer_id,)).fetchone()
        return dict(row) if row else None

    def attachment_matches_offer(self, offer_id: str, *, version: int, pricing_hash: str, scope_hash: str, customer_data_hash: str) -> bool:
        offer = self.get_offer(offer_id)
        return bool(
            offer
            and int(offer["version"]) == int(version)
            and offer["pricing_hash"] == pricing_hash
            and offer["scope_hash"] == scope_hash
            and offer["customer_data_hash"] == customer_data_hash
        )


@dataclass
class BridgeHealth:
    ttl_seconds: int = 300
    status: str = "offline"
    last_success_at: float | None = None
    last_failure_at: float | None = None
    latency_ms: int | None = None

    def check(self, probe: Callable[[], Any]) -> str:
        started = time.monotonic()
        try:
            probe()
        except Exception:
            self.last_failure_at = time.time()
            self.status = "degraded" if self.last_success_at and time.time() - self.last_success_at <= self.ttl_seconds else "offline"
        else:
            self.last_success_at = time.time()
            self.latency_ms = round((time.monotonic() - started) * 1000)
            self.status = "online"
        return self.status

    def usable(self) -> bool:
        return bool(self.last_success_at and time.time() - self.last_success_at <= self.ttl_seconds and self.status in {"online", "degraded"})


def bridge_customer_context(health: BridgeHealth) -> dict[str, Any]:
    """Return only facts safe to expose to the response composer."""
    if not health.usable():
        return {
            "runtime_mode": "offline",
            "crm_checked": False,
            "calendar_checked": False,
            "calendar_instruction": "Poproś klienta o podanie dogodnego terminu; nie podawaj konkretnych godzin.",
        }
    return {
        "runtime_mode": "normal" if health.status == "online" else "degraded",
        "crm_checked": health.status == "online",
        "calendar_checked": health.status == "online",
        "calendar_instruction": "Podaj termin dopiero po odczycie kalendarza.",
    }


@dataclass(frozen=True)
class AttachmentLimits:
    max_file_bytes: int = 25 * 1024 * 1024
    max_total_bytes: int = 50 * 1024 * 1024
    max_files: int = 10
    max_pdf_pages: int = 50
    max_image_pixels: int = 40_000_000
    max_memory_bytes: int = 512 * 1024 * 1024
    max_processing_seconds: int = 120

    @classmethod
    def from_env(cls) -> "AttachmentLimits":
        def integer(name: str, default: int) -> int:
            try:
                return max(1, int(os.environ.get(name, default)))
            except ValueError:
                return default
        return cls(
            max_file_bytes=integer("HERMES_ATTACHMENT_MAX_FILE_BYTES", cls.max_file_bytes),
            max_total_bytes=integer("HERMES_ATTACHMENT_MAX_TOTAL_BYTES", cls.max_total_bytes),
            max_files=integer("HERMES_ATTACHMENT_MAX_FILES", cls.max_files),
            max_pdf_pages=integer("HERMES_ATTACHMENT_MAX_PDF_PAGES", cls.max_pdf_pages),
            max_image_pixels=integer("HERMES_ATTACHMENT_MAX_IMAGE_PIXELS", cls.max_image_pixels),
            max_memory_bytes=integer("HERMES_ATTACHMENT_MAX_MEMORY_BYTES", cls.max_memory_bytes),
            max_processing_seconds=integer("HERMES_ATTACHMENT_MAX_PROCESSING_SECONDS", cls.max_processing_seconds),
        )


def inspect_attachment(path: str | Path, *, limits: AttachmentLimits | None = None) -> dict[str, Any]:
    """Inspect bytes without executing or fully extracting untrusted content."""
    limits = limits or AttachmentLimits.from_env()
    file_path = Path(path)
    name = file_path.name
    suffix = file_path.suffix.lower()
    size = file_path.stat().st_size if file_path.exists() else 0
    data = file_path.read_bytes()[:4096] if file_path.exists() else b""
    result: dict[str, Any] = {"filename": name, "size_bytes": size, "status": "safe", "reason_code": "", "sha256": ""}
    if not file_path.exists():
        result.update(status="blocked", reason_code="attachment_missing")
        return result
    if size > limits.max_file_bytes:
        result.update(status="blocked", reason_code="attachment_too_large")
        return result
    result["sha256"] = sha256_file(file_path)
    if suffix in {".exe", ".dll", ".scr", ".msi", ".dmg", ".pkg", ".js", ".vbs", ".ps1", ".bat", ".cmd", ".com", ".jar", ".zip", ".rar", ".7z", ".tar", ".gz"}:
        result.update(status="blocked", reason_code="attachment_executable_or_archive")
        return result
    if data.startswith(b"MZ") or data.startswith(b"#!"):
        result.update(status="blocked", reason_code="attachment_executable_magic")
        return result
    if suffix == ".pdf":
        if not data.startswith(b"%PDF-"):
            result.update(status="blocked", reason_code="attachment_magic_extension_mismatch")
            return result
        pdf_bytes = file_path.read_bytes()
        if any(marker in pdf_bytes for marker in (b"/JavaScript", b"/JS", b"/OpenAction", b"/AA")):
            result.update(status="blocked", reason_code="attachment_pdf_javascript")
            return result
        try:
            import fitz  # type: ignore
            with fitz.open(file_path) as document:
                result["pages"] = int(document.page_count)
                if document.is_encrypted:
                    result.update(status="blocked", reason_code="attachment_encrypted")
                elif document.page_count > limits.max_pdf_pages:
                    result.update(status="blocked", reason_code="attachment_pdf_pages_limit")
        except Exception:
            result.update(status="review", reason_code="attachment_pdf_probe_failed")
        return result
    if suffix in {".docx", ".xlsx", ".pptx"}:
        if not data.startswith(b"PK"):
            result.update(status="blocked", reason_code="attachment_magic_extension_mismatch")
            return result
        try:
            with zipfile.ZipFile(file_path) as archive:
                names = set(archive.namelist())
                uncompressed_size = sum(max(0, int(info.file_size)) for info in archive.infolist())
                if uncompressed_size > limits.max_memory_bytes:
                    result.update(status="blocked", reason_code="attachment_archive_memory_limit")
                elif any(name.startswith("xl/vbaProject") or name.startswith("word/vbaProject") for name in names):
                    result.update(status="blocked", reason_code="attachment_office_macro")
                elif any(name.startswith("embeddings/") for name in names):
                    result.update(status="review", reason_code="attachment_embedded_file")
                elif any("externalLink" in name for name in names):
                    result.update(status="review", reason_code="attachment_external_reference")
        except (OSError, zipfile.BadZipFile):
            result.update(status="blocked", reason_code="attachment_archive_invalid")
        return result
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".tif", ".tiff", ".webp"}:
        try:
            from PIL import Image  # type: ignore
            with Image.open(file_path) as image:
                pixels = int(image.width) * int(image.height)
                result["pixels"] = pixels
                if pixels > limits.max_image_pixels:
                    result.update(status="blocked", reason_code="attachment_image_pixels_limit")
        except Exception:
            result.update(status="review", reason_code="attachment_image_probe_failed")
        return result
    result.update(status="unsupported", reason_code="attachment_type_unsupported")
    return result


def draft_update_decision(last_system_hash: str, current_content: str, *, system_content: str | None = None) -> tuple[str, str]:
    current_hash = sha256_text(current_content)
    if system_content is not None and not last_system_hash:
        last_system_hash = sha256_text(system_content)
    if last_system_hash and current_hash != last_system_hash:
        return "human_edited", current_hash
    return "update_allowed", current_hash


def offer_fingerprints(*, pricing_version: str, pricing: Any, scope: Any, customer_data: Any) -> dict[str, str]:
    return {
        "pricing_version": pricing_version,
        "pricing_hash": sha256_text(pricing),
        "scope_hash": sha256_text(scope),
        "customer_data_hash": sha256_text(customer_data),
    }


def next_offer_version(existing_versions: Iterable[int]) -> int:
    return max([int(value) for value in existing_versions] or [0]) + 1


def retry_decision(status_code: int | None, retry_count: int, max_retries: int = 5) -> tuple[str, float]:
    if status_code in {429, 500, 502, 503, 504}:
        if retry_count >= max_retries:
            return "permanent_failed", 0.0
        # Jitter is intentionally bounded; callers may inject a clock/sleeper.
        return "retryable_failed", min(60.0, (2 ** retry_count) + ((retry_count * 17) % 100) / 100)
    if status_code is not None and 400 <= status_code < 500:
        return "permanent_failed", 0.0
    return "retryable_failed", 0.5


def crm_match_candidates(*, email: str, company: str, domain: str, subject: str, context: str, deals: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a conservative CRM decision; domain alone never selects a deal."""
    email = normalize_email(email)
    exact_email = [deal for deal in deals if normalize_email(str(deal.get("email") or "")) == email and email]
    if len(exact_email) == 1:
        return {"status": "matched", "record": exact_email[0], "reason_code": "crm_exact_email"}
    exact_company = [deal for deal in deals if str(deal.get("company") or "").strip().casefold() == company.strip().casefold() and company]
    if len(exact_company) == 1:
        return {"status": "matched", "record": exact_company[0], "reason_code": "crm_exact_company"}
    domain_matches = [deal for deal in deals if str(deal.get("domain") or "").lower() == domain.lower() and domain.lower() not in PUBLIC_EMAIL_DOMAINS]
    if len(domain_matches) > 1:
        return {"status": "conflict", "candidates": domain_matches, "reason_code": "crm_multiple_deals"}
    if len(domain_matches) == 1 and company and str(domain_matches[0].get("company") or "").casefold() == company.casefold() and subject and context:
        return {"status": "matched", "record": domain_matches[0], "reason_code": "crm_domain_company_context"}
    return {"status": "new_record_pending", "reason_code": "crm_new_contact"}


RESPONSE_POLICIES = {
    "new_email": {"use_when": "safe new RFQ inquiry", "max_questions": 2, "cta": "one next discovery step", "human": "weak fit or unknown intent"},
    "existing_client": {"use_when": "known client with safe request", "max_questions": 2, "cta": "confirm the requested change", "human": "scope conflict"},
    "existing_thread": {"use_when": "valid reply relation", "max_questions": 2, "cta": "continue the agreed next step", "human": "thread identity mismatch"},
    "price_request": {"use_when": "customer asks for price", "max_questions": 2, "cta": "collect only missing pricing inputs", "human": "pricing unavailable"},
    "unsupported_file": {"use_when": "attachment unsupported or blocked", "max_questions": 0, "cta": "request a safe alternative or human review", "human": "always"},
    "partial_file": {"use_when": "safe file only partly readable", "max_questions": 2, "cta": "request the smallest missing detail", "human": "material ambiguity"},
    "limited_mode": {"use_when": "CRM or calendar is unavailable", "max_questions": 1, "cta": "ask for a convenient time without inventing slots", "human": "external bridge required"},
    "weak_fit": {"use_when": "fit is weak", "max_questions": 0, "cta": "none until human decision", "human": "always"},
    "complaint": {"use_when": "complaint intent", "max_questions": 2, "cta": "acknowledge and collect case details", "human": "separate complaint queue"},
    "security_report": {"use_when": "security intent", "max_questions": 0, "cta": "acknowledge receipt", "human": "security owner"},
    "data_subject_request": {"use_when": "data deletion/access request", "max_questions": 0, "cta": "route to privacy owner", "human": "privacy owner"},
    "billing_issue": {"use_when": "payment or billing issue", "max_questions": 2, "cta": "collect the invoice reference", "human": "finance owner"},
    "scope_change": {"use_when": "scope changed after offer", "max_questions": 2, "cta": "confirm a new offer version", "human": "new version review"},
}


def self_test() -> int:
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        store = HermesStateStore(Path(tmp) / "state.sqlite3")
        store.plan_operation("m1", "customer_draft", input_hash="a", content_hash="b")
        store.update_operation("m1", "customer_draft", "succeeded", external_draft_id="d1")
        assert store.operation_succeeded("m1", "customer_draft")
        store.record_message("m1", {"thread_id": "t1", "correlation_id": "c1"}, status="done", run_id_value="r1")
        assert store.is_processed("m1")
        assert len(store.list_transitions("message", "m1")) >= 1
    assert retry_decision(429, 0)[0] == "retryable_failed"
    assert next_offer_version([1, 3]) == 4
    print("hermes_rfq_core self-test: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(self_test())
