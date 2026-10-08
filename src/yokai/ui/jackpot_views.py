"""The jackpot: show the draw, pick a winner by stake, pay out."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import discord

from yokai.gambling.lobby import Lobby
from yokai.ui.embeds import EmbedFactory
from yokai.ui.table_common import avatar_url, finish_table

if TYPE_CHECKING:
    from yokai.bot import YokaiBot

# A short pause between "drawing" and the result; two edits keep us far from any rate limit.
JACKPOT_REVEAL_DELAY_S = 1.5


async def run_jackpot(bot: YokaiBot, lobby: Lobby, message: discord.Message) -> None:
    """Run a started jackpot. Any failure refunds every stake and tells the players."""
    tables = bot.tables
    entries = [(user_id, seat.stake) for user_id, seat in lobby.seats.items()]

    async def play() -> discord.Embed:
        await message.edit(
            embed=EmbedFactory.jackpot_drawing(
                entries, lobby.pot, tables.jackpot_rake_percent, bot_avatar_url=avatar_url(bot)
            ),
            view=None,
        )
        await asyncio.sleep(JACKPOT_REVEAL_DELAY_S)
        result = await tables.resolve_jackpot(lobby)
        return EmbedFactory.jackpot_result(
            entries, result.winner_id, result.prize, result.rake, bot_avatar_url=avatar_url(bot)
        )

    await finish_table(bot, lobby, message, play)
