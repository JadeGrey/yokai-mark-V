"""Reliability tests for GuildPlayer (bug reproductions for the Phase 1 baseline)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.test_player import DummyAudioSource, FakeResolver, FakeVoiceClient
from yokai.music.models import LoopMode, PlayerState, StreamInfo, Track
from yokai.music.player import GuildPlayer


def make_bot() -> MagicMock:
    bot = MagicMock()
    bot.config.max_queue_size = 50
    bot.config.ffmpeg_path = None
    bot.config.alone_disconnect_seconds = 120
    bot.config.idle_disconnect_seconds = 300
    bot.loop = asyncio.get_running_loop()
    bot.presence = AsyncMock()
    bot.db = AsyncMock()
    return bot


def make_track(video_id: str, duration_s: int = 180) -> Track:
    return Track(
        video_id=video_id,
        title=f"Title {video_id}",
        duration_s=duration_s,
        requester_id=1,
    )


class HangingResolver(FakeResolver):
    """Resolver whose stream lookups never finish (simulates a stuck yt-dlp call)."""

    async def get_stream(self, track: Track, *, background: bool = False) -> StreamInfo:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


@pytest.mark.xfail(strict=True, reason="Phase 2: skip must work while a stream load is stuck")
@pytest.mark.asyncio
async def test_skip_works_while_stream_load_is_stuck() -> None:
    player = GuildPlayer(guild_id=1, bot=make_bot(), resolver=HangingResolver())
    player.voice_client = FakeVoiceClient()
    player.queue.add(make_track("stuck"))
    player.queue.add(make_track("next"))

    load = asyncio.create_task(player.play_next())
    try:
        await asyncio.sleep(0.05)
        assert player.state == PlayerState.LOADING
        skipped = await asyncio.wait_for(player.skip(), timeout=1.0)
        assert skipped is not None
        assert skipped.video_id == "stuck"
    finally:
        load.cancel()


@pytest.mark.xfail(strict=True, reason="Phase 2: skip records exactly one play event")
@pytest.mark.asyncio
async def test_skip_records_a_single_play_event() -> None:
    bot = make_bot()
    player = GuildPlayer(guild_id=1, bot=bot, resolver=FakeResolver())
    player.voice_client = FakeVoiceClient()
    player.queue.add(make_track("t1"))
    player.queue.add(make_track("t2"))

    with patch("discord.FFmpegPCMAudio", side_effect=lambda *a, **k: DummyAudioSource()):
        await player.play_next()
        await player.skip()
        await asyncio.sleep(0.05)

    events = [
        call.kwargs["outcome"]
        for call in bot.db.record_play_event.await_args_list
        if call.kwargs["video_id"] == "t1"
    ]
    assert events == ["skipped"]


@pytest.mark.xfail(strict=True, reason="Phase 2: stop must leave the queue empty in loop mode")
@pytest.mark.asyncio
async def test_stop_leaves_queue_empty_even_with_loop_track() -> None:
    player = GuildPlayer(guild_id=1, bot=make_bot(), resolver=FakeResolver())
    player.voice_client = FakeVoiceClient()
    player.queue.loop_mode = LoopMode.TRACK
    player.queue.add(make_track("t1"))

    with patch("discord.FFmpegPCMAudio", side_effect=lambda *a, **k: DummyAudioSource()):
        await player.play_next()
        await player.stop()
        await asyncio.sleep(0.05)

    assert len(player.queue) == 0
