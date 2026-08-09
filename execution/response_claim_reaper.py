#!/usr/bin/env python3
"""Read-only expired-claim detector and controlled CAS convergence worker."""

from __future__ import annotations

import argparse
import json

from unified_lead_registry import UnifiedLeadRegistry


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry-file", required=True)
    parser.add_argument("--apply", action="store_true", help="CAS-converge expired claims; never sends mail")
    args = parser.parse_args()
    registry = UnifiedLeadRegistry(args.registry_file)
    try:
        detected = registry.detect_expired_claims()
        changed = registry.reap_expired_claims() if args.apply else []
    finally:
        registry.close()
    print(json.dumps({"detected": detected, "changed": changed, "send_capability": False}, ensure_ascii=False, sort_keys=True))
    return 2 if detected and not args.apply else 0


if __name__ == "__main__":
    raise SystemExit(main())
