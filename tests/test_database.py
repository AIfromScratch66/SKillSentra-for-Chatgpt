from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.database import Database


class _FakeConnection:
    def __init__(self, journal_mode: str = "wal") -> None:
        self.journal_mode = journal_mode
        self.row_factory = None
        self.executed: list[str] = []
        self.closed = False

    def execute(self, statement: str) -> object:
        self.executed.append(statement)
        if statement == "PRAGMA journal_mode = WAL":
            return _FakeCursor((self.journal_mode,))
        return _FakeCursor(None)

    def close(self) -> None:
        self.closed = True


class _FakeCursor:
    def __init__(self, row: tuple[str] | None) -> None:
        self.row = row

    def fetchone(self) -> tuple[str] | None:
        return self.row


class DatabaseTests(unittest.TestCase):
    def test_wal_is_initialized_once_per_database_instance(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            database = Database(Path(temp_name) / "test.db")
            connections = [_FakeConnection(), _FakeConnection()]
            with patch("app.database.sqlite3.connect", side_effect=connections):
                first = database.connect()
                second = database.connect()

        self.assertEqual(first.executed.count("PRAGMA journal_mode = WAL"), 1)
        self.assertEqual(second.executed.count("PRAGMA journal_mode = WAL"), 0)
        self.assertEqual(first.executed[-2:], ["PRAGMA busy_timeout = 5000", "PRAGMA journal_mode = WAL"])
        self.assertEqual(second.executed[-1], "PRAGMA busy_timeout = 5000")

    def test_wal_initialization_failure_is_closed_and_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            database = Database(Path(temp_name) / "test.db")
            connection = _FakeConnection("delete")
            with patch("app.database.sqlite3.connect", return_value=connection):
                with self.assertRaisesRegex(RuntimeError, "SQLite WAL initialization failed"):
                    database.connect()

        self.assertTrue(connection.closed)


if __name__ == "__main__":
    unittest.main()
