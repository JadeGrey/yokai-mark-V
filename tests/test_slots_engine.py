"""Tests for the pure slots engine."""

from __future__ import annotations

import random

import pytest

from yokai.gambling.engines import slots


@pytest.mark.parametrize(
    ("reels", "tenths"),
    [
        (("SEVEN", "SEVEN", "SEVEN"), 500),
        (("BAR", "BAR", "BAR"), 150),
        (("BELL", "BELL", "BELL"), 130),
        (("LEMON", "LEMON", "LEMON"), 60),
        (("CHERRY", "CHERRY", "CHERRY"), 50),  # three cherries is a triple, not "two cherries"
        (("CHERRY", "CHERRY", "LEMON"), 14),
        (("CHERRY", "LEMON", "CHERRY"), 14),
        (("LEMON", "CHERRY", "CHERRY"), 14),
        (("CHERRY", "LEMON", "BELL"), 0),
        (("LEMON", "LEMON", "BELL"), 0),
        (("BAR", "SEVEN", "BELL"), 0),
    ],
)
def test_score_table(reels: tuple[str, str, str], tenths: int) -> None:
    assert slots.score(reels) == tenths


def test_payout_floors_and_never_over_pays() -> None:
    """Review Focus: odd stakes round down."""
    result = slots.SlotsResult(("CHERRY", "CHERRY", "LEMON"), 14)
    assert result.payout(10) == 14
    assert result.payout(15) == 21  # 15 * 1.4 = 21 exactly
    assert result.payout(11) == 15  # 15.4 floors to 15
    assert slots.SlotsResult(("BAR", "LEMON", "BELL"), 0).payout(100) == 0


def test_exact_rtp_is_about_ninety_five_percent() -> None:
    rtp = float(slots.exact_rtp())
    assert 0.94 <= rtp <= 0.96
    assert rtp == pytest.approx(0.950325)


def test_spin_is_deterministic_for_a_seed_and_uses_known_symbols() -> None:
    first = slots.spin(random.Random(1))
    second = slots.spin(random.Random(1))
    assert first == second
    assert all(symbol in slots.SYMBOLS for symbol in first.reels)
    assert first.multiplier_tenths == slots.score(first.reels)


def test_simulated_return_is_near_the_exact_rtp() -> None:
    rng = random.Random(12345)
    spins = 20000
    returned = sum(slots.spin(rng).multiplier_tenths for _ in range(spins)) / 10
    assert 0.85 <= returned / spins <= 1.05
