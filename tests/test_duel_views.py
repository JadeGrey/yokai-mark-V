"""The duel picking phase and its resolution."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from tests.moderation_helpers import fake_moderation
from tests.table_helpers import CHANNEL, announce_ok, balance_of, world
from yokai.gambling.engines.duel import DuelRound
from yokai.gambling.lobby import LobbyState
from yokai.ui.duel_views import DuelView, PickSelect, run_duel


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


def make_interaction(user_id: int) -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock(spec=discord.Member)
    interaction.user.id = user_id
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.edit_message = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


async def _running(manager):  # type: ignore[no-untyped-def]
    lobby = await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
    await manager.join(lobby, 2, None)
    await manager.begin(lobby)
    return lobby


@pytest.mark.asyncio
async def test_a_stranger_cannot_open_the_pick_prompt(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, _):
        lobby = await _running(manager)
        view = DuelView(make_bot(db, manager), lobby, DuelRound([1, 2], 5), deadline_ts=1)
        view.message = make_message()
        with patch("yokai.ui.table_common.send", new=AsyncMock()) as sent:
            await view.pick_button.callback(make_interaction(3))
        assert sent.await_args.kwargs["ephemeral"] is True
        assert "not seated" in sent.await_args.kwargs["embed"].description


@pytest.mark.asyncio
async def test_a_seated_player_gets_a_private_prompt(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, _):
        lobby = await _running(manager)
        view = DuelView(make_bot(db, manager), lobby, DuelRound([1, 2], 5), deadline_ts=1)
        view.message = make_message()
        interaction = make_interaction(1)
        with patch("yokai.ui.duel_views.send", new=AsyncMock()) as sent:
            await view.pick_button.callback(interaction)
        assert sent.await_args.kwargs["ephemeral"] is True
        assert sent.await_args.kwargs["view"] is not None


@pytest.mark.asyncio
async def test_choosing_a_number_locks_it_in_and_updates_the_table(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, _):
        lobby = await _running(manager)
        duel = DuelRound([1, 2], 5)
        view = DuelView(make_bot(db, manager), lobby, duel, deadline_ts=1)
        view.message = make_message()
        select = PickSelect(view)
        select._values = ["7"]  # type: ignore[attr-defined]
        interaction = make_interaction(1)
        await select.callback(interaction)
        assert duel.has_picked(1) and not view.all_picked.is_set()
        interaction.response.edit_message.assert_awaited_once()
        view.message.edit.assert_awaited()  # "locked in" shown on the table, the number is not


@pytest.mark.asyncio
async def test_the_second_pick_wakes_the_runner(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, _):
        lobby = await _running(manager)
        duel = DuelRound([1, 2], 5)
        view = DuelView(make_bot(db, manager), lobby, duel, deadline_ts=1)
        view.message = make_message()
        duel.submit(1, 3)
        select = PickSelect(view)
        select._values = ["8"]  # type: ignore[attr-defined]
        await select.callback(make_interaction(2))
        assert view.all_picked.is_set()


@pytest.mark.asyncio
async def test_run_duel_resolves_when_both_players_pick(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, active):
        manager.rng_factory = lambda: __import__("random").Random(1)
        lobby = await _running(manager)
        message = make_message()
        bot = make_bot(db, manager)

        original_init = DuelView.__init__

        def init(self, *a, **kw):  # type: ignore[no-untyped-def]
            original_init(self, *a, **kw)
            self.duel.submit(1, 5)
            self.duel.submit(2, 5)  # same pick: always a tie
            self.all_picked.set()

        with patch.object(DuelView, "__init__", init):
            await run_duel(bot, lobby, message)

        assert lobby.state is LobbyState.SETTLED
        assert (await balance_of(db, 1), await balance_of(db, 2)) == (1000, 1000)
        final = message.edit.await_args.kwargs
        assert "tie" in final["embed"].description.lower() and final["view"] is None
        assert not active.is_active(1)


@pytest.mark.asyncio
async def test_run_duel_fills_missing_picks_when_time_runs_out(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, _):
        manager.pick_seconds = 0.05  # type: ignore[assignment]
        lobby = await _running(manager)
        message = make_message()
        await run_duel(make_bot(db, manager), lobby, message)
        assert lobby.state is LobbyState.SETTLED
        assert await balance_of(db, 1) + await balance_of(db, 2) in (2000, 2000 - 4)


@pytest.mark.asyncio
async def test_run_duel_refunds_everyone_if_something_breaks(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, active):
        lobby = await _running(manager)
        message = make_message()
        message.edit = AsyncMock(side_effect=RuntimeError("discord broke"))
        with pytest.raises(RuntimeError):
            await run_duel(make_bot(db, manager), lobby, message)
        assert lobby.state is LobbyState.CANCELLED
        assert (await balance_of(db, 1), await balance_of(db, 2)) == (1000, 1000)
        assert not active.is_active(1) and not active.is_active(2)
