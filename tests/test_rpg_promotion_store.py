"""Tests for promotion fights in the store, the rank logic and the leaderboard query."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import Optional

import pytest

from yokai.errors import (
    AlreadyFightingError,
    NoAttemptsError,
    PromotionCooldownError,
    PromotionNotReadyError,
    PromotionStaleError,
)
from yokai.rpg import guild
from yokai.rpg.guild import Reward
from yokai.rpg.models import Character
from yokai.rpg.runtime import RpgRuntime
from yokai.rpg.store import PromotionRequest
from yokai.storage.db import CURRENT_SCHEMA_VERSION, Database

USER = 7
DAY = "2026-10-14"
WELCOME = 200
TO_D = PromotionRequest("E", "D", 5)
NOW = 1_700_000_000
SIX_HOURS = 6 * 3600


async def fresh(tmp_path: Path, *, level: int = 5, rank: str = "E") -> tuple[Database, RpgRuntime]:
    db = Database(tmp_path / "promo.db")
    await db.initialize()
    runtime = RpgRuntime.create(db, Path("content/rpg"))
    await runtime.store.create_character(USER, rank, now=1)
    await set_level(db, USER, level)
    return db, runtime


async def set_level(db: Database, user_id: int, level: int) -> None:
    async with db.transaction() as conn:
        await conn.execute(
            "UPDATE rpg_characters SET level = ? WHERE user_id = ?;", (level, user_id)
        )


async def begin(
    runtime: RpgRuntime,
    request: Optional[PromotionRequest] = TO_D,
    *,
    now: int = NOW,
    base: int = 8,
    user: int = USER,
) -> int:
    return await runtime.store.begin_fight(
        mission_id="promotion_D",
        kind="promotion",
        user_ids=[user],
        cost=2,
        today=DAY,
        base_attempts=base,
        streak_rules=runtime.content.balance.streak,
        now=now,
        promotion=request,
    )


async def settle(
    runtime: RpgRuntime, fight_id: int, *, won: bool, now: int = NOW, renown: int = 30
):  # type: ignore[no-untyped-def]
    return await runtime.store.settle_fight(
        fight_id,
        won=won,
        rewards={USER: Reward(0, renown)},
        level_cap=60,
        xp_curve=runtime.content.balance.xp,
        welcome_bonus=WELCOME,
        now=now,
        promotion_cooldown_s=SIX_HOURS,
    )


async def character(runtime: RpgRuntime) -> Character:
    found = await runtime.store.get_character(USER)
    assert found is not None
    return found


# ------------------------------------------------------------------------------ schema


@pytest.mark.asyncio
async def test_v12_adds_the_cooldown_column_and_replays_safely(tmp_path: Path) -> None:
    db, runtime = await fresh(tmp_path)
    try:
        assert CURRENT_SCHEMA_VERSION >= 12
        assert (await character(runtime)).promo_cooldown_until is None
        await db.conn.execute("PRAGMA user_version = 11;")
        await db.conn.commit()
    finally:
        await db.close()
    db = Database(tmp_path / "promo.db")
    await db.initialize()
    try:
        found = await RpgRuntime.create(db, Path("content/rpg")).store.get_character(USER)
        assert found is not None and found.promo_cooldown_until is None
    finally:
        await db.close()


# ------------------------------------------------------------------------- starting one


@pytest.mark.asyncio
async def test_a_promotion_fight_spends_attempts_and_remembers_the_target(tmp_path: Path) -> None:
    db, runtime = await fresh(tmp_path)
    try:
        fight_id = await begin(runtime)
        assert (await character(runtime)).attempts_used == 2
        async with db.conn.execute(
            "SELECT kind, participants FROM rpg_fights WHERE id = ?;", (fight_id,)
        ) as cursor:
            row = await cursor.fetchone()
        assert row is not None and row[0] == "promotion"
        assert json.loads(row[1]) == {str(USER): {"cost": 2, "day": DAY, "promote_to": "D"}}
    finally:
        await db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("setup", "error"),
    [
        ("low_level", PromotionNotReadyError),
        ("wrong_rank", PromotionStaleError),
        ("cooldown", PromotionCooldownError),
        ("no_attempts", NoAttemptsError),
    ],
)
async def test_an_ineligible_promotion_changes_nothing(
    tmp_path: Path, setup: str, error: type[Exception]
) -> None:
    """Review Focus 1: no skipping ranks or levels, and nothing is spent on a refusal."""
    db, runtime = await fresh(tmp_path)
    try:
        base = 8
        if setup == "low_level":
            await set_level(db, USER, 4)
        elif setup == "wrong_rank":
            async with db.transaction() as conn:
                await conn.execute(
                    "UPDATE rpg_characters SET rank = 'D' WHERE user_id = ?;", (USER,)
                )
        elif setup == "cooldown":
            async with db.transaction() as conn:
                await conn.execute(
                    "UPDATE rpg_characters SET promo_cooldown_until = ? WHERE user_id = ?;",
                    (NOW + 100, USER),
                )
        elif setup == "no_attempts":
            base = 1
        with pytest.raises(error):
            await begin(runtime, base=base)
        assert (await character(runtime)).attempts_used == 0
        async with db.conn.execute("SELECT COUNT(*) FROM rpg_fights;") as cursor:
            row = await cursor.fetchone()
        assert row is not None and row[0] == 0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_the_cooldown_reports_when_it_ends_and_expires(tmp_path: Path) -> None:
    db, runtime = await fresh(tmp_path)
    try:
        async with db.transaction() as conn:
            await conn.execute(
                "UPDATE rpg_characters SET promo_cooldown_until = ? WHERE user_id = ?;",
                (NOW + 500, USER),
            )
        with pytest.raises(PromotionCooldownError) as raised:
            await begin(runtime, now=NOW)
        assert raised.value.until_ts == NOW + 500
        await begin(runtime, now=NOW + 500)  # the moment it ends, a new attempt is allowed
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_two_simultaneous_challenges_open_exactly_one_fight(tmp_path: Path) -> None:
    """Review Focus 2."""
    db, runtime = await fresh(tmp_path)
    try:
        results = await asyncio.gather(*[begin(runtime) for _ in range(5)], return_exceptions=True)
        assert sum(isinstance(r, int) for r in results) == 1
        assert sum(isinstance(r, AlreadyFightingError) for r in results) == 4
        assert (await character(runtime)).attempts_used == 2
    finally:
        await db.close()


# ------------------------------------------------------------------------------ settling


@pytest.mark.asyncio
async def test_winning_promotes_pays_renown_and_clears_the_cooldown(tmp_path: Path) -> None:
    db, runtime = await fresh(tmp_path)
    try:
        async with db.transaction() as conn:
            await conn.execute(
                "UPDATE rpg_characters SET promo_cooldown_until = ? WHERE user_id = ?;",
                (NOW - 10, USER),  # an old, finished cooldown
            )
        result = await settle(runtime, await begin(runtime), won=True, renown=30)
        assert result is not None
        assert result[USER].promoted_to == "D" and result[USER].xp_gained == 0
        found = await character(runtime)
        assert (found.rank, found.promo_cooldown_until, found.battles_won) == ("D", None, 1)
        assert found.level == 5 and found.xp == 0
        wallet = await db.get_wallet(USER)
        assert wallet is not None and wallet.balance == WELCOME + 30
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_losing_starts_the_cooldown_and_keeps_the_rank(tmp_path: Path) -> None:
    db, runtime = await fresh(tmp_path)
    try:
        result = await settle(runtime, await begin(runtime), won=False)
        assert result is not None
        assert result[USER].promoted_to is None and result[USER].cooldown_until == NOW + SIX_HOURS
        found = await character(runtime)
        assert (found.rank, found.promo_cooldown_until, found.battles_lost) == (
            "E",
            NOW + SIX_HOURS,
            1,
        )
        with pytest.raises(PromotionCooldownError):
            await begin(runtime, now=NOW + SIX_HOURS - 1)
        await begin(runtime, now=NOW + SIX_HOURS)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_zero_hour_cooldown_means_no_waiting(tmp_path: Path) -> None:
    db, runtime = await fresh(tmp_path)
    try:
        fight_id = await begin(runtime)
        result = await runtime.store.settle_fight(
            fight_id, won=False, rewards={}, level_cap=60, xp_curve=runtime.content.balance.xp,
            welcome_bonus=WELCOME, now=NOW, promotion_cooldown_s=0,
        )  # fmt: skip
        assert result is not None and result[USER].cooldown_until is None
        assert (await character(runtime)).promo_cooldown_until is None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_normal_mission_never_changes_rank_or_cooldown(tmp_path: Path) -> None:
    db, runtime = await fresh(tmp_path)
    try:
        fight_id = await begin(runtime, request=None)
        await settle(runtime, fight_id, won=False)
        found = await character(runtime)
        assert found.rank == "E" and found.promo_cooldown_until is None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_settling_a_promotion_twice_promotes_once(tmp_path: Path) -> None:
    db, runtime = await fresh(tmp_path)
    try:
        fight_id = await begin(runtime)
        results = await asyncio.gather(*[settle(runtime, fight_id, won=True) for _ in range(4)])
        assert sum(r is not None for r in results) == 1
        found = await character(runtime)
        assert found.rank == "D" and found.battles_won == 1
        wallet = await db.get_wallet(USER)
        assert wallet is not None and wallet.balance == WELCOME + 30
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_aborting_a_promotion_refunds_attempts_without_a_cooldown(tmp_path: Path) -> None:
    """Review Focus 4: a restart mid-trial costs nothing."""
    db, runtime = await fresh(tmp_path)
    try:
        await begin(runtime)
        assert await runtime.store.abort_open_fights() == 1
        found = await character(runtime)
        assert (found.attempts_used, found.promo_cooldown_until, found.rank) == (0, None, "E")
        await begin(runtime)  # and a new attempt is possible straight away
    finally:
        await db.close()


# ------------------------------------------------------------------------- rank logic


def make_character(**changes: object) -> Character:
    base = Character(1, 5, 0, "E", 0, 0, 0)
    return replace(base, **changes)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_promotion_status_covers_every_situation(tmp_path: Path) -> None:
    db, runtime = await fresh(tmp_path)
    try:
        content = runtime.content
        ready = guild.promotion_status(content, make_character(level=5), NOW)
        assert ready.ready and ready.next_rank is not None and ready.next_rank.id == "D"

        low = guild.promotion_status(content, make_character(level=4), NOW)
        assert not low.ready and not low.level_ok and low.cooldown_until is None

        waiting = guild.promotion_status(
            content, make_character(level=9, promo_cooldown_until=NOW + 60), NOW
        )
        assert waiting.level_ok and not waiting.ready and waiting.cooldown_until == NOW + 60

        expired = guild.promotion_status(
            content, make_character(level=9, promo_cooldown_until=NOW - 1), NOW
        )
        assert expired.ready and expired.cooldown_until is None

        top = guild.promotion_status(content, make_character(rank="SS+", level=60), NOW)
        assert top.top and not top.ready and top.next_rank is None and top.known_rank

        lost = guild.promotion_status(content, make_character(rank="Retired"), NOW)
        assert not lost.known_rank and not lost.top and not lost.ready
    finally:
        await db.close()


# ------------------------------------------------------------------------- leaderboard


async def board(
    runtime: RpgRuntime, sort: str, *, limit: int = 20, offset: int = 0
) -> tuple[list[int], int]:
    order = [rank.id for rank in runtime.content.ranks]
    characters, total = await runtime.store.top_characters(sort, order, limit, offset)
    return [c.user_id for c in characters], total


async def add(
    runtime: RpgRuntime, db: Database, user: int, *, rank: str, level: int, wins: int, joined: int
) -> None:
    await runtime.store.create_character(user, rank, now=joined)
    async with db.transaction() as conn:
        await conn.execute(
            "UPDATE rpg_characters SET level = ?, battles_won = ? WHERE user_id = ?;",
            (level, wins, user),
        )


@pytest.mark.asyncio
async def test_the_leaderboard_sorts_by_the_rank_ladder_not_the_alphabet(tmp_path: Path) -> None:
    """Review Focus 6."""
    db, runtime = await fresh(tmp_path)
    try:
        await runtime.store.admin_reset_character(1, USER)
        await add(runtime, db, 1, rank="E", level=10, wins=50, joined=1)
        await add(runtime, db, 2, rank="SS", level=47, wins=5, joined=2)
        await add(runtime, db, 3, rank="A", level=33, wins=20, joined=3)
        await add(runtime, db, 4, rank="SS+", level=60, wins=1, joined=4)
        await add(runtime, db, 5, rank="D", level=30, wins=90, joined=5)
        ids, total = await board(runtime, "rank")
        assert (ids, total) == ([4, 2, 3, 5, 1], 5)  # SS+, SS, A, D, E
        assert (await board(runtime, "level"))[0] == [4, 2, 3, 5, 1]
        assert (await board(runtime, "wins"))[0] == [5, 1, 3, 2, 4]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_ties_break_by_the_next_sort_key_then_by_who_joined_first(tmp_path: Path) -> None:
    db, runtime = await fresh(tmp_path)
    try:
        await runtime.store.admin_reset_character(1, USER)
        await add(runtime, db, 10, rank="C", level=15, wins=3, joined=30)
        await add(runtime, db, 11, rank="C", level=18, wins=3, joined=20)
        await add(runtime, db, 12, rank="C", level=18, wins=9, joined=10)
        await add(runtime, db, 13, rank="C", level=18, wins=9, joined=5)
        assert (await board(runtime, "rank"))[0] == [13, 12, 11, 10]
        assert (await board(runtime, "level"))[0] == [13, 12, 11, 10]
        assert (await board(runtime, "wins"))[0] == [13, 12, 11, 10]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_leaderboard_paging_totals_and_edge_cases(tmp_path: Path) -> None:
    db, runtime = await fresh(tmp_path)
    try:
        await runtime.store.admin_reset_character(1, USER)
        assert await board(runtime, "rank") == ([], 0)
        for n in range(1, 8):
            await add(runtime, db, n, rank="E", level=n, wins=0, joined=n)
        page_one, total = await board(runtime, "level", limit=3, offset=0)
        page_two, _ = await board(runtime, "level", limit=3, offset=3)
        page_three, _ = await board(runtime, "level", limit=3, offset=6)
        assert (page_one, page_two, page_three, total) == ([7, 6, 5], [4, 3, 2], [1], 7)
        assert (await board(runtime, "level", limit=3, offset=99))[0] == []
        with pytest.raises(ValueError):
            await board(runtime, "renown")
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_rank_missing_from_the_content_sorts_last(tmp_path: Path) -> None:
    db, runtime = await fresh(tmp_path)
    try:
        await runtime.store.admin_reset_character(1, USER)
        await add(runtime, db, 1, rank="E", level=1, wins=0, joined=1)
        await add(runtime, db, 2, rank="Retired", level=50, wins=0, joined=2)
        assert (await board(runtime, "rank"))[0] == [1, 2]
        assert (await board(runtime, "level"))[0] == [2, 1]
    finally:
        await db.close()
