"""Generates content/rpg/items.toml from the tags and the level curve.

There are 800 items (8 tags x 5 slots x 5 tiers x 4 rarities), far too many to write by hand, so
this module is the single source of truth: change a constant here, rerun
`python scripts/gen_rpg_items.py`, and commit both. `tests/test_rpg_gear_content.py` fails if the
committed file and this generator disagree.

An item's budget is measured in "level equivalents" (how many levels of stat growth it is worth):
`POWER_PER_LEVEL x level requirement x slot weight x rarity multiplier`. The budget is spread over
the tag's lean stats, and a small part is taken away from the tag's drawback stat.
"""

from __future__ import annotations

from typing import Mapping

from yokai.rpg.models import SLOTS, STAT_NAMES, Stats, Tag

TIERS: tuple[int, ...] = (1, 12, 24, 38, 52)

SLOT_WEIGHT: dict[str, float] = {
    "weapon": 1.5,
    "body": 1.2,
    "head": 0.9,
    "boots": 0.8,
    "accessory": 0.6,
}

# rarity -> (stat multiplier, price multiplier, max durability)
RARITY_TABLE: dict[str, tuple[float, float, int]] = {
    "common": (1.0, 1.0, 100),
    "uncommon": (1.2, 2.5, 110),
    "rare": (1.45, 6.0, 125),
    "epic": (1.75, 15.0, 150),
}

POWER_PER_LEVEL = 0.12
DRAWBACK_SHARE = 0.2
PRICE_BASE = 8
MIN_BUDGET_LEVEL = 3
MIN_PRICE_LEVEL = 5
LEAN_SHARES: dict[int, tuple[float, ...]] = {1: (1.0,), 2: (0.6, 0.4), 3: (0.5, 0.3, 0.2)}
CRIT_UNIT = 0.5  # crit has no per-level growth, so one level equivalent is worth this many points

# tier index -> one adjective per rarity (common, uncommon, rare, epic)
ADJECTIVES: tuple[tuple[str, str, str, str], ...] = (
    ("Worn", "Sturdy", "Fine", "Gleaming"),
    ("Forged", "Honed", "Keen", "Gilded"),
    ("Tempered", "Veteran", "Brilliant", "Radiant"),
    ("Hardened", "Warded", "Exalted", "Sublime"),
    ("Masterwork", "Heirloom", "Ancestral", "Mythic"),
)

NOUNS: dict[str, dict[str, str]] = {
    "oni": {
        "weapon": "Oni Kanabo",
        "head": "Oni Horned Mask",
        "body": "Oni Demon Plate",
        "boots": "Oni Iron Greaves",
        "accessory": "Oni Fang Charm",
    },
    "kitsune": {
        "weapon": "Kitsune Fire Fan",
        "head": "Kitsune Fox Mask",
        "body": "Kitsune Kimono",
        "boots": "Kitsune Geta",
        "accessory": "Kitsune Foxfire Orb",
    },
    "tengu": {
        "weapon": "Tengu Katana",
        "head": "Tengu Crow Mask",
        "body": "Tengu Feather Cloak",
        "boots": "Tengu Wind Sandals",
        "accessory": "Tengu Feather Charm",
    },
    "kappa": {
        "weapon": "Kappa Water Staff",
        "head": "Kappa Dish Cap",
        "body": "Kappa River Robe",
        "boots": "Kappa Webbed Boots",
        "accessory": "Kappa Cucumber Charm",
    },
    "yuki_onna": {
        "weapon": "Yuki-onna Frost Wand",
        "head": "Yuki-onna Snow Veil",
        "body": "Yuki-onna Ice Kimono",
        "boots": "Yuki-onna Snow Boots",
        "accessory": "Yuki-onna Frost Pendant",
    },
    "tsukumogami": {
        "weapon": "Tsukumogami Haunted Lantern",
        "head": "Tsukumogami Straw Hat",
        "body": "Tsukumogami Patchwork Armor",
        "boots": "Tsukumogami Clog Boots",
        "accessory": "Tsukumogami Aged Bell",
    },
    "jorogumo": {
        "weapon": "Jorogumo Web Blades",
        "head": "Jorogumo Spider Mask",
        "body": "Jorogumo Silk Robe",
        "boots": "Jorogumo Silk Wraps",
        "accessory": "Jorogumo Venom Vial",
    },
    "baku": {
        "weapon": "Baku Dream Staff",
        "head": "Baku Tapir Mask",
        "body": "Baku Nightmare Shroud",
        "boots": "Baku Dreamwalkers",
        "accessory": "Baku Dream Charm",
    },
}

# Item ids that no longer drop or sell but keep working for members who own them.
RETIRED: frozenset[str] = frozenset()

# Epic weapons carry one extra skill, borrowed from the tag's flavour.
EPIC_WEAPON_SKILLS: dict[str, str] = {
    "oni": "crushing_blow",
    "kitsune": "fox_fire",
    "tengu": "rend",
    "jorogumo": "venom_spit",
    "yuki_onna": "terrifying_howl",
    "baku": "weakening_hex",
}


def item_id(tag: str, slot: str, tier: int, rarity: str) -> str:
    return f"{tag}_{slot}_t{tier + 1}_{rarity}"


def _unit(stat: str, growth: Stats) -> float:
    value = getattr(growth, stat)
    return float(value) if value > 0 else CRIT_UNIT


def item_stats(tag: Tag, slot: str, req_level: int, rarity: str, growth: Stats) -> dict[str, int]:
    """The base stats of one item (before rolling), in STAT_NAMES order."""
    stat_mult = RARITY_TABLE[rarity][0]
    budget = POWER_PER_LEVEL * max(req_level, MIN_BUDGET_LEVEL) * SLOT_WEIGHT[slot] * stat_mult
    shares = LEAN_SHARES[len(tag.leans)]
    raw: dict[str, int] = {}
    for position, (stat, share) in enumerate(zip(tag.leans, shares)):
        value = round(budget * share * _unit(stat, growth))
        raw[stat] = max(1, value) if position == 0 else value
    if tag.drawback and tag.drawback not in raw:
        penalty = round(budget * DRAWBACK_SHARE * _unit(tag.drawback, growth))
        if penalty:
            raw[tag.drawback] = -penalty
    return {stat: raw[stat] for stat in STAT_NAMES if raw.get(stat)}


def item_price(req_level: int, rarity: str, slot: str) -> int:
    price_mult = RARITY_TABLE[rarity][1]
    return round(PRICE_BASE * max(req_level, MIN_PRICE_LEVEL) * price_mult * SLOT_WEIGHT[slot])


def generate_items_toml(tags: Mapping[str, Tag], growth: Stats) -> str:
    """The full text of items.toml."""
    header = (
        "# GENERATED by scripts/gen_rpg_items.py from tags.toml and the level curve.\n"
        "# Do not edit by hand: change src/yokai/rpg/itemgen.py and regenerate. Ids are\n"
        "# permanent; retire an item by adding its id to RETIRED in itemgen.py, never delete it.\n"
    )
    blocks: list[str] = [header]
    for tag in tags.values():
        for slot in SLOTS:
            for tier_index, req_level in enumerate(TIERS):
                for rarity_index, rarity in enumerate(RARITY_TABLE):
                    stats = item_stats(tag, slot, req_level, rarity, growth)
                    stat_text = ", ".join(f"{name} = {value}" for name, value in stats.items())
                    lines = [
                        "[[item]]",
                        f'id = "{item_id(tag.id, slot, tier_index, rarity)}"',
                        f'name = "{ADJECTIVES[tier_index][rarity_index]} {NOUNS[tag.id][slot]}"',
                        f'slot = "{slot}"',
                        f'tag = "{tag.id}"',
                        f'rarity = "{rarity}"',
                        f"req_level = {req_level}",
                        f"price = {item_price(req_level, rarity, slot)}",
                        f"max_durability = {RARITY_TABLE[rarity][2]}",
                        f"stats = {{ {stat_text} }}",
                    ]
                    if rarity == "epic" and slot == "weapon" and tag.id in EPIC_WEAPON_SKILLS:
                        lines.append(f'skill = "{EPIC_WEAPON_SKILLS[tag.id]}"')
                    if item_id(tag.id, slot, tier_index, rarity) in RETIRED:
                        lines.append("retired = true")
                    blocks.append("\n".join(lines) + "\n")
    return "\n".join(blocks)
