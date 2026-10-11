# Poker P1: engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A pure, fully tested Texas hold'em engine (card evaluator, one-hand betting state machine, side pots) that the later poker phases build the table on.

**Architecture:** One new module, `src/yokai/gambling/engines/poker.py`, in the same style as the other engines: no `discord`, no database, deterministic given an injected deck. `PokerHand` plays exactly one hand; the later table runner (P3) creates one per hand and carries stacks between hands. Cards reuse `Card` from `engines/blackjack.py`.

**Tech Stack:** Python 3.12+, `pytest`, `ruff`. No new dependency.

**Spec:** `docs/superpowers/specs/2026-10-10-poker-design.md` (§2 rules, §3.1 engine, §6 testing). This plan is phase P1 only; P2 (money), P3 (table UI) and P4 (docs) get their own plans after owner review.

## Global Constraints

- `from __future__ import annotations`, fully type-hinted, `src/yokai/` layout, dataclasses for models (`AGENTS.md`).
- Engines import nothing from `discord` and nothing from the database; randomness only through an injected `random.Random` or a pre-built deck.
- All chip amounts are whole integers. No rake.
- Blinds are passed in; the engine never decides them (the 1% / 2% rule lives in P3).
- Cards print in poker notation (`As`, `10d`) via the existing `Card.__str__`; no suit glyphs.
- No new dependency (`AGENTS.md` rule 5).
- Never mark a task done without running `python -m pytest -q`, `python -m ruff check .` and `python -m ruff format --check .` (PowerShell, from repo root, venv active).
- Commit per task. Commit messages end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

## Review Focus

- A blind that puts a player all-in (heads-up short stack) must run the hand out with nobody asked to act, and the uncalled chips go back (test in Task 2).
- A short all-in raise must not reopen betting for players who already acted (test in Task 2).
- A player seated twice, a one-player table, a zero-chip player and a non-seated button are rejected at construction (tests in Task 2).
- Equal hands of different suits tie and split; the odd chip goes to the first winner left of the button (tests in Task 2).
- Random legal play never creates or destroys chips and never leaves a negative stack (property test in Task 3, 300 seeds).
- `PokerHand.holes` is public so the engine stays simple; P3 must never put it in a public embed outside a showdown (a note for the P3 plan; no test here).

## File Structure

- Create `src/yokai/gambling/engines/poker.py`: evaluator, `PokerHand`, `split_pots` and result dataclasses.
- Modify `src/yokai/errors.py`: add `InvalidPokerActionError`.
- Create `tests/test_poker_engine.py`: evaluator, pots, blinds, betting, full hands, conservation property.
- Modify `TASKS.md`: record Phase 11 (poker) and the P1 task list.

Existing helper used by tests: `tests/card_helpers.py::cards("As Kd 10h") -> list[Card]`.

---

### Task 1: Error type, deck and hand evaluator

**Files:**
- Modify: `src/yokai/errors.py` (append at the end of the file)
- Create: `src/yokai/gambling/engines/poker.py`
- Create: `tests/test_poker_engine.py`

**Interfaces:**
- Consumes: `Card`, `SUITS` from `yokai.gambling.engines.blackjack`; `YokaiError` from `yokai.errors`.
- Produces (used by Task 2 and later phases):
  - `InvalidPokerActionError(YokaiError)` with `title = "Can't do that"`.
  - Category constants `HIGH_CARD, PAIR, TWO_PAIR, TRIPS, STRAIGHT, FLUSH, FULL_HOUSE, QUADS, STRAIGHT_FLUSH` (0 to 8) and `CATEGORY_NAMES`.
  - `HandRank = tuple[int, ...]`; `build_deck(rng: random.Random) -> list[Card]`; `evaluate_five(cards: Sequence[Card]) -> HandRank`; `best_hand(cards: Sequence[Card]) -> HandRank`; `describe(rank: HandRank) -> str`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_poker_engine.py` with exactly:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest -q tests/test_poker_engine.py`
Expected: collection ERROR, `ModuleNotFoundError: No module named 'yokai.gambling.engines.poker'`.

- [ ] **Step 3: Add the error type**

Append to the end of `src/yokai/errors.py` (two blank lines before the class):

```python
class InvalidPokerActionError(YokaiError):
    """Raised when a poker move is not allowed right now (bad raise size, nothing to call...)."""

    title = "Can't do that"
```

- [ ] **Step 4: Write the evaluator**

Create `src/yokai/gambling/engines/poker.py` with exactly:

```python
"""Texas hold'em: card evaluator, one-hand betting state machine and side pots. No Discord."""

from __future__ import annotations

import random
from itertools import combinations
from typing import Sequence

from yokai.gambling.engines.blackjack import SUITS, Card

HIGH_CARD, PAIR, TWO_PAIR, TRIPS, STRAIGHT, FLUSH, FULL_HOUSE, QUADS, STRAIGHT_FLUSH = range(9)
CATEGORY_NAMES = (
    "high card",
    "a pair",
    "two pair",
    "three of a kind",
    "a straight",
    "a flush",
    "a full house",
    "four of a kind",
    "a straight flush",
)

HandRank = tuple[int, ...]


def build_deck(rng: random.Random) -> list[Card]:
    """A shuffled 52-card deck."""
    deck = [Card(rank, suit) for suit in SUITS for rank in range(1, 14)]
    rng.shuffle(deck)
    return deck


def _value(card: Card) -> int:
    return 14 if card.rank == 1 else card.rank


def evaluate_five(cards: Sequence[Card]) -> HandRank:
    """Rank exactly five cards: (category, tiebreakers...). Higher tuples win."""
    if len(cards) != 5:
        raise ValueError("evaluate_five needs exactly five cards")
    values = sorted((_value(c) for c in cards), reverse=True)
    flush = len({c.suit for c in cards}) == 1
    distinct = sorted(set(values), reverse=True)
    straight_high = 0
    if len(distinct) == 5:
        if distinct[0] - distinct[4] == 4:
            straight_high = distinct[0]
        elif distinct == [14, 5, 4, 3, 2]:
            straight_high = 5  # the wheel: the ace plays low
    counts: dict[int, int] = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    # Most repeated first, then highest value: gives the right kicker order for every category.
    groups = sorted(counts.items(), key=lambda item: (item[1], item[0]), reverse=True)
    shape = [n for _, n in groups]
    ordered = tuple(v for v, _ in groups)
    if straight_high and flush:
        return (STRAIGHT_FLUSH, straight_high)
    if shape == [4, 1]:
        return (QUADS, *ordered)
    if shape == [3, 2]:
        return (FULL_HOUSE, *ordered)
    if flush:
        return (FLUSH, *values)
    if straight_high:
        return (STRAIGHT, straight_high)
    if shape == [3, 1, 1]:
        return (TRIPS, *ordered)
    if shape == [2, 2, 1]:
        return (TWO_PAIR, *ordered)
    if shape == [2, 1, 1, 1]:
        return (PAIR, *ordered)
    return (HIGH_CARD, *values)


def best_hand(cards: Sequence[Card]) -> HandRank:
    """Best five-card rank among five to seven cards."""
    if not 5 <= len(cards) <= 7:
        raise ValueError("best_hand needs five to seven cards")
    return max(evaluate_five(combo) for combo in combinations(cards, 5))


def describe(rank: HandRank) -> str:
    return CATEGORY_NAMES[rank[0]]
```

(The `build_deck` function is included here because the tests in Task 2 use it.)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest -q tests/test_poker_engine.py`
Expected: all pass.

- [ ] **Step 6: Lint and commit**

Run: `python -m ruff check .` then `python -m ruff format --check .`
Expected: both clean.

```bash
git add src/yokai/errors.py src/yokai/gambling/engines/poker.py tests/test_poker_engine.py
git commit -m "feat(poker): add the hand evaluator and deck" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Side pots and the one-hand betting engine

**Files:**
- Modify: `src/yokai/gambling/engines/poker.py` (replace the import block, append the rest)
- Modify: `tests/test_poker_engine.py` (replace the import block, add helpers and tests)

**Interfaces:**
- Consumes: everything Task 1 produced; `NotYourTurnError`, `InvalidPokerActionError` from `yokai.errors`.
- Produces (used by P3):
  - `Street` enum: `PREFLOP, FLOP, TURN, RIVER, SHOWDOWN`.
  - `LegalActions(can_check: bool, call_amount: int, min_raise_to: int | None, max_raise_to: int | None, all_in_to: int)` (frozen dataclass). `min_raise_to` and `max_raise_to` are `None` when the player cannot raise.
  - `PotAward(amount: int, eligible: tuple[int, ...], winners: tuple[int, ...])`.
  - `HandResult(stacks: dict[int, int], pots: tuple[PotAward, ...], shown: dict[int, tuple[Card, Card]], ranks: dict[int, HandRank], showdown: bool)`.
  - `split_pots(contrib: Mapping[int, int], live: set[int]) -> list[tuple[int, tuple[int, ...]]]`.
  - `PokerHand(stacks: Mapping[int, int], order: Sequence[int], button: int, small_blind: int, big_blind: int, deck: Sequence[Card])` with properties `current`, `is_over`, `pot`; attributes `stacks`, `bet`, `contrib`, `holes`, `board`, `street`, `current_bet`, `min_raise`, `folded`; methods `result()`, `in_hand()`, `legal_actions(user_id)`, `fold`, `check`, `call`, `raise_to(user_id, total)`, `all_in`, `auto_action(user_id) -> "check" | "fold"`.
  - Semantics: `order` is clockwise seating and `button` a user id; cards are dealt from the front of the deck (two hole cards each starting left of the button, then flop, turn, river, no burn cards); heads-up the button is the small blind, acts first preflop and last afterwards; `raise_to(user, total)` takes the player's TOTAL bet for the current street; a short all-in does not reopen betting; `HandResult.stacks` are the stacks after the hand.

- [ ] **Step 1: Write the failing tests**

In `tests/test_poker_engine.py`, replace the import block (everything between `from __future__ import annotations` and the first `# ---- evaluator` section) with the block below, which also adds the shared helpers:

```python
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
```

Then append these sections after the evaluator tests:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest -q tests/test_poker_engine.py`
Expected: collection ERROR, `ImportError: cannot import name 'PokerHand' from 'yokai.gambling.engines.poker'`.

- [ ] **Step 3: Implement pots and the hand engine**

In `src/yokai/gambling/engines/poker.py`, replace the import block at the top (from `from __future__ import annotations` down to the line before `HIGH_CARD, PAIR, ...`) with:

```python
from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum
from itertools import combinations
from typing import Mapping, Optional, Sequence

from yokai.errors import InvalidPokerActionError, NotYourTurnError
from yokai.gambling.engines.blackjack import SUITS, Card
```

Then append the following to the end of the file:

```python
class Street(Enum):
    PREFLOP = "preflop"
    FLOP = "flop"
    TURN = "turn"
    RIVER = "river"
    SHOWDOWN = "showdown"


@dataclass(frozen=True)
class LegalActions:
    can_check: bool
    call_amount: int  # chips to call (0 when checking); capped at the player's stack
    min_raise_to: Optional[int]  # total street bet for a minimum raise; None = cannot raise
    max_raise_to: Optional[int]  # the player's all-in total for the street
    all_in_to: int  # total street bet if they push everything


@dataclass(frozen=True)
class PotAward:
    amount: int
    eligible: tuple[int, ...]
    winners: tuple[int, ...]


@dataclass(frozen=True)
class HandResult:
    stacks: dict[int, int]  # chips after the hand
    pots: tuple[PotAward, ...]
    shown: dict[int, tuple[Card, Card]]  # hole cards revealed at a showdown (empty if none)
    ranks: dict[int, HandRank]  # best hand of each showdown player (empty if none)
    showdown: bool


def split_pots(contrib: Mapping[int, int], live: set[int]) -> list[tuple[int, tuple[int, ...]]]:
    """Side pots as (amount, eligible player ids). `live` are the players who did not fold.

    Levels with the same contenders form one pot. Chips no live player can win (impossible in a
    legal hand) are merged into the previous pot.
    """
    pots: list[tuple[int, tuple[int, ...]]] = []
    previous = 0
    for level in sorted({c for c in contrib.values() if c > 0}):
        amount = sum(min(c, level) - min(c, previous) for c in contrib.values())
        eligible = tuple(u for u in contrib if u in live and contrib[u] >= level)
        if pots and (not eligible or eligible == pots[-1][1]):
            pots[-1] = (pots[-1][0] + amount, pots[-1][1])  # same contenders: one pot
        else:
            pots.append((amount, eligible))
        previous = level
    return pots


class PokerHand:
    """One hand of no-limit hold'em for 2 to 10 players, all with chips.

    `order` is the seating order (clockwise); `button` is the dealer's user id. Cards are dealt
    from the front of `deck`: two hole cards each (starting left of the button), then the board.
    """

    def __init__(
        self,
        stacks: Mapping[int, int],
        order: Sequence[int],
        button: int,
        small_blind: int,
        big_blind: int,
        deck: Sequence[Card],
    ) -> None:
        if not 2 <= len(order) <= 10 or set(order) != set(stacks) or len(set(order)) != len(order):
            raise ValueError("order must list each player with a stack exactly once (2 to 10)")
        if button not in stacks:
            raise ValueError("the button must be a seated player")
        if any(stacks[u] <= 0 for u in order):
            raise ValueError("every player in a hand needs chips")
        if not 0 < small_blind <= big_blind:
            raise ValueError("blinds must satisfy 0 < small <= big")
        self.order = list(order)
        self.button = button
        self.big_blind = big_blind
        self.stacks = dict(stacks)
        self.contrib = {u: 0 for u in self.order}
        self.bet = {u: 0 for u in self.order}  # chips put in on the current street
        self.folded: set[int] = set()
        self.board: list[Card] = []
        self.street = Street.PREFLOP
        self.current_bet = 0
        self.min_raise = big_blind
        self._deck = list(deck)
        self._next = 0
        self._needs: set[int] = set()
        self._no_raise: set[int] = set()  # acted already and only faced a short all-in since
        self._turn: Optional[int] = None
        self._result: Optional[HandResult] = None

        start = self.order.index(button)
        self.holes: dict[int, list[Card]] = {u: [] for u in self.order}
        for _ in range(2):
            for i in range(1, len(self.order) + 1):
                self.holes[self.order[(start + i) % len(self.order)]].append(self._draw())

        if len(self.order) == 2:
            small, big, first = button, self._after(button), button
        else:
            small, big = self._after(button), self._after(self._after(button))
            first = self._after(big)
        self._post(small, small_blind)
        self._post(big, big_blind)
        self.current_bet = max(self.bet.values())
        self._needs = {u for u in self.order if not self._is_all_in(u)}
        self._settle_turn(first)

    # ---- queries ---------------------------------------------------------------------------

    @property
    def current(self) -> Optional[int]:
        """The player to act, or None once the hand is over."""
        return self._turn

    @property
    def is_over(self) -> bool:
        return self._result is not None

    @property
    def pot(self) -> int:
        return sum(self.contrib.values())

    def result(self) -> HandResult:
        if self._result is None:
            raise ValueError("the hand is not over")
        return self._result

    def in_hand(self) -> list[int]:
        """Players who have not folded, in seat order."""
        return [u for u in self.order if u not in self.folded]

    def legal_actions(self, user_id: int) -> LegalActions:
        self._require_turn(user_id)
        stack = self.stacks[user_id]
        to_call = min(self.current_bet - self.bet[user_id], stack)
        all_in_to = self.bet[user_id] + stack
        can_raise = (
            user_id not in self._no_raise
            and all_in_to > self.current_bet
            and self._others_can_respond(user_id)
        )
        min_to = self.current_bet + self.min_raise
        return LegalActions(
            can_check=self.current_bet == self.bet[user_id],
            call_amount=to_call,
            min_raise_to=min(min_to, all_in_to) if can_raise else None,
            max_raise_to=all_in_to if can_raise else None,
            all_in_to=all_in_to,
        )

    # ---- actions ---------------------------------------------------------------------------

    def fold(self, user_id: int) -> None:
        self._require_turn(user_id)
        self.folded.add(user_id)
        self._finish_action(user_id)

    def check(self, user_id: int) -> None:
        self._require_turn(user_id)
        if self.current_bet != self.bet[user_id]:
            raise InvalidPokerActionError("You can't check; there is a bet to call.")
        self._finish_action(user_id)

    def call(self, user_id: int) -> None:
        self._require_turn(user_id)
        if self.current_bet == self.bet[user_id]:
            raise InvalidPokerActionError("There is nothing to call; check instead.")
        self._put_in(user_id, min(self.current_bet - self.bet[user_id], self.stacks[user_id]))
        self._finish_action(user_id)

    def raise_to(self, user_id: int, total: int) -> None:
        """Raise so the player's total bet on this street is `total` (an all-in may be short)."""
        legal = self.legal_actions(user_id)  # also checks the turn
        if legal.min_raise_to is None or legal.max_raise_to is None:
            raise InvalidPokerActionError("You can't raise here.")
        if total > legal.max_raise_to:
            raise InvalidPokerActionError(f"You only have enough to raise to {legal.max_raise_to}.")
        if total < legal.min_raise_to:
            raise InvalidPokerActionError(f"The minimum raise is to {legal.min_raise_to}.")
        self._raise(user_id, total)

    def all_in(self, user_id: int) -> None:
        """Push every chip: a call if that does not top the bet, otherwise a (maybe short) raise."""
        self._require_turn(user_id)
        stack = self.stacks[user_id]
        total = self.bet[user_id] + stack
        if total <= self.current_bet or not self.legal_actions(user_id).max_raise_to:
            self._put_in(user_id, min(self.current_bet - self.bet[user_id], stack))
            self._finish_action(user_id)
        else:
            self._raise(user_id, total)

    def auto_action(self, user_id: int) -> str:
        """Timeout action: check when it is free, otherwise fold. Returns 'check' or 'fold'."""
        self._require_turn(user_id)
        if self.current_bet == self.bet[user_id]:
            self.check(user_id)
            return "check"
        self.fold(user_id)
        return "fold"

    # ---- internals -------------------------------------------------------------------------

    def _draw(self) -> Card:
        card = self._deck[self._next]
        self._next += 1
        return card

    def _after(self, user_id: int) -> int:
        return self.order[(self.order.index(user_id) + 1) % len(self.order)]

    def _is_all_in(self, user_id: int) -> bool:
        return self.stacks[user_id] == 0

    def _require_turn(self, user_id: int) -> None:
        if self._turn is None:
            raise InvalidPokerActionError("This hand is over.")
        if user_id != self._turn:
            raise NotYourTurnError()

    def _put_in(self, user_id: int, amount: int) -> None:
        self.stacks[user_id] -= amount
        self.bet[user_id] += amount
        self.contrib[user_id] += amount

    def _post(self, user_id: int, blind: int) -> None:
        self._put_in(user_id, min(blind, self.stacks[user_id]))

    def _others_can_respond(self, user_id: int) -> bool:
        return any(
            u != user_id and u not in self.folded and not self._is_all_in(u) for u in self.order
        )

    def _raise(self, user_id: int, total: int) -> None:
        size = total - self.current_bet
        self._put_in(user_id, total - self.bet[user_id])
        if size >= self.min_raise:
            self.min_raise = size
            self._no_raise.clear()
        else:
            # A short all-in: players who already acted may call or fold but not re-raise.
            self._no_raise |= {u for u in self.order if u not in self._needs and u != user_id}
        self.current_bet = total
        self._needs |= {
            u
            for u in self.order
            if u != user_id and u not in self.folded and not self._is_all_in(u)
        }
        self._finish_action(user_id)

    def _finish_action(self, user_id: int) -> None:
        self._needs.discard(user_id)
        self._settle_turn(self._after(user_id))

    def _settle_turn(self, start: int) -> None:
        """Hand the turn to the next player who must act, or advance the street / end the hand."""
        while True:
            if len(self.in_hand()) == 1:
                self._turn = None
                self._finish_hand()
                return
            self._needs -= self.folded | {u for u in self.order if self._is_all_in(u)}
            if len(self._needs) == 1:
                (only,) = self._needs
                if self.bet[only] >= self.current_bet and not self._others_can_respond(only):
                    self._needs.clear()  # everyone else is all-in: nothing left to decide
            if self._needs:
                cursor = start
                for _ in range(len(self.order)):
                    if cursor in self._needs:
                        self._turn = cursor
                        return
                    cursor = self._after(cursor)
            if self.street is Street.RIVER:
                self._turn = None
                self._finish_hand()
                return
            self._next_street()
            start = self._after(self.button)

    def _next_street(self) -> None:
        self.street = {
            Street.PREFLOP: Street.FLOP,
            Street.FLOP: Street.TURN,
            Street.TURN: Street.RIVER,
        }[self.street]
        for _ in range(3 if self.street is Street.FLOP else 1):
            self.board.append(self._draw())
        self.bet = {u: 0 for u in self.order}
        self.current_bet = 0
        self.min_raise = self.big_blind
        self._no_raise.clear()
        live = [u for u in self.in_hand() if not self._is_all_in(u)]
        self._needs = set(live) if len(live) > 1 else set()

    def _finish_hand(self) -> None:
        contenders = self.in_hand()
        showdown = len(contenders) > 1
        ranks: dict[int, HandRank] = {}
        if showdown:
            while len(self.board) < 5:
                self.board.append(self._draw())
            self.street = Street.SHOWDOWN
            ranks = {u: best_hand([*self.holes[u], *self.board]) for u in contenders}
        stacks = dict(self.stacks)
        awards: list[PotAward] = []
        seats = self._seats_from(self._after(self.button))
        for amount, eligible in split_pots(self.contrib, set(contenders)):
            if len(eligible) == 1 or not showdown:
                winners = eligible
            else:
                top = max(ranks[u] for u in eligible)
                winners = tuple(u for u in eligible if ranks[u] == top)
            share, odd = divmod(amount, len(winners))
            ordered = sorted(winners, key=seats.index)
            for i, winner in enumerate(ordered):
                stacks[winner] += share + (1 if i < odd else 0)
            awards.append(PotAward(amount, eligible, tuple(ordered)))
        shown = {u: (self.holes[u][0], self.holes[u][1]) for u in contenders} if showdown else {}
        self._result = HandResult(stacks, tuple(awards), shown, ranks, showdown)

    def _seats_from(self, start: int) -> list[int]:
        i = self.order.index(start)
        return self.order[i:] + self.order[:i]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest -q tests/test_poker_engine.py`
Expected: all pass.

- [ ] **Step 5: Lint and commit**

Run: `python -m ruff check .` then `python -m ruff format --check .`
Expected: both clean.

```bash
git add src/yokai/gambling/engines/poker.py tests/test_poker_engine.py
git commit -m "feat(poker): add side pots and the one-hand betting engine" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Conservation property test, TASKS.md and phase verification

**Files:**
- Modify: `tests/test_poker_engine.py` (append)
- Modify: `TASKS.md` (append a Phase 11 section at the end)

**Interfaces:**
- Consumes: `PokerHand`, `build_deck`, `HandResult` from Task 2.
- Produces: nothing new in code; documents the phase.

- [ ] **Step 1: Add the property test**

Append to `tests/test_poker_engine.py`:

```python
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
```

- [ ] **Step 2: Run it**

Run: `python -m pytest -q tests/test_poker_engine.py`
Expected: all pass, including 300 parametrized `test_random_hands_conserve_chips` cases. If one fails, the test id names the seed; reproduce with that seed before changing anything.

- [ ] **Step 3: Record the phase in TASKS.md**

Append to the end of `TASKS.md`:

```markdown

---

## Phase 11 — Poker table

**Goal:** Texas hold'em as a cash-game table in a dedicated channel. Spec:
`docs/superpowers/specs/2026-10-10-poker-design.md`. Phases P1 to P4 each get their own plan and a
stop for owner review.

### P1 — Engine

Plan: `docs/superpowers/plans/2026-10-10-poker-p1-engine.md`.

- [x] P1.1 Error type, deck and hand evaluator.
- [x] P1.2 Side pots and the one-hand betting engine.
- [x] P1.3 Conservation property test.

**Acceptance criteria (P1)**
- The evaluator ranks all nine categories correctly, including the wheel and steel wheel, kickers
  and best-5-of-7.
- A hand plays blinds, action order (heads-up included), min-raise, short all-ins that do not
  reopen betting, check-or-fold timeouts, fold-wins and showdowns.
- Side pots are built from contribution levels; ties split with the odd chip to the first winner
  left of the button; uncalled chips are returned.
- 300 random legal hands never create or destroy chips.
- `pytest`, `ruff check` and `ruff format --check` are clean. Nothing here touches Discord, the
  database or the network.

**STOP — owner review.** P2 (money: `cash_out`) is planned after this review.
```

- [ ] **Step 4: Full verification**

Run, in order: `python -m pytest -q` (expect the whole suite to pass), `python -m ruff check .`, `python -m ruff format --check .`.
Expected: all clean. Report the actual counts.

- [ ] **Step 5: Commit and tag**

```bash
git add tests/test_poker_engine.py TASKS.md
git commit -m "test(poker): add the chip-conservation property test and record phase 11" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
git tag checkpoint/poker-p1
```

Then stop and wait for the owner's review (working agreement 2). Summarise what changed and list anything unverified.

---

## Self-review

- **Spec coverage (§3.1, §6):** evaluator categories, kickers, wheel and best-of-7 (Task 1); blinds including heads-up, action order, min-raise, short all-in, side pots, ties and odd chip, uncalled excess, timeouts (`auto_action`), stacked-deck hands, conservation property (Tasks 2 and 3). The money layer, views and config are P2 to P4 by the spec's phasing.
- **Placeholders:** none. All code was run before this plan was written (341 tests passing, ruff clean) and is reproduced verbatim.
- **Type consistency:** `InvalidPokerActionError`, `PokerHand`, `LegalActions`, `HandResult`, `PotAward` and `split_pots` are spelled identically in the interface blocks and the code.
