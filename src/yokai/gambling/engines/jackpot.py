"""Jackpot rules: everyone antes into one pot and one stake-weighted draw takes it."""

from __future__ import annotations

import random
from typing import Sequence

from yokai.gambling.engines.duel import prize_after_rake


def _total(entries: Sequence[tuple[int, int]]) -> int:
    if not entries:
        raise ValueError("a jackpot needs at least one entry")
    if any(stake <= 0 for _, stake in entries):
        raise ValueError("every stake must be positive")
    return sum(stake for _, stake in entries)


def draw_winner(entries: Sequence[tuple[int, int]], rng: random.Random) -> int:
    """Pick a winner with probability proportional to stake; entries are (user_id, stake)."""
    total = _total(entries)
    roll = rng.randrange(total)
    running = 0
    for user_id, stake in entries:
        running += stake
        if roll < running:
            return user_id
    raise AssertionError("unreachable: the roll is below the total")


def win_chances(entries: Sequence[tuple[int, int]]) -> dict[int, float]:
    """Each entrant's chance of winning, as a share between 0 and 1."""
    total = _total(entries)
    return {user_id: stake / total for user_id, stake in entries}


def payouts(
    entries: Sequence[tuple[int, int]], winner_id: int, rake_percent: int
) -> tuple[dict[int, int], int, int]:
    """Split the pot: the winner gets it minus the rake. Returns (payouts, prize, rake)."""
    if winner_id not in {user_id for user_id, _ in entries}:
        raise ValueError("the winner must be one of the entrants")
    prize, rake = prize_after_rake(_total(entries), rake_percent)
    return {user_id: (prize if user_id == winner_id else 0) for user_id, _ in entries}, prize, rake
