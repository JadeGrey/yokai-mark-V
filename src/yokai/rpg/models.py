"""Data models for the Guild RPG."""

from __future__ import annotations

from dataclasses import dataclass

STAT_NAMES: tuple[str, ...] = ("hp", "attack", "magic", "defense", "resist", "speed", "crit")

STAT_LABELS: dict[str, str] = {
    "hp": "HP",
    "attack": "Attack",
    "magic": "Magic",
    "defense": "Defense",
    "resist": "Resist",
    "speed": "Speed",
    "crit": "Crit",
}


@dataclass(frozen=True, slots=True)
class Stats:
    """The seven combat stats. `crit` is a percentage (5 means 5%)."""

    hp: int = 0
    attack: int = 0
    magic: int = 0
    defense: int = 0
    resist: int = 0
    speed: int = 0
    crit: int = 0

    def __add__(self, other: Stats) -> Stats:
        return Stats(*(getattr(self, name) + getattr(other, name) for name in STAT_NAMES))

    def scaled(self, factor: int) -> Stats:
        """Every stat multiplied by a whole number (used for per-level growth)."""
        return Stats(*(getattr(self, name) * factor for name in STAT_NAMES))


@dataclass(frozen=True, slots=True)
class Tag:
    """A gear tag, such as Oni or Kitsune."""

    id: str
    name: str
    role: str
    leans: tuple[str, ...]
    description: str


@dataclass(frozen=True, slots=True)
class Rank:
    """A guild rank and the level at which promotion into it opens."""

    id: str
    min_level: int


@dataclass(frozen=True, slots=True)
class LevelTable:
    """The level cap and the base stat curve: base at level 1, plus growth per level above 1."""

    cap: int
    base: Stats
    growth: Stats


@dataclass(frozen=True, slots=True)
class Character:
    """A member's adventurer, as stored in the database."""

    user_id: int
    level: int
    xp: int
    rank: str
    battles_won: int
    battles_lost: int
    created_ts: int
