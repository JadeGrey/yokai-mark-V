"""Pure gear rules: stat rolls, loadouts and set bonuses, durability, prices, drops, shop stock."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence

from yokai.rpg.content import Content
from yokai.rpg.models import (
    RARITIES,
    SLOTS,
    STAT_LABELS,
    STAT_NAMES,
    GearRules,
    ItemDef,
    ItemInstance,
    Mission,
    Stats,
    Tag,
)
from yokai.rpg.stats import stats_at_level

WARNING_THRESHOLDS: tuple[int, ...] = (50, 25, 0)
SET_PIECES_SMALL = 2
SET_PIECES_BIG = 4


# ------------------------------------------------------------------------------- rolling


def roll_stats(item: ItemDef, rules: GearRules, rng: random.Random) -> Stats:
    """The stats of a new copy of `item`: each stat varies by up to roll_percent around its base.

    A stat never rolls to 0 or flips sign, so a nerf stays a nerf and a boost stays a boost.
    """
    spread = rules.roll_percent / 100
    values: dict[str, int] = {}
    for name in STAT_NAMES:
        base = getattr(item.stats, name)
        if base == 0:
            continue
        rolled = round(base * rng.uniform(1 - spread, 1 + spread))
        if rolled == 0 or (rolled > 0) != (base > 0):
            rolled = 1 if base > 0 else -1
        values[name] = rolled
    return Stats(**values)


# --------------------------------------------------------------------------- durability


def durability_percent(durability: int, max_durability: int) -> int:
    """Durability as a whole percent, rounded up so a barely used item is not shown as 99%."""
    if max_durability <= 0 or durability <= 0:
        return 0
    return min(100, math.ceil(durability * 100 / max_durability))


def wear_crossings(before: int, after: int, max_durability: int) -> list[int]:
    """Which of the 50%, 25% and 0% warnings an item crossed going from `before` to `after`."""
    old = durability_percent(before, max_durability)
    new = durability_percent(after, max_durability)
    return [t for t in WARNING_THRESHOLDS if old > t >= new]


def repair_cost(item: ItemDef, durability: int, rules: GearRules) -> int:
    """Renown to repair an item to full: repair_percent of its price, pro rata to the damage."""
    missing = max(0, item.max_durability - max(0, durability))
    if missing == 0:
        return 0
    return max(
        1, math.ceil(item.price * rules.repair_percent / 100 * missing / item.max_durability)
    )


def sell_value(item: ItemDef, rules: GearRules) -> int:
    """Renown the guild pays for an unequipped item."""
    return max(1, item.price * rules.sell_percent // 100)


# ------------------------------------------------------------------------------ loadouts


@dataclass(frozen=True, slots=True)
class Loadout:
    """Everything a character's equipped gear adds up to."""

    stats: Stats
    skills: tuple[str, ...]
    set_counts: Mapping[str, int]
    bonus_lines: tuple[str, ...]
    broken: tuple[ItemInstance, ...]


def _format_bonus(bonus: Stats) -> str:
    parts = []
    for name in STAT_NAMES:
        value = getattr(bonus, name)
        if value:
            parts.append(
                f"+{value} {STAT_LABELS[name]}"
                if name == "crit"
                else f"+{value}% {STAT_LABELS[name]}"
            )
    return ", ".join(parts)


def _apply_bonus(stats: Stats, bonus: Stats) -> Stats:
    values: dict[str, int] = {}
    for name in STAT_NAMES:
        current = getattr(stats, name)
        extra = getattr(bonus, name)
        values[name] = current + extra if name == "crit" else round(current * (1 + extra / 100))
    return Stats(**values)


def compute_loadout(content: Content, level: int, equipped: Sequence[ItemInstance]) -> Loadout:
    """Final stats, granted skills and set bonuses for a level and the gear worn.

    An item at 0 durability counts for nothing: no stats, no set piece and no skill. An item whose
    definition no longer exists is ignored.
    """
    stats = stats_at_level(content.levels, level)
    working: list[tuple[ItemInstance, ItemDef]] = []
    broken: list[ItemInstance] = []
    for instance in equipped:
        definition = content.items.get(instance.content_id)
        if definition is None:
            continue
        if instance.durability <= 0:
            broken.append(instance)
        else:
            working.append((instance, definition))
            stats = stats + instance.rolls

    counts: dict[str, int] = {}
    for _, definition in working:
        counts[definition.tag] = counts.get(definition.tag, 0) + 1

    skills: list[str] = []
    lines: list[str] = []
    for tag_id, count in counts.items():
        tag: Optional[Tag] = content.tags.get(tag_id)
        if tag is None:
            continue
        applied: list[str] = []
        if count >= SET_PIECES_SMALL:
            stats = _apply_bonus(stats, tag.bonus_2)
            applied.append(_format_bonus(tag.bonus_2))
        if count >= SET_PIECES_BIG:
            stats = _apply_bonus(stats, tag.bonus_4)
            applied.append(_format_bonus(tag.bonus_4))
            if tag.skill and tag.skill not in skills:
                skills.append(tag.skill)
        if applied:
            skill_note = (
                f", skill {content.skills[tag.skill].name}"
                if count >= SET_PIECES_BIG and tag.skill in content.skills
                else ""
            )
            lines.append(f"{tag.name} ({count}): {'; '.join(a for a in applied if a)}{skill_note}")
    for instance, definition in working:
        if definition.slot == "weapon" and definition.skill and definition.skill not in skills:
            skills.append(definition.skill)

    final = Stats(
        hp=max(1, stats.hp),
        attack=max(0, stats.attack),
        magic=max(0, stats.magic),
        defense=max(0, stats.defense),
        resist=max(0, stats.resist),
        speed=max(0, stats.speed),
        crit=max(0, stats.crit),
    )
    return Loadout(
        stats=final,
        skills=tuple(skills),
        set_counts=counts,
        bonus_lines=tuple(lines),
        broken=tuple(broken),
    )


def typical_loadout(
    content: Content, level: int, tag_id: str = "oni", rarity: str = "uncommon"
) -> Loadout:
    """A full set of the best gear a level allows, at full durability and unrolled stats.

    Used by the pacing simulator and tests as a model of a reasonably geared member.
    """
    instances: list[ItemInstance] = []
    for slot in SLOTS:
        candidates = [
            i
            for i in content.items.values()
            if i.tag == tag_id and i.slot == slot and i.rarity == rarity and i.req_level <= level
        ]
        if not candidates:
            continue
        best = max(candidates, key=lambda i: i.req_level)
        instances.append(
            ItemInstance(
                id=len(instances) + 1,
                owner_id=0,
                content_id=best.id,
                rolls=best.stats,
                durability=best.max_durability,
                created_ts=0,
                equipped_slot=slot,
            )
        )
    return compute_loadout(content, level, instances)


# ---------------------------------------------------------------------------- drops, shop


def is_boss_mission(content: Content, mission: Mission) -> bool:
    return any(content.enemies[e].boss for stage in mission.stages for e in stage.enemies)


def roll_drop(content: Content, mission: Mission, rng: random.Random) -> Optional[ItemDef]:
    """What a won mission drops for one member, if anything."""
    loot = content.balance.loot
    boss = is_boss_mission(content, mission)
    chance = loot.boss_drop_chance_percent if boss else loot.drop_chance_percent
    if rng.random() * 100 >= chance:
        return None
    weights = loot.boss_rarity_weights if boss else loot.rarity_weights
    rarity = rng.choices([r for r, _ in weights], weights=[w for _, w in weights])[0]
    top = mission.top_level
    # If the rolled rarity has nothing to give here (for example it was all retired), fall back
    # to the next rarity down instead of losing the drop.
    for rarity_name in reversed(RARITIES[: RARITIES.index(rarity) + 1]):
        pool = [i for i in content.items.values() if not i.retired and i.rarity == rarity_name]
        near = [i for i in pool if top - loot.tier_window <= i.req_level <= top]
        candidates = near or [i for i in pool if i.req_level <= top]
        if candidates:
            return rng.choice(candidates)
    return None


def draw_shop(content: Content, day: str) -> list[str]:
    """The day's shop: a few common and uncommon items for every gear tier, seeded by the day."""
    rng = random.Random(f"rpg-shop:{day}")
    per_tier = content.balance.shop.stock_per_tier
    ids: list[str] = []
    for tier in content.item_tiers():
        pool = [
            i.id
            for i in content.items.values()
            if i.req_level == tier and not i.retired and i.rarity in ("common", "uncommon")
        ]
        ids.extend(sorted(rng.sample(pool, min(per_tier, len(pool)))))
    return ids
