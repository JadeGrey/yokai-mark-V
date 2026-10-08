"""Duel rules: two players each pick 1 to 10; the pick closest to a hidden number wins."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence

from yokai.errors import InvalidGameChoiceError, NotAtTableError

NUMBER_MIN = 1
NUMBER_MAX = 10


def draw_target(rng: random.Random) -> int:
    """The hidden number, drawn once when the seats fill."""
    return rng.randint(NUMBER_MIN, NUMBER_MAX)


def prize_after_rake(pot: int, rake_percent: int) -> tuple[int, int]:
    """Split a pot into (prize, rake); the rake is a percentage rounded down."""
    rake = pot * rake_percent // 100
    return pot - rake, rake


def decide(picks: Mapping[int, int], target: int) -> Optional[int]:
    """The winner's user id, or None when both picks are equally close."""
    (first, first_pick), (second, second_pick) = picks.items()
    first_distance = abs(first_pick - target)
    second_distance = abs(second_pick - target)
    if first_distance == second_distance:
        return None
    return first if first_distance < second_distance else second


@dataclass(frozen=True)
class DuelOutcome:
    target: int
    picks: dict[int, int]
    winner_id: Optional[int]

    @property
    def is_tie(self) -> bool:
        return self.winner_id is None


class DuelRound:
    """The secret picks of one duel."""

    def __init__(self, user_ids: Sequence[int], target: int) -> None:
        if len(user_ids) != 2 or len(set(user_ids)) != 2:
            raise ValueError("a duel needs exactly two distinct players")
        self.user_ids = list(user_ids)
        self.target = target
        self._picks: dict[int, int] = {}

    def submit(self, user_id: int, number: int) -> None:
        if user_id not in self.user_ids:
            raise NotAtTableError()
        if not NUMBER_MIN <= number <= NUMBER_MAX:
            raise InvalidGameChoiceError(f"Pick a whole number from {NUMBER_MIN} to {NUMBER_MAX}.")
        if user_id in self._picks:
            raise InvalidGameChoiceError("You already locked in a number.")
        self._picks[user_id] = number

    def has_picked(self, user_id: int) -> bool:
        return user_id in self._picks

    @property
    def picked_ids(self) -> list[int]:
        return [uid for uid in self.user_ids if uid in self._picks]

    @property
    def ready(self) -> bool:
        return len(self._picks) == len(self.user_ids)

    def fill_missing(self, rng: random.Random) -> list[int]:
        """Give every player who has not picked a random number; returns who was filled."""
        filled = [uid for uid in self.user_ids if uid not in self._picks]
        for uid in filled:
            self._picks[uid] = rng.randint(NUMBER_MIN, NUMBER_MAX)
        return filled

    def outcome(self) -> DuelOutcome:
        if not self.ready:
            raise ValueError("both players must have picked")
        picks = {uid: self._picks[uid] for uid in self.user_ids}
        return DuelOutcome(self.target, picks, decide(picks, self.target))
