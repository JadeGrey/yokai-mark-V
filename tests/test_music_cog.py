"""Tests for the /play command's queueing order."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from yokai.cogs.music import MusicCog, _eta_seconds, _track_url
from yokai.music.models import MusicQueue, PlayerState, Track

PLAYLIST_URL = "https://www.youtube.com/playlist?list=PLFgquLnL59alCl_2TQvOiD5Vgm1hCaGSI"


def make_interaction() -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild_id = 1
    interaction.guild = None
    user = MagicMock(spec=discord.Member)
    user.id = 111
    user.voice = MagicMock()
    user.voice.channel = MagicMock(spec=discord.VoiceChannel)
    interaction.user = user
    interaction.response.defer = AsyncMock()
    return interaction


@pytest.mark.asyncio
async def test_playlist_on_idle_player_queues_every_track_before_playing() -> None:
    """If the first track fails, the worker must find the rest already in the queue."""
    tracks = [Track(video_id=f"vid{i}", title=f"Song {i}", duration_s=100) for i in range(3)]
    player = MagicMock()
    player.state = PlayerState.IDLE
    player.voice_client = None
    player.queue = MusicQueue(max_size=50)
    player.connect = AsyncMock()
    player.resolver.resolve_url = AsyncMock(return_value=(tracks, True))
    queued_at_play: list[list[str]] = []

    async def play_next() -> None:
        queued_at_play.append([t.video_id for t in player.queue.upcoming])
        player.queue.pop_next()

    player.play_next = AsyncMock(side_effect=play_next)

    bot = MagicMock()
    bot.user = None
    bot.config.max_playlist_tracks = 100
    bot.get_player.return_value = player
    cog = MusicCog(bot)

    with patch("yokai.cogs.music.send", new=AsyncMock()) as send:
        await cog.play.callback(cog, make_interaction(), query=PLAYLIST_URL)

    assert queued_at_play == [["vid0", "vid1", "vid2"]]
    assert [t.video_id for t in player.queue.upcoming] == ["vid1", "vid2"]
    embed = send.await_args.kwargs["embed"]
    assert embed.title == "📑 Playlist loaded"
    fields = {f.name: f.value for f in embed.fields if f.name != "​"}
    assert fields == {"Loaded": "`3`", "Queued": "`2`", "Now playing": "Song 0"}


def make_cog_and_player(state: PlayerState = PlayerState.IDLE) -> tuple[MusicCog, MagicMock]:
    player = MagicMock()
    player.state = state
    player.voice_client = None
    player.current_track = None
    player.elapsed_seconds = 0
    player.queue = MusicQueue(max_size=50)
    player.connect = AsyncMock()
    player.warm_stream = MagicMock()
    player.maybe_prefetch = MagicMock()
    player.resolver.resolve_query = AsyncMock()

    async def play_next() -> None:
        player.queue.pop_next()

    player.play_next = AsyncMock(side_effect=play_next)
    bot = MagicMock()
    bot.user = None
    bot.config.max_playlist_tracks = 100
    bot.config.max_queue_size = 50
    bot.get_player.return_value = player
    return MusicCog(bot), player


def test_track_url_hides_pending_spotify_imports() -> None:
    real = Track(video_id="abc12345678", title="T")
    pending = Track(video_id="sp:xyz", title="T", is_pending_match=True)
    assert _track_url(real) == "https://www.youtube.com/watch?v=abc12345678"
    assert _track_url(pending) is None


def test_pending_spotify_track_gets_no_link() -> None:
    pending = Track(video_id="sp:xyz", title="T", is_pending_match=True)
    assert _track_url(pending) is None


def test_eta_counts_remaining_current_plus_tracks_ahead() -> None:
    player = MagicMock()
    player.current_track = Track(video_id="cur00000001", title="C", duration_s=200)
    player.elapsed_seconds = 50
    player.queue = MusicQueue(max_size=10)
    for i in range(3):
        player.queue.add(Track(video_id=f"q{i}000000000", title=f"Q{i}", duration_s=100))
    assert _eta_seconds(player, 1) == 150
    assert _eta_seconds(player, 3) == 150 + 200
    player.current_track = None
    assert _eta_seconds(player, 2) == 100


@pytest.mark.asyncio
async def test_single_track_idle_play_shows_a_linked_now_playing_card() -> None:
    cog, player = make_cog_and_player(PlayerState.IDLE)
    track = Track(
        video_id="dQw4w9WgXcQ",
        title="Never Gonna",
        artist="Rick",
        duration_s=213,
        requester_id=111,
        origin="search",
    )
    player.resolver.resolve_query.return_value = track
    with patch("yokai.cogs.music.send", new=AsyncMock()) as send:
        await cog.play.callback(cog, make_interaction(), query="never gonna")
    embed = send.await_args.kwargs["embed"]
    assert embed.title == "▶️ Never Gonna"
    assert embed.url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    fields = {f.name: f.value for f in embed.fields if f.name != "​"}
    assert fields["Requested by"] == "<@111>"
    assert fields["Source"] == "Search"


@pytest.mark.asyncio
async def test_queued_track_shows_position_eta_and_requester() -> None:
    cog, player = make_cog_and_player(PlayerState.PLAYING)
    player.current_track = Track(video_id="cur00000001", title="Current", duration_s=200)
    player.elapsed_seconds = 50
    track = Track(
        video_id="dQw4w9WgXcQ",
        title="Next Up",
        artist="Rick",
        duration_s=213,
        requester_id=111,
        origin="link",
    )
    player.resolver.resolve_query.return_value = track
    with patch("yokai.cogs.music.send", new=AsyncMock()) as send:
        await cog.play.callback(cog, make_interaction(), query="next up")
    embed = send.await_args.kwargs["embed"]
    assert embed.title == "📑 Queued"
    fields = {f.name: f.value for f in embed.fields if f.name != "​"}
    assert fields["Position"] == "`#1`"
    assert fields["Plays in"] == "`~2m 30s`"
    assert fields["Requested by"] == "<@111>"
    assert fields["Source"] == "YouTube link"
    assert "[Next Up](https://www.youtube.com/watch?v=dQw4w9WgXcQ)" in embed.description


@pytest.mark.asyncio
async def test_queue_full_error_has_a_specific_title() -> None:
    cog, player = make_cog_and_player(PlayerState.PLAYING)
    player.queue = MusicQueue(max_size=0)
    player.resolver.resolve_query.return_value = Track(video_id="dQw4w9WgXcQ", title="T")
    with patch("yokai.cogs.music.send", new=AsyncMock()) as send:
        await cog.play.callback(cog, make_interaction(), query="something")
    assert send.await_args.kwargs["embed"].title == "❌ Queue is full"


@pytest.mark.asyncio
async def test_unsupported_link_has_a_specific_title_and_does_not_join_voice() -> None:
    cog, player = make_cog_and_player(PlayerState.IDLE)
    with patch("yokai.cogs.music.send", new=AsyncMock()) as send:
        await cog.play.callback(cog, make_interaction(), query="https://evil.example/x")
    assert send.await_args.kwargs["embed"].title == "❌ Unsupported link"
    assert send.await_args.kwargs["ephemeral"] is True
    player.connect.assert_not_called()


@pytest.mark.asyncio
async def test_nowplaying_command_links_and_labels_the_source() -> None:
    cog, player = make_cog_and_player(PlayerState.PLAYING)
    player.current_track = Track(
        video_id="dQw4w9WgXcQ",
        title="Now",
        artist="Rick",
        duration_s=100,
        requester_id=5,
        origin="recommendation",
    )
    player.elapsed_seconds = 30
    with patch("yokai.cogs.music.send", new=AsyncMock()) as send:
        await cog.nowplaying.callback(cog, make_interaction())
    embed = send.await_args.kwargs["embed"]
    assert embed.url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    fields = {f.name: f.value for f in embed.fields if f.name != "​"}
    assert fields["Source"] == "Recommendation"
