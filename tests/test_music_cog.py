"""Tests for the /play command's queueing order."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from yokai.cogs.music import MusicCog
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
    assert "Playlist Loaded (3 tracks)" in embed.title
    assert "Started playback with **Song 0** and queued **2** additional tracks." in (
        embed.description
    )
