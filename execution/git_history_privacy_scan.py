#!/usr/bin/env python3
"""Scan every reachable Git commit for redacted PII and credential indicators."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

EMAIL_RE = re.compile(r"(?i)(?<![\w.+-])([a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9.-]+\.[a-z]{2,})(?![\w.-])")
PHONE_RE = re.compile(r"(?<!\w)(\+?\d[\d\s().-]{7,}\d)(?!\w)")
SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|password|passwd|private[_-]?key)\b\s*[:=]\s*([^\s,;]{8,})"
)
PUBLIC_EMAIL_DOMAINS = {"example.com", "example.org", "example.net", "example.invalid", "users.noreply.github.com"}
PLACEHOLDER_EMAIL_SUFFIXES = (".example", ".invalid", ".local", ".test")
CLIENT_PATH_RE = re.compile(r"(?i)(client|customer|lead|deal|crm|kontakt|klient|mail|rfq)")


def git(repo: Path, *args: str) -> bytes:
    completed = subprocess.run(
        ["git", f"--git-dir={repo}", *args],
        check=True,
        capture_output=True,
    )
    return completed.stdout


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()[:16]


def phone_candidate(value: str) -> bool:
    digits = re.sub(r"\D", "", value)
    return 9 <= len(digits) <= 15


def classify_email(value: str) -> str:
    domain = value.rsplit("@", 1)[-1].lower()
    if domain in PUBLIC_EMAIL_DOMAINS or domain.endswith(PLACEHOLDER_EMAIL_SUFFIXES):
        return "placeholder_or_public"
    return "review"


def classify_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    return "placeholder" if len(set(digits)) <= 3 else "review"


def finding(kind: str, value: str, *, commit: str, path: str, line: int, classification: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "fingerprint": fingerprint(value),
        "commit": commit,
        "path": path,
        "line": line,
        "classification": classification,
    }


def scan_line(line_text: str, *, commit: str, path: str, line_number: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for match in EMAIL_RE.finditer(line_text):
        value = match.group(1)
        items.append(
            finding(
                "email",
                value,
                commit=commit,
                path=path,
                line=line_number,
                classification=classify_email(value),
            )
        )
    for match in PHONE_RE.finditer(line_text):
        value = match.group(1)
        if phone_candidate(value):
            items.append(
                finding(
                    "phone",
                    value,
                    commit=commit,
                    path=path,
                    line=line_number,
                    classification=classify_phone(value),
                )
            )
    for match in SECRET_ASSIGNMENT_RE.finditer(line_text):
        value = match.group(2)
        placeholder = any(token in value.lower() for token in ("set_me", "example", "changeme", "<"))
        items.append(
            finding(
                "credential_assignment",
                value,
                commit=commit,
                path=path,
                line=line_number,
                classification="placeholder" if placeholder else "review",
            )
        )
    return items


def scan_sanitization_diff(repo: Path, parent: str, head: str) -> dict[str, Any]:
    raw_diff = git(repo, "diff", "--unified=0", "--no-color", parent, head).decode("utf-8", errors="replace")
    path = ""
    old_line = 0
    removed: list[dict[str, Any]] = []
    changed_paths: set[str] = set()
    for raw_line in raw_diff.splitlines():
        if raw_line.startswith("--- a/"):
            path = raw_line[6:]
            changed_paths.add(path)
            continue
        if raw_line.startswith("@@"):
            match = re.search(r"@@ -(\d+)(?:,\d+)? \+", raw_line)
            old_line = int(match.group(1)) if match else 0
            continue
        if raw_line.startswith("-") and not raw_line.startswith("---"):
            removed.extend(scan_line(raw_line[1:], commit=parent, path=path, line_number=old_line))
            old_line += 1
        elif not raw_line.startswith("+") and not raw_line.startswith("\\"):
            old_line += 1
    review = [item for item in removed if item["classification"] == "review"]
    return {
        "parent": parent,
        "sanitized_head": head,
        "changed_paths": sorted(changed_paths),
        "removed_redacted_findings": removed,
        "counts": {
            "all_removed_findings": len(removed),
            "review_removed_findings": len(review),
            "review_unique_fingerprints": len({item["fingerprint"] for item in review}),
            "review_by_kind": dict(Counter(item["kind"] for item in review)),
        },
    }


def scan(repo: Path) -> dict[str, Any]:
    commits = git(repo, "rev-list", "--all").decode("ascii").splitlines()
    refs = git(repo, "for-each-ref", "--format=%(refname):%(objectname)").decode("utf-8").splitlines()
    findings: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    blobs_scanned = 0
    bytes_scanned = 0
    seen_blob_context: set[tuple[str, str]] = set()
    for commit in commits:
        paths = git(repo, "ls-tree", "-r", "--name-only", commit).decode("utf-8", errors="replace").splitlines()
        for path in paths:
            context = (commit, path)
            if context in seen_blob_context:
                continue
            seen_blob_context.add(context)
            try:
                raw = git(repo, "show", f"{commit}:{path}")
            except subprocess.CalledProcessError:
                skipped.append({"commit": commit, "path": path, "reason": "git_show_failed"})
                continue
            if len(raw) > 5 * 1024 * 1024:
                skipped.append({"commit": commit, "path": path, "reason": "over_5_mib"})
                continue
            if b"\0" in raw[:8192]:
                skipped.append({"commit": commit, "path": path, "reason": "binary"})
                continue
            text = raw.decode("utf-8", errors="replace")
            blobs_scanned += 1
            bytes_scanned += len(raw)
            for line_number, line in enumerate(text.splitlines(), 1):
                findings.extend(scan_line(line, commit=commit, path=path, line_number=line_number))

    unique: dict[tuple[str, str, str, str, int], dict[str, Any]] = {}
    for item in findings:
        key = (item["commit"], item["kind"], item["fingerprint"], item["path"], item["line"])
        unique[key] = item
    deduplicated = sorted(unique.values(), key=lambda item: (item["classification"], item["kind"], item["path"], item["line"]))
    review = [item for item in deduplicated if item["classification"] == "review"]
    client_files = sorted({item["path"] for item in review if CLIENT_PATH_RE.search(item["path"])})
    head = git(repo, "rev-parse", "HEAD").decode("ascii").strip()
    parents = git(repo, "rev-list", "--parents", "-n", "1", head).decode("ascii").split()
    sanitization_diff = scan_sanitization_diff(repo, parents[1], head) if len(parents) == 2 else None
    head_review_fingerprints = {
        item["fingerprint"] for item in review if item["commit"] == head
    }
    historic_review_fingerprints = {
        item["fingerprint"] for item in review if item["commit"] != head
    }
    return {
        "status": "review_required" if review else "ok",
        "scope": {
            "commits": len(commits),
            "refs": refs,
            "branches": sum(ref.startswith("refs/heads/") for ref in refs),
            "tags": sum(ref.startswith("refs/tags/") for ref in refs),
            "blobs_scanned": blobs_scanned,
            "bytes_scanned": bytes_scanned,
            "skipped": skipped,
        },
        "counts": {
            "all_redacted_findings": len(deduplicated),
            "review_findings": len(review),
            "by_kind": dict(Counter(item["kind"] for item in deduplicated)),
            "review_by_kind": dict(Counter(item["kind"] for item in review)),
            "review_unique_fingerprints": len({item["fingerprint"] for item in review}),
            "historic_review_fingerprints_absent_from_head": len(
                historic_review_fingerprints - head_review_fingerprints
            ),
        },
        "client_data_candidate_files": client_files,
        "findings": deduplicated,
        "sanitization_diff": sanitization_diff,
        "privacy": "Values are never emitted; fingerprints are truncated SHA-256 of normalized matches.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--git-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if not (args.git_dir / "HEAD").is_file():
        raise SystemExit("--git-dir must point to a bare Git mirror")
    report = scan(args.git_dir)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], **report["scope"], **report["counts"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
