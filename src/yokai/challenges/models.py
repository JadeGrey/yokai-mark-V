"""Challenge data types: templates, activity events and results."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Optional, Union

CATEGORIES: Final = ("music", "games", "voice")
COUNTER_METRICS: Final = frozenset({"rounds_played", "rounds_won", "voice_minutes"})
DISTINCT_METRICS: Final = frozenset({"tracks", "artists", "games"})


@dataclass(frozen=True, slots=True)
class Template:
    """One kind of challenge in the pool."""

    id: str
    period: str  # "daily" | "weekly"
    category: str  # one of CATEGORIES
    metric: str  # one of COUNTER_METRICS | DISTINCT_METRICS
    target: int
    reward: int  # base Renown, before CHALLENGE_REWARD_SCALE
    title: str
    # only count these game names (rounds_played / rounds_won)
    game: Optional[frozenset[str]] = None


@dataclass(frozen=True, slots=True)
class Advance:
    """Result of advancing one progress row."""

    progress: int
    completed_now: bool


@dataclass(frozen=True, slots=True)
class ProgressView:
    """One challenge as shown to a user."""

    template: Template
    progress: int
    completed: bool
    reward: int  # after scaling


@dataclass(frozen=True, slots=True)
class Completion:
    """A challenge a user just finished, queued for a notice."""

    user_id: int
    template_id: str
    title: str
    reward: int
    channel_id: Optional[int]


@dataclass(frozen=True, slots=True)
class TrackListened:
    """A track finished (or was skipped past halfway) with company in the call."""

    user_ids: tuple[int, ...]
    video_id: str
    artist: Optional[str]
    channel_id: Optional[int] = None


@dataclass(frozen=True, slots=True)
class RoundPlayed:
    """A bet settled (not refunded). `round_key` groups bets of one round (blackjack double)."""

    user_id: int
    game: str
    stake: int
    payout: int
    round_key: str


@dataclass(frozen=True, slots=True)
class VoiceMinutes:
    """Time spent in a voice channel with at least one other human."""

    user_id: int
    minutes: int


Event = Union[TrackListened, RoundPlayed, VoiceMinutes]


def counts_as_listened(outcome: str, listened_s: int, duration_s: int) -> bool:
    """A track counts when it finished, or was skipped after at least half of it."""
    if outcome == "completed":
        return True
    if outcome == "skipped":
        return duration_s > 0 and listened_s * 2 >= duration_s
    return False
