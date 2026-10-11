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
