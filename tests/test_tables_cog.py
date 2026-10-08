"""TablesCog: provisioning a table channel and healing state after a restart."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from tests.table_helpers import CHANNEL, announce_ok, world
from yokai.cogs.tables import TablesCog
from yokai.errors import InvalidGameChoiceError, TableBusyError, TablePermissionError
from yokai.gambling.models import MultiplayerChannel
from yokai.ui.table_views import PLAY_CUSTOM_ID, PlayView


def make_bot(db, manager) -> MagicMock:  # type: ignore[no-untyped-def]
    bot = MagicMock()
    bot.user = None
    bot.db = db
    bot.tables = manager
    bot.config.gambling_min_bet = 10
    bot.config.gambling_max_bet = 5000
    bot.add_view = MagicMock()
    return bot


def make_channel(
    channel_id: int = CHANNEL, *, perms: tuple[bool, bool, bool] = (True, True, True)
) -> MagicMock:
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = channel_id
    channel.name = "tables"
    permissions = MagicMock()
    permissions.view_channel, permissions.send_messages, permissions.embed_links = perms
    channel.permissions_for = MagicMock(return_value=permissions)
    channel.guild = MagicMock()
    sent = MagicMock(spec=discord.Message)
    sent.id = 5000 + channel_id
    channel.send = AsyncMock(return_value=sent)
    partial = MagicMock()
    partial.delete = AsyncMock()
    partial.edit = AsyncMock()
    channel.get_partial_message = MagicMock(return_value=partial)
    channel.fetch_message = AsyncMock()
    return channel


@pytest.mark.asyncio
async def test_loading_the_cog_registers_the_persistent_play_view(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, _):
        bot = make_bot(db, manager)
        await TablesCog(bot).cog_load()
        view = bot.add_view.call_args.args[0]
        assert isinstance(view, PlayView) and view.is_persistent()
        assert view.children[0].custom_id == PLAY_CUSTOM_ID  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_setting_a_channel_posts_the_play_embed_and_stores_it(tmp_path: Path) -> None:
    async with world(tmp_path, users=()) as (db, manager, _):
        await db.clear_multiplayer_channel(CHANNEL)
        bot = make_bot(db, manager)
        channel = make_channel()
        row = await TablesCog(bot).set_channel(channel, "duel")
        assert row == MultiplayerChannel(CHANNEL, "duel", 5000 + CHANNEL, None, False)
        kwargs = channel.send.await_args.kwargs
        assert isinstance(kwargs["view"], PlayView)
        assert kwargs["allowed_mentions"].everyone is False
        assert await db.get_multiplayer_channel(CHANNEL) == row


@pytest.mark.asyncio
async def test_a_missing_permission_is_reported_before_anything_is_posted(tmp_path: Path) -> None:
    async with world(tmp_path, users=()) as (db, manager, _):
        bot = make_bot(db, manager)
        channel = make_channel(perms=(True, False, False))
        with pytest.raises(TablePermissionError) as caught:
            await TablesCog(bot).set_channel(channel, "duel")
        assert "Send Messages" in caught.value.message and "Embed Links" in caught.value.message
        channel.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_game_that_is_not_built_yet_cannot_be_dedicated(tmp_path: Path) -> None:
    async with world(tmp_path, users=()) as (db, manager, _):
        bot = make_bot(db, manager)
        channel = make_channel(20)
        for game in ("jackpot", "crash", "bjtable", "lottery"):
            with pytest.raises(InvalidGameChoiceError, match="not open yet"):
                await TablesCog(bot).set_channel(channel, game)
        channel.send.assert_not_awaited()
        assert await db.get_multiplayer_channel(20) is None


@pytest.mark.asyncio
async def test_an_unknown_game_is_refused(tmp_path: Path) -> None:
    async with world(tmp_path, users=()) as (db, manager, _):
        bot = make_bot(db, manager)
        with pytest.raises(InvalidGameChoiceError):
            await TablesCog(bot).set_channel(make_channel(), "nonsense")


@pytest.mark.asyncio
async def test_moving_a_game_deletes_the_old_play_and_table_messages(tmp_path: Path) -> None:
    async with world(tmp_path, users=()) as (db, manager, _):
        await db.set_table_message(CHANNEL, 4242, active=False)
        bot = make_bot(db, manager)
        old_channel = make_channel(CHANNEL)
        bot.get_channel = MagicMock(return_value=old_channel)
        new_channel = make_channel(20)
        await TablesCog(bot).set_channel(new_channel, "duel")
        deleted = [c.args[0] for c in old_channel.get_partial_message.call_args_list]
        assert sorted(deleted) == [1, 4242]
        assert await db.get_multiplayer_channel(CHANNEL) is None
        assert (await db.get_multiplayer_channel(20)) is not None


@pytest.mark.asyncio
async def test_a_live_table_blocks_moving_or_unsetting_its_channel(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, _):
        await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        bot = make_bot(db, manager)
        cog = TablesCog(bot)
        with pytest.raises(TableBusyError):
            await cog.set_channel(make_channel(20), "duel")
        with pytest.raises(TableBusyError):
            await cog.unset_channel(CHANNEL)
        assert await db.get_multiplayer_channel(CHANNEL) is not None


@pytest.mark.asyncio
async def test_unsetting_removes_the_row_and_its_messages(tmp_path: Path) -> None:
    async with world(tmp_path, users=()) as (db, manager, _):
        await db.set_table_message(CHANNEL, 4242, active=False)
        bot = make_bot(db, manager)
        channel = make_channel(CHANNEL)
        bot.get_channel = MagicMock(return_value=channel)
        cog = TablesCog(bot)
        assert await cog.unset_channel(CHANNEL) is True
        assert await cog.unset_channel(CHANNEL) is False
        assert sorted(c.args[0] for c in channel.get_partial_message.call_args_list) == [1, 4242]


@pytest.mark.asyncio
async def test_reconcile_rewrites_an_interrupted_table_but_not_a_finished_one(
    tmp_path: Path,
) -> None:
    async with world(tmp_path, users=()) as (db, manager, _):
        await db.set_table_message(CHANNEL, 4242, active=True)  # interrupted by the restart
        await db.set_multiplayer_channel(30, "jackpot", 2)
        await db.set_table_message(30, 4343, active=False)  # finished normally
        bot = make_bot(db, manager)
        channels = {CHANNEL: make_channel(CHANNEL), 30: make_channel(30)}
        for ch in channels.values():
            ch.fetch_message = AsyncMock()  # play embeds still exist
        bot.get_channel = MagicMock(side_effect=lambda cid: channels.get(cid))
        await TablesCog(bot).reconcile()

        interrupted = channels[CHANNEL].get_partial_message.return_value
        interrupted.edit.assert_awaited_once()
        assert "restart" in interrupted.edit.await_args.kwargs["embed"].description.lower()
        assert interrupted.edit.await_args.kwargs["view"] is None
        finished = channels[30].get_partial_message.return_value
        finished.edit.assert_not_awaited()
        row = await db.get_multiplayer_channel(CHANNEL)
        assert row is not None and row.table_active is False


@pytest.mark.asyncio
async def test_reconcile_reposts_a_deleted_play_embed(tmp_path: Path) -> None:
    async with world(tmp_path, users=()) as (db, manager, _):
        bot = make_bot(db, manager)
        channel = make_channel(CHANNEL)
        response = MagicMock(status=404, reason="Not Found")
        channel.fetch_message = AsyncMock(side_effect=discord.NotFound(response, "gone"))
        bot.get_channel = MagicMock(return_value=channel)
        await TablesCog(bot).reconcile()
        channel.send.assert_awaited_once()
        row = await db.get_multiplayer_channel(CHANNEL)
        assert row is not None and row.play_message_id == 5000 + CHANNEL


@pytest.mark.asyncio
async def test_reconcile_survives_a_channel_it_cannot_find(tmp_path: Path) -> None:
    async with world(tmp_path, users=()) as (db, manager, _):
        bot = make_bot(db, manager)
        bot.get_channel = MagicMock(return_value=None)
        response = MagicMock(status=404, reason="Not Found")
        bot.fetch_channel = AsyncMock(side_effect=discord.NotFound(response, "gone"))
        await TablesCog(bot).reconcile()  # must not raise
        assert await db.get_multiplayer_channel(CHANNEL) is not None
