"""Tests for the pure poker engine."""

from __future__ import annotations

import random

import pytest

from tests.card_helpers import cards
from yokai.errors import InvalidPokerActionError, NotYourTurnError
from yokai.gambling.engines.blackjack import Card
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
    PokerHand,
    Street,
    best_hand,
    build_deck,
    describe,
    evaluate_five,
    split_pots,
)

A, B, C, D = 1, 2, 3, 4


def stacked_deck(order: list[int], button: int, holes: dict[int, str], board: str) -> list[Card]:
    """A deck that deals `holes` and then `board` (flop, turn, river) to a hand with `order`."""
    start = order.index(button)
    seats = [order[(start + i) % len(order)] for i in range(1, len(order) + 1)]
    first = [cards(holes[u])[0] for u in seats]
    second = [cards(holes[u])[1] for u in seats]
    chosen = [*first, *second, *cards(board)]
    rest = [c for c in build_deck(random.Random(0)) if str(c) not in {str(x) for x in chosen}]
    return [*chosen, *rest]


def hand(
    stacks: dict[int, int],
    holes: dict[int, str],
    board: str,
    button: int = A,
    blinds: tuple[int, int] = (5, 10),
) -> PokerHand:
    order = list(stacks)
    return PokerHand(stacks, order, button, *blinds, stacked_deck(order, button, holes, board))


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


# ---- side pots -------------------------------------------------------------------------------


def test_split_pots_with_two_all_in_levels() -> None:
    pots = split_pots({A: 100, B: 300, C: 300, D: 50}, {A, B, C, D})
    assert pots == [(200, (A, B, C, D)), (150, (A, B, C)), (400, (B, C))]


def test_split_pots_ignores_folded_players_as_winners_but_keeps_their_chips() -> None:
    pots = split_pots({A: 100, B: 100, C: 40}, {A, B})
    assert sum(amount for amount, _ in pots) == 240
    assert all(C not in eligible for _, eligible in pots)


# ---- blinds and action order ---------------------------------------------------------------


def test_three_handed_blinds_and_first_to_act() -> None:
    h = hand({A: 1000, B: 1000, C: 1000}, {A: "2c 3d", B: "4c 5d", C: "6c 7d"}, "8h 9h 10h Jd Qs")
    # button A, small blind B, big blind C, first to act is A (left of the big blind)
    assert (h.bet[B], h.bet[C]) == (5, 10)
    assert h.current == A
    assert h.pot == 15


def test_heads_up_button_is_small_blind_and_acts_first_preflop_then_last() -> None:
    h = hand({A: 1000, B: 1000}, {A: "2c 3d", B: "4c 5d"}, "8h 9h 10h Jd Qs")
    assert (h.bet[A], h.bet[B]) == (5, 10)
    assert h.current == A
    h.call(A)
    assert h.current == B  # the big blind keeps the option
    h.check(B)
    assert h.street is Street.FLOP
    assert h.current == B  # postflop the non-button acts first


def test_short_stack_posts_a_partial_blind_and_is_all_in() -> None:
    h = hand({A: 1000, B: 3, C: 1000}, {A: "2c 3d", B: "4c 5d", C: "6c 7d"}, "8h 9h 10h Jd Qs")
    assert h.bet[B] == 3 and h.stacks[B] == 0


def test_only_the_player_to_act_can_act() -> None:
    h = hand({A: 1000, B: 1000, C: 1000}, {A: "2c 3d", B: "4c 5d", C: "6c 7d"}, "8h 9h 10h Jd Qs")
    with pytest.raises(NotYourTurnError):
        h.fold(B)


def test_cannot_check_facing_a_bet_or_call_for_free() -> None:
    h = hand({A: 1000, B: 1000, C: 1000}, {A: "2c 3d", B: "4c 5d", C: "6c 7d"}, "8h 9h 10h Jd Qs")
    with pytest.raises(InvalidPokerActionError):
        h.check(A)
    h.call(A)
    h.call(B)
    with pytest.raises(InvalidPokerActionError):
        h.call(C)  # nothing to call: the big blind checks


# ---- betting rules -------------------------------------------------------------------------


def test_minimum_raise_and_reraise() -> None:
    h = hand({A: 1000, B: 1000, C: 1000}, {A: "2c 3d", B: "4c 5d", C: "6c 7d"}, "8h 9h 10h Jd Qs")
    assert h.legal_actions(A).min_raise_to == 20
    with pytest.raises(InvalidPokerActionError, match="minimum raise is to 20"):
        h.raise_to(A, 15)
    h.raise_to(A, 40)  # raise of 30 over the big blind
    assert h.min_raise == 30
    h.call(B)
    assert h.legal_actions(C).min_raise_to == 70


def test_cannot_raise_more_than_the_stack() -> None:
    h = hand({A: 100, B: 1000, C: 1000}, {A: "2c 3d", B: "4c 5d", C: "6c 7d"}, "8h 9h 10h Jd Qs")
    with pytest.raises(InvalidPokerActionError, match="only have enough to raise to 100"):
        h.raise_to(A, 101)


def test_short_all_in_does_not_reopen_the_betting() -> None:
    h = hand({A: 1000, B: 1000, C: 25}, {A: "2c 3d", B: "4c 5d", C: "6c 7d"}, "8h 9h 10h Jd Qs")
    h.raise_to(A, 20)  # full raise to 20
    h.call(B)
    h.all_in(C)  # 25 total: a raise of 5, short of the 10 minimum
    assert h.current == A
    assert h.legal_actions(A).min_raise_to is None  # A may only call or fold
    h.call(A)
    assert h.legal_actions(B).min_raise_to is None
    h.call(B)
    assert h.street is Street.FLOP


def test_full_all_in_reopens_the_betting() -> None:
    h = hand({A: 1000, B: 1000, C: 60}, {A: "2c 3d", B: "4c 5d", C: "6c 7d"}, "8h 9h 10h Jd Qs")
    h.raise_to(A, 20)
    h.call(B)
    h.all_in(C)  # to 60: a raise of 40, bigger than the minimum
    assert h.legal_actions(A).min_raise_to == 100


def test_all_in_for_less_than_a_call_is_just_a_call() -> None:
    h = hand({A: 1000, B: 1000, C: 1000}, {A: "2c 3d", B: "4c 5d", C: "6c 7d"}, "8h 9h 10h Jd Qs")
    h.raise_to(A, 200)
    h.call(B)
    # C only has to call 190 more; all_in with enough chips is a plain call
    assert h.legal_actions(C).call_amount == 190


# ---- hands from start to finish --------------------------------------------------------------


def test_everyone_folds_to_one_player_who_wins_the_pot_without_a_showdown() -> None:
    h = hand({A: 1000, B: 1000, C: 1000}, {A: "2c 3d", B: "4c 5d", C: "6c 7d"}, "8h 9h 10h Jd Qs")
    h.fold(A)
    h.fold(B)
    assert h.is_over and h.current is None
    result = h.result()
    assert not result.showdown and result.shown == {}
    assert result.stacks == {A: 1000, B: 995, C: 1005}


def test_check_down_to_showdown_awards_the_best_hand() -> None:
    h = hand({A: 1000, B: 1000}, {A: "As Ad", B: "Kc Kd"}, "2h 7c 9d 3s 4h")
    h.call(A)
    h.check(B)
    for _ in range(3):  # flop, turn, river: the big blind acts first, then the button
        h.check(B)
        h.check(A)
    assert h.is_over
    result = h.result()
    assert result.showdown and set(result.shown) == {A, B}
    assert result.stacks == {A: 1010, B: 990}
    assert describe(result.ranks[A]) == "a pair"


def test_chopped_pot_splits_and_the_odd_chip_goes_left_of_the_button() -> None:
    # Both play the board (royal flush on the board), pot 25 is odd.
    h = hand(
        {A: 1000, B: 1000, C: 1000},
        {A: "2c 3d", B: "2d 3h", C: "2h 3c"},
        "As Ks Qs Js 10s",
        blinds=(5, 10),
    )
    h.call(A)
    h.fold(B)  # small blind folds, 5 dead
    h.check(C)
    for _ in range(3):
        h.check(C)
        h.check(A)
    result = h.result()
    assert result.pots[0].amount == 25  # 10 + 10 + 5
    assert sum(result.stacks.values()) == 3000
    # A and C tie; the odd chip goes to the first of them left of the button (C).
    assert result.stacks[C] == 1000 - 10 + 13
    assert result.stacks[A] == 1000 - 10 + 12


def test_all_in_runout_deals_the_whole_board_and_pays_side_pots() -> None:
    h = hand(
        {A: 100, B: 300, C: 300, D: 50},
        {A: "Ac Ad", B: "Kc Kd", C: "Qc Qd", D: "2c 7d"},
        "3h 8s 9d Js 4c",
        button=D,
    )
    # order A B C D, button D: small blind A, big blind B, first to act is C
    h.all_in(C)
    h.all_in(D)
    h.all_in(A)
    h.call(B)
    assert h.is_over
    result = h.result()
    assert sum(result.stacks.values()) == 750
    assert result.stacks[A] == 200 + 150  # aces win the main (4x50) and first side pot (3x50)
    assert result.stacks[B] == 400 + 0  # kings win the last side pot between B and C
    assert result.stacks[C] == 0 and result.stacks[D] == 0


def test_uncalled_excess_goes_back_to_the_bettor() -> None:
    h = hand({A: 1000, B: 100, C: 1000}, {A: "2c 3d", B: "4c 5d", C: "6c 7d"}, "8h 9h 10h Jd Qs")
    h.raise_to(A, 500)
    h.fold(B)
    h.fold(C)
    result = h.result()
    assert result.stacks == {A: 1000 + 10 + 5 + 0, B: 95, C: 990}


def test_timeout_checks_when_free_otherwise_folds() -> None:
    h = hand({A: 1000, B: 1000, C: 1000}, {A: "2c 3d", B: "4c 5d", C: "6c 7d"}, "8h 9h 10h Jd Qs")
    assert h.auto_action(A) == "fold"
    h.call(B)
    assert h.auto_action(C) == "check"


def test_acting_after_the_hand_is_over_is_rejected() -> None:
    h = hand({A: 1000, B: 1000}, {A: "2c 3d", B: "4c 5d"}, "8h 9h 10h Jd Qs")
    h.fold(A)
    with pytest.raises(InvalidPokerActionError):
        h.fold(B)


def test_constructor_rejects_bad_tables() -> None:
    deck = build_deck(random.Random(1))
    with pytest.raises(ValueError):
        PokerHand({A: 100}, [A], A, 5, 10, deck)
    with pytest.raises(ValueError):
        PokerHand({A: 100, B: 0}, [A, B], A, 5, 10, deck)
    with pytest.raises(ValueError):
        PokerHand({A: 100, B: 100}, [A, B], C, 5, 10, deck)


def test_blind_that_puts_a_player_all_in_runs_the_hand_out_without_asking_anyone() -> None:
    """Heads-up, the button posts its last 5 chips as the small blind: nobody can bet any more."""
    h = hand({A: 5, B: 1000}, {A: "As Ad", B: "2c 7d"}, "Ks 9h 4d 3c Js")
    assert h.is_over and h.current is None
    result = h.result()
    assert result.showdown
    assert result.stacks == {A: 10, B: 995}  # A wins the 5-chip main pot; B's extra 5 comes back


def test_constructor_rejects_a_player_seated_twice() -> None:
    deck = build_deck(random.Random(1))
    with pytest.raises(ValueError):
        PokerHand({A: 100, B: 100}, [A, B, A], A, 5, 10, deck)


def test_short_all_in_big_blind_does_not_lower_the_price_for_everyone_else() -> None:
    """Players still owe the full big blind when the big blind could only post part of it."""
    h = hand({A: 1000, B: 1000, C: 4}, {A: "2c 3d", B: "4c 5d", C: "6c 7d"}, "8h 9h 10h Jd Qs")
    assert h.current_bet == 10
    assert h.legal_actions(A).call_amount == 10
    assert h.legal_actions(A).min_raise_to == 20


def test_several_short_all_ins_that_add_up_to_a_full_raise_reopen_the_betting() -> None:
    h = hand(
        {A: 1000, B: 1000, C: 170, D: 230},
        {A: "2c 3d", B: "4c 5d", C: "6c 7d", D: "9c 9d"},
        "8h Kh 10h Jd Qs",
        button=D,
    )
    for user in (C, D, A):  # everyone limps, the big blind checks
        h.call(user)
    h.check(B)
    h.raise_to(A, 100)  # flop: a bet of 100, so a full raise is another 100
    h.call(B)
    h.all_in(C)  # to 160: a raise of 60, short
    h.all_in(D)  # to 220: another 60, short on its own but 120 over A's bet in total
    assert h.current == A
    assert h.legal_actions(A).min_raise_to == 320


# ---- conservation property --------------------------------------------------------------------


@pytest.mark.parametrize("seed", range(300))
def test_random_hands_conserve_chips(seed: int) -> None:
    rng = random.Random(seed)
    n = rng.randint(2, 6)
    order = list(range(1, n + 1))
    stacks = {u: rng.choice([5, 20, 100, 250, 1000]) for u in order}
    total = sum(stacks.values())
    h = PokerHand(stacks, order, rng.choice(order), 5, 10, build_deck(rng))
    for _ in range(200):
        if h.is_over:
            break
        user = h.current
        assert user is not None
        legal = h.legal_actions(user)
        move = rng.choice(["fold", "check_call", "check_call", "raise", "all_in"])
        if move == "fold":
            h.fold(user)
        elif move == "raise" and legal.min_raise_to is not None:
            assert legal.max_raise_to is not None
            h.raise_to(user, rng.randint(legal.min_raise_to, legal.max_raise_to))
        elif move == "all_in":
            h.all_in(user)
        elif legal.can_check:
            h.check(user)
        else:
            h.call(user)
    assert h.is_over
    result = h.result()
    assert sum(result.stacks.values()) == total
    assert all(chips >= 0 for chips in result.stacks.values())
    assert sum(award.amount for award in result.pots) == sum(h.contrib.values())
