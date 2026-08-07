#!/usr/bin/env python3
"""Redact credentials before operational output is persisted.

The redactor intentionally preserves business fields and JSON structure. Raw
operational logs are retained for a short period, so only credential material
is removed here; broader PII minimisation belongs in structured audit records.
"""
from __future__ import annotations

import argparse
import os
import re
import tempfile
from pathlib import Path

SECRET_VALUE_RE = re.compile(
    r"(?i)(\b(?:access_token|refresh_token|client_secret|api_key|password|secret|token)\b"
    r"(?:[\"'\s]*[:=][\"'\s]*))([^\s,;\"'}&#]{8,})"
)
AUTH_HEADER_RE = re.compile(r"(?i)(\b(?:authorization|proxy-authorization)\b\s*[:=]\s*)([^\r\n]+)")
BEARER_RE = re.compile(r"(?i)\b(bearer|zoho-oauthtoken)\s+[A-Za-z0-9._~+/=-]{8,}")
URL_SECRET_RE = re.compile(
    r"(?i)([?&](?:access_token|refresh_token|client_secret|api_key|password|secret|token)=)([^&#\s]{8,})"
)


def redact_text(text: str) -> str:
    text = SECRET_VALUE_RE.sub(r"\1[REDACTED]", text)
    text = AUTH_HEADER_RE.sub(r"\1[REDACTED]", text)
    text = BEARER_RE.sub(lambda match: f"{match.group(1)} [REDACTED]", text)
    return URL_SECRET_RE.sub(r"\1[REDACTED]", text)


def write_private(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def redact_file(source: Path, destination: Path) -> None:
    text = source.read_text(encoding="utf-8", errors="replace") if source.exists() else ""
    write_private(destination, redact_text(text))


def main() -> int:
    parser = argparse.ArgumentParser(description="Redact credentials from one operational log file.")
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    redact_file(args.source, args.destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
