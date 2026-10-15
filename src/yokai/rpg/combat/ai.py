"""Enemy decision making: a weighted pick among the actions that are ready."""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

from yokai.rpg.combat.models import ACTION_ATTACK, ACTION_MAGIC, ACTION_SKILL, Action, Combatant
from yokai.rpg.models import BASIC_ACTIONS

if TYPE_CHECKING:
    from yokai.rpg.combat.engine import Fight

_OFFENSIVE_KINDS = ("damage", "debuff", "dot")


def choose_enemy_action(foe: Combatant, fight: Fight, rng: random.Random) -> Action:
    """Pick the foe's action for this round. Never raises: it falls back to a plain attack."""
    options: list[tuple[str, int]] = []
    for do, weight in foe.ai_actions:
        if do in BASIC_ACTIONS or foe.cooldowns.get(do, 0) == 0:
            options.append((do, weight))
    if not options:
        options = [(ACTION_ATTACK, 1)]
    chosen = rng.choices([do for do, _ in options], weights=[w for _, w in options])[0]

    targets = fight.living_players()
    target_id = _pick_target(foe, targets, rng) if targets else None
    if chosen == ACTION_ATTACK:
        return Action(ACTION_ATTACK, target_id=target_id)
    if chosen == ACTION_MAGIC:
        return Action(ACTION_MAGIC, target_id=target_id)
    skill = fight.skills[chosen]
    # Support skills are resolved by the engine (lowest-HP ally, or self); only attacks need a pick.
    return Action(
        ACTION_SKILL,
        skill_id=chosen,
        target_id=target_id if skill.kind in _OFFENSIVE_KINDS else None,
    )


def _pick_target(foe: Combatant, players: list[Combatant], rng: random.Random) -> str:
    if foe.ai_target == "lowest_hp":
        return min(players, key=lambda p: p.hp).id
    return rng.choice(players).id
