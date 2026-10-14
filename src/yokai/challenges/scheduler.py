"""Background upkeep for challenges: period rollover, purging and batched notices."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Optional

import discord

from yokai.challenges import periods
from yokai.challenges.models import Completion
from yokai.textutil import escape_md
from yokai.ui.embeds import EmbedFactory

if TYPE_CHECKING:
    from yokai.bot import YokaiBot

logger = logging.getLogger(__name__)

_PURGE_EVERY_S = 3600.0


class ChallengeScheduler:
    """Every `interval_s`: make sure slates exist, purge old rows hourly, flush notices."""

    def __init__(self, bot: YokaiBot, *, interval_s: float = 10.0, max_lines: int = 10) -> None:
        self.bot = bot
        self.interval_s = interval_s
        self.max_lines = max_lines
        self._task: Optional[asyncio.Task[None]] = None
        self._last_purge = 0.0

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self.interval_s)
            await self._safe_tick()

    async def _safe_tick(self) -> None:
        try:
            await self.tick()
        except Exception:
            logger.exception("Challenge scheduler tick failed")

    async def tick(self) -> None:
        """One pass: slates (creates them after a rollover), purge, notices."""
        engine = self.bot.challenges
        if not engine.enabled:
            return
        for period in periods.PERIODS:
            await engine.active_slate(period)
        now = time.monotonic()
        if now - self._last_purge >= _PURGE_EVERY_S:
            self._last_purge = now
            await engine.purge_old()
        await self.flush_notices()

    async def flush_notices(self) -> None:
        """Send one embed per channel for everything completed since the last flush."""
        completions = self.bot.challenges.drain_completions()
        by_channel: dict[int, list[Completion]] = {}
        for completion in completions:
            if completion.channel_id is not None:
                by_channel.setdefault(completion.channel_id, []).append(completion)
        avatar = str(self.bot.user.display_avatar.url) if self.bot.user else None
        for channel_id, items in by_channel.items():
            channel = self.bot.get_channel(channel_id)
            if not isinstance(channel, discord.abc.Messageable):
                continue
            lines = [
                f"<@{c.user_id}> finished **{escape_md(c.title)}** · +{c.reward:,} Renown"
                for c in items[: self.max_lines]
            ]
            embed = EmbedFactory.challenge_notice(
                lines=lines, extra=max(0, len(items) - self.max_lines), bot_avatar_url=avatar
            )
            try:
                await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException as exc:
                logger.debug("Could not send challenge notice to %d: %s", channel_id, exc)
