"""Base interface for Spotify metadata providers."""

from __future__ import annotations

import abc

from yokai.music.spotify.models import SpotifyCollection, SpotifyTrackMeta


class SpotifyProvider(abc.ABC):
    """Abstract interface for retrieving metadata from Spotify."""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Friendly identifier for the provider (e.g. 'official' or 'scraper')."""

    @property
    @abc.abstractmethod
    def is_healthy(self) -> bool:
        """Return True if provider is operational and enabled."""

    @abc.abstractmethod
    async def get_track(self, spotify_id: str) -> SpotifyTrackMeta:
        """Fetch metadata for a single Spotify track."""

    @abc.abstractmethod
    async def get_album(self, album_id: str) -> SpotifyCollection:
        """Fetch metadata for an album and its tracks."""

    @abc.abstractmethod
    async def get_playlist(self, playlist_id: str, max_tracks: int = 100) -> SpotifyCollection:
        """Fetch metadata for a playlist and its tracks."""

    async def close(self) -> None:
        """Release underlying HTTP client or network resources."""
