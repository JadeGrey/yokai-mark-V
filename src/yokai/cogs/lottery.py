"""The lottery channel: the always-on panel, the weekly draw and its announcement."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import TYPE_CHECKING, Optional

import discord
from discord.ext import commands

from yokai.gambling.engines.lottery import draw_number
from yokai.gambling.models import LotteryDraw, MultiplayerChannel
from yokai.gambling.rng import new_rng
from yokai.ui.embeds import EmbedFactory
from yokai.ui.lottery_views import BuyView
from yokai.ui.table_common import NO_MENTIONS, avatar_url

if TYPE_CHECKING:
    from yokai.bot import YokaiBot

logger = logging.getLogger(__name__)

CHECK_SECONDS = 60


class LotteryCog(commands.Cog, name="Lottery"):
    """Owns the lottery channel's panel and runs the weekly draw."""

    def __init__(self, bot: YokaiBot) -> None:
        self.bot = bot
        self._task: Optional[asyncio.Task[None]] = None
        # Panel updates read the state and then edit the message; run one at a time so a slow,
        # older edit can never land after (and overwrite) a newer one.
        self._panel_lock = asyncio.Lock()

    async def cog_load(self) -> None:
        self.bot.add_view(BuyView(self.bot))
        self._task = asyncio.create_task(self._loop())

    async def cog_unload(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    # ---- the panel -----------------------------------------------------------------------

    async def _lottery_row(self) -> Optional[MultiplayerChannel]:
        for row in await self.bot.db.list_multiplayer_channels():
            if row.game == "lottery":
                return row
        return None

    async def panel_embed(self) -> discord.Embed:
        config = self.bot.config
        state = await self.bot.db.get_lottery_state()
        sold = await self.bot.db.lottery_tickets_sold()
        last = await self.bot.db.get_last_draw()
        return EmbedFactory.lottery_panel(
            pot=state.pot,
            tickets_sold=sold,
            number_range=config.lottery_number_range,
            price=config.lottery_ticket_price,
            max_tickets=config.lottery_max_tickets_per_user,
            next_draw_ts=state.next_draw_ts,
            last_draw=(last.winning_number, last.winners, last.prize_each) if last else None,
            bot_avatar_url=avatar_url(self.bot),
        )

    async def post_panel(self, channel: discord.TextChannel) -> discord.Message:
        """Post the panel and make sure the draw clock is running."""
        interval_s = self.bot.config.lottery_draw_interval_hours * 3600
        await self.bot.db.ensure_lottery_schedule(int(time.time()), interval_s)
        return await channel.send(
            embed=await self.panel_embed(), view=BuyView(self.bot), allowed_mentions=NO_MENTIONS
        )

    async def update_panel(self) -> None:
        """Refresh the panel in place; a missing channel or message is not an error."""
        async with self._panel_lock:
            row = await self._lottery_row()
            if row is None or row.play_message_id is None:
                return
            channel = self.bot.get_channel(row.channel_id)
            if channel is None:
                return
            await channel.get_partial_message(row.play_message_id).edit(  # type: ignore[union-attr]
                embed=await self.panel_embed()
            )

    # ---- the draw ------------------------------------------------------------------------

    async def announce(self, draw: LotteryDraw) -> None:
        """Announce a draw in the channel (a new message, never deleted) and mark it done."""
        row = await self._lottery_row()
        if row is None:
            return
        channel = self.bot.get_channel(row.channel_id)
        if channel is None:
            return
        state = await self.bot.db.get_lottery_state()
        embed = EmbedFactory.lottery_announcement(
            draw_no=draw.draw_no,
            winning_number=draw.winning_number,
            pot=draw.pot,
            winners=draw.winners,
            prize_each=draw.prize_each,
            next_pot=state.pot,
            next_draw_ts=state.next_draw_ts,
            bot_avatar_url=avatar_url(self.bot),
        )
        await channel.send(embed=embed, allowed_mentions=NO_MENTIONS)  # type: ignore[union-attr]
        await self.bot.db.mark_draw_announced(draw.draw_no)
        try:
            await self.update_panel()
        except discord.HTTPException as exc:
            logger.warning("Could not update the lottery panel after the draw: %s", exc)

    async def _announce_safely(self, draw: LotteryDraw) -> None:
        """Announce a draw; a failure is logged and the draw stays unannounced for the next pass.

        It must not abort the scheduler pass, or one stuck announcement could keep a due draw
        from running.
        """
        try:
            await self.announce(draw)
        except Exception as exc:  # network errors (OSError, timeouts) are not Discord errors
            logger.warning("Could not announce lottery draw #%d yet: %s", draw.draw_no, exc)

    async def tick(self, now: Optional[int] = None) -> None:
        """One scheduler pass: start the clock, retry announcements, draw if due."""
        row = await self._lottery_row()
        if row is None:
            return
        ts = int(time.time()) if now is None else now
        interval_s = self.bot.config.lottery_draw_interval_hours * 3600
        await self.bot.db.ensure_lottery_schedule(ts, interval_s)
        for pending in await self.bot.db.get_unannounced_draws():
            await self._announce_safely(pending)
        draw = await self.bot.db.run_lottery_draw(
            draw_number(self.bot.config.lottery_number_range, new_rng()), interval_s, ts
        )
        if draw is not None:
            await self._announce_safely(draw)

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception as exc:
                # A long-running task must outlive any one failure (a dropped connection raises
                # a plain OSError); log it with its traceback and try again next pass.
                logger.error("The lottery check failed: %s", exc, exc_info=exc)
            await asyncio.sleep(CHECK_SECONDS)


async def setup(bot: YokaiBot) -> None:
    """Extension setup entrypoint."""
    await bot.add_cog(LotteryCog(bot))
