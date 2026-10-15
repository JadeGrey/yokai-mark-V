"""Tests for the /gear commands."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest
from discord.ext import commands

from yokai.cogs.gear import GearCog
from yokai.rpg.gear_store import ItemDrop
from yokai.rpg.models import ItemInstance
from yokai.rpg.runtime import RpgRuntime
from yokai.storage.db import Database
from yokai.ui.rpg_views import InventoryPaginator

USER = 111


async def make_world(tmp_path: Path, *, cash: int = 5000) -> tuple[Database, RpgRuntime, GearCog]:
    db = Database(tmp_path / "gearcog.db")
    await db.initialize()
    runtime = RpgRuntime.create(db, Path("content/rpg"))
    await runtime.store.create_character(USER, "E", now=1)
    if cash:
        await db.credit_wallet(USER, cash, "test", welcome_bonus=0)
    bot = MagicMock()
    bot.user = None
    bot.rpg = runtime
    bot.db = db
    bot.config.rpg_tz = "UTC"
    bot.config.rpg_shop_daily_cap = 3
    bot.config.economy_welcome_bonus = 0
    return db, runtime, GearCog(bot)


def make_interaction(user_id: int = USER) -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    interaction.user = user
    interaction.response.defer = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)
    return interaction


async def give(
    runtime: RpgRuntime, content_id: str, *, durability: Optional[int] = None, user: int = USER
) -> ItemInstance:
    definition = runtime.content.items[content_id]
    return await runtime.gear.grant_item(
        user,
        ItemDrop(
            content_id,
            definition.stats,
            definition.max_durability if durability is None else durability,
        ),
        inventory_cap=60,
        now=1,
    )


async def run(coro_factory: Any) -> Any:
    """Run a command with `send` patched; returns the mock so the reply can be inspected."""
    with patch("yokai.cogs.gear.send", new=AsyncMock()) as sent:
        await coro_factory()
    return sent


def reply(sent: Any) -> discord.Embed:
    return sent.await_args.kwargs["embed"]


# ---------------------------------------------------------------------------- inventory


@pytest.mark.asyncio
async def test_commands_need_a_character(tmp_path: Path) -> None:
    db, runtime, cog = await make_world(tmp_path)
    try:
        for command, kwargs in (
            (cog.inventory, {"page": None}),
            (cog.equip, {"item": "1"}),
            (cog.repair, {"item": None}),
            (cog.sell, {"item": "1"}),
            (cog.shop, {}),
            (cog.buy, {"item": "x"}),
        ):
            interaction = make_interaction(user_id=999)
            sent = await run(lambda: command.callback(cog, interaction, **kwargs))
            assert "No adventurer yet" in (reply(sent).title or "")
            interaction.response.defer.assert_not_awaited()
        unequip = make_interaction(user_id=999)
        sent = await run(lambda: cog.unequip.callback(cog, unequip, MagicMock(value="weapon")))
        assert "No adventurer yet" in (reply(sent).title or "")
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_inventory_lists_items_with_equipped_first_and_paginates(tmp_path: Path) -> None:
    db, runtime, cog = await make_world(tmp_path)
    try:
        worn = await give(runtime, "oni_weapon_t1_common")
        await runtime.gear.equip_item(USER, worn.id, level=60, items=runtime.content.items)
        for _ in range(9):
            await give(runtime, "oni_head_t1_common", durability=0)
        high = await give(runtime, "oni_weapon_t3_common")
        interaction = make_interaction()
        sent = await run(lambda: cog.inventory.callback(cog, interaction, page=None))

        interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        assert sent.await_args.kwargs["ephemeral"] is True
        embed = reply(sent)
        assert "**11/60**" in (embed.description or "")
        lines = (embed.description or "").splitlines()[1:]
        assert len(lines) == 8 and "(equipped)" in lines[0] and f"`#{worn.id}`" in lines[0]
        assert "Page 1/2" in (embed.footer.text or "")
        view: InventoryPaginator = sent.await_args.kwargs["view"]
        assert not view.next_button.disabled and view.prev_button.disabled

        nxt = make_interaction()
        nxt.response.edit_message = AsyncMock()
        await view.next_button.callback(nxt)
        second = nxt.response.edit_message.await_args.kwargs["embed"]
        assert "Page 2/2" in (second.footer.text or "") and "broken" in (second.description or "")
        assert view.next_button.disabled and not view.prev_button.disabled
        assert f"`#{high.id}`" in (embed.description or "") + (second.description or "")
        assert "too high a level" in (embed.description or "") + (second.description or "")
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_an_empty_inventory_points_at_the_shop(tmp_path: Path) -> None:
    db, runtime, cog = await make_world(tmp_path)
    try:
        sent = await run(lambda: cog.inventory.callback(cog, make_interaction(), page=None))
        assert "/gear shop" in (reply(sent).description or "")
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_inventory_tolerates_items_whose_content_was_removed(tmp_path: Path) -> None:
    db, runtime, cog = await make_world(tmp_path)
    try:
        await runtime.gear.grant_item(
            USER, ItemDrop("deleted_item", runtime.content.items["oni_head_t1_common"].stats, 5),
            inventory_cap=60,
        )  # fmt: skip
        sent = await run(lambda: cog.inventory.callback(cog, make_interaction(), page=None))
        assert r"deleted\_item" in (reply(sent).description or "")
    finally:
        await db.close()


# ------------------------------------------------------------------------------- equip


@pytest.mark.asyncio
async def test_equip_wears_the_item_and_shows_set_bonuses(tmp_path: Path) -> None:
    db, runtime, cog = await make_world(tmp_path)
    try:
        weapon = await give(runtime, "oni_weapon_t1_common")
        head = await give(runtime, "oni_head_t1_common")
        await run(lambda: cog.equip.callback(cog, make_interaction(), item=str(weapon.id)))
        sent = await run(lambda: cog.equip.callback(cog, make_interaction(), item=f"#{head.id}"))
        embed = reply(sent)
        assert "Equipped in the head slot" in (embed.title or "")
        assert "Worn Oni Horned Mask" in (embed.description or "")
        assert "Oni \\(2\\)" in (embed.description or "") or "Oni (2)" in (embed.description or "")
        assert {i.id for i in await runtime.gear.equipped_items(USER)} == {weapon.id, head.id}
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_equip_says_so_for_a_broken_item(tmp_path: Path) -> None:
    db, runtime, cog = await make_world(tmp_path)
    try:
        item = await give(runtime, "oni_weapon_t1_common", durability=0)
        sent = await run(lambda: cog.equip.callback(cog, make_interaction(), item=str(item.id)))
        assert "counts for nothing" in (reply(sent).description or "")
    finally:
        await db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["", "abc", "-4", "99999999999999999", "1.5"])
async def test_equip_rejects_nonsense_ids(tmp_path: Path, bad: str) -> None:
    db, runtime, cog = await make_world(tmp_path)
    try:
        sent = await run(lambda: cog.equip.callback(cog, make_interaction(), item=bad))
        assert "No such item" in (reply(sent).title or "")
        assert sent.await_args.kwargs["ephemeral"] is True
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_equip_refuses_other_peoples_items_and_high_level_gear(tmp_path: Path) -> None:
    db, runtime, cog = await make_world(tmp_path)
    try:
        await runtime.store.create_character(222, "E", now=1)
        theirs = await give(runtime, "oni_weapon_t1_common", user=222)
        sent = await run(lambda: cog.equip.callback(cog, make_interaction(), item=str(theirs.id)))
        assert "No such item" in (reply(sent).title or "")
        high = await give(runtime, "oni_weapon_t3_common")
        sent = await run(lambda: cog.equip.callback(cog, make_interaction(), item=str(high.id)))
        assert "Level too low" in (reply(sent).title or "")
        assert await runtime.gear.equipped_items(USER) == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_gear_cannot_change_during_a_fight(tmp_path: Path) -> None:
    """Review Focus 7."""
    db, runtime, cog = await make_world(tmp_path)
    try:
        item = await give(runtime, "oni_weapon_t1_common", durability=10)
        runtime.active_users.add(USER)
        for command, kwargs in (
            (cog.equip, {"item": str(item.id)}),
            (cog.repair, {"item": None}),
            (cog.sell, {"item": str(item.id)}),
        ):
            sent = await run(lambda: command.callback(cog, make_interaction(), **kwargs))
            assert "Gear is locked" in (reply(sent).title or "")
        sent = await run(
            lambda: cog.unequip.callback(cog, make_interaction(), MagicMock(value="weapon"))
        )
        assert "Gear is locked" in (reply(sent).title or "")
        owned = await runtime.gear.get_item(item.id)
        assert owned is not None and owned.durability == 10 and owned.equipped_slot is None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_equip_autocomplete_offers_only_wearable_unequipped_items(tmp_path: Path) -> None:
    db, runtime, cog = await make_world(tmp_path)
    try:
        spare = await give(runtime, "oni_head_t1_common")
        worn = await give(runtime, "oni_weapon_t1_common")
        await give(runtime, "oni_weapon_t3_common")  # needs level 24
        await runtime.gear.equip_item(USER, worn.id, level=60, items=runtime.content.items)
        choices = await cog.equip_autocomplete(make_interaction(), "")
        assert [c.value for c in choices] == [str(spare.id)]
        assert f"#{spare.id}" in choices[0].name and "head" in choices[0].name
        assert await cog.equip_autocomplete(make_interaction(), "kanabo") == []
        assert [c.value for c in await cog.equip_autocomplete(make_interaction(), "mask")] == [
            str(spare.id)
        ]
        assert [
            c.value for c in await cog.equip_autocomplete(make_interaction(), str(spare.id))
        ] == [str(spare.id)]
        assert await cog.equip_autocomplete(make_interaction(999), "") == []
    finally:
        await db.close()


# ----------------------------------------------------------------------------- unequip


@pytest.mark.asyncio
async def test_unequip_empties_the_slot_once(tmp_path: Path) -> None:
    db, runtime, cog = await make_world(tmp_path)
    try:
        item = await give(runtime, "oni_weapon_t1_common")
        await runtime.gear.equip_item(USER, item.id, level=60, items=runtime.content.items)
        slot = MagicMock(value="weapon")
        sent = await run(lambda: cog.unequip.callback(cog, make_interaction(), slot))
        assert "Slot emptied" in (reply(sent).title or "")
        sent = await run(lambda: cog.unequip.callback(cog, make_interaction(), slot))
        assert "Nothing to remove" in (reply(sent).title or "")
    finally:
        await db.close()


# ------------------------------------------------------------------------------ repair


@pytest.mark.asyncio
async def test_repair_all_and_one(tmp_path: Path) -> None:
    db, runtime, cog = await make_world(tmp_path, cash=1000)
    try:
        a = await give(runtime, "oni_weapon_t3_common", durability=0)
        b = await give(runtime, "oni_head_t3_common", durability=0)
        sent = await run(lambda: cog.repair.callback(cog, make_interaction(), item=str(a.id)))
        assert "Repaired **1** item(s) for **101** Renown" in (reply(sent).description or "")
        sent = await run(lambda: cog.repair.callback(cog, make_interaction(), item=None))
        assert "Repaired **1** item(s)" in (reply(sent).description or "")
        for item in (a, b):
            fixed = await runtime.gear.get_item(item.id)
            assert fixed is not None and fixed.durability == 100
        sent = await run(lambda: cog.repair.callback(cog, make_interaction(), item=None))
        assert "Nothing to repair" in (reply(sent).title or "")
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_repair_you_cannot_afford_is_a_private_error(tmp_path: Path) -> None:
    db, runtime, cog = await make_world(tmp_path, cash=0)
    try:
        await give(runtime, "oni_weapon_t3_common", durability=0)
        sent = await run(lambda: cog.repair.callback(cog, make_interaction(), item=None))
        assert sent.await_args.kwargs["ephemeral"] is True
        assert reply(sent).color.value != 0x00E676
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_repair_autocomplete_offers_only_damaged_items(tmp_path: Path) -> None:
    db, runtime, cog = await make_world(tmp_path)
    try:
        await give(runtime, "oni_weapon_t1_common")
        hurt = await give(runtime, "oni_head_t1_common", durability=40)
        choices = await cog.repair_autocomplete(make_interaction(), "")
        assert [c.value for c in choices] == [str(hurt.id)] and "40%" in choices[0].name
    finally:
        await db.close()


# -------------------------------------------------------------------------------- sell


@pytest.mark.asyncio
async def test_sell_pays_and_names_the_item(tmp_path: Path) -> None:
    db, runtime, cog = await make_world(tmp_path, cash=0)
    try:
        item = await give(runtime, "oni_weapon_t3_common")
        sent = await run(lambda: cog.sell.callback(cog, make_interaction(), item=str(item.id)))
        assert "Tempered Oni Kanabo" in (reply(sent).description or "")
        assert "**86** Renown" in (reply(sent).description or "")
        wallet = await db.get_wallet(USER)
        assert wallet is not None and wallet.balance == 86
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_sell_refuses_equipped_and_missing_items(tmp_path: Path) -> None:
    db, runtime, cog = await make_world(tmp_path)
    try:
        item = await give(runtime, "oni_weapon_t1_common")
        await runtime.gear.equip_item(USER, item.id, level=60, items=runtime.content.items)
        sent = await run(lambda: cog.sell.callback(cog, make_interaction(), item=str(item.id)))
        assert "Item is equipped" in (reply(sent).title or "")
        sent = await run(lambda: cog.sell.callback(cog, make_interaction(), item="424242"))
        assert "No such item" in (reply(sent).title or "")
        assert await cog.sell_autocomplete(make_interaction(), "") == []
    finally:
        await db.close()


# ----------------------------------------------------------------------- shop and buy


@pytest.mark.asyncio
async def test_shop_lists_each_tier_and_the_purchase_allowance(tmp_path: Path) -> None:
    db, runtime, cog = await make_world(tmp_path, cash=1234)
    try:
        interaction = make_interaction()
        sent = await run(lambda: cog.shop.callback(cog, interaction))
        interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        embed = reply(sent)
        assert [f.name for f in embed.fields] == [f"Level {t}+ gear" for t in (1, 12, 24, 38, 52)]
        assert "**1,234** Renown" in (embed.description or "")
        assert "**3/3**" in (embed.description or "")
        assert all(f.value.count("Renown") == 3 for f in embed.fields)
        again = await run(lambda: cog.shop.callback(cog, make_interaction()))
        assert [f.value for f in reply(again).fields] == [f.value for f in embed.fields]
    finally:
        await db.close()


async def first_stock_item(cog: GearCog, runtime: RpgRuntime):  # type: ignore[no-untyped-def]
    day, _ = cog._day()
    return (await cog._stock(runtime, day))[0]


@pytest.mark.asyncio
async def test_buy_charges_the_price_and_adds_a_rolled_item(tmp_path: Path) -> None:
    db, runtime, cog = await make_world(tmp_path, cash=5000)
    try:
        stock = await first_stock_item(cog, runtime)
        sent = await run(lambda: cog.buy.callback(cog, make_interaction(), item=stock.id))
        assert "Purchased" in (reply(sent).title or "")
        wallet = await db.get_wallet(USER)
        assert wallet is not None and wallet.balance == 5000 - stock.price
        (owned,) = await runtime.gear.list_items(USER)
        assert owned.content_id == stock.id and owned.durability == stock.max_durability
        shop = await run(lambda: cog.shop.callback(cog, make_interaction()))
        assert "**2/3**" in (reply(shop).description or "")
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_buy_refuses_items_not_in_stock(tmp_path: Path) -> None:
    db, runtime, cog = await make_world(tmp_path)
    try:
        day, _ = cog._day()
        stocked = {i.id for i in await cog._stock(runtime, day)}
        off = next(i for i in runtime.content.items.values() if i.id not in stocked)
        for wanted in (off.id, "nonsense", ""):
            sent = await run(lambda: cog.buy.callback(cog, make_interaction(), item=wanted))
            assert "Not in stock" in (reply(sent).title or "")
        wallet = await db.get_wallet(USER)
        assert wallet is not None and wallet.balance == 5000
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_buy_enforces_the_daily_cap_and_the_wallet(tmp_path: Path) -> None:
    db, runtime, cog = await make_world(tmp_path, cash=200_000)
    try:
        stock = await first_stock_item(cog, runtime)
        for _ in range(3):
            await run(lambda: cog.buy.callback(cog, make_interaction(), item=stock.id))
        sent = await run(lambda: cog.buy.callback(cog, make_interaction(), item=stock.id))
        assert "Shop limit reached" in (reply(sent).title or "")
        assert len(await runtime.gear.list_items(USER)) == 3

    finally:
        await db.close()


@pytest.mark.asyncio
async def test_buy_without_enough_renown_changes_nothing(tmp_path: Path) -> None:
    db, runtime, cog = await make_world(tmp_path, cash=1)
    try:
        stock = max(await cog._stock(runtime, cog._day()[0]), key=lambda i: i.price)
        sent = await run(lambda: cog.buy.callback(cog, make_interaction(), item=stock.id))
        assert "Not enough Renown" in (reply(sent).title or "")
        assert "to spend" in (reply(sent).description or "")
        assert await runtime.gear.list_items(USER) == []
        assert await runtime.gear.purchases_today(USER, cog._day()[0]) == 0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_buy_autocomplete_offers_todays_stock(tmp_path: Path) -> None:
    db, runtime, cog = await make_world(tmp_path)
    try:
        stock = await cog._stock(runtime, cog._day()[0])
        choices = await cog.buy_autocomplete(make_interaction(), "")
        assert {c.value for c in choices} == {i.id for i in stock[:25]}
        assert all(len(c.name) <= 100 for c in choices)
        word = stock[0].name.split()[-1]
        narrowed = await cog.buy_autocomplete(make_interaction(), word.upper())
        assert stock[0].id in {c.value for c in narrowed}
    finally:
        await db.close()


# --------------------------------------------------------------------------- wiring


@pytest.mark.asyncio
async def test_the_cog_registers_every_gear_command(tmp_path: Path) -> None:
    db, runtime, _ = await make_world(tmp_path)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    try:
        bot.rpg = runtime  # type: ignore[attr-defined]
        await bot.add_cog(GearCog(bot))  # type: ignore[arg-type]
        names = {c.qualified_name for c in bot.tree.walk_commands()}
        assert {
            "gear inventory",
            "gear equip",
            "gear unequip",
            "gear repair",
            "gear shop",
            "gear buy",
            "gear sell",
        } <= names
    finally:
        await bot.close()
        await db.close()
