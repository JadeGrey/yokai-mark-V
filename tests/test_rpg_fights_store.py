"""Tests for the RPG boards, attempts, fights and settlement in the store."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from yokai.errors import NoAttemptsError, NoCharacterError
from yokai.rpg.guild import Reward
from yokai.rpg.models import StreakRules, XpCurve
from yokai.rpg.store import RpgStore
from yokai.storage.db import CURRENT_SCHEMA_VERSION, Database

RULES = StreakRules(days_per_bonus=3, bonus_cap=3, drop_per_missed_day=3)
CURVE = XpCurve(base=30, exponent=1.5)
DAY = "2026-10-14"
WELCOME = 200


async def fresh(tmp_path: Path) -> tuple[Database, RpgStore]:
    db = Database(tmp_path / "fights.db")
    await db.initialize()
    return db, RpgStore(db)


async def start(
    store: RpgStore, users: list[int], *, cost: int = 1, base: int = 8, day: str = DAY
) -> int:
    return await store.begin_fight(
        mission_id="e_pest_control",
        kind="mission",
        user_ids=users,
        cost=cost,
        today=day,
        base_attempts=base,
        streak_rules=RULES,
    )


async def settle(store: RpgStore, fight_id: int, *, won: bool, rewards: dict[int, Reward]):  # type: ignore[no-untyped-def]
    return await store.settle_fight(
        fight_id, won=won, rewards=rewards, level_cap=60, xp_curve=CURVE, welcome_bonus=WELCOME
    )


async def balance(db: Database, user_id: int) -> int:
    wallet = await db.get_wallet(user_id)
    return wallet.balance if wallet else 0


@pytest.mark.asyncio
async def test_v10_adds_the_columns_and_tables_and_replays_safely(tmp_path: Path) -> None:
    db, store = await fresh(tmp_path)
    try:
        assert CURRENT_SCHEMA_VERSION >= 10
        async with db.conn.execute("PRAGMA table_info(rpg_characters);") as cursor:
            columns = {row[1] for row in await cursor.fetchall()}
        assert {"attempts_used", "attempts_day", "streak", "last_active_day"} <= columns
        for table in ("rpg_boards", "rpg_fights"):
            async with db.conn.execute(f"PRAGMA table_info({table});") as cursor:
                assert await cursor.fetchall(), table
        await store.create_character(1, "E", now=5)
        await db.conn.execute("PRAGMA user_version = 9;")  # simulate a crash mid-upgrade
        await db.conn.commit()
    finally:
        await db.close()

    db = Database(tmp_path / "fights.db")
    await db.initialize()
    try:
        character = await RpgStore(db).get_character(1)
        assert character is not None and character.created_ts == 5 and character.streak == 0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_the_board_is_drawn_once_per_day(tmp_path: Path) -> None:
    db, store = await fresh(tmp_path)
    try:
        calls: list[int] = []

        def build_first() -> list[str]:
            calls.append(1)
            return ["a", "b"]

        assert await store.get_or_create_board(DAY, build_first) == ["a", "b"]
        assert await store.get_or_create_board(DAY, lambda: ["different"]) == ["a", "b"]
        assert await store.get_or_create_board("2026-10-15", lambda: ["c"]) == ["c"]
        assert len(calls) == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_begin_fight_spends_attempts_opens_a_fight_and_starts_the_streak(
    tmp_path: Path,
) -> None:
    db, store = await fresh(tmp_path)
    try:
        await store.create_character(1, "E", now=1)
        fight_id = await start(store, [1], cost=2)
        character = await store.get_character(1)
        assert character is not None
        assert (character.attempts_used, character.attempts_day, character.streak) == (2, DAY, 1)
        assert character.last_active_day == DAY
        async with db.conn.execute(
            "SELECT status, participants, mission_id FROM rpg_fights WHERE id = ?;", (fight_id,)
        ) as cursor:
            row = await cursor.fetchone()
        assert row is not None and row[0] == "open" and row[2] == "e_pest_control"
        assert json.loads(row[1]) == {"1": {"cost": 2, "day": DAY}}
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_begin_fight_without_enough_attempts_changes_nothing(tmp_path: Path) -> None:
    db, store = await fresh(tmp_path)
    try:
        await store.create_character(1, "E", now=1)
        with pytest.raises(NoAttemptsError):
            await start(store, [1], cost=3, base=2)
        character = await store.get_character(1)
        assert character is not None and character.attempts_used == 0 and character.streak == 0
        async with db.conn.execute("SELECT COUNT(*) FROM rpg_fights;") as cursor:
            row = await cursor.fetchone()
        assert row is not None and row[0] == 0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_begin_fight_for_a_missing_character_is_a_typed_error(tmp_path: Path) -> None:
    db, store = await fresh(tmp_path)
    try:
        with pytest.raises(NoCharacterError):
            await start(store, [404])
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_party_start_is_all_or_nothing(tmp_path: Path) -> None:
    db, store = await fresh(tmp_path)
    try:
        await store.create_character(1, "E", now=1)
        await store.create_character(2, "E", now=1)
        await start(store, [2], cost=1, base=1)  # user 2 has no attempts left
        with pytest.raises(NoAttemptsError):
            await start(store, [1, 2], cost=1, base=1)
        first = await store.get_character(1)
        assert first is not None and first.attempts_used == 0 and first.streak == 0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_two_simultaneous_starts_with_one_attempt_left_open_exactly_one_fight(
    tmp_path: Path,
) -> None:
    """Review Focus 1: no double-spend of the last attempt."""
    db, store = await fresh(tmp_path)
    try:
        await store.create_character(1, "E", now=1)
        results = await asyncio.gather(
            *[start(store, [1], cost=1, base=1) for _ in range(6)], return_exceptions=True
        )
        assert sum(isinstance(r, int) for r in results) == 1
        assert sum(isinstance(r, NoAttemptsError) for r in results) == 5
        character = await store.get_character(1)
        assert character is not None and character.attempts_used == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_attempts_reset_on_a_new_day_and_the_streak_continues(tmp_path: Path) -> None:
    db, store = await fresh(tmp_path)
    try:
        await store.create_character(1, "E", now=1)
        await start(store, [1], cost=8, base=8, day="2026-10-14")
        await start(store, [1], cost=1, base=8, day="2026-10-15")
        character = await store.get_character(1)
        assert character is not None
        assert (character.attempts_used, character.attempts_day, character.streak) == (
            1,
            "2026-10-15",
            2,
        )
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_win_pays_xp_renown_and_counts_the_battle(tmp_path: Path) -> None:
    db, store = await fresh(tmp_path)
    try:
        await store.create_character(1, "E", now=1)
        fight_id = await start(store, [1])
        result = await settle(store, fight_id, won=True, rewards={1: Reward(xp=10, renown=40)})
        assert result is not None
        assert result[1].xp_gained == 10 and result[1].renown_gained == 40
        assert result[1].levels_gained == 0
        character = await store.get_character(1)
        assert character is not None
        assert (character.level, character.xp, character.battles_won) == (1, 10, 1)
        assert await balance(db, 1) == WELCOME + 40
        async with db.conn.execute(
            "SELECT reason, delta FROM renown_transactions WHERE user_id = 1 AND reason = ?;",
            ("rpg_reward",),
        ) as cursor:
            assert await cursor.fetchall() == [("rpg_reward", 40)]
        async with db.conn.execute(
            "SELECT status FROM rpg_fights WHERE id = ?;", (fight_id,)
        ) as cursor:
            row = await cursor.fetchone()
        assert row is not None and row[0] == "won"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_win_that_crosses_a_threshold_levels_up(tmp_path: Path) -> None:
    db, store = await fresh(tmp_path)
    try:
        await store.create_character(1, "E", now=1)
        fight_id = await start(store, [1])
        need = CURVE.xp_to_next(1)
        result = await settle(store, fight_id, won=True, rewards={1: Reward(need + 3, 0)})
        assert result is not None and result[1].levels_gained == 1 and result[1].xp_now == 3
        character = await store.get_character(1)
        assert character is not None and (character.level, character.xp) == (2, 3)
        assert await balance(db, 1) == 0  # zero Renown reward does not even create a wallet
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_loss_pays_nothing_and_counts_the_defeat(tmp_path: Path) -> None:
    db, store = await fresh(tmp_path)
    try:
        await store.create_character(1, "E", now=1)
        fight_id = await start(store, [1])
        result = await settle(store, fight_id, won=False, rewards={1: Reward(999, 999)})
        assert result is not None and result[1].xp_gained == 0 and result[1].renown_gained == 0
        character = await store.get_character(1)
        assert character is not None
        assert (character.xp, character.battles_won, character.battles_lost) == (0, 0, 1)
        assert await balance(db, 1) == 0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_settling_twice_pays_once(tmp_path: Path) -> None:
    """Review Focus 2: a duplicate settlement (a retry, a stray task) is a no-op."""
    db, store = await fresh(tmp_path)
    try:
        await store.create_character(1, "E", now=1)
        fight_id = await start(store, [1])
        rewards = {1: Reward(xp=10, renown=40)}
        results = await asyncio.gather(
            settle(store, fight_id, won=True, rewards=rewards),
            settle(store, fight_id, won=True, rewards=rewards),
            settle(store, fight_id, won=False, rewards=rewards),
        )
        assert sum(1 for r in results if r is not None) == 1
        character = await store.get_character(1)
        assert character is not None
        assert character.battles_won + character.battles_lost == 1
        assert await balance(db, 1) in (0, WELCOME + 40)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_party_settlement_pays_every_member_their_own_reward(tmp_path: Path) -> None:
    db, store = await fresh(tmp_path)
    try:
        for user in (1, 2):
            await store.create_character(user, "E", now=1)
        fight_id = await start(store, [1, 2])
        result = await settle(
            store, fight_id, won=True, rewards={1: Reward(5, 10), 2: Reward(7, 20)}
        )
        assert result is not None and set(result) == {1, 2}
        assert await balance(db, 1) == WELCOME + 10 and await balance(db, 2) == WELCOME + 20
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_the_level_cap_is_respected_when_settling(tmp_path: Path) -> None:
    db, store = await fresh(tmp_path)
    try:
        await store.create_character(1, "E", now=1)
        async with db.transaction() as conn:
            await conn.execute("UPDATE rpg_characters SET level = 59 WHERE user_id = 1;")
        fight_id = await start(store, [1])
        result = await settle(store, fight_id, won=True, rewards={1: Reward(10**9, 0)})
        assert result is not None and result[1].level_after == 60 and result[1].xp_now == 0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_aborting_refunds_same_day_attempts_and_blocks_settlement(tmp_path: Path) -> None:
    db, store = await fresh(tmp_path)
    try:
        await store.create_character(1, "E", now=1)
        fight_id = await start(store, [1], cost=2)
        assert await store.abort_fight(fight_id) is True
        assert await store.abort_fight(fight_id) is False
        character = await store.get_character(1)
        assert character is not None and character.attempts_used == 0
        assert await settle(store, fight_id, won=True, rewards={1: Reward(10, 10)}) is None
        assert await balance(db, 1) == 0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_startup_recovery_aborts_open_fights_and_only_refunds_the_same_day(
    tmp_path: Path,
) -> None:
    """Review Focus 3."""
    db, store = await fresh(tmp_path)
    try:
        for user in (1, 2):
            await store.create_character(user, "E", now=1)
        await start(store, [1], cost=2, day="2026-10-14")
        await start(store, [2], cost=2, day="2026-10-14")
        # User 2 played again on the next day before the restart was noticed.
        async with db.transaction() as conn:
            await conn.execute(
                "UPDATE rpg_characters SET attempts_day = '2026-10-15', attempts_used = 3 "
                "WHERE user_id = 2;"
            )
        done = await start(store, [1], cost=1, day="2026-10-14")
        await settle(store, done, won=True, rewards={1: Reward(1, 1)})

        assert await store.abort_open_fights() == 2
        one, two = await store.get_character(1), await store.get_character(2)
        assert one is not None and two is not None
        assert one.attempts_used == 1  # the two aborted attempts refunded, the settled one kept
        assert two.attempts_used == 3  # a different day's counter is left alone
        assert await store.abort_open_fights() == 0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_set_fight_thread_records_the_thread(tmp_path: Path) -> None:
    db, store = await fresh(tmp_path)
    try:
        await store.create_character(1, "E", now=1)
        fight_id = await start(store, [1])
        await store.set_fight_thread(fight_id, 777)
        async with db.conn.execute(
            "SELECT thread_id FROM rpg_fights WHERE id = ?;", (fight_id,)
        ) as cursor:
            row = await cursor.fetchone()
        assert row is not None and row[0] == 777
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_purge_removes_old_boards_and_finished_fights_but_never_open_ones(
    tmp_path: Path,
) -> None:
    db, store = await fresh(tmp_path)
    try:
        await store.create_character(1, "E", now=1)
        await store.get_or_create_board("2020-01-01", lambda: ["a"], now=10)
        await store.get_or_create_board("2099-01-01", lambda: ["b"], now=10**10)
        old_open = await store.begin_fight(
            mission_id="m", kind="mission", user_ids=[1], cost=1, today=DAY,
            base_attempts=8, streak_rules=RULES, now=10,
        )  # fmt: skip
        old_done = await store.begin_fight(
            mission_id="m", kind="mission", user_ids=[1], cost=1, today=DAY,
            base_attempts=8, streak_rules=RULES, now=10,
        )  # fmt: skip
        await settle(store, old_done, won=False, rewards={})
        boards, fights = await store.purge_history(1000)
        assert (boards, fights) == (1, 1)
        async with db.conn.execute("SELECT id FROM rpg_fights;") as cursor:
            assert [row[0] for row in await cursor.fetchall()] == [old_open]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_credit_within_transaction_rolls_back_with_the_transaction(tmp_path: Path) -> None:
    db, _ = await fresh(tmp_path)
    try:
        with pytest.raises(RuntimeError):
            async with db.transaction():
                await db.credit_within_transaction(5, 50, "test", WELCOME)
                raise RuntimeError("boom")
        assert await db.get_wallet(5) is None
        async with db.transaction():
            await db.credit_within_transaction(5, 50, "test", WELCOME)
        assert await balance(db, 5) == WELCOME + 50
        with pytest.raises(ValueError):
            async with db.transaction():
                await db.credit_within_transaction(5, 0, "test", WELCOME)
    finally:
        await db.close()
