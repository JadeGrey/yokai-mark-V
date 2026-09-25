"""Tests for YtDlpResolver extraction, error mapping, and health status."""

from __future__ import annotations

import asyncio
import threading
import time
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
from yokai.music.resolvers import ytdlp as ytdlp_module
from yokai.music.resolvers.ytdlp import YtDlpResolver, stream_expiry


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


@pytest.mark.asyncio
async def test_resolve_url_caches_stream_info() -> None:
    single_data = {
        "id": "vid_cached",
        "title": "Cached Track",
        "uploader": "Solo Artist",
        "duration": 180,
        "url": "https://googlevideo.com/playback_cached",
        "http_headers": {"User-Agent": "FastAgent"},
    }
    resolver = YtDlpResolver()

    # 1. Resolve URL extracts both metadata and direct audio stream
    with patch.object(yt_dlp.YoutubeDL, "extract_info", return_value=single_data) as mock_extract:
        track, _ = await resolver.resolve_url("https://www.youtube.com/watch?v=vid_cached", 999)
        assert isinstance(track, Track)
        assert mock_extract.call_count == 1

    # 2. Subsequent get_stream should hit memory cache without calling extract_info again
    with patch.object(yt_dlp.YoutubeDL, "extract_info") as mock_extract_again:
        stream_info = await resolver.get_stream(track)
        assert stream_info.url == "https://googlevideo.com/playback_cached"
        assert stream_info.http_headers["User-Agent"] == "FastAgent"
        mock_extract_again.assert_not_called()


def test_stream_expiry_reads_expire_param() -> None:
    url = "https://rr1.googlevideo.com/videoplayback?expire=1790321938&id=abc"
    assert stream_expiry(url, now=1000.0) == 1790321938 - 120.0


def test_stream_expiry_falls_back_to_ttl() -> None:
    assert stream_expiry("https://example.test/audio", now=1000.0) == 1000.0 + 1800.0


@pytest.mark.asyncio
async def test_get_stream_is_single_flight() -> None:
    calls = 0

    def slow_extract(*_args: object, **_kwargs: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        time.sleep(0.05)
        return {"url": "https://googlevideo.com/one"}

    resolver = YtDlpResolver()
    track = Track(video_id="vid_sf", title="T", duration_s=100)
    with patch.object(yt_dlp.YoutubeDL, "extract_info", side_effect=slow_extract):
        results = await asyncio.gather(
            resolver.get_stream(track),
            resolver.get_stream(track, background=True),
            resolver.get_stream(track),
        )
    assert calls == 1
    assert {r.url for r in results} == {"https://googlevideo.com/one"}


@pytest.mark.asyncio
async def test_expired_cache_entry_is_refetched() -> None:
    resolver = YtDlpResolver()
    track = Track(video_id="vid_exp", title="T", duration_s=100)
    from yokai.music.models import StreamInfo

    stale = StreamInfo(url="https://old.test/a")
    resolver._stream_cache["vid_exp"] = (stale, time.time() - 5)
    with patch.object(
        yt_dlp.YoutubeDL, "extract_info", return_value={"url": "https://new.test/b"}
    ) as mock_extract:
        info = await resolver.get_stream(track)
    assert info.url == "https://new.test/b"
    assert mock_extract.call_count == 1


@pytest.mark.asyncio
async def test_invalidate_stream_drops_cache() -> None:
    resolver = YtDlpResolver()
    track = Track(video_id="vid_inv", title="T", duration_s=100)
    with patch.object(yt_dlp.YoutubeDL, "extract_info", return_value={"url": "https://a.test/1"}):
        await resolver.get_stream(track)
    resolver.invalidate_stream("vid_inv")
    with patch.object(yt_dlp.YoutubeDL, "extract_info", return_value={"url": "https://a.test/2"}):
        assert (await resolver.get_stream(track)).url == "https://a.test/2"


@pytest.mark.asyncio
async def test_background_work_does_not_starve_foreground() -> None:
    release = threading.Event()

    def extract(_self: object, target: str, download: bool = False) -> dict[str, object]:
        if "vid_bg" in target:
            release.wait(timeout=5)
        return {"url": f"https://googlevideo.com/{target[-6:]}"}

    resolver = YtDlpResolver(concurrency_limit=2)
    with patch.object(yt_dlp.YoutubeDL, "extract_info", new=extract):
        # Both background lookups are patched too (no real network): the first blocks inside
        # extract(), the second waits for the single background slot.
        background = asyncio.create_task(
            resolver.get_stream(
                Track(video_id="vid_bg", title="B", duration_s=100), background=True
            )
        )
        queued_background = asyncio.create_task(
            resolver.get_stream(
                Track(video_id="vid_bg2", title="B2", duration_s=100), background=True
            )
        )
        await asyncio.sleep(0.05)
        try:
            foreground = await asyncio.wait_for(
                resolver.get_stream(Track(video_id="vid_fg", title="F", duration_s=100)),
                timeout=2.0,
            )
            assert foreground.url.startswith("https://googlevideo.com/")
        finally:
            release.set()
            await asyncio.gather(background, queued_background, return_exceptions=True)


@pytest.mark.asyncio
async def test_flat_search_fallback_uses_flat_extraction() -> None:
    created: list[dict[str, object]] = []

    class FakeYDL:
        def __init__(self, opts: dict[str, object]) -> None:
            created.append(opts)

        def __enter__(self) -> FakeYDL:
            return self

        def __exit__(self, *_exc: object) -> bool:
            return False

        def extract_info(self, target: str, download: bool = False) -> dict[str, object]:
            return {
                "entries": [
                    {"id": "flat_vid", "title": "Flat Song", "uploader": "A", "duration": 200}
                ]
            }

    resolver = YtDlpResolver()
    with patch.object(yt_dlp, "YoutubeDL", FakeYDL):
        track = await resolver.resolve_query("some song", requester_id=5)
    assert track.video_id == "flat_vid"
    assert created[0]["extract_flat"] == "in_playlist"


@pytest.mark.asyncio
async def test_fast_profile_is_tried_first_then_default(monkeypatch: pytest.MonkeyPatch) -> None:
    fast = {"youtube": {"player_client": ["android_vr"]}}
    monkeypatch.setattr(ytdlp_module, "FAST_STREAM_EXTRACTOR_ARGS", fast)
    seen: list[object] = []

    class FakeYDL:
        def __init__(self, opts: dict[str, object]) -> None:
            self.opts = opts

        def __enter__(self) -> FakeYDL:
            return self

        def __exit__(self, *_exc: object) -> bool:
            return False

        def extract_info(self, target: str, download: bool = False) -> dict[str, object]:
            seen.append(self.opts.get("extractor_args"))
            if self.opts.get("extractor_args"):
                raise RuntimeError("fast profile rejected")
            return {"url": "https://googlevideo.com/default"}

    resolver = YtDlpResolver()
    with patch.object(yt_dlp, "YoutubeDL", FakeYDL):
        info = await resolver.get_stream(Track(video_id="vid_fast", title="T", duration_s=100))
    assert info.url == "https://googlevideo.com/default"
    assert seen == [fast, None]
