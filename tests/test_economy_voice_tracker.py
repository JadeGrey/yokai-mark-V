"""Tests for the voice-presence Renown tick (Review Focus: 2+ human requirement, bots excluded)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.moderation_helpers import fake_moderation
from yokai.economy.voice_tracker import VoiceEarnTracker


def _member(user_id: int, is_bot: bool = False) -> MagicMock:
    member = MagicMock()
    member.id = user_id
    member.bot = is_bot
    return member


def _channel(members: list[MagicMock]) -> MagicMock:
    channel = MagicMock()
    channel.members = members
    return channel


def _bot_with_guild(channels: list[MagicMock], afk_channel: MagicMock | None = None) -> MagicMock:
    bot = MagicMock()
    bot.config.guild_id = 1
    guild = MagicMock()
    guild.voice_channels = channels
    guild.afk_channel = afk_channel
    bot.get_guild.return_value = guild
    bot.db.credit_wallet = AsyncMock()
    bot.moderation = fake_moderation(owner_id=0)  # nobody blacklisted
    return bot


@pytest.mark.asyncio
async def test_tick_credits_every_human_in_a_channel_with_two_or_more_humans() -> None:
    channel = _channel([_member(1), _member(2), _member(99, is_bot=True)])
    bot = _bot_with_guild([channel])
    tracker = VoiceEarnTracker(bot, interval_s=300, reward=5)

    await tracker.tick()

    credited_ids = {call.args[0] for call in bot.db.credit_wallet.await_args_list}
    assert credited_ids == {1, 2}
    for call in bot.db.credit_wallet.await_args_list:
        assert call.args[1] == 5
        assert call.kwargs["reason"] == "voice"


@pytest.mark.asyncio
async def test_tick_does_not_credit_a_channel_with_only_one_human() -> None:
    """Review Focus: one human plus the bot itself (2 members, 1 human) must not pay out."""
    channel = _channel([_member(1), _member(99, is_bot=True)])
    bot = _bot_with_guild([channel])
    tracker = VoiceEarnTracker(bot, interval_s=300, reward=5)

    await tracker.tick()

    bot.db.credit_wallet.assert_not_awaited()


@pytest.mark.asyncio
async def test_tick_does_not_credit_an_empty_channel() -> None:
    bot = _bot_with_guild([_channel([])])
    tracker = VoiceEarnTracker(bot, interval_s=300, reward=5)
    await tracker.tick()
    bot.db.credit_wallet.assert_not_awaited()


@pytest.mark.asyncio
async def test_tick_does_nothing_when_the_guild_is_not_found() -> None:
    bot = MagicMock()
    bot.config.guild_id = 1
    bot.get_guild.return_value = None
    bot.db.credit_wallet = AsyncMock()
    tracker = VoiceEarnTracker(bot, interval_s=300, reward=5)
    await tracker.tick()
    bot.db.credit_wallet.assert_not_awaited()


@pytest.mark.asyncio
async def test_tick_skips_the_guild_afk_channel() -> None:
    """Review Focus: idle members parked in the AFK channel must not farm Renown."""
    afk_channel = _channel([_member(1), _member(2)])
    bot = _bot_with_guild([afk_channel], afk_channel=afk_channel)
    tracker = VoiceEarnTracker(bot, interval_s=300, reward=5)

    await tracker.tick()

    bot.db.credit_wallet.assert_not_awaited()


@pytest.mark.asyncio
async def test_tick_still_credits_other_channels_when_the_afk_channel_has_humans() -> None:
    afk_channel = _channel([_member(1), _member(2)])
    other_channel = _channel([_member(3), _member(4)])
    bot = _bot_with_guild([afk_channel, other_channel], afk_channel=afk_channel)
    tracker = VoiceEarnTracker(bot, interval_s=300, reward=5)

    await tracker.tick()

    credited_ids = {call.args[0] for call in bot.db.credit_wallet.await_args_list}
    assert credited_ids == {3, 4}


@pytest.mark.asyncio
async def test_tick_continues_crediting_siblings_after_one_members_credit_fails() -> None:
    """Review Focus: one member's credit_wallet failure must not abort the rest of the tick."""
    channel = _channel([_member(1), _member(2), _member(3)])
    bot = _bot_with_guild([channel])

    async def credit_side_effect(user_id: int, *args: object, **kwargs: object) -> None:
        if user_id == 2:
            raise RuntimeError("transient db error")

    bot.db.credit_wallet = AsyncMock(side_effect=credit_side_effect)
    tracker = VoiceEarnTracker(bot, interval_s=300, reward=5)

    await tracker.tick()

    attempted_ids = {call.args[0] for call in bot.db.credit_wallet.await_args_list}
    assert attempted_ids == {1, 2, 3}  # member 2's failure did not stop 1 and 3 from being tried


@pytest.mark.asyncio
async def test_tick_does_nothing_when_the_reward_is_disabled() -> None:
    """Review Focus: ECONOMY_VOICE_REWARD=0 must not call credit_wallet at all."""
    channel = _channel([_member(1), _member(2)])
    bot = _bot_with_guild([channel])
    tracker = VoiceEarnTracker(bot, interval_s=300, reward=0)

    await tracker.tick()

    bot.db.credit_wallet.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_and_stop_manage_the_background_task() -> None:
    bot = _bot_with_guild([])
    tracker = VoiceEarnTracker(bot, interval_s=300, reward=5)
    tracker.start()
    assert tracker._task is not None
    await tracker.stop()
    assert tracker._task is None


@pytest.mark.asyncio
async def test_tick_does_not_credit_a_blacklisted_member() -> None:
    """Review Focus 6: a blacklisted human earns nothing; the other human still does."""
    channel = _channel([_member(1), _member(2)])
    bot = _bot_with_guild([channel])
    bot.moderation = fake_moderation(owner_id=0, blacklisted={1})
    tracker = VoiceEarnTracker(bot, interval_s=300, reward=5)

    await tracker.tick()

    credited_ids = {call.args[0] for call in bot.db.credit_wallet.await_args_list}
    assert credited_ids == {2}
