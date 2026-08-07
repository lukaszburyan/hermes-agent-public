#!/usr/bin/env python3
"""Retired test autosend harness.

Customer email sending was permanently removed from the Orchesta RFQ runtime.
Controlled acceptance tests must create Zoho drafts and inspect them; they may
not send synthetic or real customer-facing messages.
"""
from __future__ import annotations

import sys


def main() -> int:
    print(
        "hermes_rfq_test_autosend_poller: DISABLED — customer mail automation is draft-only",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
