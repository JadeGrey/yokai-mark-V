"""Tests for the pure roulette engine (single-zero European wheel)."""

from __future__ import annotations

import random
from fractions import Fraction

import pytest

from yokai.errors import InvalidGameChoiceError
from yokai.gambling.engines import roulette
from yokai.gambling.engines.roulette import BetKind, RouletteBet


def test_wheel_has_eighteen_red_eighteen_black_and_one_green() -> None:
    colours = [roulette.colour(p) for p in range(37)]
    assert colours.count("red") == 18
    assert colours.count("black") == 18
    assert colours.count("green") == 1
    assert roulette.colour(0) == "green"
    assert roulette.colour(1) == "red"
    assert roulette.colour(2) == "black"


@pytest.mark.parametrize(
    ("bet", "pocket", "expected"),
    [
        (RouletteBet(BetKind.RED), 1, 2),
        (RouletteBet(BetKind.RED), 2, 0),
        (RouletteBet(BetKind.BLACK), 2, 2),
        (RouletteBet(BetKind.ODD), 17, 2),
        (RouletteBet(BetKind.EVEN), 18, 2),
        (RouletteBet(BetKind.LOW), 18, 2),
        (RouletteBet(BetKind.LOW), 19, 0),
        (RouletteBet(BetKind.HIGH), 19, 2),
        (RouletteBet(BetKind.DOZEN, 2), 13, 3),
        (RouletteBet(BetKind.DOZEN, 2), 24, 3),
        (RouletteBet(BetKind.DOZEN, 2), 25, 0),
        (RouletteBet(BetKind.COLUMN, 1), 34, 3),
        (RouletteBet(BetKind.COLUMN, 3), 36, 3),
        (RouletteBet(BetKind.COLUMN, 3), 35, 0),
        (RouletteBet(BetKind.STRAIGHT, 17), 17, 36),
        (RouletteBet(BetKind.STRAIGHT, 17), 18, 0),
        (RouletteBet(BetKind.STRAIGHT, 0), 0, 36),
    ],
)
def test_return_multiplier(bet: RouletteBet, pocket: int, expected: int) -> None:
    assert roulette.return_multiplier(bet, pocket) == expected


@pytest.mark.parametrize(
    "bet",
    [RouletteBet(k) for k in (BetKind.RED, BetKind.BLACK, BetKind.ODD, BetKind.EVEN)]
    + [RouletteBet(BetKind.LOW), RouletteBet(BetKind.HIGH)]
    + [RouletteBet(BetKind.DOZEN, n) for n in (1, 2, 3)]
    + [RouletteBet(BetKind.COLUMN, n) for n in (1, 2, 3)]
    + [RouletteBet(BetKind.STRAIGHT, n) for n in (0, 1, 17, 36)],
)
def test_every_bet_type_has_the_same_two_point_seven_percent_edge(
    bet: RouletteBet,
) -> None:
    assert roulette.expected_return(bet) == Fraction(36, 37)


def test_zero_loses_every_outside_bet() -> None:
    for kind in (
        BetKind.RED,
        BetKind.BLACK,
        BetKind.ODD,
        BetKind.EVEN,
        BetKind.LOW,
        BetKind.HIGH,
    ):
        assert roulette.return_multiplier(RouletteBet(kind), 0) == 0


def test_parse_bet_accepts_valid_choices() -> None:
    assert roulette.parse_bet("red", None) == RouletteBet(BetKind.RED)
    assert roulette.parse_bet("dozen", 3) == RouletteBet(BetKind.DOZEN, 3)
    assert roulette.parse_bet("straight", 0) == RouletteBet(BetKind.STRAIGHT, 0)


@pytest.mark.parametrize(
    ("choice", "pick"),
    [
        ("purple", None),  # unknown type
        ("red", 5),  # outside bets take no pick
        ("dozen", None),  # missing pick
        ("dozen", 4),
        ("column", 0),
        ("straight", 37),
        ("straight", -1),
        ("straight", None),
    ],
)
def test_parse_bet_rejects_invalid_choices(choice: str, pick: int | None) -> None:
    with pytest.raises(InvalidGameChoiceError):
        roulette.parse_bet(choice, pick)


def test_label_includes_the_pick() -> None:
    assert RouletteBet(BetKind.RED).label == "red"
    assert RouletteBet(BetKind.STRAIGHT, 17).label == "straight 17"


def test_spin_is_in_range_and_deterministic_for_a_seed() -> None:
    assert roulette.spin(random.Random(7)) == roulette.spin(random.Random(7))
    pockets = {roulette.spin(random.Random(seed)) for seed in range(300)}
    assert pockets <= set(range(37))
    assert len(pockets) > 20
