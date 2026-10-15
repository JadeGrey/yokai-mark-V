"""Tests for the pure gear rules."""

from __future__ import annotations

import random
from dataclasses import replace
from pathlib import Path

import pytest

from yokai.rpg import gear
from yokai.rpg.content import Content, load_content
from yokai.rpg.models import GearRules, ItemDef, ItemInstance, Stats
from yokai.rpg.stats import stats_at_level

RULES = GearRules(
    sell_percent=30, repair_percent=35, win_wear=3, loss_wear=8, inventory_cap=60, roll_percent=10
)


@pytest.fixture(scope="module")
def content() -> Content:
    return load_content(Path("content/rpg"))


def instance(
    content: Content, content_id: str, *, durability: int | None = None, n: int = 1
) -> ItemInstance:
    definition = content.items[content_id]
    return ItemInstance(
        id=n,
        owner_id=1,
        content_id=content_id,
        rolls=definition.stats,
        durability=definition.max_durability if durability is None else durability,
        created_ts=0,
        equipped_slot=definition.slot,
    )


def oni_set(
    content: Content, count: int, tier: int = 3, rarity: str = "common"
) -> list[ItemInstance]:
    slots = ["weapon", "head", "body", "boots", "accessory"][:count]
    return [
        instance(content, f"oni_{slot}_t{tier}_{rarity}", n=i + 1) for i, slot in enumerate(slots)
    ]


# --------------------------------------------------------------------------------- rolling


def item(stats: Stats) -> ItemDef:
    return ItemDef("x", "X", "weapon", "oni", "common", 1, stats, 10, 100)


def test_rolls_stay_within_the_percentage_and_keep_their_sign() -> None:
    definition = item(Stats(hp=100, attack=20, speed=-10))
    rng = random.Random(3)
    for _ in range(300):
        rolled = gear.roll_stats(definition, RULES, rng)
        assert 90 <= rolled.hp <= 110 and 18 <= rolled.attack <= 22
        assert -11 <= rolled.speed <= -9
        assert rolled.magic == 0 and rolled.crit == 0


def test_a_small_stat_never_rolls_to_zero_or_flips() -> None:
    definition = item(Stats(attack=1, speed=-1))
    rng = random.Random(5)
    for _ in range(200):
        rolled = gear.roll_stats(definition, replace(RULES, roll_percent=50), rng)
        assert rolled.attack >= 1 and rolled.speed <= -1


def test_rolls_are_exact_with_no_spread_and_repeatable_with_a_seed() -> None:
    definition = item(Stats(hp=77, attack=9))
    assert gear.roll_stats(definition, replace(RULES, roll_percent=0), random.Random(1)) == (
        definition.stats
    )
    assert gear.roll_stats(definition, RULES, random.Random(9)) == gear.roll_stats(
        definition, RULES, random.Random(9)
    )


# ----------------------------------------------------------------------------- durability


@pytest.mark.parametrize(
    ("current", "maximum", "percent"),
    [(100, 100, 100), (0, 100, 0), (-5, 100, 0), (1, 100, 1), (50, 100, 50), (49, 100, 49),
     (115, 110, 100), (3, 0, 0), (1, 150, 1), (75, 150, 50)],
)  # fmt: skip
def test_durability_percent_rounds_up_and_clamps(current: int, maximum: int, percent: int) -> None:
    assert gear.durability_percent(current, maximum) == percent


@pytest.mark.parametrize(
    ("before", "after", "crossed"),
    [
        (100, 99, []),
        (100, 50, [50]),
        (51, 49, [50]),
        (60, 20, [50, 25]),
        (30, 0, [25, 0]),
        (100, 0, [50, 25, 0]),
        (49, 48, []),
        (0, 0, []),
        (20, 60, []),  # a repair crosses nothing
        (50, 50, []),
    ],
)
def test_wear_crossings_report_each_threshold_once(
    before: int, after: int, crossed: list[int]
) -> None:
    assert gear.wear_crossings(before, after, 100) == crossed


def test_repair_cost_is_pro_rata_of_the_price(content: Content) -> None:
    definition = content.items["oni_weapon_t3_common"]  # price 288, durability 100
    assert gear.repair_cost(definition, 100, RULES) == 0
    assert gear.repair_cost(definition, 0, RULES) == 101  # ceil(288 * 0.35)
    assert gear.repair_cost(definition, 50, RULES) == 51
    assert gear.repair_cost(definition, 99, RULES) == 2  # a sliver still costs something
    assert gear.repair_cost(definition, -20, RULES) == 101  # negative treated as broken
    assert gear.repair_cost(definition, 150, RULES) == 0  # over max treated as full


def test_stronger_gear_costs_more_to_maintain(content: Content) -> None:
    cheap = content.items["oni_weapon_t1_common"]
    pricey = content.items["oni_weapon_t5_epic"]
    assert gear.repair_cost(pricey, 0, RULES) > 20 * gear.repair_cost(cheap, 0, RULES)


def test_selling_pays_a_share_of_the_price_with_a_floor(content: Content) -> None:
    assert gear.sell_value(content.items["oni_weapon_t3_common"], RULES) == 86
    assert gear.sell_value(item(Stats(hp=1)), replace(RULES, sell_percent=0)) == 1


# ------------------------------------------------------------------------------- loadouts


def test_no_gear_is_just_the_level_stats(content: Content) -> None:
    loadout = gear.compute_loadout(content, 10, [])
    assert loadout.stats == stats_at_level(content.levels, 10)
    assert loadout.skills == () and loadout.set_counts == {} and loadout.bonus_lines == ()


def test_one_piece_adds_its_rolled_stats_and_no_bonus(content: Content) -> None:
    piece = instance(content, "oni_weapon_t3_common")
    loadout = gear.compute_loadout(content, 24, [piece])
    assert loadout.stats == stats_at_level(content.levels, 24) + piece.rolls
    assert loadout.set_counts == {"oni": 1} and loadout.bonus_lines == ()


def test_two_pieces_give_the_small_set_bonus_in_percent(content: Content) -> None:
    pieces = oni_set(content, 2)
    plain = stats_at_level(content.levels, 24) + pieces[0].rolls + pieces[1].rolls
    loadout = gear.compute_loadout(content, 24, pieces)
    assert loadout.stats.attack == round(plain.attack * 1.08)
    assert loadout.stats.hp == plain.hp  # the 4-piece HP bonus is not active yet
    assert loadout.skills == () and "Oni (2)" in loadout.bonus_lines[0]


def test_three_pieces_are_still_only_the_small_bonus(content: Content) -> None:
    three = gear.compute_loadout(content, 24, oni_set(content, 3))
    two = gear.compute_loadout(content, 24, oni_set(content, 2))
    assert three.skills == () and three.set_counts == {"oni": 3}
    assert three.stats.attack >= two.stats.attack  # more raw stats, same bonus tier


def test_four_pieces_add_the_big_bonus_and_grant_the_tag_skill(content: Content) -> None:
    pieces = oni_set(content, 4)
    plain = stats_at_level(content.levels, 24)
    for piece in pieces:
        plain = plain + piece.rolls
    loadout = gear.compute_loadout(content, 24, pieces)
    assert loadout.skills == ("rampage",)
    expected_attack = round(round(plain.attack * 1.08) * 1.12)
    assert loadout.stats.attack == expected_attack
    assert "Rampage" in loadout.bonus_lines[0]


def test_crit_bonuses_are_flat_points_not_percent(content: Content) -> None:
    pieces = [
        instance(content, f"tengu_{s}_t3_common", n=i) for i, s in enumerate(["weapon", "head"])
    ]
    plain = stats_at_level(content.levels, 24) + pieces[0].rolls + pieces[1].rolls
    loadout = gear.compute_loadout(content, 24, pieces)
    assert loadout.stats.crit == plain.crit + 4


def test_broken_gear_counts_for_nothing(content: Content) -> None:
    """Review Focus 4: 0% durability means no stats, no set piece and no skill."""
    pieces = oni_set(content, 4)
    whole = gear.compute_loadout(content, 24, pieces)
    assert whole.skills == ("rampage",)
    pieces[0] = replace(pieces[0], durability=0)
    three_working = gear.compute_loadout(content, 24, pieces)
    assert three_working.skills == () and three_working.set_counts == {"oni": 3}
    assert three_working.broken == (pieces[0],)
    only_bare = gear.compute_loadout(content, 24, [replace(p, durability=0) for p in pieces])
    assert only_bare.stats == stats_at_level(content.levels, 24)
    assert only_bare.set_counts == {} and len(only_bare.broken) == 4


def test_an_epic_weapon_brings_its_own_skill_unless_broken(content: Content) -> None:
    weapon = instance(content, "oni_weapon_t5_epic")
    assert gear.compute_loadout(content, 52, [weapon]).skills == ("crushing_blow",)
    broken = replace(weapon, durability=0)
    assert gear.compute_loadout(content, 52, [broken]).skills == ()


def test_set_skills_and_weapon_skills_do_not_duplicate(content: Content) -> None:
    pieces = oni_set(content, 4, tier=5, rarity="epic")  # the epic weapon grants crushing_blow
    skills = gear.compute_loadout(content, 52, pieces).skills
    assert skills.count("rampage") == 1 and skills.count("crushing_blow") == 1


def test_mixed_tags_apply_their_own_bonuses(content: Content) -> None:
    pieces = [
        instance(content, "oni_weapon_t3_common", n=1),
        instance(content, "oni_head_t3_common", n=2),
        instance(content, "kitsune_body_t3_common", n=3),
        instance(content, "kitsune_boots_t3_common", n=4),
        instance(content, "baku_accessory_t3_common", n=5),
    ]
    loadout = gear.compute_loadout(content, 24, pieces)
    assert loadout.set_counts == {"oni": 2, "kitsune": 2, "baku": 1}
    assert loadout.skills == () and len(loadout.bonus_lines) == 2


def test_items_that_no_longer_exist_are_ignored(content: Content) -> None:
    ghost = ItemInstance(1, 1, "retired_and_deleted", Stats(attack=999), 50, 0)
    loadout = gear.compute_loadout(content, 10, [ghost])
    assert loadout.stats == stats_at_level(content.levels, 10) and not loadout.broken


def test_final_stats_never_go_below_their_floors(content: Content) -> None:
    awful = ItemInstance(1, 1, "oni_weapon_t1_common", Stats(hp=-9999, attack=-9999), 100, 0)
    stats = gear.compute_loadout(content, 1, [awful]).stats
    assert stats.hp == 1 and stats.attack == 0


def test_a_retired_item_still_works_for_its_owner(content: Content) -> None:
    items = dict(content.items)
    items["oni_weapon_t3_common"] = replace(items["oni_weapon_t3_common"], retired=True)
    retired_content = replace(content, items=items)
    piece = instance(content, "oni_weapon_t3_common")
    assert gear.compute_loadout(retired_content, 24, [piece]).stats.attack > (
        stats_at_level(content.levels, 24).attack
    )


def test_typical_loadouts_grow_with_the_level(content: Content) -> None:
    previous = 0
    for level in (1, 12, 24, 38, 52):
        loadout = gear.typical_loadout(content, level)
        assert loadout.set_counts == {"oni": 5} and loadout.skills == ("rampage",)
        assert loadout.stats.attack > previous
        previous = loadout.stats.attack
    assert gear.typical_loadout(content, 1).stats.attack > stats_at_level(content.levels, 1).attack


# ----------------------------------------------------------------------------- drops, shop


def with_loot(content: Content, **changes: int) -> Content:
    loot = replace(content.balance.loot, **changes)
    return replace(content, balance=replace(content.balance, loot=loot))


def test_no_drop_chance_means_no_drops(content: Content) -> None:
    quiet = with_loot(content, drop_chance_percent=0)
    mission = quiet.missions["e_pest_control"]
    rng = random.Random(1)
    assert all(gear.roll_drop(quiet, mission, rng) is None for _ in range(100))


def test_a_certain_drop_matches_the_missions_level_band(content: Content) -> None:
    sure = with_loot(content, drop_chance_percent=100)
    low = sure.missions["e_pest_control"]  # top level 1
    high = sure.missions["b_hungry_giant"]  # boss, top level 24
    rng = random.Random(2)
    for _ in range(200):
        drop = gear.roll_drop(sure, low, rng)
        assert drop is not None and drop.req_level <= low.top_level
        drop = gear.roll_drop(sure, high, rng)
        assert drop is not None and high.top_level - 15 <= drop.req_level <= high.top_level


def test_bosses_drop_better_gear_more_often(content: Content) -> None:
    sure = with_loot(content, drop_chance_percent=100)
    boss, normal = sure.missions["b_hungry_giant"], sure.missions["c_ox_pasture"]
    assert gear.is_boss_mission(sure, boss) and not gear.is_boss_mission(sure, normal)
    rng = random.Random(4)
    rare_boss = sum(gear.roll_drop(sure, boss, rng).rarity in ("rare", "epic") for _ in range(400))  # type: ignore[union-attr]
    rare_normal = sum(
        gear.roll_drop(sure, normal, rng).rarity in ("rare", "epic") for _ in range(400)
    )  # type: ignore[union-attr]
    assert rare_boss > 2 * rare_normal


def test_retired_items_never_drop_and_the_drop_chance_is_respected(content: Content) -> None:
    items = {k: replace(v, retired=v.rarity == "epic") for k, v in content.items.items()}
    sure = replace(with_loot(content, drop_chance_percent=100), items=items)
    rng = random.Random(8)
    drops = [gear.roll_drop(sure, sure.missions["b_hungry_giant"], rng) for _ in range(300)]
    assert all(d is not None and d.rarity != "epic" for d in drops)

    half = with_loot(content, drop_chance_percent=50)
    rng = random.Random(8)
    got = sum(
        gear.roll_drop(half, half.missions["e_pest_control"], rng) is not None for _ in range(1000)
    )
    assert 400 <= got <= 600


def test_the_shop_is_stable_per_day_and_stocks_cheap_gear_for_every_tier(content: Content) -> None:
    today = gear.draw_shop(content, "2026-10-14")
    assert today == gear.draw_shop(content, "2026-10-14")
    assert today != gear.draw_shop(content, "2026-10-15")
    assert len(today) == 3 * len(content.item_tiers()) == len(set(today))
    stocked = [content.items[i] for i in today]
    assert {i.rarity for i in stocked} <= {"common", "uncommon"}
    assert sorted({i.req_level for i in stocked}) == content.item_tiers()


def test_retired_items_are_never_stocked(content: Content) -> None:
    items = {k: replace(v, retired=v.req_level == 1) for k, v in content.items.items()}
    shop = gear.draw_shop(replace(content, items=items), "2026-10-14")
    assert all(content.items[i].req_level != 1 for i in shop)
