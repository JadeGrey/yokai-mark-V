"""The pure duel rules: closest pick to a hidden number wins, a tie refunds."""

from __future__ import annotations

import random

import pytest

from yokai.errors import InvalidGameChoiceError, NotAtTableError
from yokai.gambling.engines.duel import (
    NUMBER_MAX,
    NUMBER_MIN,
    DuelRound,
    decide,
    draw_target,
    prize_after_rake,
)


def test_the_hidden_number_is_always_in_range() -> None:
    rng = random.Random(7)
    drawn = {draw_target(rng) for _ in range(500)}
    assert drawn <= set(range(NUMBER_MIN, NUMBER_MAX + 1))
    assert drawn == set(range(NUMBER_MIN, NUMBER_MAX + 1))


def test_the_closer_pick_wins() -> None:
    assert decide({1: 4, 2: 9}, target=5) == 1
    assert decide({1: 4, 2: 6}, target=7) == 2
    assert decide({1: 10, 2: 1}, target=10) == 1


def test_equal_distance_is_a_tie() -> None:
    assert decide({1: 3, 2: 7}, target=5) is None


def test_the_same_pick_is_a_tie() -> None:
    assert decide({1: 6, 2: 6}, target=2) is None


def test_the_rake_is_a_percentage_rounded_down() -> None:
    assert prize_after_rake(100, 2) == (98, 2)
    assert prize_after_rake(101, 2) == (99, 2)
    assert prize_after_rake(20, 2) == (20, 0)
    assert prize_after_rake(200, 0) == (200, 0)


def test_picks_are_locked_once_made() -> None:
    duel = DuelRound([1, 2], target=5)
    duel.submit(1, 4)
    with pytest.raises(InvalidGameChoiceError, match="already"):
        duel.submit(1, 9)
    assert duel.has_picked(1) and not duel.has_picked(2)
    assert duel.picked_ids == [1] and not duel.ready


def test_only_the_two_players_can_pick() -> None:
    duel = DuelRound([1, 2], target=5)
    with pytest.raises(NotAtTableError):
        duel.submit(3, 5)


@pytest.mark.parametrize("bad", [0, 11, -1, 100])
def test_a_pick_outside_one_to_ten_is_refused(bad: int) -> None:
    duel = DuelRound([1, 2], target=5)
    with pytest.raises(InvalidGameChoiceError):
        duel.submit(1, bad)
    assert not duel.has_picked(1)


def test_the_round_is_ready_when_both_have_picked() -> None:
    duel = DuelRound([1, 2], target=5)
    duel.submit(1, 4)
    duel.submit(2, 8)
    assert duel.ready
    outcome = duel.outcome()
    assert (outcome.target, outcome.picks, outcome.winner_id) == (5, {1: 4, 2: 8}, 1)
    assert not outcome.is_tie


def test_missing_picks_are_filled_at_random_and_reported() -> None:
    duel = DuelRound([1, 2], target=5)
    duel.submit(1, 4)
    filled = duel.fill_missing(random.Random(3))
    assert filled == [2] and duel.ready
    assert NUMBER_MIN <= duel.outcome().picks[2] <= NUMBER_MAX


def test_the_outcome_needs_both_picks() -> None:
    duel = DuelRound([1, 2], target=5)
    duel.submit(1, 4)
    with pytest.raises(ValueError):
        duel.outcome()


def test_a_round_needs_exactly_two_distinct_players() -> None:
    with pytest.raises(ValueError):
        DuelRound([1], target=5)
    with pytest.raises(ValueError):
        DuelRound([1, 1], target=5)
