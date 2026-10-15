"""Pure guild rules: days, streaks, daily attempts, the mission board, XP and rewards."""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, tzinfo
from typing import Optional

from yokai.challenges.periods import DAILY, next_reset_ts, period_key
from yokai.errors import NoAttemptsError
from yokai.rpg.content import Content
from yokai.rpg.models import Character, Mission, Rank, StreakRules, XpCurve

MISSIONS_PER_RANK = 3
RAIDS_PER_RANK = 1


# ------------------------------------------------------------------------------------- days


def day_key(now_ts: int, tz: tzinfo) -> str:
    """The guild day for a Unix time: an ISO date in the configured zone."""
    return period_key(DAILY, now_ts, tz)


def next_day_ts(now_ts: int, tz: tzinfo) -> int:
    """Unix time of the next guild-day rollover."""
    return next_reset_ts(DAILY, now_ts, tz)


def days_between(earlier: str, later: str) -> int:
    """Whole days from one day key to another (negative if `later` is before `earlier`)."""
    return (date.fromisoformat(later) - date.fromisoformat(earlier)).days


# ---------------------------------------------------------------------- streak and attempts


def rollover_streak(
    streak: int, last_active_day: Optional[str], today: str, rules: StreakRules
) -> int:
    """The streak as it stands at the start of `today`.

    Each fully missed day removes `drop_per_missed_day` days; it never goes below 0.
    """
    if last_active_day is None or streak <= 0:
        return 0
    missed = max(0, days_between(last_active_day, today) - 1)
    return max(0, streak - rules.drop_per_missed_day * missed)


def streak_bonus(streak: int, rules: StreakRules) -> int:
    """Extra daily attempts earned by a streak."""
    return min(rules.bonus_cap, max(0, streak) // rules.days_per_bonus)


@dataclass(frozen=True, slots=True)
class AttemptStatus:
    """Where a member stands on today's attempts."""

    allowance: int
    used: int
    streak: int
    bonus: int

    @property
    def remaining(self) -> int:
        return max(0, self.allowance - self.used)


@dataclass(frozen=True, slots=True)
class AttemptPlan:
    """The character fields to write after spending attempts."""

    used: int
    day: str
    streak: int
    last_active_day: str


def attempt_status(
    character: Character, today: str, base_attempts: int, rules: StreakRules
) -> AttemptStatus:
    """Today's attempts for a character, as they would be after the day rolls over (read-only)."""
    streak = rollover_streak(character.streak, character.last_active_day, today, rules)
    used = character.attempts_used if character.attempts_day == today else 0
    bonus = streak_bonus(streak, rules)
    return AttemptStatus(allowance=base_attempts + bonus, used=used, streak=streak, bonus=bonus)


def plan_spend(
    character: Character,
    cost: int,
    today: str,
    base_attempts: int,
    rules: StreakRules,
    resets_ts: Optional[int] = None,
) -> AttemptPlan:
    """Work out spending `cost` attempts. Raises NoAttemptsError if there are not enough.

    The first spend on a new day also extends the streak by one.
    """
    status = attempt_status(character, today, base_attempts, rules)
    if cost > status.remaining:
        raise NoAttemptsError(status.remaining, cost, resets_ts)
    streak = status.streak + (0 if character.last_active_day == today else 1)
    return AttemptPlan(used=status.used + cost, day=today, streak=streak, last_active_day=today)


# --------------------------------------------------------------------------- XP and levels


def apply_xp(level: int, xp: int, gained: int, cap: int, curve: XpCurve) -> tuple[int, int, int]:
    """Add XP and level up. Returns (level, xp, levels gained). A capped character gains nothing."""
    if level >= cap:
        return cap, 0, 0
    total = xp + max(0, gained)
    levels_gained = 0
    while level < cap and total >= curve.xp_to_next(level):
        total -= curve.xp_to_next(level)
        level += 1
        levels_gained += 1
    if level >= cap:
        total = 0
    return level, total, levels_gained


# ---------------------------------------------------------------------- eligibility, rewards


@dataclass(frozen=True, slots=True)
class Reward:
    xp: int
    renown: int


def _rank_index(content: Content, rank_id: str) -> int:
    """A rank's position, treating a rank that no longer exists as the lowest."""
    try:
        return content.rank_index(rank_id)
    except KeyError:
        return 0


def can_enter(content: Content, player_rank: str, mission: Mission) -> bool:
    """A member may enter missions at their own rank or below."""
    return _rank_index(content, mission.rank) <= _rank_index(content, player_rank)


def party_can_enter(content: Content, ranks: list[str], mission: Mission) -> bool:
    """A party is limited by its lowest-ranked member."""
    return all(can_enter(content, rank, mission) for rank in ranks)


def mission_reward(content: Content, mission: Mission, player_rank: str) -> Reward:
    """XP and Renown for winning `mission`, reduced when it is below the player's rank."""
    rules = content.balance
    rank = content.rank(mission.rank)
    steps = max(0, _rank_index(content, player_rank) - _rank_index(content, mission.rank))
    share = max(rules.rewards.below_rank_floor, rules.rewards.below_rank_step**steps)
    top = mission.top_level
    xp = rules.xp.xp_to_next(top) * rank.xp_share * mission.attempt_cost * share
    renown = rules.rewards.renown_per_level * top * mission.attempt_cost * share
    return Reward(xp=max(1, round(xp)), renown=max(0, round(renown)))


# ------------------------------------------------------------------------------- the board


def draw_board(
    content: Content,
    day: str,
    per_rank: int = MISSIONS_PER_RANK,
    raids_per_rank: int = RAIDS_PER_RANK,
) -> list[str]:
    """Pick the day's board: regular missions and raids from each rank, seeded by the day.

    Within a rank the regular missions come first, then the raids. Ranks without raids simply
    have none. Raids use their own seed so adding or removing them never reshuffles the regular
    missions.
    """
    rngs = {
        "mission": random.Random(f"rpg-board:{day}"),
        "raid": random.Random(f"rpg-raids:{day}"),
    }
    ids: list[str] = []
    for rank in content.ranks:
        for kind, count in (("mission", per_rank), ("raid", raids_per_rank)):
            pool = [m.id for m in content.missions_for_rank(rank.id) if m.kind == kind]
            chosen = set(rngs[kind].sample(pool, min(count, len(pool))))
            ids.extend(mission_id for mission_id in pool if mission_id in chosen)
    return ids


def board_missions(content: Content, ids: list[str]) -> list[Mission]:
    """The board's missions, skipping any id that content changes have since removed."""
    return [content.missions[i] for i in ids if i in content.missions]


# ----------------------------------------------------------------------------- promotion


@dataclass(frozen=True, slots=True)
class PromotionStatus:
    """Where a member stands on the way to the next rank."""

    next_rank: Optional[Rank]  # None at the top, or when the member's rank is not in the content
    known_rank: bool
    level_ok: bool
    cooldown_until: Optional[int]  # set only while a cooldown is still running

    @property
    def top(self) -> bool:
        return self.known_rank and self.next_rank is None

    @property
    def ready(self) -> bool:
        return self.next_rank is not None and self.level_ok and self.cooldown_until is None


def promotion_status(content: Content, character: Character, now: int) -> PromotionStatus:
    """Whether a member can challenge for the next rank right now (read-only)."""
    try:
        upcoming = content.next_rank(character.rank)
    except KeyError:
        return PromotionStatus(None, False, False, None)
    cooldown = character.promo_cooldown_until
    active = cooldown if cooldown is not None and cooldown > now else None
    level_ok = upcoming is not None and character.level >= upcoming.min_level
    return PromotionStatus(upcoming, True, level_ok, active)
