"""Metadata, telemetry, and initial diagnostic commands."""

from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

from yokai.ui.embeds import EmbedFactory, send

if TYPE_CHECKING:
    from yokai.bot import YokaiBot


class MetaCog(commands.Cog, name="Meta"):
    """System commands for latency checks and operational metrics."""

    def __init__(self, bot: YokaiBot) -> None:
        self.bot = bot

    @app_commands.command(
        name="ping",
        description="Check system latencies (Gateway, REST API, Database).",
    )
    async def ping(self, interaction: discord.Interaction) -> None:
        """Measure gateway latency, REST API response time, and SQLite read/write latency."""
        # Defer immediately since we measure roundtrips
        await interaction.response.defer(thinking=True)

        # 1. Gateway latency
        gw_raw = self.bot.latency
        gw_ms: Optional[float] = None
        if not math.isnan(gw_raw) and not math.isinf(gw_raw):
            gw_ms = gw_raw * 1000.0

        # 2. REST API roundtrip latency
        rest_ms: Optional[float] = None
        try:
            start_rest = time.perf_counter()
            await self.bot.application_info()
            rest_ms = (time.perf_counter() - start_rest) * 1000.0
        except Exception:
            rest_ms = None

        # 3. Database latency
        db_ms: Optional[float] = None
        try:
            db_ms = await self.bot.db.ping()
        except Exception:
            db_ms = None

        avatar_url = str(self.bot.user.display_avatar.url) if self.bot.user else None

        embed = EmbedFactory.ping(
            gateway_ms=gw_ms,
            rest_ms=rest_ms,
            db_ms=db_ms,
            voice_ms=None,
            resolver_ms=None,
            bot_avatar_url=avatar_url,
        )

        await send(interaction, embed=embed)


async def setup(bot: YokaiBot) -> None:
    """Extension setup entrypoint."""
    await bot.add_cog(MetaCog(bot))
