"""Trade sessions (pure) and their atomic commit in the store."""

from __future__ import annotations

import asyncio
import json
import random
from pathlib import Path
from typing import Any, Optional

import pytest

from yokai.errors import (
    NoCharacterError,
    TradeClosedError,
    TradeFightingError,
    TradeFundsError,
    TradeItemsGoneError,
    TradeLimitError,
    TradeNothingError,
    TradeRenownError,
    TradeRoomError,
)
from yokai.rpg.gear_store import ItemDrop
from yokai.rpg.models import ItemInstance
from yokai.rpg.runtime import RpgRuntime
from yokai.rpg.trade import MAX_ITEMS_PER_SIDE, TradeSession
from yokai.storage.db import CURRENT_SCHEMA_VERSION, Database

A, B = 5, 6
WELCOME = 0
ITEM = "oni_weapon_t1_common"


# ------------------------------------------------------------------------------ the session


def test_a_session_needs_two_different_members() -> None:
    with pytest.raises(ValueError):
        TradeSession(1, 1)
    session = TradeSession(1, 2)
    assert session.members == (1, 2) and session.other(1) == 2 and session.other(2) == 1
    assert session.has_member(2) and not session.has_member(3)


def test_offers_start_empty_and_empty_trades_cannot_be_confirmed() -> None:
    session = TradeSession(1, 2)
    assert session.is_empty and not session.is_gift
    with pytest.raises(TradeNothingError):
        session.confirm(1)


def test_adding_the_same_item_twice_or_in_both_offers_is_ignored() -> None:
    session = TradeSession(1, 2)
    assert session.add_item(1, 10) is True
    assert session.add_item(1, 10) is False
    assert session.add_item(2, 10) is False
    assert session.offers[1].items == [10] and session.offers[2].items == []


def test_each_side_holds_a_limited_number_of_items() -> None:
    session = TradeSession(1, 2)
    for item in range(MAX_ITEMS_PER_SIDE):
        session.add_item(1, item)
    with pytest.raises(TradeLimitError):
        session.add_item(1, 99)
    assert session.add_item(2, 99) is True  # the limit is per side


def test_any_change_clears_both_confirmations_but_a_no_op_does_not() -> None:
    session = TradeSession(1, 2)
    session.add_item(1, 10)
    assert session.confirm(1) is False and session.confirm(2) is True
    assert session.set_renown(1, 0) is False and session.both_confirmed
    session.set_renown(2, 50)
    assert session.confirmed == set()
    session.confirm(1)
    session.confirm(2)
    assert session.remove_item(1, 404) is False and session.both_confirmed
    session.remove_item(1, 10)
    assert session.confirmed == set()
    session.confirm(2)
    session.drop_items((1234,))
    assert session.confirmed == set()


def test_a_gift_is_a_trade_where_one_side_offers_nothing() -> None:
    session = TradeSession(1, 2)
    session.add_item(1, 10)
    assert session.is_gift
    session.set_renown(2, 5)
    assert not session.is_gift


@pytest.mark.parametrize("amount", [-1, 10**12, 1.5, "10", True])
def test_renown_must_be_a_sane_whole_number(amount: Any) -> None:
    with pytest.raises(TradeRenownError):
        TradeSession(1, 2).set_renown(1, amount)


def test_drop_items_removes_from_whichever_side_offered_them() -> None:
    session = TradeSession(1, 2)
    session.add_item(1, 10)
    session.add_item(2, 11)
    session.drop_items({10, 11})
    assert session.is_empty


def test_a_closed_session_refuses_every_edit() -> None:
    session = TradeSession(1, 2)
    session.close()
    for call in (
        lambda: session.add_item(1, 1),
        lambda: session.remove_item(1, 1),
        lambda: session.set_renown(1, 1),
        lambda: session.confirm(1),
    ):
        with pytest.raises(TradeClosedError):
            call()


def test_a_stranger_cannot_edit() -> None:
    with pytest.raises(ValueError):
        TradeSession(1, 2).add_item(3, 1)


# ------------------------------------------------------------------------------ the store


async def world(tmp_path: Path, cash_a: int = 1000, cash_b: int = 1000):  # type: ignore[no-untyped-def]
    db = Database(tmp_path / "trade.db")
    await db.initialize()
    runtime = RpgRuntime.create(db, Path("content/rpg"))
    for user, cash in ((A, cash_a), (B, cash_b)):
        await runtime.store.create_character(user, "E", now=1)
        if cash:
            await db.credit_wallet(user, cash, "test", welcome_bonus=0)
    return db, runtime


async def give(runtime: RpgRuntime, user: int, content_id: str = ITEM) -> ItemInstance:
    definition = runtime.content.items[content_id]
    drop = ItemDrop(content_id, definition.stats, definition.max_durability)
    return await runtime.gear.grant_item(user, drop, inventory_cap=60, now=1)


async def trade(runtime: RpgRuntime, **kw: Any):  # type: ignore[no-untyped-def]
    args: dict[str, Any] = {
        "user_a": A, "user_b": B, "items_a": [], "items_b": [], "renown_a": 0,
        "renown_b": 0, "inventory_cap": 60, "welcome_bonus": WELCOME,
    }  # fmt: skip
    args.update(kw)
    return await runtime.gear.commit_trade(**args)


async def balance(db: Database, user: int) -> int:
    wallet = await db.get_wallet(user)
    return wallet.balance if wallet else 0


async def owner_of(db: Database, item_id: int) -> Optional[int]:
    async with db.conn.execute("SELECT owner_id FROM rpg_items WHERE id = ?;", (item_id,)) as c:
        row = await c.fetchone()
    return row[0] if row else None


@pytest.mark.asyncio
async def test_the_schema_has_a_trade_history_table(tmp_path: Path) -> None:
    db, _ = await world(tmp_path)
    try:
        assert CURRENT_SCHEMA_VERSION >= 13
        async with db.conn.execute("SELECT COUNT(*) FROM rpg_trades;") as cursor:
            assert (await cursor.fetchone())[0] == 0  # type: ignore[index]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_gear_for_gear_swaps_owners_and_keeps_durability(tmp_path: Path) -> None:
    db, runtime = await world(tmp_path)
    try:
        worn = await give(runtime, A)
        async with db.transaction() as conn:
            await conn.execute("UPDATE rpg_items SET durability = 17 WHERE id = ?;", (worn.id,))
        other = await give(runtime, B, "oni_head_t1_common")
        result = await trade(runtime, items_a=[worn.id], items_b=[other.id])
        assert await owner_of(db, worn.id) == B and await owner_of(db, other.id) == A
        assert result.reason == "rpg_trade"
        assert [i.id for i in result.to_b] == [worn.id] and [i.id for i in result.to_a] == [
            other.id
        ]
        assert result.to_b[0].durability == 17 and result.to_b[0].owner_id == B
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_gear_for_renown_moves_both_and_logs_it(tmp_path: Path) -> None:
    db, runtime = await world(tmp_path)
    try:
        item = await give(runtime, A)
        result = await trade(runtime, items_a=[item.id], renown_b=300)
        assert await owner_of(db, item.id) == B
        assert (await balance(db, A), await balance(db, B)) == (1300, 700)
        assert result.reason == "rpg_trade"
        async with db.conn.execute("SELECT user_a, user_b, detail FROM rpg_trades;") as cursor:
            rows = await cursor.fetchall()
        assert len(rows) == 1 and (rows[0][0], rows[0][1]) == (A, B)
        detail = json.loads(rows[0][2])
        assert detail["a"]["items"] == [item.id] and detail["b"]["renown"] == 300
        async with db.conn.execute(
            "SELECT user_id, delta FROM renown_transactions WHERE reason = 'rpg_trade' ORDER BY id;"
        ) as cursor:
            assert sorted(await cursor.fetchall()) == [(A, 300), (B, -300)]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_gift_uses_the_gift_ledger_reason(tmp_path: Path) -> None:
    db, runtime = await world(tmp_path)
    try:
        item = await give(runtime, A)
        result = await trade(runtime, items_a=[item.id], renown_a=100)
        assert result.reason == "rpg_gift"
        assert (await balance(db, A), await balance(db, B)) == (900, 1100)
        async with db.conn.execute(
            "SELECT COUNT(*) FROM renown_transactions WHERE reason = 'rpg_gift';"
        ) as cursor:
            assert (await cursor.fetchone())[0] == 2  # type: ignore[index]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_an_empty_trade_is_refused(tmp_path: Path) -> None:
    db, runtime = await world(tmp_path)
    try:
        with pytest.raises(TradeNothingError):
            await trade(runtime)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_items_that_changed_hands_or_were_equipped_or_sold_are_refused(
    tmp_path: Path,
) -> None:
    db, runtime = await world(tmp_path)
    try:
        theirs = await give(runtime, B)
        mine = await give(runtime, A)
        worn = await give(runtime, A, "oni_head_t1_common")
        await runtime.gear.equip_item(A, worn.id, level=60, items=runtime.content.items)
        for kw, gone in (
            ({"items_a": [theirs.id]}, [theirs.id]),  # not mine to give
            ({"items_a": [worn.id]}, [worn.id]),  # equipped
            ({"items_a": [999999]}, [999999]),  # never existed / sold
            ({"items_a": [mine.id, worn.id], "renown_b": 10}, [worn.id]),
        ):
            with pytest.raises(TradeItemsGoneError) as err:
                await trade(runtime, **kw)
            assert list(err.value.item_ids) == gone
        assert await owner_of(db, mine.id) == A and await balance(db, B) == 1000
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_member_in_a_fight_cannot_trade(tmp_path: Path) -> None:
    db, runtime = await world(tmp_path)
    try:
        item = await give(runtime, A)
        with pytest.raises(TradeFightingError) as err:
            await trade(runtime, items_a=[item.id], fighting={B})
        assert err.value.user_id == B
        # an open fight row also counts, even when the in-memory set is empty
        await runtime.store.begin_fight(
            mission_id="e_pest_control", kind="mission", user_ids=[A], cost=1,
            today="2026-10-14", base_attempts=8, streak_rules=runtime.content.balance.streak,
        )  # fmt: skip
        with pytest.raises(TradeFightingError) as err:
            await trade(runtime, items_a=[item.id])
        assert err.value.user_id == A
        assert await owner_of(db, item.id) == A
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_missing_characters_are_refused(tmp_path: Path) -> None:
    db, runtime = await world(tmp_path)
    try:
        item = await give(runtime, A)
        with pytest.raises(NoCharacterError):
            await trade(runtime, user_b=777, items_a=[item.id])
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_renown_is_checked_for_the_right_person(tmp_path: Path) -> None:
    db, runtime = await world(tmp_path, cash_a=50, cash_b=10)
    try:
        item = await give(runtime, A)
        with pytest.raises(TradeFundsError) as err:
            await trade(runtime, items_a=[item.id], renown_b=11)
        assert err.value.user_id == B and "<@6>" in err.value.message
        with pytest.raises(TradeFundsError) as err:
            await trade(runtime, renown_a=51)
        assert err.value.user_id == A
        assert (await balance(db, A), await balance(db, B)) == (50, 10)
        assert await owner_of(db, item.id) == A
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_full_inventory_blocks_the_trade_and_says_whose(tmp_path: Path) -> None:
    db, runtime = await world(tmp_path)
    try:
        mine = await give(runtime, A)
        await give(runtime, B)
        with pytest.raises(TradeRoomError) as err:
            await trade(runtime, items_a=[mine.id], inventory_cap=1)
        assert err.value.user_id == B
        other = await give(runtime, B, "oni_head_t1_common")
        # a one-for-one swap keeps both at their count, so it is fine at the cap
        await trade(runtime, items_a=[mine.id], items_b=[other.id], inventory_cap=2)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_failure_midway_leaves_everything_unchanged(tmp_path: Path) -> None:
    db, runtime = await world(tmp_path)
    try:
        item = await give(runtime, A)
        original = runtime.gear._db.credit_within_transaction

        async def boom(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("disk on fire")

        runtime.gear._db.credit_within_transaction = boom  # type: ignore[method-assign]
        try:
            with pytest.raises(RuntimeError):
                await trade(runtime, items_a=[item.id], renown_b=100)
        finally:
            runtime.gear._db.credit_within_transaction = original  # type: ignore[method-assign]
        assert await owner_of(db, item.id) == A
        assert (await balance(db, A), await balance(db, B)) == (1000, 1000)
        async with db.conn.execute("SELECT COUNT(*) FROM rpg_trades;") as cursor:
            assert (await cursor.fetchone())[0] == 0  # type: ignore[index]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_two_trades_for_the_same_item_only_one_wins(tmp_path: Path) -> None:
    db, runtime = await world(tmp_path)
    try:
        await db.credit_wallet(7, 1000, "test", welcome_bonus=0)
        await runtime.store.create_character(7, "E", now=1)
        item = await give(runtime, A)
        results = await asyncio.gather(
            trade(runtime, items_a=[item.id], renown_b=100),
            trade(runtime, user_b=7, items_a=[item.id], renown_b=100),
            return_exceptions=True,
        )
        wins = [r for r in results if not isinstance(r, BaseException)]
        losses = [r for r in results if isinstance(r, TradeItemsGoneError)]
        assert len(wins) == 1 and len(losses) == 1
        assert await owner_of(db, item.id) in (B, 7)
        assert await balance(db, A) == 1100
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_random_series_of_trades_never_duplicates_or_loses_anything(
    tmp_path: Path,
) -> None:
    db, runtime = await world(tmp_path)
    rng = random.Random(8)
    try:
        for _ in range(5):
            await give(runtime, A)
            await give(runtime, B, "oni_head_t1_common")
        total_items = 10
        total_renown = await balance(db, A) + await balance(db, B)
        for _ in range(60):
            giver, taker = (A, B) if rng.random() < 0.5 else (B, A)
            mine = [i.id for i in await runtime.gear.list_items(giver)]
            theirs = [i.id for i in await runtime.gear.list_items(taker)]
            offer_a = rng.sample(mine, rng.randint(0, min(2, len(mine))))
            offer_b = rng.sample(theirs, rng.randint(0, min(2, len(theirs))))
            try:
                await trade(
                    runtime, user_a=giver, user_b=taker, items_a=offer_a, items_b=offer_b,
                    renown_a=rng.randint(0, 400), renown_b=rng.randint(0, 400),
                )  # fmt: skip
            except (TradeNothingError, TradeFundsError):
                continue
            async with db.conn.execute("SELECT COUNT(*) FROM rpg_items;") as cursor:
                assert (await cursor.fetchone())[0] == total_items  # type: ignore[index]
            assert await balance(db, A) + await balance(db, B) == total_renown
    finally:
        await db.close()
