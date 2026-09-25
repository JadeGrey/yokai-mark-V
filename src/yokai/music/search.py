"""Fast YouTube Music-backed search used by /play (and later the /search picker).

`ytmusicapi` answers in about 0.4 s once warm, while a yt-dlp `ytsearch5` takes about 10 s because
it fully extracts every result. Songs are preferred (clean audio releases); plain videos are the
fallback for things that are not on YouTube Music as songs.
"""

from __future__ import annotations

import asyncio
import collections
import dataclasses
import logging
import time
from typing import Any, Optional

import ytmusicapi

from yokai.music.models import Track

logger = logging.getLogger(__name__)

CACHE_TTL_S = 600.0
CACHE_MAX_ENTRIES = 128
SEARCH_TIMEOUT_S = 8.0
SEARCH_FILTERS = ("songs", "videos")


class YtMusicSearcher:
    """Searches YouTube Music and returns lightweight `Track` records (no stream URLs)."""

    def __init__(
        self,
        client: Optional[ytmusicapi.YTMusic] = None,
        max_track_seconds: int = 3600,
    ) -> None:
        self._client = client or ytmusicapi.YTMusic()
        self._max_track_seconds = max_track_seconds
        self._cache: collections.OrderedDict[str, tuple[float, list[Track]]] = (
            collections.OrderedDict()
        )

    async def search(self, query: str, requester_id: int, limit: int = 5) -> list[Track]:
        """Return up to `limit` playable tracks. Never raises: errors yield an empty list."""
        key = f"{limit}:{query.strip().lower()}"
        cached = self._cache_get(key)
        if cached is not None:
            return [dataclasses.replace(t, requester_id=requester_id) for t in cached]

        tracks: list[Track] = []
        for search_filter in SEARCH_FILTERS:
            try:
                raw = await asyncio.wait_for(
                    asyncio.to_thread(
                        self._client.search, query, filter=search_filter, limit=limit
                    ),
                    timeout=SEARCH_TIMEOUT_S,
                )
            except Exception as exc:  # ytmusicapi raises many unrelated types
                logger.warning(
                    "YouTube Music %s search failed for %r: %s", search_filter, query, exc
                )
                continue
            tracks = self._to_tracks(raw, requester_id)
            if tracks:
                break

        if tracks:
            self._cache_put(key, tracks)
        return tracks

    async def warm_up(self) -> None:
        """Pre-warm ytmusicapi at startup (avoids 1.5s delay on /play)."""
        try:
            await asyncio.wait_for(
                asyncio.to_thread(self._client.search, "warm up", filter="songs", limit=1),
                timeout=SEARCH_TIMEOUT_S,
            )
        except Exception as exc:
            logger.debug("Search warm-up failed: %s", exc)

    def _to_tracks(self, raw: Any, requester_id: int) -> list[Track]:
        tracks: list[Track] = []
        if not isinstance(raw, list):
            return tracks
        for item in raw:
            if not isinstance(item, dict):
                continue
            video_id = item.get("videoId")
            if not video_id or item.get("isAvailable") is False:
                continue
            duration = item.get("duration_seconds")
            if not isinstance(duration, int) or duration <= 0 or duration > self._max_track_seconds:
                continue
            artists = item.get("artists") or []
            names = [a["name"] for a in artists if isinstance(a, dict) and a.get("name")]
            thumbs = item.get("thumbnails")
            thumbnail: Optional[str] = None
            if isinstance(thumbs, list) and thumbs and isinstance(thumbs[-1], dict):
                thumbnail = thumbs[-1].get("url")
            tracks.append(
                Track(
                    video_id=str(video_id),
                    title=str(item.get("title") or "Unknown Title"),
                    artist=", ".join(names) or None,
                    duration_s=duration,
                    thumbnail_url=thumbnail,
                    requester_id=requester_id,
                    origin="search",
                )
            )
        return tracks

    def _cache_get(self, key: str) -> Optional[list[Track]]:
        entry = self._cache.get(key)
        if entry is None:
            return None
        expires_at, tracks = entry
        if time.monotonic() >= expires_at:
            del self._cache[key]
            return None
        self._cache.move_to_end(key)
        return tracks

    def _cache_put(self, key: str, tracks: list[Track]) -> None:
        self._cache[key] = (time.monotonic() + CACHE_TTL_S, tracks)
        self._cache.move_to_end(key)
        while len(self._cache) > CACHE_MAX_ENTRIES:
            self._cache.popitem(last=False)
