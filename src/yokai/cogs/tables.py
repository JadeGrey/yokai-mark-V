"""Table channels: the persistent Play embed, provisioning and restart healing."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import discord
from discord.ext import commands

from yokai.errors import InvalidGameChoiceError, TableBusyError, TablePermissionError
from yokai.gambling.models import MultiplayerChannel
from yokai.ui.embeds import EmbedFactory
from yokai.ui.table_common import NO_MENTIONS, avatar_url, delete_message_quietly
from yokai.ui.table_views import PlayView

if TYPE_CHECKING:
    from yokai.bot import YokaiBot

logger = logging.getLogger(__name__)


class TablesCog(commands.Cog, name="Tables"):
    """Owns the channels dedicated to multiplayer games."""

    def __init__(self, bot: YokaiBot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        # One persistent view answers every Play button, in every channel, across restarts.
        self.bot.add_view(PlayView(self.bot))
        # When any table ends, put a fresh Play embed under its result.
        self.bot.tables.on_closed = self.repost_play_message

    def _play_embed(self, game: str) -> discord.Embed:
        spec = self.bot.tables.spec(game)
        return EmbedFactory.table_play(
            game_label=spec.label,
            rules=spec.rules,
            min_bet=self.bot.config.gambling_min_bet,
            max_bet=self.bot.config.gambling_max_bet,
            bot_avatar_url=avatar_url(self.bot),
        )

    async def _post_play_message(self, channel: Any, game: str) -> discord.Message:
        return await channel.send(
            embed=self._play_embed(game), view=PlayView(self.bot), allowed_mentions=NO_MENTIONS
        )

    async def _delete_messages_of(self, row: MultiplayerChannel) -> None:
        channel = self.bot.get_channel(row.channel_id)
        if channel is None:
            return
        for message_id in (row.play_message_id, row.table_message_id):
            if message_id:
                await delete_message_quietly(channel, message_id)

    async def repost_play_message(self, channel_id: int) -> None:
        """Post a fresh Play embed at the bottom of the channel, under the table that just ended.

        The new embed goes up before the old one comes down, so the channel is never left
        without a Play button.
        """
        row = await self.bot.db.get_multiplayer_channel(channel_id)
        channel = self.bot.get_channel(channel_id)
        if row is None or not isinstance(channel, discord.TextChannel):
            return
        try:
            message = await self._post_play_message(channel, row.game)
        except discord.HTTPException as exc:
            logger.warning("Could not repost the Play embed in %d: %s", channel_id, exc)
            return
        await self.bot.db.set_play_message(channel_id, message.id)
        if row.play_message_id:
            await delete_message_quietly(channel, row.play_message_id)

    # ---- provisioning --------------------------------------------------------------------

    async def set_channel(self, channel: discord.TextChannel, game: str) -> MultiplayerChannel:
        """Dedicate `channel` to `game` and post its Play embed."""
        spec = self.bot.tables.spec(game)  # unknown games fail here
        if not spec.available:
            err = InvalidGameChoiceError(f"{spec.label} tables are not open yet.")
            err.user_hint = "Duel is available now; the other games arrive in later updates."
            raise err
        permissions = channel.permissions_for(channel.guild.me)
        missing = [
            name
            for name, granted in (
                ("View Channel", permissions.view_channel),
                ("Send Messages", permissions.send_messages),
                ("Embed Links", permissions.embed_links),
            )
            if not granted
        ]
        if missing:
            raise TablePermissionError(missing, channel.name)
        for row in await self.bot.db.list_multiplayer_channels():
            if (row.game == game or row.channel_id == channel.id) and self.bot.tables.is_busy(
                row.channel_id
            ):
                raise TableBusyError()
        message = await self._post_play_message(channel, game)
        try:
            removed = await self.bot.db.set_multiplayer_channel(channel.id, game, message.id)
        except BaseException:
            await delete_message_quietly(channel, message.id)
            raise
        for old in removed:
            await self._delete_messages_of(old)
        return MultiplayerChannel(channel.id, game, message.id, None, False)

    async def unset_channel(self, channel_id: int) -> bool:
        """Stop dedicating a channel; returns False when it was not one."""
        if self.bot.tables.is_busy(channel_id):
            raise TableBusyError()
        row = await self.bot.db.clear_multiplayer_channel(channel_id)
        if row is None:
            return False
        await self._delete_messages_of(row)
        return True

    # ---- restart healing -----------------------------------------------------------------

    async def reconcile(self) -> None:
        """After a restart: close interrupted tables and restore missing Play embeds."""
        for row in await self.bot.db.list_multiplayer_channels():
            channel = self.bot.get_channel(row.channel_id)
            if channel is None:
                try:
                    channel = await self.bot.fetch_channel(row.channel_id)
                except discord.HTTPException as exc:
                    logger.warning("Table channel %d is unavailable: %s", row.channel_id, exc)
                    continue
            if not isinstance(channel, discord.TextChannel):
                logger.warning("Table channel %d is not a text channel.", row.channel_id)
                continue
            if row.table_active:
                await self._close_interrupted_table(channel, row)
                await self.repost_play_message(row.channel_id)  # a fresh Play under the notice
                continue
            await self._ensure_play_message(channel, row)

    async def _close_interrupted_table(
        self, channel: discord.TextChannel, row: MultiplayerChannel
    ) -> None:
        """A table still marked active was cut off by the restart; its stakes were refunded."""
        if row.table_message_id:
            notice = EmbedFactory.table_notice(
                "Table closed",
                "The bot restarted while this table was open. Every stake was refunded.",
                bot_avatar_url=avatar_url(self.bot),
            )
            try:
                await channel.get_partial_message(row.table_message_id).edit(
                    embed=notice, view=None
                )
            except discord.HTTPException as exc:
                logger.warning("Could not close stale table in %d: %s", row.channel_id, exc)
        await self.bot.db.set_table_inactive(row.channel_id)

    async def _ensure_play_message(
        self, channel: discord.TextChannel, row: MultiplayerChannel
    ) -> None:
        if row.play_message_id:
            try:
                await channel.fetch_message(row.play_message_id)
                return
            except discord.NotFound:
                pass
            except discord.HTTPException as exc:
                logger.warning("Could not check the Play embed in %d: %s", row.channel_id, exc)
                return
        try:
            message = await self._post_play_message(channel, row.game)
        except discord.HTTPException as exc:
            logger.warning("Could not repost the Play embed in %d: %s", row.channel_id, exc)
            return
        await self.bot.db.set_play_message(row.channel_id, message.id)


async def setup(bot: YokaiBot) -> None:
    """Extension setup entrypoint."""
    await bot.add_cog(TablesCog(bot))
