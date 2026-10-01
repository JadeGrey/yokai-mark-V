"""Helpers for building stacked shoes in blackjack tests."""

from __future__ import annotations

from yokai.gambling.engines.blackjack import Card

_FACE_RANKS = {"A": 1, "J": 11, "Q": 12, "K": 13}


def cards(text: str) -> list[Card]:
    """Parse poker notation such as ``"As Kd 10h"`` into cards, in order."""
    parsed: list[Card] = []
    for token in text.split():
        rank_text, suit = token[:-1], token[-1]
        rank = _FACE_RANKS.get(rank_text) or int(rank_text)
        parsed.append(Card(rank=rank, suit=suit))
    return parsed
