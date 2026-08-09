#!/usr/bin/env python3
"""Shared durable deal and conversation-safety registry for Orchesta RFQ.

Mailbox and Google Sheets adapters use this registry to resolve logical deals,
keep customer facts, prevent duplicate artifacts and enforce durable automation
handoffs.  This module has no customer-facing transport capability.
"""

import hashlib
import json
import os
import re
import sqlite3
import socket
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from message_policy import MESSAGE_TYPES
from sqlite_migrations import Migration, add_column_if_missing, migration_lock, run_migrations

# Recycled test mailboxes (Sheets multi-scenario). Same email may open many
# independent deals without link ambiguity. Override via
# HERMES_TEST_CUSTOMER_EMAILS=comma-separated list. Empty string disables.
_DEFAULT_TEST_CUSTOMER_EMAILS = (
    "test-customer-1@example.invalid,"
    "test-customer-2@example.invalid"
)

ACTIVE_DEAL_STATUSES = {
    "new", "analysing", "draft_ready", "waiting_for_customer", "offer_ready",
    "review_required", "conversation_handoff", "ready_for_final_offer", "final_offer_failed",
}
# Terminal deals drop out of candidate matching, so a finished conversation
# stops shadowing the same sender's next inquiry.
TERMINAL_DEAL_STATUSES = {"completed", "lost", "cancelled", "closed", "won", "disqualified", "archived"}
STATUS_TO_SHEET = {
    "new": "nowy",
    "analysing": "w analizie",
    "draft_ready": "draft gotowy",
    "waiting_for_customer": "oczekuje na klienta",
    "offer_ready": "oferta gotowa",
    "review_required": "wymaga sprawdzenia",
    "conversation_handoff": "wymaga sprawdzenia",
    "ready_for_final_offer": "gotowe do oferty",
    "final_offer_failed": "wymaga sprawdzenia",
}
EMAIL_RE = re.compile(r"^[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9.-]+\.[A-Z]{2,63}$", re.I)
RE_PREFIX_RE = re.compile(r"^(?:(?:re|fw|fwd|odp|aw|sv)\s*:\s*)+", re.I)
TOKEN_RE = re.compile(r"[a-z0-9]{3,}", re.I)
TOPIC_STOPWORDS = {
    "dla", "oraz", "jest", "prosze", "proszę", "sprawie", "temat", "wiadomosc", "wiadomość",
    "orchesta", "rfq", "re", "fwd", "example", "treść", "tresc", "projekt",
}
MATERIAL_SCOPE_KEYS = {"mailbox_count", "crm", "inquiry_source", "has_sample_requests"}
MAX_CONVERSATION_MESSAGES = 7
# The pre-offer reply limit is separate from final-draft eligibility.
MAX_AUTOMATIC_REPLIES = 5
MAX_CONVERSATION_AGE_DAYS = 7

# Public policy constants make every score reproducible in tests and audits.
ROUTING_WEIGHT_THREAD_ID = 1.00
ROUTING_WEIGHT_REPLY_REFERENCE = 0.95
ROUTING_WEIGHT_EXACT_SUBJECT = 0.20
ROUTING_WEIGHT_STRONG_SEMANTIC = 0.65
ROUTING_WEIGHT_ENTITY_MATCH = 0.50
ROUTING_WEIGHT_COMPANY = 0.30
ROUTING_WEIGHT_SCOPE_CONFLICT = -0.50
ROUTING_WEIGHT_NEW_SUBJECT = -0.60
ROUTING_WEIGHT_EXPLICIT_NEW = -0.80

ROUTING_LINK_THRESHOLD = 0.80
ROUTING_LINK_MARGIN = 0.20
ROUTING_REVIEW_THRESHOLD = 0.80
ROUTING_REVIEW_MARGIN = 0.15
ROUTING_CREATE_NEW_MAX_SCORE = 0.45

OUTBOX_TERMINAL_STATUSES = {
    "sent", "sent_manually", "draft_created", "manual_review", "validation_failed", "permanent_failed"
}
OUTBOX_RECONCILIATION_STATUSES = {"in_progress", "outcome_unknown"}
OUTBOX_STATUSES = OUTBOX_TERMINAL_STATUSES | OUTBOX_RECONCILIATION_STATUSES | {
    "claimed", "retryable_failed", "retry_scheduled"
}
RESPONSE_PHASES = {
    "preparing", "content_validated", "transport_starting", "post_started", "sent",
    "sent_manually", "draft_created", "validation_failed", "manual_review", "retry_scheduled",
    "outcome_unknown",
}


def default_claim_owner() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def _migration_001_durable_outbox(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS unified_outbox ("
        "operation_id TEXT PRIMARY KEY, deal_id TEXT NOT NULL, source_type TEXT NOT NULL, "
        "source_key TEXT NOT NULL, stage TEXT NOT NULL, message_type TEXT NOT NULL, "
        "recipient TEXT NOT NULL, contact_identity TEXT NOT NULL DEFAULT '', "
        "thread_id TEXT NOT NULL DEFAULT '', input_hash TEXT NOT NULL, "
        "content_hash TEXT NOT NULL DEFAULT '', "
        "status TEXT NOT NULL, owner TEXT NOT NULL, started_at TEXT NOT NULL, lease_expires_at TEXT NOT NULL, "
        "marker TEXT NOT NULL DEFAULT '', external_message_id TEXT, last_error TEXT NOT NULL DEFAULT '', "
        "created_at TEXT NOT NULL, updated_at TEXT NOT NULL, "
        "UNIQUE(deal_id,source_type,source_key,stage,content_hash))"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_unified_outbox_status ON unified_outbox(status,lease_expires_at)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_unified_outbox_deal ON unified_outbox(deal_id,created_at)"
    )
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_unified_outbox_event_stage "
        "ON unified_outbox(deal_id,source_type,source_key,stage)"
    )
    for column, declaration in (
        ("operation_id", "TEXT"),
        ("owner", "TEXT"),
        ("started_at", "TEXT"),
        ("lease_expires_at", "TEXT"),
        ("message_type", "TEXT"),
        ("recipient", "TEXT"),
        ("source_type", "TEXT"),
        ("source_key", "TEXT"),
        ("outcome", "TEXT"),
    ):
        add_column_if_missing(connection, "unified_artifacts", column, declaration)


def _migration_002_security_event_resolution(connection: sqlite3.Connection) -> None:
    add_column_if_missing(connection, "unified_security_events", "resolved_at", "TEXT")
    add_column_if_missing(connection, "unified_security_events", "resolved_by", "TEXT")
    add_column_if_missing(connection, "unified_security_events", "resolution", "TEXT")


def _migration_003_durable_contact_identity(connection: sqlite3.Connection) -> None:
    add_column_if_missing(
        connection,
        "unified_outbox",
        "contact_identity",
        "TEXT NOT NULL DEFAULT ''",
    )
    add_column_if_missing(connection, "unified_artifacts", "contact_identity", "TEXT")
    connection.execute(
        "UPDATE unified_outbox SET contact_identity='email:' || lower(trim(recipient)) "
        "WHERE contact_identity='' AND trim(recipient)!=''"
    )


def _migration_004_response_lifecycle(connection: sqlite3.Connection) -> None:
    """Expand-only response lifecycle, review queue and source evidence."""
    for column, declaration in (
        ("phase", "TEXT NOT NULL DEFAULT ''"),
        ("post_started_at", "TEXT"),
        ("terminal_at", "TEXT"),
        ("provider_evidence_json", "TEXT NOT NULL DEFAULT '{}'"),
        ("validation_errors_json", "TEXT NOT NULL DEFAULT '[]'"),
    ):
        add_column_if_missing(connection, "unified_outbox", column, declaration)
    for column, declaration in (
        ("phase", "TEXT NOT NULL DEFAULT ''"),
        ("terminal_at", "TEXT"),
        ("provider_evidence_json", "TEXT NOT NULL DEFAULT '{}'"),
    ):
        add_column_if_missing(connection, "unified_artifacts", column, declaration)
    for column, declaration in (
        ("resolved_reply_recipient", "TEXT NOT NULL DEFAULT ''"),
        ("recipient_evidence_json", "TEXT NOT NULL DEFAULT '{}'"),
        ("content_version", "TEXT NOT NULL DEFAULT ''"),
    ):
        add_column_if_missing(connection, "unified_events", column, declaration)
    lifecycle_schema = """
        CREATE TABLE IF NOT EXISTS unified_review_tasks (
          task_id TEXT PRIMARY KEY,
          source_type TEXT NOT NULL,
          source_key TEXT NOT NULL,
          deal_id TEXT NOT NULL,
          operation_id TEXT NOT NULL,
          reason_codes_json TEXT NOT NULL DEFAULT '[]',
          rejected_body TEXT NOT NULL DEFAULT '',
          validation_errors_json TEXT NOT NULL DEFAULT '[]',
          recipient TEXT NOT NULL DEFAULT '',
          provider_evidence_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL,
          sla_at TEXT NOT NULL,
          notification_status TEXT NOT NULL DEFAULT 'pending',
          status TEXT NOT NULL DEFAULT 'open',
          updated_at TEXT NOT NULL,
          UNIQUE(operation_id)
        );
        CREATE INDEX IF NOT EXISTS idx_unified_review_tasks_open
          ON unified_review_tasks(status, notification_status, sla_at);
        CREATE TABLE IF NOT EXISTS unified_source_dispositions (
          source_type TEXT NOT NULL,
          source_key TEXT NOT NULL,
          deal_id TEXT NOT NULL,
          operation_id TEXT NOT NULL,
          disposition TEXT NOT NULL,
          evidence_json TEXT NOT NULL DEFAULT '{}',
          updated_at TEXT NOT NULL,
          PRIMARY KEY(source_type, source_key)
        );
        CREATE TABLE IF NOT EXISTS unified_response_events (
          event_id INTEGER PRIMARY KEY AUTOINCREMENT,
          operation_id TEXT NOT NULL,
          deal_id TEXT NOT NULL,
          previous_phase TEXT NOT NULL,
          new_phase TEXT NOT NULL,
          evidence_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_unified_response_events_operation
          ON unified_response_events(operation_id, event_id);
        CREATE TABLE IF NOT EXISTS unified_validation_attempts (
          attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
          operation_id TEXT NOT NULL,
          deal_id TEXT NOT NULL,
          attempt_number INTEGER NOT NULL,
          prompt_version TEXT NOT NULL,
          contract_digest TEXT NOT NULL,
          content_hash TEXT NOT NULL,
          error_codes_json TEXT NOT NULL DEFAULT '[]',
          offending_spans_json TEXT NOT NULL DEFAULT '[]',
          sanitizer_actions_json TEXT NOT NULL DEFAULT '[]',
          created_at TEXT NOT NULL,
          UNIQUE(operation_id, attempt_number)
        );
        CREATE TABLE IF NOT EXISTS unified_source_versions (
          source_type TEXT NOT NULL,
          source_key TEXT NOT NULL,
          content_version TEXT NOT NULL,
          first_seen_at TEXT NOT NULL,
          last_seen_at TEXT NOT NULL,
          PRIMARY KEY(source_type, source_key, content_version)
        );
        CREATE TABLE IF NOT EXISTS unified_manual_sends (
          deal_id TEXT NOT NULL,
          stage TEXT NOT NULL,
          external_message_id TEXT NOT NULL,
          sent_at TEXT NOT NULL,
          marker TEXT NOT NULL,
          pdf_hash TEXT NOT NULL DEFAULT '',
          recipient TEXT NOT NULL,
          thread_id TEXT NOT NULL DEFAULT '',
          evidence_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL,
          PRIMARY KEY(deal_id, stage),
          UNIQUE(external_message_id)
        );
        """
    for statement in lifecycle_schema.split(";"):
        if statement.strip():
            connection.execute(statement)
    connection.execute(
        "UPDATE unified_outbox SET phase=CASE status "
        "WHEN 'claimed' THEN 'preparing' WHEN 'in_progress' THEN 'post_started' "
        "WHEN 'retryable_failed' THEN 'retry_scheduled' WHEN 'permanent_failed' THEN 'validation_failed' "
        "ELSE status END WHERE phase=''"
    )
    connection.execute(
        "UPDATE unified_artifacts SET phase=CASE "
        "WHEN outcome='claimed' THEN 'preparing' WHEN outcome='in_progress' THEN 'post_started' "
        "WHEN outcome='retryable_failed' THEN 'retry_scheduled' WHEN outcome='permanent_failed' THEN 'validation_failed' "
        "WHEN outcome!='' THEN outcome ELSE status END WHERE phase=''"
    )


REGISTRY_MIGRATIONS = (
    Migration(1, "durable_outbox_and_claim_metadata", _migration_001_durable_outbox),
    Migration(2, "auditable_security_event_resolution", _migration_002_security_event_resolution),
    Migration(3, "durable_contact_identity", _migration_003_durable_contact_identity),
    Migration(4, "authoritative_response_lifecycle", _migration_004_response_lifecycle),
)


@dataclass
class DealRoutingDecision:
    action: str
    deal_id: str
    confidence: float
    margin: float
    evidence: list[str]
    candidate_scores: dict[str, float]
    reason: str = ""


GENERIC_SUBJECTS = {
    "", "dzien dobry", "pytanie", "zapytanie", "prosba o informacje",
    "oferta", "wiadomosc",
}
ROUTING_STOPWORDS = TOPIC_STOPWORDS | {
    "aby", "albo", "ale", "czy", "dzien", "dobry", "informacje", "jak", "kolejne", "ma", "moze",
    "nowa", "nowe", "nowy", "osobny", "potrzebujemy", "prosba", "prosze", "pytanie", "taki", "tego",
}
TOPIC_PREFIXES = {
    "crm": "crm", "hubspot": "hubspot", "salesforce": "salesforce", "pipedrive": "pipedrive",
    "lead": "lead", "pipeline": "pipeline", "sprzedaz": "sprzedaz", "telegram": "telegram",
    "bot": "bot", "grup": "grupa", "powiadom": "powiadomienie", "faktur": "faktura",
    "invoice": "faktura", "ocr": "ocr", "ksieg": "ksiegowosc", "erp": "erp",
    "magazyn": "magazyn", "stan": "stany", "umow": "umowa", "obieg": "obieg",
    "integrac": "integracja", "automatyz": "automatyzacja",
}
SCOPE_GROUPS = {
    "crm": {"crm", "hubspot", "salesforce", "pipedrive", "lead", "pipeline", "sprzedaz"},
    "telegram": {"telegram", "bot", "grupa", "powiadomienie"},
    "invoices": {"faktura", "ocr", "ksiegowosc"},
    "erp": {"erp", "magazyn", "stany"},
    "contracts": {"umowa", "obieg"},
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _to_text(value: Any) -> str:
    """Serialize a fact value to a stable text form for provenance storage."""
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        return canonical_json(value)
    return str(value)


def digest(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def normalize_email(value: str) -> str:
    email = str(value or "").strip().lower()
    if not EMAIL_RE.fullmatch(email):
        return ""
    local, domain = email.rsplit("@", 1)
    if domain in {"gmail.com", "googlemail.com"}:
        local = local.split("+", 1)[0].replace(".", "")
        domain = "gmail.com"
    return f"{local}@{domain}"


def contact_identity_for_email(value: str) -> str:
    normalized = normalize_email(value)
    return f"email:{normalized}" if normalized else ""


def is_test_customer_email(email: str) -> bool:
    """True for recycled test customer mailboxes (see HERMES_TEST_CUSTOMER_EMAILS)."""
    raw = os.environ.get("HERMES_TEST_CUSTOMER_EMAILS", _DEFAULT_TEST_CUSTOMER_EMAILS)
    if raw.strip() == "":
        return False
    allowed = {normalize_email(part) for part in raw.split(",") if part.strip()}
    allowed.discard("")
    normalized = normalize_email(email)
    return bool(normalized) and normalized in allowed


def normalize_company(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", ascii_text(str(value or "")))


def normalize_subject(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    previous = None
    while text != previous:
        previous = text
        text = RE_PREFIX_RE.sub("", text).strip()
    return re.sub(r"\s+", " ", text)


def topic_signature(subject: str, content: str = "") -> set[str]:
    normalized = unicodedata.normalize("NFKD", f"{normalize_subject(subject)} {content}").casefold()
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return {token for token in TOKEN_RE.findall(normalized) if token not in TOPIC_STOPWORDS}


def ascii_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "")).casefold()
    return "".join(char for char in normalized if not unicodedata.combining(char))


def routing_tokens(value: str) -> set[str]:
    result: set[str] = set()
    for raw in TOKEN_RE.findall(ascii_text(value)):
        if raw in ROUTING_STOPWORDS:
            continue
        canonical = raw
        for prefix, replacement in TOPIC_PREFIXES.items():
            if raw.startswith(prefix):
                canonical = replacement
                break
        result.add(canonical)
    return result


def scope_groups(tokens: set[str]) -> set[str]:
    return {name for name, vocabulary in SCOPE_GROUPS.items() if tokens & vocabulary}


def is_generic_subject(subject: str) -> bool:
    return ascii_text(normalize_subject(subject)) in GENERIC_SUBJECTS


def is_informative_subject(subject: str) -> bool:
    normalized = ascii_text(normalize_subject(subject))
    return bool(normalized and normalized not in GENERIC_SUBJECTS and len(routing_tokens(normalized)) >= 2)


def explicit_new_matter(text: str) -> bool:
    normalized = ascii_text(text)
    return bool(re.search(
        r"\b(nowa sprawa|nowy projekt|nowe zapytanie|kolejne zapytanie|osobny temat|osobny projekt|osobna sprawa|to nowa sprawa)\b",
        normalized,
    ))


def parse_time(value: str | None) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class UnifiedLeadRegistry:
    def __init__(self, path: str | Path, *, routing_llm: Any | None = None) -> None:
        self.path = Path(path)
        self.routing_llm = routing_llm
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA busy_timeout=30000")
        with migration_lock(self.path):
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA foreign_keys=ON")
            self._create_schema()
            self.schema_version = run_migrations(
                self.connection,
                namespace="unified_lead_registry",
                migrations=REGISTRY_MIGRATIONS,
            )
            self._backfill_legacy_review_tasks()
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS unified_deals (
              deal_id TEXT PRIMARY KEY,
              status TEXT NOT NULL,
              primary_email TEXT,
              company TEXT,
              contact_name TEXT,
              facts_json TEXT NOT NULL DEFAULT '{}',
              context_json TEXT NOT NULL DEFAULT '[]',
              current_draft_id TEXT,
              final_draft_id TEXT,
              last_response_id TEXT,
              price_net_display TEXT,
              scope_display TEXT,
              customer_thread_id TEXT,
              is_correlation_case INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS unified_identities (
              identity_type TEXT NOT NULL,
              identity_value TEXT NOT NULL,
              deal_id TEXT NOT NULL,
              created_at TEXT NOT NULL,
              PRIMARY KEY(identity_type, identity_value)
            );
            CREATE TABLE IF NOT EXISTS unified_deal_identities (
              deal_id TEXT NOT NULL,
              identity_type TEXT NOT NULL,
              identity_value TEXT NOT NULL,
              created_at TEXT NOT NULL,
              PRIMARY KEY(deal_id, identity_type, identity_value)
            );
            CREATE INDEX IF NOT EXISTS idx_unified_deal_identity_lookup
              ON unified_deal_identities(identity_type, identity_value, deal_id);
            CREATE TABLE IF NOT EXISTS unified_events (
              event_id INTEGER PRIMARY KEY AUTOINCREMENT,
              source_type TEXT NOT NULL,
              source_key TEXT NOT NULL,
              deal_id TEXT NOT NULL,
              relation TEXT NOT NULL,
              email TEXT,
              company TEXT,
              contact_name TEXT,
              content_hash TEXT NOT NULL,
              thread_id TEXT,
              metadata_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL,
              UNIQUE(source_type, source_key)
            );
            CREATE TABLE IF NOT EXISTS unified_artifacts (
              deal_id TEXT NOT NULL,
              stage TEXT NOT NULL,
              status TEXT NOT NULL,
              content_hash TEXT,
              external_draft_id TEXT,
              external_message_id TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              PRIMARY KEY(deal_id, stage)
            );
            CREATE TABLE IF NOT EXISTS unified_thread_bindings (
              provider TEXT NOT NULL,
              account_id TEXT NOT NULL,
              thread_id TEXT NOT NULL,
              deal_id TEXT NOT NULL,
              first_seen_at TEXT NOT NULL,
              last_seen_at TEXT NOT NULL,
              PRIMARY KEY(provider, account_id, thread_id)
            );
            CREATE TABLE IF NOT EXISTS unified_conversation_control (
              deal_id TEXT NOT NULL,
              account_id TEXT NOT NULL,
              thread_id TEXT NOT NULL,
              state TEXT NOT NULL DEFAULT 'active',
              reason TEXT NOT NULL DEFAULT '',
              evidence_json TEXT NOT NULL DEFAULT '{}',
              paused_at TEXT,
              resumed_at TEXT,
              baseline_message_count INTEGER NOT NULL DEFAULT 0,
              baseline_automatic_count INTEGER NOT NULL DEFAULT 0,
              baseline_at TEXT,
              updated_at TEXT NOT NULL,
              PRIMARY KEY(deal_id, account_id, thread_id)
            );
            CREATE TABLE IF NOT EXISTS unified_conversation_messages (
              account_id TEXT NOT NULL,
              message_id TEXT NOT NULL,
              deal_id TEXT NOT NULL,
              thread_id TEXT NOT NULL,
              direction TEXT NOT NULL,
              origin TEXT NOT NULL,
              occurred_at TEXT NOT NULL,
              metadata_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL,
              PRIMARY KEY(account_id, message_id)
            );
            CREATE INDEX IF NOT EXISTS idx_unified_conversation_messages
              ON unified_conversation_messages(deal_id, account_id, thread_id, occurred_at);
            CREATE TABLE IF NOT EXISTS unified_automation_events (
              event_id INTEGER PRIMARY KEY AUTOINCREMENT,
              deal_id TEXT NOT NULL,
              account_id TEXT NOT NULL,
              thread_id TEXT NOT NULL,
              previous_state TEXT NOT NULL,
              new_state TEXT NOT NULL,
              reason TEXT NOT NULL,
              actor TEXT NOT NULL,
              evidence_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS unified_correlation_audits (
              audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
              source_type TEXT NOT NULL,
              source_key TEXT NOT NULL,
              action TEXT NOT NULL,
              candidate_deal_ids_json TEXT NOT NULL DEFAULT '[]',
              candidate_scores_json TEXT NOT NULL DEFAULT '{}',
              selected_deal_id TEXT,
              provisional_deal_id TEXT,
              confidence REAL NOT NULL,
              margin REAL NOT NULL,
              evidence_json TEXT NOT NULL DEFAULT '[]',
              reason_code TEXT NOT NULL,
              created_at TEXT NOT NULL,
              UNIQUE(source_type, source_key)
            );
            CREATE INDEX IF NOT EXISTS idx_unified_events_deal ON unified_events(deal_id, event_id);
            CREATE INDEX IF NOT EXISTS idx_unified_events_email ON unified_events(email);
            CREATE INDEX IF NOT EXISTS idx_unified_correlation_audits_action
              ON unified_correlation_audits(action, created_at);
            CREATE TABLE IF NOT EXISTS unified_fact_provenance (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              deal_id TEXT NOT NULL,
              field TEXT NOT NULL,
              previous_value TEXT,
              new_value TEXT,
              action TEXT NOT NULL,
              source_type TEXT,
              source_message_id TEXT,
              occurred_at TEXT,
              recorded_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_unified_fact_provenance_deal
              ON unified_fact_provenance(deal_id, field, id);
            CREATE TABLE IF NOT EXISTS unified_conversation_state (
              deal_id TEXT PRIMARY KEY,
              agent_reply_count INTEGER NOT NULL DEFAULT 0,
              customer_message_count INTEGER NOT NULL DEFAULT 0,
              is_first_agent_reply INTEGER NOT NULL DEFAULT 1,
              acknowledgement_already_sent INTEGER NOT NULL DEFAULT 0,
              conversation_stage TEXT NOT NULL DEFAULT 'discovery',
              last_agent_reply_at TEXT,
              last_customer_reply_at TEXT,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS unified_security_events (
              security_event_id INTEGER PRIMARY KEY AUTOINCREMENT,
              event_type TEXT NOT NULL,
              severity TEXT NOT NULL,
              deal_id TEXT,
              source_type TEXT,
              source_key TEXT,
              operation_id TEXT,
              details_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_unified_security_events_lookup
              ON unified_security_events(event_type, created_at);
            """
        )
        deal_columns = {str(row[1]) for row in self.connection.execute("PRAGMA table_info(unified_deals)").fetchall()}
        if "last_response_id" not in deal_columns:
            self.connection.execute("ALTER TABLE unified_deals ADD COLUMN last_response_id TEXT")
        if "is_correlation_case" not in deal_columns:
            self.connection.execute(
                "ALTER TABLE unified_deals ADD COLUMN is_correlation_case INTEGER NOT NULL DEFAULT 0"
            )
        if "tenant_id" not in deal_columns:
            self.connection.execute("ALTER TABLE unified_deals ADD COLUMN tenant_id TEXT NOT NULL DEFAULT ''")
        if "rfq_id" not in deal_columns:
            self.connection.execute("ALTER TABLE unified_deals ADD COLUMN rfq_id TEXT")
            self.connection.execute("CREATE INDEX IF NOT EXISTS idx_unified_deals_rfq_id ON unified_deals(rfq_id)")
        artifact_columns = {str(row[1]) for row in self.connection.execute("PRAGMA table_info(unified_artifacts)").fetchall()}
        if "external_message_id" not in artifact_columns:
            self.connection.execute("ALTER TABLE unified_artifacts ADD COLUMN external_message_id TEXT")
        # Expand-only migration: preserve the legacy one-email-to-one-deal table,
        # but copy it into the new many-to-many identity map used for correlation.
        self.connection.execute(
            "INSERT OR IGNORE INTO unified_deal_identities(deal_id,identity_type,identity_value,created_at) "
            "SELECT deal_id,identity_type,identity_value,created_at FROM unified_identities"
        )

    def _backfill_legacy_review_tasks(self) -> int:
        """Give pre-v4 ``review_required`` deals one durable, no-send task."""
        rows = self.connection.execute(
            "SELECT d.deal_id,d.primary_email FROM unified_deals d WHERE d.status='review_required' "
            "AND NOT EXISTS (SELECT 1 FROM unified_review_tasks t WHERE t.deal_id=d.deal_id AND t.status='open') "
            "ORDER BY d.created_at,d.deal_id"
        ).fetchall()
        if not rows:
            return 0
        now = utc_now()
        sla_at = (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat(timespec="seconds")
        reason = "legacy_review_state_backfill"
        created = 0
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            for deal in rows:
                deal_id = str(deal["deal_id"])
                unresolved = self.connection.execute(
                    "SELECT operation_id,source_type,source_key,recipient FROM unified_outbox WHERE deal_id=? "
                    "AND status IN ('claimed','retryable_failed','retry_scheduled','in_progress','outcome_unknown') "
                    "ORDER BY created_at DESC,operation_id DESC LIMIT 1",
                    (deal_id,),
                ).fetchone()
                event = self.connection.execute(
                    "SELECT source_type,source_key,email FROM unified_events WHERE deal_id=? "
                    "ORDER BY event_id DESC LIMIT 1",
                    (deal_id,),
                ).fetchone()
                source_type = str(
                    unresolved["source_type"] if unresolved else event["source_type"] if event else "legacy_registry"
                )
                source_key = str(
                    unresolved["source_key"] if unresolved else event["source_key"] if event else f"deal:{deal_id}"
                )
                recipient = normalize_email(str(
                    (unresolved["recipient"] if unresolved else "")
                    or (event["email"] if event else "") or deal["primary_email"] or ""
                ))
                operation_id = str(
                    unresolved["operation_id"] if unresolved else "legacy-review-state:" + digest(deal_id)
                )
                task_id = "review-" + digest(operation_id)[:28]
                evidence = {
                    "legacy_migration": True,
                    "provider_evidence": "unavailable",
                    "send_capability": False,
                    "bound_unresolved_operation": bool(unresolved),
                }
                cursor = self.connection.execute(
                    "INSERT INTO unified_review_tasks(task_id,source_type,source_key,deal_id,operation_id,reason_codes_json,"
                    "rejected_body,validation_errors_json,recipient,provider_evidence_json,created_at,sla_at,"
                    "notification_status,status,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'open',?) "
                    "ON CONFLICT(operation_id) DO UPDATE SET reason_codes_json=excluded.reason_codes_json,"
                    "rejected_body=excluded.rejected_body,validation_errors_json=excluded.validation_errors_json,"
                    "recipient=excluded.recipient,provider_evidence_json=excluded.provider_evidence_json,"
                    "notification_status=CASE WHEN unified_review_tasks.status='open' "
                    "AND unified_review_tasks.notification_status='sent' THEN 'sent' ELSE 'pending' END,"
                    "status='open',updated_at=excluded.updated_at",
                    (
                        task_id, source_type, source_key, deal_id, operation_id, canonical_json([reason]),
                        "[legacy rejected body unavailable]", canonical_json([reason]), recipient,
                        canonical_json(evidence), now, sla_at, "pending", now,
                    ),
                )
                created += int(cursor.rowcount > 0)
                if not unresolved:
                    self.connection.execute(
                        "INSERT INTO unified_response_events(operation_id,deal_id,previous_phase,new_phase,evidence_json,created_at) "
                        "SELECT ?,?,'legacy','manual_review',?,? WHERE NOT EXISTS (SELECT 1 FROM unified_response_events "
                        "WHERE operation_id=? AND previous_phase='legacy' AND new_phase='manual_review')",
                        (
                            operation_id, deal_id,
                            canonical_json({"review_task_id": task_id, "reason_codes": [reason], **evidence}),
                            now, operation_id,
                        ),
                    )
            self.connection.execute("COMMIT")
            return created
        except Exception:
            try:
                self.connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise

    def close(self) -> None:
        self.connection.close()

    def _deal_id(self, normalized_email: str, company: str, source_type: str, source_key: str) -> str:
        seed = f"case:v2:{source_type}:{source_key}:{normalized_email}:{normalize_company(company)}"
        return "deal-" + digest(seed)[:24]

    def _active_deals_for_email(self, normalized_email: str) -> list[str]:
        if not normalized_email:
            return []
        rows = self.connection.execute(
            "SELECT DISTINCT i.deal_id FROM unified_deal_identities i "
            "JOIN unified_deals d ON d.deal_id=i.deal_id "
            "WHERE i.identity_type='email' AND i.identity_value=? AND d.status IN (%s) "
            "AND COALESCE(d.is_correlation_case,0)=0 "
            "ORDER BY d.updated_at DESC" % ",".join("?" for _ in ACTIVE_DEAL_STATUSES),
            (normalized_email, *sorted(ACTIVE_DEAL_STATUSES)),
        ).fetchall()
        return [str(row["deal_id"]) for row in rows]

    def _bound_deal(self, provider: str, account_id: str, thread_id: str) -> str:
        if not thread_id:
            return ""
        row = self.connection.execute(
            "SELECT deal_id FROM unified_thread_bindings WHERE provider=? AND account_id=? AND thread_id=?",
            (provider, account_id, thread_id),
        ).fetchone()
        return str(row["deal_id"]) if row else ""

    def bind_thread(self, deal_id: str, *, provider: str, account_id: str, thread_id: str) -> None:
        """Persist one deterministic provider thread binding without re-routing it."""
        if not (deal_id and provider and account_id and thread_id):
            raise ValueError("deal_id, provider, account_id and thread_id are required")
        existing = self._bound_deal(provider, account_id, thread_id)
        if existing and existing != deal_id:
            raise ValueError("thread_binding_conflict")
        now = utc_now()
        self.connection.execute(
            "INSERT INTO unified_thread_bindings(provider,account_id,thread_id,deal_id,first_seen_at,last_seen_at) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(provider,account_id,thread_id) DO UPDATE SET last_seen_at=excluded.last_seen_at",
            (provider, account_id, thread_id, deal_id, now, now),
        )

    def _candidate_metadata(self, deal_id: str) -> tuple[set[str], set[str], set[str], str]:
        rows = self.connection.execute(
            "SELECT source_type,thread_id,metadata_json,created_at FROM unified_events WHERE deal_id=? ORDER BY event_id",
            (deal_id,),
        ).fetchall()
        sources: set[str] = set()
        threads: set[str] = set()
        subjects: set[str] = set()
        latest = ""
        for row in rows:
            sources.add(str(row["source_type"]))
            if row["thread_id"]:
                threads.add(str(row["thread_id"]))
            metadata = json.loads(row["metadata_json"] or "{}")
            subject = normalize_subject(str(metadata.get("subject") or ""))
            if subject:
                subjects.add(subject)
            latest = str(metadata.get("occurred_at") or row["created_at"] or latest)
        return sources, threads, subjects, latest

    def _candidate_message_ids(self, deal_id: str) -> set[str]:
        """RFC Message-IDs this deal has actually been seen to contain."""
        known: set[str] = set()
        for table in ("unified_events", "unified_conversation_messages"):
            rows = self.connection.execute(
                f"SELECT metadata_json FROM {table} WHERE deal_id=?", (deal_id,)  # noqa: S608 - fixed table names
            ).fetchall()
            for row in rows:
                value = str(json.loads(row["metadata_json"] or "{}").get("rfc_message_id") or "").strip()
                if value:
                    known.add(value)
        return known

    def _resolve_reply_candidate(
        self,
        candidates: list[str],
        metadata: dict[str, Any],
        thread_id: str,
        company: str,
    ) -> str:
        """Pick one active deal for a customer reply when several share the email."""
        if len(candidates) == 1:
            return candidates[0]
        reference_ids = set(
            re.findall(r"<[^>]+>", " ".join(str(metadata.get(key) or "") for key in ("references", "in_reply_to")))
        )
        subject = normalize_subject(str(metadata.get("subject") or ""))
        thread_hits: list[str] = []
        rfc_hits: list[str] = []
        subject_hits: list[str] = []
        company_hits: list[str] = []
        for candidate in candidates:
            _sources, threads, subjects, _latest = self._candidate_metadata(candidate)
            if thread_id and thread_id in threads:
                thread_hits.append(candidate)
            known = self._candidate_message_ids(candidate)
            if reference_ids and (reference_ids & known):
                rfc_hits.append(candidate)
            if subject and subject in subjects:
                subject_hits.append(candidate)
            old = self.get_deal(candidate) or {}
            if company and old.get("company") and normalize_company(company) == normalize_company(str(old.get("company"))):
                company_hits.append(candidate)
        for group in (thread_hits, rfc_hits, subject_hits, company_hits):
            if len(group) == 1:
                return group[0]
        return ""

    def _anchored_relation(
        self, relation: str, metadata: dict[str, Any], thread_id: str, candidates: list[str]
    ) -> str:
        """Downgrade a header-only "reply" that points at an unknown conversation.

        A customer composing a brand new inquiry by hitting reply inside their own
        mailbox still sends References/In-Reply-To. Those ids belong to their
        internal thread, so treating them as a reply silently merged an unrelated
        inquiry into an old deal and kept the old, wrong scope.

        Outbound Hermes Message-IDs are often missing from the registry, so a
        single open deal in waiting/analysing must keep the reply relation even
        when References only mention the agent message we failed to store.
        """
        if relation != "reply" or not candidates:
            return relation
        reference_ids = set(
            re.findall(r"<[^>]+>", " ".join(str(metadata.get(key) or "") for key in ("references", "in_reply_to")))
        )
        if not reference_ids:
            # The reply signal came from the classifier, not from headers.
            return relation
        for candidate in candidates:
            if thread_id and thread_id in self._candidate_metadata(candidate)[1]:
                return relation
            known = self._candidate_message_ids(candidate)
            if not known or (reference_ids & known):
                # An empty set means there is nothing to contradict the reply, as
                # with a sheet-sourced deal whose first mail thread is the answer
                # to an outbound campaign. Only recorded ids may reject an anchor.
                return relation
        # Outbound Hermes Message-IDs are often missing from the registry. Keep
        # the reply relation when subject / open waiting deal uniquely identifies
        # the conversation among several active deals for the same email.
        subject = normalize_subject(str(metadata.get("subject") or ""))
        if subject:
            subject_hits = [
                candidate
                for candidate in candidates
                if subject in self._candidate_metadata(candidate)[2]
            ]
            if len(subject_hits) == 1:
                return relation
        waiting = [
            candidate
            for candidate in candidates
            if str((self.get_deal(candidate) or {}).get("status") or "") == "waiting_for_customer"
        ]
        if len(waiting) == 1 and any("@orchesta.eu>" in item for item in reference_ids):
            return relation
        if len(candidates) == 1:
            deal = self.get_deal(candidates[0]) or {}
            if str(deal.get("status") or "") in {"waiting_for_customer", "analysing", "draft_ready"}:
                return relation
        return "new"

    def _candidate_dossier(self, deal_id: str) -> dict[str, Any]:
        deal = self.get_deal(deal_id) or {}
        rows = self.connection.execute(
            "SELECT metadata_json FROM unified_events WHERE deal_id=? ORDER BY event_id",
            (deal_id,),
        ).fetchall()
        subjects: set[str] = set()
        display_subjects: list[str] = []
        text_parts = [
            canonical_json(list(dict(deal.get("facts") or {}).values())),
            str(deal.get("scope_display") or ""),
        ]
        for row in rows:
            metadata = json.loads(row["metadata_json"] or "{}")
            raw_subject = str(metadata.get("subject") or "").strip()
            subject = normalize_subject(raw_subject)
            if subject:
                subjects.add(subject)
                display_subjects.append(RE_PREFIX_RE.sub("", raw_subject).strip())
            for key in ("body", "summary", "context", "scope", "keywords"):
                value = metadata.get(key)
                if value:
                    text_parts.append(canonical_json(value) if isinstance(value, (dict, list)) else str(value))
        for item in deal.get("context") or []:
            text_parts.append(str(item.get("content") or ""))
        text = "\n".join(text_parts)
        return {
            "deal": deal,
            "subjects": subjects,
            "display_subjects": display_subjects,
            "text": text,
            "tokens": routing_tokens(text + " " + " ".join(subjects)),
        }

    def _llm_routing_payload(
        self,
        *,
        subject: str,
        content: str,
        metadata: dict[str, Any],
        company: str,
        facts: dict[str, Any],
        candidates: list[str],
    ) -> dict[str, Any]:
        """Build the minimal, single-customer payload for ambiguous routing."""
        active: list[dict[str, Any]] = []
        for deal_id in candidates:
            dossier = self._candidate_dossier(deal_id)
            deal = dossier["deal"] or {}
            customer_messages = [
                str(item.get("content") or "")[:1200]
                for item in (deal.get("context") or [])
                if str(item.get("source_type") or "") in {"mail", "zoho_mail", "google_sheets"}
            ][-3:]
            outbound = self.connection.execute(
                "SELECT metadata_json FROM unified_conversation_messages "
                "WHERE deal_id=? AND origin='hermes_automatic' ORDER BY occurred_at DESC LIMIT 1",
                (deal_id,),
            ).fetchone()
            outbound_metadata = json.loads(outbound["metadata_json"] or "{}") if outbound else {}
            display_subjects = list(dossier.get("display_subjects") or [])
            active.append({
                "deal_id": deal_id,
                "company": str(deal.get("company") or ""),
                "saved_facts": dict(deal.get("facts") or {}),
                "short_description": str(deal.get("scope_display") or (display_subjects[-1] if display_subjects else ""))[:300],
                "subject": display_subjects[-1] if display_subjects else "",
                "status": str(deal.get("status") or ""),
                "last_customer_messages": customer_messages,
                "last_hermes_reply": str(outbound_metadata.get("body") or "")[:1200],
            })
        return {
            "current_message": {
                "subject": subject,
                "body": content[:4000],
                "thread_headers": {
                    "thread_id": str(metadata.get("thread_id") or ""),
                    "in_reply_to": str(metadata.get("in_reply_to") or ""),
                    "references": str(metadata.get("references") or ""),
                    "rfc_message_id": str(metadata.get("rfc_message_id") or ""),
                },
                "company": company,
                "extracted_facts": facts,
            },
            "active_deals": active,
            "output_contract": {
                "decision": "existing | new | manual_review",
                "deal_id": "candidate deal_id or empty string",
                "confidence": "number from 0.0 to 1.0",
                "reason": "short reason",
            },
        }

    def _route_with_llm(
        self,
        *,
        subject: str,
        content: str,
        metadata: dict[str, Any],
        company: str,
        facts: dict[str, Any],
        candidates: list[str],
        scores: dict[str, float],
        margin: float,
    ) -> DealRoutingDecision:
        if self.routing_llm is None:
            return DealRoutingDecision(
                "review", "", 0.0, margin, ["risk:llm_router_unavailable"], scores,
                "llm_router_unavailable",
            )
        payload = self._llm_routing_payload(
            subject=subject,
            content=content,
            metadata=metadata,
            company=company,
            facts=facts,
            candidates=candidates,
        )
        try:
            raw = self.routing_llm(payload)
            parsed = raw if isinstance(raw, dict) else json.loads(str(raw or ""))
            if not isinstance(parsed, dict) or set(parsed) != {"decision", "deal_id", "confidence", "reason"}:
                raise ValueError("invalid_output_shape")
            decision = str(parsed.get("decision") or "")
            deal_id = str(parsed.get("deal_id") or "")
            confidence = float(parsed.get("confidence"))
            reason = str(parsed.get("reason") or "")[:300]
            if decision not in {"existing", "new", "manual_review"} or not 0.0 <= confidence <= 1.0:
                raise ValueError("invalid_decision")
            if confidence < 0.85:
                return DealRoutingDecision(
                    "review", "", confidence, margin, ["llm:low_confidence"], scores,
                    "llm_low_confidence",
                )
            if decision == "existing":
                if not deal_id or deal_id not in candidates:
                    return DealRoutingDecision(
                        "review", "", confidence, margin, ["risk:llm_unknown_deal_id"], scores,
                        "llm_unknown_deal_id",
                    )
                return DealRoutingDecision(
                    "link_existing", deal_id, confidence, margin, ["llm:existing"], scores,
                    reason or "llm_existing",
                )
            if decision == "new":
                if deal_id:
                    return DealRoutingDecision(
                        "review", "", confidence, margin, ["risk:llm_new_with_deal_id"], scores,
                        "llm_invalid_new",
                    )
                return DealRoutingDecision(
                    "create_new", "", confidence, margin, ["llm:new"], scores,
                    reason or "llm_new",
                )
            return DealRoutingDecision(
                "review", "", confidence, margin, ["llm:manual_review"], scores,
                reason or "llm_manual_review",
            )
        except (TypeError, ValueError, json.JSONDecodeError, KeyError):
            return DealRoutingDecision(
                "review", "", 0.0, margin, ["risk:llm_invalid_json"], scores,
                "llm_invalid_json",
            )

    def _deals_for_message_ids(self, message_ids: set[str]) -> set[str]:
        if not message_ids:
            return set()
        hits: set[str] = set()
        rows = self.connection.execute(
            "SELECT deal_id,metadata_json FROM unified_events UNION ALL "
            "SELECT deal_id,metadata_json FROM unified_conversation_messages"
        ).fetchall()
        for row in rows:
            metadata = json.loads(row["metadata_json"] or "{}")
            known = str(metadata.get("rfc_message_id") or "").strip()
            if known and known in message_ids:
                hits.add(str(row["deal_id"]))
        return hits

    def _explicit_identifier_hits(self, text: str, candidates: list[str]) -> set[str]:
        hits = {candidate for candidate in candidates if candidate and candidate in text}
        for candidate in candidates:
            deal = self.get_deal(candidate) or {}
            for identifier in (
                deal.get("rfq_id"),
                deal.get("final_draft_id"),
                deal.get("current_draft_id"),
            ):
                value = str(identifier or "").strip()
                if value and value in text:
                    hits.add(candidate)
            facts = dict(deal.get("facts") or {})
            for key, value in facts.items():
                if not re.search(r"deal|case|spraw|offer|ofert|quote|referenc|ident", str(key), re.I):
                    continue
                identifier = str(value or "").strip()
                if 3 <= len(identifier) <= 100 and identifier in text:
                    hits.add(candidate)
        return hits

    def _routing_scores(
        self,
        candidates: list[str],
        *,
        subject: str,
        content: str,
        facts: dict[str, Any],
        company: str,
    ) -> tuple[dict[str, float], dict[str, list[str]]]:
        normalized_subject = normalize_subject(subject)
        subject_tokens = routing_tokens(normalized_subject)
        incoming_tokens = routing_tokens(f"{subject} {content} {canonical_json(list(facts.values()))}")
        incoming_groups = scope_groups(incoming_tokens)
        incoming_entities = set().union(*SCOPE_GROUPS.values()) & incoming_tokens
        explicit_new = explicit_new_matter(f"{subject} {content}")
        scores: dict[str, float] = {}
        evidence: dict[str, list[str]] = {}
        for candidate in candidates:
            dossier = self._candidate_dossier(candidate)
            candidate_tokens = set(dossier["tokens"])
            candidate_groups = scope_groups(candidate_tokens)
            candidate_entities = set().union(*SCOPE_GROUPS.values()) & candidate_tokens
            score = 0.0
            used: list[str] = []

            if normalized_subject and not is_generic_subject(subject) and normalized_subject in dossier["subjects"]:
                score += ROUTING_WEIGHT_EXACT_SUBJECT
                used.append(f"score:exact_subject:+{ROUTING_WEIGHT_EXACT_SUBJECT:.2f}")

            overlap = incoming_tokens & candidate_tokens
            similarity = len(overlap) / max(1, len(incoming_tokens | candidate_tokens))
            if similarity:
                semantic = ROUTING_WEIGHT_STRONG_SEMANTIC * min(1.0, similarity)
                score += semantic
                used.append(f"score:semantic:+{semantic:.3f}")

            entity_overlap = incoming_entities & candidate_entities
            if incoming_entities and entity_overlap:
                entity_score = ROUTING_WEIGHT_ENTITY_MATCH * len(entity_overlap) / len(incoming_entities)
                score += entity_score
                used.append(f"score:entities:+{entity_score:.3f}")

            candidate_company = str((dossier["deal"] or {}).get("company") or "")
            if company and candidate_company and normalize_company(company) == normalize_company(candidate_company):
                score += ROUTING_WEIGHT_COMPANY
                used.append("score:company:+0.30")

            if incoming_groups and candidate_groups and incoming_groups.isdisjoint(candidate_groups):
                score += ROUTING_WEIGHT_SCOPE_CONFLICT
                used.append("score:scope_conflict:-0.50")

            candidate_subject_tokens = routing_tokens(" ".join(dossier["subjects"]))
            if (
                is_informative_subject(subject)
                and normalized_subject not in dossier["subjects"]
                and not (subject_tokens & candidate_subject_tokens)
                and similarity < 0.50
            ):
                score += ROUTING_WEIGHT_NEW_SUBJECT
                used.append("score:new_informative_subject:-0.60")

            if explicit_new:
                score += ROUTING_WEIGHT_EXPLICIT_NEW
                used.append("score:explicit_new:-0.80")

            scores[candidate] = round(score, 3)
            evidence[candidate] = used
        return scores, evidence

    def _route_deal(
        self,
        *,
        source_type: str,
        normalized_email: str,
        company: str,
        content: str,
        facts: dict[str, Any],
        metadata: dict[str, Any],
        provider: str,
        account_id: str,
        thread_id: str,
        candidates: list[str],
    ) -> tuple[DealRoutingDecision, bool]:
        subject = str(metadata.get("subject") or "")
        scores, score_evidence = self._routing_scores(
            candidates, subject=subject, content=content, facts=facts, company=company
        )
        incoming = f"{subject} {content} {canonical_json(facts)}"
        in_reply_ids = set(re.findall(r"<[^>]+>", str(metadata.get("in_reply_to") or "")))
        reference_ids = set(re.findall(r"<[^>]+>", str(metadata.get("references") or "")))
        in_reply_hits = self._deals_for_message_ids(in_reply_ids)
        reference_hits = self._deals_for_message_ids(reference_ids)
        bound = self._bound_deal(provider, account_id, thread_id)
        explicit_hits = self._explicit_identifier_hits(incoming, candidates)

        hard_sets = [hits for hits in ({bound} if bound else set(), in_reply_hits, reference_hits, explicit_hits) if hits]
        hard_union = set().union(*hard_sets) if hard_sets else set()
        hard_evidence: list[str] = []
        if bound:
            hard_evidence.append("hard:thread_id")
            scores[bound] = max(scores.get(bound, 0.0), ROUTING_WEIGHT_THREAD_ID)
        if in_reply_hits:
            hard_evidence.append("hard:in_reply_to")
            for hit in in_reply_hits:
                scores[hit] = max(scores.get(hit, 0.0), ROUTING_WEIGHT_REPLY_REFERENCE)
        if reference_hits:
            hard_evidence.append("hard:references")
            for hit in reference_hits:
                scores[hit] = max(scores.get(hit, 0.0), ROUTING_WEIGHT_REPLY_REFERENCE)
        if explicit_hits:
            hard_evidence.append("hard:deal_identifier")

        foreign_hard = {
            deal_id for deal_id in hard_union
            if normalized_email and normalize_email(str((self.get_deal(deal_id) or {}).get("primary_email") or "")) != normalized_email
        }
        conflicting_headers = bool(
            len(in_reply_hits) > 1
            or len(reference_hits) > 1
            or len(explicit_hits) > 1
            or (bound and in_reply_hits and bound not in in_reply_hits)
            or (bound and reference_hits and bound not in reference_hits)
            or (in_reply_hits and reference_hits and in_reply_hits != reference_hits)
            or (explicit_hits and hard_union - explicit_hits)
            or foreign_hard
        )
        if conflicting_headers:
            ordered = sorted(scores.values(), reverse=True)
            margin = (ordered[0] - ordered[1]) if len(ordered) > 1 else 0.0
            return DealRoutingDecision(
                "review", "", 1.0, round(margin, 3),
                hard_evidence + (["risk:cross_customer_binding"] if foreign_hard else ["risk:hard_signal_conflict"]),
                scores, "conflicting_hard_signals",
            ), False

        header_target = bound or (next(iter(in_reply_hits)) if len(in_reply_hits) == 1 else "") or (
            next(iter(reference_hits)) if len(reference_hits) == 1 else ""
        )
        if header_target:
            evidence = hard_evidence + score_evidence.get(header_target, [])
            weight = ROUTING_WEIGHT_THREAD_ID if bound else ROUTING_WEIGHT_REPLY_REFERENCE
            return DealRoutingDecision(
                "link_existing", header_target, weight, round(weight, 3), evidence, scores,
                "hard_header_match",
            ), False
        if len(explicit_hits) == 1:
            target = next(iter(explicit_hits))
            scores[target] = max(scores.get(target, 0.0), ROUTING_WEIGHT_REPLY_REFERENCE)
            return DealRoutingDecision(
                "link_existing", target, ROUTING_WEIGHT_REPLY_REFERENCE, ROUTING_WEIGHT_REPLY_REFERENCE,
                hard_evidence + score_evidence.get(target, []), scores, "explicit_deal_identifier",
            ), False
        if len(explicit_hits) > 1:
            return DealRoutingDecision(
                "review", "", 1.0, 0.0, hard_evidence + ["risk:multiple_explicit_identifiers"],
                scores, "conflicting_hard_signals",
            ), False

        # A mail reply to the sole active sheet-originated lead can reference an
        # outbound Message-ID that predates registry capture.  Keep this narrow
        # campaign bridge, but never use it when the sender has multiple deals.
        if (in_reply_ids or reference_ids) and len(candidates) == 1:
            candidate = candidates[0]
            sources, threads, _subjects, _latest = self._candidate_metadata(candidate)
            if sources == {"google_sheets"} and not threads and source_type == "mail":
                scores[candidate] = max(scores.get(candidate, 0.0), ROUTING_WEIGHT_REPLY_REFERENCE)
                return DealRoutingDecision(
                    "link_existing", candidate, 0.90, 0.90,
                    ["hard:single_sheet_campaign_reply"] + score_evidence.get(candidate, []), scores,
                    "single_sheet_campaign_reply",
                ), False

        # Preserve exact cross-source deduplication, but never let it override a
        # valid thread/reference header resolved above.
        duplicate_hits: list[str] = []
        content_hash = digest(content)
        incoming_duplicate_subject = normalize_subject(subject)
        for candidate in candidates:
            exact_rows = self.connection.execute(
                "SELECT metadata_json FROM unified_events "
                "WHERE deal_id=? AND content_hash=? AND source_type<>?",
                (candidate, content_hash, source_type),
            ).fetchall()
            exact = False
            for row in exact_rows:
                previous_metadata = json.loads(row["metadata_json"] or "{}")
                previous_subject_raw = str(previous_metadata.get("subject") or "")
                previous_subject = normalize_subject(previous_subject_raw)
                incoming_subject_meaningful = bool(incoming_duplicate_subject) and not is_generic_subject(subject)
                previous_subject_meaningful = bool(previous_subject) and not is_generic_subject(previous_subject_raw)
                if (
                    (incoming_subject_meaningful or previous_subject_meaningful)
                    and incoming_duplicate_subject != previous_subject
                ):
                    continue
                exact = True
                break
            if exact:
                duplicate_hits.append(candidate)
        if len(duplicate_hits) == 1:
            target = duplicate_hits[0]
            scores[target] = max(scores.get(target, 0.0), ROUTING_WEIGHT_THREAD_ID)
            return DealRoutingDecision(
                "link_existing", target, 1.0, 1.0,
                ["hard:cross_source_fingerprint"] + score_evidence.get(target, []), scores,
                "cross_source_duplicate",
            ), True
        if len(duplicate_hits) > 1:
            return DealRoutingDecision(
                "review", "", 1.0, 0.0, ["risk:cross_source_fingerprint_conflict"], scores,
                "conflicting_database_links",
            ), False

        if candidates and explicit_new_matter(incoming):
            ordered_values = sorted(scores.values(), reverse=True)
            top_score = ordered_values[0] if ordered_values else 0.0
            second_score = ordered_values[1] if len(ordered_values) > 1 else 0.0
            return DealRoutingDecision(
                "create_new", "", 0.95, round(top_score - second_score, 3),
                ["explicit_new_matter", f"max_candidate_score:{top_score:.3f}"], scores,
                "explicit_new_matter",
            ), False

        if not candidates:
            return DealRoutingDecision(
                "create_new", "", 1.0, 1.0, ["no_active_deals"], {}, "no_active_deals"
            ), False

        ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        top_id, top_score = ordered[0]
        second_score = ordered[1][1] if len(ordered) > 1 else 0.0
        margin = round(top_score - second_score, 3)
        incoming_groups = scope_groups(routing_tokens(incoming))

        if top_score >= ROUTING_REVIEW_THRESHOLD and second_score >= ROUTING_REVIEW_THRESHOLD and margin < ROUTING_REVIEW_MARGIN:
            return DealRoutingDecision(
                "review", "", min(1.0, max(0.0, top_score)), margin,
                score_evidence.get(top_id, []) + ["risk:high_score_tie"], scores,
                "equally_strong_candidates",
            ), False

        if top_score >= ROUTING_LINK_THRESHOLD and margin >= ROUTING_LINK_MARGIN:
            return DealRoutingDecision(
                "link_existing", top_id, min(1.0, top_score), margin,
                score_evidence.get(top_id, []) + ["semantic:high_confidence_margin"], scores,
                "semantic_high_confidence",
            ), False

        # Accept either an informative subject (mail replies) or informative
        # incoming content (sheet leads have no subject but a rich message body).
        # A low semantic score vs active deals means a clearly different topic,
        # so this is a new deal, not a duplicate of the same-email candidate.
        incoming_informative = len(routing_tokens(content)) >= 2
        if (is_informative_subject(subject) or incoming_informative) and top_score < ROUTING_CREATE_NEW_MAX_SCORE:
            evidence_tag = "informative_new_subject" if is_informative_subject(subject) else "informative_new_content"
            return DealRoutingDecision(
                "create_new", "", min(0.95, max(0.70, 1.0 - max(0.0, top_score))), margin,
                [evidence_tag, f"max_candidate_score:{top_score:.3f}"], scores,
                "informative_new_topic_low_similarity",
            ), False

        if incoming_groups and top_score <= 0.15:
            return DealRoutingDecision(
                "create_new", "", 0.80, margin,
                ["new_scope_low_similarity", f"max_candidate_score:{top_score:.3f}"], scores,
                "new_scope_low_similarity",
            ), False

        decision = self._route_with_llm(
            subject=subject,
            content=content,
            metadata=metadata,
            company=company,
            facts=facts,
            candidates=candidates,
            scores=scores,
            margin=margin,
        )
        if decision.action == "create_new":
            decision.evidence.extend(score_evidence.get(top_id, []))
        return decision, False

    def _record_correlation_audit(
        self,
        *,
        source_type: str,
        source_key: str,
        decision: DealRoutingDecision,
        selected_deal_id: str,
        provisional_deal_id: str,
        now: str,
    ) -> None:
        candidate_ids = list(decision.candidate_scores)
        # Idempotent: re-processing the same source_key (e.g. after a poller
        # state reset) must not crash on the UNIQUE(source_type, source_key)
        # constraint. The decision is deterministic for a given source_key, so
        # keeping the first audit is correct; subsequent inserts are ignored.
        self.connection.execute(
            "INSERT OR IGNORE INTO unified_correlation_audits("
            "source_type,source_key,action,candidate_deal_ids_json,candidate_scores_json,selected_deal_id,"
            "provisional_deal_id,confidence,margin,evidence_json,reason_code,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                source_type, source_key, decision.action, canonical_json(candidate_ids),
                canonical_json(decision.candidate_scores), selected_deal_id or None, provisional_deal_id or None,
                float(decision.confidence), float(decision.margin), canonical_json(decision.evidence),
                decision.reason, now,
            ),
        )

    def correlation_audit(self, source_type: str, source_key: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM unified_correlation_audits WHERE source_type=? AND source_key=?",
            (str(source_type), str(source_key)),
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["candidate_deal_ids"] = json.loads(result.pop("candidate_deal_ids_json") or "[]")
        result["candidate_scores"] = json.loads(result.pop("candidate_scores_json") or "{}")
        result["evidence"] = json.loads(result.pop("evidence_json") or "[]")
        return result

    def register_event(
        self,
        *,
        source_type: str,
        source_key: str,
        email: str,
        company: str,
        contact_name: str,
        content: str,
        relation: str,
        thread_id: str = "",
        facts: dict[str, Any] | None = None,
        source_metadata: dict[str, Any] | None = None,
        tenant_id: str = "",
    ) -> dict[str, Any]:
        source_type = str(source_type or "unknown").strip()
        source_key = str(source_key or "").strip()
        if not source_key:
            raise ValueError("source_key is required")
        metadata = dict(source_metadata or {})
        provider = str(metadata.get("provider") or ("zoho" if source_type == "mail" else source_type))
        account_id = str(metadata.get("account_id") or "")
        thread_id = str(thread_id or metadata.get("thread_id") or "")
        tenant_id = str(tenant_id or metadata.get("tenant_id") or "").strip()
        incoming_facts = dict(facts or {})
        now = utc_now()

        try:
            self.connection.execute("BEGIN IMMEDIATE")
            existing = self.connection.execute(
                "SELECT * FROM unified_events WHERE source_type=? AND source_key=?", (source_type, source_key)
            ).fetchone()
            if existing:
                content_version = str(metadata.get("content_hash") or digest(content))
                self.connection.execute(
                    "INSERT INTO unified_source_versions(source_type,source_key,content_version,first_seen_at,last_seen_at) "
                    "VALUES(?,?,?,?,?) ON CONFLICT(source_type,source_key,content_version) DO UPDATE SET "
                    "last_seen_at=excluded.last_seen_at",
                    (source_type, source_key, content_version, now, now),
                )
                deal = self.get_deal(str(existing["deal_id"])) or {}
                audit = self.correlation_audit(source_type, source_key) or {}
                existing_metadata = json.loads(existing["metadata_json"] or "{}")
                existing_material_changes = dict(existing_metadata.get("material_scope_changes") or {})
                existing_fact_conflicts = dict(existing_metadata.get("fact_conflicts") or {})
                action = str(audit.get("action") or "link_existing")
                decision = {
                    "action": action,
                    "deal_id": str(audit.get("selected_deal_id") or ""),
                    "confidence": float(audit.get("confidence") or 0.0),
                    "margin": float(audit.get("margin") or 0.0),
                    "evidence": list(audit.get("evidence") or []),
                    "candidate_scores": dict(audit.get("candidate_scores") or {}),
                    "reason": str(audit.get("reason_code") or "already_registered"),
                }
                self.connection.execute("COMMIT")
                return {
                    "deal_id": str(existing["deal_id"]), "is_new_event": False, "duplicate": True,
                    "is_cross_source_duplicate": "hard:cross_source_fingerprint" in decision["evidence"],
                    "requires_review": action == "review", "reason_code": "already_registered",
                    "correlation_outcome": "duplicate", "routing_action": action,
                    "routing_decision": decision, "material_scope_change": bool(existing_material_changes),
                    "material_scope_changes": existing_material_changes,
                    "fact_conflicts": existing_fact_conflicts,
                    "status": str(deal.get("status") or ""),
                }

            normalized = normalize_email(email)
            candidates = self._active_deals_for_email(normalized)
            if not normalized:
                decision = DealRoutingDecision(
                    "review", "", 1.0, 0.0, ["risk:invalid_email"], {}, "invalid_email"
                )
                is_cross_source_duplicate = False
            else:
                decision, is_cross_source_duplicate = self._route_deal(
                    source_type=source_type,
                    normalized_email=normalized,
                    company=company,
                    content=content,
                    facts=incoming_facts,
                    metadata=metadata,
                    provider=provider,
                    account_id=account_id,
                    thread_id=thread_id,
                    candidates=candidates,
                )
                # Test mailboxes (development/training): each Google Sheets row is
                # an independent scenario. Force create_new so linking on the
                # recycled address never blocks auto-reply during live tests.
                # Production emails are unchanged. Mail replies still use thread
                # hard-matching via In-Reply-To / thread_id elsewhere.
                if (
                    source_type == "google_sheets"
                    and is_test_customer_email(normalized)
                    and decision.action in {"link_existing", "review"}
                    and not is_cross_source_duplicate
                ):
                    decision = DealRoutingDecision(
                        "create_new",
                        "",
                        1.0,
                        1.0,
                        list(decision.evidence) + ["test_customer_email_always_new"],
                        dict(decision.candidate_scores),
                        "test_customer_email_always_new",
                    )

            if decision.action == "link_existing":
                deal_id = decision.deal_id
            else:
                deal_id = self._deal_id(normalized, company, source_type, source_key)
                if decision.action == "create_new":
                    decision.deal_id = deal_id

            old = self.get_deal(deal_id) if decision.action == "link_existing" else None
            old_facts = dict((old or {}).get("facts") or {})
            material_changes: dict[str, dict[str, Any]] = {}
            fact_conflicts: dict[str, dict[str, Any]] = {}
            for key, value in incoming_facts.items():
                if value is None or value == "":
                    continue
                key = str(key)
                if key in old_facts and old_facts[key] != value:
                    change = {"previous": old_facts[key], "proposed": value}
                    fact_conflicts[key] = change
                    if key in MATERIAL_SCOPE_KEYS:
                        material_changes[key] = change
                    continue
                old_facts[key] = value
            if fact_conflicts:
                decision.evidence.extend(f"fact_conflict:{key}" for key in sorted(fact_conflicts))

            context = list((old or {}).get("context") or [])
            context.append({
                "source_type": source_type,
                "source_key": source_key,
                "relation": str(relation or "new"),
                "content": str(content or "")[:6000],
                "created_at": now,
            })
            context = context[-30:]
            if decision.action == "review":
                status = "review_required"
            elif old:
                old_status = str(old.get("status") or "analysing")
                status = "analysing" if old_status in TERMINAL_DEAL_STATUSES else old_status
                if str(relation or "") == "reply" and status not in {"review_required", "offer_ready"}:
                    status = "analysing"
            else:
                status = "analysing"
            is_correlation_case = int(decision.action == "review")

            self.connection.execute(
                "INSERT INTO unified_deals("
                "deal_id,status,primary_email,company,contact_name,facts_json,context_json,customer_thread_id,"
                "is_correlation_case,tenant_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(deal_id) DO UPDATE SET "
                "status=excluded.status,primary_email=COALESCE(NULLIF(unified_deals.primary_email,''),excluded.primary_email),"
                "company=COALESCE(NULLIF(unified_deals.company,''),excluded.company),"
                "contact_name=COALESCE(NULLIF(unified_deals.contact_name,''),excluded.contact_name),"
                "facts_json=excluded.facts_json,context_json=excluded.context_json,"
                "customer_thread_id=COALESCE(NULLIF(unified_deals.customer_thread_id,''),excluded.customer_thread_id),"
                "tenant_id=COALESCE(NULLIF(unified_deals.tenant_id,''),excluded.tenant_id),"
                "updated_at=excluded.updated_at",
                (
                    deal_id, status, normalized, str(company or "").strip(), str(contact_name or "").strip(),
                    canonical_json(old_facts), canonical_json(context), thread_id, is_correlation_case, tenant_id, now, now,
                ),
            )
            if normalized:
                self.connection.execute(
                    "INSERT OR IGNORE INTO unified_deal_identities(deal_id,identity_type,identity_value,created_at) "
                    "VALUES(?,'email',?,?)",
                    (deal_id, normalized, now),
                )
                self.connection.execute(
                    "INSERT OR IGNORE INTO unified_identities(identity_type,identity_value,deal_id,created_at) "
                    "VALUES('email',?,?,?)",
                    (normalized, deal_id, now),
                )

            # Correlation hold cases are deliberately not bound to a customer
            # thread. The customer's answer can then resolve to a real candidate
            # or create a clean new deal instead of inheriting the hold context.
            if thread_id and decision.action in {"link_existing", "create_new"}:
                current_binding = self._bound_deal(provider, account_id, thread_id)
                if current_binding and current_binding != deal_id:
                    raise RuntimeError("thread_binding_changed_during_transaction")
                self.connection.execute(
                    "INSERT INTO unified_thread_bindings(provider,account_id,thread_id,deal_id,first_seen_at,last_seen_at) "
                    "VALUES(?,?,?,?,?,?) ON CONFLICT(provider,account_id,thread_id) DO UPDATE SET last_seen_at=excluded.last_seen_at",
                    (provider, account_id, thread_id, deal_id, now, now),
                )

            event_metadata = dict(metadata)
            event_metadata["routing_decision"] = asdict(decision)
            if material_changes:
                event_metadata["material_scope_changes"] = material_changes
            if fact_conflicts:
                event_metadata["fact_conflicts"] = fact_conflicts
            resolved_recipient = normalize_email(str(event_metadata.get("resolved_reply_recipient") or normalized))
            recipient_evidence = dict(event_metadata.get("recipient_resolution_evidence") or {})
            if resolved_recipient and not recipient_evidence:
                recipient_evidence = {
                    "source": "registered_event_email",
                    "resolved_reply_recipient": resolved_recipient,
                    "reply_all": False,
                }
                event_metadata["resolved_reply_recipient"] = resolved_recipient
                event_metadata["recipient_resolution_evidence"] = recipient_evidence
            content_version = str(event_metadata.get("content_hash") or digest(content))
            self.connection.execute(
                "INSERT INTO unified_events("
                "source_type,source_key,deal_id,relation,email,company,contact_name,content_hash,thread_id,metadata_json,"
                "resolved_reply_recipient,recipient_evidence_json,content_version,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    source_type, source_key, deal_id, str(relation or "new"), normalized, str(company or ""),
                    str(contact_name or ""), digest(content), thread_id, canonical_json(event_metadata),
                    resolved_recipient, canonical_json(recipient_evidence), content_version, now,
                ),
            )
            self.connection.execute(
                "INSERT INTO unified_source_versions(source_type,source_key,content_version,first_seen_at,last_seen_at) "
                "VALUES(?,?,?,?,?) ON CONFLICT(source_type,source_key,content_version) DO UPDATE SET "
                "last_seen_at=excluded.last_seen_at",
                (source_type, source_key, content_version, now, now),
            )
            selected = deal_id if decision.action in {"link_existing", "create_new"} else ""
            provisional = deal_id if decision.action == "review" else ""
            self._record_correlation_audit(
                source_type=source_type, source_key=source_key, decision=decision,
                selected_deal_id=selected, provisional_deal_id=provisional, now=now,
            )
            self.connection.execute("COMMIT")

            legacy_outcome = {
                "link_existing": "linked", "create_new": "created",
                "review": "review_required",
            }[decision.action]
            return {
                "deal_id": deal_id,
                "is_new_event": True,
                "duplicate": False,
                "is_cross_source_duplicate": is_cross_source_duplicate,
                "requires_review": decision.action == "review",
                "reason_code": decision.reason,
                "correlation_outcome": legacy_outcome,
                "routing_action": decision.action,
                "routing_decision": asdict(decision),
                "status": status,
                "material_scope_change": bool(material_changes),
                "material_scope_changes": material_changes,
                "fact_conflicts": fact_conflicts,
            }
        except Exception:
            try:
                self.connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise

    def get_deal(self, deal_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM unified_deals WHERE deal_id=?", (str(deal_id),)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["facts"] = json.loads(result.pop("facts_json") or "{}")
        result["context"] = json.loads(result.pop("context_json") or "[]")
        return result

    def get_event(self, source_type: str, source_key: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM unified_events WHERE source_type=? AND source_key=?",
            (str(source_type), str(source_key)),
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["metadata"] = json.loads(result.pop("metadata_json") or "{}")
        return result

    def persist_message_policy(
        self,
        source_type: str,
        source_key: str,
        *,
        requested_type: str,
        effective_type: str,
        transport_mode: str,
        reasons: list[str] | tuple[str, ...],
    ) -> dict[str, Any]:
        """Persist the system policy; an existing final offer is irreversible."""
        from message_policy import AUTO_SEND_TYPES, FINAL_OFFER, MANUAL_REVIEW, MESSAGE_TYPES

        if effective_type not in MESSAGE_TYPES | {MANUAL_REVIEW}:
            raise ValueError("invalid_effective_message_type")
        expected_transport = (
            "draft_only" if effective_type == FINAL_OFFER
            else "manual_review" if effective_type == MANUAL_REVIEW
            else "auto_send"
        )
        if effective_type not in AUTO_SEND_TYPES and effective_type not in {FINAL_OFFER, MANUAL_REVIEW}:
            raise ValueError("message_type_has_no_transport_policy")
        if transport_mode != expected_transport:
            transport_mode = expected_transport
            reasons = [*reasons, "transport_mode_normalized_from_durable_type"]
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            row = self.connection.execute(
                "SELECT metadata_json FROM unified_events WHERE source_type=? AND source_key=?",
                (str(source_type), str(source_key)),
            ).fetchone()
            if not row:
                self.connection.execute("ROLLBACK")
                raise KeyError("durable_event_not_found")
            metadata = json.loads(row["metadata_json"] or "{}")
            previous = dict(metadata.get("message_policy") or {})
            if str(previous.get("effective_type") or "") == FINAL_OFFER and effective_type != FINAL_OFFER:
                effective_type = FINAL_OFFER
                transport_mode = "draft_only"
                reasons = [*reasons, "final_offer_type_is_irreversible"]
            policy = {
                "requested_type": str(requested_type or ""),
                "effective_type": effective_type,
                "transport_mode": transport_mode,
                "reasons": list(dict.fromkeys(str(item) for item in reasons if item)),
                "decided_by": "deterministic_message_policy",
                "updated_at": utc_now(),
            }
            metadata["message_type"] = effective_type
            metadata["message_policy"] = policy
            self.connection.execute(
                "UPDATE unified_events SET metadata_json=? WHERE source_type=? AND source_key=?",
                (canonical_json(metadata), str(source_type), str(source_key)),
            )
            self.connection.execute("COMMIT")
            return policy
        except Exception:
            try:
                self.connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise

    def record_security_event(
        self,
        event_type: str,
        *,
        severity: str = "high",
        deal_id: str = "",
        source_type: str = "",
        source_key: str = "",
        operation_id: str = "",
        details: dict[str, Any] | None = None,
    ) -> int:
        cursor = self.connection.execute(
            "INSERT INTO unified_security_events(event_type,severity,deal_id,source_type,source_key,operation_id,details_json,created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (
                str(event_type), str(severity), str(deal_id), str(source_type), str(source_key),
                str(operation_id), canonical_json(details or {}), utc_now(),
            ),
        )
        return int(cursor.lastrowid)

    def security_events(self, event_type: str = "") -> list[dict[str, Any]]:
        if event_type:
            rows = self.connection.execute(
                "SELECT * FROM unified_security_events WHERE event_type=? ORDER BY security_event_id",
                (str(event_type),),
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM unified_security_events ORDER BY security_event_id"
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["details"] = json.loads(item.pop("details_json") or "{}")
            result.append(item)
        return result

    def resolve_security_event(self, security_event_id: int, *, resolved_by: str, resolution: str) -> bool:
        """Resolve one reviewed alarm without deleting its audit history."""
        actor = str(resolved_by or "").strip()
        reason = str(resolution or "").strip()
        if not actor or not reason:
            raise ValueError("security_resolution_actor_and_reason_required")
        cursor = self.connection.execute(
            "UPDATE unified_security_events SET resolved_at=?,resolved_by=?,resolution=? "
            "WHERE security_event_id=? AND resolved_at IS NULL",
            (utc_now(), actor[:200], reason[:500], int(security_event_id)),
        )
        return cursor.rowcount == 1

    def context_for_deal(self, deal_id: str) -> dict[str, Any]:
        deal = self.get_deal(deal_id)
        if not deal:
            return {}
        return {
            "deal_id": deal_id,
            "email": deal.get("primary_email") or "",
            "company": deal.get("company") or "",
            "contact_name": deal.get("contact_name") or "",
            "facts": deal.get("facts") or {},
            "thread_id": deal.get("customer_thread_id") or "",
            "status": deal.get("status") or "",
        }

    def context_text(self, deal_id: str) -> str:
        deal = self.get_deal(deal_id)
        if not deal:
            return ""
        lines = [
            f"Kontakt: {deal.get('contact_name') or ''}",
            f"Firma: {deal.get('company') or ''}",
            f"Email: {deal.get('primary_email') or ''}",
            "Zebrane dane: " + canonical_json(deal.get("facts") or {}),
        ]
        for item in deal.get("context") or []:
            lines.append(f"[{item.get('source_type')}/{item.get('relation')}] {item.get('content') or ''}")
        return "\n".join(lines)[-12000:]

    def observe_conversation_message(
        self,
        deal_id: str,
        *,
        account_id: str,
        thread_id: str,
        message_id: str,
        direction: str,
        origin: str,
        occurred_at: str,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        if not (deal_id and account_id and thread_id and message_id):
            raise ValueError("deal_id, account_id, thread_id and message_id are required")
        occurred = parse_time(occurred_at)
        if not occurred:
            raise ValueError("valid occurred_at is required")
        now = utc_now()
        cursor = self.connection.execute(
            "INSERT OR IGNORE INTO unified_conversation_messages(account_id,message_id,deal_id,thread_id,direction,origin,occurred_at,metadata_json,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (account_id, message_id, deal_id, thread_id, direction, origin, occurred.isoformat(), canonical_json(metadata or {}), now),
        )
        self.connection.execute(
            "INSERT OR IGNORE INTO unified_conversation_control(deal_id,account_id,thread_id,state,reason,evidence_json,updated_at) "
            "VALUES(?,?,?,'active','','{}',?)",
            (deal_id, account_id, thread_id, now),
        )
        return bool(cursor.rowcount)

    def last_automatic_reply(
        self,
        deal_id: str,
        *,
        account_id: str = "",
        thread_id: str = "",
    ) -> dict[str, Any] | None:
        """Return the latest durably recorded Hermes outbound in this thread."""
        clauses = ["deal_id=?", "origin='hermes_automatic'"]
        values: list[Any] = [str(deal_id)]
        if account_id:
            clauses.append("account_id=?")
            values.append(str(account_id))
        if thread_id:
            clauses.append("thread_id=?")
            values.append(str(thread_id))
        row = self.connection.execute(
            "SELECT account_id,message_id,deal_id,thread_id,direction,origin,occurred_at,metadata_json "
            f"FROM unified_conversation_messages WHERE {' AND '.join(clauses)} "
            "ORDER BY occurred_at DESC,created_at DESC LIMIT 1",
            tuple(values),
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["metadata"] = json.loads(result.pop("metadata_json") or "{}")
        return result

    def automation_control(self, deal_id: str, *, account_id: str, thread_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT * FROM unified_conversation_control WHERE deal_id=? AND account_id=? AND thread_id=?",
            (deal_id, account_id, thread_id),
        ).fetchone()
        if not row:
            return {
                "deal_id": deal_id, "account_id": account_id, "thread_id": thread_id,
                "state": "active", "reason": "", "evidence": {},
                "baseline_message_count": 0, "baseline_automatic_count": 0, "baseline_at": None,
            }
        result = dict(row)
        result["evidence"] = json.loads(result.pop("evidence_json") or "{}")
        return result

    def pause_automation(
        self,
        deal_id: str,
        *,
        account_id: str,
        thread_id: str,
        reason: str,
        actor: str,
        evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        deal_status = (
            "conversation_handoff"
            if reason in {"conversation_message_limit", "conversation_age_limit"}
            else "review_required"
        )
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            current = self.automation_control(deal_id, account_id=account_id, thread_id=thread_id)
            self.connection.execute(
                "INSERT INTO unified_conversation_control(deal_id,account_id,thread_id,state,reason,evidence_json,paused_at,updated_at) "
                "VALUES(?,?,?,'paused',?,?,?,?) ON CONFLICT(deal_id,account_id,thread_id) DO UPDATE SET "
                "state='paused',reason=excluded.reason,evidence_json=excluded.evidence_json,paused_at=COALESCE(unified_conversation_control.paused_at,excluded.paused_at),updated_at=excluded.updated_at",
                (deal_id, account_id, thread_id, str(reason), canonical_json(evidence or {}), now, now),
            )
            if current.get("state") != "paused" or current.get("reason") != reason:
                self.connection.execute(
                    "INSERT INTO unified_automation_events(deal_id,account_id,thread_id,previous_state,new_state,reason,actor,evidence_json,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (deal_id, account_id, thread_id, str(current.get("state") or "active"), "paused", str(reason), str(actor), canonical_json(evidence or {}), now),
                )
            self.connection.execute(
                "UPDATE unified_deals SET status=?,"
                "scope_display=CASE WHEN status IN ('review_required','conversation_handoff') "
                "AND COALESCE(scope_display,'')<>'' THEN scope_display ELSE ? END,"
                "updated_at=? WHERE deal_id=?",
                (deal_status, str(reason), now, deal_id),
            )
            self.connection.execute("COMMIT")
        except Exception:
            try:
                self.connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        return self.automation_control(deal_id, account_id=account_id, thread_id=thread_id)

    def resume_automation(
        self,
        deal_id: str,
        *,
        account_id: str,
        thread_id: str,
        actor: str,
        reason: str,
    ) -> dict[str, Any]:
        now = utc_now()
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            current = self.automation_control(deal_id, account_id=account_id, thread_id=thread_id)
            counts = self.connection.execute(
                "SELECT COUNT(*) AS messages, SUM(CASE WHEN origin='hermes_automatic' THEN 1 ELSE 0 END) AS automatic, MAX(occurred_at) AS last_at "
                "FROM unified_conversation_messages WHERE deal_id=? AND account_id=? AND thread_id=?",
                (deal_id, account_id, thread_id),
            ).fetchone()
            message_count = int(counts["messages"] or 0)
            automatic_count = int(counts["automatic"] or 0)
            baseline_at = str(counts["last_at"] or now)
            self.connection.execute(
                "INSERT INTO unified_conversation_control(deal_id,account_id,thread_id,state,reason,evidence_json,resumed_at,baseline_message_count,baseline_automatic_count,baseline_at,updated_at) "
                "VALUES(?,?,?,'active','','{}',?,?,?,?,?) ON CONFLICT(deal_id,account_id,thread_id) DO UPDATE SET "
                "state='active',reason='',evidence_json='{}',paused_at=NULL,resumed_at=excluded.resumed_at,"
                "baseline_message_count=excluded.baseline_message_count,baseline_automatic_count=excluded.baseline_automatic_count,baseline_at=excluded.baseline_at,updated_at=excluded.updated_at",
                (deal_id, account_id, thread_id, now, message_count, automatic_count, baseline_at, now),
            )
            self.connection.execute(
                "INSERT INTO unified_automation_events(deal_id,account_id,thread_id,previous_state,new_state,reason,actor,evidence_json,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (deal_id, account_id, thread_id, str(current.get("state") or "active"), "active", str(reason), str(actor), "{}", now),
            )
            if current.get("state") == "paused" and current.get("reason"):
                self.connection.execute(
                    "UPDATE unified_deals SET status='analysing',scope_display=NULL,updated_at=? "
                    "WHERE deal_id=? AND status IN ('review_required','conversation_handoff') AND scope_display=?",
                    (now, deal_id, str(current.get("reason"))),
                )
            self.connection.execute("COMMIT")
        except Exception:
            try:
                self.connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        return self.automation_control(deal_id, account_id=account_id, thread_id=thread_id)

    def automation_events(self, deal_id: str, account_id: str, thread_id: str) -> list[dict[str, Any]]:
        return [
            dict(row) for row in self.connection.execute(
                "SELECT * FROM unified_automation_events WHERE deal_id=? AND account_id=? AND thread_id=? ORDER BY event_id",
                (deal_id, account_id, thread_id),
            ).fetchall()
        ]

    def evaluate_automation(
        self,
        deal_id: str,
        *,
        account_id: str,
        thread_id: str,
        material_scope_change: bool = False,
        action_kind: str = "pre_offer",
        now: str | None = None,
    ) -> dict[str, Any]:
        control = self.automation_control(deal_id, account_id=account_id, thread_id=thread_id)
        if control.get("state") == "paused":
            if control.get("reason") == "automatic_reply_limit" and action_kind == "final_offer":
                pass
            else:
                return {"allowed": False, "reason": str(control.get("reason") or "paused"), "control": control}
        if material_scope_change:
            paused = self.pause_automation(
                deal_id, account_id=account_id, thread_id=thread_id,
                reason="material_scope_change", actor="conversation_policy", evidence={},
            )
            return {"allowed": False, "reason": "material_scope_change", "control": paused}
        counts = self.connection.execute(
            "SELECT COUNT(*) AS messages, SUM(CASE WHEN origin='hermes_automatic' THEN 1 ELSE 0 END) AS automatic, "
            "MIN(occurred_at) AS first_at, MAX(occurred_at) AS last_at "
            "FROM unified_conversation_messages WHERE deal_id=? AND account_id=? AND thread_id=?",
            (deal_id, account_id, thread_id),
        ).fetchone()
        messages = int(counts["messages"] or 0) - int(control.get("baseline_message_count") or 0)
        automatic = int(counts["automatic"] or 0) - int(control.get("baseline_automatic_count") or 0)
        reason = ""
        if messages >= MAX_CONVERSATION_MESSAGES:
            reason = "conversation_message_limit"
        else:
            first_at = parse_time(str(control.get("baseline_at") or counts["first_at"] or ""))
            current = parse_time(now or utc_now())
            if first_at and current and current - first_at >= timedelta(days=MAX_CONVERSATION_AGE_DAYS):
                reason = "conversation_age_limit"
        if reason:
            paused = self.pause_automation(
                deal_id, account_id=account_id, thread_id=thread_id,
                reason=reason, actor="conversation_policy",
                evidence={"message_count": messages, "automatic_reply_count": automatic},
            )
            return {"allowed": False, "reason": reason, "control": paused}
        if action_kind != "final_offer" and automatic >= MAX_AUTOMATIC_REPLIES:
            return {
                "allowed": False,
                "reason": "automatic_reply_limit",
                "control": control,
                "message_count": messages,
                "automatic_reply_count": automatic,
            }
        return {
            "allowed": True, "reason": "clear", "control": control,
            "message_count": messages, "automatic_reply_count": automatic,
        }

    def known_automatic_message_ids(self, deal_id: str) -> set[str]:
        rows = self.connection.execute(
            "SELECT external_message_id FROM unified_artifacts WHERE deal_id=? AND status='sent' AND external_message_id IS NOT NULL",
            (deal_id,),
        ).fetchall()
        return {str(row["external_message_id"]) for row in rows if row["external_message_id"]}

    def has_sent_pre_offer(self, deal_id: str) -> bool:
        """Return whether this deal already has a durably recorded pre-offer send."""
        row = self.connection.execute(
            "SELECT 1 FROM unified_artifacts WHERE deal_id=? AND stage LIKE 'response:%' AND status='sent' LIMIT 1",
            (deal_id,),
        ).fetchone()
        return row is not None

    def claim_draft(self, deal_id: str, stage: str, *, content_hash: str) -> bool:
        deal = self.get_deal(deal_id)
        if not deal:
            return False
        now = utc_now()
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            existing = self.connection.execute(
                "SELECT status FROM unified_artifacts WHERE deal_id=? AND stage=?", (deal_id, stage)
            ).fetchone()
            if existing and str(existing["status"]) == "retryable_failed":
                self.connection.execute(
                    "UPDATE unified_artifacts SET status='creating',content_hash=?,updated_at=? WHERE deal_id=? AND stage=?",
                    (content_hash, now, deal_id, stage),
                )
                self.connection.execute(
                    "UPDATE unified_deals SET status='analysing',updated_at=? WHERE deal_id=?", (now, deal_id)
                )
                self.connection.execute("COMMIT")
                return True
            if existing or deal.get("status") == "review_required":
                self.connection.execute("ROLLBACK")
                return False
            self.connection.execute(
                "INSERT INTO unified_artifacts(deal_id,stage,status,content_hash,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (deal_id, stage, "creating", content_hash, now, now),
            )
            self.connection.execute("COMMIT")
            return True
        except Exception:
            try:
                self.connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise

    def record_draft(self, deal_id: str, stage: str, *, draft_id: str, content_hash: str) -> None:
        now = utc_now()
        self.connection.execute(
            "INSERT INTO unified_artifacts(deal_id,stage,status,content_hash,external_draft_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(deal_id,stage) DO UPDATE SET status='created',content_hash=excluded.content_hash,external_draft_id=excluded.external_draft_id,updated_at=excluded.updated_at",
            (deal_id, stage, "created", content_hash, draft_id, now, now),
        )
        self.connection.execute(
            "UPDATE unified_deals SET status='draft_ready',current_draft_id=?,updated_at=? WHERE deal_id=?",
            (draft_id, now, deal_id),
        )

    def outbox_operation(self, operation_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM unified_outbox WHERE operation_id=?",
            (str(operation_id),),
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["provider_evidence"] = json.loads(result.pop("provider_evidence_json", "{}") or "{}")
        result["validation_errors"] = json.loads(result.pop("validation_errors_json", "[]") or "[]")
        return result

    def _record_response_event_tx(
        self,
        *,
        operation_id: str,
        deal_id: str,
        previous_phase: str,
        new_phase: str,
        evidence: dict[str, Any] | None = None,
        now: str,
    ) -> None:
        self.connection.execute(
            "INSERT INTO unified_response_events(operation_id,deal_id,previous_phase,new_phase,evidence_json,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (operation_id, deal_id, previous_phase, new_phase, canonical_json(evidence or {}), now),
        )

    def mark_response_phase(self, operation_id: str, phase: str) -> bool:
        """CAS one non-terminal response phase and retain an auditable transition."""
        if phase not in {"content_validated", "transport_starting", "post_started"}:
            raise ValueError("invalid_active_response_phase")
        allowed = {
            "content_validated": {"preparing"},
            "transport_starting": {"content_validated"},
            "post_started": {"transport_starting"},
        }
        now = utc_now()
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            row = self.connection.execute(
                "SELECT deal_id,phase,status FROM unified_outbox WHERE operation_id=?",
                (str(operation_id),),
            ).fetchone()
            if not row:
                self.connection.execute("ROLLBACK")
                return False
            previous = str(row["phase"] or ("preparing" if row["status"] == "claimed" else ""))
            if previous not in allowed[phase]:
                self.connection.execute("ROLLBACK")
                return False
            status = "in_progress" if phase == "post_started" else "claimed"
            self.connection.execute(
                "UPDATE unified_outbox SET status=?,phase=?,post_started_at=CASE WHEN ?='post_started' THEN ? "
                "ELSE post_started_at END,updated_at=? WHERE operation_id=?",
                (status, phase, phase, now, now, str(operation_id)),
            )
            self.connection.execute(
                "UPDATE unified_artifacts SET phase=?,outcome=?,updated_at=? WHERE operation_id=?",
                (phase, phase, now, str(operation_id)),
            )
            self._record_response_event_tx(
                operation_id=str(operation_id), deal_id=str(row["deal_id"]), previous_phase=previous,
                new_phase=phase, now=now,
            )
            self.connection.execute("COMMIT")
            return True
        except Exception:
            try:
                self.connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise

    def mark_content_validated(self, operation_id: str) -> bool:
        return self.mark_response_phase(operation_id, "content_validated")

    def mark_transport_starting(self, operation_id: str) -> bool:
        return self.mark_response_phase(operation_id, "transport_starting")

    def claim_response(
        self,
        deal_id: str,
        stage: str,
        *,
        content_hash: str,
        operation_id: str = "",
        owner: str = "",
        lease_seconds: int = 300,
        message_type: str = "acknowledgement",
        recipient: str = "",
        source_type: str = "legacy",
        source_key: str = "",
        thread_id: str = "",
        marker: str = "",
    ) -> bool:
        """Atomically claim one response; active/unknown operations never re-claim."""
        deal = self.get_deal(deal_id)
        if not deal or str(deal.get("status") or "") == "review_required":
            return False
        operation_id = str(operation_id or f"send-{digest(f'{deal_id}|{stage}|{content_hash}')[:32]}")
        owner = str(owner or default_claim_owner())
        source_key = str(source_key or stage)
        recipient = normalize_email(recipient or str(deal.get("primary_email") or ""))
        contact_identity = contact_identity_for_email(recipient)
        if message_type not in MESSAGE_TYPES or not recipient or not contact_identity or lease_seconds < 1:
            return False
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat(timespec="seconds")
        lease_expires = (now_dt + timedelta(seconds=lease_seconds)).isoformat(timespec="seconds")
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            artifact = self.connection.execute(
                "SELECT status,operation_id FROM unified_artifacts WHERE deal_id=? AND stage=?",
                (deal_id, stage),
            ).fetchone()
            if artifact and str(artifact["operation_id"] or "") not in {operation_id}:
                self.connection.execute("ROLLBACK")
                return False
            existing = self.connection.execute(
                "SELECT * FROM unified_outbox WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
            if existing:
                expected_binding = (
                    str(deal_id), str(source_type), source_key, str(stage), str(content_hash),
                    message_type, recipient, contact_identity, str(thread_id),
                )
                durable_binding = (
                    str(existing["deal_id"] or ""), str(existing["source_type"] or ""),
                    str(existing["source_key"] or ""), str(existing["stage"] or ""),
                    str(existing["input_hash"] or ""), str(existing["message_type"] or ""),
                    str(existing["recipient"] or ""), str(existing["contact_identity"] or ""),
                    str(existing["thread_id"] or ""),
                )
                if durable_binding != expected_binding:
                    self.connection.execute("ROLLBACK")
                    return False
                status = str(existing["status"] or "")
                if status in OUTBOX_TERMINAL_STATUSES or status in OUTBOX_RECONCILIATION_STATUSES:
                    self.connection.execute("ROLLBACK")
                    return False
                active_until = parse_time(str(existing["lease_expires_at"] or ""))
                if status == "claimed" and active_until and active_until > now_dt:
                    self.connection.execute("ROLLBACK")
                    return False
                if status not in {"claimed", "retryable_failed", "retry_scheduled"}:
                    self.connection.execute("ROLLBACK")
                    return False
                self.connection.execute(
                    "UPDATE unified_outbox SET status='claimed',phase='preparing',owner=?,started_at=?,lease_expires_at=?,"
                    "last_error='',post_started_at=NULL,terminal_at=NULL,updated_at=? "
                    "WHERE operation_id=?",
                    (owner, now, lease_expires, now, operation_id),
                )
            else:
                self.connection.execute(
                    "INSERT INTO unified_outbox("
                    "operation_id,deal_id,source_type,source_key,stage,message_type,recipient,contact_identity,"
                    "thread_id,input_hash,"
                    "status,phase,owner,started_at,lease_expires_at,marker,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,'claimed','preparing',?,?,?,?,?,?)",
                    (
                        operation_id, deal_id, str(source_type), source_key, stage, message_type, recipient,
                        contact_identity, str(thread_id), str(content_hash), owner, now, lease_expires,
                        str(marker), now, now,
                    ),
                )
            self.connection.execute(
                "INSERT INTO unified_artifacts("
                "deal_id,stage,status,content_hash,created_at,updated_at,operation_id,owner,started_at,lease_expires_at,"
                "message_type,recipient,contact_identity,source_type,source_key,outcome,phase) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(deal_id,stage) DO UPDATE SET "
                "status='creating',content_hash=excluded.content_hash,updated_at=excluded.updated_at,"
                "operation_id=excluded.operation_id,owner=excluded.owner,started_at=excluded.started_at,"
                "lease_expires_at=excluded.lease_expires_at,message_type=excluded.message_type,"
                "recipient=excluded.recipient,contact_identity=excluded.contact_identity,"
                "source_type=excluded.source_type,source_key=excluded.source_key,outcome='preparing',phase='preparing'",
                (
                    deal_id, stage, "creating", str(content_hash), now, now, operation_id, owner, now,
                    lease_expires, message_type, recipient, contact_identity, str(source_type), source_key,
                    "preparing", "preparing",
                ),
            )
            self._record_response_event_tx(
                operation_id=operation_id, deal_id=deal_id, previous_phase="",
                new_phase="preparing", evidence={"source_type": source_type, "source_key": source_key}, now=now,
            )
            self.connection.execute("COMMIT")
            return True
        except sqlite3.IntegrityError:
            try:
                self.connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            return False
        except Exception:
            try:
                self.connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise

    def mark_outbox_in_progress(self, operation_id: str) -> bool:
        return self.mark_response_phase(operation_id, "post_started")

    def bind_outbox_content(self, operation_id: str, *, content_hash: str) -> bool:
        """Bind the exact rendered transport payload while the claim is held."""
        value = str(content_hash or "")
        if not value:
            return False
        now = utc_now()
        cursor = self.connection.execute(
            "UPDATE unified_outbox SET content_hash=?,updated_at=? "
            "WHERE operation_id=? AND status='claimed' AND phase IN ('preparing','content_validated') "
            "AND (content_hash='' OR content_hash=?)",
            (value, now, str(operation_id), value),
        )
        if cursor.rowcount:
            self.connection.execute(
                "UPDATE unified_artifacts SET content_hash=?,updated_at=? WHERE operation_id=?",
                (value, now, str(operation_id)),
            )
        return cursor.rowcount == 1

    def update_outbox_policy(self, operation_id: str, *, message_type: str, status: str = "") -> None:
        from message_policy import MANUAL_REVIEW
        if message_type not in MESSAGE_TYPES | {MANUAL_REVIEW}:
            raise ValueError("invalid_outbox_message_type")
        if status and status not in OUTBOX_STATUSES:
            raise ValueError("invalid_outbox_status")
        now = utc_now()
        if status:
            self.connection.execute(
                "UPDATE unified_outbox SET message_type=?,status=?,updated_at=? WHERE operation_id=?",
                (message_type, status, now, str(operation_id)),
            )
        else:
            self.connection.execute(
                "UPDATE unified_outbox SET message_type=?,updated_at=? WHERE operation_id=?",
                (message_type, now, str(operation_id)),
            )
        self.connection.execute(
            "UPDATE unified_artifacts SET message_type=?,outcome=COALESCE(NULLIF(?,''),outcome),updated_at=? "
            "WHERE operation_id=?",
            (message_type, status, now, str(operation_id)),
        )

    def _create_review_task_tx(
        self,
        *,
        operation: sqlite3.Row | dict[str, Any],
        reason_codes: list[str],
        rejected_body: str,
        validation_errors: list[str],
        provider_evidence: dict[str, Any],
        now: str,
    ) -> str:
        operation_id = str(operation["operation_id"])
        task_id = "review-" + digest(operation_id)[:28]
        sla_at = (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat(timespec="seconds")
        self.connection.execute(
            "INSERT INTO unified_review_tasks("
            "task_id,source_type,source_key,deal_id,operation_id,reason_codes_json,rejected_body,"
            "validation_errors_json,recipient,provider_evidence_json,created_at,sla_at,"
            "notification_status,status,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'open',?) "
            "ON CONFLICT(operation_id) DO UPDATE SET reason_codes_json=excluded.reason_codes_json,"
            "rejected_body=excluded.rejected_body,validation_errors_json=excluded.validation_errors_json,"
            "recipient=excluded.recipient,provider_evidence_json=excluded.provider_evidence_json,"
            "notification_status=CASE WHEN unified_review_tasks.status='open' "
            "AND unified_review_tasks.notification_status='sent' THEN 'sent' ELSE 'pending' END,"
            "status='open',updated_at=excluded.updated_at",
            (
                task_id, str(operation["source_type"]), str(operation["source_key"]),
                str(operation["deal_id"]), operation_id, canonical_json(reason_codes),
                str(rejected_body or ""), canonical_json(validation_errors), str(operation["recipient"] or ""),
                canonical_json(provider_evidence), now, sla_at, "pending", now,
            ),
        )
        return task_id

    def ensure_legacy_review_task(
        self,
        *,
        source_key: str,
        reason_codes: list[str] | tuple[str, ...],
        rejected_body: str,
        validation_errors: list[str] | tuple[str, ...] = (),
        recipient: str = "",
        provider_evidence: dict[str, Any] | None = None,
        source_type: str = "legacy_mail",
    ) -> dict[str, str]:
        """Create one idempotent terminal review projection for a legacy source.

        Historical pipeline rows can predate the unified event/outbox tables.  A
        source may be moved out of ``done`` only after this transaction creates
        its durable deal, event, terminal operation/artifact, source disposition
        and review task.  The synthetic deal has no identity binding, so it
        cannot affect routing of later customer mail.  This method has no send
        capability.
        """
        source_key = str(source_key or "").strip()
        source_type = str(source_type or "legacy_mail").strip()
        reasons = list(dict.fromkeys(str(item) for item in reason_codes if str(item)))
        errors = list(dict.fromkeys(str(item) for item in validation_errors if str(item)))
        body = str(rejected_body or "").strip()
        if not source_key or not source_type or not reasons or not body:
            raise ValueError("legacy_review_requires_source_reasons_and_rejected_body")
        recipient = normalize_email(recipient)
        contact_identity = contact_identity_for_email(recipient)
        evidence = {"legacy_reconciliation": True, **dict(provider_evidence or {})}
        fingerprint = digest(f"{source_type}:{source_key}")
        deal_id = "legacy-deal-" + fingerprint[:24]
        operation_id = "legacy-review:" + fingerprint
        stage = "legacy_reconciliation"
        now = utc_now()
        content_hash = digest(body)
        recipient_evidence = {
            "source": "legacy_state",
            "resolved_reply_recipient": recipient,
            "reply_all": False,
            "resolution": "resolved" if recipient else "unavailable",
        }
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self.connection.execute(
                "INSERT INTO unified_deals(deal_id,status,primary_email,company,contact_name,facts_json,context_json,"
                "scope_display,is_correlation_case,created_at,updated_at) VALUES(?, 'review_required', ?, '', '', '{}',"
                "'[]', ?, 1, ?, ?) ON CONFLICT(deal_id) DO UPDATE SET status='review_required',scope_display=excluded.scope_display,"
                "updated_at=excluded.updated_at",
                (deal_id, recipient or None, ",".join(reasons)[:500], now, now),
            )
            self.connection.execute(
                "INSERT INTO unified_events(source_type,source_key,deal_id,relation,email,company,contact_name,content_hash,"
                "thread_id,metadata_json,resolved_reply_recipient,recipient_evidence_json,content_version,created_at) "
                "VALUES(?,?,?,'legacy',?,'','',?,'',?,?,?,?,?) ON CONFLICT(source_type,source_key) DO NOTHING",
                (
                    source_type, source_key, deal_id, recipient or None, content_hash,
                    canonical_json({"legacy_reconciliation": True, "provider_evidence": evidence}),
                    recipient, canonical_json(recipient_evidence), content_hash, now,
                ),
            )
            self.connection.execute(
                "INSERT INTO unified_outbox(operation_id,deal_id,source_type,source_key,stage,message_type,recipient,"
                "contact_identity,thread_id,input_hash,content_hash,status,phase,owner,started_at,lease_expires_at,marker,"
                "last_error,created_at,updated_at,terminal_at,provider_evidence_json,validation_errors_json) "
                "VALUES(?,?,?,?,?,'manual_review',?,?, '',?,?, 'manual_review','manual_review','','','','',?,?,?, ?,?,?) "
                "ON CONFLICT(operation_id) DO UPDATE SET status='manual_review',phase='manual_review',owner='',started_at='',"
                "lease_expires_at='',last_error=excluded.last_error,terminal_at=excluded.terminal_at,"
                "provider_evidence_json=excluded.provider_evidence_json,validation_errors_json=excluded.validation_errors_json,"
                "updated_at=excluded.updated_at",
                (
                    operation_id, deal_id, source_type, source_key, stage, recipient, contact_identity,
                    fingerprint, content_hash, ",".join(reasons)[:500], now, now, now,
                    canonical_json(evidence), canonical_json(errors),
                ),
            )
            self.connection.execute(
                "INSERT INTO unified_artifacts(deal_id,stage,status,content_hash,created_at,updated_at,operation_id,owner,"
                "started_at,lease_expires_at,message_type,recipient,contact_identity,source_type,source_key,outcome,phase,"
                "terminal_at,provider_evidence_json) VALUES(?,?, 'manual_review', ?,?,?,?,'','','','manual_review',?,?,?,?,"
                "'manual_review','manual_review',?,?) ON CONFLICT(deal_id,stage) DO UPDATE SET status='manual_review',"
                "outcome='manual_review',phase='manual_review',owner='',started_at='',lease_expires_at='',"
                "terminal_at=excluded.terminal_at,provider_evidence_json=excluded.provider_evidence_json,"
                "updated_at=excluded.updated_at",
                (
                    deal_id, stage, content_hash, now, now, operation_id, recipient, contact_identity,
                    source_type, source_key, now, canonical_json(evidence),
                ),
            )
            operation = {
                "operation_id": operation_id, "source_type": source_type, "source_key": source_key,
                "deal_id": deal_id, "recipient": recipient,
            }
            task_id = self._create_review_task_tx(
                operation=operation, reason_codes=reasons, rejected_body=body,
                validation_errors=errors, provider_evidence=evidence, now=now,
            )
            disposition_evidence = {
                "outcome": "manual_review", "review_task_id": task_id,
                "reason_codes": reasons, "legacy_reconciliation": True,
            }
            self.connection.execute(
                "INSERT INTO unified_source_dispositions(source_type,source_key,deal_id,operation_id,disposition,evidence_json,"
                "updated_at) VALUES(?,?,?,?, 'manual_action_required', ?,?) ON CONFLICT(source_type,source_key) DO UPDATE SET "
                "deal_id=excluded.deal_id,operation_id=excluded.operation_id,disposition='manual_action_required',"
                "evidence_json=excluded.evidence_json,updated_at=excluded.updated_at",
                (source_type, source_key, deal_id, operation_id, canonical_json(disposition_evidence), now),
            )
            self.connection.execute(
                "INSERT INTO unified_response_events(operation_id,deal_id,previous_phase,new_phase,evidence_json,created_at) "
                "SELECT ?,?,'legacy','manual_review',?,? WHERE NOT EXISTS (SELECT 1 FROM unified_response_events "
                "WHERE operation_id=? AND previous_phase='legacy' AND new_phase='manual_review')",
                (operation_id, deal_id, canonical_json(disposition_evidence), now, operation_id),
            )
            self.connection.execute("COMMIT")
            return {"deal_id": deal_id, "operation_id": operation_id, "task_id": task_id}
        except Exception:
            try:
                self.connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise

    def finalize_response_attempt(
        self,
        operation_id: str,
        *,
        outcome: str,
        external_message_id: str = "",
        external_draft_id: str = "",
        provider_evidence: dict[str, Any] | None = None,
        reason_codes: list[str] | tuple[str, ...] = (),
        rejected_body: str = "",
        validation_errors: list[str] | tuple[str, ...] = (),
        source_disposition: str = "",
        reconcile_terminal_claim: bool = False,
    ) -> bool:
        """Atomically converge operation, outbox, artifact, claim, deal and review.

        ``outcome_unknown`` is impossible before the durable ``post_started``
        transition. A successful send requires provider-locatable evidence.
        """
        outcome = str(outcome or "")
        if outcome not in {
            "sent", "draft_created", "validation_failed", "manual_review", "retry_scheduled",
            "outcome_unknown",
        }:
            raise ValueError("invalid_response_outcome")
        evidence = dict(provider_evidence or {})
        reasons = list(dict.fromkeys(str(item) for item in reason_codes if str(item)))
        errors = list(dict.fromkeys(str(item) for item in validation_errors if str(item)))
        provider_message_id = str(external_message_id or evidence.get("provider_message_id") or "").strip()
        marker_match = bool(evidence.get("sent_marker_match")) and bool(evidence.get("marker"))
        if outcome == "sent":
            if provider_message_id.startswith("zoho-accepted:"):
                return False
            if not provider_message_id and not marker_match:
                return False
            if not provider_message_id:
                provider_message_id = "sent-marker:" + digest(canonical_json(evidence))[:24]

        now = utc_now()
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            row = self.connection.execute(
                "SELECT * FROM unified_outbox WHERE operation_id=?", (str(operation_id),)
            ).fetchone()
            if not row:
                self.connection.execute("ROLLBACK")
                return False
            phase = str(row["phase"] or "")
            status = str(row["status"] or "")
            terminal_repair_allowed = False
            if phase in {"sent", "sent_manually", "draft_created", "validation_failed", "manual_review"}:
                terminal_repair_allowed = (
                    reconcile_terminal_claim
                    and str(row["owner"] or "").startswith("reaper:")
                    and (
                        (
                            outcome == "manual_review"
                            and status in {"permanent_failed", "validation_failed"}
                        )
                        or (outcome == "sent" and status == "sent" and phase == "sent")
                    )
                )
                if not terminal_repair_allowed:
                    self.connection.execute("ROLLBACK")
                    return phase == outcome
            if outcome == "outcome_unknown" and phase != "post_started":
                self.connection.execute("ROLLBACK")
                return False
            if outcome == "retry_scheduled" and phase == "post_started" and not evidence.get("provider_rejected"):
                self.connection.execute("ROLLBACK")
                return False
            if outcome == "sent" and phase not in {"post_started", "outcome_unknown"} and not terminal_repair_allowed:
                self.connection.execute("ROLLBACK")
                return False
            if outcome == "draft_created" and not str(external_draft_id or evidence.get("provider_draft_id") or ""):
                self.connection.execute("ROLLBACK")
                return False

            durable_status = outcome
            artifact_status = {
                "sent": "sent", "draft_created": "created", "validation_failed": "failed",
                "manual_review": "manual_review", "retry_scheduled": "retry_scheduled",
                "outcome_unknown": "outcome_unknown",
            }[outcome]
            error_text = ",".join(reasons or errors)[:500]
            self.connection.execute(
                "UPDATE unified_outbox SET status=?,phase=?,external_message_id=COALESCE(NULLIF(?,''),external_message_id),"
                "last_error=?,owner='',started_at='',lease_expires_at='',terminal_at=CASE WHEN ? IN "
                "('sent','draft_created','validation_failed','manual_review') THEN ? ELSE NULL END,"
                "provider_evidence_json=?,validation_errors_json=?,updated_at=? WHERE operation_id=?",
                (
                    durable_status, outcome, provider_message_id, error_text, outcome, now,
                    canonical_json(evidence), canonical_json(errors), now, str(operation_id),
                ),
            )
            self.connection.execute(
                "UPDATE unified_artifacts SET status=?,phase=?,outcome=?,external_message_id=COALESCE(NULLIF(?,''),external_message_id),"
                "external_draft_id=COALESCE(NULLIF(?,''),external_draft_id),owner='',started_at='',lease_expires_at='',"
                "terminal_at=CASE WHEN ? IN ('sent','draft_created','validation_failed','manual_review') THEN ? ELSE NULL END,"
                "provider_evidence_json=?,updated_at=? WHERE operation_id=?",
                (
                    artifact_status, outcome, outcome, provider_message_id, str(external_draft_id),
                    outcome, now, canonical_json(evidence), now, str(operation_id),
                ),
            )

            deal_status = {
                "sent": "waiting_for_customer", "draft_created": "draft_ready",
                "validation_failed": "review_required", "manual_review": "review_required",
                "retry_scheduled": "analysing", "outcome_unknown": "review_required",
            }[outcome]
            self.connection.execute(
                "UPDATE unified_deals SET status=?,last_response_id=CASE WHEN ?='sent' THEN ? ELSE last_response_id END,"
                "current_draft_id=CASE WHEN ?='draft_created' THEN ? ELSE current_draft_id END,"
                "scope_display=CASE WHEN ? IN ('validation_failed','manual_review','outcome_unknown') "
                "THEN ? ELSE scope_display END,updated_at=? WHERE deal_id=?",
                (
                    deal_status, outcome, provider_message_id, outcome, str(external_draft_id),
                    outcome, error_text, now, str(row["deal_id"]),
                ),
            )
            task_id = ""
            if outcome in {"validation_failed", "manual_review", "outcome_unknown"}:
                task_id = self._create_review_task_tx(
                    operation=row, reason_codes=reasons or [outcome], rejected_body=rejected_body,
                    validation_errors=errors, provider_evidence=evidence, now=now,
                )
            else:
                self.connection.execute(
                    "UPDATE unified_review_tasks SET status='closed',updated_at=? "
                    "WHERE operation_id=? AND status='open'",
                    (now, str(operation_id)),
                )

            disposition = source_disposition or {
                "sent": "customer_succeeded", "draft_created": "draft_verified",
                "validation_failed": "manual_action_required", "manual_review": "manual_action_required",
                "retry_scheduled": "retry_scheduled", "outcome_unknown": "outcome_unknown",
            }[outcome]
            disposition_evidence = {
                "outcome": outcome,
                "provider_message_id": provider_message_id,
                "external_draft_id": str(external_draft_id),
                "review_task_id": task_id,
                "reason_codes": reasons,
            }
            self.connection.execute(
                "INSERT INTO unified_source_dispositions(source_type,source_key,deal_id,operation_id,disposition,evidence_json,updated_at) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(source_type,source_key) DO UPDATE SET "
                "deal_id=excluded.deal_id,operation_id=excluded.operation_id,disposition=excluded.disposition,"
                "evidence_json=excluded.evidence_json,updated_at=excluded.updated_at",
                (
                    str(row["source_type"]), str(row["source_key"]), str(row["deal_id"]),
                    str(operation_id), disposition, canonical_json(disposition_evidence), now,
                ),
            )
            self._record_response_event_tx(
                operation_id=str(operation_id), deal_id=str(row["deal_id"]), previous_phase=phase,
                new_phase=outcome, evidence=disposition_evidence, now=now,
            )
            self.connection.execute("COMMIT")
            return True
        except Exception:
            try:
                self.connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise

    def review_tasks(self, *, status: str = "open") -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM unified_review_tasks WHERE status=? ORDER BY created_at", (str(status),)
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            for key in ("reason_codes_json", "validation_errors_json", "provider_evidence_json"):
                item[key.removesuffix("_json")] = json.loads(item.pop(key) or ("[]" if key != "provider_evidence_json" else "{}"))
            result.append(item)
        return result

    def mark_review_task_notified(self, task_id: str, *, channel: str, provider_evidence: dict[str, Any]) -> bool:
        now = utc_now()
        cursor = self.connection.execute(
            "UPDATE unified_review_tasks SET notification_status='sent',provider_evidence_json=?,updated_at=? "
            "WHERE task_id=? AND status='open'",
            (canonical_json({"channel": str(channel), **dict(provider_evidence or {})}), now, str(task_id)),
        )
        return cursor.rowcount == 1

    def source_disposition(self, source_type: str, source_key: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM unified_source_dispositions WHERE source_type=? AND source_key=?",
            (str(source_type), str(source_key)),
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["evidence"] = json.loads(result.pop("evidence_json") or "{}")
        return result

    def artifact(self, deal_id: str, stage: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM unified_artifacts WHERE deal_id=? AND stage=?", (str(deal_id), str(stage))
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["provider_evidence"] = json.loads(result.pop("provider_evidence_json", "{}") or "{}")
        return result

    def record_validation_attempt(
        self,
        operation_id: str,
        *,
        deal_id: str,
        attempt_number: int,
        prompt_version: str,
        contract_digest: str,
        body: str,
        error_codes: list[str],
        offending_spans: list[dict[str, Any]],
        sanitizer_actions: list[str],
    ) -> None:
        self.connection.execute(
            "INSERT INTO unified_validation_attempts(operation_id,deal_id,attempt_number,prompt_version,contract_digest,"
            "content_hash,error_codes_json,offending_spans_json,sanitizer_actions_json,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(operation_id,attempt_number) DO UPDATE SET "
            "prompt_version=excluded.prompt_version,contract_digest=excluded.contract_digest,content_hash=excluded.content_hash,"
            "error_codes_json=excluded.error_codes_json,offending_spans_json=excluded.offending_spans_json,"
            "sanitizer_actions_json=excluded.sanitizer_actions_json,created_at=excluded.created_at",
            (
                str(operation_id), str(deal_id), int(attempt_number), str(prompt_version), str(contract_digest),
                digest(body), canonical_json(error_codes), canonical_json(offending_spans),
                canonical_json(sanitizer_actions), utc_now(),
            ),
        )

    def complete_outbox(self, operation_id: str, *, external_message_id: str) -> bool:
        return self.finalize_response_attempt(
            str(operation_id), outcome="sent", external_message_id=str(external_message_id),
            provider_evidence={"provider_message_id": str(external_message_id)},
        )

    def reconcile_outbox_sent(self, operation_id: str, *, external_message_id: str) -> bool:
        return self.finalize_response_attempt(
            str(operation_id), outcome="sent", external_message_id=str(external_message_id),
            provider_evidence={"provider_message_id": str(external_message_id), "reconciled": True},
        )

    def mark_outbox_outcome_unknown(self, operation_id: str, *, error: str) -> bool:
        return self.finalize_response_attempt(
            str(operation_id), outcome="outcome_unknown", reason_codes=[str(error)[:500]],
        )

    def fail_outbox(self, operation_id: str, *, error: str, retryable: bool = False) -> bool:
        return self.finalize_response_attempt(
            str(operation_id), outcome="retry_scheduled" if retryable else "validation_failed",
            reason_codes=[str(error)[:500]], validation_errors=[str(error)[:500]],
        )

    def unresolved_outcome_unknown(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM unified_outbox WHERE status='outcome_unknown' ORDER BY created_at"
        ).fetchall()
        return [dict(row) for row in rows]

    def detect_expired_claims(self, *, now: str | None = None) -> list[dict[str, Any]]:
        cutoff = parse_time(now or utc_now()) or datetime.now(timezone.utc)
        rows = self.connection.execute(
            "SELECT * FROM unified_outbox WHERE owner!='' AND lease_expires_at!='' "
            "AND status IN ('claimed','in_progress','permanent_failed','sent') ORDER BY lease_expires_at"
        ).fetchall()
        return [dict(row) for row in rows if (parse_time(str(row["lease_expires_at"] or "")) or cutoff) <= cutoff]

    def reap_expired_claims(self, *, now: str | None = None) -> list[dict[str, Any]]:
        """CAS expired leases into retry/reconciliation; this worker never sends."""
        results: list[dict[str, Any]] = []
        for candidate in self.detect_expired_claims(now=now):
            operation_id = str(candidate["operation_id"])
            owner = str(candidate.get("owner") or "")
            lease = str(candidate.get("lease_expires_at") or "")
            reaper_owner = f"reaper:{default_claim_owner()}"
            cursor = self.connection.execute(
                "UPDATE unified_outbox SET owner=? WHERE operation_id=? AND owner=? AND lease_expires_at=?",
                (reaper_owner, operation_id, owner, lease),
            )
            if cursor.rowcount != 1:
                continue
            phase = str(candidate.get("phase") or "")
            status = str(candidate.get("status") or "")
            if status == "sent" or phase == "sent":
                try:
                    evidence = json.loads(str(candidate.get("provider_evidence_json") or "{}"))
                except json.JSONDecodeError:
                    evidence = {}
                evidence.update({"reaper": True, "previous_status": status})
                finalized = self.finalize_response_attempt(
                    operation_id,
                    outcome="sent",
                    external_message_id=str(candidate.get("external_message_id") or ""),
                    provider_evidence=evidence,
                    reconcile_terminal_claim=True,
                )
                if not finalized:
                    self.connection.execute(
                        "UPDATE unified_outbox SET owner=? WHERE operation_id=? AND owner=? AND lease_expires_at=?",
                        (owner, operation_id, reaper_owner, lease),
                    )
                    continue
                outcome = "sent"
            elif status == "permanent_failed" or phase == "validation_failed":
                finalized = self.finalize_response_attempt(
                    operation_id,
                    outcome="manual_review",
                    reason_codes=["expired_terminal_failure_claim"],
                    validation_errors=[str(candidate.get("last_error") or "terminal_failure")],
                    provider_evidence={"reaper": True, "previous_status": str(candidate.get("status") or "")},
                    reconcile_terminal_claim=True,
                )
                if not finalized:
                    continue
                outcome = "manual_review"
            elif phase == "post_started" or str(candidate.get("status") or "") == "in_progress":
                self.finalize_response_attempt(
                    operation_id, outcome="outcome_unknown", reason_codes=["expired_post_started_lease"],
                )
                outcome = "outcome_unknown"
            else:
                self.finalize_response_attempt(
                    operation_id, outcome="retry_scheduled", reason_codes=["expired_pre_post_lease"],
                )
                outcome = "retry_scheduled"
            results.append({"operation_id": operation_id, "previous_phase": phase, "outcome": outcome})
        return results

    def record_manual_final_offer_sent(
        self,
        deal_id: str,
        *,
        external_message_id: str,
        sent_at: str,
        marker: str,
        pdf_hash: str,
        recipient: str,
        thread_id: str,
        evidence: dict[str, Any] | None = None,
    ) -> bool:
        deal_id = str(deal_id or "")
        recipient = normalize_email(recipient)
        thread_id = str(thread_id or "").strip()
        if not all((deal_id, external_message_id, sent_at, marker, pdf_hash, recipient, thread_id)):
            return False
        now = utc_now()
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            artifact = self.connection.execute(
                "SELECT * FROM unified_artifacts WHERE deal_id=? AND stage='final_offer'",
                (deal_id,),
            ).fetchone()
            if not artifact or str(artifact["status"] or "") not in {"created", "draft_created", "sent_manually"}:
                self.connection.execute("ROLLBACK")
                return False
            operation_id = str(artifact["operation_id"] or "")
            operation = None
            if operation_id:
                operation = self.connection.execute(
                    "SELECT * FROM unified_outbox WHERE operation_id=?", (operation_id,)
                ).fetchone()
                if (
                    not operation
                    or str(operation["deal_id"] or "") != deal_id
                    or str(operation["stage"] or "") != "final_offer"
                    or str(operation["message_type"] or "") != "final_offer"
                    or str(operation["status"] or "") not in {"draft_created", "sent_manually"}
                ):
                    self.connection.execute("ROLLBACK")
                    return False
            existing = self.connection.execute(
                "SELECT external_message_id,sent_at,marker,pdf_hash,recipient,thread_id "
                "FROM unified_manual_sends WHERE deal_id=? AND stage='final_offer'",
                (deal_id,),
            ).fetchone()
            if existing:
                expected = (
                    str(external_message_id), str(sent_at), str(marker), str(pdf_hash), recipient, thread_id,
                )
                durable = tuple(str(existing[key] or "") for key in (
                    "external_message_id", "sent_at", "marker", "pdf_hash", "recipient", "thread_id"
                ))
                if durable != expected:
                    self.connection.execute("ROLLBACK")
                    return False

            previous_phase = str(operation["phase"] or "") if operation else str(artifact["phase"] or "")
            artifact_evidence = json.loads(str(artifact["provider_evidence_json"] or "{}"))
            manual_evidence = {
                **artifact_evidence,
                **dict(evidence or {}),
                "provider_message_id": str(external_message_id),
                "sent_marker_match": True,
                "manual_send": True,
                "marker": str(marker),
                "pdf_hash": str(pdf_hash),
                "recipient": recipient,
                "thread_id": thread_id,
                "sent_at": str(sent_at),
            }
            if not existing:
                self.connection.execute(
                    "INSERT INTO unified_manual_sends(deal_id,stage,external_message_id,sent_at,marker,pdf_hash,recipient,"
                    "thread_id,evidence_json,created_at) VALUES(?,'final_offer',?,?,?,?,?,?,?,?)",
                    (
                        deal_id, str(external_message_id), str(sent_at), str(marker), str(pdf_hash), recipient,
                        thread_id, canonical_json(manual_evidence), now,
                    ),
                )
            else:
                self.connection.execute(
                    "UPDATE unified_manual_sends SET evidence_json=? WHERE deal_id=? AND stage='final_offer'",
                    (canonical_json(manual_evidence), deal_id),
                )
            if operation:
                self.connection.execute(
                    "UPDATE unified_outbox SET status='sent_manually',phase='sent_manually',external_message_id=?,"
                    "owner='',started_at='',lease_expires_at='',terminal_at=?,provider_evidence_json=?,updated_at=? "
                    "WHERE operation_id=?",
                    (
                        str(external_message_id), now, canonical_json(manual_evidence), now, operation_id,
                    ),
                )
            self.connection.execute(
                "UPDATE unified_artifacts SET status='sent_manually',phase='sent_manually',outcome='sent_manually',"
                "external_message_id=?,terminal_at=?,provider_evidence_json=?,updated_at=? "
                "WHERE deal_id=? AND stage='final_offer'",
                (str(external_message_id), now, canonical_json(manual_evidence), now, deal_id),
            )
            self.connection.execute(
                "UPDATE unified_deals SET status='completed',last_response_id=?,updated_at=? WHERE deal_id=?",
                (str(external_message_id), now, deal_id),
            )
            if operation:
                disposition_evidence = {
                    "outcome": "sent_manually",
                    "provider_message_id": str(external_message_id),
                    "external_draft_id": str(artifact["external_draft_id"] or ""),
                    "marker": str(marker),
                    "pdf_hash": str(pdf_hash),
                }
                self.connection.execute(
                    "INSERT INTO unified_source_dispositions(source_type,source_key,deal_id,operation_id,disposition,"
                    "evidence_json,updated_at) VALUES(?,?,?,?, 'draft_verified', ?,?) "
                    "ON CONFLICT(source_type,source_key) DO UPDATE SET deal_id=excluded.deal_id,"
                    "operation_id=excluded.operation_id,disposition='draft_verified',"
                    "evidence_json=excluded.evidence_json,updated_at=excluded.updated_at",
                    (
                        str(operation["source_type"]), str(operation["source_key"]), deal_id, operation_id,
                        canonical_json(disposition_evidence), now,
                    ),
                )
                self.connection.execute(
                    "UPDATE unified_review_tasks SET status='closed',updated_at=? "
                    "WHERE operation_id=? AND status='open'",
                    (now, operation_id),
                )
                if previous_phase != "sent_manually":
                    self._record_response_event_tx(
                        operation_id=operation_id,
                        deal_id=deal_id,
                        previous_phase=previous_phase,
                        new_phase="sent_manually",
                        evidence=disposition_evidence,
                        now=now,
                    )
            self.connection.execute("COMMIT")
            return True
        except Exception:
            try:
                self.connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise

    def invariant_violations(self, *, now: str | None = None) -> list[dict[str, Any]]:
        cutoff = str(now or utc_now())
        checks = {
            "stale_claimed": (
                "SELECT operation_id FROM unified_outbox WHERE status='claimed' AND lease_expires_at!='' AND lease_expires_at<?",
                (cutoff,),
            ),
            "orphan_artifact_creating": (
                "SELECT a.operation_id FROM unified_artifacts a LEFT JOIN unified_outbox o ON o.operation_id=a.operation_id "
                "WHERE a.status='creating' AND (o.operation_id IS NULL OR o.status IN "
                "('sent','sent_manually','draft_created','validation_failed','manual_review','permanent_failed'))",
                (),
            ),
            "permanent_failed_active_claim": (
                "SELECT operation_id FROM unified_outbox WHERE status='permanent_failed' AND owner!=''",
                (),
            ),
            "review_required_without_task": (
                "SELECT d.deal_id AS operation_id FROM unified_deals d LEFT JOIN unified_review_tasks r "
                "ON r.deal_id=d.deal_id AND r.status='open' WHERE d.status='review_required' AND r.task_id IS NULL",
                (),
            ),
            "duplicate_open_review_tasks": (
                "SELECT deal_id AS operation_id FROM unified_review_tasks WHERE status='open' "
                "GROUP BY deal_id HAVING COUNT(*)>1",
                (),
            ),
            "outcome_unknown": (
                "SELECT operation_id FROM unified_outbox WHERE status='outcome_unknown'",
                (),
            ),
            "projection_drift": (
                "SELECT o.operation_id FROM unified_outbox o JOIN unified_artifacts a ON a.operation_id=o.operation_id "
                "WHERE o.phase!=a.phase",
                (),
            ),
            "expired_lease": (
                "SELECT operation_id FROM unified_outbox WHERE owner!='' AND lease_expires_at!='' AND lease_expires_at<?",
                (cutoff,),
            ),
        }
        violations: list[dict[str, Any]] = []
        for code, (query, params) in checks.items():
            rows = self.connection.execute(query, params).fetchall()
            for row in rows:
                violations.append({"code": code, "operation_id": str(row["operation_id"] or "")})
        security = self.connection.execute(
            "SELECT security_event_id,event_type FROM unified_security_events WHERE event_type IN "
            "('zoho_account_folder_mismatch','recipient_resolution_failed') AND resolved_at IS NULL"
        ).fetchall()
        violations.extend(
            {"code": "account_folder_mismatch", "operation_id": str(row["security_event_id"]), "event_type": row["event_type"]}
            for row in security
        )
        return violations

    def record_response(
        self,
        deal_id: str,
        stage: str,
        *,
        message_id: str,
        content_hash: str,
        operation_id: str = "",
    ) -> None:
        now = utc_now()
        outbox = self.outbox_operation(operation_id) if operation_id else None
        durable_content_hash = str((outbox or {}).get("content_hash") or content_hash)
        self.connection.execute(
            "INSERT INTO unified_artifacts(deal_id,stage,status,content_hash,external_message_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(deal_id,stage) DO UPDATE SET status='sent',content_hash=excluded.content_hash,external_message_id=excluded.external_message_id,updated_at=excluded.updated_at",
            (deal_id, stage, "sent", durable_content_hash, message_id, now, now),
        )
        self.connection.execute(
            "UPDATE unified_deals SET status='waiting_for_customer',last_response_id=?,updated_at=? WHERE deal_id=?",
            (message_id, now, deal_id),
        )
        if operation_id:
            row = self.outbox_operation(operation_id)
            if row and str(row.get("status") or "") in {"in_progress", "outcome_unknown"}:
                self.reconcile_outbox_sent(operation_id, external_message_id=message_id)

    def fail_response(self, deal_id: str, stage: str, *, error: str) -> None:
        row = self.connection.execute(
            "SELECT status FROM unified_outbox WHERE deal_id=? AND stage=? ORDER BY created_at DESC LIMIT 1",
            (deal_id, stage),
        ).fetchone()
        if row and str(row["status"] or "") in OUTBOX_RECONCILIATION_STATUSES:
            self.mark_review(deal_id, f"outbox_reconciliation_required:{error}")
            return
        self.fail_draft(deal_id, stage, error=error)

    def fail_draft(self, deal_id: str, stage: str, *, error: str) -> None:
        now = utc_now()
        self.connection.execute(
            "UPDATE unified_artifacts SET status='retryable_failed',updated_at=? WHERE deal_id=? AND stage=?",
            (now, deal_id, stage),
        )
        self.connection.execute(
            "UPDATE unified_deals SET status='review_required',scope_display=?,updated_at=? WHERE deal_id=?",
            (str(error or "draft_failed")[:500], now, deal_id),
        )

    def record_offer(
        self,
        deal_id: str,
        *,
        draft_id: str,
        price_net_display: str,
        scope: str,
        pdf_hash: str = "",
        marker: str = "",
        recipient: str = "",
        thread_id: str = "",
    ) -> None:
        now = utc_now()
        provider_evidence = {
            "provider_draft_id": str(draft_id), "pdf_hash": str(pdf_hash), "marker": str(marker),
            "recipient": normalize_email(recipient), "thread_id": str(thread_id),
        }
        self.connection.execute(
            "INSERT INTO unified_artifacts(deal_id,stage,status,content_hash,external_draft_id,created_at,updated_at,"
            "phase,provider_evidence_json) VALUES(?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(deal_id,stage) DO UPDATE SET status='created',phase='draft_created',"
            "external_draft_id=excluded.external_draft_id,provider_evidence_json=excluded.provider_evidence_json,"
            "updated_at=excluded.updated_at",
            (
                deal_id, "final_offer", "created", digest(f"{price_net_display}|{scope}"), draft_id, now, now,
                "draft_created", canonical_json(provider_evidence),
            ),
        )
        self.connection.execute(
            "UPDATE unified_deals SET status='offer_ready',final_draft_id=?,current_draft_id=?,price_net_display=?,scope_display=?,updated_at=? WHERE deal_id=?",
            (draft_id, draft_id, price_net_display, scope, now, deal_id),
        )

    def mark_review(self, deal_id: str, reason: str) -> None:
        """Create the durable task first; notifications are only projections."""
        deal_id = str(deal_id or "")
        if not deal_id:
            return
        reason = str(reason or "requires_review")[:500]
        now = utc_now()
        event = self.connection.execute(
            "SELECT source_type,source_key,email FROM unified_events WHERE deal_id=? ORDER BY event_id DESC LIMIT 1",
            (deal_id,),
        ).fetchone()
        source_type = str(event["source_type"] if event else "internal")
        source_key = str(event["source_key"] if event else f"deal:{deal_id}")
        recipient = normalize_email(str(event["email"] if event else ""))
        operation_id = f"review:{deal_id}:{digest(reason)[:16]}"
        task_id = "review-" + digest(operation_id)[:28]
        sla_at = (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat(timespec="seconds")
        self.connection.execute(
            "UPDATE unified_deals SET status='review_required',scope_display=?,updated_at=? WHERE deal_id=?",
            (reason, now, deal_id),
        )
        self.connection.execute(
            "INSERT INTO unified_review_tasks(task_id,source_type,source_key,deal_id,operation_id,reason_codes_json,"
            "rejected_body,validation_errors_json,recipient,provider_evidence_json,created_at,sla_at,"
            "notification_status,status,updated_at) VALUES(?,?,?,?,?,?,'','[]',?,'{}',?,?,?,'open',?) "
            "ON CONFLICT(operation_id) DO UPDATE SET reason_codes_json=excluded.reason_codes_json,"
            "recipient=excluded.recipient,status='open',updated_at=excluded.updated_at",
            (
                task_id, source_type, source_key, deal_id, operation_id, canonical_json([reason]),
                recipient, now, sla_at, "pending", now,
            ),
        )
        self.connection.execute(
            "INSERT INTO unified_source_dispositions(source_type,source_key,deal_id,operation_id,disposition,evidence_json,updated_at) "
            "VALUES(?,?,?,?,?,?,?) ON CONFLICT(source_type,source_key) DO UPDATE SET disposition='manual_action_required',"
            "operation_id=excluded.operation_id,evidence_json=excluded.evidence_json,updated_at=excluded.updated_at",
            (
                source_type, source_key, deal_id, operation_id, "manual_action_required",
                canonical_json({"review_task_id": task_id, "reason_codes": [reason]}), now,
            ),
        )

    def set_status(self, deal_id: str, status: str) -> bool:
        """Move a deal between the active pool and a terminal state, reversibly.

        Nothing else in the pipeline could ever retire a deal, so every finished
        conversation stayed an active candidate and pushed the sender's next
        inquiry into review. Row data is preserved so the change can be undone.
        """
        status = str(status or "").strip()
        if status not in ACTIVE_DEAL_STATUSES | TERMINAL_DEAL_STATUSES:
            raise ValueError(f"unsupported deal status: {status!r}")
        cursor = self.connection.execute(
            "UPDATE unified_deals SET status=?,updated_at=? WHERE deal_id=?",
            (status, utc_now(), str(deal_id)),
        )
        self.connection.commit()
        return cursor.rowcount > 0

    def assign_rfq_id(self, deal_id: str, *, date: str | None = None) -> str:
        """Assign and persist a human-readable ``RFQ-YYYYMMDD-NNNN`` id.

        Spec section 19. The sequence is per-day: the Nth deal created on a given
        date gets the next sequence number. Idempotent — if the deal already
        has an rfq_id it is returned unchanged.
        """
        import datetime as _dt
        from deal_id import format_deal_id, parse_deal_id

        deal_id = str(deal_id or "")
        row = self.connection.execute("SELECT rfq_id FROM unified_deals WHERE deal_id=?", (deal_id,)).fetchone()
        existing = str(row["rfq_id"]) if row and row["rfq_id"] is not None else ""
        if existing:
            return existing
        if date:
            yyyymmdd = str(date).replace("-", "")[:8]
        else:
            yyyymmdd = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d")
        prefix = f"RFQ-{yyyymmdd}-"
        max_seq = self.connection.execute(
            "SELECT rfq_id FROM unified_deals WHERE rfq_id LIKE ? ORDER BY rfq_id DESC",
            (f"{prefix}%",),
        ).fetchone()
        next_seq = 1
        if max_seq and max_seq["rfq_id"]:
            parsed = parse_deal_id(max_seq["rfq_id"])
            if parsed and parsed[0] == yyyymmdd:
                next_seq = parsed[1] + 1
        rfq_id = format_deal_id(yyyymmdd, next_seq)
        self.connection.execute(
            "UPDATE unified_deals SET rfq_id=?,updated_at=? WHERE deal_id=?",
            (rfq_id, utc_now(), deal_id),
        )
        self.connection.commit()
        return rfq_id

    def get_rfq_id(self, deal_id: str) -> str:
        row = self.connection.execute("SELECT rfq_id FROM unified_deals WHERE deal_id=?", (str(deal_id),)).fetchone()
        return str(row["rfq_id"]) if row and row["rfq_id"] is not None else ""

    def record_provided_information(
        self,
        deal_id: str,
        provided_info: dict[str, Any],
        *,
        source_message_id: str,
        source_type: str = "mail",
        occurred_at: str = "",
    ) -> dict[str, Any]:
        """Controlled save of customer-provided data (spec section 11).

        Merges ``provided_info`` into the deal's facts without silently
        overwriting earlier values: a new value for an existing field is recorded
        as a provenance entry with action ``conflict_kept`` and the previous
        value is preserved. Each saved/changed field gets a provenance row with
        source, message_id and timestamp so no update is traceless.

        Returns a summary: {"added": [...], "updated": [...], "conflict_kept":
        [...], "unchanged": [...], "facts": {...}}.
        """
        deal_id = str(deal_id or "")
        if not deal_id:
            raise ValueError("deal_id is required")
        deal = self.get_deal(deal_id)
        if not deal:
            raise KeyError(f"unknown deal: {deal_id}")
        existing = dict(deal.get("facts") or {})
        added: list[str] = []
        updated: list[str] = []
        conflict_kept: list[str] = []
        unchanged: list[str] = []
        now = utc_now()
        occurred = str(occurred_at or now)
        from offer_readiness import normalize_fact
        for field, new_value in (provided_info or {}).items():
            if new_value is None or new_value == "":
                continue
            field = str(field)
            previous = existing.get(field)
            if previous is None or previous == "":
                existing[field] = new_value
                added.append(field)
                action = "added"
            elif previous == new_value:
                unchanged.append(field)
                action = "unchanged"
            elif (
                normalize_fact(previous).get("state") in {"unknown_confirmed", "not_asked", "assumed"}
                and normalize_fact(new_value).get("state") == "known"
            ):
                existing[field] = new_value
                updated.append(field)
                action = "updated"
            elif (
                normalize_fact(previous).get("state") == "known"
                and normalize_fact(new_value).get("state") in {"unknown_confirmed", "not_asked"}
            ):
                unchanged.append(field)
                action = "known_value_kept"
            else:
                # Controlled update: do NOT silently overwrite. Keep the
                # previous value, record the conflict for human review.
                conflict_kept.append(field)
                action = "conflict_kept"
            self.connection.execute(
                "INSERT INTO unified_fact_provenance("
                "deal_id,field,previous_value,new_value,action,source_type,source_message_id,occurred_at,recorded_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    deal_id, field, _to_text(previous), _to_text(new_value),
                    action, str(source_type or ""), str(source_message_id or ""),
                    occurred, now,
                ),
            )
        self.connection.execute(
            "UPDATE unified_deals SET facts_json=?,updated_at=? WHERE deal_id=?",
            (canonical_json(existing), now, deal_id),
        )
        self.connection.commit()
        return {
            "added": added,
            "updated": updated,
            "conflict_kept": conflict_kept,
            "unchanged": unchanged,
            "facts": existing,
        }

    def fact_provenance(self, deal_id: str) -> list[dict[str, Any]]:
        """Return the provenance log for a deal's facts (newest last)."""
        rows = self.connection.execute(
            "SELECT field,previous_value,new_value,action,source_type,source_message_id,occurred_at,recorded_at "
            "FROM unified_fact_provenance WHERE deal_id=? ORDER BY id",
            (str(deal_id),),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_conversation_state(self, deal_id: str) -> dict[str, Any]:
        """Return the conversation-continuity state for a deal (spec section 13).

        Returns default state when no row exists yet.
        """
        row = self.connection.execute(
            "SELECT * FROM unified_conversation_state WHERE deal_id=?",
            (str(deal_id),),
        ).fetchone()
        if not row:
            return {
                "deal_id": str(deal_id),
                "agent_reply_count": 0,
                "customer_message_count": 0,
                "is_first_agent_reply": True,
                "acknowledgement_already_sent": False,
                "conversation_stage": "discovery",
                "last_agent_reply_at": None,
                "last_customer_reply_at": None,
            }
        result = dict(row)
        result["is_first_agent_reply"] = bool(result.get("is_first_agent_reply"))
        result["acknowledgement_already_sent"] = bool(result.get("acknowledgement_already_sent"))
        return result

    def save_conversation_state(self, deal_id: str, state: dict[str, Any]) -> None:
        now = utc_now()
        self.connection.execute(
            "INSERT INTO unified_conversation_state("
            "deal_id,agent_reply_count,customer_message_count,is_first_agent_reply,"
            "acknowledgement_already_sent,conversation_stage,last_agent_reply_at,last_customer_reply_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(deal_id) DO UPDATE SET "
            "agent_reply_count=excluded.agent_reply_count,"
            "customer_message_count=excluded.customer_message_count,"
            "is_first_agent_reply=excluded.is_first_agent_reply,"
            "acknowledgement_already_sent=excluded.acknowledgement_already_sent,"
            "conversation_stage=excluded.conversation_stage,"
            "last_agent_reply_at=excluded.last_agent_reply_at,"
            "last_customer_reply_at=excluded.last_customer_reply_at,"
            "updated_at=excluded.updated_at",
            (
                str(deal_id),
                int(state.get("agent_reply_count", 0)),
                int(state.get("customer_message_count", 0)),
                1 if state.get("is_first_agent_reply") else 0,
                1 if state.get("acknowledgement_already_sent") else 0,
                str(state.get("conversation_stage", "discovery")),
                state.get("last_agent_reply_at"),
                state.get("last_customer_reply_at"),
                now,
            ),
        )
        self.connection.commit()

    def record_customer_message(self, deal_id: str, *, occurred_at: str = "") -> dict[str, Any]:
        from conversation_continuity import on_customer_message  # local import to avoid cycle

        state = self.get_conversation_state(deal_id)
        state = on_customer_message(state, occurred_at=occurred_at or utc_now())
        self.save_conversation_state(deal_id, state)
        return state

    def record_agent_reply(self, deal_id: str, *, occurred_at: str = "") -> dict[str, Any]:
        from conversation_continuity import on_agent_reply  # local import to avoid cycle

        state = self.get_conversation_state(deal_id)
        state = on_agent_reply(state, occurred_at=occurred_at or utc_now())
        self.save_conversation_state(deal_id, state)
        return state

    def sheet_refs_with_pending_status(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT e.deal_id,e.metadata_json,d.status,d.final_draft_id,d.current_draft_id,d.price_net_display,d.scope_display "
            "FROM unified_events e JOIN unified_deals d ON d.deal_id=e.deal_id WHERE e.source_type='google_sheets' ORDER BY e.event_id"
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            metadata = json.loads(row["metadata_json"] or "{}")
            result.append({
                "deal_id": row["deal_id"], **metadata,
                "desired_status": STATUS_TO_SHEET.get(str(row["status"]), "wymaga sprawdzenia"),
                "draft_id": row["final_draft_id"] or row["current_draft_id"] or "",
                "price_net_display": row["price_net_display"] or "",
                "scope_display": row["scope_display"] or "",
            })
        return result

    def event_count(self) -> int:
        row = self.connection.execute("SELECT COUNT(*) AS n FROM unified_events").fetchone()
        return int(row["n"] if row else 0)
