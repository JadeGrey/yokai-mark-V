"""Tests for the YouTube Music-backed searcher and its resolver integration."""

from __future__ import annotations

from typing import Any, Optional
from unittest.mock import patch

import pytest
import yt_dlp

from yokai.errors import NotFoundError
from yokai.music.resolvers.ytdlp import YtDlpResolver
from yokai.music.search import YtMusicSearcher


def song(video_id: str, title: str = "Song", duration: Optional[int] = 200, **extra: Any) -> dict:
    item: dict[str, Any] = {
        "resultType": "song",
        "videoId": video_id,
        "title": title,
        "artists": [{"name": "Artist A"}, {"name": "Artist B"}],
        "duration_seconds": duration,
        "thumbnails": [{"url": "https://img/small"}, {"url": "https://img/large"}],
    }
    item.update(extra)
    return item


class FakeYTMusic:
    def __init__(
        self,
        songs: Optional[list[dict]] = None,
        videos: Optional[list[dict]] = None,
        fail: bool = False,
    ) -> None:
        self.songs = songs or []
        self.videos = videos or []
        self.fail = fail
        self.calls: list[tuple[str, Optional[str], int]] = []

    def search(self, query: str, filter: Optional[str] = None, limit: int = 20) -> list[dict]:
        self.calls.append((query, filter, limit))
        if self.fail:
            raise RuntimeError("ytmusic down")
        return list(self.songs if filter == "songs" else self.videos)


@pytest.mark.asyncio
async def test_search_prefers_songs_and_maps_fields() -> None:
    client = FakeYTMusic(songs=[song("vid_song1", "Never Gonna")], videos=[song("vid_video1")])
    searcher = YtMusicSearcher(client=client)  # type: ignore[arg-type]
    tracks = await searcher.search("never gonna", requester_id=42)
    assert [t.video_id for t in tracks] == ["vid_song1"]
    track = tracks[0]
    assert track.title == "Never Gonna"
    assert track.artist == "Artist A, Artist B"
    assert track.duration_s == 200
    assert track.thumbnail_url == "https://img/large"
    assert track.requester_id == 42
    assert track.origin == "search"
    assert [c[1] for c in client.calls] == ["songs"]


@pytest.mark.asyncio
async def test_search_falls_back_to_videos_when_no_songs() -> None:
    client = FakeYTMusic(songs=[], videos=[song("vid_video1")])
    tracks = await YtMusicSearcher(client=client).search("q", 1)  # type: ignore[arg-type]
    assert [t.video_id for t in tracks] == ["vid_video1"]
    assert [c[1] for c in client.calls] == ["songs", "videos"]


@pytest.mark.asyncio
async def test_search_skips_unusable_results() -> None:
    client = FakeYTMusic(
        songs=[
            song("vid_ok1234", "Good"),
            {"resultType": "song", "title": "no id", "duration_seconds": 100},
            song("vid_nodur1", duration=None),
            song("vid_zero01", duration=0),
            song("vid_toolong", duration=99999),
            song("vid_unavail", isAvailable=False),
        ]
    )
    tracks = await YtMusicSearcher(client=client, max_track_seconds=3600).search("q", 1)  # type: ignore[arg-type]
    assert [t.video_id for t in tracks] == ["vid_ok1234"]


@pytest.mark.asyncio
async def test_search_cache_returns_copies_for_new_requester() -> None:
    client = FakeYTMusic(songs=[song("vid_cache1")])
    searcher = YtMusicSearcher(client=client)  # type: ignore[arg-type]
    first = await searcher.search("Same Query", requester_id=1)
    second = await searcher.search("same query", requester_id=2)
    assert len(client.calls) == 1
    assert first[0].requester_id == 1
    assert second[0].requester_id == 2
    assert first[0] is not second[0]


@pytest.mark.asyncio
async def test_search_swallows_client_errors() -> None:
    searcher = YtMusicSearcher(client=FakeYTMusic(fail=True))  # type: ignore[arg-type]
    assert await searcher.search("anything", requester_id=1) == []


@pytest.mark.asyncio
async def test_empty_results_are_not_cached() -> None:
    client = FakeYTMusic()
    searcher = YtMusicSearcher(client=client)  # type: ignore[arg-type]
    await searcher.search("nothing here", 1)
    await searcher.search("nothing here", 1)
    assert len(client.calls) == 4  # songs + videos, twice


@pytest.mark.asyncio
async def test_resolver_uses_searcher_without_touching_ytdlp() -> None:
    client = FakeYTMusic(songs=[song("vid_fast123", "Fast")])
    resolver = YtDlpResolver(searcher=YtMusicSearcher(client=client))  # type: ignore[arg-type]
    with patch.object(yt_dlp.YoutubeDL, "extract_info", side_effect=AssertionError("yt-dlp used")):
        track = await resolver.resolve_query("fast", requester_id=9)
    assert track.video_id == "vid_fast123"
    assert resolver.get_average_latency() is not None


@pytest.mark.asyncio
async def test_resolver_falls_back_when_search_returns_nothing() -> None:
    resolver = YtDlpResolver(searcher=YtMusicSearcher(client=FakeYTMusic()))  # type: ignore[arg-type]
    flat = {"entries": [{"id": "flat_vid1", "title": "Flat", "uploader": "U", "duration": 120}]}
    with patch.object(yt_dlp.YoutubeDL, "extract_info", return_value=flat):
        track = await resolver.resolve_query("obscure", requester_id=3)
    assert track.video_id == "flat_vid1"


@pytest.mark.asyncio
async def test_resolver_raises_not_found_when_nothing_usable_anywhere() -> None:
    resolver = YtDlpResolver(searcher=YtMusicSearcher(client=FakeYTMusic(fail=True)))  # type: ignore[arg-type]
    only_live = {"entries": [{"id": "live1234", "title": "Live", "is_live": True, "duration": 0}]}
    with patch.object(yt_dlp.YoutubeDL, "extract_info", return_value=only_live):
        with pytest.raises(NotFoundError):
            await resolver.resolve_query("live only", requester_id=3)
