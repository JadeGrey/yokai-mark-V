"""The pure crash rules: a drawn crash point, a rising multiplier, server-judged cash-outs."""

from __future__ import annotations

import math
import random

import pytest

from yokai.errors import InvalidGameChoiceError, LobbyClosedError, NotAtTableError
from yokai.gambling.engines.crash import (
    GROWTH_PER_SECOND,
    MAX_TICK_S,
    MIN_TICK_S,
    CrashRound,
    draw_crash_point,
    multiplier_at,
    next_tick_interval,
    time_to_reach,
)


class FixedRandom(random.Random):
    """A random source whose random() always returns the same value."""

    def __init__(self, value: float) -> None:
        super().__init__()
        self.value = value

    def random(self) -> float:
        return self.value


def test_the_multiplier_starts_at_one_and_only_rises() -> None:
    assert multiplier_at(0) == 1.0 and multiplier_at(-5) == 1.0
    assert multiplier_at(1) < multiplier_at(2) < multiplier_at(30)
    assert multiplier_at(10) == pytest.approx(math.exp(GROWTH_PER_SECOND * 10))


def test_time_to_reach_inverts_the_multiplier() -> None:
    assert time_to_reach(multiplier_at(12.5)) == pytest.approx(12.5)
    assert time_to_reach(1.0) == 0.0


def test_the_crash_point_follows_the_house_edge_formula() -> None:
    assert draw_crash_point(FixedRandom(0.0), cap=100) == 100  # u = 1 -> 0.97 -> floor of 1.00
    assert draw_crash_point(FixedRandom(0.5), cap=100) == 194  # u = 0.5 -> 1.94
    assert draw_crash_point(FixedRandom(0.99), cap=100) == 9700  # u = 0.01 -> 97.00


def test_the_crash_point_is_capped() -> None:
    assert draw_crash_point(FixedRandom(0.9999999), cap=100) == 10_000
    assert draw_crash_point(FixedRandom(0.9999999), cap=5) == 500


def test_about_three_percent_of_rounds_crash_at_one_and_the_tail_matches() -> None:
    rng = random.Random(2024)
    points = [draw_crash_point(rng, cap=100) for _ in range(30_000)]
    assert min(points) == 100
    assert 0.02 < sum(p == 100 for p in points) / 30_000 < 0.04
    assert 0.46 < sum(p >= 200 for p in points) / 30_000 < 0.51  # about 0.97 / 2


def test_the_tick_interval_widens_when_edits_are_slow_and_never_leaves_its_bounds() -> None:
    assert next_tick_interval(0.05) == MIN_TICK_S
    assert next_tick_interval(0.8) == pytest.approx(1.6)
    assert next_tick_interval(30.0) == MAX_TICK_S


def test_a_cash_out_locks_the_multiplier_at_the_moment_of_the_press() -> None:
    crash = CrashRound([1, 2], crash_point=500)  # crashes at 5.00x, about 23 s in
    assert crash.cash_out(1, elapsed=5.0) == math.floor(multiplier_at(5.0) * 100)
    assert crash.cashed_out == {1: 141}


def test_a_press_at_or_after_the_crash_time_loses() -> None:
    crash = CrashRound([1], crash_point=250)
    assert not crash.has_crashed(crash.crash_time_s - 0.001)
    with pytest.raises(LobbyClosedError):
        crash.cash_out(1, elapsed=crash.crash_time_s)
    with pytest.raises(LobbyClosedError):
        crash.cash_out(1, elapsed=crash.crash_time_s + 5)
    assert crash.cashed_out == {}


def test_a_crash_point_of_one_crashes_before_anyone_can_cash_out() -> None:
    crash = CrashRound([1, 2], crash_point=100)
    assert crash.crash_time_s == 0.0 and crash.has_crashed(0.0) and crash.is_over(0.0)
    with pytest.raises(LobbyClosedError):
        crash.cash_out(1, elapsed=0.0)


def test_a_cash_out_never_exceeds_the_crash_point() -> None:
    crash = CrashRound([1], crash_point=250)
    just_before = crash.crash_time_s - 1e-9
    assert crash.cash_out(1, elapsed=just_before) <= 250


def test_cashing_out_twice_is_refused() -> None:
    crash = CrashRound([1], crash_point=500)
    crash.cash_out(1, elapsed=3.0)
    with pytest.raises(InvalidGameChoiceError, match="already"):
        crash.cash_out(1, elapsed=4.0)


def test_only_players_can_cash_out() -> None:
    crash = CrashRound([1], crash_point=500)
    with pytest.raises(NotAtTableError):
        crash.cash_out(9, elapsed=1.0)


def test_payouts_use_the_locked_multiplier_and_round_down() -> None:
    crash = CrashRound([1, 2, 3], crash_point=1000)
    crash.cash_out(1, elapsed=5.0)  # 1.41x
    crash.cash_out(2, elapsed=10.0)  # 2.01x
    result = crash.payouts({1: 100, 2: 55, 3: 500})
    assert result == {1: 141, 2: 55 * 201 // 100, 3: 0}


def test_the_round_ends_early_when_everyone_has_cashed_out() -> None:
    crash = CrashRound([1, 2], crash_point=1000)
    assert not crash.is_over(1.0)
    crash.cash_out(1, elapsed=1.0)
    assert not crash.all_cashed_out and not crash.is_over(2.0)
    crash.cash_out(2, elapsed=2.0)
    assert crash.all_cashed_out and crash.is_over(2.0)


def test_the_round_ends_at_the_crash() -> None:
    crash = CrashRound([1], crash_point=300)
    assert not crash.is_over(crash.crash_time_s - 0.01)
    assert crash.is_over(crash.crash_time_s)


def test_the_displayed_multiplier_never_passes_the_crash_point() -> None:
    crash = CrashRound([1], crash_point=250)
    assert crash.multiplier_hundredths_at(0.0) == 100
    assert crash.multiplier_hundredths_at(1_000.0) == 250


def test_a_round_needs_at_least_one_player() -> None:
    with pytest.raises(ValueError):
        CrashRound([], crash_point=200)
