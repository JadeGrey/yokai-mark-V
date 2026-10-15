"""Damage and healing math. All randomness comes from the RNG passed in."""

from __future__ import annotations

import random

from yokai.rpg.models import CombatRules


def mitigation(rules: CombatRules, defense: int) -> float:
    """Fraction of damage that gets through a defensive stat (1.0 at 0 defense)."""
    return rules.defense_k / (rules.defense_k + max(0, defense))


def roll_damage(
    rules: CombatRules,
    rng: random.Random,
    *,
    offense: int,
    power: int,
    defense: int,
    crit_chance: int,
    defending: bool,
) -> tuple[int, bool]:
    """Damage of one hit and whether it crit. Always at least 1."""
    spread = rules.variance_percent / 100
    variance = rng.uniform(1 - spread, 1 + spread)
    crit = rng.random() < min(rules.crit_cap, max(0, crit_chance)) / 100
    amount = offense * power / 100 * mitigation(rules, defense) * variance
    if crit:
        amount *= rules.crit_multiplier_percent / 100
    if defending:
        amount *= rules.defend_percent / 100
    return max(1, round(amount)), crit


def dot_damage(rules: CombatRules, *, offense: int, power: int, defense: int) -> int:
    """Per-round damage of a damage-over-time effect, fixed when it is applied."""
    return max(1, round(offense * power / 100 * mitigation(rules, defense)))


def heal_amount(*, magic: int, power: int) -> int:
    """Healing of one cast. Always at least 1."""
    return max(1, round(magic * power / 100))
