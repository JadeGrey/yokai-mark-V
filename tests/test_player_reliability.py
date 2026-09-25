"""Reliability tests for GuildPlayer: races, loop modes, timeouts, resume, gapless, buffer."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.test_player import DummyAudioSource, FakeResolver, FakeVoiceClient
from yokai.errors import BotCheckError
from yokai.music import player as player_module
from yokai.music.buffer import AudioBuffer
from yokai.music.models import LoopMode, PlayerState, StreamInfo, Track
from yokai.music.player import GuildPlayer


class RecordingSource(DummyAudioSource):
    def __init__(self) -> None:
        self.cleaned = False

    def cleanup(self) -> None:
        self.cleaned = True


@pytest.fixture
def ffmpeg():
    """Replace FFmpegPCMAudio with a recorder that returns silent dummy sources."""
    created: list[RecordingSource] = []

    def factory(*_args: object, **_kwargs: object) -> RecordingSource:
        source = RecordingSource()
        created.append(source)
        return source

    with patch("discord.FFmpegPCMAudio", side_effect=factory) as mock:
        mock.created = created
        yield mock


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


def make_player(
    resolver: Optional[FakeResolver] = None,
) -> tuple[GuildPlayer, FakeVoiceClient, MagicMock]:
    bot = make_bot()
    player = GuildPlayer(guild_id=1, bot=bot, resolver=resolver or FakeResolver())
    vc = FakeVoiceClient()
    player.voice_client = vc
    return player, vc, bot


def make_track(video_id: str, duration_s: int = 180) -> Track:
    return Track(
        video_id=video_id, title=f"Title {video_id}", duration_s=duration_s, requester_id=1
    )


async def settle() -> None:
    await asyncio.sleep(0.05)


def age_playback(player: GuildPlayer, seconds: float) -> None:
    """Pretend the current track has been playing for `seconds`."""
    player._track_start_time = time.monotonic() - seconds


def outcomes(bot: MagicMock, video_id: str) -> list[str]:
    return [
        call.kwargs["outcome"]
        for call in bot.db.record_play_event.await_args_list
        if call.kwargs["video_id"] == video_id
    ]


class HangingResolver(FakeResolver):
    """Stream lookups never finish (a stuck yt-dlp call)."""

    async def get_stream(self, track: Track, *, background: bool = False) -> StreamInfo:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


class SelectiveResolver(FakeResolver):
    """Hangs or fails for chosen video IDs, works for the rest."""

    def __init__(self, hang: frozenset[str] = frozenset(), fail: frozenset[str] = frozenset()):
        super().__init__()
        self.hang = hang
        self.fail = fail

    async def get_stream(self, track: Track, *, background: bool = False) -> StreamInfo:
        if track.video_id in self.hang:
            await asyncio.Event().wait()
        if track.video_id in self.fail:
            raise BotCheckError()
        return await super().get_stream(track, background=background)


# --------------------------------------------------------------- skip / stop races


async def test_skip_works_while_stream_load_is_stuck(ffmpeg) -> None:
    player, _vc, _bot = make_player(HangingResolver())
    player.queue.add(make_track("stuck"))
    player.queue.add(make_track("next"))

    load = asyncio.create_task(player.play_next())
    await settle()
    assert player.state == PlayerState.LOADING

    skipped = await asyncio.wait_for(player.skip(), timeout=1.0)
    assert skipped is not None and skipped.video_id == "stuck"
    await settle()
    assert player.current_track is not None and player.current_track.video_id == "next"

    await player.stop()
    await asyncio.wait_for(load, timeout=1.0)


async def test_skip_records_a_single_play_event(ffmpeg) -> None:
    player, _vc, bot = make_player()
    player.queue.add(make_track("t1"))
    player.queue.add(make_track("t2"))
    await player.play_next()
    await player.skip()
    await settle()
    assert outcomes(bot, "t1") == ["skipped"]
    assert player.current_track is not None and player.current_track.video_id == "t2"


async def test_stop_leaves_queue_empty_even_with_loop_track(ffmpeg) -> None:
    player, _vc, bot = make_player()
    player.queue.loop_mode = LoopMode.TRACK
    player.queue.add(make_track("t1"))
    await player.play_next()
    await player.stop()
    await settle()
    assert len(player.queue) == 0
    assert player.state == PlayerState.IDLE
    assert player.current_track is None
    assert outcomes(bot, "t1") == ["stopped"]


async def test_double_skip_advances_twice_and_goes_idle(ffmpeg) -> None:
    player, _vc, bot = make_player()
    player.queue.add(make_track("t1"))
    player.queue.add(make_track("t2"))
    await player.play_next()

    first = await player.skip()
    second = await player.skip()
    await settle()

    assert first is not None and first.video_id == "t1"
    assert second is not None and second.video_id == "t2"
    assert player.state == PlayerState.IDLE
    assert outcomes(bot, "t1") == ["skipped"]
    assert outcomes(bot, "t2") == ["skipped"]
    assert await player.skip() is None  # nothing left: no error


async def test_concurrent_play_next_starts_one_worker(ffmpeg) -> None:
    player, _vc, _bot = make_player()
    player.queue.add(make_track("t1"))
    player.queue.add(make_track("t2"))
    await asyncio.gather(player.play_next(), player.play_next())
    assert len(ffmpeg.created) == 1
    assert player.current_track is not None and player.current_track.video_id == "t1"


async def test_stale_voice_callback_is_ignored(ffmpeg) -> None:
    player, vc, _bot = make_player()
    for name in ("t1", "t2", "t3"):
        player.queue.add(make_track(name))
    await player.play_next()
    stale_after = vc._after

    await player.skip()
    await settle()
    assert player.current_track is not None and player.current_track.video_id == "t2"

    assert stale_after is not None
    stale_after(None)  # the old source "ends" again after we already moved on
    await settle()
    assert player.current_track is not None and player.current_track.video_id == "t2"
    assert [t.video_id for t in player.queue.upcoming] == ["t3"]


# ------------------------------------------------------------- natural end / loops


async def test_natural_end_records_completed_and_plays_next(ffmpeg) -> None:
    player, vc, bot = make_player()
    player.queue.add(make_track("t1"))
    player.queue.add(make_track("t2"))
    await player.play_next()

    age_playback(player, 180)
    vc.finish()
    await settle()

    assert outcomes(bot, "t1") == ["completed"]
    assert player.current_track is not None and player.current_track.video_id == "t2"
    assert player.state == PlayerState.PLAYING


async def test_loop_track_replays_before_other_tracks(ffmpeg) -> None:
    player, vc, _bot = make_player()
    player.queue.loop_mode = LoopMode.TRACK
    player.queue.add(make_track("t1"))
    player.queue.add(make_track("t2"))
    await player.play_next()

    age_playback(player, 180)
    vc.finish()
    await settle()

    assert player.current_track is not None and player.current_track.video_id == "t1"
    assert [t.video_id for t in player.queue.upcoming] == ["t2"]


async def test_loop_queue_moves_finished_track_to_the_end(ffmpeg) -> None:
    player, vc, _bot = make_player()
    player.queue.loop_mode = LoopMode.QUEUE
    player.queue.add(make_track("t1"))
    player.queue.add(make_track("t2"))
    await player.play_next()

    age_playback(player, 180)
    vc.finish()
    await settle()

    assert player.current_track is not None and player.current_track.video_id == "t2"
    assert [t.video_id for t in player.queue.upcoming] == ["t1"]


async def test_skip_in_loop_track_moves_on(ffmpeg) -> None:
    player, _vc, _bot = make_player()
    player.queue.loop_mode = LoopMode.TRACK
    player.queue.add(make_track("t1"))
    player.queue.add(make_track("t2"))
    await player.play_next()
    await player.skip()
    await settle()
    assert player.current_track is not None and player.current_track.video_id == "t2"


# ------------------------------------------------------------- resume and failures


async def test_premature_end_resumes_at_the_elapsed_offset(ffmpeg) -> None:
    resolver = FakeResolver()
    player, vc, bot = make_player(resolver)
    player.queue.add(make_track("t1"))
    await player.play_next()

    age_playback(player, 20)
    vc.finish()
    await settle()

    assert resolver.invalidated == ["t1"]
    assert "-ss 20" in ffmpeg.call_args_list[-1].kwargs["before_options"]
    assert outcomes(bot, "t1") == []  # not finished yet, so nothing recorded
    assert player.current_track is not None and player.current_track.video_id == "t1"
    assert player.state == PlayerState.PLAYING
    assert player.elapsed_seconds >= 20


async def test_resume_attempts_are_bounded(ffmpeg) -> None:
    player, vc, bot = make_player()
    player.queue.loop_mode = LoopMode.TRACK  # a failing track must NOT be re-queued
    player.queue.add(make_track("t1"))
    player.queue.add(make_track("t2"))
    await player.play_next()

    for _ in range(3):  # 2 resumes, then give up
        vc.finish(RuntimeError("stream died"))
        await settle()

    assert outcomes(bot, "t1") == ["error"]
    assert player.current_track is not None and player.current_track.video_id == "t2"
    assert "t1" not in [t.video_id for t in player.queue.upcoming]


async def test_load_failure_skips_to_next_and_notifies_once(ffmpeg) -> None:
    resolver = SelectiveResolver(fail=frozenset({"bad1", "bad2"}))
    player, _vc, bot = make_player(resolver)
    player.text_channel = MagicMock()
    player.text_channel.send = AsyncMock()
    for name in ("bad1", "bad2", "good"):
        player.queue.add(make_track(name))

    await player.play_next()
    await settle()

    assert player.current_track is not None and player.current_track.video_id == "good"
    assert player.state == PlayerState.PLAYING
    assert outcomes(bot, "bad1") == ["error"]
    assert outcomes(bot, "bad2") == ["error"]
    assert player.text_channel.send.await_count == 1
    assert resolver.invalidated.count("bad1") >= 1  # retried once with a fresh URL


async def test_stuck_load_times_out_and_moves_on(ffmpeg, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(player_module, "STREAM_ATTEMPT_TIMEOUT_S", 0.05)
    player, _vc, bot = make_player(SelectiveResolver(hang=frozenset({"slow"})))
    player.queue.add(make_track("slow"))
    player.queue.add(make_track("fast"))

    await asyncio.wait_for(player.play_next(), timeout=3.0)

    assert player.current_track is not None and player.current_track.video_id == "fast"
    assert player.state == PlayerState.PLAYING
    await settle()
    assert outcomes(bot, "slow") == ["error"]


# ------------------------------------------------------------------------ watchdog


async def test_watchdog_recovers_a_silent_voice_client(ffmpeg) -> None:
    player, vc, _bot = make_player()
    player.queue.add(make_track("t1"))
    await player.play_next()
    assert len(ffmpeg.created) == 1

    vc._playing = False  # the voice player died without calling `after`
    player._watchdog_tick(time.monotonic())
    assert len(ffmpeg.created) == 1  # one silent tick is tolerated
    player._watchdog_tick(time.monotonic())
    await settle()

    assert len(ffmpeg.created) == 2  # resumed the same track
    assert player.state == PlayerState.PLAYING
    assert player.current_track is not None and player.current_track.video_id == "t1"


async def test_watchdog_abandons_a_load_stuck_past_the_limit(ffmpeg) -> None:
    player, _vc, bot = make_player(HangingResolver())
    player.queue.add(make_track("stuck"))
    player.queue.add(make_track("next"))
    asyncio.create_task(player.play_next())
    await settle()
    assert player.state == PlayerState.LOADING

    player._loading_since = time.monotonic() - (player_module.LOAD_WATCHDOG_S + 5)
    player._watchdog_tick(time.monotonic())
    await settle()

    assert player.current_track is not None and player.current_track.video_id == "next"
    assert outcomes(bot, "stuck") == ["error"]
    await player.stop()


# ------------------------------------------------------------------------- gapless


async def test_next_source_is_prebuilt_and_used_without_a_gap(ffmpeg) -> None:
    player, vc, _bot = make_player()
    player.queue.add(make_track("t1", duration_s=100))
    player.queue.add(make_track("t2", duration_s=100))
    await player.play_next()
    assert len(ffmpeg.created) == 1

    age_playback(player, 95)  # 5 s left, inside PREBUILD_LEAD_S
    player._watchdog_tick(time.monotonic())
    await settle()
    assert player._prepared is not None and player._prepared.track.video_id == "t2"
    assert len(ffmpeg.created) == 2

    age_playback(player, 100)
    vc.finish()
    await settle()

    assert player.current_track is not None and player.current_track.video_id == "t2"
    assert len(ffmpeg.created) == 2  # the pre-built process was reused, none spawned at the switch


async def test_stale_prepared_source_is_discarded(ffmpeg) -> None:
    player, _vc, _bot = make_player()
    player.queue.add(make_track("t1", duration_s=100))
    player.queue.add(make_track("t2", duration_s=100))
    await player.play_next()
    age_playback(player, 95)
    player._watchdog_tick(time.monotonic())
    await settle()
    prebuilt = ffmpeg.created[1]
    assert prebuilt.cleaned is False

    player.queue.clear()  # /clear while a pre-built FFmpeg process is waiting
    player._watchdog_tick(time.monotonic())

    assert player._prepared is None
    assert prebuilt.cleaned is True


# ---------------------------------------------------------------------- the buffer


async def test_buffered_file_is_played_from_disk(ffmpeg, tmp_path: Path) -> None:
    async def write_file(_url: str, dest: Path) -> None:
        dest.write_bytes(b"audio")

    buffer = AudioBuffer(tmp_path / "cache", 10_000_000, downloader=write_file)
    player, vc, _bot = make_player()
    player.buffer = buffer
    player.queue.add(make_track("track_a1"))
    player.queue.add(make_track("track_b2"))
    await player.play_next()

    assert player._prefetch_task is not None
    await player._prefetch_task
    await buffer.wait_idle()
    expected = buffer.get("track_b2")
    assert expected is not None

    age_playback(player, 180)
    vc.finish()
    await settle()

    last_call = ffmpeg.call_args_list[-1]
    assert last_call.args[0] == str(expected)
    assert last_call.kwargs["before_options"] is None  # no network reconnect flags for a file


async def test_buffer_failure_does_not_block_playback(ffmpeg, tmp_path: Path) -> None:
    async def failing(_url: str, _dest: Path) -> None:
        raise OSError("disk full")

    buffer = AudioBuffer(tmp_path / "cache", 10_000_000, downloader=failing)
    player, vc, _bot = make_player()
    player.buffer = buffer
    player.queue.add(make_track("track_a1"))
    player.queue.add(make_track("track_b2"))
    await player.play_next()
    assert player._prefetch_task is not None
    await player._prefetch_task
    await buffer.wait_idle()

    age_playback(player, 180)
    vc.finish()
    await settle()

    assert player.current_track is not None and player.current_track.video_id == "track_b2"
    assert ffmpeg.call_args_list[-1].args[0].startswith("http://stream.test/")


async def test_stop_clears_the_buffer(ffmpeg, tmp_path: Path) -> None:
    buffer = AudioBuffer(tmp_path / "cache", 10_000_000)
    buffer.clear = AsyncMock()  # type: ignore[method-assign]
    player, _vc, _bot = make_player()
    player.buffer = buffer
    player.queue.add(make_track("t1"))
    await player.play_next()
    await player.stop()
    buffer.clear.assert_awaited_once()
