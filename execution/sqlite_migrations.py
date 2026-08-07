#!/usr/bin/env python3
"""Small transactional SQLite migration runner used by Hermes state stores."""

from __future__ import annotations

import fcntl
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Sequence


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    apply: Callable[[sqlite3.Connection], None]


@contextmanager
def migration_lock(database: str | Path) -> Iterator[None]:
    """Serialize schema bootstrap before SQLite tables are guaranteed to exist."""
    path = Path(database)
    lock_path = path.with_name(path.name + ".migrate.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def run_migrations(
    connection: sqlite3.Connection,
    *,
    namespace: str,
    migrations: Sequence[Migration],
) -> int:
    """Apply ordered migrations once under one exclusive transaction."""
    versions = [migration.version for migration in migrations]
    if versions != sorted(set(versions)) or any(version < 1 for version in versions):
        raise ValueError("migrations_must_have_unique_positive_ordered_versions")
    try:
        connection.execute("BEGIN EXCLUSIVE")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_versions ("
            "namespace TEXT NOT NULL, version INTEGER NOT NULL, name TEXT NOT NULL, "
            "applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(namespace,version))"
        )
        applied = {
            int(row[0])
            for row in connection.execute(
                "SELECT version FROM schema_versions WHERE namespace=?",
                (namespace,),
            ).fetchall()
        }
        for migration in migrations:
            if migration.version in applied:
                continue
            migration.apply(connection)
            connection.execute(
                "INSERT INTO schema_versions(namespace,version,name) VALUES(?,?,?)",
                (namespace, migration.version, migration.name),
            )
        connection.execute("COMMIT")
    except Exception:
        try:
            connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    row = connection.execute(
        "SELECT COALESCE(MAX(version),0) FROM schema_versions WHERE namespace=?",
        (namespace,),
    ).fetchone()
    return int(row[0] if row else 0)


def add_column_if_missing(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    declaration: str,
) -> None:
    columns = {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")

