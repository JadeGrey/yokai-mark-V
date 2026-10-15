"""Tests for the gear content: loaders, validators and the generated items file."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.rpg_helpers import BALANCE, ITEMS, TAGS, mutate, write_content_dir
from yokai.rpg import itemgen
from yokai.rpg.content import Content, ContentError, load_content
from yokai.rpg.models import RARITIES, SLOTS, STAT_NAMES


@pytest.fixture(scope="module")
def content() -> Content:
    return load_content(Path("content/rpg"))


# --------------------------------------------------------------------------- generated file


def test_the_committed_items_file_matches_the_generator(content: Content) -> None:
    """Review Focus 6: edit the generator, not the file."""
    text = itemgen.generate_items_toml(content.tags, content.levels.growth)
    committed = Path("content/rpg/items.toml").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert committed == text


def test_there_is_one_item_for_every_tag_slot_tier_and_rarity(content: Content) -> None:
    assert len(content.items) == len(content.tags) * len(SLOTS) * len(itemgen.TIERS) * len(RARITIES)
    combos = {(i.tag, i.slot, i.req_level, i.rarity) for i in content.items.values()}
    assert len(combos) == len(content.items)
    assert content.item_tiers() == list(itemgen.TIERS)


def test_item_stats_follow_the_tag_leans_and_drawback(content: Content) -> None:
    for item in content.items.values():
        tag = content.tags[item.tag]
        positive = {s for s in STAT_NAMES if getattr(item.stats, s) > 0}
        negative = {s for s in STAT_NAMES if getattr(item.stats, s) < 0}
        assert positive, item.id
        assert positive <= set(tag.leans), item.id
        assert negative <= {tag.drawback}, item.id


def test_power_and_price_rise_with_rarity_and_tier(content: Content) -> None:
    def power(item_id: str) -> int:
        stats = content.items[item_id].stats
        return sum(max(0, getattr(stats, s)) for s in STAT_NAMES)

    for tag in content.tags:
        for slot in SLOTS:
            for tier in range(len(itemgen.TIERS)):
                chain = [content.items[itemgen.item_id(tag, slot, tier, r)] for r in RARITIES]
                assert [i.price for i in chain] == sorted(i.price for i in chain)
                assert [i.max_durability for i in chain] == sorted(i.max_durability for i in chain)
                assert power(chain[0].id) <= power(chain[-1].id)
            first = content.items[itemgen.item_id(tag, slot, 0, "common")]
            last = content.items[itemgen.item_id(tag, slot, 4, "common")]
            assert power(first.id) < power(last.id) and first.price < last.price


def test_every_tag_has_bonuses_a_skill_and_a_drawback(content: Content) -> None:
    for tag in content.tags.values():
        assert any(getattr(tag.bonus_2, s) for s in STAT_NAMES), tag.id
        assert any(getattr(tag.bonus_4, s) for s in STAT_NAMES), tag.id
        assert tag.skill in content.skills, tag.id
        assert tag.drawback and tag.drawback not in tag.leans, tag.id


def test_only_epic_weapons_carry_an_extra_skill(content: Content) -> None:
    with_skill = [i for i in content.items.values() if i.skill]
    assert with_skill and all(i.rarity == "epic" and i.slot == "weapon" for i in with_skill)
    assert all(i.skill in content.skills for i in with_skill)


def test_item_names_do_not_repeat_within_a_tag_and_slot(content: Content) -> None:
    seen: dict[tuple[str, str], set[str]] = {}
    for item in content.items.values():
        names = seen.setdefault((item.tag, item.slot), set())
        assert item.name not in names, item.name
        names.add(item.name)


def test_the_generated_item_numbers_are_sane(content: Content) -> None:
    cheapest = min(i.price for i in content.items.values())
    assert 10 <= cheapest <= 100
    for item in content.items.values():
        assert 1 <= item.req_level <= content.levels.cap
        assert item.price >= 1 and 50 <= item.max_durability <= 200


# --------------------------------------------------------------------------- the sample content


def test_the_sample_items_and_tag_bonuses_load(tmp_path: Path) -> None:
    content = load_content(write_content_dir(tmp_path))
    oni = content.tags["oni"]
    assert oni.bonus_2.attack == 8 and oni.bonus_4.hp == 10 and oni.skill == "rend"
    assert oni.drawback == "speed"
    assert content.tags["kitsune"].skill is None and content.tags["kitsune"].bonus_2.attack == 0
    weapon = content.items["oni_weapon_t1_common"]
    assert weapon.stats.speed == -1 and weapon.stats.attack == 1 and not weapon.retired
    assert content.items["oni_weapon_t2_epic"].skill == "hex"
    assert content.balance.gear.win_wear == 3 and content.balance.gear.inventory_cap == 60
    assert dict(content.balance.loot.rarity_weights)["epic"] == 2
    assert content.balance.shop.stock_per_tier == 3


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ('id = "oni_head_t1_common"', 'id = "oni_weapon_t1_common"', "duplicate item id"),
        ('slot = "head"', 'slot = "cape"', "slot must be one of"),
        ('tag = "oni"', 'tag = "ghost"', "is not in tags.toml"),
        ('rarity = "common"', 'rarity = "mythic"', "rarity must be one of"),
        ("req_level = 12", "req_level = 61", "req_level must be at most 60"),
        ("price = 60", "price = 0", "price must be at least 1"),
        ("max_durability = 100", "max_durability = 0", "max_durability must be at least 1"),
        ("stats = { hp = 1, attack = 1 }", "stats = {}", "must change at least one stat"),
        ("stats = { hp = 1, attack = 1 }", "stats = { luck = 1 }", "unknown stat"),
        ("stats = { hp = 1, attack = 1 }", 'stats = { hp = "lots" }', "must be a whole number"),
        ('skill = "hex"', 'skill = "nothing"', "is not in skills.toml"),
    ],
)
def test_bad_items_are_rejected(tmp_path: Path, old: str, new: str, message: str) -> None:
    with pytest.raises(ContentError, match=message):
        load_content(write_content_dir(tmp_path, items=mutate(ITEMS, old, new)))


def test_retired_must_be_a_boolean_and_is_read(tmp_path: Path) -> None:
    text = mutate(ITEMS, 'skill = "hex"', 'skill = "hex"\nretired = true')
    assert load_content(write_content_dir(tmp_path, items=text)).items["oni_weapon_t2_epic"].retired
    bad = mutate(ITEMS, 'skill = "hex"', 'skill = "hex"\nretired = "yes"')
    with pytest.raises(ContentError, match="retired must be true or false"):
        load_content(write_content_dir(tmp_path, items=bad))


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("bonus_2 = { attack = 8 }", "bonus_2 = { luck = 8 }", "unknown stat"),
        ("bonus_2 = { attack = 8 }", "bonus_2 = { attack = 101 }", "must be at most 100"),
        ("bonus_4 = { attack = 12, hp = 10 }", "bonus_4 = { attack = -1 }", "must be at least 0"),
        ('skill = "rend"', 'skill = "unknown"', "is not in skills.toml"),
        ('drawback = "speed"', 'drawback = "luck"', "drawback must be one of"),
    ],
)
def test_bad_tag_bonuses_are_rejected(tmp_path: Path, old: str, new: str, message: str) -> None:
    with pytest.raises(ContentError, match=message):
        load_content(write_content_dir(tmp_path, tags=mutate(TAGS, old, new)))


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("sell_percent = 30", "sell_percent = 101", "sell_percent must be at most 100"),
        ("inventory_cap = 60", "inventory_cap = 0", "inventory_cap must be at least 1"),
        ("roll_percent = 10", "roll_percent = 80", "roll_percent must be at most 50"),
        ("drop_chance_percent = 35", "drop_chance_percent = 150", "must be at most 100"),
        (
            "{ common = 60, uncommon = 28, rare = 10, epic = 2 }",
            "{ legendary = 5 }",
            "unknown rarity",
        ),
        ("{ common = 60, uncommon = 28, rare = 10, epic = 2 }", "{ common = 0 }", "above 0"),
        ("stock_per_tier = 3", "stock_per_tier = 0", "stock_per_tier must be at least 1"),
    ],
)
def test_bad_gear_balance_is_rejected(tmp_path: Path, old: str, new: str, message: str) -> None:
    with pytest.raises(ContentError, match=message):
        load_content(write_content_dir(tmp_path, balance=mutate(BALANCE, old, new)))


@pytest.mark.parametrize("section", ["gear", "loot", "shop"])
def test_missing_gear_balance_sections_are_rejected(tmp_path: Path, section: str) -> None:
    start = BALANCE.index(f"[{section}]")
    others = [BALANCE.index(f"[{s}]") for s in ("gear", "loot", "shop") if s != section]
    end = min((o for o in others if o > start), default=len(BALANCE))
    broken = BALANCE[:start] + BALANCE[end:]
    with pytest.raises(ContentError, match=rf"\[{section}\] must be a table"):
        load_content(write_content_dir(tmp_path, balance=broken))


def test_a_missing_items_file_names_the_file(tmp_path: Path) -> None:
    write_content_dir(tmp_path)
    (tmp_path / "items.toml").unlink()
    with pytest.raises(ContentError, match=r"Missing content file.*items\.toml"):
        load_content(tmp_path)
