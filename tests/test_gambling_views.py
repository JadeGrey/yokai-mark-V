"""Tests for the slots 'Spin again' view."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from yokai.ui.game_views import SlotsView


def _interaction(user_id: int) -> MagicMock:
    interaction = MagicMock()
    interaction.user.id = user_id
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.response.edit_message = AsyncMock()
    interaction.response.send_message = AsyncMock()
    return interaction


@pytest.mark.asyncio
async def test_only_the_player_can_press_spin_again() -> None:
    view = SlotsView(user_id=1, stake=10, replay=AsyncMock())
    assert await view.interaction_check(_interaction(1)) is True
    stranger = _interaction(2)
    assert await view.interaction_check(stranger) is False
    stranger.response.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_spin_again_disables_the_button_and_replays_the_same_stake_once() -> None:
    replay = AsyncMock()
    view = SlotsView(user_id=1, stake=25, replay=replay)
    interaction = _interaction(1)
    await view.spin_again.callback(interaction)
    assert view.spin_again.disabled is True
    interaction.response.edit_message.assert_awaited_once()
    replay.assert_awaited_once_with(interaction, 25)
