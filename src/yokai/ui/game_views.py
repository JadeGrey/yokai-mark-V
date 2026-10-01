"""Interactive views for the gambling games."""

from __future__ import annotations

from typing import Awaitable, Callable

import discord

from yokai.ui.views import BaseView

ReplayFn = Callable[[discord.Interaction, int], Awaitable[None]]


class SlotsView(BaseView):
    """A single-use 'Spin again' button that replays the same stake for the same player."""

    def __init__(self, user_id: int, stake: int, replay: ReplayFn, timeout: float = 120.0) -> None:
        super().__init__(allowed_user_ids=[user_id], timeout=timeout)
        self._stake = stake
        self._replay = replay

    @discord.ui.button(label="Spin again", style=discord.ButtonStyle.primary)
    async def spin_again(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.disable_all_items()
        await interaction.response.edit_message(view=self)
        await self._replay(interaction, self._stake)
