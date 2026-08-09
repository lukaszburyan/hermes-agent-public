#!/usr/bin/env python3
"""Inventory and evidence-gated convergence of legacy source projections.

The worker never contacts a provider and never sends. Operator-supplied
evidence must already have been verified against the provider or a durable
review task before ``--apply`` is accepted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from email.utils import parseaddr
from pathlib import Path
from typing import Any

from unified_lead_registry import UnifiedLeadRegistry, normalize_email


VALID_DONE_OUTCOMES = {"customer_succeeded", "draft_verified"}
VALID_RECONCILIATION_OUTCOMES = VALID_DONE_OUTCOMES | {"manual_action_required", "terminal_discard"}
STATUS_FOR_OUTCOME = {
    "customer_succeeded": "done",
    "draft_verified": "done",
    "manual_action_required": "manual_review",
    "terminal_discard": "precheck_skipped",
}


class ReconciliationError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def source_hash(message_id: str) -> str:
    return hashlib.sha256(str(message_id).encode("utf-8")).hexdigest()


def _record(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def inventory(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        "SELECT message_id,status,record_json,updated_at FROM messages WHERE status='done' ORDER BY updated_at,message_id"
    ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        record = _record(str(row["record_json"] or "{}"))
        if str(record.get("business_outcome") or "") in VALID_DONE_OUTCOMES:
            continue
        result.append({
            "message_sha256": source_hash(str(row["message_id"])),
            "status": str(row["status"]),
            "updated_at": str(row["updated_at"]),
            "classification": str(record.get("classification") or ""),
            "draft_action": str(record.get("draft_action") or "none"),
        })
    return result


def _validate_review_task(registry_file: Path, task_id: str) -> None:
    if not registry_file.is_file():
        raise ReconciliationError("registry_required_for_manual_action")
    connection = sqlite3.connect(f"file:{registry_file}?mode=ro", uri=True)
    try:
        tables = {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "unified_review_tasks" not in tables:
            raise ReconciliationError("review_task_table_missing")
        row = connection.execute(
            "SELECT status FROM unified_review_tasks WHERE task_id=?", (str(task_id),)
        ).fetchone()
        if not row or str(row[0]) != "open":
            raise ReconciliationError(f"durable_review_task_not_open:{task_id}")
    finally:
        connection.close()


def _recipient_from_record(record: dict[str, Any]) -> str:
    sender_identity = record.get("sender_identity")
    candidates = [
        record.get("form_customer_email"),
        record.get("source_from"),
        sender_identity.get("email") if isinstance(sender_identity, dict) else "",
    ]
    for candidate in candidates:
        parsed = parseaddr(str(candidate or ""))[1] or str(candidate or "")
        recipient = normalize_email(parsed)
        if recipient:
            return recipient
    return ""


def _prepare_manual_review_tasks(
    *,
    registry_file: Path,
    indexed: dict[str, dict[str, Any]],
    by_hash: dict[str, sqlite3.Row],
) -> None:
    pending: list[tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]] = []
    for key, item in indexed.items():
        if str(item.get("outcome") or "") != "manual_action_required" or str(item.get("review_task_id") or ""):
            continue
        payload = item.get("review_payload")
        if not isinstance(payload, dict):
            raise ReconciliationError(f"review_payload_required:{key}")
        reasons = payload.get("reason_codes")
        rejected_body = str(payload.get("rejected_body") or "").strip()
        validation_errors = payload.get("validation_errors") or []
        provider_evidence = payload.get("provider_evidence") or {}
        if not isinstance(reasons, list) or not reasons or not rejected_body:
            raise ReconciliationError(f"review_payload_invalid:{key}")
        if not isinstance(validation_errors, list) or not isinstance(provider_evidence, dict):
            raise ReconciliationError(f"review_payload_invalid:{key}")
        row = by_hash.get(key)
        if row is None:
            raise ReconciliationError(f"legacy_source_missing:{key}")
        expected_updated_at = str(item.get("expected_updated_at") or item.get("updated_at") or "")
        if not expected_updated_at or expected_updated_at != str(row["updated_at"]):
            raise ReconciliationError(f"source_version_mismatch:{key}")
        pending.append((key, item, payload, _record(str(row["record_json"] or "{}"))))
    if not pending:
        return
    registry = UnifiedLeadRegistry(registry_file)
    try:
        for key, item, payload, record in pending:
            row = by_hash[key]
            result = registry.ensure_legacy_review_task(
                source_key=str(row["message_id"]),
                reason_codes=[str(value) for value in payload["reason_codes"]],
                rejected_body=str(payload["rejected_body"]),
                validation_errors=[str(value) for value in (payload.get("validation_errors") or [])],
                recipient=str(payload.get("recipient") or _recipient_from_record(record)),
                provider_evidence={
                    "legacy_message_sha256": key,
                    **dict(payload.get("provider_evidence") or {}),
                },
            )
            item["review_task_id"] = result["task_id"]
    finally:
        registry.close()


def validate_evidence(item: dict[str, Any], *, registry_file: Path) -> None:
    outcome = str(item.get("outcome") or "")
    if outcome not in VALID_RECONCILIATION_OUTCOMES:
        raise ReconciliationError(f"invalid_outcome:{outcome}")
    if outcome == "customer_succeeded" and not str(item.get("provider_message_id") or "").strip():
        raise ReconciliationError("provider_message_id_required")
    if outcome == "draft_verified" and not str(item.get("provider_draft_id") or "").strip():
        raise ReconciliationError("provider_draft_id_required")
    if outcome == "manual_action_required":
        task_id = str(item.get("review_task_id") or "").strip()
        if not task_id:
            raise ReconciliationError("review_task_id_required")
        _validate_review_task(registry_file, task_id)
    if outcome == "terminal_discard" and not str(item.get("discard_reason") or "").strip():
        raise ReconciliationError("discard_reason_required")


def apply_reconciliation(
    state_file: Path,
    evidence: dict[str, Any],
    *,
    registry_file: Path,
) -> list[dict[str, str]]:
    if int(evidence.get("schema_version") or 0) != 1:
        raise ReconciliationError("unsupported_evidence_schema")
    raw_items = evidence.get("items")
    if not isinstance(raw_items, list):
        raise ReconciliationError("evidence_items_required")
    indexed: dict[str, dict[str, Any]] = {}
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise ReconciliationError("invalid_evidence_item")
        key = str(raw.get("message_sha256") or "")
        if len(key) != 64 or key in indexed:
            raise ReconciliationError(f"invalid_or_duplicate_message_sha256:{key}")
        indexed[key] = raw

    connection = sqlite3.connect(state_file, timeout=30, isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN IMMEDIATE")
        pending = inventory(connection)
        pending_keys = {item["message_sha256"] for item in pending}
        if set(indexed) != pending_keys:
            missing = sorted(pending_keys - set(indexed))
            extra = sorted(set(indexed) - pending_keys)
            raise ReconciliationError(f"evidence_coverage_mismatch:missing={missing}:extra={extra}")
        rows = connection.execute(
            "SELECT message_id,status,record_json,updated_at FROM messages WHERE status='done'"
        ).fetchall()
        by_hash = {source_hash(str(row["message_id"])): row for row in rows}
        _prepare_manual_review_tasks(registry_file=registry_file, indexed=indexed, by_hash=by_hash)
        changed: list[dict[str, str]] = []
        now = utc_now()
        for key in sorted(indexed):
            item = indexed[key]
            validate_evidence(item, registry_file=registry_file)
            row = by_hash[key]
            expected_updated_at = str(item.get("expected_updated_at") or item.get("updated_at") or "")
            if not expected_updated_at or expected_updated_at != str(row["updated_at"]):
                raise ReconciliationError(f"source_version_mismatch:{key}")
            outcome = str(item["outcome"])
            record = _record(str(row["record_json"] or "{}"))
            record["business_outcome"] = outcome
            record["legacy_reconciliation"] = {
                name: item[name]
                for name in (
                    "provider_message_id", "provider_draft_id", "review_task_id", "discard_reason",
                    "provider", "verified_at", "verified_by",
                )
                if str(item.get(name) or "").strip()
            }
            record["reason_code"] = "legacy_source_evidence_reconciled"
            status = STATUS_FOR_OUTCOME[outcome]
            cursor = connection.execute(
                "UPDATE messages SET status=?,record_json=?,updated_at=? "
                "WHERE message_id=? AND status='done' AND updated_at=?",
                (status, json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")), now,
                 str(row["message_id"]), expected_updated_at),
            )
            if cursor.rowcount != 1:
                raise ReconciliationError(f"source_cas_lost:{key}")
            connection.execute(
                "INSERT INTO state_transitions(entity_type,entity_id,previous_status,new_status,reason_code,timestamp,run_id) "
                "VALUES('message',?,?,?,?,?,?)",
                (str(row["message_id"]), "done", status, "legacy_source_evidence_reconciled", now,
                 str(evidence.get("run_id") or "legacy-reconciliation")),
            )
            changed.append({"message_sha256": key, "outcome": outcome, "status": status})
        connection.execute("COMMIT")
        return changed
    except Exception:
        try:
            connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-file", type=Path, required=True)
    parser.add_argument("--registry-file", type=Path, default=Path(""))
    parser.add_argument("--evidence-file", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.apply and not args.evidence_file:
        parser.error("--apply requires --evidence-file")
    if args.apply:
        evidence = json.loads(args.evidence_file.read_text(encoding="utf-8"))
        changed = apply_reconciliation(args.state_file, evidence, registry_file=args.registry_file)
        print(json.dumps({"changed": changed, "send_capability": False}, ensure_ascii=False, sort_keys=True))
        return 0
    connection = sqlite3.connect(f"file:{args.state_file}?mode=ro", uri=True)
    try:
        pending = inventory(connection)
    finally:
        connection.close()
    print(json.dumps({"pending": pending, "send_capability": False}, ensure_ascii=False, sort_keys=True))
    return 2 if pending else 0


if __name__ == "__main__":
    raise SystemExit(main())
