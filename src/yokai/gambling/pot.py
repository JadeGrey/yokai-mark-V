"""What the house keeps from a settled game; the lottery pot is fed from it."""

from __future__ import annotations

from typing import Sequence

# Games where players win each other's stakes. Only the rake (what the payouts leave of the
# pot) is the house's; counting the losing stakes too would count them twice.
POOLED_GAMES: frozenset[str] = frozenset({"duel", "jackpot", "poker"})


def bet_house_take(game: str, stake: int, payout: int) -> int:
    """House take from one bet settled on its own (a pooled game's bets are settled together)."""
    if game in POOLED_GAMES:
        return 0
    return max(0, stake - payout)


def lobby_house_take(game: str, stakes: Sequence[int], payouts: Sequence[int]) -> int:
    """House take from a whole lobby settled in one transaction."""
    if len(stakes) != len(payouts):
        raise ValueError("stakes and payouts must have the same length")
    if game in POOLED_GAMES:
        return max(0, sum(stakes) - sum(payouts))
    return sum(max(0, stake - payout) for stake, payout in zip(stakes, payouts))
