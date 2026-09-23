"""Scraper Spotify provider using spotify_scraper, worker threads, and 24h caching."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

import spotify_scraper

from yokai.errors import NotFoundError, UnavailableError
from yokai.music.spotify.base import SpotifyProvider
from yokai.music.spotify.models import SpotifyCollection, SpotifyTrackMeta

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 86400  # 24 hours


class ScraperSpotifyProvider(SpotifyProvider):
    """Scraper provider that extracts Spotify metadata without official API credentials."""

    def __init__(self, enabled: bool = True) -> None:
        self._enabled = enabled
        self._cache: dict[str, tuple[Any, float]] = {}
        self.consecutive_failures: int = 0

    @property
    def name(self) -> str:
        return "scraper"

    @property
    def is_healthy(self) -> bool:
        return self._enabled

    def _get_from_cache(self, key: str) -> Optional[Any]:
        if key in self._cache:
            item, exp = self._cache[key]
            if time.time() < exp:
                return item
            del self._cache[key]
        return None

    def _set_cache(self, key: str, value: Any) -> None:
        self._cache[key] = (value, time.time() + CACHE_TTL_SECONDS)

    async def get_track(self, spotify_id: str) -> SpotifyTrackMeta:
        """Fetch metadata for a single Spotify track via web scraper."""
        if not self._enabled:
            raise UnavailableError("Spotify scraper provider is disabled.")

        cache_key = f"track:{spotify_id}"
        cached = self._get_from_cache(cache_key)
        if cached is not None:
            return cached

        try:
            track_obj = await asyncio.to_thread(self._sync_get_track, spotify_id)
            meta = self._convert_track(track_obj)
            self._set_cache(cache_key, meta)
            self.consecutive_failures = 0
            return meta
        except NotFoundError:
            self.consecutive_failures += 1
            raise
        except Exception as exc:
            self.consecutive_failures += 1
            logger.error("Spotify scraper get_track failed for %s: %s", spotify_id, exc)
            raise UnavailableError(f"Scraper could not retrieve track {spotify_id}: {exc}") from exc

    def _sync_get_track(self, spotify_id: str) -> Any:
        client = spotify_scraper.SpotifyClient()
        try:
            return client.get_track(spotify_id)
        except spotify_scraper.NotFoundError as err:
            raise NotFoundError(f"Spotify track {spotify_id} not found.") from err
        finally:
            client.close()

    async def get_album(self, album_id: str) -> SpotifyCollection:
        """Fetch metadata for an album and its tracks via web scraper."""
        if not self._enabled:
            raise UnavailableError("Spotify scraper provider is disabled.")

        cache_key = f"album:{album_id}"
        cached = self._get_from_cache(cache_key)
        if cached is not None:
            return cached

        try:
            album_obj = await asyncio.to_thread(self._sync_get_album, album_id)
            collection = self._convert_album(album_obj)
            self._set_cache(cache_key, collection)
            self.consecutive_failures = 0
            return collection
        except NotFoundError:
            self.consecutive_failures += 1
            raise
        except Exception as exc:
            self.consecutive_failures += 1
            logger.error("Spotify scraper get_album failed for %s: %s", album_id, exc)
            raise UnavailableError(f"Scraper could not retrieve album {album_id}: {exc}") from exc

    def _sync_get_album(self, album_id: str) -> Any:
        client = spotify_scraper.SpotifyClient()
        try:
            return client.get_album(album_id)
        except spotify_scraper.NotFoundError as err:
            raise NotFoundError(f"Spotify album {album_id} not found.") from err
        finally:
            client.close()

    async def get_playlist(self, playlist_id: str, max_tracks: int = 100) -> SpotifyCollection:
        """Fetch metadata for a playlist and its tracks via web scraper."""
        if not self._enabled:
            raise UnavailableError("Spotify scraper provider is disabled.")

        cache_key = f"playlist:{playlist_id}:{max_tracks}"
        cached = self._get_from_cache(cache_key)
        if cached is not None:
            return cached

        try:
            playlist_obj = await asyncio.to_thread(self._sync_get_playlist, playlist_id, max_tracks)
            collection = self._convert_playlist(playlist_obj, max_tracks)
            self._set_cache(cache_key, collection)
            self.consecutive_failures = 0
            return collection
        except NotFoundError:
            self.consecutive_failures += 1
            raise
        except Exception as exc:
            self.consecutive_failures += 1
            logger.error("Spotify scraper get_playlist failed for %s: %s", playlist_id, exc)
            raise UnavailableError(
                f"Scraper could not retrieve playlist {playlist_id}: {exc}"
            ) from exc

    def _sync_get_playlist(self, playlist_id: str, max_tracks: int) -> Any:
        client = spotify_scraper.SpotifyClient()
        try:
            return client.get_playlist(playlist_id, max_tracks=max_tracks)
        except spotify_scraper.NotFoundError as err:
            raise NotFoundError(f"Spotify playlist {playlist_id} not found.") from err
        finally:
            client.close()

    def _convert_track(self, track: Any) -> SpotifyTrackMeta:
        """Convert spotify_scraper.Track to SpotifyTrackMeta."""
        artists = [a.name for a in getattr(track, "artists", []) if getattr(a, "name", None)]
        duration_ms = getattr(track, "duration_ms", 0) or 0
        duration_s = max(0, int(duration_ms // 1000))

        album_obj = getattr(track, "album", None)
        album_name = getattr(album_obj, "name", None) if album_obj else None

        # Thumbnail URL
        thumb_url = None
        images = getattr(track, "images", None) or (
            getattr(album_obj, "images", None) if album_obj else None
        )
        if images and len(images) > 0:
            thumb_url = getattr(images[0], "url", None)

        return SpotifyTrackMeta(
            spotify_id=getattr(track, "id", ""),
            title=getattr(track, "name", "Unknown Title"),
            artists=artists,
            duration_s=duration_s,
            album_name=album_name,
            thumbnail_url=thumb_url,
            explicit=bool(getattr(track, "explicit", False)),
        )

    def _convert_album(self, album: Any) -> SpotifyCollection:
        """Convert spotify_scraper.Album to SpotifyCollection."""
        name = getattr(album, "name", "Unknown Album")
        total = getattr(album, "total_tracks", 0) or 0
        raw_tracks = getattr(album, "tracks", []) or []

        images = getattr(album, "images", None)
        album_thumb = getattr(images[0], "url", None) if images and len(images) > 0 else None

        tracks: list[SpotifyTrackMeta] = []
        for t in raw_tracks:
            meta = self._convert_track(t)
            if not meta.album_name:
                meta.album_name = name
            if not meta.thumbnail_url:
                meta.thumbnail_url = album_thumb
            tracks.append(meta)

        return SpotifyCollection(
            name=name,
            tracks=tracks,
            total=total or len(tracks),
            partial=len(tracks) < total,
        )

    def _convert_playlist(self, playlist: Any, max_tracks: int) -> SpotifyCollection:
        """Convert spotify_scraper.Playlist to SpotifyCollection."""
        name = getattr(playlist, "name", "Unknown Playlist")
        total = getattr(playlist, "total_tracks", 0) or 0
        raw_entries = getattr(playlist, "tracks", []) or []

        tracks: list[SpotifyTrackMeta] = []
        for pt in raw_entries:
            t = getattr(pt, "track", None)
            if t is None:
                continue
            meta = self._convert_track(t)
            tracks.append(meta)
            if len(tracks) >= max_tracks:
                break

        return SpotifyCollection(
            name=name,
            tracks=tracks,
            total=total or len(tracks),
            partial=len(tracks) < total,
        )
