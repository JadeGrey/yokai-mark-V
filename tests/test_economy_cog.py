"""Tests for the economy slash commands."""

from __future__ import annotations

import random
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from yokai.cogs.economy import EconomyCog
from yokai.economy.models import DailyClaimResult, Wallet


def make_bot() -> MagicMock:
    bot = MagicMock()
    bot.user = None
    bot.config.owner_id = 999
    bot.config.economy_welcome_bonus = 200
    bot.config.economy_daily_base = 100
    return bot


def make_interaction(user_id: int = 111) -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    user.bot = False
    interaction.user = user
    interaction.response.defer = AsyncMock()
    return interaction


def _wallet(user_id: int, balance: int) -> Wallet:
    return Wallet(
        user_id=user_id, balance=balance, last_daily_ts=None, daily_streak=0, created_ts=0
    )


@pytest.mark.asyncio
async def test_balance_defaults_to_the_caller_and_ensures_their_wallet() -> None:
    bot = make_bot()
    bot.db.ensure_wallet = AsyncMock(return_value=_wallet(111, 200))
    cog = EconomyCog(bot)
    interaction = make_interaction(111)

    with patch("yokai.cogs.economy.send", new=AsyncMock()) as sent:
        await cog.balance.callback(cog, interaction, user=None)

    bot.db.ensure_wallet.assert_awaited_once_with(111, 200)
    embed = sent.await_args.kwargs["embed"]
    assert "200" in embed.description


@pytest.mark.asyncio
async def test_balance_for_another_user_is_read_only_and_shows_zero_if_absent() -> None:
    bot = make_bot()
    bot.db.get_wallet = AsyncMock(return_value=None)
    cog = EconomyCog(bot)
    interaction = make_interaction(111)
    target = MagicMock(spec=discord.Member)
    target.id = 222

    with patch("yokai.cogs.economy.send", new=AsyncMock()) as sent:
        await cog.balance.callback(cog, interaction, user=target)

    bot.db.get_wallet.assert_awaited_once_with(222)
    embed = sent.await_args.kwargs["embed"]
    assert "0" in embed.description


@pytest.mark.asyncio
async def test_daily_on_cooldown_replies_ephemeral_without_deferring() -> None:
    bot = make_bot()
    bot.db.get_wallet = AsyncMock(return_value=_wallet(111, 200))
    with patch("yokai.economy.daily.cooldown_remaining_s", return_value=3600):
        cog = EconomyCog(bot)
        interaction = make_interaction(111)
        with patch("yokai.cogs.economy.send", new=AsyncMock()) as sent:
            await cog.daily.callback(cog, interaction)

    interaction.response.defer.assert_not_awaited()
    assert sent.await_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_daily_success_defers_publicly_and_shows_the_payout() -> None:
    bot = make_bot()
    bot.db.get_wallet = AsyncMock(return_value=None)  # first-ever claim, no cooldown
    bot.db.claim_daily = AsyncMock(
        return_value=DailyClaimResult(payout=120, streak_day=1, new_balance=320)
    )
    cog = EconomyCog(bot)
    cog._rng = random.Random(0)
    interaction = make_interaction(111)

    with patch("yokai.cogs.economy.send", new=AsyncMock()) as sent:
        await cog.daily.callback(cog, interaction)

    interaction.response.defer.assert_awaited_once()
    assert sent.await_args.kwargs.get("ephemeral", False) is False
    embed = sent.await_args.kwargs["embed"]
    assert "120" in embed.description


@pytest.mark.asyncio
async def test_pay_rejects_self_payment_before_touching_balances() -> None:
    """Review Focus: self-pay must be refused before any wallet is read or changed."""
    bot = make_bot()
    bot.db.ensure_wallet = AsyncMock()
    cog = EconomyCog(bot)
    interaction = make_interaction(111)
    target = MagicMock(spec=discord.Member)
    target.id = 111
    target.bot = False

    with patch("yokai.cogs.economy.send", new=AsyncMock()) as sent:
        await cog.pay.callback(cog, interaction, user=target, amount=10)

    bot.db.ensure_wallet.assert_not_awaited()
    assert sent.await_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_pay_rejects_a_bot_target() -> None:
    bot = make_bot()
    bot.db.ensure_wallet = AsyncMock()
    cog = EconomyCog(bot)
    interaction = make_interaction(111)
    target = MagicMock(spec=discord.Member)
    target.id = 222
    target.bot = True

    with patch("yokai.cogs.economy.send", new=AsyncMock()) as sent:
        await cog.pay.callback(cog, interaction, user=target, amount=10)

    bot.db.ensure_wallet.assert_not_awaited()
    assert sent.await_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_pay_rejects_a_non_positive_amount() -> None:
    bot = make_bot()
    bot.db.ensure_wallet = AsyncMock()
    cog = EconomyCog(bot)
    interaction = make_interaction(111)
    target = MagicMock(spec=discord.Member)
    target.id = 222
    target.bot = False

    with patch("yokai.cogs.economy.send", new=AsyncMock()) as sent:
        await cog.pay.callback(cog, interaction, user=target, amount=0)

    bot.db.ensure_wallet.assert_not_awaited()
    assert sent.await_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_pay_rejects_when_the_sender_cannot_afford_it_before_deferring() -> None:
    bot = make_bot()
    bot.db.ensure_wallet = AsyncMock(return_value=_wallet(111, 5))
    cog = EconomyCog(bot)
    interaction = make_interaction(111)
    target = MagicMock(spec=discord.Member)
    target.id = 222
    target.bot = False

    with patch("yokai.cogs.economy.send", new=AsyncMock()) as sent:
        await cog.pay.callback(cog, interaction, user=target, amount=999)

    interaction.response.defer.assert_not_awaited()
    assert sent.await_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_pay_success_defers_publicly_and_transfers() -> None:
    bot = make_bot()
    bot.db.ensure_wallet = AsyncMock(return_value=_wallet(111, 500))
    bot.db.pay = AsyncMock(return_value=(400, 300))
    cog = EconomyCog(bot)
    interaction = make_interaction(111)
    target = MagicMock(spec=discord.Member)
    target.id = 222
    target.bot = False

    with patch("yokai.cogs.economy.send", new=AsyncMock()) as sent:
        await cog.pay.callback(cog, interaction, user=target, amount=100)

    bot.db.pay.assert_awaited_once_with(111, 222, 100, welcome_bonus=200)
    assert sent.await_args.kwargs.get("ephemeral", False) is False


@pytest.mark.asyncio
async def test_economy_admin_refuses_non_owners() -> None:
    bot = make_bot()
    bot.db.admin_adjust_wallet = AsyncMock()
    cog = EconomyCog(bot)
    interaction = make_interaction(111)  # not the owner (999)
    target = MagicMock(spec=discord.Member)
    target.id = 222

    with patch("yokai.cogs.economy.send", new=AsyncMock()) as sent:
        await cog.economy_admin.callback(cog, interaction, user=target, amount=100)

    bot.db.admin_adjust_wallet.assert_not_awaited()
    interaction.response.defer.assert_not_awaited()
    assert sent.await_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_economy_admin_allows_the_owner() -> None:
    bot = make_bot()
    bot.db.admin_adjust_wallet = AsyncMock(return_value=(100, 300))
    cog = EconomyCog(bot)
    interaction = make_interaction(999)  # the owner
    target = MagicMock(spec=discord.Member)
    target.id = 222

    with patch("yokai.cogs.economy.send", new=AsyncMock()) as sent:
        await cog.economy_admin.callback(cog, interaction, user=target, amount=100)

    bot.db.admin_adjust_wallet.assert_awaited_once_with(222, 100, welcome_bonus=200)
    assert sent.await_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_leaderboard_defers_and_sends_a_paginator_view() -> None:
    from yokai.ui.views import LeaderboardPaginator

    bot = make_bot()
    bot.db.get_leaderboard = AsyncMock(return_value=([_wallet(111, 500)], 1))
    cog = EconomyCog(bot)
    interaction = make_interaction(111)
    message = MagicMock(spec=discord.Message)

    with patch("yokai.cogs.economy.send", new=AsyncMock(return_value=message)) as sent:
        await cog.leaderboard.callback(cog, interaction)

    interaction.response.defer.assert_awaited_once()
    view = sent.await_args.kwargs["view"]
    assert isinstance(view, LeaderboardPaginator)
    assert view.message is message
