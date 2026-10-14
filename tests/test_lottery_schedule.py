"""Migration v7 and the lottery's state and schedule."""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from yokai.storage.db import (
    CURRENT_SCHEMA_VERSION,
    MIGRATION_V1,
    MIGRATION_V2,
    MIGRATION_V3,
    MIGRATION_V4,
    MIGRATION_V5,
    MIGRATION_V6,
    Database,
)

WEEK = 7 * 24 * 3600


async def _db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "lottery.db")
    await db.initialize()
    return db


async def _tables(db: Database) -> set[str]:
    async with db.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table';") as cur:
        return {row[0] for row in await cur.fetchall()}


@pytest.mark.asyncio
async def test_migration_v7_creates_the_lottery_tables_and_state_columns(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        assert CURRENT_SCHEMA_VERSION >= 7
        assert {"lottery_tickets", "lottery_draws"} <= await _tables(db)
        state = await db.get_lottery_state()
        assert (state.pot, state.draw_no, state.next_draw_ts) == (0, 1, None)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_v6_database_upgrades_keeping_its_pot_and_rows(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    raw = await aiosqlite.connect(path)
    for script in (MIGRATION_V1, MIGRATION_V2, MIGRATION_V3, MIGRATION_V4, MIGRATION_V5):
        await raw.executescript(script)
    await raw.executescript(MIGRATION_V6)
    await raw.execute("PRAGMA user_version = 6;")
    await raw.execute("UPDATE lottery_state SET pot = 777 WHERE id = 1;")
    await raw.execute(
        "INSERT INTO wallets (user_id, balance, daily_streak, created_ts) VALUES (7, 321, 0, 1);"
    )
    await raw.commit()
    await raw.close()

    db = Database(path)
    await db.initialize()
    try:
        state = await db.get_lottery_state()
        assert (state.pot, state.draw_no, state.next_draw_ts) == (777, 1, None)
        wallet = await db.get_wallet(7)
        assert wallet is not None and wallet.balance == 321
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_the_schedule_starts_once_and_is_then_left_alone(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        assert await db.ensure_lottery_schedule(1_000, WEEK) == 1_000 + WEEK
        assert await db.ensure_lottery_schedule(5_000, WEEK) == 1_000 + WEEK  # not restarted
        state = await db.get_lottery_state()
        assert state.next_draw_ts == 1_000 + WEEK
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_fresh_lottery_has_no_tickets(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        assert await db.lottery_tickets_sold() == 0
        assert await db.user_lottery_numbers(1) == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_migration_v7_can_be_replayed_after_a_half_finished_upgrade(tmp_path: Path) -> None:
    """The version number only moves at the end, so a crash mid-upgrade replays the whole step.

    ALTER TABLE ... ADD COLUMN fails on a column that already exists, so the step must add the
    lottery columns only when they are missing.
    """
    path = tmp_path / "replay.db"
    db = Database(path)
    await db.initialize()
    await db.ensure_lottery_schedule(1_000, WEEK)
    await db.conn.execute("PRAGMA user_version = 6;")  # as if the upgrade had not finished
    await db.conn.commit()
    await db.close()

    again = Database(path)
    await again.initialize()  # must not raise "duplicate column name"
    try:
        state = await again.get_lottery_state()
        assert state.next_draw_ts == 1_000 + WEEK  # nothing was reset
        async with again.conn.execute("PRAGMA user_version;") as cursor:
            row = await cursor.fetchone()
        assert row is not None and row[0] == CURRENT_SCHEMA_VERSION
    finally:
        await again.close()
