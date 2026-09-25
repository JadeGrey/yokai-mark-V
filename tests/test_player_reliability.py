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


async def test_stale_callback_after_watchdog_resume_is_ignored(ffmpeg) -> None:
    """The same Track object is current again, so only the generation can reject the callback."""
    resolver = FakeResolver()
    player, vc, _bot = make_player(resolver)
    player.queue.add(make_track("t1"))
    player.queue.add(make_track("t2"))
    await player.play_next()
    pre_resume_after = vc._after

    vc._playing = False  # the voice player died without calling `after`
    player._watchdog_tick(time.monotonic())
    player._watchdog_tick(time.monotonic())
    await settle()
    assert len(ffmpeg.created) == 2
    assert player.current_track is not None and player.current_track.video_id == "t1"

    assert pre_resume_after is not None
    pre_resume_after(None)  # the stalled player thread finally reports the old source's end
    await settle()

    assert len(ffmpeg.created) == 2  # no second resume
    assert resolver.invalidated == ["t1"]
    assert player.current_track is not None and player.current_track.video_id == "t1"
    assert [t.video_id for t in player.queue.upcoming] == ["t2"]
    assert player.state == PlayerState.PLAYING


async def test_stale_callback_after_loop_track_replay_is_ignored(ffmpeg) -> None:
    resolver = FakeResolver()
    player, vc, bot = make_player(resolver)
    player.queue.loop_mode = LoopMode.TRACK
    player.queue.add(make_track("t1"))
    player.queue.add(make_track("t2"))
    await player.play_next()
    first_after = vc._after
    first_track = player.current_track

    age_playback(player, 180)
    vc.finish()
    await settle()
    assert player.current_track is first_track  # same Track object replays
    assert len(ffmpeg.created) == 2

    assert first_after is not None
    first_after(None)  # the first play's callback arrives again, late
    await settle()

    assert len(ffmpeg.created) == 2  # no resume, no advance
    assert resolver.invalidated == []
    assert player.current_track is first_track
    assert [t.video_id for t in player.queue.upcoming] == ["t2"]
    assert outcomes(bot, "t1") == ["completed"]


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


async def test_overstated_duration_ends_as_completed_and_stays_in_loop(ffmpeg) -> None:
    """A 188 s upload whose metadata says 200 s must not be treated as an error."""
    resolver = FakeResolver()
    player, vc, bot = make_player(resolver)
    player.queue.loop_mode = LoopMode.QUEUE
    player.queue.add(make_track("t1", duration_s=200))
    player.queue.add(make_track("t2"))
    await player.play_next()

    age_playback(player, 188)
    vc.finish()  # clean end at 188 s: looks premature, so one resume is tried
    await settle()
    assert player.current_track is not None and player.current_track.video_id == "t1"

    vc.finish()  # the resumed source (seeked to 188 s) ends at once: the stream is over
    await settle()

    assert outcomes(bot, "t1") == ["completed"]
    assert resolver.invalidated == ["t1"]  # at most one resume
    assert len(ffmpeg.created) == 3  # t1, one resume of t1, then t2
    assert player.current_track is not None and player.current_track.video_id == "t2"
    assert [t.video_id for t in player.queue.upcoming] == ["t1"]


async def test_clean_premature_end_after_exhausted_resumes_is_completed(ffmpeg) -> None:
    resolver = FakeResolver()
    player, vc, bot = make_player(resolver)
    player.queue.add(make_track("t1"))
    player.queue.add(make_track("t2"))
    await player.play_next()

    age_playback(player, 20)
    vc.finish()  # cut off at 20 s: resume 1
    await settle()
    for _ in range(2):  # each resumed source plays 10 s more, then is cut off again
        age_playback(player, 10)
        vc.finish()
        await settle()

    assert resolver.invalidated == ["t1", "t1"]  # both resumes were used
    assert outcomes(bot, "t1") == ["completed"]  # a clean end is not an error
    assert player.current_track is not None and player.current_track.video_id == "t2"


def attach_channel(player: GuildPlayer) -> AsyncMock:
    player.text_channel = MagicMock()
    player.text_channel.send = AsyncMock()
    return player.text_channel.send


async def test_dead_stream_in_loop_track_ends_as_error_without_replaying(ffmpeg) -> None:
    """Production delivers `after(None)` when FFmpeg dies: it must still count as a failure."""
    player, vc, bot = make_player()
    send = attach_channel(player)
    player.queue.loop_mode = LoopMode.TRACK
    player.queue.add(make_track("dead1"))
    player.queue.add(make_track("t2"))
    await player.play_next()

    for _ in range(12):
        if player.current_track is None or player.current_track.video_id != "dead1":
            break
        vc.finish()  # no error argument, exactly like discord.py with a PCMVolumeTransformer
        await settle()

    assert outcomes(bot, "dead1") == ["error"]
    assert send.await_count == 1
    assert "dead1" not in [t.video_id for t in player.queue.upcoming]
    assert player.current_track is not None and player.current_track.video_id == "t2"
    assert len(ffmpeg.created) == 1 + player_module.MAX_RESUME_ATTEMPTS + 1  # bounded


async def test_dead_stream_with_loop_off_records_error_and_notifies(ffmpeg) -> None:
    player, vc, bot = make_player(SelectiveResolver(fail=frozenset({"bad"})))
    send = attach_channel(player)
    player.queue.add(make_track("dead1"))
    await player.play_next()

    for _ in range(player_module.MAX_RESUME_ATTEMPTS + 1):
        vc.finish()
        await settle()

    assert outcomes(bot, "dead1") == ["error"]
    assert send.await_count == 1
    assert player.state == PlayerState.IDLE

    # Going idle ended the burst, so a later failure (here: a load failure, which never
    # starts playback to reset the flag) is notified again.
    player.queue.add(make_track("bad"))
    await player.play_next()
    await settle()
    assert outcomes(bot, "bad") == ["error"]
    assert send.await_count == 2


async def test_ffmpeg_error_on_the_wrapped_source_is_surfaced(ffmpeg) -> None:
    """discord.py only checks `_current_error` on the transformer; we read the wrapped source."""
    resolver = FakeResolver()
    player, vc, bot = make_player(resolver)
    player.queue.add(make_track("t1"))
    await player.play_next()

    # Near the end, a clean end would be "completed"; the FFmpeg error must force a resume.
    ffmpeg.created[0]._current_error = RuntimeError("FFmpeg exited with code 1")
    age_playback(player, 175)
    vc.finish()
    await settle()

    assert resolver.invalidated == ["t1"]
    assert outcomes(bot, "t1") == []
    assert "-ss 175" in ffmpeg.call_args_list[-1].kwargs["before_options"]


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


async def test_a_later_failure_burst_is_notified_again(ffmpeg) -> None:
    resolver = SelectiveResolver(fail=frozenset({"bad1", "bad2", "bad3"}))
    player, _vc, _bot = make_player(resolver)
    player.text_channel = MagicMock()
    player.text_channel.send = AsyncMock()

    player.queue.add(make_track("bad1"))
    player.queue.add(make_track("bad2"))
    await player.play_next()
    await settle()
    assert player.state == PlayerState.IDLE
    assert player.text_channel.send.await_count == 1  # one notice for the whole burst

    player.queue.add(make_track("bad3"))  # a separate, later failure
    await player.play_next()
    await settle()
    assert player.state == PlayerState.IDLE
    assert player.text_channel.send.await_count == 2


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


async def test_url_sources_get_a_read_timeout(ffmpeg) -> None:
    player, _vc, _bot = make_player()
    player.queue.add(make_track("t1"))
    await player.play_next()
    assert "-rw_timeout 15000000" in ffmpeg.call_args_list[-1].kwargs["before_options"]


async def test_watchdog_recovers_a_stream_stalled_past_its_end(ffmpeg) -> None:
    """A dead TCP read keeps the voice client "playing" forever; the watchdog moves on."""
    resolver = FakeResolver()
    player, vc, bot = make_player(resolver)
    player.queue.add(make_track("t1", duration_s=180))
    player.queue.add(make_track("t2"))
    await player.play_next()
    first = ffmpeg.created[0]

    age_playback(player, 180 + player_module.STALL_GRACE_S - 2)
    player._watchdog_tick(time.monotonic())
    await settle()
    assert player.current_track is not None and player.current_track.video_id == "t1"

    age_playback(player, 180 + player_module.STALL_GRACE_S + 2)
    assert vc.is_playing()
    stops: list[bool] = []
    real_stop = vc.stop

    def spy_stop() -> None:
        stops.append(vc.is_playing())
        real_stop()

    vc.stop = spy_stop  # type: ignore[method-assign]
    player._watchdog_tick(time.monotonic())
    await settle()

    assert stops == [True]  # the stuck source was stopped (discord.py refuses a second play)
    assert outcomes(bot, "t1") == ["completed"]
    assert player.current_track is not None and player.current_track.video_id == "t2"
    assert player.state == PlayerState.PLAYING
    assert resolver.invalidated == []  # not resumed
    assert len(ffmpeg.created) == 2  # t1, then t2
    assert first is ffmpeg.created[0]


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


def buffered_player(tmp_path: Path, *video_ids: str) -> tuple[GuildPlayer, FakeVoiceClient, Path]:
    """A player whose buffer already holds finished files for `video_ids`."""
    cache = tmp_path / "cache"
    cache.mkdir()
    for video_id in video_ids:
        (cache / f"{video_id}.mka").write_bytes(b"audio")
    player, vc, _bot = make_player()
    player.buffer = AudioBuffer(cache, 10_000_000)
    return player, vc, cache


async def test_resume_does_not_replay_the_buffer_file(ffmpeg, tmp_path: Path) -> None:
    player, vc, cache = buffered_player(tmp_path, "track_a1")
    player.queue.add(make_track("track_a1"))
    await player.play_next()
    assert ffmpeg.call_args_list[-1].args[0] == str(cache / "track_a1.mka")

    age_playback(player, 60)
    vc.finish()  # a (possibly truncated) local file ended at 60 s of 180
    await settle()

    last_call = ffmpeg.call_args_list[-1]
    assert last_call.args[0] == "http://stream.test/track_a1"
    assert "-ss 60" in last_call.kwargs["before_options"]
    assert not (cache / "track_a1.mka").exists()  # the bad file is gone for later plays


async def test_resume_of_a_url_source_ignores_a_buffer_file(ffmpeg, tmp_path: Path) -> None:
    """A file finished while the track played from its URL: a seek into it is not trusted."""
    player, vc, cache = buffered_player(tmp_path)
    player.queue.add(make_track("track_a1"))
    await player.play_next()
    assert ffmpeg.call_args_list[-1].args[0] == "http://stream.test/track_a1"
    (cache / "track_a1.mka").write_bytes(b"audio")

    age_playback(player, 60)
    vc.finish()
    await settle()

    assert ffmpeg.call_args_list[-1].args[0] == "http://stream.test/track_a1"
    assert "-ss 60" in ffmpeg.call_args_list[-1].kwargs["before_options"]


async def test_instant_end_of_a_buffer_file_deletes_it_and_uses_the_url(
    ffmpeg, tmp_path: Path
) -> None:
    player, vc, cache = buffered_player(tmp_path, "track_a1")
    player.queue.add(make_track("track_a1"))
    await player.play_next()

    vc.finish()  # the file "ends" at 0 s: a bad file, retried from offset 0
    await settle()

    assert ffmpeg.call_args_list[-1].args[0] == "http://stream.test/track_a1"
    assert not (cache / "track_a1.mka").exists()


async def test_second_stream_attempt_skips_the_buffer_file(ffmpeg, tmp_path: Path) -> None:
    player, _vc, cache = buffered_player(tmp_path, "track_a1")
    player.queue.add(make_track("track_a1"))
    real_factory = ffmpeg.side_effect
    calls: list[object] = []

    def fail_first(*args: object, **kwargs: object) -> RecordingSource:
        calls.append(args[0])
        if len(calls) == 1:
            raise RuntimeError("ffmpeg could not start")
        return real_factory(*args, **kwargs)

    ffmpeg.side_effect = fail_first
    await player.play_next()

    assert calls == [str(cache / "track_a1.mka"), "http://stream.test/track_a1"]
    assert player.state == PlayerState.PLAYING


async def test_stop_clears_the_buffer(ffmpeg, tmp_path: Path) -> None:
    buffer = AudioBuffer(tmp_path / "cache", 10_000_000)
    buffer.clear = AsyncMock()  # type: ignore[method-assign]
    player, _vc, _bot = make_player()
    player.buffer = buffer
    player.queue.add(make_track("t1"))
    await player.play_next()
    await player.stop()
    buffer.clear.assert_awaited_once()
