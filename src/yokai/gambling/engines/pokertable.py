"""A poker cash-game table across many hands: stacks, blinds, the button, busts and leavers.

Pure bookkeeping around `PokerHand`: no Discord and no database. The runner plays the hands,
this keeps the table between them and says who is owed what when someone leaves.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

from yokai.errors import NotAtTableError
from yokai.gambling.engines.poker import HandResult, PokerHand, build_deck

IDLE_LIMIT = 2  # consecutive timed-out turns before a player is removed from the table


def blinds_for(buy_in: int) -> tuple[int, int]:
    """Small and big blind: 1% and 2% of the buy-in rounded up, never below 1 and 2."""
    small = max(1, -(-buy_in // 100))
    big = max(2, -(-(buy_in * 2) // 100))
    return small, big


@dataclass(frozen=True)
class CashOut:
    """A seat leaving the table with `chips`: it busted, asked to leave, or sat out."""

    user_id: int
    chips: int
    reason: str  # "bust", "left" or "inactive"


class PokerTable:
    """Equal stacks at the start; hands are dealt one at a time until the table is finished."""

    def __init__(
        self,
        order: Sequence[int],
        buy_in: int,
        max_hands: int,
        rng_factory: Callable[[], random.Random],
    ) -> None:
        if len(set(order)) != len(order) or len(order) < 2:
            raise ValueError("a poker table needs at least two different players")
        if buy_in <= 0 or max_hands < 1:
            raise ValueError("the buy-in and the hand cap must be positive")
        self.order = list(order)
        self.buy_in = buy_in
        self.max_hands = max_hands
        self.small_blind, self.big_blind = blinds_for(buy_in)
        self.stacks: dict[int, int] = {user_id: buy_in for user_id in self.order}
        self.hand_no = 0
        self.button: Optional[int] = None
        self.leaving: set[int] = set()
        self._idle: dict[int, int] = {}
        self._seated: list[int] = list(self.order)
        self._rng_factory = rng_factory

    @property
    def seated(self) -> list[int]:
        """Players still at the table, in seat order."""
        return list(self._seated)

    @property
    def is_finished(self) -> bool:
        return self.hand_no >= self.max_hands or len(self._seated) < 2

    # ---- who is still playing --------------------------------------------------------------

    def request_leave(self, user_id: int) -> None:
        """Mark a player to be cashed out once the current hand is over."""
        if user_id not in self._seated:
            raise NotAtTableError()
        self.leaving.add(user_id)

    def record_action(self, user_id: int) -> None:
        self._idle.pop(user_id, None)

    def record_timeout(self, user_id: int) -> bool:
        """Count a missed turn; True when it was the last one allowed and they are now leaving."""
        self._idle[user_id] = self._idle.get(user_id, 0) + 1
        if self._idle[user_id] >= IDLE_LIMIT:
            self.leaving.add(user_id)
            return True
        return False

    def take_cash_outs(self) -> list[CashOut]:
        """Remove busted and leaving players from the table and say what each is owed."""
        out: list[CashOut] = []
        for user_id in list(self._seated):
            chips = self.stacks[user_id]
            if chips == 0:
                reason = "bust"
            elif user_id in self.leaving:
                reason = "inactive" if self._idle.get(user_id, 0) >= IDLE_LIMIT else "left"
            else:
                continue
            self._seated.remove(user_id)
            del self.stacks[user_id]
            out.append(CashOut(user_id, chips, reason))
        return out

    # ---- hands ----------------------------------------------------------------------------

    def start_hand(self) -> PokerHand:
        if self.is_finished:
            raise ValueError("this table is finished")
        if any(self.stacks[user_id] <= 0 or user_id in self.leaving for user_id in self._seated):
            raise ValueError("cash out busted and leaving players before the next hand")
        self.button = self._next_button()
        self.hand_no += 1
        stacks = {user_id: self.stacks[user_id] for user_id in self._seated}
        return PokerHand(
            stacks,
            self._seated,
            self.button,
            self.small_blind,
            self.big_blind,
            build_deck(self._rng_factory()),
        )

    def finish_hand(self, result: HandResult) -> None:
        """Take the stacks a finished hand ended with; the chip total must not change."""
        before = sum(self.stacks[user_id] for user_id in result.stacks)
        if set(result.stacks) - set(self.stacks) or sum(result.stacks.values()) != before:
            raise ValueError("a hand must keep the chips it started with")
        self.stacks.update(result.stacks)

    def final_payouts(self) -> dict[int, int]:
        """What every player still at the table is owed when it closes."""
        return {user_id: self.stacks[user_id] for user_id in self._seated}

    def _next_button(self) -> int:
        if self.button is None:
            return self._seated[0]
        start = self.order.index(self.button)
        for step in range(1, len(self.order) + 1):
            candidate = self.order[(start + step) % len(self.order)]
            if candidate in self._seated:
                return candidate
        raise ValueError("nobody is seated")  # unreachable: a table is never empty here
