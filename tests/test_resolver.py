"""Tests for YtDlpResolver extraction, error mapping, and health status."""

from __future__ import annotations

from unittest.mock import patch

import pytest
import yt_dlp

from yokai.errors import (
    BotCheckError,
    LoginRequiredError,
    NotFoundError,
    UnavailableError,
)
from yokai.music.models import Track
from yokai.music.resolvers.ytdlp import YtDlpResolver


@pytest.mark.asyncio
async def test_resolve_query_success() -> None:
    mock_data = {
        "entries": [
            {
                "id": "vid_live",
                "title": "Live Stream",
                "is_live": True,
                "duration": 0,
            },
            {
                "id": "vid_valid",
                "title": "Song Title",
                "uploader": "Cool Artist",
                "duration": 215,
                "thumbnail": "https://img.youtube.com/vi/vid_valid/0.jpg",
                "is_live": False,
            },
        ]
    }

    resolver = YtDlpResolver(max_track_seconds=3600)
    with patch.object(yt_dlp.YoutubeDL, "extract_info", return_value=mock_data):
        track = await resolver.resolve_query("cool artist song", requester_id=123)
        assert track.video_id == "vid_valid"
        assert track.title == "Song Title"
        assert track.artist == "Cool Artist"
        assert track.duration_s == 215
        assert track.requester_id == 123
        assert track.origin == "search"


@pytest.mark.asyncio
async def test_resolve_query_no_valid_results() -> None:
    mock_data = {
        "entries": [
            {
                "id": "vid_too_long",
                "title": "10 Hour Relaxing Audio",
                "duration": 36000,
                "is_live": False,
            }
        ]
    }

    resolver = YtDlpResolver(max_track_seconds=3600)
    with patch.object(yt_dlp.YoutubeDL, "extract_info", return_value=mock_data):
        with pytest.raises(NotFoundError, match="No suitable non-live tracks"):
            await resolver.resolve_query("relaxing loop", requester_id=123)


@pytest.mark.asyncio
async def test_resolve_url_single_and_playlist() -> None:
    single_data = {
        "id": "vid_single",
        "title": "Solo Track",
        "uploader": "Solo Artist",
        "duration": 180,
    }
    playlist_data = {
        "entries": [
            {"id": "vid_p1", "title": "Track 1", "uploader": "A1", "duration": 150},
            {"id": "vid_p2", "title": "Track 2", "uploader": "A2", "duration": 200},
        ]
    }

    resolver = YtDlpResolver()

    # 1. Single
    with patch.object(yt_dlp.YoutubeDL, "extract_info", return_value=single_data):
        res, is_pl = await resolver.resolve_url("https://www.youtube.com/watch?v=vid_single", 999)
        assert is_pl is False
        assert isinstance(res, Track)
        assert res.video_id == "vid_single"

    # 2. Playlist
    with patch.object(yt_dlp.YoutubeDL, "extract_info", return_value=playlist_data):
        res, is_pl = await resolver.resolve_url("https://www.youtube.com/playlist?list=PL123", 999)
        assert is_pl is True
        assert isinstance(res, list)
        assert len(res) == 2
        assert res[0].video_id == "vid_p1"


@pytest.mark.asyncio
async def test_get_stream_success() -> None:
    stream_data = {
        "url": "https://rr1---sn-audio.googlevideo.com/videoplayback?id=123",
        "http_headers": {"User-Agent": "Mozilla/5.0"},
    }
    resolver = YtDlpResolver()
    track = Track(video_id="vid_123", title="Test", duration_s=180)

    with patch.object(yt_dlp.YoutubeDL, "extract_info", return_value=stream_data):
        stream_info = await resolver.get_stream(track)
        assert "googlevideo.com" in stream_info.url
        assert stream_info.http_headers["User-Agent"] == "Mozilla/5.0"


@pytest.mark.asyncio
async def test_error_mapping_and_health_degradation() -> None:
    resolver = YtDlpResolver()
    track = Track(video_id="vid_123", title="Test", duration_s=180)

    # 1. Age restriction -> LoginRequiredError
    with patch.object(
        yt_dlp.YoutubeDL,
        "extract_info",
        side_effect=Exception("Sign in to confirm your age"),
    ):
        with pytest.raises(LoginRequiredError):
            await resolver.get_stream(track)
        assert resolver.consecutive_failures == 1
        assert resolver.is_degraded is False

    # 2. Country block -> UnavailableError
    with patch.object(
        yt_dlp.YoutubeDL,
        "extract_info",
        side_effect=Exception("The uploader has not made this video available in your country"),
    ):
        with pytest.raises(UnavailableError):
            await resolver.get_stream(track)
        assert resolver.consecutive_failures == 2
        assert resolver.is_degraded is False

    # 3. HTTP 403 / Bot check -> BotCheckError -> Triggers DEGRADED
    with patch.object(
        yt_dlp.YoutubeDL,
        "extract_info",
        side_effect=Exception("HTTP Error 403: Forbidden"),
    ):
        with pytest.raises(BotCheckError):
            await resolver.get_stream(track)
        assert resolver.consecutive_failures == 3
        assert resolver.is_degraded is True
        assert len(resolver.error_ring_buffer) == 3

    # 4. Success restores health
    with patch.object(yt_dlp.YoutubeDL, "extract_info", return_value={"url": "http://stream.url"}):
        await resolver.get_stream(track)
        assert resolver.consecutive_failures == 0
        assert resolver.is_degraded is False
