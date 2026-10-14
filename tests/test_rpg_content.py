"""Tests for the RPG content loader and validators."""

from __future__ import annotations

from pathlib import Path

import pytest

from yokai.rpg.content import Content, ContentError, load_content
from yokai.rpg.models import STAT_NAMES, Stats

LEVELS = """\
cap = 60

[base]
hp = 100
attack = 10
magic = 10
defense = 5
resist = 5
speed = 10
crit = 5

[growth]
hp = 12
attack = 2
magic = 2
defense = 1
resist = 1
speed = 1
crit = 0
"""

RANKS = """\
[[rank]]
id = "E"
min_level = 1

[[rank]]
id = "D"
min_level = 5

[[rank]]
id = "C"
min_level = 12
"""

TAGS = """\
[[tag]]
id = "oni"
name = "Oni"
role = "Bruiser"
leans = ["attack", "hp"]
description = "Ogres of folklore."

[[tag]]
id = "kitsune"
name = "Kitsune"
role = "Trickster mage"
leans = ["magic", "speed"]
description = "Fox spirits."
"""


def write_dir(tmp_path: Path, levels: str = LEVELS, ranks: str = RANKS, tags: str = TAGS) -> Path:
    (tmp_path / "levels.toml").write_text(levels, encoding="utf-8")
    (tmp_path / "ranks.toml").write_text(ranks, encoding="utf-8")
    (tmp_path / "tags.toml").write_text(tags, encoding="utf-8")
    return tmp_path


def mutate(text: str, old: str, new: str) -> str:
    assert old in text, f"test bug: {old!r} not in the sample text"
    return text.replace(old, new, 1)


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


def test_a_valid_folder_loads(tmp_path: Path) -> None:
    content = load_content(write_dir(tmp_path))
    assert isinstance(content, Content)
    assert content.levels.base == Stats(
        hp=100, attack=10, magic=10, defense=5, resist=5, speed=10, crit=5
    )
    assert content.tags["oni"].leans == ("attack", "hp")
    assert content.tags["kitsune"].name == "Kitsune"


def test_tags_are_read_only(tmp_path: Path) -> None:
    content = load_content(write_dir(tmp_path))
    with pytest.raises(TypeError):
        content.tags["new"] = content.tags["oni"]  # type: ignore[index]


def test_rank_lookups(tmp_path: Path) -> None:
    content = load_content(write_dir(tmp_path))
    assert content.rank_index("E") == 0
    assert content.rank_index("C") == 2
    next_rank = content.next_rank("E")
    assert next_rank is not None and next_rank.id == "D" and next_rank.min_level == 5
    assert content.next_rank("C") is None
    with pytest.raises(KeyError):
        content.rank_index("Z")
    with pytest.raises(KeyError):
        content.next_rank("Z")


def test_a_missing_file_names_the_file(tmp_path: Path) -> None:
    write_dir(tmp_path)
    (tmp_path / "ranks.toml").unlink()
    with pytest.raises(ContentError, match=r"Missing content file.*ranks\.toml"):
        load_content(tmp_path)


def test_a_missing_folder_is_a_content_error(tmp_path: Path) -> None:
    with pytest.raises(ContentError, match="Missing content file"):
        load_content(tmp_path / "does-not-exist")


def test_invalid_toml_names_the_file(tmp_path: Path) -> None:
    write_dir(tmp_path, tags="[[tag]\nid = ")
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
    levels = mutate(LEVELS, old, new)
    with pytest.raises(ContentError, match=message):
        load_content(write_dir(tmp_path, levels=levels))


def test_levels_without_a_base_table_are_rejected(tmp_path: Path) -> None:
    levels = "cap = 60\n"
    with pytest.raises(ContentError, match=r"\[base\] must be a table"):
        load_content(write_dir(tmp_path, levels=levels))


@pytest.mark.parametrize(
    ("ranks", "message"),
    [
        ("", "needs at least one"),
        (mutate(RANKS, "min_level = 5", "min_level = 1"), "higher than the previous"),
        (mutate(RANKS, 'id = "D"', 'id = "E"'), "duplicate rank id"),
        (mutate(RANKS, "min_level = 1", "min_level = 2"), "first rank must start at level 1"),
        (mutate(RANKS, "min_level = 12", "min_level = 61"), "above the level cap"),
        (mutate(RANKS, 'id = "C"', 'id = ""'), "id must be non-empty text"),
    ],
)
def test_bad_ranks_are_rejected(tmp_path: Path, ranks: str, message: str) -> None:
    with pytest.raises(ContentError, match=message):
        load_content(write_dir(tmp_path, ranks=ranks))


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
        load_content(write_dir(tmp_path, tags=tags))


def test_every_stat_name_is_accepted_as_a_lean(tmp_path: Path) -> None:
    entries = "\n".join(
        f'[[tag]]\nid = "t{i}"\nname = "T{i}"\nrole = "r"\nleans = ["{stat}"]\ndescription = "d"\n'
        for i, stat in enumerate(STAT_NAMES)
    )
    content = load_content(write_dir(tmp_path, tags=entries))
    assert len(content.tags) == len(STAT_NAMES)
