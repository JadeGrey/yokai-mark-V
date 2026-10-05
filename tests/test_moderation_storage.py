"""Tests for migration v4 and the moderation/heartbeat Database methods."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import AsyncIterator

import pytest

from yokai.storage.db import CURRENT_SCHEMA_VERSION, Database


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    """An open database that is always closed, even when the test fails.

    aiosqlite runs a worker thread; a test that fails before `close()` would otherwise keep the
    interpreter alive and hang pytest at exit.
    """
    database = Database(tmp_path / "t.db")
    await database.initialize()
    try:
        yield database
    finally:
        await database.close()


async def test_migration_v4_creates_the_moderation_tables(db: Database) -> None:
    async with db.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table';") as cursor:
        names = {row[0] for row in await cursor.fetchall()}
    assert {"blacklist", "admins", "admin_audit", "bot_heartbeat"} <= names
    assert CURRENT_SCHEMA_VERSION == 4


async def test_reopening_a_v4_database_is_a_no_op(db: Database, tmp_path: Path) -> None:
    await db.close()
    reopened = Database(tmp_path / "t.db")
    await reopened.initialize()
    try:
        async with reopened.conn.execute("PRAGMA user_version;") as cursor:
            row = await cursor.fetchone()
        assert row is not None and row[0] == 4
    finally:
        await reopened.close()


async def test_a_v3_database_with_data_upgrades_without_losing_rows(
    db: Database, tmp_path: Path
) -> None:
    """Review Focus 1: a real v3 file keeps its rows when it gains the v4 tables."""
    await db.log_command(user_id=101, command="play")
    await db.credit_wallet(101, 50, reason="daily", welcome_bonus=200)
    await db.close()

    # Rewind the version marker and drop the v4 tables to mimic a genuine v3 file.
    raw = sqlite3.connect(tmp_path / "t.db")
    try:
        for table in ("blacklist", "admins", "admin_audit", "bot_heartbeat"):
            raw.execute(f"DROP TABLE {table};")
        raw.execute("PRAGMA user_version = 3;")
        raw.commit()
    finally:
        raw.close()

    upgraded = Database(tmp_path / "t.db")
    await upgraded.initialize()
    try:
        async with upgraded.conn.execute("PRAGMA user_version;") as cursor:
            row = await cursor.fetchone()
        assert row is not None and row[0] == 4
        async with upgraded.conn.execute("SELECT COUNT(*) FROM command_log;") as cursor:
            assert (await cursor.fetchone())[0] == 1  # type: ignore[index]
        wallet = await upgraded.get_wallet(101)
        assert wallet is not None and wallet.balance == 250
    finally:
        await upgraded.close()


async def test_bot_heartbeat_allows_only_one_row(db: Database) -> None:
    await db.conn.execute("INSERT INTO bot_heartbeat (id, ts, pid, version) VALUES (1, 1, 1, 'x');")
    with pytest.raises(sqlite3.IntegrityError):
        await db.conn.execute(
            "INSERT INTO bot_heartbeat (id, ts, pid, version) VALUES (2, 1, 1, 'x');"
        )
