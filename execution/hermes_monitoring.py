#!/usr/bin/env python3
"""Read-only monitoring snapshot for the Hermes RFQ SQLite state."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hermes_rfq_core import HermesStateStore, utc_now


METRIC_NAMES = (
    "api_errors", "retries", "duplicates", "security_blocks", "crm_conflicts",
    "human_edited_drafts", "offers_waiting", "pdf_errors", "errors_429", "errors_5xx",
)


def record_poller_heartbeat(store: HermesStateStore) -> None:
    store.connection.execute(
        "INSERT INTO metrics(name,value,updated_at) VALUES('poller_last_run',1,?) ON CONFLICT(name) DO UPDATE SET updated_at=excluded.updated_at",
        (utc_now(),),
    )


def collect_snapshot(store: HermesStateStore, *, backlog_limit: int = 100, heartbeat_ttl_seconds: int = 600) -> dict[str, Any]:
    now = time.time()
    queue_rows = store.connection.execute(
        "SELECT created_at FROM messages WHERE status NOT IN ('done','precheck_skipped') ORDER BY created_at ASC"
    ).fetchall()
    oldest_age = 0
    if queue_rows:
        try:
            oldest = datetime.fromisoformat(str(queue_rows[0]["created_at"])).timestamp()
            oldest_age = max(0, round(now - oldest))
        except ValueError:
            oldest_age = -1
    metrics = {name: 0 for name in METRIC_NAMES}
    for row in store.connection.execute("SELECT name,value FROM metrics").fetchall():
        if row["name"] in metrics:
            metrics[row["name"]] = int(row["value"])
    heartbeat = store.connection.execute("SELECT updated_at FROM metrics WHERE name='poller_last_run'").fetchone()
    heartbeat_age = None
    if heartbeat:
        try:
            heartbeat_age = max(0, round(now - datetime.fromisoformat(heartbeat["updated_at"]).timestamp()))
        except ValueError:
            heartbeat_age = -1
    alarms: list[str] = []
    if len(queue_rows) > backlog_limit:
        alarms.append("backlog_growing")
    if heartbeat_age is None or heartbeat_age > heartbeat_ttl_seconds:
        alarms.append("poller_not_running")
    if metrics["errors_429"] >= 5:
        alarms.append("series_of_429")
    if metrics["errors_5xx"] >= 5:
        alarms.append("series_of_5xx")
    if metrics["security_blocks"]:
        alarms.append("security_block_detected")
    if metrics["duplicates"]:
        alarms.append("draft_duplicate_detected")
    if metrics["pdf_errors"]:
        alarms.append("pdf_error_detected")
    return {
        "queue_count": len(queue_rows),
        "oldest_message_age_seconds": oldest_age,
        "metrics": metrics,
        "heartbeat_age_seconds": heartbeat_age,
        "alarms": alarms,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-file", default=".tmp/hermes-rfq-state/state.sqlite3")
    parser.add_argument("--backlog-limit", type=int, default=100)
    parser.add_argument("--heartbeat-ttl", type=int, default=600)
    args = parser.parse_args()
    store = HermesStateStore(Path(args.state_file))
    print(json.dumps(collect_snapshot(store, backlog_limit=args.backlog_limit, heartbeat_ttl_seconds=args.heartbeat_ttl), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
