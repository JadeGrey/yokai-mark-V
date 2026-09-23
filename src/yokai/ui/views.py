"""Reusable base view for UI interactions with timeout handling and permission checks."""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
from typing import TYPE_CHECKING, Any, Optional, Sequence

import discord

from yokai.theme import quip_bank
from yokai.ui.embeds import EmbedFactory

if TYPE_CHECKING:
    from yokai.music.models import Track
    from yokai.music.player import GuildPlayer

logger = logging.getLogger(__name__)


class BaseView(discord.ui.View):
    """Base interactive view with automatic timeout deactivation and caller checks."""

    def __init__(
        self,
        allowed_user_ids: Optional[Sequence[int]] = None,
        timeout: float = 180.0,
    ) -> None:
        super().__init__(timeout=timeout)
        self.allowed_user_ids: Optional[set[int]] = (
            set(allowed_user_ids) if allowed_user_ids is not None else None
        )
        self.message: Optional[discord.Message] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Validate if the interacting user is authorized to use this view."""
        if self.allowed_user_ids is not None and interaction.user.id not in self.allowed_user_ids:
            err_embed = EmbedFactory.error(
                message="You do not have permission to interact with this control.",
                user_hint="Only the user who initiated this request can use these buttons.",
                context="Permissions",
                include_quip=False,
            )
            if not interaction.response.is_done():
                await interaction.response.send_message(embed=err_embed, ephemeral=True)
            else:
                await interaction.followup.send(embed=err_embed, ephemeral=True)
            return False
        return True

    def disable_all_items(self) -> None:
        """Disable every child button or select component in the view."""
        for item in self.children:
            if isinstance(item, (discord.ui.Button, discord.ui.Select)):
                item.disabled = True

    async def on_timeout(self) -> None:
        """Disable components when the interaction times out."""
        self.disable_all_items()
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException as exc:
                logger.debug("Could not disable view on timeout: %s", exc)

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item[discord.ui.View],
    ) -> None:
        """Catch unhandled interaction errors and present a safe error embed."""
        logger.error("Unhandled error in view item %s: %s", item, error, exc_info=error)
        err_embed = EmbedFactory.error(
            message="An unexpected error occurred while processing this action.",
            user_hint="Please try again or contact the bot owner if the issue persists.",
            context="Control Error",
        )
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(embed=err_embed, ephemeral=True)
            else:
                await interaction.followup.send(embed=err_embed, ephemeral=True)
        except Exception as exc:
            logger.error("Failed to notify user of view error: %s", exc)


class QueuePaginator(BaseView):
    """Interactive paginator for queue pages."""

    def __init__(
        self,
        queue: Any,
        author_id: int,
        initial_page: int = 1,
        bot_avatar_url: Optional[str] = None,
        timeout: float = 180.0,
    ) -> None:
        super().__init__(allowed_user_ids=[author_id], timeout=timeout)
        self.queue = queue
        self.current_page = initial_page
        self.bot_avatar_url = bot_avatar_url
        self._update_buttons()

    def _update_buttons(self) -> None:
        _, total_pages, _ = self.queue.get_page(self.current_page)
        self.prev_button.disabled = self.current_page <= 1
        self.next_button.disabled = self.current_page >= total_pages

    def build_embed(self) -> discord.Embed:
        tracks, total_pages, total_tracks = self.queue.get_page(self.current_page)
        return EmbedFactory.queue_page(
            tracks=[(pos, t.title, t.artist, t.duration_s) for pos, t in tracks],
            page=self.current_page,
            total_pages=total_pages,
            total_tracks=total_tracks,
            loop_mode=self.queue.loop_mode.value,
            bot_avatar_url=self.bot_avatar_url,
        )

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
    async def prev_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if self.current_page > 1:
            self.current_page -= 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        _, total_pages, _ = self.queue.get_page(self.current_page)
        if self.current_page < total_pages:
            self.current_page += 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)


class RecommendView(BaseView):
    """View with a single-use 'Queue them all' button for track recommendations."""

    def __init__(
        self,
        player: GuildPlayer,
        tracks: Sequence[Track],
        requester_id: int,
        seed_title: str,
        bot_avatar_url: Optional[str] = None,
        timeout: float = 180.0,
    ) -> None:
        super().__init__(allowed_user_ids=[requester_id], timeout=timeout)
        self.player = player
        self.tracks = list(tracks)
        self.requester_id = requester_id
        self.seed_title = seed_title
        self.bot_avatar_url = bot_avatar_url
        self._lock = asyncio.Lock()
        self._queued = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Allow interaction if user is original requester or in the bot's voice channel."""
        user = interaction.user
        if not isinstance(user, discord.Member):
            return False

        # 1. Requester is always allowed
        if user.id == self.requester_id:
            return True

        # 2. Member in same voice channel as bot is allowed
        if (
            self.player.voice_client
            and self.player.voice_client.channel
            and user.voice
            and user.voice.channel
            and user.voice.channel.id == self.player.voice_client.channel.id
        ):
            return True

        # Deny with friendly explanation
        err_embed = EmbedFactory.error(
            message=(
                "You must be in the voice channel or be the requester to queue recommendations."
            ),
            user_hint="Join the voice channel to use this button.",
            context="Permissions",
            include_quip=False,
        )
        if not interaction.response.is_done():
            await interaction.response.send_message(embed=err_embed, ephemeral=True)
        else:
            await interaction.followup.send(embed=err_embed, ephemeral=True)
        return False

    @discord.ui.button(label="Queue them all", style=discord.ButtonStyle.primary, emoji="▶️")
    async def queue_all_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        async with self._lock:
            if self._queued:
                return
            self._queued = True

            # 1. Connect to voice channel if bot is disconnected
            if not self.player.voice_client or not self.player.voice_client.is_connected():
                if (
                    isinstance(interaction.user, discord.Member)
                    and interaction.user.voice
                    and interaction.user.voice.channel
                ):
                    await self.player.connect(interaction.user.voice.channel)

            # 2. Enqueue all recommended tracks
            if not self.tracks:
                return

            if not self.player.is_playing and not self.player.is_paused:
                first = self.tracks[0]
                rest = self.tracks[1:]
                self.player.queue.add(first)
                await self.player.play_next()
                self.player.queue.add_many(rest)
                self.player.maybe_prefetch()
            else:
                self.player.queue.add_many(self.tracks)
                self.player.maybe_prefetch()

            # 3. Disable all buttons and transition embed
            self.disable_all_items()
            quip = quip_bank.get("recs_queued")
            embed = EmbedFactory.success(
                title="Recommendations Queued",
                description=f"Added **{len(self.tracks)}** recommended tracks to the queue.",
                context=f"Seed: {self.seed_title}",
                quip=quip,
                bot_avatar_url=self.bot_avatar_url,
            )
            await interaction.response.edit_message(embed=embed, view=self)


class DiagView(BaseView):
    """Owner-only diagnostic view featuring an in-place yt-dlp package updater."""

    def __init__(
        self,
        owner_id: int,
        bot_avatar_url: Optional[str] = None,
        timeout: float = 180.0,
    ) -> None:
        super().__init__(allowed_user_ids=[owner_id], timeout=timeout)
        self.owner_id = owner_id
        self.bot_avatar_url = bot_avatar_url
        self._lock = asyncio.Lock()
        self._updated = False

    @discord.ui.button(label="Update yt-dlp", style=discord.ButtonStyle.primary, emoji="🔄")
    async def update_ytdlp_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        async with self._lock:
            if self._updated:
                return
            self._updated = True

            # Defer followup because pip install takes a few seconds
            await interaction.response.defer(ephemeral=True)

            self.disable_all_items()
            if self.message:
                try:
                    await self.message.edit(view=self)
                except Exception:
                    pass

            def _run_pip_update() -> subprocess.CompletedProcess[str]:
                cmd = [sys.executable, "-m", "pip", "install", "-U", "yt-dlp[default]"]
                return subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                )

            try:
                proc = await asyncio.to_thread(_run_pip_update)
                if proc.returncode == 0:
                    embed = EmbedFactory.success(
                        title="yt-dlp Updated",
                        description=(
                            "yt-dlp was successfully updated to the latest release.\n\n"
                            "⚠️ **Restart Required:** Please restart the Yokai process "
                            "to load the updated package into memory."
                        ),
                        context="Maintenance",
                        bot_avatar_url=self.bot_avatar_url,
                    )

                else:
                    err_lines = (
                        (proc.stderr or proc.stdout or "Unknown pip error").strip().splitlines()
                    )
                    last_err = err_lines[-1] if err_lines else f"Exit code {proc.returncode}"
                    embed = EmbedFactory.error(
                        message=f"Failed to update yt-dlp: {last_err}",
                        user_hint="Check host environment and pip permissions.",
                        context="Maintenance",
                        bot_avatar_url=self.bot_avatar_url,
                        include_quip=False,
                    )
            except Exception as exc:
                logger.error("Exception occurred while updating yt-dlp: %s", exc)
                embed = EmbedFactory.error(
                    message=f"Failed to execute pip update: {exc}",
                    user_hint="Check host environment permissions.",
                    context="Maintenance",
                    bot_avatar_url=self.bot_avatar_url,
                    include_quip=False,
                )

            await interaction.followup.send(embed=embed, ephemeral=True)
