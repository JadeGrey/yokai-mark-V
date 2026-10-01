"""Bet validation and the escrow context manager that guarantees a stake is never lost."""

from __future__ import annotations

import contextlib
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, AsyncIterator, Optional

from yokai.errors import BetNotOpenError, BetOutOfRangeError

if TYPE_CHECKING:
    from yokai.storage.db import Database


def validate_bet(amount: int, minimum: int, maximum: int) -> None:
    """Raise BetOutOfRangeError unless `minimum <= amount <= maximum`."""
    if amount < minimum or amount > maximum:
        raise BetOutOfRangeError(minimum, maximum)


@asynccontextmanager
async def escrowed_bet(
    db: Database,
    user_id: int,
    amount: int,
    game: str,
    welcome_bonus: int,
    group_id: Optional[str] = None,
) -> AsyncIterator[int]:
    """Escrow a stake for the body of the block; refund it if the body raises.

    The body is expected to settle the bet itself. A bet it already settled is left alone.
    """
    bet_id = await db.place_bet(user_id, amount, game, welcome_bonus, group_id=group_id)
    try:
        yield bet_id
    except BaseException:
        with contextlib.suppress(BetNotOpenError):
            await db.refund_bet(bet_id)
        raise
