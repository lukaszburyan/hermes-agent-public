#!/usr/bin/env python3
"""Persistent, source-isolated health tracking for autonomous RFQ pollers."""
from __future__ import annotations

import argparse
import fcntl
import json
from datetime import datetime, timezone
from pathlib import Path


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def record(path: str | Path, *, source: str, ok: bool, detail: str = "", threshold: int = 3) -> str:
    state_path = Path(path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with state_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.seek(0)
        raw = handle.read().strip()
        try:
            all_state = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            all_state = {}
        previous = dict(all_state.get(source) or {})
        previous_failures = int(previous.get("consecutive_failures") or 0)
        was_alerted = bool(previous.get("alerted"))
        message = ""
        if ok:
            if was_alerted or previous_failures >= threshold:
                message = f"Orchesta RFQ: źródło {source} działa ponownie po {previous_failures} kolejnych błędach."
            current = {
                "consecutive_failures": 0,
                "alerted": False,
                "last_ok_at": now_iso(),
                "last_error": "",
            }
        else:
            failures = previous_failures + 1
            alerted = was_alerted or failures >= threshold
            current = {
                **previous,
                "consecutive_failures": failures,
                "alerted": alerted,
                "last_failure_at": now_iso(),
                "last_error": str(detail or "unknown_error")[:500],
            }
            if failures == threshold or (failures > threshold and failures % 10 == 0):
                message = (
                    f"Orchesta RFQ: źródło {source} ma {failures} kolejne błędy. "
                    f"Pozostałe źródła działają niezależnie. Ostatni błąd: {current['last_error']}"
                )
        all_state[source] = current
        handle.seek(0)
        handle.truncate()
        json.dump(all_state, handle, ensure_ascii=False, sort_keys=True)
        handle.write("\n")
        handle.flush()
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    try:
        state_path.chmod(0o600)
    except OSError:
        pass
    return message


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-file", default="/opt/data/.tmp/orchesta-rfq-source-health.json")
    parser.add_argument("--source", required=True)
    parser.add_argument("--status", choices=("ok", "failure"), required=True)
    parser.add_argument("--detail", default="")
    parser.add_argument("--threshold", type=int, default=3)
    args = parser.parse_args()
    message = record(
        args.state_file,
        source=args.source,
        ok=args.status == "ok",
        detail=args.detail,
        threshold=max(1, args.threshold),
    )
    if message:
        print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
