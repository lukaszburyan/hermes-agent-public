#!/usr/bin/env python3
"""Compare Hermes shadow decisions with human review labels.

Shadow input is JSONL produced by the poller, one summary object per run.
Human labels are JSONL with at least ``message_id`` and ``expected_action``.
Optional fields compare classification, recipient, and risk without storing
message bodies.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL at {path}:{line_number}") from exc
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _shadow_messages(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for run in rows:
        for briefing in run.get("briefings", []) or []:
            message_id = str(briefing.get("message_id") or "")
            if not message_id:
                continue
            axes = briefing.get("classification_axes") or {}
            draft = briefing.get("draft") or {}
            form = briefing.get("form_submission") or {}
            result[message_id] = {
                "message_id": message_id,
                "run_id": run.get("run_id"),
                "classification": briefing.get("classification"),
                "action": axes.get("action") or draft.get("action"),
                "risk": axes.get("risk"),
                "recipient": draft.get("recipient") or form.get("customer_email"),
                "correlation_id": briefing.get("correlation_id") or form.get("correlation_id"),
            }
    return result


def compare(shadow_rows: list[dict[str, Any]], human_rows: list[dict[str, Any]]) -> dict[str, Any]:
    shadow = _shadow_messages(shadow_rows)
    comparisons: list[dict[str, Any]] = []
    missing_shadow = 0
    mismatches = 0
    for human in human_rows:
        message_id = str(human.get("message_id") or "")
        observed = shadow.get(message_id)
        if not observed:
            missing_shadow += 1
            comparisons.append({"message_id": message_id, "status": "missing_shadow"})
            continue
        differences: dict[str, dict[str, Any]] = {}
        fields = {
            "action": human.get("expected_action"),
            "classification": human.get("expected_classification"),
            "risk": human.get("expected_risk"),
            "recipient": human.get("expected_recipient"),
        }
        for field, expected in fields.items():
            if expected is not None and expected != observed.get(field):
                differences[field] = {"expected": expected, "observed": observed.get(field)}
        status = "match" if not differences else "mismatch"
        if differences:
            mismatches += 1
        comparisons.append({
            "message_id": message_id,
            "run_id": observed.get("run_id"),
            "status": status,
            "differences": differences,
        })
    reviewed = len(comparisons) - missing_shadow
    return {
        "reviewed": reviewed,
        "matches": sum(1 for item in comparisons if item["status"] == "match"),
        "mismatches": mismatches,
        "missing_shadow": missing_shadow,
        "coverage": (reviewed / len(human_rows)) if human_rows else 0.0,
        "comparisons": comparisons,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare Hermes shadow JSONL with handlowiec labels.")
    parser.add_argument("--shadow-log", required=True, type=Path)
    parser.add_argument("--decisions", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = compare(_read_jsonl(args.shadow_log), _read_jsonl(args.decisions))
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
