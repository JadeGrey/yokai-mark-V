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
        """Credit every non-bot human in a voice channel that has 2 or more non-bot humans.

        The guild's AFK channel is skipped so idle members parked there can't farm Renown.
        A single member's credit failure is caught and logged so it doesn't abort crediting
        the rest of that tick. Blacklisted members still count toward the 2-human minimum but
        are never credited.
        """
        if self.reward <= 0:
            return
        guild = self.bot.get_guild(self.bot.config.guild_id)
        if guild is None:
            return
        afk_channel = guild.afk_channel
        for channel in guild.voice_channels:
            if afk_channel is not None and channel.id == afk_channel.id:
                continue
            humans = [m for m in channel.members if not m.bot]
            if len(humans) < 2:
                continue
            for member in humans:
                if await self.bot.moderation.is_blacklisted(member.id):
                    continue
                try:
                    await self.bot.db.credit_wallet(
                        member.id,
                        self.reward,
                        reason="voice",
                        welcome_bonus=self.bot.config.economy_welcome_bonus,
                    )
                except Exception as exc:
                    logger.error("Voice-earn credit failed for user %d: %s", member.id, exc)
