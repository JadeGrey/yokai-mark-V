"""Blackjack with one shared dealer: players act in join order, each hand settles on its own."""

from __future__ import annotations

from typing import Mapping, Optional, Sequence

from yokai.errors import LobbyClosedError, NotYourTurnError
from yokai.gambling.engines.blackjack import (
    Card,
    Outcome,
    compare_hands,
    hand_value,
    is_blackjack,
    payout_for,
)


class BlackjackTable:
    """Deals P1, P2, ..., Dealer, then P1, P2, ..., Dealer from `shoe`; no double, no splits."""

    def __init__(self, shoe: Sequence[Card], user_ids: Sequence[int]) -> None:
        self._shoe = list(shoe)
        self._next = 0
        self.order = list(user_ids)
        self.hands: dict[int, list[Card]] = {user_id: [] for user_id in self.order}
        self.dealer: list[Card] = []
        self.busted: set[int] = set()
        self._done: set[int] = set()
        for _ in range(2):
            for user_id in self.order:
                self.hands[user_id].append(self._draw())
            self.dealer.append(self._draw())
        if is_blackjack(self.dealer):
            self._done = set(self.order)  # the dealer peeks: nobody gets to act
        else:
            self._done = {u for u in self.order if is_blackjack(self.hands[u])}

    def _draw(self) -> Card:
        card = self._shoe[self._next]
        self._next += 1
        return card

    @property
    def current(self) -> Optional[int]:
        """The player whose turn it is, or None once every player is done."""
        for user_id in self.order:
            if user_id not in self._done:
                return user_id
        return None

    @property
    def players_finished(self) -> bool:
        return self.current is None

    def _require_turn(self, user_id: int) -> None:
        current = self.current
        if current is None:
            raise LobbyClosedError("The players are done; the dealer is playing.", user_hint=None)
        if current != user_id:
            raise NotYourTurnError()

    def hit(self, user_id: int) -> None:
        self._require_turn(user_id)
        self.hands[user_id].append(self._draw())
        total = hand_value(self.hands[user_id])[0]
        if total > 21:
            self.busted.add(user_id)
            self._done.add(user_id)
        elif total == 21:
            self._done.add(user_id)

    def stand(self, user_id: int) -> None:
        self._require_turn(user_id)
        self._done.add(user_id)

    def status_of(self, user_id: int) -> str:
        """'bust', 'blackjack', '21', 'stand' for a finished hand, 'playing' otherwise."""
        if user_id in self.busted:
            return "bust"
        if is_blackjack(self.hands[user_id]):
            return "blackjack"
        if user_id not in self._done:
            return "playing"
        return "21" if hand_value(self.hands[user_id])[0] == 21 else "stand"

    def play_dealer(self) -> None:
        """The dealer draws to 17 unless every player busted or has a natural. Safe to repeat."""
        if not self.players_finished:
            raise ValueError("the players are not done yet")
        live = [
            user_id
            for user_id in self.order
            if user_id not in self.busted and not is_blackjack(self.hands[user_id])
        ]
        if not live or is_blackjack(self.dealer):
            return
        while hand_value(self.dealer)[0] < 17:
            self.dealer.append(self._draw())

    def outcomes(self) -> dict[int, Outcome]:
        return {user_id: compare_hands(self.hands[user_id], self.dealer) for user_id in self.order}

    def payouts(self, stakes: Mapping[int, int]) -> dict[int, int]:
        return {
            user_id: payout_for(outcome, stakes[user_id])
            for user_id, outcome in self.outcomes().items()
        }
