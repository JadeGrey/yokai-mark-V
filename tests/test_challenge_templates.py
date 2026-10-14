"""The template pool, the slate picker and the listened rule."""

from __future__ import annotations

import itertools

from yokai.challenges.models import (
    CATEGORIES,
    COUNTER_METRICS,
    DISTINCT_METRICS,
    counts_as_listened,
)
from yokai.challenges.slate import pick_slate
from yokai.challenges.templates import POOL, TEMPLATES_BY_ID


def test_pool_ids_are_unique_and_indexed() -> None:
    assert len({t.id for t in POOL}) == len(POOL)
    assert set(TEMPLATES_BY_ID) == {t.id for t in POOL}


def test_every_period_and_category_has_a_template() -> None:
    for period, category in itertools.product(("daily", "weekly"), CATEGORIES):
        assert any(t.period == period and t.category == category for t in POOL)


def test_template_fields_are_sane() -> None:
    for t in POOL:
        assert t.target > 0 and t.reward > 0 and t.title
        assert t.metric in COUNTER_METRICS | DISTINCT_METRICS
        if t.game is not None:
            assert t.metric in {"rounds_played", "rounds_won"}


def test_every_possible_daily_slate_is_worth_about_one_daily() -> None:
    by_category = [
        [t.reward for t in POOL if t.period == "daily" and t.category == c] for c in CATEGORIES
    ]
    for combo in itertools.product(*by_category):
        assert 80 <= sum(combo) <= 140


def test_every_possible_weekly_slate_is_worth_three_or_four_dailies() -> None:
    by_category = [
        [t.reward for t in POOL if t.period == "weekly" and t.category == c] for c in CATEGORIES
    ]
    for combo in itertools.product(*by_category):
        assert 300 <= sum(combo) <= 400


def test_slate_is_deterministic_one_per_category_and_valid() -> None:
    first = pick_slate("daily", "2026-10-13", POOL)
    assert first == pick_slate("daily", "2026-10-13", POOL)
    assert len(first) == len(CATEGORIES)
    assert len(set(first)) == len(first)
    assert [TEMPLATES_BY_ID[i].category for i in first] == list(CATEGORIES)
    assert all(TEMPLATES_BY_ID[i].period == "daily" for i in first)


def test_slates_vary_across_days_and_with_a_salt() -> None:
    days = {tuple(pick_slate("daily", f"2026-10-{d:02d}", POOL)) for d in range(1, 29)}
    assert len(days) > 1
    salted = {tuple(pick_slate("daily", "2026-10-13", POOL, salt=str(n))) for n in range(30)}
    assert len(salted) > 1


def test_slate_skips_categories_with_no_template() -> None:
    only_music = tuple(t for t in POOL if t.category == "music")
    assert [TEMPLATES_BY_ID[i].category for i in pick_slate("daily", "k", only_music)] == ["music"]
    assert pick_slate("weekly", "k", ()) == []


def test_counts_as_listened() -> None:
    assert counts_as_listened("completed", 5, 200)
    assert counts_as_listened("skipped", 100, 200)
    assert not counts_as_listened("skipped", 99, 200)
    assert not counts_as_listened("skipped", 50, 0)  # unknown duration cannot prove 50%
    assert not counts_as_listened("stopped", 190, 200)
    assert not counts_as_listened("error", 190, 200)
