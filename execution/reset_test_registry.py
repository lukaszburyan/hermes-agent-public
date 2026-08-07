#!/usr/bin/env python3
"""Reset the TEST Hermes registry only (spec section 21).

Hard guard: this script ABORTS unless ``HERMES_ENV=test``. It never touches the
production registry. The registry path comes from ``HERMES_REGISTRY_PATH``;
when unset, a local default under ``.tmp/test/`` is used so test runs never
land on the production path.

Usage:
  HERMES_ENV=test python execution/reset_test_registry.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def registry_path() -> Path:
    env = os.environ.get("HERMES_REGISTRY_PATH", "").strip()
    if env:
        return Path(env)
    return Path(".tmp/test/hermes-registry.sqlite")


def main(argv: list[str] | None = None) -> int:
    env = os.environ.get("HERMES_ENV", "").strip().lower()
    if env != "test":
        print(
            "reset_test_registry: ABORT — HERMES_ENV must be 'test' "
            f"(got {env!r}). Tests must never clear the production DB.",
            file=sys.stderr,
        )
        return 2
    path = registry_path()
    removed: list[str] = []
    for candidate in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
        if candidate.exists():
            candidate.unlink()
            removed.append(str(candidate))
    if removed:
        print("reset_test_registry: removed " + ", ".join(removed))
    else:
        print(f"reset_test_registry: nothing to remove at {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
