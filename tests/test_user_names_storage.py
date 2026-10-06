"""Tests for migration v5 and the user-name cache table."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import AsyncIterator

import pytest

from yokai.storage.db import CURRENT_SCHEMA_VERSION, Database


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    """An open database that is always closed, even when the test fails."""
    database = Database(tmp_path / "t.db")
    await database.initialize()
    try:
        yield database
    finally:
        await database.close()


async def _names(db: Database) -> list[tuple[int, str, int]]:
    async with db.conn.execute("SELECT user_id, display_name, updated_ts FROM user_names;") as c:
        return [tuple(row) for row in await c.fetchall()]  # type: ignore[misc]


async def test_schema_is_at_v5_with_the_user_names_table(db: Database) -> None:
    assert CURRENT_SCHEMA_VERSION == 5
    async with db.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table';") as c:
        assert "user_names" in {row[0] for row in await c.fetchall()}


async def test_upsert_inserts_then_updates_one_row(db: Database) -> None:
    await db.upsert_user_name(7, "Sami", now=100)
    await db.upsert_user_name(7, "Sami (renamed)", now=200)
    assert await _names(db) == [(7, "Sami (renamed)", 200)]


async def test_a_v4_database_upgrades_to_v5_keeping_its_rows(db: Database, tmp_path: Path) -> None:
    await db.write_heartbeat(pid=1, version="x", now=5)
    await db.conn.execute(
        "INSERT INTO blacklist (user_id, reason, added_ts, added_by) VALUES (9, 'spam', 1, 'x');"
    )
    await db.conn.commit()
    await db.close()

    raw = sqlite3.connect(tmp_path / "t.db")
    try:
        raw.execute("DROP TABLE user_names;")
        raw.execute("PRAGMA user_version = 4;")
        raw.commit()
    finally:
        raw.close()

    upgraded = Database(tmp_path / "t.db")
    await upgraded.initialize()
    try:
        async with upgraded.conn.execute("PRAGMA user_version;") as c:
            row = await c.fetchone()
        assert row is not None and row[0] == 5
        assert await upgraded.get_moderation_lists() == (frozenset({9}), frozenset())
        await upgraded.upsert_user_name(1, "ok", now=1)  # the table exists again
    finally:
        await upgraded.close()
