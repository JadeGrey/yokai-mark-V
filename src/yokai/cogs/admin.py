"""Admin-only slash commands, grouped under /admin."""

from __future__ import annotations

import platform
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import discord
import ytmusicapi
from discord import app_commands
from discord.ext import commands

from yokai.errors import InvalidGameChoiceError, InvalidPaymentError, YokaiError
from yokai.gambling.lobby import GAME_SPECS
from yokai.ui.embeds import EmbedFactory, send
from yokai.ui.views import DiagView

if TYPE_CHECKING:
    from yokai.bot import YokaiBot

GAME_CHOICES = [
    app_commands.Choice(name=spec.label, value=name) for name, spec in GAME_SPECS.items()
]


class AdminCog(commands.Cog, name="Admin"):
    """Tools for the owner and the adminlist, all under the /admin group."""

    admin = app_commands.Group(name="admin", description="Admin-only tools.")

    def __init__(self, bot: YokaiBot) -> None:
        self.bot = bot

    def _avatar_url(self) -> Optional[str]:
        return str(self.bot.user.display_avatar.url) if self.bot.user else None

    async def _send_domain_error(self, interaction: discord.Interaction, err: Exception) -> None:
        embed = EmbedFactory.error(
            message=getattr(err, "message", str(err)),
            title=getattr(err, "title", "That didn't work"),
            user_hint=getattr(err, "user_hint", None),
            bot_avatar_url=self._avatar_url(),
        )
        await send(interaction, embed=embed, ephemeral=True)

    async def _deny_unless_admin(self, interaction: discord.Interaction, hint: str) -> bool:
        """Send the ephemeral 'Admins only' refusal and return True when the caller is no admin."""
        if await self.bot.moderation.is_admin(interaction.user.id):
            return False
        embed = EmbedFactory.error(
            message="Access denied: this command is restricted to Yokai admins.",
            title="Admins only",
            user_hint=hint,
            context="Permissions",
            bot_avatar_url=self._avatar_url(),
            include_quip=False,
        )
        await send(interaction, embed=embed, ephemeral=True)
        return True

    @admin.command(name="economy", description="Grant or deduct a member's Renown.")
    @app_commands.describe(user="Member to adjust", amount="Renown to add (negative to deduct)")
    async def economy(
        self, interaction: discord.Interaction, user: discord.Member, amount: int
    ) -> None:
        """Balance correction, always ephemeral, floored at 0."""
        if await self._deny_unless_admin(
            interaction, "Ask the bot owner if you need a balance corrected."
        ):
            return

        if user.bot:
            await self._send_domain_error(
                interaction,
                InvalidPaymentError("You can only adjust another human member of the server."),
            )
            return

        await interaction.response.defer(ephemeral=True)
        applied, new_balance = await self.bot.db.admin_adjust_wallet(
            user.id, amount, welcome_bonus=self.bot.config.economy_welcome_bonus
        )
        embed = EmbedFactory.economy_admin(
            target_mention=f"<@{user.id}>",
            delta_applied=applied,
            new_balance=new_balance,
            bot_avatar_url=self._avatar_url(),
        )
        await send(interaction, embed=embed, ephemeral=True)

    @admin.command(name="diag", description="Run comprehensive system diagnostics.")
    async def diag(self, interaction: discord.Interaction) -> None:
        """Diagnostic overview and yt-dlp updater."""
        avatar_url = self._avatar_url()

        if await self._deny_unless_admin(interaction, "Ask the bot owner if you need diagnostics."):
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
            ytdlp_age_str += " (outdated)"

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

        needs_attention = (
            getattr(self.bot.resolver, "is_degraded", False) is True
            or self.bot.health.ytdlp_outdated is True
            or self.bot.health.davey_available is False
        )
        embed = EmbedFactory.diag(
            diagnostics=diagnostics,
            recent_errors=recent_errors,
            bot_avatar_url=avatar_url,
            status="warn" if needs_attention else "ok",
        )

        view = DiagView(admin_id=interaction.user.id, bot_avatar_url=avatar_url)
        msg = await send(interaction, embed=embed, view=view, ephemeral=True)
        if isinstance(msg, discord.Message):
            view.message = msg

    @admin.command(
        name="set_multiplayer", description="Make this channel the table channel for a game."
    )
    @app_commands.describe(game="Which game this channel hosts")
    @app_commands.choices(game=GAME_CHOICES)
    async def set_multiplayer(
        self, interaction: discord.Interaction, game: app_commands.Choice[str]
    ) -> None:
        """Dedicate the current channel to a multiplayer game and post its Play embed."""
        if await self._deny_unless_admin(interaction, "Ask the bot owner to set up a table."):
            return
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await self._send_domain_error(
                interaction, InvalidGameChoiceError("Run this in a normal text channel.")
            )
            return
        await interaction.response.defer(ephemeral=True)
        tables = self.bot.get_cog("Tables")
        try:
            await tables.set_channel(channel, game.value)  # type: ignore[union-attr]
        except YokaiError as err:
            await self._send_domain_error(interaction, err)
            return
        embed = EmbedFactory.success(
            title="Table channel set",
            description=(
                f"This channel now hosts **{game.name}** tables. "
                "Members open one with the Play button."
            ),
            context="Admin",
            bot_avatar_url=self._avatar_url(),
        )
        await send(interaction, embed=embed, ephemeral=True)

    @admin.command(
        name="unset_multiplayer", description="Stop using this channel as a table channel."
    )
    async def unset_multiplayer(self, interaction: discord.Interaction) -> None:
        if await self._deny_unless_admin(interaction, "Ask the bot owner to change tables."):
            return
        await interaction.response.defer(ephemeral=True)
        tables = self.bot.get_cog("Tables")
        try:
            removed = await tables.unset_channel(interaction.channel_id)  # type: ignore[union-attr]
        except YokaiError as err:
            await self._send_domain_error(interaction, err)
            return
        if removed:
            embed = EmbedFactory.success(
                title="Table channel removed",
                description="This channel is no longer a table channel.",
                context="Admin",
                bot_avatar_url=self._avatar_url(),
            )
        else:
            embed = EmbedFactory.info(
                title="Nothing to remove",
                description="This is not a table channel.",
                context="Admin",
                bot_avatar_url=self._avatar_url(),
            )
        await send(interaction, embed=embed, ephemeral=True)


async def setup(bot: YokaiBot) -> None:
    """Extension setup entrypoint."""
    await bot.add_cog(AdminCog(bot))
