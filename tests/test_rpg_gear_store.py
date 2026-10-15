"""Tests for items, equipment, the shop, repairs, sales and gear settlement in the store."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

import pytest

from yokai.errors import (
    GearLevelTooLowError,
    GearNotFoundError,
    InsufficientFundsError,
    InventoryFullError,
    ItemEquippedError,
    NothingToRepairError,
    ShopLimitError,
)
from yokai.rpg.gear_store import GearSettlement, ItemDrop
from yokai.rpg.guild import Reward
from yokai.rpg.models import ItemInstance
from yokai.rpg.runtime import RpgRuntime
from yokai.storage.db import CURRENT_SCHEMA_VERSION, Database

USER = 5
DAY = "2026-10-14"
WELCOME = 200


async def fresh(tmp_path: Path, *, cash: int = 0) -> tuple[Database, RpgRuntime]:
    db = Database(tmp_path / "gear.db")
    await db.initialize()
    runtime = RpgRuntime.create(db, Path("content/rpg"))
    await runtime.store.create_character(USER, "E", now=1)
    if cash:
        await db.credit_wallet(USER, cash, "test", welcome_bonus=0)
    return db, runtime


def drop_of(runtime: RpgRuntime, content_id: str, durability: Optional[int] = None) -> ItemDrop:
    definition = runtime.content.items[content_id]
    return ItemDrop(
        content_id,
        definition.stats,
        definition.max_durability if durability is None else durability,
    )


async def give(
    runtime: RpgRuntime, content_id: str, *, user: int = USER, durability: Optional[int] = None
) -> ItemInstance:
    return await runtime.gear.grant_item(
        user, drop_of(runtime, content_id, durability), inventory_cap=60, now=1
    )


async def balance(db: Database, user: int = USER) -> int:
    wallet = await db.get_wallet(user)
    return wallet.balance if wallet else 0


async def equip(runtime: RpgRuntime, item: ItemInstance, level: int = 60) -> ItemInstance:
    return await runtime.gear.equip_item(
        item.owner_id, item.id, level=level, items=runtime.content.items
    )


# -------------------------------------------------------------------------------- schema


@pytest.mark.asyncio
async def test_v11_creates_the_gear_tables_and_replays_safely(tmp_path: Path) -> None:
    db, runtime = await fresh(tmp_path)
    try:
        assert CURRENT_SCHEMA_VERSION >= 11
        for table in ("rpg_items", "rpg_equipment", "rpg_shops", "rpg_shop_purchases"):
            async with db.conn.execute(f"PRAGMA table_info({table});") as cursor:
                assert await cursor.fetchall(), table
        await give(runtime, "oni_weapon_t1_common")
        await db.conn.execute("PRAGMA user_version = 10;")
        await db.conn.commit()
    finally:
        await db.close()

    db = Database(tmp_path / "gear.db")
    await db.initialize()
    try:
        runtime = RpgRuntime.create(db, Path("content/rpg"))
        assert len(await runtime.gear.list_items(USER)) == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_debit_within_transaction_takes_renown_or_rolls_back(tmp_path: Path) -> None:
    db, _ = await fresh(tmp_path, cash=100)
    try:
        async with db.transaction():
            assert await db.debit_within_transaction(USER, 30, "test", 0) == 70
        assert await balance(db) == 70
        with pytest.raises(InsufficientFundsError):
            async with db.transaction():
                await db.debit_within_transaction(USER, 500, "test", 0)
        with pytest.raises(RuntimeError):
            async with db.transaction():
                await db.debit_within_transaction(USER, 10, "test", 0)
                raise RuntimeError("boom")
        assert await balance(db) == 70
        with pytest.raises(ValueError):
            async with db.transaction():
                await db.debit_within_transaction(USER, 0, "test", 0)
        async with db.conn.execute(
            "SELECT delta FROM renown_transactions WHERE reason = 'test' AND delta < 0;"
        ) as cursor:
            assert [row[0] for row in await cursor.fetchall()] == [-30]
    finally:
        await db.close()


# ------------------------------------------------------------------------------ items


@pytest.mark.asyncio
async def test_a_granted_item_round_trips_its_rolls(tmp_path: Path) -> None:
    db, runtime = await fresh(tmp_path)
    try:
        item = await give(runtime, "oni_weapon_t3_common")
        again = await runtime.gear.get_item(item.id)
        assert again == item and again.rolls == runtime.content.items["oni_weapon_t3_common"].stats
        assert again.equipped_slot is None and again.durability == 100
        assert [i.id for i in await runtime.gear.list_items(USER)] == [item.id]
        assert await runtime.gear.list_items(999) == []
        assert await runtime.gear.get_item(12345) is None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_the_inventory_cap_limits_grants(tmp_path: Path) -> None:
    db, runtime = await fresh(tmp_path)
    try:
        for _ in range(3):
            await runtime.gear.grant_item(
                USER, drop_of(runtime, "oni_head_t1_common"), inventory_cap=3
            )
        with pytest.raises(InventoryFullError):
            await runtime.gear.grant_item(
                USER, drop_of(runtime, "oni_head_t1_common"), inventory_cap=3
            )
        assert len(await runtime.gear.list_items(USER)) == 3
    finally:
        await db.close()


# ---------------------------------------------------------------------------- equipping


@pytest.mark.asyncio
async def test_equipping_marks_the_item_and_replaces_the_slot(tmp_path: Path) -> None:
    db, runtime = await fresh(tmp_path)
    try:
        first = await give(runtime, "oni_weapon_t1_common")
        second = await give(runtime, "oni_weapon_t2_common")
        assert (await equip(runtime, first)).equipped_slot == "weapon"
        await equip(runtime, second)
        worn = await runtime.gear.equipped_items(USER)
        assert [i.id for i in worn] == [second.id]
        again = await runtime.gear.get_item(first.id)
        assert again is not None and again.equipped_slot is None
        await equip(runtime, second)  # equipping the same item again changes nothing
        assert len(await runtime.gear.equipped_items(USER)) == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_gear_above_your_level_cannot_be_worn(tmp_path: Path) -> None:
    """Review Focus 5."""
    db, runtime = await fresh(tmp_path)
    try:
        item = await give(runtime, "oni_weapon_t3_common")  # needs level 24
        with pytest.raises(GearLevelTooLowError) as raised:
            await equip(runtime, item, level=23)
        assert "level 24" in raised.value.message and "level 23" in raised.value.message
        assert await runtime.gear.equipped_items(USER) == []
        assert (await equip(runtime, item, level=24)).equipped_slot == "weapon"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_you_cannot_wear_someone_elses_item_or_a_missing_one(tmp_path: Path) -> None:
    db, runtime = await fresh(tmp_path)
    try:
        await runtime.store.create_character(6, "E", now=1)
        theirs = await give(runtime, "oni_weapon_t1_common", user=6)
        with pytest.raises(GearNotFoundError):
            await equip(
                runtime, ItemInstance(theirs.id, USER, theirs.content_id, theirs.rolls, 1, 0)
            )
        with pytest.raises(GearNotFoundError):
            await runtime.gear.equip_item(USER, 99999, level=60, items=runtime.content.items)
        assert await runtime.gear.equipped_items(USER) == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_an_item_whose_content_was_removed_cannot_be_worn(tmp_path: Path) -> None:
    db, runtime = await fresh(tmp_path)
    try:
        ghost = await runtime.gear.grant_item(
            USER,
            ItemDrop("deleted_item", runtime.content.items["oni_head_t1_common"].stats, 50),
            inventory_cap=60,
        )
        with pytest.raises(GearNotFoundError):
            await equip(runtime, ghost)
        assert (await runtime.gear.list_items(USER))[0].id == ghost.id  # still listed
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_unequipping_by_item_and_by_slot(tmp_path: Path) -> None:
    db, runtime = await fresh(tmp_path)
    try:
        weapon = await equip(runtime, await give(runtime, "oni_weapon_t1_common"))
        head = await equip(runtime, await give(runtime, "oni_head_t1_common"))
        assert await runtime.gear.unequip_item(USER, weapon.id) is True
        assert await runtime.gear.unequip_item(USER, weapon.id) is False
        assert await runtime.gear.unequip_slot(USER, "head") is True
        assert await runtime.gear.unequip_slot(USER, "head") is False
        assert head.equipped_slot == "head" and await runtime.gear.equipped_items(USER) == []
        assert not await runtime.gear.unequip_item(777, weapon.id)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_racing_equips_for_one_slot_leave_exactly_one_item_worn(tmp_path: Path) -> None:
    """Review Focus 1: no item is ever in two places and a slot never holds two items."""
    db, runtime = await fresh(tmp_path)
    try:
        items = [await give(runtime, f"oni_weapon_t{t}_common") for t in (1, 2, 1, 2, 1)]
        await asyncio.gather(*(equip(runtime, item) for item in items))
        worn = await runtime.gear.equipped_items(USER)
        assert len(worn) == 1
        async with db.conn.execute("SELECT COUNT(*) FROM rpg_equipment;") as cursor:
            row = await cursor.fetchone()
        assert row is not None and row[0] == 1
    finally:
        await db.close()


# ----------------------------------------------------------------------------- repairing


@pytest.mark.asyncio
async def test_repairing_pays_pro_rata_and_restores_durability(tmp_path: Path) -> None:
    db, runtime = await fresh(tmp_path, cash=1000)
    try:
        broken = await give(runtime, "oni_weapon_t3_common", durability=0)  # cost 101
        half = await give(runtime, "oni_head_t3_common", durability=50)  # price 173 -> 31
        whole = await give(runtime, "oni_body_t3_common")
        result = await runtime.gear.repair_items(
            USER,
            None,
            items=runtime.content.items,
            rules=runtime.content.balance.gear,
            welcome_bonus=0,
        )
        assert result.repaired == 2 and result.cost == 101 + 31
        assert await balance(db) == 1000 - result.cost
        for item in (broken, half, whole):
            fixed = await runtime.gear.get_item(item.id)
            assert fixed is not None and fixed.durability == 100
        async with db.conn.execute(
            "SELECT delta FROM renown_transactions WHERE reason = 'rpg_repair';"
        ) as cursor:
            assert [row[0] for row in await cursor.fetchall()] == [-(101 + 31)]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_repairing_one_item_leaves_the_others_damaged(tmp_path: Path) -> None:
    db, runtime = await fresh(tmp_path, cash=1000)
    try:
        a = await give(runtime, "oni_weapon_t3_common", durability=0)
        b = await give(runtime, "oni_head_t3_common", durability=0)
        result = await runtime.gear.repair_items(
            USER,
            [a.id],
            items=runtime.content.items,
            rules=runtime.content.balance.gear,
            welcome_bonus=0,
        )
        assert result.repaired == 1
        fixed, still = await runtime.gear.get_item(a.id), await runtime.gear.get_item(b.id)
        assert fixed is not None and still is not None
        assert (fixed.durability, still.durability) == (100, 0)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_repair_you_cannot_afford_changes_nothing(tmp_path: Path) -> None:
    """Review Focus 2."""
    db, runtime = await fresh(tmp_path, cash=10)
    try:
        item = await give(runtime, "oni_weapon_t3_common", durability=0)
        with pytest.raises(InsufficientFundsError):
            await runtime.gear.repair_items(
                USER,
                None,
                items=runtime.content.items,
                rules=runtime.content.balance.gear,
                welcome_bonus=0,
            )
        again = await runtime.gear.get_item(item.id)
        assert again is not None and again.durability == 0 and await balance(db) == 10
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_repair_errors_for_undamaged_or_foreign_items(tmp_path: Path) -> None:
    db, runtime = await fresh(tmp_path, cash=1000)
    try:
        sound = await give(runtime, "oni_weapon_t3_common")
        kwargs = {
            "items": runtime.content.items,
            "rules": runtime.content.balance.gear,
            "welcome_bonus": 0,
        }
        with pytest.raises(NothingToRepairError):
            await runtime.gear.repair_items(USER, None, **kwargs)
        with pytest.raises(NothingToRepairError):
            await runtime.gear.repair_items(USER, [sound.id], **kwargs)
        with pytest.raises(GearNotFoundError):
            await runtime.gear.repair_items(USER, [sound.id, 99999], **kwargs)
        assert await balance(db) == 1000
    finally:
        await db.close()


# --------------------------------------------------------------------------- the shop


async def buy(
    runtime: RpgRuntime,
    content_id: str,
    *,
    day: str = DAY,
    daily_cap: int = 3,
    inventory_cap: int = 60,
) -> ItemInstance:
    definition = runtime.content.items[content_id]
    return await runtime.gear.buy_shop_item(
        USER,
        drop_of(runtime, content_id),
        price=definition.price,
        day=day,
        daily_cap=daily_cap,
        inventory_cap=inventory_cap,
        welcome_bonus=0,
    )


@pytest.mark.asyncio
async def test_shop_stock_is_drawn_once_per_day(tmp_path: Path) -> None:
    db, runtime = await fresh(tmp_path)
    try:
        assert await runtime.gear.get_shop(DAY) is None
        assert await runtime.gear.get_or_create_shop(DAY, lambda: ["a", "b"]) == ["a", "b"]
        assert await runtime.gear.get_or_create_shop(DAY, lambda: ["z"]) == ["a", "b"]
        assert await runtime.gear.get_shop(DAY) == ["a", "b"]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_buying_takes_the_price_and_adds_the_item(tmp_path: Path) -> None:
    db, runtime = await fresh(tmp_path, cash=500)
    try:
        item = await buy(runtime, "oni_weapon_t1_common")  # price 60
        assert item.owner_id == USER and item.durability == 100
        assert await balance(db) == 440
        assert await runtime.gear.purchases_today(USER, DAY) == 1
        assert await runtime.gear.purchases_today(USER, "2026-10-15") == 0
        async with db.conn.execute(
            "SELECT delta FROM renown_transactions WHERE reason = 'rpg_shop';"
        ) as cursor:
            assert [row[0] for row in await cursor.fetchall()] == [-60]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_the_daily_purchase_cap_is_enforced_and_resets_the_next_day(tmp_path: Path) -> None:
    db, runtime = await fresh(tmp_path, cash=1000)
    try:
        for _ in range(3):
            await buy(runtime, "oni_head_t1_common")
        with pytest.raises(ShopLimitError):
            await buy(runtime, "oni_head_t1_common")
        assert await balance(db) == 1000 - 3 * 36 and len(await runtime.gear.list_items(USER)) == 3
        await buy(runtime, "oni_head_t1_common", day="2026-10-15")
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_purchase_you_cannot_afford_changes_nothing(tmp_path: Path) -> None:
    db, runtime = await fresh(tmp_path, cash=20)
    try:
        with pytest.raises(InsufficientFundsError):
            await buy(runtime, "oni_weapon_t3_common")
        assert await balance(db) == 20 and await runtime.gear.list_items(USER) == []
        assert await runtime.gear.purchases_today(USER, DAY) == 0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_full_inventory_blocks_buying_without_charging(tmp_path: Path) -> None:
    db, runtime = await fresh(tmp_path, cash=1000)
    try:
        await give(runtime, "oni_head_t1_common")
        with pytest.raises(InventoryFullError):
            await buy(runtime, "oni_head_t1_common", inventory_cap=1)
        assert await balance(db) == 1000
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_racing_purchases_respect_the_daily_cap(tmp_path: Path) -> None:
    db, runtime = await fresh(tmp_path, cash=10_000)
    try:
        results = await asyncio.gather(
            *[buy(runtime, "oni_head_t1_common", daily_cap=2) for _ in range(8)],
            return_exceptions=True,
        )
        assert sum(isinstance(r, ItemInstance) for r in results) == 2
        assert sum(isinstance(r, ShopLimitError) for r in results) == 6
        assert len(await runtime.gear.list_items(USER)) == 2
    finally:
        await db.close()


# ----------------------------------------------------------------------------- selling


def sell_args(runtime: RpgRuntime) -> dict:  # type: ignore[type-arg]
    return {
        "items": runtime.content.items,
        "rules": runtime.content.balance.gear,
        "welcome_bonus": 0,
    }


@pytest.mark.asyncio
async def test_selling_pays_the_guild_rate_and_removes_the_item(tmp_path: Path) -> None:
    db, runtime = await fresh(tmp_path)
    try:
        item = await give(runtime, "oni_weapon_t3_common")  # price 288 -> 86
        assert await runtime.gear.sell_item(USER, item.id, **sell_args(runtime)) == 86
        assert await balance(db) == 86 and await runtime.gear.get_item(item.id) is None
        with pytest.raises(GearNotFoundError):
            await runtime.gear.sell_item(USER, item.id, **sell_args(runtime))
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_equipped_and_foreign_items_cannot_be_sold(tmp_path: Path) -> None:
    db, runtime = await fresh(tmp_path)
    try:
        await runtime.store.create_character(6, "E", now=1)
        worn = await equip(runtime, await give(runtime, "oni_weapon_t1_common"))
        theirs = await give(runtime, "oni_head_t1_common", user=6)
        with pytest.raises(ItemEquippedError):
            await runtime.gear.sell_item(USER, worn.id, **sell_args(runtime))
        with pytest.raises(GearNotFoundError):
            await runtime.gear.sell_item(USER, theirs.id, **sell_args(runtime))
        assert await balance(db) == 0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_racing_sales_pay_once(tmp_path: Path) -> None:
    db, runtime = await fresh(tmp_path)
    try:
        item = await give(runtime, "oni_weapon_t3_common")
        results = await asyncio.gather(
            *[runtime.gear.sell_item(USER, item.id, **sell_args(runtime)) for _ in range(6)],
            return_exceptions=True,
        )
        assert sum(isinstance(r, int) for r in results) == 1
        assert await balance(db) == 86
    finally:
        await db.close()


# ----------------------------------------------------------------------- fight settlement


async def open_fight(runtime: RpgRuntime) -> int:
    return await runtime.store.begin_fight(
        mission_id="e_pest_control", kind="mission", user_ids=[USER], cost=1, today=DAY,
        base_attempts=8, streak_rules=runtime.content.balance.streak,
    )  # fmt: skip


def gear_settlement(
    runtime: RpgRuntime, drops: dict[int, list[ItemDrop]] | None = None
) -> GearSettlement:
    return GearSettlement(
        items=runtime.content.items, rules=runtime.content.balance.gear, drops=drops or {}
    )


async def settle(runtime: RpgRuntime, fight_id: int, *, won: bool, gear: GearSettlement):  # type: ignore[no-untyped-def]
    return await runtime.store.settle_fight(
        fight_id,
        won=won,
        rewards={USER: Reward(3, 4)},
        level_cap=60,
        xp_curve=runtime.content.balance.xp,
        welcome_bonus=WELCOME,
        gear=gear,
    )


@pytest.mark.asyncio
async def test_a_win_wears_equipped_gear_and_reports_crossed_warnings(tmp_path: Path) -> None:
    db, runtime = await fresh(tmp_path)
    try:
        worn = await equip(runtime, await give(runtime, "oni_weapon_t1_common", durability=52))
        spare = await give(runtime, "oni_head_t1_common")
        result = await settle(
            runtime, await open_fight(runtime), won=True, gear=gear_settlement(runtime)
        )
        assert result is not None
        (report,) = result[USER].wear
        assert (report.item_id, report.before, report.after) == (worn.id, 52, 49)
        assert report.crossed == (50,)
        still_worn = await runtime.gear.get_item(worn.id)
        untouched = await runtime.gear.get_item(spare.id)
        assert still_worn is not None and untouched is not None
        assert (still_worn.durability, untouched.durability) == (49, 100)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_loss_wears_more_and_can_break_gear(tmp_path: Path) -> None:
    db, runtime = await fresh(tmp_path)
    try:
        worn = await equip(runtime, await give(runtime, "oni_weapon_t1_common", durability=5))
        result = await settle(
            runtime, await open_fight(runtime), won=False, gear=gear_settlement(runtime)
        )
        assert result is not None
        (report,) = result[USER].wear
        assert (report.before, report.after) == (5, 0) and report.crossed == (0,)
        broken = await runtime.gear.get_item(worn.id)
        assert broken is not None and broken.durability == 0 and result[USER].drops == ()
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_drops_are_added_on_a_win_but_never_on_a_loss(tmp_path: Path) -> None:
    db, runtime = await fresh(tmp_path)
    try:
        drop = drop_of(runtime, "kitsune_head_t1_rare")
        won = await settle(
            runtime,
            await open_fight(runtime),
            won=True,
            gear=gear_settlement(runtime, {USER: [drop]}),
        )
        assert won is not None and len(won[USER].drops) == 1
        assert won[USER].drops[0].item_id is not None and won[USER].drops[0].sold_for is None
        assert len(await runtime.gear.list_items(USER)) == 1
        lost = await settle(
            runtime,
            await open_fight(runtime),
            won=False,
            gear=gear_settlement(runtime, {USER: [drop]}),
        )
        assert lost is not None and lost[USER].drops == ()
        assert len(await runtime.gear.list_items(USER)) == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_settling_twice_applies_wear_and_drops_once(tmp_path: Path) -> None:
    """Review Focus 3."""
    db, runtime = await fresh(tmp_path)
    try:
        worn = await equip(runtime, await give(runtime, "oni_weapon_t1_common"))
        drop = drop_of(runtime, "kitsune_head_t1_rare")
        fight_id = await open_fight(runtime)
        settlement = gear_settlement(runtime, {USER: [drop]})
        results = await asyncio.gather(
            *[settle(runtime, fight_id, won=True, gear=settlement) for _ in range(4)]
        )
        assert sum(r is not None for r in results) == 1
        item = await runtime.gear.get_item(worn.id)
        assert item is not None and item.durability == 97
        assert len(await runtime.gear.list_items(USER)) == 2  # the worn weapon plus one drop
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_drop_into_a_full_inventory_is_sold_and_never_exceeds_the_cap(
    tmp_path: Path,
) -> None:
    """Review Focus 8."""
    db, runtime = await fresh(tmp_path)
    try:
        cap = runtime.content.balance.gear.inventory_cap
        for _ in range(cap):
            await give(runtime, "oni_head_t1_common")
        drop = drop_of(runtime, "oni_weapon_t3_common")  # sells for 86
        result = await settle(
            runtime,
            await open_fight(runtime),
            won=True,
            gear=gear_settlement(runtime, {USER: [drop, drop]}),
        )
        assert result is not None
        sales = [d for d in result[USER].drops if d.sold_for is not None]
        assert len(sales) == 2 and all(d.item_id is None and d.sold_for == 86 for d in sales)
        assert len(await runtime.gear.list_items(USER)) == cap
        assert await balance(db) == WELCOME + 4 + 2 * 86
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_settling_without_gear_still_works_like_before(tmp_path: Path) -> None:
    db, runtime = await fresh(tmp_path)
    try:
        result = await runtime.store.settle_fight(
            await open_fight(runtime), won=True, rewards={USER: Reward(3, 4)}, level_cap=60,
            xp_curve=runtime.content.balance.xp, welcome_bonus=WELCOME,
        )  # fmt: skip
        assert result is not None and result[USER].wear == () and result[USER].drops == ()
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_old_shops_and_boards_are_purged(tmp_path: Path) -> None:
    db, runtime = await fresh(tmp_path)
    try:
        await runtime.gear.get_or_create_shop("2020-01-01", lambda: ["a"], now=10)
        await runtime.gear.get_or_create_shop("2099-01-01", lambda: ["b"], now=10**10)
        await runtime.store.purge_history(1000)
        assert await runtime.gear.get_shop("2020-01-01") is None
        assert await runtime.gear.get_shop("2099-01-01") == ["b"]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_hard_loss_can_cross_the_quarter_warning(tmp_path: Path) -> None:
    db, runtime = await fresh(tmp_path)
    try:
        await equip(runtime, await give(runtime, "oni_weapon_t1_common", durability=30))
        result = await settle(
            runtime, await open_fight(runtime), won=False, gear=gear_settlement(runtime)
        )
        assert result is not None
        (report,) = result[USER].wear
        assert (report.before, report.after, report.crossed) == (30, 22, (25,))
    finally:
        await db.close()
