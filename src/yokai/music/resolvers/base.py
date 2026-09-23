"""Base Resolver interface for audio extraction and metadata resolution."""

from __future__ import annotations

from abc import ABC, abstractmethod

from yokai.music.models import StreamInfo, Track


class Resolver(ABC):
    """Abstract audio source resolver."""

    @abstractmethod
    async def resolve_query(self, query: str, requester_id: int) -> Track:
        """Resolve a free-text search query to a single playable Track."""
        ...

    @abstractmethod
    async def resolve_url(
        self, url: str, requester_id: int, max_tracks: int = 100
    ) -> tuple[Track | list[Track], bool]:
        """Resolve a direct URL to a Track or list of Tracks.

        Returns:
            (track_or_tracks, is_playlist)
        """
        ...

    @abstractmethod
    async def get_stream(self, track: Track) -> StreamInfo:
        """Fetch fresh direct playback audio stream URL and HTTP headers for FFmpeg."""
        ...
