"""Reusable base view for UI interactions with timeout handling and permission checks."""

from __future__ import annotations

import logging
from typing import Any, Optional, Sequence

import discord

from yokai.ui.embeds import EmbedFactory

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
