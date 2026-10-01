"""European single-zero roulette: one bet per spin, 2.7% house edge on every bet type."""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum
from fractions import Fraction
from typing import Optional

from yokai.errors import InvalidGameChoiceError

POCKETS = 37
RED_POCKETS: frozenset[int] = frozenset(
    {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}
)


class BetKind(str, Enum):
    RED = "red"
    BLACK = "black"
    ODD = "odd"
    EVEN = "even"
    LOW = "low"
    HIGH = "high"
    DOZEN = "dozen"
    COLUMN = "column"
    STRAIGHT = "straight"


# Bet kinds that need a pick, with the inclusive range it must fall in.
_PICK_BOUNDS: dict[BetKind, tuple[int, int]] = {
    BetKind.DOZEN: (1, 3),
    BetKind.COLUMN: (1, 3),
    BetKind.STRAIGHT: (0, 36),
}


@dataclass(frozen=True, slots=True)
class RouletteBet:
    kind: BetKind
    pick: Optional[int] = None

    @property
    def label(self) -> str:
        return self.kind.value if self.pick is None else f"{self.kind.value} {self.pick}"


def parse_bet(choice: str, pick: Optional[int]) -> RouletteBet:
    """Validate a bet type and pick from user input; raises InvalidGameChoiceError."""
    try:
        kind = BetKind(choice)
    except ValueError:
        raise InvalidGameChoiceError("That isn't a roulette bet type.") from None
    bounds = _PICK_BOUNDS.get(kind)
    if bounds is None:
        if pick is not None:
            raise InvalidGameChoiceError(f"A {kind.value} bet doesn't take a pick.")
        return RouletteBet(kind)
    low, high = bounds
    if pick is None or not low <= pick <= high:
        raise InvalidGameChoiceError(f"A {kind.value} bet needs a pick from {low} to {high}.")
    return RouletteBet(kind, pick)


def colour(pocket: int) -> str:
    if pocket == 0:
        return "green"
    return "red" if pocket in RED_POCKETS else "black"


def spin(rng: random.Random) -> int:
    return rng.randrange(POCKETS)


def return_multiplier(bet: RouletteBet, pocket: int) -> int:
    """Total returned as a multiple of the stake (0 = lost, 2 = even money, 36 = straight)."""
    kind = bet.kind
    if kind is BetKind.STRAIGHT:
        return 36 if pocket == bet.pick else 0
    if pocket == 0:
        return 0
    if kind is BetKind.RED:
        return 2 if pocket in RED_POCKETS else 0
    if kind is BetKind.BLACK:
        return 2 if pocket not in RED_POCKETS else 0
    if kind is BetKind.ODD:
        return 2 if pocket % 2 == 1 else 0
    if kind is BetKind.EVEN:
        return 2 if pocket % 2 == 0 else 0
    if kind is BetKind.LOW:
        return 2 if pocket <= 18 else 0
    if kind is BetKind.HIGH:
        return 2 if pocket >= 19 else 0
    if kind is BetKind.DOZEN:
        return 3 if (pocket - 1) // 12 + 1 == bet.pick else 0
    # Column 1 holds 1,4,7...; column 3 holds 3,6,9... (pocket % 3 == 0).
    assert bet.pick is not None
    return 3 if pocket % 3 == bet.pick % 3 else 0


def expected_return(bet: RouletteBet) -> Fraction:
    """Exact expected return per unit staked over all 37 pockets."""
    return Fraction(sum(return_multiplier(bet, p) for p in range(POCKETS)), POCKETS)
