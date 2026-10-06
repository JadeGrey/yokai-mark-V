"""Tests for the panel's read-only access to the bot's SQLite file."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import AsyncIterator

import pytest

from yokai.moderation.store import ModerationStore
from yokai.storage.db import Database
from yokai_admin.core import db as panel_db
from yokai_admin.core.db import MIN_SCHEMA_VERSION, readonly, require_schema, write_transaction
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
        with pytest.raises(PanelDbError, match=r"v3.*v5"):
            require_schema(conn)


def test_a_file_that_is_not_sqlite_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "junk.db"
    path.write_bytes(b"this is not a database" * 50)
    with readonly(path) as conn:
        with pytest.raises(PanelDbError, match="not a SQLite database"):
            require_schema(conn)


async def test_a_write_transaction_commits_and_the_bot_sees_it(
    db: Database, tmp_path: Path
) -> None:
    """Review Focus 5: the bot's own connection reads what the panel wrote."""
    with write_transaction(tmp_path / "t.db") as conn:
        conn.execute(
            "INSERT INTO blacklist (user_id, reason, added_ts, added_by) "
            "VALUES (5, 'spam', 1, 'panel');"
        )
    assert await db.get_moderation_lists() == (frozenset({5}), frozenset())
    store = ModerationStore(db, owner_id=1, ttl_s=0)
    assert await store.is_blacklisted(5) is True


async def test_a_failing_write_transaction_rolls_everything_back(
    db: Database, tmp_path: Path
) -> None:
    with pytest.raises(RuntimeError):
        with write_transaction(tmp_path / "t.db") as conn:
            conn.execute(
                "INSERT INTO blacklist (user_id, reason, added_ts, added_by) "
                "VALUES (5, 'x', 1, 'p');"
            )
            raise RuntimeError("boom")
    assert await db.get_moderation_lists() == (frozenset(), frozenset())


async def test_a_busy_database_gives_a_friendly_error(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(panel_db, "WRITE_BUSY_TIMEOUT_S", 0.2)
    holder = sqlite3.connect(tmp_path / "t.db", isolation_level=None)
    holder.execute("BEGIN IMMEDIATE;")
    try:
        with pytest.raises(PanelDbError, match="busy"):
            with write_transaction(tmp_path / "t.db"):
                pass
    finally:
        holder.execute("ROLLBACK;")
        holder.close()


def test_writing_to_an_old_schema_is_refused_and_changes_nothing(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    raw.execute("PRAGMA user_version = 4;")
    raw.execute(
        "CREATE TABLE blacklist (user_id INTEGER PRIMARY KEY, reason TEXT, "
        "added_ts INTEGER, added_by TEXT);"
    )
    raw.commit()
    raw.close()
    with pytest.raises(PanelDbError, match="v4.*v5"):
        with write_transaction(path) as conn:
            conn.execute("INSERT INTO blacklist VALUES (1, 'x', 1, 'p');")
    check = sqlite3.connect(path)
    assert check.execute("SELECT COUNT(*) FROM blacklist;").fetchone()[0] == 0
    check.close()


def test_writing_to_a_missing_or_non_sqlite_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(PanelDbError, match="not found"):
        with write_transaction(tmp_path / "nope.db"):
            pass
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"this is not a database" * 50)
    with pytest.raises(PanelDbError):
        with write_transaction(junk):
            pass
