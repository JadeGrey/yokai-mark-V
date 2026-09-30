"""Pure math for the /daily claim: cooldown, streak progression, and payout rolls.

Kept dependency-free and side-effect-free so the numbers can be tested without a database,
mirroring how `music/resolvers/matcher.py` isolates scoring from I/O.
"""

from __future__ import annotations

import random
from typing import Optional

DAILY_COOLDOWN_S = 86400
STREAK_RESET_THRESHOLD_S = 172800  # 48 hours
STREAK_CAP_DAYS = 7
MAX_STREAK_MULTIPLIER = 3.0


def cooldown_remaining_s(last_daily_ts: Optional[int], now: int) -> Optional[int]:
    """Seconds until the next claim is allowed, or None if a claim is allowed right now."""
    if last_daily_ts is None:
        return None
    elapsed = now - last_daily_ts
    if elapsed >= DAILY_COOLDOWN_S:
        return None
    return DAILY_COOLDOWN_S - elapsed


def next_streak(previous_streak: int, last_daily_ts: Optional[int], now: int) -> int:
    """Day count for this claim.

    Continues (+1) if the previous claim was 24-48h ago; resets to day 1 on a first claim or
    if 48h or more have passed. Callers only reach this after `cooldown_remaining_s` has
    already cleared the <24h case.
    """
    if last_daily_ts is None:
        return 1
    elapsed = now - last_daily_ts
    if elapsed >= STREAK_RESET_THRESHOLD_S:
        return 1
    return previous_streak + 1


def streak_multiplier(streak_day: int) -> float:
    """1.0x on day 1, growing linearly to 3.0x at day 7, held at 3.0x from day 7 onward."""
    capped_day = min(max(streak_day, 1), STREAK_CAP_DAYS)
    return 1.0 + (capped_day - 1) * (MAX_STREAK_MULTIPLIER - 1.0) / (STREAK_CAP_DAYS - 1)


def roll_daily_payout(base: int, streak_day: int, rng: random.Random) -> int:
    """Base * a random 1.0-1.5x roll * the streak multiplier, rounded to a whole number."""
    random_mult = rng.uniform(1.0, 1.5)
    return round(base * random_mult * streak_multiplier(streak_day))
