"""Play button, stake form and lobby view, against a real database and mocked Discord."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from tests.moderation_helpers import fake_moderation
from tests.table_helpers import CHANNEL, announce_ok, balance_of, world
from yokai.errors import InvalidGameChoiceError
from yokai.gambling.lobby import LobbyState
from yokai.ui.table_common import parse_stake
from yokai.ui.table_views import (
    GAME_RUNNERS,
    PLAY_CUSTOM_ID,
    LobbyView,
    PlayView,
    StakeModal,
    handle_play,
)


def make_bot(db, manager) -> MagicMock:  # type: ignore[no-untyped-def]
    bot = MagicMock()
    bot.user = None
    bot.db = db
    bot.tables = manager
    bot.config.economy_welcome_bonus = 0
    bot.config.gambling_min_bet = 10
    bot.moderation = fake_moderation(owner_id=999, admins={555})
    return bot


def make_interaction(user_id: int, channel_id: int = CHANNEL) -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock(spec=discord.Member)
    interaction.user.id = user_id
    interaction.channel_id = channel_id
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.response.send_modal = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    interaction.delete_original_response = AsyncMock()
    return interaction


def make_view(bot, lobby) -> LobbyView:  # type: ignore[no-untyped-def]
    view = LobbyView(bot, lobby)
    view.message = MagicMock()
    view.message.edit = AsyncMock()
    return view


def error_embed_of(mock_send: AsyncMock):  # type: ignore[no-untyped-def]
    return mock_send.await_args.kwargs["embed"]


# ---- parsing ----------------------------------------------------------------------------


@pytest.mark.parametrize("text,expected", [("100", 100), (" 1,000 ", 1000), ("25 ", 25)])
def test_parse_stake_accepts_whole_numbers(text: str, expected: int) -> None:
    assert parse_stake(text) == expected


@pytest.mark.parametrize("text", ["", "abc", "10.5", "-", "1e3"])
def test_parse_stake_rejects_anything_else(text: str) -> None:
    with pytest.raises(InvalidGameChoiceError):
        parse_stake(text)


# ---- the persistent Play button ---------------------------------------------------------


def test_the_play_view_is_persistent_with_a_fixed_custom_id() -> None:
    view = PlayView(MagicMock())
    assert view.timeout is None and view.is_persistent()
    assert [item.custom_id for item in view.children] == [PLAY_CUSTOM_ID]  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_pressing_play_in_an_unconfigured_channel_is_refused_ephemerally(
    tmp_path: Path,
) -> None:
    async with world(tmp_path) as (db, manager, _):
        bot = make_bot(db, manager)
        interaction = make_interaction(1, channel_id=12345)
        with patch("yokai.ui.table_common.send", new=AsyncMock()) as sent:
            await handle_play(bot, interaction)
        interaction.response.send_modal.assert_not_awaited()
        assert sent.await_args.kwargs["ephemeral"] is True
        assert "not set up" in error_embed_of(sent).description


@pytest.mark.asyncio
async def test_pressing_play_while_a_table_is_live_is_refused(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, _):
        await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        bot = make_bot(db, manager)
        interaction = make_interaction(2)
        with patch("yokai.ui.table_common.send", new=AsyncMock()) as sent:
            await handle_play(bot, interaction)
        interaction.response.send_modal.assert_not_awaited()
        assert "already open" in error_embed_of(sent).description
        assert await balance_of(db, 2) == 1000


@pytest.mark.asyncio
async def test_pressing_play_with_too_little_renown_is_refused(tmp_path: Path) -> None:
    async with world(tmp_path, balance=5) as (db, manager, _):
        bot = make_bot(db, manager)
        interaction = make_interaction(1)
        with patch("yokai.ui.table_common.send", new=AsyncMock()) as sent:
            await handle_play(bot, interaction)
        interaction.response.send_modal.assert_not_awaited()
        assert "isn't enough" in error_embed_of(sent).description


@pytest.mark.asyncio
async def test_pressing_play_opens_the_stake_form(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, _):
        bot = make_bot(db, manager)
        interaction = make_interaction(1)
        await handle_play(bot, interaction)
        interaction.response.send_modal.assert_awaited_once()
        assert isinstance(interaction.response.send_modal.await_args.args[0], StakeModal)


@pytest.mark.asyncio
async def test_submitting_a_stake_opens_the_table_and_clears_the_old_message(
    tmp_path: Path,
) -> None:
    async with world(tmp_path) as (db, manager, active):
        await db.set_table_message(CHANNEL, 4242, active=False)
        bot = make_bot(db, manager)
        interaction = make_interaction(1)
        channel = MagicMock()
        sent_message = MagicMock(spec=discord.Message)
        sent_message.id = 9001
        channel.send = AsyncMock(return_value=sent_message)
        channel.get_partial_message = MagicMock()
        old = MagicMock()
        old.delete = AsyncMock()
        channel.get_partial_message.return_value = old
        interaction.channel = channel

        await handle_play(bot, interaction)
        modal = interaction.response.send_modal.await_args.args[0]
        modal.stake._value = "150"  # type: ignore[attr-defined]
        submit = make_interaction(1)
        submit.channel = channel
        with patch.object(LobbyView, "start") as start_window:
            await modal.on_submit(submit)
        start_window.assert_called_once()

        channel.get_partial_message.assert_called_once_with(4242)
        old.delete.assert_awaited_once()
        lobby = manager.lobby_for(CHANNEL)
        assert lobby is not None and lobby.message_id == 9001
        assert await balance_of(db, 1) == 850 and active.is_active(1)
        row = await db.get_multiplayer_channel(CHANNEL)
        assert row is not None and (row.table_message_id, row.table_active) == (9001, True)
        submit.delete_original_response.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_bad_stake_is_an_ephemeral_error_and_costs_nothing(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, _):
        bot = make_bot(db, manager)
        interaction = make_interaction(1)
        await handle_play(bot, interaction)
        modal = interaction.response.send_modal.await_args.args[0]
        modal.stake._value = "lots"  # type: ignore[attr-defined]
        submit = make_interaction(1)
        with patch("yokai.ui.table_common.send", new=AsyncMock()) as sent:
            await modal.on_submit(submit)
        assert sent.await_args.kwargs["ephemeral"] is True
        assert manager.lobby_for(CHANNEL) is None and await balance_of(db, 1) == 1000


# ---- the lobby --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_duel_lobby_has_no_start_button(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, _):
        lobby = await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        view = make_view(make_bot(db, manager), lobby)
        labels = [getattr(item, "label", None) for item in view.children]
        assert "Start" not in labels and {"Join", "Leave", "Cancel"} <= set(labels)


@pytest.mark.asyncio
async def test_joining_a_duel_fills_the_table_and_starts_the_game(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, _):
        lobby = await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        bot = make_bot(db, manager)
        view = make_view(bot, lobby)
        runner = AsyncMock()
        with patch.dict(GAME_RUNNERS, {"duel": runner}):
            await view.join_button.callback(make_interaction(2))
        assert lobby.state is LobbyState.RUNNING and lobby.is_full
        runner.assert_awaited_once()
        assert await balance_of(db, 2) == 900


@pytest.mark.asyncio
async def test_joining_with_too_little_renown_replies_with_an_error_and_keeps_the_table_open(
    tmp_path: Path,
) -> None:
    async with world(tmp_path) as (db, manager, _):
        await db.ensure_wallet(4, 30)
        lobby = await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        view = make_view(make_bot(db, manager), lobby)
        with patch("yokai.ui.table_common.send", new=AsyncMock()) as sent:
            await view.join_button.callback(make_interaction(4))
        assert sent.await_args.kwargs["ephemeral"] is True
        assert lobby.state is LobbyState.OPEN and 4 not in lobby.seats


@pytest.mark.asyncio
async def test_only_the_host_or_an_admin_can_cancel(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, _):
        lobby = await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        view = make_view(make_bot(db, manager), lobby)
        with patch("yokai.ui.table_common.send", new=AsyncMock()) as sent:
            await view.cancel_button.callback(make_interaction(3))
        assert "Only the host" in error_embed_of(sent).description
        assert lobby.state is LobbyState.OPEN

        await view.cancel_button.callback(make_interaction(555))  # an admin
        assert lobby.state is LobbyState.CANCELLED and await balance_of(db, 1) == 1000
        view.message.edit.assert_awaited()


@pytest.mark.asyncio
async def test_the_window_ending_below_the_minimum_cancels_and_refunds(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, active):
        lobby = await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        view = make_view(make_bot(db, manager), lobby)
        await view._begin()
        assert lobby.state is LobbyState.CANCELLED
        assert await balance_of(db, 1) == 1000 and not active.is_active(1)
        embed = view.message.edit.await_args.kwargs["embed"]
        assert "cancelled" in embed.title.lower()


@pytest.mark.asyncio
async def test_leaving_removes_the_guest_and_refunds(tmp_path: Path) -> None:
    from tests.test_table_manager import TRIO

    async with world(tmp_path, specs={"duel": TRIO}) as (db, manager, _):
        lobby = await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        await manager.join(lobby, 2, 100)
        view = make_view(make_bot(db, manager), lobby)
        await view.leave_button.callback(make_interaction(2))
        assert 2 not in lobby.seats and await balance_of(db, 2) == 1000


@pytest.mark.asyncio
async def test_a_variable_stake_table_shows_a_start_button_and_the_host_can_use_it(
    tmp_path: Path,
) -> None:
    from tests.test_table_manager import TRIO

    async with world(tmp_path, specs={"duel": TRIO}) as (db, manager, _):
        lobby = await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        await manager.join(lobby, 2, 100)
        view = make_view(make_bot(db, manager), lobby)
        assert "Start" in [getattr(item, "label", None) for item in view.children]
        runner = AsyncMock()
        with patch.dict(GAME_RUNNERS, {"duel": runner}):
            await view.start_button.callback(make_interaction(1))
        assert lobby.state is LobbyState.RUNNING
        runner.assert_awaited_once()
