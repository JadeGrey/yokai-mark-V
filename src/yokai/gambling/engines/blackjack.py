"""Blackjack: 6-deck shoe, 3:2 naturals, dealer stands on all 17s, no splits or insurance."""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Sequence

DECKS = 6
SUITS = "cdhs"
_RANK_LABELS = {1: "A", 11: "J", 12: "Q", 13: "K"}


@dataclass(frozen=True, slots=True)
class Card:
    rank: int  # 1 = Ace ... 13 = King
    suit: str  # one of "cdhs"

    def __str__(self) -> str:
        return f"{_RANK_LABELS.get(self.rank, str(self.rank))}{self.suit}"


def hand_value(cards: Sequence[Card]) -> tuple[int, bool]:
    """Best total for a hand and whether an ace is currently counted as 11 (soft)."""
    total = sum(min(card.rank, 10) for card in cards)
    if any(card.rank == 1 for card in cards) and total + 10 <= 21:
        return total + 10, True
    return total, False


def is_blackjack(cards: Sequence[Card]) -> bool:
    return len(cards) == 2 and hand_value(cards)[0] == 21


def build_shoe(rng: random.Random, decks: int = DECKS) -> list[Card]:
    shoe = [Card(rank, suit) for _ in range(decks) for suit in SUITS for rank in range(1, 14)]
    rng.shuffle(shoe)
    return shoe


class Outcome(str, Enum):
    BLACKJACK = "blackjack"
    WIN = "win"
    PUSH = "push"
    LOSE = "lose"


def payout_for(outcome: Outcome, stake: int) -> int:
    """Total returned for `stake` (stake included), floored to a whole Renown."""
    if outcome is Outcome.BLACKJACK:
        return stake + stake * 3 // 2
    if outcome is Outcome.WIN:
        return stake * 2
    if outcome is Outcome.PUSH:
        return stake
    return 0


def compare_hands(player: Sequence[Card], dealer: Sequence[Card]) -> Outcome:
    """The result of a finished hand against the dealer's finished hand."""
    player_total = hand_value(player)[0]
    dealer_total = hand_value(dealer)[0]
    if player_total > 21:
        return Outcome.LOSE
    player_natural = is_blackjack(player)
    dealer_natural = is_blackjack(dealer)
    if player_natural and dealer_natural:
        return Outcome.PUSH
    if player_natural:
        return Outcome.BLACKJACK
    if dealer_natural:
        return Outcome.LOSE
    if dealer_total > 21 or player_total > dealer_total:
        return Outcome.WIN
    if player_total == dealer_total:
        return Outcome.PUSH
    return Outcome.LOSE


class BlackjackGame:
    """One hand against the dealer. Draw order from `shoe`: P, D, P, D, then play."""

    def __init__(self, shoe: Sequence[Card]) -> None:
        self._shoe = list(shoe)
        self._next = 0
        self.player: list[Card] = []
        self.dealer: list[Card] = []
        self.finished = False
        self.outcome: Optional[Outcome] = None
        self.doubled = False
        self.player_busted = False

        self.player.append(self._draw())
        self.dealer.append(self._draw())
        self.player.append(self._draw())
        self.dealer.append(self._draw())
        if is_blackjack(self.player) or is_blackjack(self.dealer):
            self._compare()

    def _draw(self) -> Card:
        card = self._shoe[self._next]
        self._next += 1
        return card

    @property
    def can_double(self) -> bool:
        return not self.finished and len(self.player) == 2

    def _require_active(self) -> None:
        if self.finished:
            raise ValueError("The hand is already finished.")

    def _bust(self) -> None:
        self.player_busted = True
        self.outcome = Outcome.LOSE
        self.finished = True

    def hit(self) -> None:
        self._require_active()
        self.player.append(self._draw())
        total = hand_value(self.player)[0]
        if total > 21:
            self._bust()
        elif total == 21:
            self.stand()

    def stand(self) -> None:
        self._require_active()
        while hand_value(self.dealer)[0] < 17:
            self.dealer.append(self._draw())
        self._compare()

    def double(self) -> None:
        if not self.can_double:
            raise ValueError("Double is only allowed on the first two cards.")
        self.doubled = True
        self.player.append(self._draw())
        if hand_value(self.player)[0] > 21:
            self._bust()
        else:
            self.stand()

    def _compare(self) -> None:
        self.outcome = compare_hands(self.player, self.dealer)
        self.finished = True
