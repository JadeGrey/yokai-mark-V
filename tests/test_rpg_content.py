"""Tests for the RPG content loader and validators."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.rpg_helpers import (
    BALANCE,
    ENEMIES,
    LEVELS,
    MISSIONS,
    RANKS,
    SKILLS,
    TAGS,
    mutate,
    write_content_dir,
)
from yokai.rpg.content import Content, ContentError, load_content
from yokai.rpg.models import STAT_NAMES, Stats


def test_the_shipped_content_files_load_and_match_the_spec() -> None:
    content = load_content(Path("content/rpg"))
    assert content.levels.cap == 60
    assert [rank.id for rank in content.ranks] == ["E", "D", "C", "B", "A", "SS", "SS+"]
    assert content.ranks[0].min_level == 1
    assert list(content.tags) == [
        "oni",
        "kitsune",
        "tengu",
        "kappa",
        "yuki_onna",
        "tsukumogami",
        "jorogumo",
        "baku",
    ]
    for rank in content.ranks:
        assert len(content.missions_for_rank(rank.id)) >= 4, rank.id


def test_every_shipped_mission_uses_levels_inside_its_rank_band() -> None:
    content = load_content(Path("content/rpg"))
    for mission in content.missions.values():
        index = content.rank_index(mission.rank)
        low = content.ranks[index].min_level
        upper = content.ranks[index + 1].min_level - 1 if index + 1 < len(content.ranks) else 60
        for stage in mission.stages:
            assert low <= stage.level <= upper, f"{mission.id}: level {stage.level}"


def test_a_valid_folder_loads(tmp_path: Path) -> None:
    content = load_content(write_content_dir(tmp_path))
    assert isinstance(content, Content)
    assert content.levels.base == Stats(
        hp=100, attack=10, magic=10, defense=5, resist=5, speed=10, crit=5
    )
    assert content.tags["oni"].leans == ("attack", "hp")
    assert content.tags["kitsune"].name == "Kitsune"
    assert content.ranks[2].xp_share == 0.08
    assert content.balance.combat.defense_k == 40
    assert content.balance.xp.xp_to_next(10) == round(30 * 10**1.5)
    assert content.balance.party.hp_percent == (100, 170, 240, 310)
    assert content.skills["rend"].kind == "dot" and content.skills["hex"].stat == "attack"
    assert content.enemies["cat"].target == "lowest_hp"
    assert content.enemies["cat"].actions == (("attack", 2), ("rend", 1))
    assert content.missions["e_cats"].top_level == 3
    assert [m.id for m in content.missions_for_rank("E")] == ["e_pests", "e_cats"]


def test_collections_are_read_only(tmp_path: Path) -> None:
    content = load_content(write_content_dir(tmp_path))
    with pytest.raises(TypeError):
        content.tags["new"] = content.tags["oni"]  # type: ignore[index]
    with pytest.raises(TypeError):
        content.missions["new"] = content.missions["e_cats"]  # type: ignore[index]


def test_rank_lookups(tmp_path: Path) -> None:
    content = load_content(write_content_dir(tmp_path))
    assert content.rank_index("E") == 0
    assert content.rank_index("C") == 2
    assert content.rank("D").min_level == 5
    next_rank = content.next_rank("E")
    assert next_rank is not None and next_rank.id == "D" and next_rank.min_level == 5
    assert content.next_rank("C") is None
    for lookup in (content.rank_index, content.next_rank, content.rank):
        with pytest.raises(KeyError):
            lookup("Z")


@pytest.mark.parametrize(
    "name", ["levels", "ranks", "tags", "balance", "skills", "enemies", "missions"]
)
def test_a_missing_file_names_the_file(tmp_path: Path, name: str) -> None:
    write_content_dir(tmp_path)
    (tmp_path / f"{name}.toml").unlink()
    with pytest.raises(ContentError, match=rf"Missing content file.*{name}\.toml"):
        load_content(tmp_path)


def test_a_missing_folder_is_a_content_error(tmp_path: Path) -> None:
    with pytest.raises(ContentError, match="Missing content file"):
        load_content(tmp_path / "does-not-exist")


def test_invalid_toml_names_the_file(tmp_path: Path) -> None:
    write_content_dir(tmp_path, tags="[[tag]\nid = ")
    with pytest.raises(ContentError, match=r"tags\.toml is not valid TOML"):
        load_content(tmp_path)


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("cap = 60", 'cap = "sixty"', "cap must be a whole number"),
        ("cap = 60", "cap = 0", "cap must be at least 1"),
        ("cap = 60", "cap = true", "cap must be a whole number"),
        ("hp = 100", "hp = 0", r"\[base\].hp must be at least 1"),
        ("hp = 12", "hp = -1", r"\[growth\].hp must be at least 0"),
        ("crit = 5\n", "", r"\[base\] is missing stat\(s\): crit"),
        ("crit = 5\n", "crit = 5\nluck = 1\n", r"\[base\] has unknown stat\(s\): luck"),
    ],
)
def test_bad_levels_are_rejected(tmp_path: Path, old: str, new: str, message: str) -> None:
    with pytest.raises(ContentError, match=message):
        load_content(write_content_dir(tmp_path, levels=mutate(LEVELS, old, new)))


def test_levels_without_a_base_table_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ContentError, match=r"\[base\] must be a table"):
        load_content(write_content_dir(tmp_path, levels="cap = 60\n"))


@pytest.mark.parametrize(
    ("ranks", "message"),
    [
        ("", "needs at least one"),
        (mutate(RANKS, "min_level = 5", "min_level = 1"), "higher than the previous"),
        (mutate(RANKS, 'id = "D"', 'id = "E"'), "duplicate rank id"),
        (mutate(RANKS, "min_level = 1", "min_level = 2"), "first rank must start at level 1"),
        (mutate(RANKS, "min_level = 12", "min_level = 61"), "above the level cap"),
        (mutate(RANKS, 'id = "C"', 'id = ""'), "id must be non-empty text"),
        (mutate(RANKS, "xp_share = 0.08", "xp_share = 0"), "xp_share must be between"),
        (mutate(RANKS, "xp_share = 0.08\n", ""), "xp_share must be a number"),
    ],
)
def test_bad_ranks_are_rejected(tmp_path: Path, ranks: str, message: str) -> None:
    with pytest.raises(ContentError, match=message):
        load_content(write_content_dir(tmp_path, ranks=ranks))


@pytest.mark.parametrize(
    ("tags", "message"),
    [
        ("", "needs at least one"),
        (mutate(TAGS, 'id = "kitsune"', 'id = "oni"'), "duplicate tag id"),
        (mutate(TAGS, 'name = "Kitsune"', 'name = "ONI"'), "duplicate tag name"),
        (mutate(TAGS, '["attack", "hp"]', '["attack", "luck"]'), "unknown stat"),
        (mutate(TAGS, '["attack", "hp"]', "[]"), "1 to 3 stats"),
        (
            mutate(TAGS, '["attack", "hp"]', '["attack", "hp", "magic", "speed"]'),
            "1 to 3 stats",
        ),
        (mutate(TAGS, '["attack", "hp"]', '"attack"'), "must be a list"),
        (mutate(TAGS, 'role = "Bruiser"', 'role = ""'), "role must be non-empty text"),
    ],
)
def test_bad_tags_are_rejected(tmp_path: Path, tags: str, message: str) -> None:
    with pytest.raises(ContentError, match=message):
        load_content(write_content_dir(tmp_path, tags=tags))


def test_every_stat_name_is_accepted_as_a_lean(tmp_path: Path) -> None:
    entries = "\n".join(
        f'[[tag]]\nid = "t{i}"\nname = "T{i}"\nrole = "r"\nleans = ["{stat}"]\ndescription = "d"\n'
        for i, stat in enumerate(STAT_NAMES)
    )
    items = (
        '[[item]]\nid = "x"\nname = "X"\nslot = "weapon"\ntag = "t0"\nrarity = "common"\n'
        "req_level = 1\nprice = 5\nmax_durability = 100\nstats = { hp = 1 }\n"
    )
    content = load_content(write_content_dir(tmp_path, tags=entries, items=items))
    assert len(content.tags) == len(STAT_NAMES)


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("defense_k = 40", "defense_k = 0", "defense_k must be at least 1"),
        ("crit_cap = 75", "crit_cap = 101", "crit_cap must be at most 100"),
        ("defend_percent = 50", "defend_percent = 0", "defend_percent must be at least 1"),
        ("max_rounds = 40", "max_rounds = 0", "max_rounds must be at least 1"),
        ("exponent = 1.5", 'exponent = "x"', "exponent must be a number"),
        ("below_rank_step = 0.6", "below_rank_step = 1.5", "below_rank_step must be between"),
        ("[100, 170, 240, 310]", "[100, 170, 240]", "list of 4 whole numbers"),
        ("[100, 170, 240, 310]", "[100, 300, 240, 310]", "must not decrease"),
        ("[100, 170, 240, 310]", "[110, 170, 240, 310]", "must start at 100"),
        ("days_per_bonus = 3", "days_per_bonus = 0", "days_per_bonus must be at least 1"),
    ],
)
def test_bad_balance_is_rejected(tmp_path: Path, old: str, new: str, message: str) -> None:
    with pytest.raises(ContentError, match=message):
        load_content(write_content_dir(tmp_path, balance=mutate(BALANCE, old, new)))


def test_a_missing_balance_section_is_rejected(tmp_path: Path) -> None:
    broken = BALANCE.split("[party]")[0]
    with pytest.raises(ContentError, match=r"\[party\] must be a table"):
        load_content(write_content_dir(tmp_path, balance=broken))


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ('kind = "dot"', 'kind = "explode"', "kind must be one of"),
        ('target = "foe"', 'target = "nobody"', "target must be one of"),
        ('target = "foe"', 'target = "self"', r"\(dot\) must target foe or all_foes"),
        ("power = 40", "power = 0", "power must be at least 1"),
        ("duration = 3", "duration = 0", "duration must be at least 1"),
        ('scaling = "attack"', 'scaling = "luck"', "scaling must be"),
        ('id = "hex"', 'id = "rend"', "duplicate or reserved skill id"),
        ('id = "hex"', 'id = "attack"', "duplicate or reserved skill id"),
        ('stat = "attack"', 'stat = "hp"', "stat must be one of"),
        ("percent = 25", "percent = 0", "percent must be at least 1"),
    ],
)
def test_bad_skills_are_rejected(tmp_path: Path, old: str, new: str, message: str) -> None:
    with pytest.raises(ContentError, match=message):
        load_content(write_content_dir(tmp_path, skills=mutate(SKILLS, old, new)))


def test_a_heal_or_buff_must_target_allies(tmp_path: Path) -> None:
    heal = (
        SKILLS
        + '\n[[skill]]\nid = "mend"\nname = "Mend"\nkind = "heal"\ntarget = "foe"\npower = 50\n'
    )
    with pytest.raises(ContentError, match=r"\(heal\) must target ally"):
        load_content(write_content_dir(tmp_path, skills=heal))
    good = heal.replace('target = "foe"\npower = 50', 'target = "ally"\npower = 50')
    content = load_content(write_content_dir(tmp_path, skills=good))
    assert content.skills["mend"].scaling == "magic"


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ('id = "cat"', 'id = "gaki"', "duplicate enemy id"),
        ('do = "rend"', 'do = "unknown_move"', "unknown skill: unknown_move"),
        ('target = "lowest_hp"', 'target = "nearest"', "target must be one of"),
        ("weight = 1 }]\n\n[[enemy]]", "weight = 0 }]\n\n[[enemy]]", "weight must be at least 1"),
        ("hp = 70", "hp = 0", "mult.hp must be at least 1"),
        ('actions = [{ do = "attack", weight = 1 }]', "actions = []", "non-empty list"),
    ],
)
def test_bad_enemies_are_rejected(tmp_path: Path, old: str, new: str, message: str) -> None:
    with pytest.raises(ContentError, match=message):
        load_content(write_content_dir(tmp_path, enemies=mutate(ENEMIES, old, new)))


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ('id = "e_cats"', 'id = "e_pests"', "duplicate mission id"),
        ('rank = "D"', 'rank = "Q"', "is not in ranks.toml"),
        ("attempt_cost = 2", "attempt_cost = 0", "attempt_cost must be at least 1"),
        ('enemies = ["gaki", "gaki"]', 'enemies = ["ghost"]', "unknown enemy: ghost"),
        ('enemies = ["gaki", "gaki"]', "enemies = []", "1 to 5 enemies"),
        ("level = 6", "level = 61", "level must be at most 60"),
        ("level = 6", "level = 0", "level must be at least 1"),
        ('kind = "mission"', 'kind = "dungeon"', "kind must be one of"),
    ],
)
def test_bad_missions_are_rejected(tmp_path: Path, old: str, new: str, message: str) -> None:
    text = MISSIONS.replace("attempt_cost = 1", 'kind = "mission"\nattempt_cost = 1', 1)
    with pytest.raises(ContentError, match=message):
        load_content(write_content_dir(tmp_path, missions=mutate(text, old, new)))


def test_a_mission_with_no_stages_is_rejected(tmp_path: Path) -> None:
    text = mutate(MISSIONS, 'stages = [{ enemies = ["cat", "cat"], level = 6 }]', "stages = []")
    with pytest.raises(ContentError, match="stages must be a list of 1 to 5 stages"):
        load_content(write_content_dir(tmp_path, missions=text))


def test_enemy_power_defaults_to_one_hundred_and_is_validated(tmp_path: Path) -> None:
    content = load_content(write_content_dir(tmp_path))
    assert all(rank.enemy_power == 100 for rank in content.ranks)
    powered = mutate(RANKS, "xp_share = 0.1", "xp_share = 0.1\nenemy_power = 45")
    assert load_content(write_content_dir(tmp_path, ranks=powered)).ranks[0].enemy_power == 45
    for bad in ("enemy_power = 5", "enemy_power = 401", 'enemy_power = "hard"'):
        text = mutate(RANKS, "xp_share = 0.1", f"xp_share = 0.1\n{bad}")
        with pytest.raises(ContentError, match="enemy_power"):
            load_content(write_content_dir(tmp_path, ranks=text))


def test_the_shipped_enemy_power_is_a_sane_discount_on_player_equivalent_enemies() -> None:
    content = load_content(Path("content/rpg"))
    assert all(20 <= rank.enemy_power <= 120 for rank in content.ranks)


def test_mission_power_defaults_to_one_hundred_and_is_validated(tmp_path: Path) -> None:
    content = load_content(write_content_dir(tmp_path))
    assert all(m.power == 100 for m in content.missions.values())
    text = mutate(MISSIONS, "attempt_cost = 2", "attempt_cost = 2\npower = 140")
    assert load_content(write_content_dir(tmp_path, missions=text)).missions["d_cats"].power == 140
    for bad in ("power = 5", "power = 1001", 'power = "hard"'):
        broken = mutate(MISSIONS, "attempt_cost = 2", f"attempt_cost = 2\n{bad}")
        with pytest.raises(ContentError, match="power"):
            load_content(write_content_dir(tmp_path, missions=broken))


def test_every_shipped_mission_has_a_sane_calibrated_power() -> None:
    content = load_content(Path("content/rpg"))
    for mission in content.missions.values():
        assert 40 <= mission.power <= 250, mission.id
