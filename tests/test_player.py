"""Tests for GuildPlayer state machine, prefetching, retry logic, and play events."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from yokai.music.models import PlayerState, StreamInfo, Track
from yokai.music.player import GuildPlayer
from yokai.music.resolvers.base import Resolver


class DummyAudioSource(discord.AudioSource):
    """Real AudioSource subclass for discord.py player unit tests."""

    def read(self) -> bytes:
        return b""

    def is_opus(self) -> bool:
        return False

    def cleanup(self) -> None:
        pass


class FakeResolver(Resolver):
    """Test fake for Resolver interface."""

    def __init__(self) -> None:
        self.stream_call_count = 0

    async def resolve_query(self, query: str, requester_id: int) -> Track:
        return Track(
            video_id="fake_q",
            title="Query Result",
            duration_s=180,
            requester_id=requester_id,
        )

    async def resolve_url(
        self, url: str, requester_id: int, max_tracks: int = 100
    ) -> tuple[Track | list[Track], bool]:
        return (
            Track(
                video_id="fake_u",
                title="URL Result",
                duration_s=200,
                requester_id=requester_id,
            ),
            False,
        )

    async def get_stream(self, track: Track) -> StreamInfo:
        self.stream_call_count += 1
        return StreamInfo(url=f"http://stream.test/{track.video_id}")


class FakeVoiceClient:
    """Mock discord.VoiceClient."""

    def __init__(self) -> None:
        self._playing = False
        self._paused = False
        self._after = None
        self.channel = MagicMock(spec=discord.VoiceChannel)
        self.channel.name = "General"
        self.channel.members = []

    def is_connected(self) -> bool:
        return True

    def is_playing(self) -> bool:
        return self._playing

    def is_paused(self) -> bool:
        return self._paused

    def play(self, source: discord.AudioSource, *, after: object = None) -> None:
        self._playing = True
        self._paused = False
        self._after = after

    def pause(self) -> None:
        self._paused = True
        self._playing = False

    def resume(self) -> None:
        self._paused = False
        self._playing = True

    def stop(self) -> None:
        self._playing = False
        self._paused = False
        if self._after:
            cb = self._after
            self._after = None
            cb(None)

    async def disconnect(self, *, force: bool = False) -> None:
        self._playing = False


@pytest.mark.asyncio
async def test_player_state_transitions_and_controls() -> None:
    mock_bot = MagicMock()
    mock_bot.config.max_queue_size = 50
    mock_bot.config.ffmpeg_path = None
    mock_bot.config.alone_disconnect_seconds = 120
    mock_bot.config.idle_disconnect_seconds = 300
    mock_bot.loop = asyncio.get_running_loop()
    mock_bot.presence = AsyncMock()
    mock_bot.db = AsyncMock()

    resolver = FakeResolver()
    player = GuildPlayer(guild_id=1, bot=mock_bot, resolver=resolver)
    player.voice_client = FakeVoiceClient()

    # Initial state
    assert player.state == PlayerState.IDLE
    assert player.is_playing is False
    assert player.is_paused is False

    # Add track and play
    t1 = Track(video_id="t1", title="Track 1", duration_s=180, requester_id=111)
    t2 = Track(video_id="t2", title="Track 2", duration_s=200, requester_id=222)
    player.queue.add(t1)
    player.queue.add(t2)

    with patch("discord.FFmpegPCMAudio", return_value=DummyAudioSource()):
        await player.play_next()

    assert player.state == PlayerState.PLAYING
    assert player.current_track == t1
    assert player.is_playing is True
    assert resolver.stream_call_count == 1

    # Pause
    assert player.pause() is True
    assert player.state == PlayerState.PAUSED
    assert player.is_paused is True

    # Resume
    assert player.resume() is True
    assert player.state == PlayerState.PLAYING
    assert player.is_playing is True

    # Skip
    with patch("discord.FFmpegPCMAudio", return_value=DummyAudioSource()):
        skipped = await player.skip()
        await asyncio.sleep(0.01)
    assert skipped == t1
    assert player.current_track == t2

    # Stop
    await player.stop()
    assert player.state == PlayerState.IDLE
    assert player.current_track is None
    assert len(player.queue) == 0


@pytest.mark.asyncio
async def test_player_prefetch_cache_hit() -> None:
    mock_bot = MagicMock()
    mock_bot.config.max_queue_size = 50
    mock_bot.config.ffmpeg_path = None
    mock_bot.config.alone_disconnect_seconds = 120
    mock_bot.config.idle_disconnect_seconds = 300
    mock_bot.presence = AsyncMock()
    mock_bot.db = AsyncMock()

    resolver = FakeResolver()
    player = GuildPlayer(guild_id=1, bot=mock_bot, resolver=resolver)
    player.voice_client = FakeVoiceClient()

    t1 = Track(video_id="song_a", title="Song A", duration_s=180)
    t2 = Track(video_id="song_b", title="Song B", duration_s=180)
    player.queue.add(t1)
    player.queue.add(t2)

    with patch("discord.FFmpegPCMAudio", return_value=DummyAudioSource()):
        await player.play_next()

    # Pre-populate prefetch cache as if background task completed
    player._prefetched = ("song_b", StreamInfo(url="http://cached.url"))

    # Next track should hit the prefetch cache without calling resolver.get_stream again
    calls_before = resolver.stream_call_count
    with patch("discord.FFmpegPCMAudio", return_value=DummyAudioSource()):
        await player.play_next()

    assert resolver.stream_call_count == calls_before
    assert player.current_track == t2


@pytest.mark.asyncio
async def test_player_volume_clamping() -> None:
    mock_bot = MagicMock()
    mock_bot.config.max_queue_size = 50
    mock_bot.presence = AsyncMock()
    resolver = FakeResolver()

    player = GuildPlayer(guild_id=1, bot=mock_bot, resolver=resolver)

    assert player.set_volume(150) == 100
    assert player.volume == 1.0

    assert player.set_volume(-20) == 0
    assert player.volume == 0.0

    assert player.set_volume(45) == 45
    assert player.volume == 0.45


@pytest.mark.asyncio
async def test_alone_timer_management() -> None:
    mock_bot = MagicMock()
    mock_bot.config.max_queue_size = 50
    mock_bot.config.alone_disconnect_seconds = 120
    mock_bot.loop = asyncio.get_event_loop()
    mock_bot.presence = AsyncMock()
    resolver = FakeResolver()

    player = GuildPlayer(guild_id=1, bot=mock_bot, resolver=resolver)
    vc = FakeVoiceClient()
    player.voice_client = vc

    # 1. Voice channel has only bots (alone) -> starts alone timer
    bot_member = MagicMock()
    bot_member.bot = True
    vc.channel.members = [bot_member]

    player.on_voice_member_update()
    assert player._alone_timer is not None

    # 2. Human joins -> alone timer cancelled
    human_member = MagicMock()
    human_member.bot = False
    vc.channel.members.append(human_member)

    player.on_voice_member_update()
    assert player._alone_timer is None


@pytest.mark.asyncio
async def test_maybe_prefetch() -> None:
    mock_bot = MagicMock()
    mock_bot.config.max_queue_size = 50
    mock_bot.presence = AsyncMock()
    resolver = FakeResolver()

    player = GuildPlayer(guild_id=1, bot=mock_bot, resolver=resolver)
    player.voice_client = FakeVoiceClient()
    t1 = Track(video_id="v1", title="T1", duration_s=100)
    t2 = Track(video_id="v2", title="T2", duration_s=100)
    player.queue.add(t1)
    player.queue.add(t2)

    # 1. When idle, maybe_prefetch does nothing
    player.state = PlayerState.IDLE
    player.maybe_prefetch()
    assert player._prefetch_task is None

    # 2. When playing, maybe_prefetch triggers prefetch
    player.state = PlayerState.PLAYING
    player.maybe_prefetch()
    assert player._prefetch_task is not None
    await player._prefetch_task
    assert player._prefetched is not None
    assert player._prefetched[0] == "v1"
