"""Metadata, telemetry, analytics, and operational diagnostic commands."""

from __future__ import annotations

import math
import platform
import time
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Optional

import discord
import ytmusicapi
from discord import app_commands
from discord.ext import commands

import yokai
from yokai.ui.embeds import EmbedFactory, send
from yokai.ui.views import DiagView

if TYPE_CHECKING:
    from yokai.bot import YokaiBot


def _format_uptime(seconds: int) -> str:
    """Format total seconds into human-readable uptime string."""
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)


class MetaCog(commands.Cog, name="Meta"):
    """System commands for latency checks, analytics, and diagnostics."""

    def __init__(self, bot: YokaiBot) -> None:
        self.bot = bot

    @app_commands.command(
        name="ping",
        description="Check system latencies (Gateway, REST API, Database, Voice, Resolver).",
    )
    async def ping(self, interaction: discord.Interaction) -> None:
        """Measure gateway latency, REST API response time, SQLite latency, voice, and resolver."""
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

        # 4. Voice latency
        voice_ms: Optional[float] = None
        voice_avg_ms: Optional[float] = None
        guild = interaction.guild
        if guild and guild.voice_client and guild.voice_client.is_connected():
            vc = guild.voice_client
            raw_lat = getattr(vc, "latency", None)
            if raw_lat is not None and not math.isnan(raw_lat) and not math.isinf(raw_lat):
                voice_ms = raw_lat * 1000.0

            raw_avg = getattr(vc, "average_latency", None)
            if raw_avg is not None and not math.isnan(raw_avg) and not math.isinf(raw_avg):
                voice_avg_ms = raw_avg * 1000.0

        # 5. Resolver rolling average latency
        resolver_ms = self.bot.resolver.get_average_latency()

        avatar_url = str(self.bot.user.display_avatar.url) if self.bot.user else None

        embed = EmbedFactory.ping(
            gateway_ms=gw_ms,
            rest_ms=rest_ms,
            db_ms=db_ms,
            voice_ms=voice_ms,
            voice_avg_ms=voice_avg_ms,
            resolver_ms=resolver_ms,
            bot_avatar_url=avatar_url,
        )

        await send(interaction, embed=embed)

    @app_commands.command(
        name="usage",
        description="Display operator and command usage rankings.",
    )
    @app_commands.describe(period="Filter usage activity period")
    async def usage(
        self,
        interaction: discord.Interaction,
        period: Literal["all", "30d", "7d"] = "all",
    ) -> None:
        """Render usage leaderboard of top operators and commands."""
        await interaction.response.defer(thinking=True)

        period_days_map: dict[str, Optional[int]] = {
            "all": None,
            "30d": 30,
            "7d": 7,
        }
        period_label_map: dict[str, str] = {
            "all": "All Time",
            "30d": "Last 30 Days",
            "7d": "Last 7 Days",
        }

        period_days = period_days_map.get(period)
        period_label = period_label_map.get(period, "All Time")

        top_users, top_commands = await self.bot.db.get_usage_stats(period_days=period_days)
        avatar_url = str(self.bot.user.display_avatar.url) if self.bot.user else None

        embed = EmbedFactory.usage(
            top_users=top_users,
            top_commands=top_commands,
            period=period_label,
            bot_avatar_url=avatar_url,
        )

        await send(interaction, embed=embed)

    @app_commands.command(
        name="about",
        description="Display bot information, runtime stack, uptime, and credits.",
    )
    async def about(self, interaction: discord.Interaction) -> None:
        """Display Yokai version, uptime, host environment, and stack versions."""
        await interaction.response.defer(thinking=True)

        uptime_s = int(time.time() - self.bot.start_time)
        uptime_str = _format_uptime(uptime_s)
        host_os = f"{platform.system()} {platform.release()}"

        stack_info = {
            "Python": platform.python_version(),
            "discord.py": discord.__version__,
            "yt-dlp": self.bot.health.ytdlp_version or "Unknown",
            "ytmusicapi": getattr(ytmusicapi, "__version__", "Unknown"),
            "FFmpeg": self.bot.health.ffmpeg_version or "Not installed",
        }

        avatar_url = str(self.bot.user.display_avatar.url) if self.bot.user else None

        embed = EmbedFactory.about(
            version=yokai.__version__,
            uptime_str=uptime_str,
            host_os=host_os,
            stack_info=stack_info,
            owner_id=self.bot.config.owner_id,
            bot_avatar_url=avatar_url,
        )

        await send(interaction, embed=embed)

    @app_commands.command(
        name="diag",
        description="Run comprehensive system diagnostics (Owner only).",
    )
    async def diag(self, interaction: discord.Interaction) -> None:
        """Owner-only diagnostic overview and yt-dlp updater."""
        avatar_url = str(self.bot.user.display_avatar.url) if self.bot.user else None

        # Strict owner authorization check
        if interaction.user.id != self.bot.config.owner_id:
            embed = EmbedFactory.error(
                message="Access denied: this command is restricted to the bot owner.",
                user_hint="Contact the bot operator if you require diagnostics.",
                context="Permissions",
                bot_avatar_url=avatar_url,
                include_quip=False,
            )
            await send(interaction, embed=embed, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        # 1. Inspect DB size
        db_file = Path(self.bot.config.db_path)
        if db_file.exists():
            size_bytes = db_file.stat().st_size
            if size_bytes < 1024 * 1024:
                db_size_str = f"{size_bytes / 1024:.1f} KB"
            else:
                db_size_str = f"{size_bytes / (1024 * 1024):.2f} MB"
        else:
            db_size_str = "File not created"

        # 2. Inspect Spotify orchestrator status
        sp_status = self.bot.spotify.get_provider_status()
        sp_official_str = (
            f"Configured: {sp_status['official_configured']} · "
            f"Circuit: {'Open' if not sp_status['official_circuit_available'] else 'Closed'} · "
            f"Failures: {sp_status['official_failures']}"
        )
        sp_scraper_str = (
            f"Enabled: {sp_status['scraper_enabled']} · "
            f"Circuit: {'Open' if not sp_status['scraper_circuit_available'] else 'Closed'} · "
            f"Failures: {sp_status['scraper_failures']}"
        )

        # 3. Inspect YouTube resolver health
        yt_health_str = (
            f"Degraded ({self.bot.resolver.consecutive_failures} failures)"
            if getattr(self.bot.resolver, "is_degraded", False)
            else "Healthy"
        )

        # 4. Inspect DAVE status
        dave_str = (
            f"Enabled (davey {self.bot.health.davey_version})"
            if self.bot.health.davey_available
            else "Disabled (davey missing)"
        )

        # 5. Inspect yt-dlp age
        ytdlp_age_str = (
            f"{self.bot.health.ytdlp_version} ({self.bot.health.ytdlp_age_days}d old)"
            if self.bot.health.ytdlp_age_days is not None
            else (self.bot.health.ytdlp_version or "Unknown")
        )
        if self.bot.health.ytdlp_outdated:
            ytdlp_age_str += " ⚠️ Outdated"

        diagnostics = {
            "Python / discord.py": f"{platform.python_version()} / {discord.__version__}",
            "DAVE E2EE": dave_str,
            "FFmpeg Path": self.bot.health.ffmpeg_path or "Missing",
            "Deno Path": self.bot.health.deno_path or "Missing",
            "yt-dlp Version": ytdlp_age_str,
            "YouTube Health": yt_health_str,
            "Spotify Official": sp_official_str,
            "Spotify Scraper": sp_scraper_str,
            "ytmusicapi": getattr(ytmusicapi, "__version__", "Unknown"),
            "Database Size": db_size_str,
        }

        # Gather recent errors
        recent_errors: list[str] = list(self.bot.error_ring_buffer)
        for err in getattr(self.bot.resolver, "error_ring_buffer", []):
            if err not in recent_errors:
                recent_errors.append(err)

        embed = EmbedFactory.diag(
            diagnostics=diagnostics,
            recent_errors=recent_errors,
            bot_avatar_url=avatar_url,
        )

        view = DiagView(owner_id=self.bot.config.owner_id, bot_avatar_url=avatar_url)
        await send(interaction, embed=embed, view=view, ephemeral=True)


async def setup(bot: YokaiBot) -> None:
    """Extension setup entrypoint."""
    await bot.add_cog(MetaCog(bot))
