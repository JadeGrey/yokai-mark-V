"""Yokai Bot subclass and centralized lifecycle management."""

from __future__ import annotations

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from yokai.config import Config
from yokai.errors import YokaiError
from yokai.health import HealthReport, check_health
from yokai.presence import PresenceManager, get_default_activity
from yokai.storage.db import Database
from yokai.ui.embeds import EmbedFactory, send

logger = logging.getLogger(__name__)


class YokaiBot(commands.Bot):
    """Core Discord bot class for Yokai."""

    def __init__(self, config: Config, health_report: Optional[HealthReport] = None) -> None:
        self.config = config
        self.health = health_report or check_health(config.ffmpeg_path, config.deno_path)
        self.db = Database(config.db_path)
        self.presence = PresenceManager(self)

        # Standard default intents with zero privileged intents
        intents = discord.Intents.default()
        intents.message_content = False
        intents.members = False
        intents.presences = False

        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            allowed_mentions=discord.AllowedMentions.none(),
            status=discord.Status.idle,
            activity=get_default_activity(),
        )

        # Hook app command tree error handler
        self.tree.on_error = self.on_tree_error

    async def setup_hook(self) -> None:
        """Initialize database, load cogs, and synchronize slash commands to single guild."""
        logger.info("Initializing SQLite storage at %s...", self.config.db_path)
        await self.db.initialize()

        # Load extension cogs
        logger.info("Loading extensions...")
        await self.load_extension("yokai.cogs.meta")

        # Sync commands directly to the single target guild for instant availability
        guild_obj = discord.Object(id=self.config.guild_id)
        self.tree.copy_global_to(guild=guild_obj)
        try:
            synced = await self.tree.sync(guild=guild_obj)
            logger.info("Synced %d slash command(s) to guild %d", len(synced), self.config.guild_id)
        except Exception as exc:
            logger.error("Failed to sync commands to guild %d: %s", self.config.guild_id, exc)

    async def on_ready(self) -> None:
        """Log startup status once connected to Discord gateway."""
        bot_user = self.user
        user_str = f"{bot_user.name}#{bot_user.discriminator}" if bot_user else "Yokai"
        logger.info("Yokai online as %s (Guild ID: %d)", user_str, self.config.guild_id)
        logger.info("Initial presence confirmed: IDLE, Watching for plant")

    async def on_app_command_completion(
        self,
        interaction: discord.Interaction,
        command: app_commands.Command | app_commands.ContextMenu,
    ) -> None:
        """Central audit hook recording successful slash commands to command_log."""
        try:
            cmd_name = command.qualified_name
            await self.db.log_command(interaction.user.id, cmd_name)
            logger.debug("Logged command /%s by user %d", cmd_name, interaction.user.id)
        except Exception as exc:
            logger.error("Failed to log command execution in database: %s", exc)

    async def on_tree_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        """Central app command error handler with typed error mapping and ephemeral output."""
        # Unwrap nested command errors
        cause: Exception = error
        if isinstance(error, app_commands.CommandInvokeError) and error.original:
            cause = error.original

        # Log command attempt even on failure
        cmd_name = interaction.command.qualified_name if interaction.command else "unknown"
        try:
            await self.db.log_command(interaction.user.id, cmd_name)
        except Exception as db_exc:
            logger.error("Failed to record failed command to db: %s", db_exc)

        avatar_url = str(self.user.display_avatar.url) if self.user else None

        if isinstance(cause, YokaiError):
            logger.warning("Domain error executing /%s: %s", cmd_name, cause)
            embed = EmbedFactory.error(
                message=cause.message,
                user_hint=cause.user_hint,
                bot_avatar_url=avatar_url,
            )
        else:
            logger.error("Unexpected error executing /%s: %s", cmd_name, cause, exc_info=cause)
            embed = EmbedFactory.error(
                message="An unexpected system error occurred while processing this command.",
                user_hint="Check /diag or contact the bot owner if the issue persists.",
                bot_avatar_url=avatar_url,
            )

        await send(interaction, embed=embed, ephemeral=True)

    async def close(self) -> None:
        """Clean up database connections and close bot session."""
        logger.info("Shutting down Yokai...")
        try:
            await self.db.close()
        except Exception as exc:
            logger.error("Error closing database: %s", exc)
        await super().close()
