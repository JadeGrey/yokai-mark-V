"""Voice ticks report time spent with company to the challenge engine."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from tests.moderation_helpers import fake_moderation
from yokai.challenges.models import VoiceMinutes
from yokai.economy.voice_tracker import VoiceEarnTracker


def _member(user_id: int, bot: bool = False) -> MagicMock:
    member = MagicMock()
    member.id = user_id
    member.bot = bot
    return member


def _bot(channels, blacklisted=()) -> MagicMock:
    bot = MagicMock()
    bot.config.guild_id = 1
    bot.config.economy_welcome_bonus = 200
    guild = MagicMock()
    guild.voice_channels = channels
    guild.afk_channel = None
    bot.get_guild.return_value = guild
    bot.db.credit_wallet = AsyncMock()
    bot.moderation = fake_moderation(owner_id=0, blacklisted=blacklisted)
    return bot


def _channel(members) -> MagicMock:
    channel = MagicMock()
    channel.id = 10
    channel.members = members
    return channel


def _emitted(bot: MagicMock) -> set[VoiceMinutes]:
    return {call.args[0] for call in bot.challenges.emit.call_args_list}


async def test_every_credited_human_gets_minutes_for_the_tick() -> None:
    bot = _bot([_channel([_member(1), _member(2), _member(99, bot=True)])])
    await VoiceEarnTracker(bot, interval_s=300, reward=5).tick()
    assert _emitted(bot) == {VoiceMinutes(1, 5), VoiceMinutes(2, 5)}


async def test_a_lone_human_and_blacklisted_members_get_nothing() -> None:
    alone = _bot([_channel([_member(1), _member(99, bot=True)])])
    await VoiceEarnTracker(alone, interval_s=300, reward=5).tick()
    assert _emitted(alone) == set()

    bot = _bot([_channel([_member(1), _member(2)])], blacklisted=(2,))
    await VoiceEarnTracker(bot, interval_s=300, reward=5).tick()
    assert _emitted(bot) == {VoiceMinutes(1, 5)}


async def test_minutes_still_count_when_voice_pay_is_off_or_the_credit_fails() -> None:
    off = _bot([_channel([_member(1), _member(2)])])
    await VoiceEarnTracker(off, interval_s=300, reward=0).tick()
    off.db.credit_wallet.assert_not_awaited()
    assert _emitted(off) == {VoiceMinutes(1, 5), VoiceMinutes(2, 5)}

    failing = _bot([_channel([_member(1), _member(2)])])
    failing.db.credit_wallet.side_effect = RuntimeError("db down")
    await VoiceEarnTracker(failing, interval_s=300, reward=5).tick()
    assert _emitted(failing) == {VoiceMinutes(1, 5), VoiceMinutes(2, 5)}


async def test_a_short_interval_still_counts_at_least_one_minute() -> None:
    bot = _bot([_channel([_member(1), _member(2)])])
    await VoiceEarnTracker(bot, interval_s=30, reward=5).tick()
    assert _emitted(bot) == {VoiceMinutes(1, 1), VoiceMinutes(2, 1)}
