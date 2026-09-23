"""Data models and queue data structure for music playback."""

from __future__ import annotations

import enum
import math
import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional, Sequence

if TYPE_CHECKING:
    from yokai.music.spotify.models import SpotifyTrackMeta


class LoopMode(enum.Enum):
    """Playback looping behavior."""

    OFF = "off"
    TRACK = "track"
    QUEUE = "queue"


class PlayerState(enum.Enum):
    """Audio player lifecycle states."""

    IDLE = "idle"
    LOADING = "loading"
    PLAYING = "playing"
    PAUSED = "paused"


@dataclass(slots=True)
class Track:
    """Represents a playable audio track in the queue or history."""

    video_id: str
    title: str
    artist: Optional[str] = None
    duration_s: int = 0
    thumbnail_url: Optional[str] = None
    requester_id: int = 0
    origin: str = "link"  # link | search | import | recommendation
    spotify_id: Optional[str] = None
    is_pending_match: bool = False
    spotify_meta: Optional[SpotifyTrackMeta] = None

    @property
    def url(self) -> str:
        """Construct full YouTube watch URL."""
        return f"https://www.youtube.com/watch?v={self.video_id}"

    @property
    def duration_str(self) -> str:
        """Formatted duration string (MM:SS or HH:MM:SS)."""
        if self.duration_s <= 0:
            return "--:--"
        m, s = divmod(self.duration_s, 60)
        h, m = divmod(m, 60)
        if h > 0:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"


@dataclass(slots=True)
class StreamInfo:
    """Transient stream audio URL and headers required for FFmpeg."""

    url: str
    http_headers: dict[str, str] = field(default_factory=dict)
    expires_at: Optional[float] = None


class MusicQueue:
    """In-memory queue managing upcoming tracks, loop modes, and play history."""

    def __init__(self, max_size: int = 200, max_history: int = 100) -> None:
        self.max_size = max_size
        self.max_history = max_history
        self._upcoming: list[Track] = []
        self._history: list[Track] = []
        self.loop_mode: LoopMode = LoopMode.OFF

    def __len__(self) -> int:
        return len(self._upcoming)

    @property
    def is_empty(self) -> bool:
        return len(self._upcoming) == 0

    @property
    def upcoming(self) -> Sequence[Track]:
        return tuple(self._upcoming)

    @property
    def history(self) -> Sequence[Track]:
        return tuple(self._history)

    def add(self, track: Track) -> bool:
        """Add a track to the end of the queue.

        Returns False if the queue is at capacity.
        """
        if len(self._upcoming) >= self.max_size:
            return False
        self._upcoming.append(track)
        return True

    def add_many(self, tracks: Sequence[Track]) -> int:
        """Add multiple tracks up to max_size.

        Returns the number of tracks successfully added.
        """
        available = max(0, self.max_size - len(self._upcoming))
        to_add = list(tracks[:available])
        self._upcoming.extend(to_add)
        return len(to_add)

    def pop_next(self) -> Optional[Track]:
        """Remove and return the next track in the queue, or None if empty."""
        if not self._upcoming:
            return None
        return self._upcoming.pop(0)

    def peek_next(self) -> Optional[Track]:
        """Inspect the next track in the queue without removing it."""
        if not self._upcoming:
            return None
        return self._upcoming[0]

    def remove(self, position_1_indexed: int) -> Track:
        """Remove a track at a 1-based position index.

        Raises:
            IndexError: If position is out of range.
        """
        if position_1_indexed < 1 or position_1_indexed > len(self._upcoming):
            raise IndexError(f"Position #{position_1_indexed} is outside queue bounds.")
        return self._upcoming.pop(position_1_indexed - 1)

    def clear(self) -> int:
        """Clear all upcoming tracks from the queue.

        Returns the count of tracks removed.
        """
        count = len(self._upcoming)
        self._upcoming.clear()
        return count

    def shuffle(self) -> None:
        """Randomize the order of upcoming tracks in place."""
        random.shuffle(self._upcoming)

    def record_history(self, track: Track) -> None:
        """Record a played track into history, trimming to max_history."""
        self._history.append(track)
        if len(self._history) > self.max_history:
            self._history.pop(0)

    def get_page(self, page: int, per_page: int = 10) -> tuple[list[tuple[int, Track]], int, int]:
        """Get a paginated view of upcoming tracks.

        Returns:
            (list of (1-based position, track), total_pages, total_tracks)
        """
        total = len(self._upcoming)
        if total == 0:
            return [], 1, 0

        total_pages = max(1, math.ceil(total / per_page))
        safe_page = min(max(page, 1), total_pages)
        start_idx = (safe_page - 1) * per_page
        end_idx = start_idx + per_page

        items = [(idx + 1, self._upcoming[idx]) for idx in range(start_idx, min(end_idx, total))]
        return items, total_pages, total
