"""Tests for the pure poker engine."""

from __future__ import annotations

import pytest

from tests.card_helpers import cards
from yokai.gambling.engines.poker import (
    FLUSH,
    FULL_HOUSE,
    HIGH_CARD,
    PAIR,
    QUADS,
    STRAIGHT,
    STRAIGHT_FLUSH,
    TRIPS,
    TWO_PAIR,
    best_hand,
    describe,
    evaluate_five,
)

# ---- evaluator -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("As Kd 9h 5c 2d", HIGH_CARD),
        ("As Ad 9h 5c 2d", PAIR),
        ("As Ad 9h 9c 2d", TWO_PAIR),
        ("As Ad Ah 5c 2d", TRIPS),
        ("5s 6d 7h 8c 9d", STRAIGHT),
        ("As 2d 3h 4c 5d", STRAIGHT),  # the wheel
        ("10s Jd Qh Kc Ad", STRAIGHT),  # broadway
        ("As 3s 9s 5s 2s", FLUSH),
        ("As Ad Ah 5c 5d", FULL_HOUSE),
        ("As Ad Ah Ac 2d", QUADS),
        ("5s 6s 7s 8s 9s", STRAIGHT_FLUSH),
        ("As 2s 3s 4s 5s", STRAIGHT_FLUSH),  # steel wheel
    ],
)
def test_categories(text: str, category: int) -> None:
    assert evaluate_five(cards(text))[0] == category


def test_the_wheel_is_the_lowest_straight() -> None:
    assert evaluate_five(cards("As 2d 3h 4c 5d")) < evaluate_five(cards("2s 3d 4h 5c 6d"))


def test_no_wraparound_straight() -> None:
    assert evaluate_five(cards("Qs Kd Ah 2c 3d"))[0] == HIGH_CARD


def test_kickers_decide_equal_categories() -> None:
    assert evaluate_five(cards("As Ad Kh 5c 2d")) > evaluate_five(cards("As Ad Qh 5c 2d"))
    assert evaluate_five(cards("As Ad Kh Kc 2d")) > evaluate_five(cards("As Ad Qh Qc Kd"))
    assert evaluate_five(cards("Ks Kd Kh 2c 2d")) < evaluate_five(cards("As Ad Ah 3c 3d"))


def test_equal_hands_tie_regardless_of_suit() -> None:
    assert evaluate_five(cards("As Kd 9h 5c 2d")) == evaluate_five(cards("Ah Ks 9d 5h 2c"))


def test_best_hand_picks_the_best_five_of_seven() -> None:
    assert best_hand(cards("As Ad 2c 3d 9h 9c 9s"))[0] == FULL_HOUSE
    assert best_hand(cards("As Kd 2c 3d 9h 4c 5s"))[0] == STRAIGHT  # wheel hidden in seven
    assert best_hand(cards("2s 3s 4s 5s 6s Ad Ac")) == (STRAIGHT_FLUSH, 6)


def test_best_hand_rejects_wrong_sizes() -> None:
    with pytest.raises(ValueError):
        best_hand(cards("As Kd 9h 5c"))
    with pytest.raises(ValueError):
        evaluate_five(cards("As Kd 9h 5c 2d 3d"))


def test_describe_names_the_category() -> None:
    assert describe(evaluate_five(cards("As Ad Ah 5c 5d"))) == "a full house"
