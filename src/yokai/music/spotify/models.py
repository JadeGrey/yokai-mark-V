"""Data models for Spotify metadata representations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(slots=True)
class SpotifyTrackMeta:
    """Metadata extracted for a single Spotify track."""

    spotify_id: str
    title: str
    artists: list[str] = field(default_factory=list)
    duration_s: int = 0
    album_name: Optional[str] = None
    thumbnail_url: Optional[str] = None
    explicit: bool = False

    @property
    def primary_artist(self) -> str:
        """First credited artist or Unknown."""
        return self.artists[0] if self.artists else "Unknown Artist"

    @property
    def artist_summary(self) -> str:
        """Comma-separated artist string."""
        return ", ".join(self.artists) if self.artists else "Unknown Artist"

    @property
    def search_query(self) -> str:
        """Clean search string for YouTube/YTMusic candidate lookup."""
        return f"{self.primary_artist} {self.title}".strip()


@dataclass(slots=True)
class SpotifyCollection:
    """Represents a Spotify album or playlist collection."""

    name: str
    tracks: list[SpotifyTrackMeta] = field(default_factory=list)
    total: int = 0
    partial: bool = False

    @property
    def count(self) -> int:
        return len(self.tracks)
