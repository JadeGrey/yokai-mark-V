"""Build fight combatants from characters and mission stages."""

from __future__ import annotations

import string
from collections import Counter
from typing import Optional

from yokai.rpg.combat.models import SIDE_FOES, SIDE_PLAYERS, Combatant
from yokai.rpg.content import Content
from yokai.rpg.models import STAT_NAMES, Mission, Stage, Stats
from yokai.rpg.stats import stats_at_level

MAX_FOES = 6


def player_combatant(
    user_id: int, name: str, stats: Stats, skills: tuple[str, ...] = ()
) -> Combatant:
    """A player at full health."""
    return Combatant(
        id=f"p{user_id}",
        name=name,
        side=SIDE_PLAYERS,
        stats=stats,
        hp=stats.hp,
        skills=skills,
        user_id=user_id,
    )


def enemy_stats(base: Stats, mult: Stats, power_percent: int = 100) -> Stats:
    """The stats of an enemy: a level's base stats times its archetype and rank power (percent)."""
    return Stats(
        *(
            max(1, round(getattr(base, name) * getattr(mult, name) / 100 * power_percent / 100))
            for name in STAT_NAMES
        )
    )


def build_stage_foes(
    content: Content,
    stage: Stage,
    stage_number: int,
    party_size: int,
    power_percent: int = 100,
) -> list[Combatant]:
    """The foes of one stage, scaled for the party size (spec section 8).

    Party scaling raises foe HP and adds extra copies of the stage's weakest non-boss foe, but
    only to a stage that already has a group (a pack, or a boss with escorts). A stage with one
    lone foe just gets more HP, so a party never meets three copies of a single big monster.
    """
    scaling = content.balance.party
    index = min(max(party_size, 1), len(scaling.hp_percent)) - 1
    hp_percent = scaling.hp_percent[index]
    foe_ids = list(stage.enemies)
    candidates = [e for e in foe_ids if not content.enemies[e].boss]
    if scaling.extra_foes[index] and candidates and len(foe_ids) >= 2:
        weakest = min(candidates, key=lambda e: content.enemies[e].mult.hp)
        foe_ids.extend([weakest] * scaling.extra_foes[index])
    foe_ids = foe_ids[:MAX_FOES]

    base = stats_at_level(content.levels, stage.level)
    totals = Counter(foe_ids)
    seen: Counter[str] = Counter()
    foes: list[Combatant] = []
    for position, enemy_id in enumerate(foe_ids):
        definition = content.enemies[enemy_id]
        stats = enemy_stats(base, definition.mult, power_percent)
        max_hp = max(1, round(stats.hp * hp_percent / 100))
        stats = Stats(
            max_hp, stats.attack, stats.magic, stats.defense, stats.resist, stats.speed, stats.crit
        )
        name = definition.name
        if totals[enemy_id] > 1:
            name = f"{name} {_letter(seen[enemy_id])}"
        seen[enemy_id] += 1
        foes.append(
            Combatant(
                id=f"f{stage_number}_{position}",
                name=name,
                side=SIDE_FOES,
                stats=stats,
                hp=stats.hp,
                boss=definition.boss,
                ai_actions=definition.actions,
                ai_target=definition.target,
            )
        )
    return foes


def _letter(index: int) -> str:
    return string.ascii_uppercase[index % 26]


def build_stages(
    content: Content, stages: tuple[Stage, ...], party_size: int, power_percent: int = 100
) -> list[list[Combatant]]:
    """All stages of a mission, ready to hand to `Fight`."""
    return [
        build_stage_foes(content, stage, number, party_size, power_percent)
        for number, stage in enumerate(stages, start=1)
    ]


def build_mission_stages(
    content: Content, mission: Mission, party_size: int
) -> list[list[Combatant]]:
    """A mission's stages, with its rank's enemy power applied."""
    power = round(content.rank(mission.rank).enemy_power * mission.power / 100)
    return build_stages(content, mission.stages, party_size, max(1, power))


def default_player_name(user_id: int, name: Optional[str]) -> str:
    """A display name for a player, never empty."""
    cleaned = (name or "").strip()
    return cleaned[:32] if cleaned else f"Adventurer {user_id % 10000}"
