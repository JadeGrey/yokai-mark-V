"""Parsers for balance.toml, skills.toml, enemies.toml and missions.toml."""

from __future__ import annotations

from typing import Any, Mapping

from yokai.rpg.content.common import (
    ContentError,
    entries,
    int_value,
    number_value,
    stats_value,
    table_value,
    text_value,
)
from yokai.rpg.models import (
    BASIC_ACTIONS,
    ENEMY_TARGET_RULES,
    MISSION_KINDS,
    MODIFIABLE_STATS,
    SKILL_KINDS,
    SKILL_TARGETS,
    Balance,
    CombatRules,
    EnemyDef,
    Mission,
    PartyScaling,
    RewardRules,
    SkillDef,
    Stage,
    StreakRules,
    XpCurve,
)

PARTY_SIZES = 4
MAX_STAGES = 5
MAX_FOES_PER_STAGE = 5
FOE_TARGETS = ("foe", "all_foes")
ALLY_TARGETS = ("ally", "all_allies", "self")


def parse_balance(raw: dict[str, Any]) -> Balance:
    combat = table_value(raw.get("combat"), "balance.toml [combat]")
    xp = table_value(raw.get("xp"), "balance.toml [xp]")
    rewards = table_value(raw.get("rewards"), "balance.toml [rewards]")
    party = table_value(raw.get("party"), "balance.toml [party]")
    streak = table_value(raw.get("streak"), "balance.toml [streak]")
    where = "balance.toml [combat]"
    combat_rules = CombatRules(
        defense_k=int_value(combat.get("defense_k"), f"{where} defense_k", 1),
        crit_multiplier_percent=int_value(
            combat.get("crit_multiplier_percent"), f"{where} crit_multiplier_percent", 100, 500
        ),
        crit_cap=int_value(combat.get("crit_cap"), f"{where} crit_cap", 0, 100),
        variance_percent=int_value(
            combat.get("variance_percent"), f"{where} variance_percent", 0, 50
        ),
        defend_percent=int_value(combat.get("defend_percent"), f"{where} defend_percent", 1, 100),
        stage_recovery_percent=int_value(
            combat.get("stage_recovery_percent"), f"{where} stage_recovery_percent", 0, 100
        ),
        max_rounds=int_value(combat.get("max_rounds"), f"{where} max_rounds", 1, 500),
    )
    xp_curve = XpCurve(
        base=number_value(xp.get("base"), "balance.toml [xp] base", 0.1, 100000),
        exponent=number_value(xp.get("exponent"), "balance.toml [xp] exponent", 0.1, 5),
    )
    reward_rules = RewardRules(
        renown_per_level=int_value(
            rewards.get("renown_per_level"), "balance.toml [rewards] renown_per_level", 0
        ),
        below_rank_step=number_value(
            rewards.get("below_rank_step"), "balance.toml [rewards] below_rank_step", 0.01, 1
        ),
        below_rank_floor=number_value(
            rewards.get("below_rank_floor"), "balance.toml [rewards] below_rank_floor", 0, 1
        ),
    )
    hp_percent = _int_list(party.get("hp_percent"), "balance.toml [party] hp_percent", 100)
    extra_foes = _int_list(party.get("extra_foes"), "balance.toml [party] extra_foes", 0)
    if hp_percent[0] != 100:
        raise ContentError("balance.toml [party] hp_percent must start at 100 (a party of one)")
    if list(hp_percent) != sorted(hp_percent):
        raise ContentError("balance.toml [party] hp_percent must not decrease with party size")
    party_scaling = PartyScaling(hp_percent=hp_percent, extra_foes=extra_foes)
    where = "balance.toml [streak]"
    streak_rules = StreakRules(
        days_per_bonus=int_value(streak.get("days_per_bonus"), f"{where} days_per_bonus", 1),
        bonus_cap=int_value(streak.get("bonus_cap"), f"{where} bonus_cap", 0),
        drop_per_missed_day=int_value(
            streak.get("drop_per_missed_day"), f"{where} drop_per_missed_day", 0
        ),
    )
    return Balance(
        combat=combat_rules,
        xp=xp_curve,
        rewards=reward_rules,
        party=party_scaling,
        streak=streak_rules,
    )


def _int_list(value: object, where: str, minimum: int) -> tuple[int, ...]:
    if not isinstance(value, list) or len(value) != PARTY_SIZES:
        raise ContentError(f"{where} must be a list of {PARTY_SIZES} whole numbers")
    return tuple(int_value(item, f"{where}[{i}]", minimum) for i, item in enumerate(value))


def parse_skills(raw: dict[str, Any]) -> dict[str, SkillDef]:
    skills: dict[str, SkillDef] = {}
    for index, entry in enumerate(entries(raw, "skill", "skills.toml"), start=1):
        where = f"skills.toml skill #{index}"
        skill_id = text_value(entry.get("id"), f"{where} id")
        if skill_id in skills or skill_id in BASIC_ACTIONS:
            raise ContentError(f"skills.toml has a duplicate or reserved skill id: {skill_id}")
        skills[skill_id] = _parse_skill(skill_id, entry, where)
    return skills


def _parse_skill(skill_id: str, entry: dict[str, Any], where: str) -> SkillDef:
    name = text_value(entry.get("name"), f"{where} name")
    kind = entry.get("kind")
    if kind not in SKILL_KINDS:
        raise ContentError(f"{where} kind must be one of: {', '.join(SKILL_KINDS)}")
    target = entry.get("target")
    if target not in SKILL_TARGETS:
        raise ContentError(f"{where} target must be one of: {', '.join(SKILL_TARGETS)}")
    cooldown = int_value(entry.get("cooldown", 0), f"{where} cooldown", 0, 20)

    offensive = kind in ("damage", "debuff", "dot")
    if offensive and target not in FOE_TARGETS:
        raise ContentError(f"{where} ({kind}) must target foe or all_foes")
    if not offensive and target not in ALLY_TARGETS:
        raise ContentError(f"{where} ({kind}) must target ally, all_allies or self")

    scaling = "magic" if kind == "heal" else entry.get("scaling", "attack")
    power = 0
    stat = None
    percent = 0
    duration = 0
    if kind in ("damage", "dot", "heal"):
        if scaling not in BASIC_ACTIONS:
            raise ContentError(f"{where} scaling must be 'attack' or 'magic'")
        power = int_value(entry.get("power"), f"{where} power", 1, 1000)
    if kind in ("dot", "buff", "debuff"):
        duration = int_value(entry.get("duration"), f"{where} duration", 1, 20)
    if kind in ("buff", "debuff"):
        stat = entry.get("stat")
        if stat not in MODIFIABLE_STATS:
            raise ContentError(f"{where} stat must be one of: {', '.join(MODIFIABLE_STATS)}")
        percent = int_value(entry.get("percent"), f"{where} percent", 1, 90)
    return SkillDef(
        id=skill_id,
        name=name,
        kind=kind,
        target=target,
        scaling=scaling,
        power=power,
        cooldown=cooldown,
        stat=stat,
        percent=percent,
        duration=duration,
    )


def parse_enemies(raw: dict[str, Any], skills: Mapping[str, SkillDef]) -> dict[str, EnemyDef]:
    enemies: dict[str, EnemyDef] = {}
    for index, entry in enumerate(entries(raw, "enemy", "enemies.toml"), start=1):
        where = f"enemies.toml enemy #{index}"
        enemy_id = text_value(entry.get("id"), f"{where} id")
        if enemy_id in enemies:
            raise ContentError(f"enemies.toml has a duplicate enemy id: {enemy_id}")
        target = entry.get("target", "random")
        if target not in ENEMY_TARGET_RULES:
            raise ContentError(f"{where} target must be one of: {', '.join(ENEMY_TARGET_RULES)}")
        boss = entry.get("boss", False)
        if not isinstance(boss, bool):
            raise ContentError(f"{where} boss must be true or false")
        enemies[enemy_id] = EnemyDef(
            id=enemy_id,
            name=text_value(entry.get("name"), f"{where} name"),
            mult=stats_value(entry.get("mult"), f"{where} mult", 1, minimum=1),
            actions=_parse_actions(entry.get("actions"), where, skills),
            target=target,
            boss=boss,
        )
    return enemies


def _parse_actions(
    value: object, where: str, skills: Mapping[str, SkillDef]
) -> tuple[tuple[str, int], ...]:
    if not isinstance(value, list) or not value:
        raise ContentError(f"{where} actions must be a non-empty list")
    actions: list[tuple[str, int]] = []
    for position, item in enumerate(value, start=1):
        item_where = f"{where} action #{position}"
        data = table_value(item, item_where)
        action = text_value(data.get("do"), f"{item_where} do")
        if action not in BASIC_ACTIONS and action not in skills:
            raise ContentError(f"{item_where} uses an unknown skill: {action}")
        actions.append((action, int_value(data.get("weight"), f"{item_where} weight", 1, 100)))
    return tuple(actions)


def parse_missions(
    raw: dict[str, Any],
    rank_ids: tuple[str, ...],
    enemies: Mapping[str, EnemyDef],
    cap: int,
) -> dict[str, Mission]:
    missions: dict[str, Mission] = {}
    for index, entry in enumerate(entries(raw, "mission", "missions.toml"), start=1):
        where = f"missions.toml mission #{index}"
        mission_id = text_value(entry.get("id"), f"{where} id")
        if mission_id in missions:
            raise ContentError(f"missions.toml has a duplicate mission id: {mission_id}")
        rank = entry.get("rank")
        if rank not in rank_ids:
            raise ContentError(f"{where} rank {rank!r} is not in ranks.toml")
        kind = entry.get("kind", "mission")
        if kind not in MISSION_KINDS:
            raise ContentError(f"{where} kind must be one of: {', '.join(MISSION_KINDS)}")
        missions[mission_id] = Mission(
            id=mission_id,
            name=text_value(entry.get("name"), f"{where} name"),
            rank=rank,
            kind=kind,
            attempt_cost=int_value(entry.get("attempt_cost"), f"{where} attempt_cost", 1, 10),
            stages=_parse_stages(entry.get("stages"), where, enemies, cap),
            description=str(entry.get("description", "")).strip(),
        )
    return missions


def _parse_stages(
    value: object, where: str, enemies: Mapping[str, EnemyDef], cap: int
) -> tuple[Stage, ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_STAGES:
        raise ContentError(f"{where} stages must be a list of 1 to {MAX_STAGES} stages")
    stages: list[Stage] = []
    for position, item in enumerate(value, start=1):
        stage_where = f"{where} stage #{position}"
        data = table_value(item, stage_where)
        foes = data.get("enemies")
        if not isinstance(foes, list) or not 1 <= len(foes) <= MAX_FOES_PER_STAGE:
            raise ContentError(f"{stage_where} enemies must list 1 to {MAX_FOES_PER_STAGE} enemies")
        for foe in foes:
            if foe not in enemies:
                raise ContentError(f"{stage_where} uses an unknown enemy: {foe}")
        level = int_value(data.get("level"), f"{stage_where} level", 1, cap)
        stages.append(Stage(enemies=tuple(foes), level=level))
    return tuple(stages)
