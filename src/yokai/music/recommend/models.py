"""Data models for recommendations subsystem."""

from __future__ import annotations

from dataclasses import dataclass, field

from yokai.music.models import Track


@dataclass(slots=True)
class Recommendation:
    """Represents a set of recommended tracks generated from a seed track."""

    tracks: list[Track] = field(default_factory=list)
    seed: Track = field(default_factory=lambda: Track(video_id="", title="Unknown"))
    strategy: str = "ytmusic_radio"

    @property
    def count(self) -> int:
        return len(self.tracks)
