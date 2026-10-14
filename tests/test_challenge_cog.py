"""/challenges: opens ephemerally, switches tabs, refuses strangers, respects the off switch."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import discord

from tests.challenge_helpers import tpl
from tests.test_bot_wiring import make_bot
from yokai.challenges.models import ProgressView
from yokai.cogs import challenges as challenges_module
from yokai.cogs.challenges import ChallengesCog
from yokai.ui.challenge_views import ChallengesView


def _interaction(user_id: int = 1) -> MagicMock:
    interaction = MagicMock()
    interaction.user.id = user_id
    interaction.response.defer = AsyncMock()
    interaction.response.edit_message = AsyncMock()
    interaction.response.is_done.return_value = False
    return interaction


def _bot_with_rows():
    bot = make_bot()
    row = ProgressView(tpl("d_a", "tracks", 5, 40), progress=2, completed=False, reward=40)
    bot.challenges.view = AsyncMock(return_value=[row])
    bot.challenges.next_reset = MagicMock(return_value=1_800_000_000)
    return bot


async def test_the_command_is_registered_with_a_short_description() -> None:
    bot = make_bot()
    await challenges_module.setup(bot)
    command = bot.tree.get_command("challenges")
    assert command is not None and 0 < len(command.description) <= 100


async def test_the_command_defers_ephemerally_then_replies_with_the_board() -> None:
    bot = _bot_with_rows()
    cog = ChallengesCog(bot)
    interaction = _interaction()
    with patch("yokai.cogs.challenges.send", new=AsyncMock()) as send:
        await cog.challenges.callback(cog, interaction)
    interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
    kwargs = send.await_args.kwargs
    assert kwargs["ephemeral"] is True
    assert isinstance(kwargs["view"], ChallengesView)
    assert "Daily" in kwargs["embed"].title and "2/5" in kwargs["embed"].description


async def test_when_challenges_are_off_it_answers_without_deferring() -> None:
    bot = _bot_with_rows()
    bot.challenges.enabled = False
    cog = ChallengesCog(bot)
    interaction = _interaction()
    with patch("yokai.cogs.challenges.send", new=AsyncMock()) as send:
        await cog.challenges.callback(cog, interaction)
    interaction.response.defer.assert_not_awaited()
    assert send.await_args.kwargs["ephemeral"] is True
    assert "off" in send.await_args.kwargs["embed"].title.lower()


async def test_the_tabs_switch_the_period_and_edit_the_message() -> None:
    bot = _bot_with_rows()
    view = ChallengesView(bot, user_id=1)
    interaction = _interaction()
    await view.weekly_button.callback(interaction)
    assert view.period == "weekly"
    bot.challenges.view.assert_awaited_with(1, "weekly")
    edited = interaction.response.edit_message.await_args.kwargs
    assert "Weekly" in edited["embed"].title and edited["view"] is view


async def test_only_the_owner_of_the_view_may_use_it() -> None:
    view = ChallengesView(_bot_with_rows(), user_id=1)
    assert view.timeout is not None and view.timeout <= 180
    with patch("yokai.moderation.gate.screen", new=AsyncMock(return_value=False)):
        stranger = _interaction(user_id=2)
        stranger.response.send_message = AsyncMock()
        assert await view.interaction_check(stranger) is False
        stranger.response.send_message.assert_awaited_once()
        assert stranger.response.send_message.await_args.kwargs["ephemeral"] is True

        owner = _interaction(user_id=1)
        owner.response.send_message = AsyncMock()
        assert await view.interaction_check(owner) is True
        owner.response.send_message.assert_not_awaited()


async def test_timeout_disables_the_buttons() -> None:
    view = ChallengesView(_bot_with_rows(), user_id=1)
    view.message = MagicMock(spec=discord.Message)
    view.message.edit = AsyncMock()
    await view.on_timeout()
    assert all(item.disabled for item in view.children)
