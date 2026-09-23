"""Official Spotify Web API provider using httpx and OAuth client credentials."""

from __future__ import annotations

import base64
import logging
import time
from typing import Any, Optional

import httpx

from yokai.errors import NotFoundError, UnavailableError
from yokai.music.spotify.base import SpotifyProvider
from yokai.music.spotify.models import SpotifyCollection, SpotifyTrackMeta

logger = logging.getLogger(__name__)

TOKEN_ENDPOINT = "https://accounts.spotify.com/api/token"
API_BASE = "https://api.spotify.com/v1"


class OfficialSpotifyProvider(SpotifyProvider):
    """Official Spotify provider backed by the Web API via httpx."""

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self._custom_client = client is not None
        self._client = client or httpx.AsyncClient(timeout=15.0)

        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0
        self._is_enabled: bool = bool(client_id and client_secret)
        self._is_healthy: bool = self._is_enabled
        self.consecutive_failures: int = 0

    @property
    def name(self) -> str:
        return "official"

    @property
    def is_healthy(self) -> bool:
        return self._is_enabled and self._is_healthy

    def disable(self, reason: str) -> None:
        """Permanently disable official provider if rejected by Spotify."""
        logger.warning("Disabling official Spotify provider: %s", reason)
        self._is_enabled = False
        self._is_healthy = False

    async def _get_auth_header(self) -> dict[str, str]:
        """Fetch or reuse cached Bearer access token."""
        if not self._is_enabled:
            raise UnavailableError("Official Spotify provider is disabled.")

        now = time.time()
        if self._access_token and now < self._token_expires_at:
            return {"Authorization": f"Bearer {self._access_token}"}

        # Request new bearer token
        auth_bytes = f"{self.client_id}:{self.client_secret}".encode("utf-8")
        auth_b64 = base64.b64encode(auth_bytes).decode("utf-8")

        headers = {
            "Authorization": f"Basic {auth_b64}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        data = {"grant_type": "client_credentials"}

        try:
            resp = await self._client.post(TOKEN_ENDPOINT, headers=headers, data=data)
            if resp.status_code in (400, 401, 403):
                self.disable(f"Authentication failed with HTTP {resp.status_code}: {resp.text}")
                raise UnavailableError("Invalid Spotify API credentials.")
            resp.raise_for_status()
            payload = resp.json()
            token = payload.get("access_token")
            expires_in = payload.get("expires_in", 3600)

            if not token:
                raise UnavailableError("No access token returned by Spotify.")

            self._access_token = token
            # Expire slightly before actual duration
            self._token_expires_at = now + max(60, expires_in - 60)
            self._is_healthy = True
            return {"Authorization": f"Bearer {self._access_token}"}
        except httpx.HTTPError as exc:
            self.consecutive_failures += 1
            logger.error("Spotify token request failed: %s", exc)
            raise UnavailableError(f"Spotify token request error: {exc}") from exc

    async def _request(
        self, endpoint: str, params: Optional[dict[str, Any]] = None
    ) -> dict[str, Any]:
        """Perform authenticated GET request against Spotify Web API."""
        headers = await self._get_auth_header()
        url = f"{API_BASE}{endpoint}"

        try:
            resp = await self._client.get(url, headers=headers, params=params)
            if resp.status_code == 404:
                raise NotFoundError("Requested Spotify entity was not found.")
            if resp.status_code in (401, 403):
                logger.warning("Spotify API returned HTTP %d for %s", resp.status_code, endpoint)
                raise UnavailableError(
                    f"Spotify API access forbidden or unauthorized (HTTP {resp.status_code})."
                )
            resp.raise_for_status()
            data = resp.json()
            self.consecutive_failures = 0
            return data
        except (NotFoundError, UnavailableError):
            self.consecutive_failures += 1
            raise
        except Exception as exc:
            self.consecutive_failures += 1
            logger.error("Spotify API request error for %s: %s", endpoint, exc)
            raise UnavailableError(f"Spotify request error: {exc}") from exc

    async def get_track(self, spotify_id: str) -> SpotifyTrackMeta:
        """Fetch metadata for a single track."""
        data = await self._request(f"/tracks/{spotify_id}")
        return self._parse_track_payload(data)

    async def get_album(self, album_id: str) -> SpotifyCollection:
        """Fetch metadata for an album and its track listing."""
        data = await self._request(f"/albums/{album_id}")
        name = data.get("name", "Unknown Album")
        total = data.get("total_tracks", 0)
        tracks_data = data.get("tracks", {}).get("items", [])

        # Album images
        images = data.get("images", [])
        thumb_url = images[0]["url"] if images else None

        tracks: list[SpotifyTrackMeta] = []
        for item in tracks_data:
            if not item:
                continue
            meta = self._parse_track_payload(item, album_name=name, thumbnail_url=thumb_url)
            tracks.append(meta)

        return SpotifyCollection(
            name=name,
            tracks=tracks,
            total=total or len(tracks),
            partial=len(tracks) < total,
        )

    async def get_playlist(self, playlist_id: str, max_tracks: int = 100) -> SpotifyCollection:
        """Fetch playlist metadata and items using /playlists/{id}/items per 2026 Dev Mode."""
        # 1. Fetch playlist metadata
        info = await self._request(
            f"/playlists/{playlist_id}", params={"fields": "name,tracks.total,images"}
        )
        name = info.get("name", "Unknown Playlist")
        total = info.get("tracks", {}).get("total", 0)

        # 2. Fetch items (2026 endpoint: /playlists/{id}/items)
        items_payload = await self._request(
            f"/playlists/{playlist_id}/items",
            params={"limit": min(max_tracks, 100)},
        )
        items = items_payload.get("items", [])
        if not items:
            # Under Dev Mode 2026, unowned playlists return empty items or 403
            raise UnavailableError("No playlist items returned by official API (unowned playlist).")

        tracks: list[SpotifyTrackMeta] = []
        for entry in items:
            track_data = entry.get("track") if isinstance(entry, dict) else None
            # Skip podcasts or null track entries
            if not track_data or track_data.get("type") != "track":
                continue
            meta = self._parse_track_payload(track_data)
            tracks.append(meta)

        return SpotifyCollection(
            name=name,
            tracks=tracks,
            total=total or len(tracks),
            partial=len(tracks) < total,
        )

    def _parse_track_payload(
        self,
        data: dict[str, Any],
        album_name: Optional[str] = None,
        thumbnail_url: Optional[str] = None,
    ) -> SpotifyTrackMeta:
        """Convert a Spotify track JSON object into SpotifyTrackMeta."""
        spotify_id = data.get("id", "")
        title = data.get("name", "Unknown Title")
        duration_ms = data.get("duration_ms", 0)
        duration_s = max(0, int(duration_ms // 1000))
        explicit = bool(data.get("explicit", False))

        # Artists
        raw_artists = data.get("artists", [])
        artists = [a.get("name", "") for a in raw_artists if a.get("name")]

        # Album & thumbnail fallback
        if not album_name:
            album_obj = data.get("album")
            if isinstance(album_obj, dict):
                album_name = album_obj.get("name")
                if not thumbnail_url:
                    imgs = album_obj.get("images", [])
                    if imgs and isinstance(imgs, list) and isinstance(imgs[0], dict):
                        thumbnail_url = imgs[0].get("url")

        return SpotifyTrackMeta(
            spotify_id=spotify_id,
            title=title,
            artists=artists,
            duration_s=duration_s,
            album_name=album_name,
            thumbnail_url=thumbnail_url,
            explicit=explicit,
        )

    async def close(self) -> None:
        if not self._custom_client:
            await self._client.aclose()
