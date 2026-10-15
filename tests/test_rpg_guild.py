"""Tests for the pure guild rules: days, streaks, attempts, XP, rewards and the board."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from yokai.errors import NoAttemptsError
from yokai.rpg import guild
from yokai.rpg.content import Content, load_content
from yokai.rpg.models import Character, StreakRules, XpCurve

RULES = StreakRules(days_per_bonus=3, bonus_cap=3, drop_per_missed_day=3)
CURVE = XpCurve(base=30, exponent=1.5)


@pytest.fixture(scope="module")
def content() -> Content:
    return load_content(Path("content/rpg"))


def character(**overrides: object) -> Character:
    base = Character(
        user_id=1,
        level=1,
        xp=0,
        rank="E",
        battles_won=0,
        battles_lost=0,
        created_ts=0,
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


# ----------------------------------------------------------------------------------- days


def test_day_key_follows_the_configured_zone() -> None:
    late = int(datetime(2026, 10, 14, 23, 30, tzinfo=timezone.utc).timestamp())
    assert guild.day_key(late, timezone.utc) == "2026-10-14"
    assert guild.day_key(late, timezone(timedelta(hours=2))) == "2026-10-15"


def test_next_day_is_the_following_midnight() -> None:
    noon = int(datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc).timestamp())
    expected = int(datetime(2026, 10, 15, 0, 0, tzinfo=timezone.utc).timestamp())
    assert guild.next_day_ts(noon, timezone.utc) == expected


def test_days_between() -> None:
    assert guild.days_between("2026-10-14", "2026-10-14") == 0
    assert guild.days_between("2026-10-14", "2026-10-15") == 1
    assert guild.days_between("2026-12-30", "2027-01-02") == 3
    assert guild.days_between("2026-10-15", "2026-10-14") == -1


# ------------------------------------------------------------------------------- streaks


@pytest.mark.parametrize(
    ("streak", "last", "expected"),
    [
        (5, None, 0),
        (0, "2026-10-13", 0),
        (5, "2026-10-14", 5),  # already active today
        (5, "2026-10-13", 5),  # yesterday: no missed day
        (5, "2026-10-12", 2),  # one missed day drops 3
        (5, "2026-10-11", 0),  # two missed days drop 6, floored at 0
        (20, "2026-10-09", 8),  # four missed days drop 12
        (-4, "2026-10-13", 0),
    ],
)
def test_rollover_streak_drops_three_per_missed_day_but_never_below_zero(
    streak: int, last: str | None, expected: int
) -> None:
    assert guild.rollover_streak(streak, last, "2026-10-14", RULES) == expected


@pytest.mark.parametrize(
    ("streak", "bonus"), [(0, 0), (2, 0), (3, 1), (5, 1), (6, 2), (9, 3), (100, 3), (-5, 0)]
)
def test_streak_bonus_is_capped(streak: int, bonus: int) -> None:
    assert guild.streak_bonus(streak, RULES) == bonus


# ------------------------------------------------------------------------------ attempts


def test_a_new_character_has_the_base_attempts() -> None:
    status = guild.attempt_status(character(), "2026-10-14", 8, RULES)
    assert (status.allowance, status.used, status.remaining, status.streak) == (8, 0, 8, 0)


def test_used_attempts_reset_when_the_day_changes() -> None:
    tired = character(
        attempts_used=8, attempts_day="2026-10-13", streak=1, last_active_day="2026-10-13"
    )
    assert guild.attempt_status(tired, "2026-10-13", 8, RULES).remaining == 0
    assert guild.attempt_status(tired, "2026-10-14", 8, RULES).remaining == 8


def test_a_streak_adds_attempts_at_the_start_of_the_day() -> None:
    keen = character(
        streak=7, last_active_day="2026-10-13", attempts_used=3, attempts_day="2026-10-13"
    )
    status = guild.attempt_status(keen, "2026-10-14", 8, RULES)
    assert (status.bonus, status.allowance, status.used) == (2, 10, 0)


def test_missing_a_day_costs_streak_attempts() -> None:
    keen = character(streak=7, last_active_day="2026-10-12")
    status = guild.attempt_status(keen, "2026-10-14", 8, RULES)
    assert (status.streak, status.bonus) == (4, 1)


def test_the_first_spend_of_a_day_extends_the_streak_once() -> None:
    fresh = character()
    first = guild.plan_spend(fresh, 1, "2026-10-14", 8, RULES)
    assert (first.used, first.streak, first.last_active_day) == (1, 1, "2026-10-14")
    after_first = replace(
        fresh, attempts_used=first.used, attempts_day=first.day, streak=first.streak,
        last_active_day=first.last_active_day,
    )  # fmt: skip
    second = guild.plan_spend(after_first, 2, "2026-10-14", 8, RULES)
    assert (second.used, second.streak) == (3, 1)


def test_a_consecutive_day_continues_the_streak_and_a_gap_drops_then_extends() -> None:
    base = character(
        streak=5, last_active_day="2026-10-13", attempts_used=4, attempts_day="2026-10-13"
    )
    assert guild.plan_spend(base, 1, "2026-10-14", 8, RULES).streak == 6
    gapped = replace(base, last_active_day="2026-10-12")
    assert guild.plan_spend(gapped, 1, "2026-10-14", 8, RULES).streak == 3


def test_spending_exactly_the_remaining_attempts_is_allowed_and_one_more_is_not() -> None:
    nearly = character(attempts_used=6, attempts_day="2026-10-14", last_active_day="2026-10-14")
    ok = guild.plan_spend(nearly, 2, "2026-10-14", 8, RULES)
    assert ok.used == 8
    with pytest.raises(NoAttemptsError) as raised:
        guild.plan_spend(nearly, 3, "2026-10-14", 8, RULES, resets_ts=1_900_000_000)
    assert raised.value.remaining == 2 and raised.value.cost == 3
    assert "<t:1900000000:R>" in (raised.value.user_hint or "")


def test_a_streak_bonus_can_pay_for_a_mission_the_base_cannot() -> None:
    keen = character(streak=9, last_active_day="2026-10-13", attempts_used=0)
    assert guild.plan_spend(keen, 11, "2026-10-14", 8, RULES).used == 11
    with pytest.raises(NoAttemptsError):
        guild.plan_spend(keen, 12, "2026-10-14", 8, RULES)


# --------------------------------------------------------------------------------- XP


def test_apply_xp_below_the_threshold_just_adds() -> None:
    assert guild.apply_xp(1, 0, 10, 60, CURVE) == (1, 10, 0)


def test_apply_xp_levels_up_and_carries_the_remainder() -> None:
    need = CURVE.xp_to_next(1)
    assert guild.apply_xp(1, 0, need + 5, 60, CURVE) == (2, 5, 1)


def test_apply_xp_can_gain_several_levels_at_once() -> None:
    need = CURVE.xp_to_next(1) + CURVE.xp_to_next(2) + CURVE.xp_to_next(3)
    assert guild.apply_xp(1, 0, need + 1, 60, CURVE) == (4, 1, 3)


def test_apply_xp_stops_at_the_cap_and_discards_the_rest() -> None:
    assert guild.apply_xp(59, 0, 10**9, 60, CURVE) == (60, 0, 1)


def test_a_capped_character_gains_nothing() -> None:
    assert guild.apply_xp(60, 0, 1000, 60, CURVE) == (60, 0, 0)
    assert guild.apply_xp(61, 5, 1000, 60, CURVE) == (60, 0, 0)


def test_negative_xp_is_ignored() -> None:
    assert guild.apply_xp(3, 7, -50, 60, CURVE) == (3, 7, 0)


# -------------------------------------------------------------------- eligibility, rewards


def test_members_can_enter_their_rank_and_below(content: Content) -> None:
    e_mission = content.missions["e_pest_control"]
    d_mission = content.missions["d_alley_cats"]
    assert guild.can_enter(content, "E", e_mission)
    assert guild.can_enter(content, "D", e_mission)
    assert not guild.can_enter(content, "E", d_mission)
    assert guild.can_enter(content, "Q", e_mission)  # an unknown rank counts as the lowest
    assert not guild.can_enter(content, "Q", d_mission)


def test_a_party_is_limited_by_its_lowest_ranked_member(content: Content) -> None:
    d_mission = content.missions["d_alley_cats"]
    assert guild.party_can_enter(content, ["D", "B", "SS+"], d_mission)
    assert not guild.party_can_enter(content, ["D", "E", "SS+"], d_mission)


def test_a_mission_at_your_rank_pays_the_formula(content: Content) -> None:
    mission = content.missions["e_pest_control"]  # top level 1, cost 1, rank E share 0.1
    reward = guild.mission_reward(content, mission, "E")
    assert reward.xp == max(1, round(CURVE.xp_to_next(1) * 0.1))
    assert reward.renown == content.balance.rewards.renown_per_level * 1 * 1


def test_attempt_cost_scales_the_reward(content: Content) -> None:
    mission = content.missions["b_hungry_giant"]  # cost 2
    cheap = replace(mission, attempt_cost=1)
    full = guild.mission_reward(content, mission, "B")
    single = guild.mission_reward(content, cheap, "B")
    assert abs(full.xp - 2 * single.xp) <= 1 and full.renown == 2 * single.renown


def test_missions_below_your_rank_pay_less_with_a_floor(content: Content) -> None:
    mission = content.missions["e_midnight_gate"]
    at_rank = guild.mission_reward(content, mission, "E")
    one_up = guild.mission_reward(content, mission, "D")
    far_up = guild.mission_reward(content, mission, "SS+")
    assert one_up.renown == round(at_rank.renown * 0.6)
    assert far_up.renown == round(at_rank.renown * 0.1)  # 0.6^6 is below the 0.1 floor
    assert far_up.xp >= 1


def test_rewards_for_an_unknown_player_rank_use_the_mission_rank(content: Content) -> None:
    mission = content.missions["d_alley_cats"]
    assert guild.mission_reward(content, mission, "Q") == guild.mission_reward(
        content, mission, "E"
    )


# ---------------------------------------------------------------------------------- board


def test_the_board_is_stable_for_a_day_and_changes_between_days(content: Content) -> None:
    assert guild.draw_board(content, "2026-10-14") == guild.draw_board(content, "2026-10-14")
    boards = {tuple(guild.draw_board(content, f"2026-10-{d:02d}")) for d in range(1, 15)}
    assert len(boards) > 1


def test_the_board_has_three_regular_missions_per_rank_and_a_raid_from_c_up(
    content: Content,
) -> None:
    ids = guild.draw_board(content, "2026-10-14")
    missions = guild.board_missions(content, ids)
    for rank in content.ranks:
        in_rank = [m for m in missions if m.rank == rank.id]
        regular = [m for m in in_rank if m.kind == "mission"]
        raids = [m for m in in_rank if m.kind == "raid"]
        assert len(regular) == 3
        assert len(raids) == (0 if content.rank_index(rank.id) < content.rank_index("C") else 1)
        assert in_rank == regular + raids  # regular missions first, then the raid
    assert len(set(ids)) == len(ids) == 26


def test_the_board_can_leave_raids_out(content: Content) -> None:
    ids = guild.draw_board(content, "2026-10-14", raids_per_rank=0)
    assert len(ids) == 21 and all(content.missions[i].kind == "mission" for i in ids)


def test_the_board_is_ordered_by_rank_then_file_order(content: Content) -> None:
    ids = guild.draw_board(content, "2026-10-14")
    ranks = [content.missions[i].rank for i in ids]
    assert ranks == sorted(ranks, key=content.rank_index)


def test_a_larger_per_rank_than_the_pool_returns_the_whole_pool(content: Content) -> None:
    ids = guild.draw_board(content, "2026-10-14", per_rank=99, raids_per_rank=99)
    assert len(ids) == len(content.missions)


def test_board_missions_skips_ids_that_content_no_longer_has(content: Content) -> None:
    missions = guild.board_missions(content, ["e_pest_control", "retired_mission"])
    assert [m.id for m in missions] == ["e_pest_control"]
