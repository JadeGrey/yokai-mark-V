"""Party lobby state and member eligibility (pure logic) plus the raid content rules."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from yokai.errors import (
    AlreadyFightingError,
    AlreadyInPartyError,
    MissionRankError,
    NoAttemptsError,
    NoCharacterError,
    NotInPartyError,
    PartyFullError,
    PartyTooSmallError,
)
from yokai.rpg.content import ContentError, load_content
from yokai.rpg.models import Character
from yokai.rpg.party import Lobby, PartyMember, check_member, screen_members

DAY = "2026-10-14"


@pytest.fixture(scope="module")
def content():  # type: ignore[no-untyped-def]
    return load_content(Path("content/rpg"))


def character(user_id: int = 1, rank: str = "C", level: int = 14, used: int = 0) -> Character:
    return Character(
        user_id=user_id, rank=rank, level=level, xp=0, battles_won=0, battles_lost=0,
        created_ts=0, attempts_used=used, attempts_day=DAY if used else None,
        streak=0, last_active_day=None, promo_cooldown_until=None,
    )  # fmt: skip


def member(user_id: int, rank: str = "C", level: int = 14) -> PartyMember:
    return PartyMember(user_id, f"Member {user_id}", rank, level)


def rules(content):  # type: ignore[no-untyped-def]
    return {
        "today": DAY, "base_attempts": 8, "streak_rules": content.balance.streak,
        "resets_ts": 1_900_000_000, "fighting": set(),
    }  # fmt: skip


# ------------------------------------------------------------------------------- the lobby


def test_a_lobby_seats_up_to_four_and_keeps_join_order(content) -> None:  # type: ignore[no-untyped-def]
    lobby = Lobby(content.missions["raid_c_bandit_fortress"], leader_id=1)
    for user in (1, 2, 3, 4):
        lobby.add(member(user))
    assert list(lobby.members) == [1, 2, 3, 4] and lobby.is_full
    with pytest.raises(PartyFullError):
        lobby.add(member(5))
    assert lobby.size == 4


def test_joining_twice_and_leaving_without_a_seat_are_refused(content) -> None:  # type: ignore[no-untyped-def]
    lobby = Lobby(content.missions["raid_c_bandit_fortress"], leader_id=1)
    lobby.add(member(1))
    with pytest.raises(AlreadyInPartyError):
        lobby.add(member(1))
    with pytest.raises(NotInPartyError):
        lobby.remove(9)
    assert lobby.remove(1).user_id == 1 and lobby.size == 0


def test_a_raid_needs_two_members_a_regular_mission_needs_one(content) -> None:  # type: ignore[no-untyped-def]
    raid = Lobby(content.missions["raid_c_bandit_fortress"], leader_id=1)
    raid.add(member(1))
    with pytest.raises(PartyTooSmallError) as err:
        raid.check_ready()
    assert "at least 2" in err.value.message
    raid.add(member(2))
    raid.check_ready()
    solo = Lobby(content.missions["c_ox_pasture"], leader_id=1)
    solo.add(member(1))
    solo.check_ready()


# ----------------------------------------------------------------------------- eligibility


def test_a_member_is_checked_for_rank_attempts_and_fights(content) -> None:  # type: ignore[no-untyped-def]
    raid = content.missions["raid_b_sunken_temple"]  # rank B, cost 3
    with pytest.raises(MissionRankError):
        check_member(content, character(rank="C"), raid, **rules(content))
    check_member(content, character(rank="B", level=22), raid, **rules(content))
    with pytest.raises(NoAttemptsError) as err:
        check_member(content, character(rank="B", level=22, used=6), raid, **rules(content))
    assert "3" in err.value.message
    busy = {**rules(content), "fighting": {1}}
    with pytest.raises(AlreadyFightingError):
        check_member(content, character(rank="B", level=22), raid, **busy)


def test_a_higher_ranked_member_may_take_a_lower_ranked_mission(content) -> None:  # type: ignore[no-untyped-def]
    check_member(content, character(rank="A", level=31), content.missions["c_ox_pasture"],
                 **rules(content))  # fmt: skip


def test_screening_splits_eligible_from_rejected_with_reasons(content) -> None:  # type: ignore[no-untyped-def]
    raid = content.missions["raid_c_bandit_fortress"]  # rank C, cost 3
    characters = {
        1: character(1, "C"),
        2: character(2, "D", level=8),  # rank too low
        3: character(3, "C", used=7),  # 1 attempt left
        4: None,  # reset by an admin while waiting
        5: character(5, "B", level=21),
    }
    ok, rejected = screen_members(content, characters, raid, **{**rules(content), "fighting": {5}})
    assert [c.user_id for c in ok] == [1]
    assert isinstance(rejected[2], MissionRankError)
    assert isinstance(rejected[3], NoAttemptsError)
    assert isinstance(rejected[4], NoCharacterError)
    assert isinstance(rejected[5], AlreadyFightingError)


# ---------------------------------------------------------------------------- raid content


def test_every_raid_is_a_three_stage_party_mission_with_a_boss(content) -> None:  # type: ignore[no-untyped-def]
    raids = [m for m in content.missions.values() if m.kind == "raid"]
    assert {m.rank for m in raids} == {"C", "B", "A", "SS", "SS+"}
    assert all(sum(1 for m in raids if m.rank == r) == 2 for r in {m.rank for m in raids})
    for raid in raids:
        assert len(raid.stages) == 3 and raid.min_party == 2 and raid.attempt_cost in (3, 4)
        assert raid.attempt_cost > 2


def test_raids_drop_like_bosses(content) -> None:  # type: ignore[no-untyped-def]
    import random

    from yokai.rpg.gear import roll_drop

    raid = content.missions["raid_c_bandit_fortress"]
    plain = replace(raid, kind="mission")
    boss_chance = content.balance.loot.boss_drop_chance_percent
    rng = random.Random(4)
    drops = sum(roll_drop(content, raid, rng) is not None for _ in range(400))
    assert drops >= 400 * (boss_chance - 15) / 100
    plain_rng = random.Random(4)
    assert sum(roll_drop(content, plain, plain_rng) is not None for _ in range(400)) < drops


def test_a_raid_cannot_ask_for_a_party_of_one(tmp_path: Path) -> None:
    import shutil

    shutil.copytree("content/rpg", tmp_path / "c")
    path = tmp_path / "c" / "missions.toml"
    text = path.read_text(encoding="utf-8")
    path.write_text(
        text.replace(
            'kind = "raid"\nattempt_cost = 3', 'kind = "raid"\nmin_party = 1\nattempt_cost = 3', 1
        ),
        encoding="utf-8",
    )
    with pytest.raises(ContentError, match="min_party must be at least 2"):
        load_content(tmp_path / "c")
    text = text.replace(
        'kind = "raid"\nattempt_cost = 3', 'kind = "raid"\nmin_party = 9\nattempt_cost = 3', 1
    )
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ContentError, match="min_party"):
        load_content(tmp_path / "c")
