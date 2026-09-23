"""Unit tests for recommendations filtering, caching, seed selection, and RecommendView."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from yokai.music.models import MusicQueue, Track
from yokai.music.player import GuildPlayer
from yokai.music.recommend.ytmusic import YTMusicRadioRecommender
from yokai.ui.views import RecommendView


@pytest.fixture
def mock_ytmusic_data() -> dict[str, Any]:
    return {
        "tracks": [
            # 1. Seed duplicate (same videoId)
            {
                "videoId": "seed_vid",
                "title": "Seed Song",
                "artists": [{"name": "Seed Artist"}],
                "duration_seconds": 200,
            },
            # 2. Excluded track (e.g. already in queue)
            {
                "videoId": "excluded_vid",
                "title": "Queued Track",
                "artists": [{"name": "Queued Artist"}],
                "duration_seconds": 180,
            },
            # 3. Valid candidate 1
            {
                "videoId": "rec_vid_1",
                "title": "Valid Rec 1",
                "artists": [{"name": "Artist 1"}],
                "duration_seconds": 210,
                "videoType": "MUSIC_VIDEO_TYPE_ATV",
            },
            # 4. Normalized duplicate of valid candidate 1 (different videoId)
            {
                "videoId": "rec_vid_1_dup",
                "title": "Valid Rec 1 (Official Video)",
                "artists": [{"name": "Artist 1"}],
                "duration_seconds": 210,
            },
            # 5. Non-music video type (podcast episode)
            {
                "videoId": "podcast_vid",
                "title": "A Talk Show Episode",
                "artists": [{"name": "Host"}],
                "duration_seconds": 300,
                "videoType": "MUSIC_VIDEO_TYPE_PODCAST_EPISODE",
            },
            # 6. Over 10-minute track (>600s)
            {
                "videoId": "long_vid",
                "title": "Super Long Mix",
                "artists": [{"name": "DJ Long"}],
                "duration_seconds": 750,
            },
            # 7. Valid candidate 2 (with length string)
            {
                "videoId": "rec_vid_2",
                "title": "Valid Rec 2",
                "artists": [{"name": "Artist 2"}],
                "length": "4:15",
            },
            # 8. Valid candidate 3
            {
                "videoId": "rec_vid_3",
                "title": "Valid Rec 3",
                "artists": [{"name": "Artist 3"}],
                "duration_seconds": 195,
            },
        ]
    }


@pytest.mark.asyncio
async def test_recommender_filtering(mock_ytmusic_data: dict[str, Any]) -> None:
    recommender = YTMusicRadioRecommender()
    seed = Track(video_id="seed_vid", title="Seed Song", artist="Seed Artist", duration_s=200)
    exclude = {"excluded_vid"}

    with patch.object(recommender, "_fetch_radio", return_value=mock_ytmusic_data):
        rec = await recommender.recommend(seed=seed, count=5, exclude=exclude)

        assert rec.seed == seed
        assert rec.count == 3  # rec_vid_1, rec_vid_2, rec_vid_3
        vids = [t.video_id for t in rec.tracks]

        # Assert seed was dropped
        assert "seed_vid" not in vids
        # Assert excluded was dropped
        assert "excluded_vid" not in vids
        # Assert duplicate was dropped
        assert "rec_vid_1_dup" not in vids
        # Assert podcast was dropped
        assert "podcast_vid" not in vids
        # Assert >10min track was dropped
        assert "long_vid" not in vids

        # Assert valid candidates included
        assert vids == ["rec_vid_1", "rec_vid_2", "rec_vid_3"]
        assert rec.tracks[1].duration_s == 255  # 4:15 -> 255s


@pytest.mark.asyncio
async def test_recommender_10min_cache(mock_ytmusic_data: dict[str, Any]) -> None:
    recommender = YTMusicRadioRecommender()
    seed = Track(video_id="seed_vid", title="Seed Song", duration_s=200)

    with patch.object(recommender, "_fetch_radio", return_value=mock_ytmusic_data) as mock_fetch:
        # First call fetches and caches
        res1 = await recommender.recommend(seed=seed, count=2)
        assert mock_fetch.call_count == 1
        assert res1.count == 2

        # Second call hits in-memory cache
        res2 = await recommender.recommend(seed=seed, count=2)
        assert mock_fetch.call_count == 1
        assert res2.count == 2


@pytest.mark.asyncio
async def test_recommend_view_permissions_and_queue_action() -> None:
    mock_bot = MagicMock()
    mock_bot.config.max_queue_size = 50
    mock_bot.presence = AsyncMock()

    player = GuildPlayer(guild_id=1, bot=mock_bot, resolver=MagicMock())
    player.queue = MusicQueue(max_size=50)

    t1 = Track(video_id="r1", title="Rec 1", duration_s=100)
    t2 = Track(video_id="r2", title="Rec 2", duration_s=120)

    view = RecommendView(
        player=player,
        tracks=[t1, t2],
        requester_id=111,
        seed_title="Seed Track",
    )

    # 1. Permission check: requester is authorized
    interaction_requester = MagicMock(spec=discord.Interaction)
    user_requester = MagicMock(spec=discord.Member)
    user_requester.id = 111
    interaction_requester.user = user_requester
    assert await view.interaction_check(interaction_requester) is True

    # 2. Permission check: unauthorized stranger outside voice channel
    interaction_stranger = MagicMock(spec=discord.Interaction)
    user_stranger = MagicMock(spec=discord.Member)
    user_stranger.id = 999
    user_stranger.voice = None
    interaction_stranger.user = user_stranger
    interaction_stranger.response.is_done.return_value = False
    interaction_stranger.response.send_message = AsyncMock()

    assert await view.interaction_check(interaction_stranger) is False
    interaction_stranger.response.send_message.assert_called_once()

    # 3. Press button: queues all tracks, transitions embed, disables button
    interaction_press = MagicMock(spec=discord.Interaction)
    interaction_press.user = user_requester
    interaction_press.response.edit_message = AsyncMock()

    # Mock voice client connected and playback active
    mock_vc = MagicMock()
    mock_vc.is_connected.return_value = True
    player.voice_client = mock_vc
    player.state = player.state.PLAYING

    await view.queue_all_button.callback(interaction_press)
    assert len(player.queue) == 2
    assert view._queued is True
    interaction_press.response.edit_message.assert_called_once()
    assert all(child.disabled for child in view.children)

    # 4. Double press protection: subsequent click does nothing
    await view.queue_all_button.callback(interaction_press)
    assert len(player.queue) == 2  # Still 2, not 4!
