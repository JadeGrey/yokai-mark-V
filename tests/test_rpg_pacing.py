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
        assert 0.80 <= run.win_rate() <= 0.995


def test_each_promotion_is_a_real_step_up_in_difficulty(
    content: Content, runs: list[SimResult]
) -> None:
    """Right after a promotion fights are risky but winnable; the player then outlevels them.

    A few days is only a handful of fights per seed, so this averages the seeds.
    """
    for rank in content.ranks[2:]:  # E and D are gentle by design (new members have no gear)
        rates = [run.win_rate_after_promotion(rank.id) for run in runs]
        mean = sum(rates) / len(rates)
        assert 0.55 <= mean <= 0.97, f"rank {rank.id}: {mean:.0%} ({rates})"


def test_missions_within_a_rank_are_comparably_hard(content: Content) -> None:
    """Per-mission power evens out content: no mission is a wall or a walkover at its rank."""
    import random

    from yokai.rpg.gear import typical_loadout
    from yokai.rpg.sim import fight_once
    from yokai.rpg.stats import stats_at_level

    entry = {"C": (13, 1), "B": (21, 12), "A": (31, 24), "SS": (46, 38), "SS+": (60, 52)}
    rng = random.Random(5)
    for rank_id, (level, tier) in entry.items():
        stats = typical_loadout(content, level, rarity="uncommon", gear_level=tier).stats
        for mission in content.missions_for_rank(rank_id):
            wins = sum(fight_once(content, mission, stats, rng)[0] for _ in range(60))
            assert 0.55 <= wins / 60 <= 1.0, f"{mission.id}: {wins / 60:.0%}"
    for rank_id, level in (("E", 2), ("D", 6)):
        stats = stats_at_level(content.levels, level)
        for mission in content.missions_for_rank(rank_id):
            wins = sum(fight_once(content, mission, stats, rng)[0] for _ in range(60))
            assert 0.55 <= wins / 60 <= 1.0, f"{mission.id}: {wins / 60:.0%}"


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


def test_gear_is_worth_having(content: Content) -> None:
    """A member who never buys gear fights at a clear disadvantage."""
    geared = simulate(content, base_attempts=8, days=60, seed=1, geared=True)
    bare = simulate(content, base_attempts=8, days=60, seed=1, geared=False)
    assert bare.win_rate() < geared.win_rate() - 0.05
    assert (bare.day_reaching_level(30) or 999) > (geared.day_reaching_level(30) or 999)


def test_gear_costs_renown_but_leaves_income_positive(runs: list[SimResult]) -> None:
    """Purchases and repairs are real sinks, and never swallow the whole income."""
    run = runs[0]
    for first, last in ((1, 30), (31, 60), (61, 90)):
        spent = sum(r.spent for r in run.records if first <= r.day <= last) / (last - first + 1)
        earned = run.renown_per_day(first, last)
        assert spent > 0 and 0 < run.net_per_day(first, last) < earned
        # The first month is an investment: a new member buys a set for every gear tier.
        assert spent < (0.85 if first == 1 else 0.7) * earned


def test_rank_a_arrives_through_its_trial_in_about_a_month(
    content: Content, runs: list[SimResult]
) -> None:
    days = sorted(run.rank_day["A"] for run in runs)
    assert 26 <= days[len(days) // 2] <= 36


def test_every_promotion_trial_is_won_within_a_few_tries(
    content: Content, runs: list[SimResult]
) -> None:
    """Trials are tuned to about 70% at the threshold level; a daily player passes within days."""
    for run in runs:
        for rank in content.ranks[1:]:
            assert rank.id in run.rank_day, f"rank {rank.id} was never reached"
            tries = run.promotion_attempts.get(rank.id, 0)
            assert 1 <= tries <= 4, f"rank {rank.id} took {tries} tries"
    total = sum(sum(run.promotion_attempts.values()) for run in runs)
    needed = len(runs) * (len(content.ranks) - 1)
    assert needed / total >= 0.5  # overall at least half of all trial fights are won


def test_a_failed_trial_costs_a_day_not_the_whole_run(runs: list[SimResult]) -> None:
    for run in runs:
        lost = sum(r.promotions_lost for r in run.records)
        won = sum(r.promotions_won for r in run.records)
        assert won == 6 and lost <= 6
