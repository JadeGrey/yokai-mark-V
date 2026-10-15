"""Pacing simulator: plays a scripted daily player through the real engine and guild rules.

Used by `scripts/rpg_sim.py` (a printed report) and `tests/test_rpg_pacing.py` (which fails when
a balance change breaks the pacing targets). The player always attacks, takes the best mission
they can enter by XP per attempt, and steps away from a mission they keep losing.

Promotion is played for real: when the player reaches a rank's level threshold they fight its
trial (spending attempts and wearing gear). A loss blocks further tries until the next day, which
is a little harsher than the real six-hour cooldown.

By default the player is geared. They start with the 200 Renown welcome bonus, buy the best full
set of Oni gear they can afford (uncommon, else common) whenever a new gear tier opens, upgrade a
common set to uncommon when they can afford it, and repair everything at the end of every day, so
purchases and repairs are Renown sinks the report can net against income. They never count drops,
which makes the model slightly pessimistic about gear.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable, Optional

from yokai.rpg import guild
from yokai.rpg.combat.builders import build_mission_stages, player_combatant
from yokai.rpg.combat.engine import Fight
from yokai.rpg.combat.models import ACTION_ATTACK, OUTCOME_WON, Action
from yokai.rpg.content import Content
from yokai.rpg.gear import typical_items, typical_loadout
from yokai.rpg.models import Mission, Stats
from yokai.rpg.stats import stats_at_level

START_DAY = date(2026, 1, 1)
LOSSES_BEFORE_GIVING_UP = 2
STARTING_RENOWN = 200  # the welcome bonus

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
    spent: int = 0
    promotions_won: int = 0
    promotions_lost: int = 0


@dataclass(slots=True)
class SimResult:
    records: list[DayRecord] = field(default_factory=list)
    level_day: dict[int, int] = field(default_factory=dict)
    rank_day: dict[str, int] = field(default_factory=dict)
    # rank id -> (gear tier requirement, rarity) the player wore on the day they reached it
    gear_at_rank: dict[str, tuple[Optional[int], Optional[str]]] = field(default_factory=dict)
    # rank id -> how many times its promotion trial was fought
    promotion_attempts: dict[str, int] = field(default_factory=dict)

    def promotion_win_rate(self, rank_id: str) -> Optional[float]:
        """Share of this rank's trials that were won, or None if it was never fought."""
        tries = self.promotion_attempts.get(rank_id, 0)
        if not tries:
            return None
        won = 1 if rank_id in self.rank_day else 0
        return won / tries

    def day_reaching_level(self, level: int) -> Optional[int]:
        return self.level_day.get(level)

    def win_rate(self, first_day: int = 1, last_day: Optional[int] = None) -> float:
        rows = [
            r for r in self.records if r.day >= first_day and (not last_day or r.day <= last_day)
        ]
        wins, losses = sum(r.wins for r in rows), sum(r.losses for r in rows)
        return wins / (wins + losses) if wins + losses else 0.0

    def win_rate_after_promotion(self, rank_id: str, days: int = 3) -> float:
        """Win rate in the first few days a rank is open: the hardest stretch of each band."""
        start = self.rank_day.get(rank_id)
        if start is None:
            return 0.0
        return self.win_rate(start, start + days - 1)

    def renown_per_day(self, first_day: int, last_day: int) -> float:
        rows = [r for r in self.records if first_day <= r.day <= last_day]
        return sum(r.renown for r in rows) / len(rows) if rows else 0.0

    def net_per_day(self, first_day: int, last_day: int) -> float:
        """Renown earned minus Renown spent on gear and repairs, per day."""
        rows = [r for r in self.records if first_day <= r.day <= last_day]
        return sum(r.renown - r.spent for r in rows) / len(rows) if rows else 0.0


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
    geared: bool = True,
) -> SimResult:
    """Simulate a member who plays every day, using all their attempts."""
    rng = random.Random(seed)
    rules = content.balance
    cap = content.levels.cap
    plain = lambda level: stats_at_level(content.levels, level)  # noqa: E731
    level, xp, rank_index, streak = 1, 0, 0, 0
    balance = STARTING_RENOWN
    owned_tier: Optional[int] = None  # level requirement of the gear tier the player wears
    owned_rarity = "common"
    loadouts: dict[tuple[int, Optional[int], str], Stats] = {}

    def stats_of(at_level: int) -> Stats:
        if stats_for is not None:
            return stats_for(at_level)
        if not geared or owned_tier is None:
            return plain(at_level)
        key = (at_level, owned_tier, owned_rarity)
        if key not in loadouts:
            loadouts[key] = typical_loadout(
                content, at_level, rarity=owned_rarity, gear_level=owned_tier
            ).stats
        return loadouts[key]

    def wear_cost(points: int) -> int:
        """Renown to repair one fight's worth of wear on every worn item."""
        if owned_tier is None:
            return 0
        rules = rules_gear
        total = 0.0
        for item in typical_items(content, owned_tier, rarity=owned_rarity):
            total += item.price * rules.repair_percent / 100 * points / item.max_durability
        return math.ceil(total)

    rules_gear = content.balance.gear
    result = SimResult(rank_day={content.ranks[0].id: 1})
    result.level_day[1] = 1
    promo_blocked = False
    repairs_due = 0
    remaining = 0
    record = DayRecord(day=0, level=level, rank=content.ranks[0].id)
    day = 0

    def attempt_promotion() -> None:
        """Fight the next rank's trial if the member is eligible and has the attempts."""
        nonlocal rank_index, remaining, balance, promo_blocked, repairs_due
        if promo_blocked or rank_index + 1 >= len(content.ranks):
            return
        target = content.ranks[rank_index + 1]
        if level < target.min_level:
            return
        trial = content.promotion_mission(target.id)
        if trial is None or trial.attempt_cost > remaining:
            return
        remaining -= trial.attempt_cost
        record.attempts_used += trial.attempt_cost
        won, _ = fight_once(content, trial, stats_of(level), rng)
        repairs_due += wear_cost(rules_gear.win_wear if won else rules_gear.loss_wear)
        result.promotion_attempts[target.id] = result.promotion_attempts.get(target.id, 0) + 1
        if not won:
            record.promotions_lost += 1
            promo_blocked = True
            return
        record.promotions_won += 1
        reward = guild.mission_reward(content, trial, content.ranks[rank_index].id)
        record.renown += reward.renown
        balance += reward.renown
        rank_index += 1
        result.rank_day.setdefault(target.id, day)
        result.gear_at_rank.setdefault(
            target.id, (owned_tier, owned_rarity if owned_tier else None)
        )

    for day in range(1, days + 1):
        day_key = (START_DAY + timedelta(days=day - 1)).isoformat()
        allowance = base_attempts + guild.streak_bonus(streak, rules.streak)
        remaining = allowance
        record = DayRecord(day=day, level=level, rank=content.ranks[rank_index].id)
        repairs_due = 0
        if geared and stats_for is None:
            reachable = [t for t in content.item_tiers() if t <= level]
            if reachable:
                top_tier = reachable[-1]
                for rarity in ("uncommon", "common"):
                    already = owned_tier == top_tier and (
                        owned_rarity == rarity or rarity == "common"
                    )
                    if already:
                        break
                    cost = sum(i.price for i in typical_items(content, top_tier, rarity=rarity))
                    if balance >= cost:
                        balance -= cost
                        record.spent += cost
                        owned_tier, owned_rarity = top_tier, rarity
                        break
        losses_here: dict[str, int] = {}
        board = [
            m
            for m in guild.board_missions(content, guild.draw_board(content, day_key))
            if m.kind == "mission"
        ]
        promo_blocked = False
        attempt_promotion()

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
            repairs_due += wear_cost(rules_gear.win_wear if won else rules_gear.loss_wear)
            if not won:
                record.losses += 1
                losses_here[mission.id] = losses_here.get(mission.id, 0) + 1
                continue
            record.wins += 1
            reward = guild.mission_reward(content, mission, rank_id)
            record.renown += reward.renown
            balance += reward.renown
            before = level
            level, xp, _ = guild.apply_xp(level, xp, reward.xp, cap, rules.xp)
            for reached in range(before + 1, level + 1):
                result.level_day.setdefault(reached, day)
            attempt_promotion()

        balance -= repairs_due
        record.spent += repairs_due
        if record.attempts_used:
            streak += 1
        else:
            streak = max(0, streak - rules.streak.drop_per_missed_day)
        record.level = level
        result.records.append(record)
    return result
