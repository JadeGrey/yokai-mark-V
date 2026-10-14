"""Tests for the RPG stat model and level math."""

from __future__ import annotations

import pytest

from yokai.rpg.models import STAT_LABELS, STAT_NAMES, LevelTable, Stats
from yokai.rpg.stats import format_stats, stats_at_level

TABLE = LevelTable(
    cap=60,
    base=Stats(hp=100, attack=10, magic=10, defense=5, resist=5, speed=10, crit=5),
    growth=Stats(hp=12, attack=2, magic=2, defense=1, resist=1, speed=1, crit=0),
)


def test_stat_names_and_labels_cover_the_same_seven_stats() -> None:
    assert STAT_NAMES == ("hp", "attack", "magic", "defense", "resist", "speed", "crit")
    assert set(STAT_LABELS) == set(STAT_NAMES)


def test_stats_add_and_scale_every_field() -> None:
    a = Stats(hp=1, attack=2, magic=3, defense=4, resist=5, speed=6, crit=7)
    b = Stats(hp=10, attack=20, magic=30, defense=40, resist=50, speed=60, crit=70)
    assert a + b == Stats(hp=11, attack=22, magic=33, defense=44, resist=55, speed=66, crit=77)
    assert a.scaled(3) == Stats(hp=3, attack=6, magic=9, defense=12, resist=15, speed=18, crit=21)


def test_level_one_is_the_base_stats() -> None:
    assert stats_at_level(TABLE, 1) == TABLE.base


def test_each_level_adds_the_growth_once() -> None:
    assert stats_at_level(TABLE, 5) == Stats(
        hp=148, attack=18, magic=18, defense=9, resist=9, speed=14, crit=5
    )


@pytest.mark.parametrize("bad_level", [0, -3])
def test_a_level_below_one_is_treated_as_level_one(bad_level: int) -> None:
    assert stats_at_level(TABLE, bad_level) == TABLE.base


def test_a_level_above_the_cap_is_clamped_to_the_cap() -> None:
    assert stats_at_level(TABLE, 9999) == stats_at_level(TABLE, 60)


def test_format_stats_returns_labelled_values_with_crit_as_a_percentage() -> None:
    pairs = format_stats(Stats(hp=1234, attack=10, magic=11, defense=5, resist=6, speed=7, crit=5))
    assert pairs == [
        ("HP", "1,234"),
        ("Attack", "10"),
        ("Magic", "11"),
        ("Defense", "5"),
        ("Resist", "6"),
        ("Speed", "7"),
        ("Crit", "5%"),
    ]
