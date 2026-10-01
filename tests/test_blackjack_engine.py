"""Tests for the pure blackjack engine."""

from __future__ import annotations

import random

import pytest

from tests.card_helpers import cards
from yokai.gambling.engines.blackjack import (
    BlackjackGame,
    Card,
    Outcome,
    build_shoe,
    hand_value,
    is_blackjack,
    payout_for,
)

# Shoe order: player1, dealer1, player2, dealer2, then draws.


def test_card_str_is_poker_notation_without_suit_glyphs() -> None:
    assert str(Card(1, "s")) == "As"
    assert str(Card(10, "d")) == "10d"
    assert str(Card(13, "h")) == "Kh"


def test_hand_value_handles_soft_and_hard_aces() -> None:
    assert hand_value(cards("As Ad")) == (12, True)
    assert hand_value(cards("As Ad 9c")) == (21, True)
    assert hand_value(cards("As Kd 5c")) == (16, False)
    assert hand_value(cards("As Kd")) == (21, True)
    assert hand_value(cards("10h 9d 5c")) == (24, False)


def test_is_blackjack_needs_exactly_two_cards() -> None:
    assert is_blackjack(cards("As Kd"))
    assert not is_blackjack(cards("7h 7d 7c"))


def test_payout_for_floors_the_three_to_two_bonus() -> None:
    """Review Focus: odd stakes never over-pay."""
    assert payout_for(Outcome.BLACKJACK, 10) == 25
    assert payout_for(Outcome.BLACKJACK, 15) == 37
    assert payout_for(Outcome.WIN, 10) == 20
    assert payout_for(Outcome.PUSH, 10) == 10
    assert payout_for(Outcome.LOSE, 10) == 0


def test_player_natural_against_a_non_natural_dealer_pays_blackjack() -> None:
    game = BlackjackGame(cards("As Kd Ks 9h"))
    assert game.finished and game.outcome is Outcome.BLACKJACK


def test_both_naturals_push() -> None:
    game = BlackjackGame(cards("As Ad Kc Kh"))
    assert game.finished and game.outcome is Outcome.PUSH


def test_dealer_natural_ends_the_hand_immediately() -> None:
    game = BlackjackGame(cards("10h As 9c Kd"))
    assert game.finished and game.outcome is Outcome.LOSE
    assert not game.player_busted


def test_stand_makes_the_dealer_draw_to_seventeen() -> None:
    game = BlackjackGame(cards("10h 6d 9c 5s 7c"))
    assert not game.finished
    game.stand()
    assert [str(c) for c in game.dealer] == ["6d", "5s", "7c"]
    assert game.outcome is Outcome.WIN  # 19 against 18


def test_dealer_stands_on_soft_seventeen() -> None:
    game = BlackjackGame(cards("10h As 9c 6d 5c"))
    game.stand()
    assert len(game.dealer) == 2
    assert game.outcome is Outcome.WIN  # 19 against soft 17


def test_dealer_bust_is_a_win() -> None:
    game = BlackjackGame(cards("10h 6d 8c 10s 9c"))
    game.stand()
    assert hand_value(game.dealer)[0] == 25
    assert game.outcome is Outcome.WIN


def test_equal_totals_push_and_a_lower_total_loses() -> None:
    push = BlackjackGame(cards("10h 10d 8c 8s"))
    push.stand()
    assert push.outcome is Outcome.PUSH
    lose = BlackjackGame(cards("10h 10d 7c 9s"))
    lose.stand()
    assert lose.outcome is Outcome.LOSE


def test_hit_bust_loses_without_the_dealer_drawing() -> None:
    game = BlackjackGame(cards("10h 9d 6c 8s 10c"))
    game.hit()
    assert game.finished and game.player_busted and game.outcome is Outcome.LOSE
    assert len(game.dealer) == 2


def test_hitting_to_twenty_one_stands_automatically() -> None:
    game = BlackjackGame(cards("5h 9d 6c 8s 10c"))
    game.hit()
    assert game.finished and game.outcome is Outcome.WIN  # 21 against 17


def test_double_draws_exactly_one_card_then_the_dealer_plays() -> None:
    game = BlackjackGame(cards("5h 9d 6c 8s 10c"))
    assert game.can_double
    game.double()
    assert game.doubled and game.finished
    assert len(game.player) == 3
    assert game.outcome is Outcome.WIN


def test_double_bust_loses() -> None:
    game = BlackjackGame(cards("10h 9d 6c 8s 10c"))
    game.double()
    assert game.player_busted and game.outcome is Outcome.LOSE


def test_double_is_not_allowed_after_a_hit() -> None:
    game = BlackjackGame(cards("2h 9d 3c 8s 2c 2d"))
    game.hit()
    assert not game.can_double
    with pytest.raises(ValueError):
        game.double()


def test_actions_after_the_hand_is_finished_raise() -> None:
    game = BlackjackGame(cards("As Kd Ks 9h"))
    for action in (game.hit, game.stand):
        with pytest.raises(ValueError):
            action()


def test_shoe_has_six_decks_and_is_deterministic_for_a_seed() -> None:
    shoe = build_shoe(random.Random(3))
    assert len(shoe) == 312
    assert sum(1 for c in shoe if c == Card(1, "s")) == 6
    assert build_shoe(random.Random(3)) == build_shoe(random.Random(3))
