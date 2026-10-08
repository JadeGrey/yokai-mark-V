"""The pure jackpot rules: a stake-weighted draw, the winner takes the pot minus the rake."""

from __future__ import annotations

import random

import pytest

from yokai.gambling.engines.jackpot import draw_winner, payouts, win_chances


def test_a_single_entrant_always_wins() -> None:
    assert draw_winner([(7, 100)], random.Random(1)) == 7


def test_the_draw_is_deterministic_for_a_seed() -> None:
    entries = [(1, 100), (2, 100), (3, 100)]
    assert draw_winner(entries, random.Random(5)) == draw_winner(entries, random.Random(5))


def test_a_stake_three_times_larger_wins_about_three_times_as_often() -> None:
    rng = random.Random(12345)
    entries = [(1, 100), (2, 300)]
    wins = sum(draw_winner(entries, rng) == 2 for _ in range(20_000))
    assert 0.72 < wins / 20_000 < 0.78


def test_every_entrant_can_win() -> None:
    rng = random.Random(9)
    entries = [(1, 50), (2, 50), (3, 50), (4, 50)]
    assert {draw_winner(entries, rng) for _ in range(500)} == {1, 2, 3, 4}


def test_the_roll_boundaries_pick_the_first_and_last_entrant() -> None:
    class Roll(random.Random):
        def __init__(self, value: int) -> None:
            super().__init__()
            self.value = value

        def randrange(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            return self.value

    entries = [(1, 100), (2, 200), (3, 300)]
    assert draw_winner(entries, Roll(0)) == 1
    assert draw_winner(entries, Roll(99)) == 1
    assert draw_winner(entries, Roll(100)) == 2
    assert draw_winner(entries, Roll(599)) == 3


@pytest.mark.parametrize("entries", [[], [(1, 0)], [(1, -5), (2, 10)]])
def test_an_empty_or_non_positive_draw_is_refused(entries: list[tuple[int, int]]) -> None:
    with pytest.raises(ValueError):
        draw_winner(entries, random.Random(1))


def test_win_chances_are_stake_shares() -> None:
    chances = win_chances([(1, 100), (2, 300)])
    assert chances == {1: 0.25, 2: 0.75}


def test_payouts_give_the_winner_the_pot_minus_the_rake() -> None:
    result, prize, rake = payouts([(1, 100), (2, 300)], winner_id=2, rake_percent=2)
    assert (prize, rake) == (392, 8)
    assert result == {1: 0, 2: 392}


def test_the_rake_rounds_down_and_never_exceeds_the_pot() -> None:
    result, prize, rake = payouts([(1, 10), (2, 15)], winner_id=1, rake_percent=2)
    assert (prize, rake) == (25, 0)
    assert sum(result.values()) + rake == 25


def test_a_zero_rake_pays_the_whole_pot() -> None:
    result, prize, rake = payouts([(1, 100), (2, 100)], winner_id=1, rake_percent=0)
    assert (prize, rake) == (200, 0) and result[1] == 200


def test_the_winner_must_be_one_of_the_entrants() -> None:
    with pytest.raises(ValueError):
        payouts([(1, 100), (2, 100)], winner_id=3, rake_percent=2)
