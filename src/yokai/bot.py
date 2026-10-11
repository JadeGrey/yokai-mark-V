"""Yokai Bot subclass and centralized lifecycle management."""

from __future__ import annotations

import asyncio
import collections
import logging
import time
from typing import Optional

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands

from yokai import __version__
from yokai.cogs.music import MUSIC_COMMAND_NAMES
from yokai.config import Config
from yokai.economy.ratelimit import CommandRewardLimiter
from yokai.economy.voice_tracker import VoiceEarnTracker
from yokai.errors import YokaiError
from yokai.gambling.sessions import ActiveGames
from yokai.gambling.tables import TableManager
from yokai.health import HealthReport, check_health
from yokai.heartbeat import HeartbeatWriter
from yokai.looplag import LoopLagMonitor
from yokai.moderation import ModerationStore, UserNameCache, YokaiTree
from yokai.music.buffer import AudioBuffer
from yokai.music.player import GuildPlayer
from yokai.music.recommend import YTMusicRadioRecommender
from yokai.music.resolvers.matcher import TrackMatcher
from yokai.music.resolvers.ytdlp import YtDlpResolver
from yokai.music.search import YtMusicSearcher
from yokai.music.spotify import (
    OfficialSpotifyProvider,
    ScraperSpotifyProvider,
    SpotifyOrchestrator,
)
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
        self.moderation = ModerationStore(self.db, config.owner_id)
        self.names = UserNameCache(self.db)
        self.heartbeat = HeartbeatWriter(self.db, __version__)
        self.loop_lag = LoopLagMonitor()
        self.presence = PresenceManager(self)
        self.searcher = YtMusicSearcher(max_track_seconds=config.max_track_seconds)
        self.resolver = YtDlpResolver(
            deno_path=config.deno_path,
            cookies_file=config.ytdlp_cookies_file,
            max_track_seconds=config.max_track_seconds,
            searcher=self.searcher,
        )
        self.audio_buffer = AudioBuffer(
            directory=config.audio_buffer_dir,
            max_bytes=config.audio_buffer_mb * 1024 * 1024,
            ffmpeg=str(config.ffmpeg_path)
            if config.ffmpeg_path
            else (self.health.ffmpeg_path or "ffmpeg"),
        )
        self._warmup_task: Optional[asyncio.Task[None]] = None

        official_sp = None
        if config.spotify_client_id and config.spotify_client_secret:
            official_sp = OfficialSpotifyProvider(
                client_id=config.spotify_client_id,
                client_secret=config.spotify_client_secret,
            )
        scraper_sp = ScraperSpotifyProvider(enabled=config.spotify_scraper_enabled)
        self.spotify = SpotifyOrchestrator(
            official_provider=official_sp,
            scraper_provider=scraper_sp,
        )
        self.matcher = TrackMatcher(ytdlp_resolver=self.resolver, db=self.db)
        self.recommender = YTMusicRadioRecommender()
        self.players: dict[int, GuildPlayer] = {}
        self.active_games = ActiveGames()
        self.tables = TableManager(
            self.db,
            self.active_games,
            welcome_bonus=config.economy_welcome_bonus,
            min_bet=config.gambling_min_bet,
            max_bet=config.gambling_max_bet,
            rake_percent=config.gambling_duel_rake_percent,
            join_seconds=config.gambling_lobby_join_seconds,
            pick_seconds=config.gambling_turn_seconds,
            jackpot_rake_percent=config.gambling_jackpot_rake_percent,
            crash_max_multiplier=config.gambling_crash_max_multiplier,
            poker_max_hands=config.gambling_poker_max_hands,
        )
        self._tables_reconciled = False
        self._tables_reconciled = False
        self.start_time = time.time()
        self.error_ring_buffer: collections.deque[str] = collections.deque(maxlen=20)
        self.command_reward_limiter = CommandRewardLimiter(
            limit=config.economy_command_rate_limit, window_s=60.0
        )
        self.voice_earn_tracker = VoiceEarnTracker(
            bot=self,
            interval_s=config.economy_voice_interval_seconds,
            reward=config.economy_voice_reward,
        )

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
            tree_cls=YokaiTree,
        )

        # Hook app command tree error handler
        self.tree.on_error = self.on_tree_error

    def get_player(self, guild_id: int) -> GuildPlayer:
        """Get or create the single GuildPlayer for this guild."""
        if guild_id not in self.players:
            self.players[guild_id] = GuildPlayer(
                guild_id=guild_id,
                bot=self,
                resolver=self.resolver,
                buffer=self.audio_buffer,
            )
        return self.players[guild_id]

    async def setup_hook(self) -> None:
        """Initialize database, load cogs, and synchronize slash commands to single guild."""
        logger.info("Initializing SQLite storage at %s...", self.config.db_path)
        await self.db.initialize()
        refunded = await self.db.refund_open_bets()
        if refunded:
            logger.info("Refunded %d gambling bet(s) left open by the previous run.", refunded)
        await self.audio_buffer.start()
        # ytmusicapi's first call is slow (about 1.5 s); pay it now, not on someone's /play
        self._warmup_task = asyncio.create_task(self.searcher.warm_up())

        # Load extension cogs
        logger.info("Loading extensions...")
        await self.load_extension("yokai.cogs.meta")
        await self.load_extension("yokai.cogs.music")
        await self.load_extension("yokai.cogs.economy")
        await self.load_extension("yokai.cogs.gambling")
        await self.load_extension("yokai.cogs.admin")
        await self.load_extension("yokai.cogs.owner")
        await self.load_extension("yokai.cogs.tables")
        await self.load_extension("yokai.cogs.lottery")

        # Sync commands directly to the single target guild for instant availability
        guild_obj = discord.Object(id=self.config.guild_id)
        self.tree.copy_global_to(guild=guild_obj)
        try:
            synced = await self.tree.sync(guild=guild_obj)
            logger.info("Synced %d slash command(s) to guild %d", len(synced), self.config.guild_id)
        except Exception as exc:
            logger.error("Failed to sync commands to guild %d: %s", self.config.guild_id, exc)

        self.voice_earn_tracker.start()

    async def on_ready(self) -> None:
        """Log startup status once connected to Discord gateway."""
        bot_user = self.user
        user_str = f"{bot_user.name}#{bot_user.discriminator}" if bot_user else "Yokai"
        logger.info("Yokai online as %s (Guild ID: %d)", user_str, self.config.guild_id)
        logger.info("Initial presence confirmed: IDLE, Watching for plant")
        self.heartbeat.start()
        self.loop_lag.start()
        if not self._tables_reconciled:
            self._tables_reconciled = True
            tables_cog = self.get_cog("Tables")
            if tables_cog is not None:
                try:
                    await tables_cog.reconcile()  # type: ignore[attr-defined]
                except (discord.HTTPException, aiosqlite.Error) as exc:
                    logger.error("Could not reconcile table channels: %s", exc)

    async def on_app_command_completion(
        self,
        interaction: discord.Interaction,
        command: app_commands.Command | app_commands.ContextMenu,
    ) -> None:
        """Central audit hook: records commands and credits Renown for eligible music commands."""
        cmd_name = command.qualified_name
        try:
            await self.db.log_command(interaction.user.id, cmd_name)
            logger.debug("Logged command /%s by user %d", cmd_name, interaction.user.id)
        except Exception as exc:
            logger.error("Failed to log command execution in database: %s", exc)

        if (
            self.config.economy_command_reward > 0
            and cmd_name in MUSIC_COMMAND_NAMES
            and self.command_reward_limiter.try_consume(interaction.user.id)
        ):
            try:
                await self.db.credit_wallet(
                    interaction.user.id,
                    self.config.economy_command_reward,
                    reason="command",
                    welcome_bonus=self.config.economy_welcome_bonus,
                )
            except Exception as exc:
                logger.error("Failed to credit music-command Renown reward: %s", exc)

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

        err_msg = str(cause).splitlines()[0] if str(cause) else type(cause).__name__
        self.error_ring_buffer.append(f"/{cmd_name}: {type(cause).__name__}: {err_msg}")

        avatar_url = str(self.user.display_avatar.url) if self.user else None

        if isinstance(cause, YokaiError):
            logger.warning("Domain error executing /%s: %s", cmd_name, cause)
            embed = EmbedFactory.error(
                message=cause.message,
                title=cause.title,
                user_hint=cause.user_hint,
                bot_avatar_url=avatar_url,
            )
        else:
            logger.error("Unexpected error executing /%s: %s", cmd_name, cause, exc_info=cause)
            embed = EmbedFactory.error(
                message="An unexpected system error occurred while processing this command.",
                title="Something broke",
                user_hint="Check /admin diag or contact the bot owner if the issue persists.",
                bot_avatar_url=avatar_url,
            )

        await send(interaction, embed=embed, ephemeral=True)

    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        """Handle voice occupancy updates and bot disconnects/moves."""
        if member.guild.id != self.config.guild_id:
            return

        player = self.players.get(member.guild.id)
        if not player:
            return

        # 1. Update for bot itself
        if self.user and member.id == self.user.id:
            if before.channel is not None and after.channel is None:
                # Our own stop() clears voice_client before it disconnects, so only a kick or
                # an external disconnect still has one; never re-enter stop() for our own.
                if player.voice_client is not None:
                    logger.info("Yokai was disconnected/kicked from voice channel.")
                    await player.stop()
                await self.presence.set_idle()
            elif before.channel != after.channel and after.channel is not None:
                logger.info("Yokai was moved to voice channel #%s", after.channel.name)
                player.on_voice_member_update()
            return

        # 2. Member joined or left player's voice channel
        if player.voice_client and player.voice_client.channel:
            bot_vc_id = player.voice_client.channel.id
            if (before.channel and before.channel.id == bot_vc_id) or (
                after.channel and after.channel.id == bot_vc_id
            ):
                player.on_voice_member_update()

    async def close(self) -> None:
        """Clean up database connections, players, and close bot session."""
        logger.info("Shutting down Yokai...")
        for p in list(self.players.values()):
            try:
                await p.stop()
            except Exception as exc:
                logger.debug("Error stopping player during shutdown: %s", exc)
        await self.voice_earn_tracker.stop()
        await self.heartbeat.stop()
        await self.loop_lag.stop()
        await self.names.close()
        try:
            await self.spotify.close()
        except Exception as exc:
            logger.error("Error closing Spotify orchestrator: %s", exc)

        try:
            await self.db.close()
        except Exception as exc:
            logger.error("Error closing database: %s", exc)
        await super().close()
