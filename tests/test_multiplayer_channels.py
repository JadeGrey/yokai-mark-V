"""Storage for dedicated table channels."""

from __future__ import annotations

from pathlib import Path

import pytest

from yokai.gambling.models import MultiplayerChannel
from yokai.storage.db import Database


async def _db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "channels.db")
    await db.initialize()
    return db


@pytest.mark.asyncio
async def test_a_channel_can_be_dedicated_and_read_back(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        removed = await db.set_multiplayer_channel(10, "duel", 555)
        assert removed == []
        assert await db.get_multiplayer_channel(10) == MultiplayerChannel(
            10, "duel", 555, None, False
        )
        assert await db.get_multiplayer_channel(11) is None
        assert await db.list_multiplayer_channels() == [
            MultiplayerChannel(10, "duel", 555, None, False)
        ]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_dedicating_the_same_game_elsewhere_moves_it_and_returns_the_old_row(
    tmp_path: Path,
) -> None:
    db = await _db(tmp_path)
    try:
        await db.set_multiplayer_channel(10, "duel", 555)
        await db.set_table_message(10, 600, active=False)
        removed = await db.set_multiplayer_channel(20, "duel", 777)
        assert removed == [MultiplayerChannel(10, "duel", 555, 600, False)]
        assert await db.get_multiplayer_channel(10) is None
        assert (await db.get_multiplayer_channel(20)).play_message_id == 777  # type: ignore[union-attr]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_repointing_a_channel_to_another_game_replaces_its_row(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        await db.set_multiplayer_channel(10, "duel", 555)
        removed = await db.set_multiplayer_channel(10, "jackpot", 888)
        assert [r.game for r in removed] == ["duel"]
        current = await db.get_multiplayer_channel(10)
        assert current is not None and current.game == "jackpot"
        assert len(await db.list_multiplayer_channels()) == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_table_message_and_active_flag_round_trip(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        await db.set_multiplayer_channel(10, "duel", 555)
        await db.set_table_message(10, 900, active=True)
        row = await db.get_multiplayer_channel(10)
        assert row is not None and (row.table_message_id, row.table_active) == (900, True)
        await db.set_table_inactive(10)
        row = await db.get_multiplayer_channel(10)
        assert row is not None and (row.table_message_id, row.table_active) == (900, False)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_set_play_message_replaces_only_the_play_message(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        await db.set_multiplayer_channel(10, "duel", 555)
        await db.set_table_message(10, 900, active=False)
        await db.set_play_message(10, 556)
        row = await db.get_multiplayer_channel(10)
        assert row == MultiplayerChannel(10, "duel", 556, 900, False)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_clearing_returns_the_row_once(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        await db.set_multiplayer_channel(10, "duel", 555)
        assert (await db.clear_multiplayer_channel(10)) == MultiplayerChannel(
            10, "duel", 555, None, False
        )
        assert await db.clear_multiplayer_channel(10) is None
        assert await db.list_multiplayer_channels() == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_updating_an_unknown_channel_is_a_quiet_no_op(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        await db.set_table_message(99, 1, active=True)
        await db.set_table_inactive(99)
        await db.set_play_message(99, 1)
        assert await db.list_multiplayer_channels() == []
    finally:
        await db.close()
