"""Mutable fight state: combatants, statuses, chosen actions and the events a round produces."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from yokai.rpg.models import Stats

SIDE_PLAYERS = "players"
SIDE_FOES = "foes"

ACTION_ATTACK = "attack"
ACTION_MAGIC = "magic"
ACTION_DEFEND = "defend"
ACTION_SKILL = "skill"
ACTION_KINDS = (ACTION_ATTACK, ACTION_MAGIC, ACTION_DEFEND, ACTION_SKILL)

OUTCOME_WON = "won"
OUTCOME_LOST = "lost"


@dataclass(slots=True)
class Status:
    """A timed effect on a combatant: a stat buff or debuff, or damage over time."""

    key: str
    name: str
    kind: str  # "buff" | "debuff" | "dot"
    remaining: int
    stat: Optional[str] = None
    percent: int = 0  # signed: negative for a debuff
    damage: int = 0  # per round, for a dot


@dataclass(slots=True)
class Combatant:
    """One side's fighter. `stats.hp` is the maximum; `hp` is the current value."""

    id: str
    name: str
    side: str
    stats: Stats
    hp: int
    skills: tuple[str, ...] = ()
    user_id: Optional[int] = None
    boss: bool = False
    ai_actions: tuple[tuple[str, int], ...] = ()
    ai_target: str = "random"
    cooldowns: dict[str, int] = field(default_factory=dict)
    statuses: list[Status] = field(default_factory=list)
    defending: bool = False

    @property
    def max_hp(self) -> int:
        return self.stats.hp

    @property
    def alive(self) -> bool:
        return self.hp > 0

    def effective(self, stat: str) -> int:
        """A stat after buffs and debuffs. HP is never modified, and nothing drops below 0."""
        base: int = getattr(self.stats, stat)
        if stat == "hp":
            return base
        percent = sum(s.percent for s in self.statuses if s.stat == stat)
        return max(0, round(base * (1 + percent / 100)))


@dataclass(frozen=True, slots=True)
class Action:
    """What a combatant chose to do this round."""

    kind: str
    skill_id: Optional[str] = None
    target_id: Optional[str] = None


@dataclass(frozen=True, slots=True)
class Event:
    """One thing that happened, for the fight log. `actor` and `target` are display names."""

    kind: str
    actor: str = ""
    target: str = ""
    amount: int = 0
    crit: bool = False
    label: str = ""
    side: str = ""
