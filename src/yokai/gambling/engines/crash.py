"""Crash rules: a multiplier rises until a crash point drawn up front; cash out before it.

Multipliers are whole hundredths (250 means 2.50x) so payouts are exact integers.
"""

from __future__ import annotations

import math
import random
from typing import Mapping, Sequence

from yokai.errors import InvalidGameChoiceError, LobbyClosedError, NotAtTableError

GROWTH_PER_SECOND = 0.07  # the multiplier is exp(0.07 * seconds): 2x after ~10 s, 100x after ~66 s
HOUSE_FACTOR = 0.97  # a 3% edge: P(crash point >= m) = 0.97 / m
MIN_TICK_S = 1.0
MAX_TICK_S = 3.0


def multiplier_at(elapsed_s: float) -> float:
    """The multiplier `elapsed_s` seconds into a round."""
    return math.exp(GROWTH_PER_SECOND * max(0.0, elapsed_s))


def time_to_reach(multiplier: float) -> float:
    """Seconds into a round at which the multiplier reaches `multiplier` (at least 1.0)."""
    return math.log(max(1.0, multiplier)) / GROWTH_PER_SECOND


def draw_crash_point(rng: random.Random, cap: int) -> int:
    """The crash point in hundredths, drawn once: max(1.00, 0.97 / U) with U in (0, 1]."""
    u = 1.0 - rng.random()
    raw = HOUSE_FACTOR / u
    # The epsilon keeps a mathematically exact value (97.00) from floating-point wobble below it.
    return min(cap * 100, max(100, math.floor(raw * 100 + 1e-9)))


def next_tick_interval(last_edit_s: float) -> float:
    """Seconds to wait before the next message edit: slower when the last edit was slow."""
    return min(MAX_TICK_S, max(MIN_TICK_S, 2.0 * last_edit_s))


class CrashRound:
    """One round: who is in, who cashed out at what multiplier, and when it crashes."""

    def __init__(self, user_ids: Sequence[int], crash_point: int) -> None:
        if not user_ids:
            raise ValueError("a crash round needs at least one player")
        self.user_ids = list(user_ids)
        self.crash_point = crash_point
        self.crash_time_s = time_to_reach(crash_point / 100)
        self.cashed_out: dict[int, int] = {}

    def has_crashed(self, elapsed_s: float) -> bool:
        return elapsed_s >= self.crash_time_s

    @property
    def all_cashed_out(self) -> bool:
        return len(self.cashed_out) == len(self.user_ids)

    def is_over(self, elapsed_s: float) -> bool:
        return self.has_crashed(elapsed_s) or self.all_cashed_out

    def multiplier_hundredths_at(self, elapsed_s: float) -> int:
        """The multiplier to show, never above the crash point."""
        return min(self.crash_point, math.floor(multiplier_at(elapsed_s) * 100))

    def cash_out(self, user_id: int, elapsed: float) -> int:
        """Lock in the multiplier (hundredths) at the server's `elapsed` seconds, or raise."""
        if user_id not in self.user_ids:
            raise NotAtTableError()
        if user_id in self.cashed_out:
            raise InvalidGameChoiceError("You already cashed out.")
        if self.has_crashed(elapsed):
            raise LobbyClosedError("It already crashed.", user_hint=None)
        locked = self.multiplier_hundredths_at(elapsed)
        self.cashed_out[user_id] = locked
        return locked

    def payouts(self, stakes: Mapping[int, int]) -> dict[int, int]:
        """Total returned per player: stake times the locked multiplier, 0 if never cashed out."""
        return {
            user_id: stakes[user_id] * self.cashed_out[user_id] // 100
            if user_id in self.cashed_out
            else 0
            for user_id in self.user_ids
        }
