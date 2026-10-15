"""Pacing simulator: plays a scripted daily player through the real engine and guild rules.

Used by `scripts/rpg_sim.py` (a printed report) and `tests/test_rpg_pacing.py` (which fails when
a balance change breaks the pacing targets). The player always attacks, takes the best mission
they can enter by XP per attempt, and steps away from a mission they keep losing.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable, Optional

from yokai.rpg import guild
from yokai.rpg.combat.builders import build_mission_stages, player_combatant
from yokai.rpg.combat.engine import Fight
from yokai.rpg.combat.models import ACTION_ATTACK, OUTCOME_WON, Action
from yokai.rpg.content import Content
from yokai.rpg.models import Mission, Stats
from yokai.rpg.stats import stats_at_level

START_DAY = date(2026, 1, 1)
LOSSES_BEFORE_GIVING_UP = 2

StatsFor = Callable[[int], Stats]


@dataclass(slots=True)
class DayRecord:
    day: int
    level: int
    rank: str
    wins: int = 0
    losses: int = 0
    renown: int = 0
    attempts_used: int = 0


@dataclass(slots=True)
class SimResult:
    records: list[DayRecord] = field(default_factory=list)
    level_day: dict[int, int] = field(default_factory=dict)
    rank_day: dict[str, int] = field(default_factory=dict)

    def day_reaching_level(self, level: int) -> Optional[int]:
        return self.level_day.get(level)

    def win_rate(self, first_day: int = 1, last_day: Optional[int] = None) -> float:
        rows = [
            r for r in self.records if r.day >= first_day and (not last_day or r.day <= last_day)
        ]
        wins, losses = sum(r.wins for r in rows), sum(r.losses for r in rows)
        return wins / (wins + losses) if wins + losses else 0.0

    def renown_per_day(self, first_day: int, last_day: int) -> float:
        rows = [r for r in self.records if first_day <= r.day <= last_day]
        return sum(r.renown for r in rows) / len(rows) if rows else 0.0


def fight_once(
    content: Content, mission: Mission, stats: Stats, rng: random.Random
) -> tuple[bool, int]:
    """Fight one mission solo with an always-attack player. Returns (won, rounds)."""
    hero = player_combatant(1, "Sim", stats)
    stages = build_mission_stages(content, mission, 1)
    fight = Fight([hero], stages, content.skills, content.balance.combat, rng)
    while not fight.finished:
        fight.submit(hero.id, Action(ACTION_ATTACK))
        fight.resolve_round()
    return fight.outcome == OUTCOME_WON, fight.round_no - 1


def simulate(
    content: Content,
    *,
    base_attempts: int = 8,
    days: int = 150,
    seed: int = 1,
    stats_for: Optional[StatsFor] = None,
) -> SimResult:
    """Simulate a member who plays every day, using all their attempts."""
    rng = random.Random(seed)
    rules = content.balance
    cap = content.levels.cap
    stats_of = stats_for or (lambda level: stats_at_level(content.levels, level))
    level, xp, rank_index, streak = 1, 0, 0, 0
    result = SimResult(rank_day={content.ranks[0].id: 1})
    result.level_day[1] = 1

    for day in range(1, days + 1):
        day_key = (START_DAY + timedelta(days=day - 1)).isoformat()
        allowance = base_attempts + guild.streak_bonus(streak, rules.streak)
        remaining = allowance
        record = DayRecord(day=day, level=level, rank=content.ranks[rank_index].id)
        losses_here: dict[str, int] = {}
        board = guild.board_missions(content, guild.draw_board(content, day_key))

        while remaining > 0:
            rank_id = content.ranks[rank_index].id
            options = [
                m
                for m in board
                if guild.can_enter(content, rank_id, m)
                and m.attempt_cost <= remaining
                and losses_here.get(m.id, 0) < LOSSES_BEFORE_GIVING_UP
            ]
            if not options:
                break
            mission = max(
                options,
                key=lambda m: guild.mission_reward(content, m, rank_id).xp / m.attempt_cost,
            )
            remaining -= mission.attempt_cost
            record.attempts_used += mission.attempt_cost
            won, _ = fight_once(content, mission, stats_of(level), rng)
            if not won:
                record.losses += 1
                losses_here[mission.id] = losses_here.get(mission.id, 0) + 1
                continue
            record.wins += 1
            reward = guild.mission_reward(content, mission, rank_id)
            record.renown += reward.renown
            before = level
            level, xp, _ = guild.apply_xp(level, xp, reward.xp, cap, rules.xp)
            for reached in range(before + 1, level + 1):
                result.level_day.setdefault(reached, day)
            # Phase 2 promotes instantly at the level threshold; phase 4 adds real promotion fights.
            while (
                rank_index + 1 < len(content.ranks)
                and level >= content.ranks[rank_index + 1].min_level
            ):
                rank_index += 1
                result.rank_day.setdefault(content.ranks[rank_index].id, day)

        if record.attempts_used:
            streak += 1
        else:
            streak = max(0, streak - rules.streak.drop_per_missed_day)
        record.level = level
        result.records.append(record)
    return result
