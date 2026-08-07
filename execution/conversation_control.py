#!/usr/bin/env python3
"""Explicit conversation pause/resume control for Orchesta RFQ.

This command never sends email.  It only changes the durable automation state
for one exact Zoho account/thread/deal tuple and emits a JSON audit result.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

EXECUTION_DIR = Path(__file__).resolve().parent
if str(EXECUTION_DIR) not in sys.path:
    sys.path.insert(0, str(EXECUTION_DIR))

from unified_lead_registry import UnifiedLeadRegistry  # noqa: E402

DEFAULT_REGISTRY = Path("/opt/data/.tmp/orchesta-rfq-unified.sqlite3")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Control automation for one Orchesta RFQ conversation")
    result.add_argument("action", choices=("status", "pause", "resume"))
    result.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    result.add_argument("--deal-id", required=True)
    result.add_argument("--account-id", required=True)
    result.add_argument("--thread-id", required=True)
    result.add_argument("--actor", default="lukasz")
    result.add_argument("--reason", default="explicit_operator_action")
    return result


def main() -> int:
    args = parser().parse_args()
    registry = UnifiedLeadRegistry(args.registry)
    try:
        if registry.get_deal(args.deal_id) is None:
            print(json.dumps({"ok": False, "error": "deal_not_found", "deal_id": args.deal_id}, ensure_ascii=False, sort_keys=True))
            return 2
        if args.action == "status":
            control = registry.automation_control(args.deal_id, account_id=args.account_id, thread_id=args.thread_id)
        elif args.action == "pause":
            control = registry.pause_automation(
                args.deal_id,
                account_id=args.account_id,
                thread_id=args.thread_id,
                reason=args.reason,
                actor=args.actor,
                evidence={"source": "conversation_control_cli"},
            )
        else:
            control = registry.resume_automation(
                args.deal_id,
                account_id=args.account_id,
                thread_id=args.thread_id,
                actor=args.actor,
                reason=args.reason,
            )
        print(json.dumps({"ok": True, "action": args.action, "control": control}, ensure_ascii=False, sort_keys=True))
        return 0
    finally:
        registry.close()


if __name__ == "__main__":
    raise SystemExit(main())
