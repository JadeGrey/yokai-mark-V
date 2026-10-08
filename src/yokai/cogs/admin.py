"""Admin-only slash commands, grouped under /admin."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

from yokai.errors import InvalidPaymentError
from yokai.ui.embeds import EmbedFactory, send

if TYPE_CHECKING:
    from yokai.bot import YokaiBot


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


async def setup(bot: YokaiBot) -> None:
    """Extension setup entrypoint."""
    await bot.add_cog(AdminCog(bot))
