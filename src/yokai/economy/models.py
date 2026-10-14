"""Data models for the Renown economy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(slots=True)
class Wallet:
    """A user's Renown wallet."""

    user_id: int
    balance: int
    last_daily_ts: Optional[int]
    daily_streak: int
    created_ts: int


@dataclass(slots=True)
class DailyClaimResult:
    """The outcome of a successful /daily claim."""

    payout: int
    streak_day: int
    new_balance: int


@dataclass(frozen=True, slots=True)
class BetSettlement:
    """A bet that was settled (not refunded): who staked what and what came back."""

    bet_id: int
    user_id: int
    game: str
    stake: int
    payout: int
    group_id: Optional[str]
