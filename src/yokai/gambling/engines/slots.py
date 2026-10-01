"""Three-reel slots with a weighted symbol table and a fixed paytable (about 95% RTP)."""

from __future__ import annotations

import random
from dataclasses import dataclass
from fractions import Fraction
from itertools import product

SYMBOLS: tuple[str, ...] = ("CHERRY", "LEMON", "BELL", "BAR", "SEVEN")
WEIGHTS: dict[str, int] = {"CHERRY": 40, "LEMON": 30, "BELL": 15, "BAR": 10, "SEVEN": 5}

# Payouts are in tenths of the stake so they stay integers (14 = 1.4x).
TRIPLE_TENTHS: dict[str, int] = {
    "CHERRY": 50,
    "LEMON": 60,
    "BELL": 130,
    "BAR": 150,
    "SEVEN": 500,
}
TWO_CHERRY_TENTHS = 14


@dataclass(frozen=True, slots=True)
class SlotsResult:
    reels: tuple[str, str, str]
    multiplier_tenths: int

    def payout(self, stake: int) -> int:
        """Total returned for `stake` (stake included), floored to a whole Renown."""
        return stake * self.multiplier_tenths // 10


def score(reels: tuple[str, str, str]) -> int:
    """Payout multiplier in tenths of the stake for a set of reels."""
    if reels[0] == reels[1] == reels[2]:
        return TRIPLE_TENTHS[reels[0]]
    if reels.count("CHERRY") == 2:
        return TWO_CHERRY_TENTHS
    return 0


def spin(rng: random.Random) -> SlotsResult:
    """Spin three independent reels."""
    drawn = rng.choices(SYMBOLS, weights=[WEIGHTS[s] for s in SYMBOLS], k=3)
    reels = (drawn[0], drawn[1], drawn[2])
    return SlotsResult(reels=reels, multiplier_tenths=score(reels))


def exact_rtp() -> Fraction:
    """Exact expected return per unit staked, computed by enumerating every reel outcome."""
    total_weight = sum(WEIGHTS.values())
    expected = Fraction(0)
    for combo in product(SYMBOLS, repeat=3):
        probability = Fraction(1)
        for symbol in combo:
            probability *= Fraction(WEIGHTS[symbol], total_weight)
        expected += probability * score(combo)
    return expected / 10
