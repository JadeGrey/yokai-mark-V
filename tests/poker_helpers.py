"""Shared helpers for the poker tests: a deck stacked to deal exactly the cards a test wants."""

from __future__ import annotations

import random

from tests.card_helpers import cards
from yokai.gambling.engines.blackjack import Card
from yokai.gambling.engines.poker import build_deck


def stacked_deck(order: list[int], button: int, holes: dict[int, str], board: str) -> list[Card]:
    """A deck that deals `holes` and then `board` (flop, turn, river) to a hand with `order`."""
    start = order.index(button)
    seats = [order[(start + i) % len(order)] for i in range(1, len(order) + 1)]
    first = [cards(holes[u])[0] for u in seats]
    second = [cards(holes[u])[1] for u in seats]
    chosen = [*first, *second, *cards(board)]
    rest = [c for c in build_deck(random.Random(0)) if str(c) not in {str(x) for x in chosen}]
    return [*chosen, *rest]
