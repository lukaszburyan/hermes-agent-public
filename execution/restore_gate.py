#!/usr/bin/env python3
"""Fail-closed restore reconciliation gate for every side-effect entry point."""

from __future__ import annotations

import os
from pathlib import Path


DEFAULT_RESTORE_GATE = Path("/opt/data/rfq-state/RESTORE_RECONCILIATION_REQUIRED")


class RestoreReconciliationRequired(RuntimeError):
    pass


def restore_gate_path() -> Path:
    configured = str(os.environ.get("HERMES_RESTORE_GATE_PATH") or "").strip()
    return Path(configured) if configured else DEFAULT_RESTORE_GATE


def assert_restore_reconciled() -> None:
    marker = restore_gate_path()
    if marker.exists():
        raise RestoreReconciliationRequired(f"restore_reconciliation_required:{marker}")
