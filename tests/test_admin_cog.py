"""Tests for the /admin command group."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from tests.moderation_helpers import fake_moderation
from yokai.cogs.admin import AdminCog


def make_bot() -> MagicMock:
    bot = MagicMock()
    bot.user = None
    bot.config.owner_id = 999
    bot.moderation = fake_moderation(owner_id=999, admins={555})
    bot.config.economy_welcome_bonus = 200
    return bot


def make_interaction(user_id: int = 111) -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    user.bot = False
    interaction.user = user
    interaction.response.defer = AsyncMock()
    return interaction


def make_target(user_id: int = 222, *, bot: bool = False) -> MagicMock:
    target = MagicMock(spec=discord.Member)
    target.id = user_id
    target.bot = bot
    return target


def test_the_admin_group_is_named_admin() -> None:
    assert AdminCog.admin.name == "admin"


@pytest.mark.asyncio
async def test_economy_refuses_non_admins() -> None:
    bot = make_bot()
    bot.db.admin_adjust_wallet = AsyncMock()
    cog = AdminCog(bot)
    interaction = make_interaction(111)  # neither owner (999) nor admin (555)

    with patch("yokai.cogs.admin.send", new=AsyncMock()) as sent:
        await cog.economy.callback(cog, interaction, user=make_target(), amount=100)

    bot.db.admin_adjust_wallet.assert_not_awaited()
    interaction.response.defer.assert_not_awaited()
    assert sent.await_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_economy_allows_the_owner() -> None:
    bot = make_bot()
    bot.db.admin_adjust_wallet = AsyncMock(return_value=(100, 300))
    cog = AdminCog(bot)
    interaction = make_interaction(999)

    with patch("yokai.cogs.admin.send", new=AsyncMock()) as sent:
        await cog.economy.callback(cog, interaction, user=make_target(), amount=100)

    bot.db.admin_adjust_wallet.assert_awaited_once_with(222, 100, welcome_bonus=200)
    assert sent.await_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_economy_allows_an_adminlist_member() -> None:
    bot = make_bot()
    bot.db.admin_adjust_wallet = AsyncMock(return_value=(100, 300))
    cog = AdminCog(bot)
    interaction = make_interaction(555)  # admin, not the owner

    with patch("yokai.cogs.admin.send", new=AsyncMock()):
        await cog.economy.callback(cog, interaction, user=make_target(), amount=100)

    bot.db.admin_adjust_wallet.assert_awaited_once()


@pytest.mark.asyncio
async def test_economy_rejects_a_bot_target() -> None:
    """Review Focus: /admin economy must not be able to create a wallet for a bot."""
    bot = make_bot()
    bot.db.admin_adjust_wallet = AsyncMock()
    cog = AdminCog(bot)
    interaction = make_interaction(999)

    with patch("yokai.cogs.admin.send", new=AsyncMock()) as sent:
        await cog.economy.callback(cog, interaction, user=make_target(bot=True), amount=100)

    bot.db.admin_adjust_wallet.assert_not_awaited()
    interaction.response.defer.assert_not_awaited()
    assert sent.await_args.kwargs["ephemeral"] is True
