"""YouTube Music Radio recommender backed by unauthenticated watch playlist endpoint."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

import ytmusicapi

from yokai.music.models import Track
from yokai.music.recommend.base import Recommender
from yokai.music.recommend.models import Recommendation
from yokai.music.resolvers.matcher import normalize_title

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 600.0  # 10 minutes
MAX_DURATION_SECONDS = 600  # 10 minutes cap
NON_MUSIC_VIDEO_TYPES = {
    "MUSIC_VIDEO_TYPE_PODCAST_EPISODE",
    "podcast",
    "episode",
}


def _parse_duration_seconds(length_str: Optional[str]) -> int:
    """Parse MM:SS or HH:MM:SS duration string into total seconds."""
    if not length_str:
        return 0
    parts = length_str.strip().split(":")
    try:
        parts_int = [int(p) for p in parts]
    except ValueError:
        return 0
    if len(parts_int) == 1:
        return max(0, parts_int[0])
    if len(parts_int) == 2:
        return max(0, parts_int[0] * 60 + parts_int[1])
    if len(parts_int) == 3:
        return max(0, parts_int[0] * 3600 + parts_int[1] * 60 + parts_int[2])
    return 0


class YTMusicRadioRecommender(Recommender):
    """Recommender querying YouTube Music unauthenticated radio watch playlists."""

    def __init__(self, ytmusic_client: Optional[ytmusicapi.YTMusic] = None) -> None:
        self._ytmusic = ytmusic_client or ytmusicapi.YTMusic()
        # Per-seed cache: video_id -> (list of raw track dicts, expires_at)
        self._cache: dict[str, tuple[list[dict[str, Any]], float]] = {}

    def _get_from_cache(self, video_id: str) -> Optional[list[dict[str, Any]]]:
        if video_id in self._cache:
            data, expires_at = self._cache[video_id]
            if time.time() < expires_at:
                return data
            del self._cache[video_id]
        return None

    def _set_cache(self, video_id: str, tracks: list[dict[str, Any]]) -> None:
        self._cache[video_id] = (tracks, time.time() + CACHE_TTL_SECONDS)

    async def recommend(
        self,
        seed: Track,
        count: int = 8,
        exclude: Optional[set[str]] = None,
    ) -> Recommendation:
        """Fetch and filter radio track recommendations based on seed track."""
        exclude_set = exclude or set()
        raw_tracks = self._get_from_cache(seed.video_id)

        if raw_tracks is None:
            try:
                data = await asyncio.to_thread(self._fetch_radio, seed.video_id)
                raw_tracks = data.get("tracks", []) if isinstance(data, dict) else []
                self._set_cache(seed.video_id, raw_tracks)
            except Exception as exc:
                logger.error("Failed to fetch radio recommendations for %s: %s", seed.video_id, exc)
                return Recommendation(tracks=[], seed=seed, strategy="ytmusic_radio")

        recommended_tracks: list[Track] = []
        seen_normalized_keys: set[str] = set()

        # Seed key to avoid duplicates with seed title+artist
        seed_key = f"{normalize_title(seed.title)}:{normalize_title(seed.artist or '')}"
        seen_normalized_keys.add(seed_key)

        for item in raw_tracks:
            if not isinstance(item, dict):
                continue

            vid = item.get("videoId")
            if not vid:
                continue

            # 1. Filter: seed video ID itself
            if vid == seed.video_id:
                continue

            # 2. Filter: excluded video IDs (playing, queued, recent 100 plays)
            if vid in exclude_set:
                continue

            # 3. Filter: non-music video types where identifiable
            video_type = item.get("videoType")
            if video_type and str(video_type) in NON_MUSIC_VIDEO_TYPES:
                continue

            # 4. Filter: duration parsing & 10-minute maximum
            dur_s = item.get("duration_seconds")
            if dur_s is None or dur_s <= 0:
                length_str = item.get("length")
                dur_s = _parse_duration_seconds(length_str)

            if dur_s <= 0 or dur_s > MAX_DURATION_SECONDS:
                continue

            # 5. Extract metadata
            title = item.get("title") or "Unknown Title"
            artists_data = item.get("artists", [])
            artist_names = [
                a.get("name", "") for a in artists_data if isinstance(a, dict) and a.get("name")
            ]
            artist_str = ", ".join(artist_names) if artist_names else None

            # 6. Filter: normalized title + artist deduplication
            norm_key = f"{normalize_title(title)}:{normalize_title(artist_str or '')}"
            if norm_key in seen_normalized_keys:
                continue
            seen_normalized_keys.add(norm_key)

            # Thumbnail
            thumbnail_url = None
            thumbs = item.get("thumbnail") or item.get("thumbnails")
            if isinstance(thumbs, list) and thumbs:
                last_thumb = thumbs[-1]
                if isinstance(last_thumb, dict):
                    thumbnail_url = last_thumb.get("url")

            track = Track(
                video_id=vid,
                title=title,
                artist=artist_str,
                duration_s=dur_s,
                thumbnail_url=thumbnail_url,
                requester_id=seed.requester_id,
                origin="recommendation",
            )
            recommended_tracks.append(track)

            if len(recommended_tracks) >= count:
                break

        return Recommendation(
            tracks=recommended_tracks,
            seed=seed,
            strategy="ytmusic_radio",
        )

    def _fetch_radio(self, video_id: str) -> dict[str, Any]:
        """Synchronous call to ytmusicapi get_watch_playlist."""
        return self._ytmusic.get_watch_playlist(videoId=video_id, radio=True, limit=25)
