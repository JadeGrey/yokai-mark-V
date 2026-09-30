"""Tests for pure /daily claim math: cooldown, streak progression, and payout rolls."""

from __future__ import annotations

from unittest.mock import MagicMock

from yokai.economy.daily import (
    cooldown_remaining_s,
    next_streak,
    roll_daily_payout,
    streak_multiplier,
)


def test_cooldown_remaining_is_none_for_a_first_claim() -> None:
    assert cooldown_remaining_s(None, now=1000) is None


def test_cooldown_remaining_blocks_within_24_hours() -> None:
    now = 100_000
    assert cooldown_remaining_s(now - 3600, now) == 86400 - 3600


def test_cooldown_remaining_clears_after_24_hours() -> None:
    now = 100_000
    assert cooldown_remaining_s(now - 86400, now) is None
    assert cooldown_remaining_s(now - 90000, now) is None


def test_next_streak_starts_at_one_for_a_first_claim() -> None:
    assert next_streak(previous_streak=0, last_daily_ts=None, now=100_000) == 1


def test_next_streak_continues_between_24_and_48_hours() -> None:
    now = 200_000
    assert next_streak(previous_streak=3, last_daily_ts=now - 90000, now=now) == 4


def test_next_streak_resets_after_48_hours() -> None:
    now = 300_000
    assert next_streak(previous_streak=6, last_daily_ts=now - 172800, now=now) == 1
    assert next_streak(previous_streak=6, last_daily_ts=now - 200000, now=now) == 1


def test_streak_multiplier_grows_linearly_and_caps_at_day_seven() -> None:
    assert streak_multiplier(1) == 1.0
    assert streak_multiplier(4) == 2.0
    assert streak_multiplier(7) == 3.0
    assert streak_multiplier(8) == 3.0
    assert streak_multiplier(100) == 3.0


def test_roll_daily_payout_applies_random_and_streak_multipliers() -> None:
    rng = MagicMock()
    rng.uniform.return_value = 1.2
    assert roll_daily_payout(base=100, streak_day=1, rng=rng) == round(100 * 1.2 * 1.0)
    assert roll_daily_payout(base=100, streak_day=7, rng=rng) == round(100 * 1.2 * 3.0)
    rng.uniform.assert_called_with(1.0, 1.5)
