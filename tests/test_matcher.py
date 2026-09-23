"""Table-driven unit tests for Spotify-to-YouTube matching engine."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from yokai.music.resolvers.matcher import (
    ACCEPTANCE_THRESHOLD,
    TrackMatcher,
    normalize_title,
    score_candidate,
)
from yokai.music.resolvers.ytdlp import YtDlpResolver
from yokai.music.spotify.models import SpotifyTrackMeta
from yokai.storage.db import Database


@pytest.mark.parametrize(
    "spotify_meta,cand_title,cand_artist,cand_dur,expected_pass,description",
    [
        (
            SpotifyTrackMeta("1", "Blinding Lights", ["The Weeknd"], 200),
            "The Weeknd - Blinding Lights",
            "The Weeknd",
            200,
            True,
            "Exact match with standard duration",
        ),
        (
            SpotifyTrackMeta(
                "2", "Comfortably Numb - 2011 Remastered Version", ["Pink Floyd"], 382
            ),
            "Pink Floyd - Comfortably Numb",
            "Pink Floyd",
            383,
            True,
            "Remastered version suffix stripped",
        ),
        (
            SpotifyTrackMeta("3", "Levitating (feat. DaBaby)", ["Dua Lipa"], 203),
            "Dua Lipa - Levitating (Official Audio)",
            "Dua Lipa feat. DaBaby",
            203,
            True,
            "Feature credits in title vs artist string",
        ),
        (
            SpotifyTrackMeta("4", "Hotel California", ["Eagles"], 390),
            "Hotel California (Live at The Forum)",
            "Eagles",
            395,
            False,
            "Live-version trap: studio requested, live candidate penalized",
        ),
        (
            SpotifyTrackMeta("5", "All Too Well", ["Taylor Swift"], 329),
            "Taylor Swift - All Too Well (10 Minute Version)",
            "Taylor Swift",
            613,
            False,
            "Wrong-duration trap: duration difference > 15s rejected",
        ),
        (
            SpotifyTrackMeta("6", "夜に駆ける", ["YOASOBI"], 261),
            "YOASOBI「夜に駆ける」 Official Music Video",
            "YOASOBI",
            261,
            True,
            "Non-Latin Japanese Kanji title matched",
        ),
    ],
)
def test_score_candidate_table(
    spotify_meta: SpotifyTrackMeta,
    cand_title: str,
    cand_artist: str,
    cand_dur: int,
    expected_pass: bool,
    description: str,
) -> None:
    score = score_candidate(spotify_meta, cand_title, cand_artist, cand_dur)
    passed = score >= ACCEPTANCE_THRESHOLD
    assert passed == expected_pass, (
        f"{description}: score={score:.2f}, expected_pass={expected_pass}"
    )


def test_normalize_title() -> None:
    assert normalize_title("Song Name (feat. DaBaby)") == "song name"
    assert normalize_title("Song Name [ft. Lil Wayne]") == "song name"
    assert normalize_title("Comfortably Numb - 2011 Remastered Version") == "comfortably numb"
    assert normalize_title("Thriller (25th Anniversary Edition)") == "thriller"
    assert normalize_title("夜に駆ける") == "夜に駆ける"


@pytest.mark.asyncio
async def test_track_matcher_cache_hit_and_miss(tmp_path: Path) -> None:
    db_file = tmp_path / "test.db"
    db = Database(db_file)
    await db.initialize()

    # Pre-populate cache
    await db.set_spotify_match("sp_cached", "yt_video_123", 0.95)

    mock_resolver = AsyncMock(spec=YtDlpResolver)
    matcher = TrackMatcher(ytdlp_resolver=mock_resolver, db=db)

    # 1. Match from SQLite cache: zero network/search calls
    sp_track = SpotifyTrackMeta(
        spotify_id="sp_cached",
        title="Cached Song",
        artists=["Cached Artist"],
        duration_s=180,
    )
    with patch.object(matcher, "_ytmusic_search") as mock_ytm:
        matched = await matcher.match_track(sp_track, requester_id=999)
        assert matched is not None
        assert matched.video_id == "yt_video_123"
        assert matched.title == "Cached Song"
        assert matched.origin == "import"
        mock_ytm.assert_not_called()

    # 2. Match from ytmusicapi search
    sp_new = SpotifyTrackMeta(
        spotify_id="sp_new",
        title="New Song",
        artists=["Artist A"],
        duration_s=210,
    )
    mock_results = [
        {
            "videoId": "yt_new_456",
            "title": "New Song",
            "artists": [{"name": "Artist A"}],
            "duration_seconds": 210,
            "thumbnails": [{"url": "https://img.youtube.com/vi/yt_new_456/0.jpg"}],
        }
    ]
    with patch.object(matcher, "_ytmusic_search", return_value=mock_results):
        matched_new = await matcher.match_track(sp_new, requester_id=999)
        assert matched_new is not None
        assert matched_new.video_id == "yt_new_456"

        # Verify it was cached in SQLite
        cached_entry = await db.get_spotify_match("sp_new")
        assert cached_entry is not None
        assert cached_entry[0] == "yt_new_456"

    await db.close()
