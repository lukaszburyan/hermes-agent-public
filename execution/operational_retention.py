#!/usr/bin/env python3
"""Apply the Hermes 7/90-day retention policy.

Raw operational files are deleted after seven days. Audit transition records
and explicitly supplied audit directories are pruned after ninety days.
Idempotency messages and operation rows are deliberately retained because
removing them could permit duplicate customer actions.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import stat
from pathlib import Path
from typing import Any, Iterable

DEFAULT_RAW_DAYS = 7
DEFAULT_AUDIT_DAYS = 90


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def cutoff_timestamp(days: int, now: dt.datetime | None = None) -> float:
    if days < 0:
        raise ValueError("retention days cannot be negative")
    current = now or utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.timezone.utc)
    return (current - dt.timedelta(days=days)).timestamp()


def cutoff_iso(days: int, now: dt.datetime | None = None) -> str:
    current = now or utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.timezone.utc)
    return (current - dt.timedelta(days=days)).astimezone(dt.timezone.utc).isoformat(timespec="seconds")


def _regular_files(root: Path) -> Iterable[Path]:
    if not root.exists() or root.is_symlink():
        return []
    files: list[Path] = []
    for path in root.rglob("*"):
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISREG(mode) and not stat.S_ISLNK(mode):
            files.append(path)
    return files


def prune_files(root: Path, *, days: int, now: dt.datetime | None = None, dry_run: bool = False) -> dict[str, Any]:
    threshold = cutoff_timestamp(days, now)
    removed: list[str] = []
    errors: list[dict[str, str]] = []
    for path in _regular_files(root):
        try:
            if path.stat().st_mtime >= threshold:
                continue
            removed.append(str(path))
            if not dry_run:
                path.unlink()
        except OSError as exc:
            errors.append({"path": str(path), "error": exc.__class__.__name__})
    return {
        "kind": "directory",
        "path": str(root),
        "days": days,
        "removed_count": len(removed),
        "removed": removed,
        "errors": errors,
        "dry_run": dry_run,
    }


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def prune_state_transitions(
    database: Path,
    *,
    days: int,
    now: dt.datetime | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "kind": "sqlite_state_transitions",
        "path": str(database),
        "days": days,
        "removed_count": 0,
        "dry_run": dry_run,
    }
    if not database.exists():
        result["status"] = "missing"
        return result
    cutoff = cutoff_iso(days, now)
    try:
        connection = sqlite3.connect(database, timeout=5)
        try:
            if not _table_exists(connection, "state_transitions"):
                result["status"] = "table_missing"
                return result
            columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(state_transitions)").fetchall()
            }
            if "timestamp" not in columns:
                raise sqlite3.OperationalError("state_transitions.timestamp is required")
            count_row = connection.execute(
                "SELECT COUNT(*) FROM state_transitions WHERE timestamp < ?",
                (cutoff,),
            ).fetchone()
            result["removed_count"] = int(count_row[0] if count_row else 0)
            if not dry_run and result["removed_count"]:
                connection.execute("DELETE FROM state_transitions WHERE timestamp < ?", (cutoff,))
            if not dry_run:
                completed_at = (now or utc_now()).astimezone(dt.timezone.utc).isoformat(timespec="seconds")
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS retention_metrics ("
                    "name TEXT PRIMARY KEY,last_success_at TEXT,last_error TEXT,updated_at TEXT NOT NULL)"
                )
                connection.execute(
                    "INSERT INTO retention_metrics(name,last_success_at,last_error,updated_at) VALUES('state_transitions',?,'',?) "
                    "ON CONFLICT(name) DO UPDATE SET last_success_at=excluded.last_success_at,last_error='',updated_at=excluded.updated_at",
                    (completed_at, completed_at),
                )
                connection.commit()
            result["status"] = "ok"
            result["last_success_at"] = None if dry_run else completed_at
            return result
        finally:
            connection.close()
    except sqlite3.Error as exc:
        result["status"] = "error"
        result["error"] = exc.__class__.__name__
        return result


def apply_retention(
    *,
    raw_dirs: Iterable[Path],
    audit_dirs: Iterable[Path],
    audit_databases: Iterable[Path],
    raw_days: int = DEFAULT_RAW_DAYS,
    audit_days: int = DEFAULT_AUDIT_DAYS,
    now: dt.datetime | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    return {
        "raw_days": raw_days,
        "audit_days": audit_days,
        "raw": [prune_files(path, days=raw_days, now=now, dry_run=dry_run) for path in raw_dirs],
        "audit_files": [prune_files(path, days=audit_days, now=now, dry_run=dry_run) for path in audit_dirs],
        "audit_databases": [
            prune_state_transitions(path, days=audit_days, now=now, dry_run=dry_run)
            for path in audit_databases
        ],
        "dry_run": dry_run,
    }


def retention_failed(result: dict[str, Any]) -> bool:
    file_results = [
        *result.get("raw", []),
        *result.get("audit_files", []),
    ]
    if any(item.get("errors") for item in file_results if isinstance(item, dict)):
        return True
    return any(
        item.get("status") == "error"
        for item in result.get("audit_databases", [])
        if isinstance(item, dict)
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply Hermes raw-log and audit-history retention.")
    parser.add_argument("--raw-dir", action="append", default=[], type=Path)
    parser.add_argument("--audit-dir", action="append", default=[], type=Path)
    parser.add_argument("--audit-sqlite", action="append", default=[], type=Path)
    parser.add_argument("--raw-days", type=int, default=DEFAULT_RAW_DAYS)
    parser.add_argument("--audit-days", type=int, default=DEFAULT_AUDIT_DAYS)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.raw_days < 0 or args.audit_days < 0:
        parser.error("retention days cannot be negative")
    result = apply_retention(
        raw_dirs=args.raw_dir,
        audit_dirs=args.audit_dir,
        audit_databases=args.audit_sqlite,
        raw_days=args.raw_days,
        audit_days=args.audit_days,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 1 if retention_failed(result) else 0


if __name__ == "__main__":
    raise SystemExit(main())
