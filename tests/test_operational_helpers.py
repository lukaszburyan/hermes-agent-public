from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXECUTION = ROOT / "execution"
if str(EXECUTION) not in sys.path:
    sys.path.insert(0, str(EXECUTION))

from operational_retention import (  # noqa: E402
    cutoff_timestamp,
    prune_files,
    prune_state_transitions,
    retention_failed,
)
from redact_operational_log import redact_file, redact_text  # noqa: E402


def test_operational_redactor_removes_supported_secret_shapes():
    raw = (
        "access_token=abcdefghijk Authorization: Bearer abcdefghijk\n"
        "https://example.test/?client_secret=abcdefghijkl&safe=yes\n"
        "Zoho-oauthtoken abcdefghijk"
    )
    redacted = redact_text(raw)
    assert "abcdefghijk" not in redacted
    assert "abcdefghijkl" not in redacted
    assert redacted.count("[REDACTED]") >= 3
    assert "safe=yes" in redacted


def test_redacted_operational_file_is_private(tmp_path: Path):
    source = tmp_path / "raw.log"
    destination = tmp_path / "safe.log"
    source.write_text("token=abcdefghijk", encoding="utf-8")
    redact_file(source, destination)
    assert destination.read_text(encoding="utf-8") == "token=[REDACTED]"
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600


def test_retention_dry_run_reports_stale_file_without_deleting_it(tmp_path: Path):
    now = dt.datetime(2026, 8, 4, 12, tzinfo=dt.timezone.utc)
    stale = tmp_path / "stale.log"
    fresh = tmp_path / "fresh.log"
    stale.write_text("stale", encoding="utf-8")
    fresh.write_text("fresh", encoding="utf-8")
    os.utime(stale, (cutoff_timestamp(8, now), cutoff_timestamp(8, now)))
    os.utime(fresh, (cutoff_timestamp(1, now), cutoff_timestamp(1, now)))

    result = prune_files(tmp_path, days=7, now=now, dry_run=True)
    assert result["removed"] == [str(stale)]
    assert stale.exists()
    assert fresh.exists()


def test_retention_sqlite_dry_run_preserves_idempotency_and_transition_rows(tmp_path: Path):
    database = tmp_path / "state.sqlite3"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE state_transitions (timestamp TEXT NOT NULL);
        CREATE TABLE messages (message_id TEXT PRIMARY KEY);
        INSERT INTO state_transitions(timestamp) VALUES ('2020-01-01T00:00:00+00:00');
        INSERT INTO messages(message_id) VALUES ('keep-idempotency');
        """
    )
    connection.commit()
    connection.close()
    result = prune_state_transitions(
        database,
        days=90,
        now=dt.datetime(2026, 8, 4, tzinfo=dt.timezone.utc),
        dry_run=True,
    )
    assert result["removed_count"] == 1
    connection = sqlite3.connect(database)
    assert connection.execute("SELECT COUNT(*) FROM state_transitions").fetchone()[0] == 1
    assert connection.execute("SELECT message_id FROM messages").fetchone()[0] == "keep-idempotency"
    connection.close()


def test_retention_deletes_only_old_transitions_and_records_last_success(tmp_path: Path):
    database = tmp_path / "state.sqlite3"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE state_transitions (timestamp TEXT NOT NULL);
        INSERT INTO state_transitions(timestamp) VALUES ('2020-01-01T00:00:00+00:00');
        INSERT INTO state_transitions(timestamp) VALUES ('2026-08-03T00:00:00+00:00');
        """
    )
    connection.commit()
    connection.close()
    now = dt.datetime(2026, 8, 4, tzinfo=dt.timezone.utc)
    result = prune_state_transitions(database, days=90, now=now, dry_run=False)
    assert result["status"] == "ok"
    assert result["removed_count"] == 1
    connection = sqlite3.connect(database)
    remaining = connection.execute("SELECT timestamp FROM state_transitions").fetchall()
    metric = connection.execute(
        "SELECT last_success_at,last_error FROM retention_metrics WHERE name='state_transitions'"
    ).fetchone()
    connection.close()
    assert remaining == [("2026-08-03T00:00:00+00:00",)]
    assert metric == ("2026-08-04T00:00:00+00:00", "")


def test_retention_exposes_legacy_created_at_schema_error(tmp_path: Path):
    database = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE state_transitions (created_at TEXT NOT NULL)")
    connection.commit()
    connection.close()
    result = prune_state_transitions(database, days=90, dry_run=False)
    assert result["status"] == "error"
    assert result["error"] == "OperationalError"
    assert retention_failed({"raw": [], "audit_files": [], "audit_databases": [result]})


def test_retention_cli_returns_nonzero_for_visible_database_error(tmp_path: Path):
    database = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE state_transitions (created_at TEXT NOT NULL)")
    connection.commit()
    connection.close()
    result = subprocess.run(
        [
            sys.executable,
            str(EXECUTION / "operational_retention.py"),
            "--audit-sqlite",
            str(database),
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["audit_databases"][0]["status"] == "error"


def test_negative_retention_is_rejected():
    with pytest.raises(ValueError, match="cannot be negative"):
        cutoff_timestamp(-1)
