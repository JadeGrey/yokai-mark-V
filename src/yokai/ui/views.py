"""Reusable base view for UI interactions with timeout handling and permission checks."""

from __future__ import annotations

import logging
from typing import Optional, Sequence

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
