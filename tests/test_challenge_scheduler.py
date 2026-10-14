"""The scheduler batches completion notices per channel and survives failures."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import discord

from yokai.challenges.models import Completion
from yokai.challenges.scheduler import ChallengeScheduler


def _completion(user: int, channel: int | None, title: str = "Listen to 3 tracks") -> Completion:
    return Completion(user, "d_music_3", title, 30, channel)


def _setup(completions: list[Completion], channels: dict[int, object]):
    bot = MagicMock()
    bot.user = None
    bot.get_channel.side_effect = channels.get
    engine = MagicMock()
    engine.drain_completions.return_value = completions
    engine.active_slate = AsyncMock()
    engine.purge_old = AsyncMock(return_value=0)
    bot.challenges = engine
    return bot, engine


def _text_channel() -> MagicMock:
    channel = MagicMock(spec=discord.TextChannel)
    channel.send = AsyncMock()
    return channel


async def test_completions_in_one_channel_become_one_embed() -> None:
    channel = _text_channel()
    bot, _ = _setup([_completion(1, 5), _completion(2, 5)], {5: channel})
    await ChallengeScheduler(bot).flush_notices()
    channel.send.assert_awaited_once()
    embed = channel.send.await_args.kwargs["embed"]
    assert "<@1>" in embed.description and "<@2>" in embed.description
    assert channel.send.await_args.kwargs["allowed_mentions"].users is False


async def test_channels_are_separate_and_unknown_or_missing_channels_are_dropped() -> None:
    a, b = _text_channel(), _text_channel()
    bot, _ = _setup(
        [_completion(1, 5), _completion(2, 6), _completion(3, None), _completion(4, 99)],
        {5: a, 6: b},
    )
    await ChallengeScheduler(bot).flush_notices()
    a.send.assert_awaited_once()
    b.send.assert_awaited_once()


async def test_a_flood_is_collapsed_to_max_lines_plus_a_count() -> None:
    channel = _text_channel()
    bot, _ = _setup([_completion(i, 5) for i in range(25)], {5: channel})
    await ChallengeScheduler(bot, max_lines=10).flush_notices()
    description = channel.send.await_args.kwargs["embed"].description
    assert description.count("finished") == 10
    assert "15 more" in description


async def test_a_failed_send_is_swallowed() -> None:
    channel = _text_channel()
    channel.send.side_effect = discord.HTTPException(MagicMock(status=403), "forbidden")
    bot, _ = _setup([_completion(1, 5)], {5: channel})
    await ChallengeScheduler(bot).flush_notices()  # must not raise


async def test_an_unexpected_send_error_does_not_stop_other_channels(caplog) -> None:
    a, b = _text_channel(), _text_channel()
    a.send.side_effect = RuntimeError("boom")
    bot, _ = _setup([_completion(1, 5), _completion(2, 6)], {5: a, 6: b})
    with caplog.at_level("WARNING"):
        await ChallengeScheduler(bot).flush_notices()
    b.send.assert_awaited_once()
    assert any(r.levelname == "WARNING" and "5" in r.getMessage() for r in caplog.records)


async def test_tick_creates_both_slates_and_a_broken_tick_does_not_kill_the_loop() -> None:
    bot, engine = _setup([], {})
    scheduler = ChallengeScheduler(bot)
    await scheduler.tick()
    assert [c.args[0] for c in engine.active_slate.await_args_list] == ["daily", "weekly"]

    engine.active_slate.side_effect = RuntimeError("db down")
    await scheduler._safe_tick()  # logs and returns


async def test_start_and_stop_run_the_loop_without_leaking_the_task() -> None:
    bot, engine = _setup([], {})
    scheduler = ChallengeScheduler(bot, interval_s=0.01)
    scheduler.start()
    scheduler.start()  # idempotent
    for _ in range(100):
        if engine.active_slate.await_count:
            break
        await asyncio.sleep(0.01)
    await scheduler.stop()
    assert engine.active_slate.await_count >= 2
    assert scheduler._task is None
