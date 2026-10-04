from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._journal_mode_ready = False
        self._journal_mode_lock = threading.Lock()

    def _ensure_journal_mode(self, connection: sqlite3.Connection) -> None:
        if self._journal_mode_ready:
            return
        with self._journal_mode_lock:
            if self._journal_mode_ready:
                return
            row = connection.execute("PRAGMA journal_mode = WAL").fetchone()
            mode = str(row[0]).lower() if row else ""
            if mode != "wal":
                raise RuntimeError(f"SQLite WAL initialization failed: {mode or 'unknown'}")
            self._journal_mode_ready = True

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            # WAL is persistent for a database file. Reapplying it for every
            # short read takes an exclusive SQLite lock and dominates local
            # policy checks on Windows, so initialize and verify it once per
            # Database instance while retaining per-connection safety PRAGMAs.
            self._ensure_journal_mode(connection)
        except Exception:
            connection.close()
            raise
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def session(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            yield connection
        finally:
            connection.close()

    @staticmethod
    def expected_migration_versions() -> tuple[str, ...]:
        migration_dir = Path(__file__).with_name("migrations")
        return tuple(migration.stem for migration in sorted(migration_dir.glob("*.sql")))

    def migrate(self) -> None:
        migration_dir = Path(__file__).with_name("migrations")
        with self.transaction() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            applied = {
                row["version"]
                for row in connection.execute("SELECT version FROM schema_migrations")
            }
            for migration in sorted(migration_dir.glob("*.sql")):
                if migration.stem in applied:
                    continue
                connection.executescript(migration.read_text(encoding="utf-8"))
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (migration.stem, utc_now()),
                )
