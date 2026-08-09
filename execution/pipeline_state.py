#!/usr/bin/env python3
"""SQLite-backed restart-safe state for the Hermes RFQ pipeline.

`processed_messages.json` is intentionally no longer used.  The public
PipelineState methods remain compatible with the poller, while operations and
state transitions are durable and queryable.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from hermes_rfq_core import HermesStateStore, run_id, utc_now


DEFAULT_STATE_FILE = ".tmp/hermes-rfq-state/state.sqlite3"


def default_state_file() -> Path:
    return Path(os.environ.get("HERMES_RFQ_STATE_FILE", DEFAULT_STATE_FILE))


class PipelineState:
    """Compatibility facade over :class:`HermesStateStore`."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else default_state_file()
        self.store = HermesStateStore(self.path)

    def is_processed(self, message_id: str) -> bool:
        row = self.store.get_message(str(message_id))
        return bool(row and row["status"] in {"done", "precheck_skipped", "blocked", "manual_review"})

    def get(self, message_id: str) -> dict[str, Any] | None:
        row = self.store.get_message(str(message_id))
        if not row:
            return None
        try:
            record = json.loads(row.get("record_json") or "{}")
        except json.JSONDecodeError:
            record = {}
        record["status"] = row.get("status")
        return record

    def mark_processed(self, message_id: str, record: dict[str, Any] | None = None) -> dict[str, Any]:
        key = str(message_id).strip()
        if not key:
            raise ValueError("message_id must be a non-empty string")
        clean = dict(record or {})
        clean.setdefault("processed_at", utc_now())
        outcome = str(clean.get("business_outcome") or "").strip()
        status = {
            "customer_succeeded": "done",
            "draft_verified": "done",
            "terminal_discard": "precheck_skipped",
            "manual_action_required": "manual_review",
            "retry_scheduled": "retry_scheduled",
            "outcome_unknown": "reconciliation_pending",
        }.get(outcome, "analysis_pending")
        self.store.record_message(key, clean, status=status, run_id_value=str(clean.get("processed_run_id") or run_id()))
        return clean

    def plan_operation(self, message_id: str, action_type: str, **kwargs: Any) -> dict[str, Any]:
        return self.store.plan_operation(str(message_id), action_type, **kwargs)

    def operation(self, message_id: str, action_type: str) -> dict[str, Any] | None:
        return self.store.operation(str(message_id), action_type)

    def operation_succeeded(self, message_id: str, action_type: str) -> bool:
        return self.store.operation_succeeded(str(message_id), action_type)

    def update_operation(self, message_id: str, action_type: str, status: str, **kwargs: Any) -> dict[str, Any]:
        return self.store.update_operation(str(message_id), action_type, status, **kwargs)

    def count(self) -> int:
        row = self.store.connection.execute(
            "SELECT COUNT(*) AS count FROM messages WHERE status IN ('done','precheck_skipped','manual_review')"
        ).fetchone()
        return int(row["count"] if row else 0)

    def save(self) -> Path:
        # SQLite is committed transaction-by-transaction.  Keep this method for
        # callers of the former JSON facade and ensure the file is private.
        try:
            self.path.chmod(0o600)
        except OSError:
            pass
        return self.path


def self_test() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        state_path = Path(tmp) / "nested" / "state.sqlite3"
        state = PipelineState(state_path)
        if state.count() != 0:
            failures.append("fresh state should be empty")
        state.mark_processed("msg-1", {"classification": "new_quote_request", "business_outcome": "customer_succeeded", "processed_run_id": "r1"})
        state.mark_processed("msg-2", {"classification": "newsletter_automated_spam", "business_outcome": "terminal_discard", "wake_agent": False})
        state.plan_operation("msg-1", "customer_draft", input_hash="input", content_hash="content")
        state.update_operation("msg-1", "customer_draft", "succeeded", external_draft_id="draft-1")
        if not state.operation_succeeded("msg-1", "customer_draft"):
            failures.append("succeeded operation was not durable")
        reloaded = PipelineState(state_path)
        if not reloaded.is_processed("msg-1") or reloaded.count() != 2:
            failures.append("SQLite state did not survive restart")
        if reloaded.operation("msg-1", "customer_draft")["external_draft_id"] != "draft-1":
            failures.append("operation external id did not survive restart")
        if len(reloaded.store.list_transitions("message", "msg-1")) < 1:
            failures.append("state transition log missing")
        try:
            reloaded.mark_processed("   ", {})
            failures.append("empty message_id should raise")
        except ValueError:
            pass
    if failures:
        print("pipeline_state self-test failures:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print("pipeline_state self-test: ok")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect or self-test Hermes SQLite state.")
    parser.add_argument("--state-file", default=None)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--show", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    state = PipelineState(args.state_file)
    if args.show:
        rows = state.store.connection.execute("SELECT message_id,status,thread_id,correlation_id,updated_at FROM messages ORDER BY updated_at DESC").fetchall()
        print(json.dumps({"state_file": str(state.path), "count": state.count(), "messages": [dict(row) for row in rows]}, ensure_ascii=False, indent=2))
        return 0
    parser.error("provide --self-test or --show")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
