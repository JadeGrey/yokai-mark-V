"""Background loop crediting Renown to users active in voice with another human present."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from yokai.bot import YokaiBot

logger = logging.getLogger(__name__)


class VoiceEarnTracker:
    """Ticks every `interval_s`, crediting `reward` Renown to each human in a 2+-human channel."""

    def __init__(self, bot: YokaiBot, interval_s: int, reward: int) -> None:
        self.bot = bot
        self.interval_s = interval_s
        self.reward = reward
        self._task: Optional[asyncio.Task[None]] = None

    def start(self) -> None:
        """Start the background tick loop, if not already running."""
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """Cancel the background tick loop and wait for it to finish."""
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
            try:
                await self.tick()
            except Exception as exc:
                logger.error("Voice-earn tick failed: %s", exc)

    async def tick(self) -> None:
        """Credit every non-bot human in a voice channel that has 2 or more non-bot humans."""
        guild = self.bot.get_guild(self.bot.config.guild_id)
        if guild is None:
            return
        for channel in guild.voice_channels:
            humans = [m for m in channel.members if not m.bot]
            if len(humans) < 2:
                continue
            for member in humans:
                await self.bot.db.credit_wallet(
                    member.id,
                    self.reward,
                    reason="voice",
                    welcome_bonus=self.bot.config.economy_welcome_bonus,
                )
