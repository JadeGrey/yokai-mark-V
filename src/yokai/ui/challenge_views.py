"""The /challenges tab view: Daily and Weekly for the user who opened it."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import discord

from yokai.ui.embeds import EmbedFactory
from yokai.ui.views import BaseView

if TYPE_CHECKING:
    from yokai.bot import YokaiBot


class ChallengesView(BaseView):
    """Two tabs over the same message. Times out and only answers to its owner."""

    def __init__(
        self,
        bot: YokaiBot,
        user_id: int,
        bot_avatar_url: Optional[str] = None,
        timeout: float = 120.0,
    ) -> None:
        super().__init__(allowed_user_ids=[user_id], timeout=timeout)
        self.bot = bot
        self.user_id = user_id
        self.bot_avatar_url = bot_avatar_url
        self.period = "daily"
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        daily = self.period == "daily"
        self.daily_button.style = (
            discord.ButtonStyle.primary if daily else discord.ButtonStyle.secondary
        )
        self.weekly_button.style = (
            discord.ButtonStyle.secondary if daily else discord.ButtonStyle.primary
        )

    async def build_embed(self) -> discord.Embed:
        engine = self.bot.challenges
        views = await engine.view(self.user_id, self.period)
        rows = [
            (v.template.title, v.progress, v.template.target, v.reward, v.completed) for v in views
        ]
        return EmbedFactory.challenge_board(
            period=self.period,
            rows=rows,
            resets_ts=engine.next_reset(self.period),
            bot_avatar_url=self.bot_avatar_url,
        )

    async def _show(self, interaction: discord.Interaction, period: str) -> None:
        self.period = period
        self._sync_buttons()
        embed = await self.build_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Daily", style=discord.ButtonStyle.primary)
    async def daily_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._show(interaction, "daily")

    @discord.ui.button(label="Weekly", style=discord.ButtonStyle.secondary)
    async def weekly_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._show(interaction, "weekly")
