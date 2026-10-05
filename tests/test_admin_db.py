"""Tests for the panel's read-only access to the bot's SQLite file."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import AsyncIterator

import pytest

from yokai.storage.db import Database
from yokai_admin.core.db import MIN_SCHEMA_VERSION, readonly, require_schema
from yokai_admin.errors import PanelDbError


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    """An open bot database that is always closed, even when the test fails."""
    database = Database(tmp_path / "t.db")
    await database.initialize()
    try:
        yield database
    finally:
        await database.close()


def test_a_missing_file_says_to_start_the_bot(tmp_path: Path) -> None:
    with pytest.raises(PanelDbError, match="not found"):
        with readonly(tmp_path / "nope.db"):
            pass


async def test_reads_a_live_database_the_bot_still_has_open(db: Database, tmp_path: Path) -> None:
    await db.credit_wallet(1, 10, reason="daily", welcome_bonus=200)
    with readonly(tmp_path / "t.db") as conn:
        assert conn.execute("SELECT balance FROM wallets WHERE user_id = 1;").fetchone()[0] == 210


async def test_reads_a_database_the_bot_closed_cleanly(db: Database, tmp_path: Path) -> None:
    await db.credit_wallet(1, 10, reason="daily", welcome_bonus=200)
    await db.close()
    with readonly(tmp_path / "t.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM wallets;").fetchone()[0] == 1


async def test_the_connection_cannot_write(db: Database, tmp_path: Path) -> None:
    with readonly(tmp_path / "t.db") as conn:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO blacklist (user_id, added_ts, added_by) VALUES (1, 1, 'x');")


async def test_require_schema_accepts_the_current_version(db: Database, tmp_path: Path) -> None:
    with readonly(tmp_path / "t.db") as conn:
        assert require_schema(conn) == MIN_SCHEMA_VERSION


def test_an_old_schema_is_refused_with_a_hint(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    raw.execute("PRAGMA user_version = 3;")
    raw.execute("CREATE TABLE t (x INTEGER);")
    raw.commit()
    raw.close()
    with readonly(path) as conn:
        with pytest.raises(PanelDbError, match=r"v3.*v4"):
            require_schema(conn)


def test_a_file_that_is_not_sqlite_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "junk.db"
    path.write_bytes(b"this is not a database" * 50)
    with readonly(path) as conn:
        with pytest.raises(PanelDbError, match="not a SQLite database"):
            require_schema(conn)
