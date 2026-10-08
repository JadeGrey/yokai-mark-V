"""Crash: a multiplier climbs until it crashes; players cash out before it does."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import TYPE_CHECKING, Optional

import discord

from yokai.errors import YokaiError
from yokai.gambling.engines.crash import (
    MIN_TICK_S,
    CrashRound,
    draw_crash_point,
    next_tick_interval,
)
from yokai.gambling.lobby import Lobby
from yokai.moderation.gate import screen
from yokai.ui.embeds import EmbedFactory, send
from yokai.ui.table_common import avatar_url, finish_table, reply_error

if TYPE_CHECKING:
    from yokai.bot import YokaiBot

logger = logging.getLogger(__name__)

# Never wait less than this: a sleep that shrinks toward the exact crash time can fall below what
# the clock can represent and spin without ever reaching it. Cash-outs are still judged on the
# exact time, so a round only ever ends up to this much late.
MIN_PAUSE_S = 0.01


class CrashView(discord.ui.View):
    """The table message while a round flies: one Cash out button for every seated player."""

    def __init__(self, bot: YokaiBot, lobby: Lobby, crash: CrashRound, started_at: float) -> None:
        super().__init__(timeout=None)
        self.bot = bot
        self.lobby = lobby
        self.crash = crash
        self.started_at = started_at
        self.wake = asyncio.Event()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return not await screen(interaction)

    def rows(self) -> list[tuple[int, int, Optional[int]]]:
        return [
            (user_id, seat.stake, self.crash.cashed_out.get(user_id))
            for user_id, seat in self.lobby.seats.items()
        ]

    @discord.ui.button(label="Cash out", style=discord.ButtonStyle.success)
    async def cash_out_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        # The server's clock at the moment this press is handled decides everything; the
        # number on the message is only a picture that may be a moment behind.
        elapsed = self.bot.tables.clock() - self.started_at
        user_id = interaction.user.id
        try:
            locked = self.crash.cash_out(user_id, elapsed)
        except YokaiError as err:
            await reply_error(self.bot, interaction, err)
            return
        payout = self.lobby.seats[user_id].stake * locked // 100
        embed = EmbedFactory.info(
            title="Cashed out",
            description=(
                f"You locked in **{locked / 100:.2f}x** and will be paid **{payout:,}** Renown "
                "when the round ends."
            ),
            context="Gambling",
            bot_avatar_url=avatar_url(self.bot),
        )
        await send(interaction, embed=embed, ephemeral=True)
        if self.crash.all_cashed_out:
            self.wake.set()


async def _pause(view: CrashView, seconds: float) -> None:
    """Wait up to `seconds`, or until everyone has cashed out. Tests replace this."""
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(view.wake.wait(), timeout=seconds)


async def run_crash(bot: YokaiBot, lobby: Lobby, message: discord.Message) -> None:
    """Run a started crash round. Any failure refunds every stake and tells the players."""
    tables = bot.tables
    crash = CrashRound(
        list(lobby.seats), draw_crash_point(tables.rng_factory(), tables.crash_max_multiplier)
    )
    started_at = tables.clock()
    view = CrashView(bot, lobby, crash, started_at)

    async def play() -> discord.Embed:
        interval = MIN_TICK_S
        pending: Optional[asyncio.Task[None]] = None

        async def refresh() -> None:
            nonlocal interval
            began = time.monotonic()
            elapsed = tables.clock() - started_at
            try:
                await message.edit(
                    embed=EmbedFactory.crash_flying(
                        view.rows(),
                        crash.multiplier_hundredths_at(elapsed),
                        bot_avatar_url=avatar_url(bot),
                    ),
                    view=view,
                )
            except discord.HTTPException as exc:
                logger.warning("Could not update the crash message: %s", exc)
            interval = next_tick_interval(time.monotonic() - began)

        await message.edit(
            embed=EmbedFactory.crash_flying(view.rows(), 100, bot_avatar_url=avatar_url(bot)),
            view=view,
        )
        while True:
            elapsed = tables.clock() - started_at
            if crash.is_over(elapsed):
                break
            if pending is None or pending.done():
                pending = asyncio.create_task(refresh())
            until_crash = max(0.0, crash.crash_time_s - elapsed)
            await _pause(view, max(MIN_PAUSE_S, min(interval, until_crash)))
        view.stop()
        if pending is not None:
            # A slow Discord edit must never delay settlement: drop one that is still in flight
            # (the result message replaces it anyway).
            if not pending.done():
                pending.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pending
        stakes = {user_id: seat.stake for user_id, seat in lobby.seats.items()}
        payouts = crash.payouts(stakes)
        await tables.settle(lobby, payouts)
        return EmbedFactory.crash_result(
            view.rows(), crash.crash_point, payouts, bot_avatar_url=avatar_url(bot)
        )

    await finish_table(bot, lobby, message, play)
