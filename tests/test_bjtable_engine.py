"""The shared-dealer blackjack table: turn order, naturals, dealer play and payouts."""

from __future__ import annotations

import pytest

from tests.card_helpers import cards
from yokai.errors import LobbyClosedError, NotYourTurnError
from yokai.gambling.engines.bjtable import BlackjackTable
from yokai.gambling.engines.blackjack import Outcome, compare_hands


def _table(shoe: str, users: list[int]) -> BlackjackTable:
    return BlackjackTable(cards(shoe), users)


def test_the_deal_goes_player_by_player_then_the_dealer_twice() -> None:
    table = _table("2c 3c 4c 5c 6c 7c", [1, 2])
    assert [str(c) for c in table.hands[1]] == ["2c", "5c"]
    assert [str(c) for c in table.hands[2]] == ["3c", "6c"]
    assert [str(c) for c in table.dealer] == ["4c", "7c"]


def test_players_act_in_join_order_and_only_when_it_is_their_turn() -> None:
    table = _table("10h 9d 7h 8c 9s 10d", [1, 2])  # 18, 18 against a dealer 17
    assert table.current == 1
    with pytest.raises(NotYourTurnError):
        table.stand(2)
    with pytest.raises(NotYourTurnError):
        table.hit(2)
    table.stand(1)
    assert table.current == 2 and not table.players_finished
    table.stand(2)
    assert table.current is None and table.players_finished


def test_a_hit_that_busts_moves_on_to_the_next_player() -> None:
    table = _table("10h 9d 7h 6c 9s 10d 10c", [1, 2])  # player 1 has 16 and hits a ten
    table.hit(1)
    assert table.status_of(1) == "bust" and 1 in table.busted
    assert table.current == 2


def test_reaching_twenty_one_stands_automatically() -> None:
    table = _table("10h 9d 7h 5c 9s 10d 6c", [1, 2])  # 15 + a six = 21
    table.hit(1)
    assert table.status_of(1) == "21" and table.current == 2


def test_a_natural_skips_that_players_turn() -> None:
    table = _table("Ah 9d 7h Kc 9s 10d", [1, 2])  # player 1 has a natural
    assert table.status_of(1) == "blackjack" and table.current == 2


def test_a_dealer_natural_ends_the_round_at_the_deal() -> None:
    table = _table("10h 9d As 8c 9s Kd", [1, 2])  # dealer Ace + King
    assert table.current is None and table.players_finished
    table.play_dealer()
    assert len(table.dealer) == 2  # nothing to draw
    assert table.outcomes() == {1: Outcome.LOSE, 2: Outcome.LOSE}


def test_a_player_natural_pushes_against_a_dealer_natural() -> None:
    table = _table("Ah 9d As Kc 9s Kd", [1, 2])
    assert table.outcomes() == {1: Outcome.PUSH, 2: Outcome.LOSE}


def test_the_dealer_draws_to_seventeen_when_a_player_is_still_live() -> None:
    table = _table("10h 9d 5h 8c 9s 6d 7c", [1, 2])  # dealer 5 + 6 = 11, draws a seven
    table.stand(1)
    table.stand(2)
    table.play_dealer()
    assert [str(c) for c in table.dealer] == ["5h", "6d", "7c"]
    assert table.outcomes() == {1: Outcome.PUSH, 2: Outcome.PUSH}  # 18 against 18


def test_the_dealer_does_not_draw_when_every_player_busted() -> None:
    table = _table("10h 10d 5h 6c 6s 6d 10c 10s", [1, 2])  # both players hit to a bust
    table.hit(1)
    table.hit(2)
    table.play_dealer()
    assert len(table.dealer) == 2  # 5 + 6 = 11 stays untouched
    assert table.outcomes() == {1: Outcome.LOSE, 2: Outcome.LOSE}


def test_the_dealer_does_not_draw_when_every_player_has_a_natural() -> None:
    table = _table("Ah Kd 5h Kc Ad 6d", [1, 2])
    assert table.players_finished
    table.play_dealer()
    assert len(table.dealer) == 2
    assert table.outcomes() == {1: Outcome.BLACKJACK, 2: Outcome.BLACKJACK}


def test_a_dealer_bust_pays_every_standing_player() -> None:
    table = _table("10h 9d 6h 8c 9s 10d 10c", [1, 2])  # dealer 16 draws a ten and busts
    table.stand(1)
    table.stand(2)
    table.play_dealer()
    assert table.outcomes() == {1: Outcome.WIN, 2: Outcome.WIN}


def test_payouts_follow_the_solo_rules_including_three_to_two() -> None:
    table = _table("Ah 10d 9h Kc 8s 8d", [1, 2])  # natural for player 1, 18 for player 2
    table.stand(2)
    table.play_dealer()
    # dealer 9 + 8 = 17 stands: player 1 natural, player 2 wins 18 against 17
    assert table.payouts({1: 5, 2: 100}) == {1: 5 + 7, 2: 200}


def test_the_dealer_plays_once() -> None:
    table = _table("10h 9d 5h 8c 9s 6d 7c 2c", [1, 2])
    table.stand(1)
    table.stand(2)
    table.play_dealer()
    table.play_dealer()
    assert len(table.dealer) == 3


def test_the_dealer_cannot_play_before_the_players_are_done() -> None:
    table = _table("10h 9d 7h 8c 9s 10d", [1, 2])
    with pytest.raises(ValueError):
        table.play_dealer()


def test_no_one_can_act_once_the_round_is_over() -> None:
    table = _table("10h 9d As 8c 9s Kd", [1, 2])  # dealer natural
    with pytest.raises(LobbyClosedError):
        table.hit(1)


def test_compare_hands_matches_the_solo_game() -> None:
    assert compare_hands(cards("10h 6c 10d"), cards("9d 8s")) is Outcome.LOSE  # player bust
    assert compare_hands(cards("Ah Kd"), cards("9d 8s")) is Outcome.BLACKJACK
    assert compare_hands(cards("Ah Kd"), cards("As Kc")) is Outcome.PUSH
    assert compare_hands(cards("10h 9c"), cards("As Kc")) is Outcome.LOSE
    assert compare_hands(cards("10h 9c"), cards("10d 6s 10c")) is Outcome.WIN
    assert compare_hands(cards("10h 8c"), cards("9d 9s")) is Outcome.PUSH
