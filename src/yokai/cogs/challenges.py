"""The /challenges command: your daily and weekly challenge progress."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

from yokai.ui.challenge_views import ChallengesView
from yokai.ui.embeds import EmbedFactory, send

if TYPE_CHECKING:
    from yokai.bot import YokaiBot


class ChallengesCog(commands.Cog, name="Challenges"):
    """Daily and weekly challenges."""

    def __init__(self, bot: YokaiBot) -> None:
        self.bot = bot

    def _avatar_url(self) -> Optional[str]:
        return str(self.bot.user.display_avatar.url) if self.bot.user else None

    @app_commands.command(
        name="challenges", description="See your daily and weekly challenges and progress."
    )
    async def challenges(self, interaction: discord.Interaction) -> None:
        if not self.bot.challenges.enabled:
            embed = EmbedFactory.info(
                title="Challenges are off",
                description="The owner has switched challenges off for now.",
                context="Challenges",
                bot_avatar_url=self._avatar_url(),
            )
            await send(interaction, embed=embed, ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        view = ChallengesView(self.bot, interaction.user.id, bot_avatar_url=self._avatar_url())
        embed = await view.build_embed()
        view.message = await send(interaction, embed=embed, view=view, ephemeral=True)


async def setup(bot: YokaiBot) -> None:
    """Extension setup entrypoint."""
    await bot.add_cog(ChallengesCog(bot))
