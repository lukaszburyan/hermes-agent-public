#!/usr/bin/env python3
"""Generate deterministic SHA-256 checksums for the committed backup payload."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path


EXCLUDED_PARTS = {".git", ".pytest_cache", "__pycache__"}
EXCLUDED_FILES = {
    "inventory/manifest.sha256",
    "skills/.bundled_manifest",
    "skills/.curator_state",
    "skills/.gitkeep",
    "skills/.usage.json",
    "skills/.usage.json.lock",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    root = args.root.resolve()

    entries: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part in EXCLUDED_PARTS for part in relative.parts):
            continue
        if relative.as_posix() in EXCLUDED_FILES:
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        entries.append(f"{digest}  {relative.as_posix()}")

    destination = root / "inventory/manifest.sha256"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(entries) + "\n", encoding="utf-8")
    print(f"generate-manifest: {len(entries)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
