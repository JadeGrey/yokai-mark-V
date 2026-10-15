"""A two-sided trade session: what each side offers and who has confirmed.

Pure state with no Discord or database. The session view edits it, and the atomic commit lives in
`GearStore.commit_trade`. Any change to an offer clears both confirmations, so nobody confirms
one deal and receives another.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from yokai.errors import (
    TradeClosedError,
    TradeLimitError,
    TradeNothingError,
    TradeRenownError,
)

MAX_ITEMS_PER_SIDE = 6
SESSION_SECONDS = 300
MAX_RENOWN = 1_000_000_000


@dataclass(slots=True)
class Offer:
    """One side's items (by item id) and Renown."""

    items: list[int] = field(default_factory=list)
    renown: int = 0

    @property
    def is_empty(self) -> bool:
        return not self.items and self.renown == 0


class TradeSession:
    """Two members, their offers and their confirmations."""

    def __init__(self, initiator_id: int, partner_id: int, *, now: float | None = None) -> None:
        if initiator_id == partner_id:
            raise ValueError("A trade needs two different members.")
        self.initiator_id = initiator_id
        self.partner_id = partner_id
        self.offers: dict[int, Offer] = {initiator_id: Offer(), partner_id: Offer()}
        self.confirmed: set[int] = set()
        self.closed = False
        self.expires_ts = int((time.time() if now is None else now) + SESSION_SECONDS)

    # ------------------------------------------------------------------ queries

    @property
    def members(self) -> tuple[int, int]:
        return self.initiator_id, self.partner_id

    def has_member(self, user_id: int) -> bool:
        return user_id in self.offers

    def other(self, user_id: int) -> int:
        return self.partner_id if user_id == self.initiator_id else self.initiator_id

    @property
    def is_empty(self) -> bool:
        return all(offer.is_empty for offer in self.offers.values())

    @property
    def is_gift(self) -> bool:
        """Exactly one side offers anything."""
        empties = [offer.is_empty for offer in self.offers.values()]
        return empties.count(True) == 1

    @property
    def both_confirmed(self) -> bool:
        return len(self.confirmed) == 2

    def offered_item_ids(self) -> set[int]:
        return {item for offer in self.offers.values() for item in offer.items}

    # ------------------------------------------------------------------ edits

    def _open_offer(self, user_id: int) -> Offer:
        if self.closed:
            raise TradeClosedError()
        if user_id not in self.offers:
            raise ValueError("That member is not part of this trade.")
        return self.offers[user_id]

    def add_item(self, user_id: int, item_id: int) -> bool:
        """Offer an item. Returns False (changing nothing) if it is already in this trade."""
        offer = self._open_offer(user_id)
        if item_id in self.offered_item_ids():
            return False
        if len(offer.items) >= MAX_ITEMS_PER_SIDE:
            raise TradeLimitError(MAX_ITEMS_PER_SIDE)
        offer.items.append(item_id)
        self.confirmed.clear()
        return True

    def remove_item(self, user_id: int, item_id: int) -> bool:
        offer = self._open_offer(user_id)
        if item_id not in offer.items:
            return False
        offer.items.remove(item_id)
        self.confirmed.clear()
        return True

    def drop_items(self, item_ids: set[int] | tuple[int, ...]) -> None:
        """Remove items that can no longer be traded from whichever side offered them."""
        wanted = set(item_ids)
        for offer in self.offers.values():
            offer.items = [item for item in offer.items if item not in wanted]
        self.confirmed.clear()

    def set_renown(self, user_id: int, amount: int) -> bool:
        """Offer an amount of Renown (0 withdraws it). Returns False if nothing changed."""
        offer = self._open_offer(user_id)
        if isinstance(amount, bool) or not isinstance(amount, int):
            raise TradeRenownError()
        if amount < 0 or amount > MAX_RENOWN:
            raise TradeRenownError()
        if offer.renown == amount:
            return False
        offer.renown = amount
        self.confirmed.clear()
        return True

    def confirm(self, user_id: int) -> bool:
        """Confirm the current deal. Returns True once both sides have."""
        self._open_offer(user_id)
        if self.is_empty:
            raise TradeNothingError()
        self.confirmed.add(user_id)
        return self.both_confirmed

    def clear_confirmations(self) -> None:
        self.confirmed.clear()

    def close(self) -> None:
        self.closed = True
        self.confirmed.clear()
