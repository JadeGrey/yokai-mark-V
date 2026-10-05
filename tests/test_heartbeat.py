"""Tests for the heartbeat writer the admin panel uses to show bot online/offline."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from yokai.heartbeat import HeartbeatWriter
from yokai.storage.db import Database


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    """An open database that is always closed, even when the test fails."""
    database = Database(tmp_path / "t.db")
    await database.initialize()
    try:
        yield database
    finally:
        await database.close()


async def _heartbeat_rows(db: Database) -> int:
    async with db.conn.execute("SELECT COUNT(*) FROM bot_heartbeat;") as cursor:
        row = await cursor.fetchone()
    assert row is not None
    return int(row[0])


async def test_beat_writes_pid_and_version(db: Database) -> None:
    writer = HeartbeatWriter(db, version="9.9.9", pid=4242)

    await writer.beat()

    async with db.conn.execute("SELECT pid, version FROM bot_heartbeat;") as cursor:
        assert [tuple(r) for r in await cursor.fetchall()] == [(4242, "9.9.9")]


async def test_stop_clears_the_row_when_it_was_running(db: Database) -> None:
    writer = HeartbeatWriter(db, version="1", interval_s=60, pid=1)
    writer.start()
    for _ in range(100):  # let the first beat land
        if await _heartbeat_rows(db) == 1:
            break
        await asyncio.sleep(0.01)
    assert await _heartbeat_rows(db) == 1

    await writer.stop()

    assert await _heartbeat_rows(db) == 0


async def test_stop_without_start_touches_nothing() -> None:
    db = MagicMock()
    db.clear_heartbeat = AsyncMock()
    writer = HeartbeatWriter(db, version="1")
    await writer.stop()
    db.clear_heartbeat.assert_not_awaited()


async def test_start_is_idempotent() -> None:
    db = MagicMock()
    db.write_heartbeat = AsyncMock()
    db.clear_heartbeat = AsyncMock()
    writer = HeartbeatWriter(db, version="1", interval_s=60)
    writer.start()
    first = writer._task
    writer.start()
    assert writer._task is first
    await writer.stop()


async def test_a_failed_beat_does_not_kill_the_loop() -> None:
    db = MagicMock()
    db.write_heartbeat = AsyncMock(side_effect=[sqlite3.OperationalError("locked"), None, None])
    db.clear_heartbeat = AsyncMock()
    writer = HeartbeatWriter(db, version="1", interval_s=0.01)
    writer.start()
    for _ in range(200):
        if db.write_heartbeat.await_count >= 2:
            break
        await asyncio.sleep(0.01)
    await writer.stop()
    assert db.write_heartbeat.await_count >= 2
