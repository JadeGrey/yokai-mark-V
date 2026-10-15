"""Background loop crediting Renown to users active in voice with another human present."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Optional

from yokai.challenges.models import VoiceMinutes
from yokai.challenges.periods import load_tz
from yokai.rpg.guild import day_key

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
        are never credited. Each such human's minutes are reported to the challenge engine even
        when voice pay is off (reward 0) or the credit fails. The same humans also earn guild
        voice time (extra mission attempts), recorded in one batch at the end of the tick.
        """
        guild = self.bot.get_guild(self.bot.config.guild_id)
        if guild is None:
            return
        afk_channel = guild.afk_channel
        in_company: list[int] = []
        for channel in guild.voice_channels:
            if afk_channel is not None and channel.id == afk_channel.id:
                continue
            humans = [m for m in channel.members if not m.bot]
            if len(humans) < 2:
                continue
            for member in humans:
                if await self.bot.moderation.is_blacklisted(member.id):
                    continue
                if self.reward > 0:
                    try:
                        await self.bot.db.credit_wallet(
                            member.id,
                            self.reward,
                            reason="voice",
                            welcome_bonus=self.bot.config.economy_welcome_bonus,
                        )
                    except Exception as exc:
                        logger.error("Voice-earn credit failed for user %d: %s", member.id, exc)
                self.bot.challenges.emit(VoiceMinutes(member.id, max(1, self.interval_s // 60)))
                in_company.append(member.id)
        await self._record_guild_time(in_company)

    async def _record_guild_time(self, user_ids: list[int]) -> None:
        """Add this tick to each member's guild voice time. Never raises."""
        config = self.bot.config
        runtime = self.bot.rpg
        if not user_ids or runtime is None:
            return
        try:
            if config.rpg_voice_minutes_per_attempt <= 0 or config.rpg_voice_max_bonus <= 0:
                return
            today = day_key(int(time.time()), load_tz(config.rpg_tz))
            await runtime.store.add_voice_time(user_ids, self.interval_s, today)
        except Exception as exc:
            logger.error("Recording guild voice time failed: %s", exc)
