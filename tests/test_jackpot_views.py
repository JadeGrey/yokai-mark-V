"""The jackpot runner: draw, reveal, settle, and refund on any failure."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from tests.moderation_helpers import fake_moderation
from tests.table_helpers import ALL_PLAYABLE, CHANNEL, announce_ok, balance_of, world
from tests.test_table_manager import FixedRoll
from yokai.gambling.lobby import LobbyState
from yokai.ui.jackpot_views import run_jackpot


def make_bot(db, manager) -> MagicMock:  # type: ignore[no-untyped-def]
    bot = MagicMock()
    bot.user = None
    bot.db = db
    bot.tables = manager
    bot.moderation = fake_moderation(owner_id=999)
    return bot


def make_message() -> MagicMock:
    message = MagicMock(spec=discord.Message)
    message.edit = AsyncMock()
    return message


async def _running(manager):  # type: ignore[no-untyped-def]
    lobby = await manager.open_table(CHANNEL, "jackpot", 1, 100, announce_ok)
    await manager.join(lobby, 2, 300)
    await manager.begin(lobby)
    return lobby


@pytest.mark.asyncio
async def test_the_runner_shows_the_draw_then_the_result_and_pays_the_winner(
    tmp_path: Path,
) -> None:
    async with world(tmp_path, specs=ALL_PLAYABLE) as (db, manager, active):
        manager.rng_factory = lambda: FixedRoll(399)  # the last entrant, user 2
        lobby = await _running(manager)
        message = make_message()
        with patch("yokai.ui.jackpot_views.JACKPOT_REVEAL_DELAY_S", 0):
            await run_jackpot(make_bot(db, manager), lobby, message)
        assert message.edit.await_count == 2
        drawing = message.edit.await_args_list[0].kwargs["embed"]
        assert "Drawing" in drawing.description
        final = message.edit.await_args.kwargs
        assert "<@2> wins 392" in final["embed"].description and final["view"] is None
        assert await balance_of(db, 2) == 1000 - 300 + 392
        assert await db.get_pot() == 8 and lobby.state is LobbyState.SETTLED
        assert not active.is_active(1) and not active.is_active(2)


@pytest.mark.asyncio
async def test_a_failure_while_drawing_refunds_everyone_and_tells_the_players(
    tmp_path: Path,
) -> None:
    async with world(tmp_path, specs=ALL_PLAYABLE) as (db, manager, active):
        lobby = await _running(manager)
        message = make_message()
        failing = AsyncMock(side_effect=RuntimeError("disk"))
        with (
            patch("yokai.ui.jackpot_views.JACKPOT_REVEAL_DELAY_S", 0),
            patch.object(manager, "resolve_jackpot", new=failing),
        ):
            with pytest.raises(RuntimeError):
                await run_jackpot(make_bot(db, manager), lobby, message)
        assert lobby.state is LobbyState.CANCELLED
        assert (await balance_of(db, 1), await balance_of(db, 2)) == (1000, 1000)
        assert not active.is_active(1)
        assert "cancelled" in message.edit.await_args.kwargs["embed"].title.lower()


@pytest.mark.asyncio
async def test_a_message_that_cannot_be_edited_still_refunds(tmp_path: Path) -> None:
    async with world(tmp_path, specs=ALL_PLAYABLE) as (db, manager, _):
        lobby = await _running(manager)
        message = make_message()
        message.edit = AsyncMock(side_effect=RuntimeError("deleted"))
        with patch("yokai.ui.jackpot_views.JACKPOT_REVEAL_DELAY_S", 0):
            with pytest.raises(RuntimeError):
                await run_jackpot(make_bot(db, manager), lobby, message)
        assert (await balance_of(db, 1), await balance_of(db, 2)) == (1000, 1000)
        assert lobby.state is LobbyState.CANCELLED
