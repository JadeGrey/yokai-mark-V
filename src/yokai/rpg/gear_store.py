"""Database access for items, equipment, the guild shop, repairs and sales."""

from __future__ import annotations

import json
import time
from collections.abc import Collection
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Mapping, Optional, Sequence

import aiosqlite

from yokai.errors import (
    GearLevelTooLowError,
    GearNotFoundError,
    InventoryFullError,
    ItemEquippedError,
    NoCharacterError,
    NothingToRepairError,
    ShopLimitError,
    TradeFightingError,
    TradeFundsError,
    TradeItemsGoneError,
    TradeNothingError,
    TradeRoomError,
)
from yokai.rpg import gear
from yokai.rpg.audit import record_admin_action
from yokai.rpg.models import GearRules, ItemDef, ItemInstance, Stats

if TYPE_CHECKING:
    from yokai.storage.db import Database

_ITEM_SELECT = (
    "SELECT i.id, i.owner_id, i.content_id, i.rolls, i.durability, i.created_ts, e.slot "
    "FROM rpg_items i LEFT JOIN rpg_equipment e ON e.item_id = i.id"
)


# --------------------------------------------------------------------------- result types


@dataclass(frozen=True, slots=True)
class TradeResult:
    """What a committed trade moved: items that went to each side, and the ledger reason."""

    trade_id: int
    to_a: list[ItemInstance]
    to_b: list[ItemInstance]
    reason: str


@dataclass(frozen=True, slots=True)
class ItemDrop:
    """A new item to create: which one, with which rolled stats, at what durability."""

    content_id: str
    rolls: Stats
    durability: int


@dataclass(frozen=True, slots=True)
class DropResult:
    """What happened to a drop: kept as an item, or sold because the inventory was full."""

    content_id: str
    item_id: Optional[int]
    sold_for: Optional[int]


@dataclass(frozen=True, slots=True)
class GearWear:
    """One equipped item after a fight's wear, and which warnings it crossed."""

    item_id: int
    content_id: str
    before: int
    after: int
    max_durability: int
    crossed: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class GearSettlement:
    """Everything settle_fight needs to apply wear and add drops for the participants."""

    items: Mapping[str, ItemDef]
    rules: GearRules
    drops: Mapping[int, Sequence[ItemDrop]]


@dataclass(frozen=True, slots=True)
class RepairResult:
    cost: int
    repaired: int


# -------------------------------------------------------------------------- row helpers


def _item_from_row(row: Sequence[Any]) -> ItemInstance:
    return ItemInstance(
        id=row[0],
        owner_id=row[1],
        content_id=row[2],
        rolls=Stats(**json.loads(row[3])),
        durability=row[4],
        created_ts=row[5],
        equipped_slot=row[6],
    )


def _stats_dict(stats: Stats) -> dict[str, int]:
    return {
        name: getattr(stats, name)
        for name in ("hp", "attack", "magic", "defense", "resist", "speed", "crit")
        if getattr(stats, name)
    }


async def fetch_item(conn: aiosqlite.Connection, item_id: int) -> Optional[ItemInstance]:
    async with conn.execute(f"{_ITEM_SELECT} WHERE i.id = ?;", (item_id,)) as cursor:
        row = await cursor.fetchone()
    return _item_from_row(row) if row else None


async def fetch_owned(conn: aiosqlite.Connection, user_id: int) -> list[ItemInstance]:
    async with conn.execute(
        f"{_ITEM_SELECT} WHERE i.owner_id = ? ORDER BY i.id;", (user_id,)
    ) as cursor:
        return [_item_from_row(row) for row in await cursor.fetchall()]


async def fetch_equipped(conn: aiosqlite.Connection, user_id: int) -> list[ItemInstance]:
    async with conn.execute(
        f"{_ITEM_SELECT} WHERE e.user_id = ? ORDER BY e.slot;", (user_id,)
    ) as cursor:
        return [_item_from_row(row) for row in await cursor.fetchall()]


async def count_items(conn: aiosqlite.Connection, user_id: int) -> int:
    async with conn.execute(
        "SELECT COUNT(*) FROM rpg_items WHERE owner_id = ?;", (user_id,)
    ) as cursor:
        row = await cursor.fetchone()
    return int(row[0]) if row else 0


async def insert_item(conn: aiosqlite.Connection, user_id: int, drop: ItemDrop, now: int) -> int:
    cursor = await conn.execute(
        "INSERT INTO rpg_items (owner_id, content_id, rolls, durability, created_ts) "
        "VALUES (?, ?, ?, ?, ?);",
        (user_id, drop.content_id, json.dumps(_stats_dict(drop.rolls)), drop.durability, now),
    )
    item_id = cursor.lastrowid
    assert item_id is not None
    return item_id


async def apply_wear(
    conn: aiosqlite.Connection, user_id: int, points: int, items: Mapping[str, ItemDef]
) -> list[GearWear]:
    """Wear down everything the member has equipped; report the warnings crossed."""
    report: list[GearWear] = []
    for item in await fetch_equipped(conn, user_id):
        after = max(0, item.durability - points)
        if after != item.durability:
            await conn.execute(
                "UPDATE rpg_items SET durability = ? WHERE id = ?;", (after, item.id)
            )
        definition = items.get(item.content_id)
        maximum = definition.max_durability if definition else max(item.durability, 1)
        report.append(
            GearWear(
                item_id=item.id,
                content_id=item.content_id,
                before=item.durability,
                after=after,
                max_durability=maximum,
                crossed=tuple(gear.wear_crossings(item.durability, after, maximum)),
            )
        )
    return report


async def add_drops(
    conn: aiosqlite.Connection,
    db: Database,
    user_id: int,
    drops: Sequence[ItemDrop],
    settlement: GearSettlement,
    welcome_bonus: int,
    now: int,
) -> list[DropResult]:
    """Add drops to the inventory; a drop into a full inventory is sold on the spot."""
    results: list[DropResult] = []
    held = await count_items(conn, user_id)
    for drop in drops:
        definition = settlement.items.get(drop.content_id)
        if held >= settlement.rules.inventory_cap and definition is not None:
            value = gear.sell_value(definition, settlement.rules)
            await db.credit_within_transaction(user_id, value, "rpg_sale", welcome_bonus)
            results.append(DropResult(drop.content_id, None, value))
            continue
        item_id = await insert_item(conn, user_id, drop, now)
        held += 1
        results.append(DropResult(drop.content_id, item_id, None))
    return results


# ------------------------------------------------------------------------------- the store


class GearStore:
    """Equip, repair, shop and sell. Each operation is one atomic transaction."""

    def __init__(self, db: Database) -> None:
        self._db = db

    # ------------------------------------------------------------------ reading

    async def get_item(self, item_id: int) -> Optional[ItemInstance]:
        return await fetch_item(self._db.conn, item_id)

    async def list_items(self, user_id: int) -> list[ItemInstance]:
        """Everything the member owns, equipped or not, oldest first."""
        return await fetch_owned(self._db.conn, user_id)

    async def equipped_items(self, user_id: int) -> list[ItemInstance]:
        return await fetch_equipped(self._db.conn, user_id)

    async def get_shop(self, day: str) -> Optional[list[str]]:
        async with self._db.conn.execute(
            "SELECT item_ids FROM rpg_shops WHERE day_key = ?;", (day,)
        ) as cursor:
            row = await cursor.fetchone()
        return list(json.loads(row[0])) if row else None

    async def purchases_today(self, user_id: int, day: str) -> int:
        async with self._db.conn.execute(
            "SELECT count FROM rpg_shop_purchases WHERE user_id = ? AND day_key = ?;",
            (user_id, day),
        ) as cursor:
            row = await cursor.fetchone()
        return int(row[0]) if row else 0

    # ------------------------------------------------------------------ shop stock

    async def get_or_create_shop(
        self, day: str, build: Callable[[], list[str]], now: Optional[int] = None
    ) -> list[str]:
        created_ts = int(time.time()) if now is None else now
        async with self._db.transaction() as conn:
            async with conn.execute(
                "SELECT item_ids FROM rpg_shops WHERE day_key = ?;", (day,)
            ) as cursor:
                row = await cursor.fetchone()
            if row:
                return list(json.loads(row[0]))
            ids = build()
            await conn.execute(
                "INSERT INTO rpg_shops (day_key, item_ids, created_ts) VALUES (?, ?, ?);",
                (day, json.dumps(ids), created_ts),
            )
        return ids

    # ------------------------------------------------------------------ giving items

    async def grant_item(
        self,
        user_id: int,
        drop: ItemDrop,
        *,
        inventory_cap: int,
        now: Optional[int] = None,
        audit_actor: Optional[int] = None,
    ) -> ItemInstance:
        """Create an item for a member (admin grants and tests). Respects the inventory cap.

        With `audit_actor` the grant is written to the admin audit log in the same transaction.
        """
        created_ts = int(time.time()) if now is None else now
        async with self._db.transaction() as conn:
            if await count_items(conn, user_id) >= inventory_cap:
                raise InventoryFullError(inventory_cap)
            item_id = await insert_item(conn, user_id, drop, created_ts)
            if audit_actor is not None:
                await record_admin_action(
                    conn,
                    audit_actor,
                    "grant_item",
                    user_id,
                    {"item": drop.content_id, "item_id": item_id},
                    now=created_ts,
                )
            created = await fetch_item(conn, item_id)
        assert created is not None
        return created

    # ------------------------------------------------------------------ equipping

    async def equip_item(
        self, user_id: int, item_id: int, *, level: int, items: Mapping[str, ItemDef]
    ) -> ItemInstance:
        """Wear an item you own. Replaces whatever is in that slot. Raises typed errors."""
        async with self._db.transaction() as conn:
            item = await fetch_item(conn, item_id)
            definition = items.get(item.content_id) if item else None
            if item is None or item.owner_id != user_id or definition is None:
                raise GearNotFoundError()
            if level < definition.req_level:
                raise GearLevelTooLowError(definition.name, definition.req_level, level)
            await conn.execute(
                "DELETE FROM rpg_equipment WHERE user_id = ? AND slot = ?;",
                (user_id, definition.slot),
            )
            await conn.execute(
                "INSERT OR REPLACE INTO rpg_equipment (user_id, slot, item_id) VALUES (?, ?, ?);",
                (user_id, definition.slot, item_id),
            )
            worn = await fetch_item(conn, item_id)
        assert worn is not None
        return worn

    async def unequip_item(self, user_id: int, item_id: int) -> bool:
        """Take an item off. False if it was not equipped by this member."""
        async with self._db.transaction() as conn:
            cursor = await conn.execute(
                "DELETE FROM rpg_equipment WHERE user_id = ? AND item_id = ?;", (user_id, item_id)
            )
            return cursor.rowcount == 1

    async def unequip_slot(self, user_id: int, slot: str) -> bool:
        async with self._db.transaction() as conn:
            cursor = await conn.execute(
                "DELETE FROM rpg_equipment WHERE user_id = ? AND slot = ?;", (user_id, slot)
            )
            return cursor.rowcount == 1

    # ------------------------------------------------------------------ repairing

    async def repair_items(
        self,
        user_id: int,
        item_ids: Optional[Sequence[int]],
        *,
        items: Mapping[str, ItemDef],
        rules: GearRules,
        welcome_bonus: int,
    ) -> RepairResult:
        """Repair some of a member's items (all of them if `item_ids` is None) in one payment."""
        async with self._db.transaction() as conn:
            owned = await fetch_owned(conn, user_id)
            wanted = set(item_ids) if item_ids is not None else None
            if wanted is not None and not wanted <= {i.id for i in owned}:
                raise GearNotFoundError()
            to_fix: list[tuple[ItemInstance, ItemDef, int]] = []
            for item in owned:
                definition = items.get(item.content_id)
                if definition is None or (wanted is not None and item.id not in wanted):
                    continue
                cost = gear.repair_cost(definition, item.durability, rules)
                if cost > 0:
                    to_fix.append((item, definition, cost))
            if not to_fix:
                raise NothingToRepairError()
            total = sum(cost for _, _, cost in to_fix)
            await self._db.debit_within_transaction(user_id, total, "rpg_repair", welcome_bonus)
            for item, definition, _ in to_fix:
                await conn.execute(
                    "UPDATE rpg_items SET durability = ? WHERE id = ?;",
                    (definition.max_durability, item.id),
                )
        return RepairResult(cost=total, repaired=len(to_fix))

    # ------------------------------------------------------------------ shop and sales

    async def buy_shop_item(
        self,
        user_id: int,
        drop: ItemDrop,
        *,
        price: int,
        day: str,
        daily_cap: int,
        inventory_cap: int,
        welcome_bonus: int,
        now: Optional[int] = None,
    ) -> ItemInstance:
        """Buy one item: checks the daily cap and inventory, takes the Renown, adds the item."""
        created_ts = int(time.time()) if now is None else now
        async with self._db.transaction() as conn:
            async with conn.execute(
                "SELECT count FROM rpg_shop_purchases WHERE user_id = ? AND day_key = ?;",
                (user_id, day),
            ) as cursor:
                row = await cursor.fetchone()
            bought = int(row[0]) if row else 0
            if bought >= daily_cap:
                raise ShopLimitError(daily_cap)
            if await count_items(conn, user_id) >= inventory_cap:
                raise InventoryFullError(inventory_cap)
            await self._db.debit_within_transaction(user_id, price, "rpg_shop", welcome_bonus)
            item_id = await insert_item(conn, user_id, drop, created_ts)
            await conn.execute(
                "INSERT INTO rpg_shop_purchases (user_id, day_key, count) VALUES (?, ?, 1) "
                "ON CONFLICT(user_id, day_key) DO UPDATE SET count = count + 1;",
                (user_id, day),
            )
            bought_item = await fetch_item(conn, item_id)
        assert bought_item is not None
        return bought_item

    async def sell_item(
        self,
        user_id: int,
        item_id: int,
        *,
        items: Mapping[str, ItemDef],
        rules: GearRules,
        welcome_bonus: int,
    ) -> int:
        """Sell an unequipped item to the guild. Returns the Renown paid."""
        async with self._db.transaction() as conn:
            item = await fetch_item(conn, item_id)
            if item is None or item.owner_id != user_id:
                raise GearNotFoundError()
            if item.equipped_slot is not None:
                raise ItemEquippedError()
            definition = items.get(item.content_id)
            value = gear.sell_value(definition, rules) if definition else 1
            await conn.execute("DELETE FROM rpg_items WHERE id = ?;", (item_id,))
            await self._db.credit_within_transaction(user_id, value, "rpg_sale", welcome_bonus)
        return value

    async def commit_trade(
        self,
        *,
        user_a: int,
        user_b: int,
        items_a: Sequence[int],
        items_b: Sequence[int],
        renown_a: int,
        renown_b: int,
        inventory_cap: int,
        welcome_bonus: int,
        fighting: Collection[int] = (),
        now: Optional[int] = None,
    ) -> TradeResult:
        """Swap the offers of two members, all or nothing.

        `items_a` / `renown_a` are what A gives to B, and the reverse. Everything is re-checked
        inside one transaction: neither side in a fight, both have characters, every item still
        belongs to its giver and is unequipped, both can afford their Renown and neither ends up
        over the inventory cap. Any failure raises a typed error and changes nothing.
        """
        if user_a == user_b:
            raise ValueError("A trade needs two different members.")
        ids = list(items_a) + list(items_b)
        if len(set(ids)) != len(ids):
            raise ValueError("An item can only appear once in a trade.")
        if not ids and renown_a <= 0 and renown_b <= 0:
            raise TradeNothingError()
        ts = int(time.time()) if now is None else now
        async with self._db.transaction() as conn:
            for user in (user_a, user_b):
                if user in fighting or await _in_open_fight(conn, user):
                    raise TradeFightingError(user)
                async with conn.execute(
                    "SELECT 1 FROM rpg_characters WHERE user_id = ?;", (user,)
                ) as cursor:
                    if await cursor.fetchone() is None:
                        raise NoCharacterError(own=False)
            gone: list[int] = []
            moved: dict[int, ItemInstance] = {}
            for giver, item_ids in ((user_a, items_a), (user_b, items_b)):
                for item_id in item_ids:
                    item = await fetch_item(conn, item_id)
                    if item is None or item.owner_id != giver or item.equipped_slot is not None:
                        gone.append(item_id)
                    else:
                        moved[item_id] = item
            if gone:
                raise TradeItemsGoneError(gone)
            count_a, count_b = await count_items(conn, user_a), await count_items(conn, user_b)
            if count_a - len(items_a) + len(items_b) > inventory_cap:
                raise TradeRoomError(user_a, inventory_cap)
            if count_b - len(items_b) + len(items_a) > inventory_cap:
                raise TradeRoomError(user_b, inventory_cap)
            for payer, amount in ((user_a, renown_a), (user_b, renown_b)):
                if amount > 0:
                    wallet = await self._db.get_wallet(payer)
                    balance = wallet.balance if wallet else 0
                    if balance < amount:
                        raise TradeFundsError(payer, amount, balance)
            gift = (not items_a and renown_a <= 0) != (not items_b and renown_b <= 0)
            reason = "rpg_gift" if gift else "rpg_trade"
            if renown_a > 0:
                await self._db.debit_within_transaction(
                    user_a, renown_a, reason, welcome_bonus, action="trade"
                )
            if renown_b > 0:
                await self._db.debit_within_transaction(
                    user_b, renown_b, reason, welcome_bonus, action="trade"
                )
            if renown_a > 0:
                await self._db.credit_within_transaction(user_b, renown_a, reason, welcome_bonus)
            if renown_b > 0:
                await self._db.credit_within_transaction(user_a, renown_b, reason, welcome_bonus)
            for giver, receiver, item_ids in ((user_a, user_b, items_a), (user_b, user_a, items_b)):
                for item_id in item_ids:
                    cursor = await conn.execute(
                        "UPDATE rpg_items SET owner_id = ? WHERE id = ? AND owner_id = ?;",
                        (receiver, item_id, giver),
                    )
                    if cursor.rowcount != 1:
                        raise TradeItemsGoneError([item_id])
            detail = {
                "kind": reason,
                "a": {"items": list(items_a), "renown": max(0, renown_a)},
                "b": {"items": list(items_b), "renown": max(0, renown_b)},
            }
            cursor = await conn.execute(
                "INSERT INTO rpg_trades (ts, user_a, user_b, detail) VALUES (?, ?, ?, ?);",
                (ts, user_a, user_b, json.dumps(detail)),
            )
            trade_id = cursor.lastrowid
            assert trade_id is not None
            moved_items = {i: await fetch_item(conn, i) for i in moved}
        to_b = [moved_items[i] for i in items_a if moved_items[i] is not None]
        to_a = [moved_items[i] for i in items_b if moved_items[i] is not None]
        return TradeResult(trade_id, to_a, to_b, reason)  # type: ignore[arg-type]


async def _in_open_fight(conn: aiosqlite.Connection, user_id: int) -> bool:
    async with conn.execute("SELECT participants FROM rpg_fights WHERE status = 'open';") as cursor:
        for (raw,) in await cursor.fetchall():
            if str(user_id) in json.loads(raw):
                return True
    return False
