"""The pacing targets from the spec, checked by playing the real engine. See scripts/rpg_sim.py.

If one of these fails after a balance change, run `python scripts/rpg_sim.py` and retune
`enemy_power` and `xp_share` in content/rpg/ranks.toml (or the XP curve in balance.toml).
"""

from __future__ import annotations

import statistics
from pathlib import Path

import pytest

from yokai.rpg.content import Content, load_content
from yokai.rpg.sim import SimResult, simulate

SEEDS = (1, 2, 3)


@pytest.fixture(scope="module")
def content() -> Content:
    return load_content(Path("content/rpg"))


@pytest.fixture(scope="module")
def runs(content: Content) -> list[SimResult]:
    return [simulate(content, base_attempts=8, days=120, seed=seed) for seed in SEEDS]


def median_day(runs: list[SimResult], level: int) -> float:
    days = [r.day_reaching_level(level) for r in runs]
    assert all(day is not None for day in days), f"level {level} was not reached in time"
    return statistics.median(d for d in days if d is not None)


def test_a_daily_player_reaches_rank_a_in_about_a_month(runs: list[SimResult]) -> None:
    assert 26 <= median_day(runs, 30) <= 34


def test_a_daily_player_reaches_the_level_cap_in_about_three_months(runs: list[SimResult]) -> None:
    assert 84 <= median_day(runs, 60) <= 98


def test_the_first_promotion_comes_within_the_first_week(runs: list[SimResult]) -> None:
    assert median_day(runs, 5) <= 8


def test_levels_never_come_instantly(runs: list[SimResult]) -> None:
    """Not instantly grindable: nobody reaches level 12 on day one or level 30 in two weeks."""
    assert median_day(runs, 12) >= 8
    assert median_day(runs, 30) >= 20


def test_progress_is_possible_from_the_very_first_day(runs: list[SimResult]) -> None:
    for run in runs:
        first = run.records[0]
        assert first.wins >= 1, "a brand new character must be able to win something"
        assert run.win_rate(1, 5) >= 0.6


def test_the_game_is_not_impossible_or_trivial_overall(runs: list[SimResult]) -> None:
    for run in runs:
        assert 0.80 <= run.win_rate() <= 0.99


def test_every_rank_is_reached_in_order(content: Content, runs: list[SimResult]) -> None:
    for run in runs:
        days = [run.rank_day[rank.id] for rank in content.ranks]
        assert days == sorted(days)


def test_renown_income_stays_modest_while_the_shop_does_not_exist_yet(
    content: Content, runs: list[SimResult]
) -> None:
    """A guard against runaway inflation: phase 3 prices are set against these numbers."""
    first_month = runs[0].renown_per_day(1, 30)
    assert 50 <= first_month <= 600
    assert runs[0].renown_per_day(91, 120) <= 1500


def test_casual_players_progress_more_slowly(content: Content) -> None:
    casual = simulate(content, base_attempts=3, days=120, seed=1)
    daily = simulate(content, base_attempts=8, days=120, seed=1)
    assert (casual.day_reaching_level(30) or 999) > (daily.day_reaching_level(30) or 999)
