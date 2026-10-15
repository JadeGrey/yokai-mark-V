"""The /trade command: open a two-sided trade with another adventurer."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

from yokai.errors import (
    NoCharacterError,
    TradeBotError,
    TradeBusyError,
    TradeFightingError,
    TradeSelfError,
    YokaiError,
)
from yokai.rpg.runtime import RpgRuntime
from yokai.rpg.trade import TradeSession
from yokai.ui.embeds import EmbedFactory, send
from yokai.ui.trade_views import TradeView

if TYPE_CHECKING:
    from yokai.bot import YokaiBot

logger = logging.getLogger(__name__)


def _display_name(user: discord.abc.User) -> str:
    name = getattr(user, "display_name", None) or getattr(user, "name", None)
    return name if isinstance(name, str) and name.strip() else f"Adventurer {user.id % 10000}"


class TradeCog(commands.Cog, name="Trade"):
    """Trade gear and Renown with another adventurer."""

    def __init__(self, bot: YokaiBot) -> None:
        self.bot = bot

    def _runtime(self) -> RpgRuntime:
        runtime = self.bot.rpg
        if runtime is None:
            raise RuntimeError("The Trade cog was loaded without an RPG runtime.")
        return runtime

    async def _send_domain_error(self, interaction: discord.Interaction, err: Exception) -> None:
        embed = EmbedFactory.error(
            message=getattr(err, "message", str(err)),
            title=getattr(err, "title", "That didn't work"),
            user_hint=getattr(err, "user_hint", None),
            context="Guild",
            bot_avatar_url=str(self.bot.user.display_avatar.url) if self.bot.user else None,
        )
        await send(interaction, embed=embed, ephemeral=True)

    @app_commands.command(name="trade", description="Trade gear or Renown with another adventurer.")
    @app_commands.describe(member="Who to trade with")
    async def trade(self, interaction: discord.Interaction, member: discord.Member) -> None:
        """Everything that can be refused is checked before posting, so refusals are private."""
        runtime = self._runtime()
        me = interaction.user
        try:
            if member.bot:
                raise TradeBotError()
            if member.id == me.id:
                raise TradeSelfError()
            if await runtime.store.get_character(me.id) is None:
                raise NoCharacterError(own=True)
            if await runtime.store.get_character(member.id) is None:
                raise NoCharacterError(own=False)
            for user_id in (me.id, member.id):
                if user_id in runtime.active_users:
                    raise TradeFightingError(user_id, you=user_id == me.id)
            if me.id in runtime.trading:
                raise TradeBusyError(you=True)
            if member.id in runtime.trading:
                raise TradeBusyError(you=False)
        except YokaiError as err:
            await self._send_domain_error(interaction, err)
            return

        session = TradeSession(me.id, member.id)
        runtime.trading[me.id] = session
        runtime.trading[member.id] = session
        names = {me.id: _display_name(me), member.id: _display_name(member)}
        view = TradeView(self.bot, runtime, session, names)
        message: Optional[discord.Message] = await send(
            interaction, embed=await view.build_embed(), view=view
        )
        if message is None:
            view.release()
            return
        view.message = message


async def setup(bot: YokaiBot) -> None:
    await bot.add_cog(TradeCog(bot))
