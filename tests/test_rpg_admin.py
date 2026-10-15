"""Tests for /admin rpg: grant, level, rank and reset, all audited."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from tests.moderation_helpers import fake_moderation
from yokai.cogs.admin import AdminCog
from yokai.rpg.gear_store import ItemDrop
from yokai.rpg.runtime import RpgRuntime
from yokai.storage.db import Database

OWNER = 999
ADMIN = 555
TARGET = 222


async def make_world(
    tmp_path: Path, *, character: bool = True
) -> tuple[Database, RpgRuntime, AdminCog]:
    db = Database(tmp_path / "admin.db")
    await db.initialize()
    runtime = RpgRuntime.create(db, Path("content/rpg"))
    if character:
        await runtime.store.create_character(TARGET, "E", now=1)
    bot = MagicMock()
    bot.user = None
    bot.rpg = runtime
    bot.config.owner_id = OWNER
    bot.moderation = fake_moderation(owner_id=OWNER, admins={ADMIN})
    cog = AdminCog(bot)
    return db, runtime, cog


def interaction(user_id: int = ADMIN) -> MagicMock:
    result = MagicMock(spec=discord.Interaction)
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    result.user = user
    result.response.defer = AsyncMock()
    return result


def member(user_id: int = TARGET, *, bot: bool = False) -> MagicMock:
    target = MagicMock(spec=discord.Member)
    target.id = user_id
    target.bot = bot
    return target


async def call(command: Any, cog: AdminCog, who: MagicMock, **kwargs: Any) -> Any:
    with patch("yokai.cogs.admin.send", new=AsyncMock()) as sent:
        await command.callback(cog, who, **kwargs)
    return sent


def reply(sent: Any) -> discord.Embed:
    return sent.await_args.kwargs["embed"]


async def audit_rows(db: Database) -> list[tuple[str, str, str, str]]:
    async with db.conn.execute(
        "SELECT actor, action, target, detail_json FROM admin_audit "
        "WHERE module = 'rpg' ORDER BY id;"
    ) as cursor:
        return [tuple(row) for row in await cursor.fetchall()]  # type: ignore[misc]


@pytest.mark.asyncio
async def test_the_rpg_group_hangs_off_admin(tmp_path: Path) -> None:
    db, _, cog = await make_world(tmp_path)
    try:
        assert AdminCog.rpg.parent is AdminCog.admin
        names = {c.name for c in AdminCog.rpg.commands}
        assert names == {"grant", "level", "rank", "reset"}
    finally:
        await db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command_name", "kwargs"),
    [
        ("rpg_grant", {"item": "oni_weapon_t1_common"}),
        ("rpg_level", {"level": 5}),
        ("rpg_rank", {"rank": "D"}),
        ("rpg_reset", {"confirm": True}),
    ],
)
async def test_every_rpg_admin_command_refuses_non_admins_and_bots(
    tmp_path: Path, command_name: str, kwargs: dict[str, Any]
) -> None:
    db, runtime, cog = await make_world(tmp_path)
    try:
        command = getattr(cog, command_name)
        sent = await call(command, cog, interaction(111), user=member(), **kwargs)
        assert "Admins only" in (reply(sent).title or "") and sent.await_args.kwargs["ephemeral"]
        sent = await call(command, cog, interaction(ADMIN), user=member(bot=True), **kwargs)
        assert "Bots can't" in (reply(sent).description or "")
        character = await runtime.store.get_character(TARGET)
        assert character is not None and character.level == 1 and character.rank == "E"
        assert await audit_rows(db) == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_the_commands_say_so_when_the_rpg_is_off(tmp_path: Path) -> None:
    db, runtime, cog = await make_world(tmp_path)
    try:
        cog.bot.rpg = None
        sent = await call(cog.rpg_level, cog, interaction(), user=member(), level=3)
        assert "switched off" in (reply(sent).description or "")
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_grant_creates_the_item_and_audits_it(tmp_path: Path) -> None:
    db, runtime, cog = await make_world(tmp_path)
    try:
        sent = await call(
            cog.rpg_grant, cog, interaction(OWNER), user=member(), item="oni_weapon_t3_rare"
        )
        assert "Item granted" in (reply(sent).title or "")
        (item,) = await runtime.gear.list_items(TARGET)
        assert item.content_id == "oni_weapon_t3_rare" and item.durability == 125
        (row,) = await audit_rows(db)
        assert row[0] == str(OWNER) and row[1] == "grant_item" and row[2] == str(TARGET)
        assert json.loads(row[3]) == {"item": "oni_weapon_t3_rare", "item_id": item.id}
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_grant_checks_the_item_the_character_and_the_inventory(tmp_path: Path) -> None:
    db, runtime, cog = await make_world(tmp_path)
    try:
        sent = await call(cog.rpg_grant, cog, interaction(), user=member(), item="nope")
        assert "no item with that id" in (reply(sent).description or "")
        sent = await call(
            cog.rpg_grant, cog, interaction(), user=member(777), item="oni_weapon_t1_common"
        )
        assert "No adventurer yet" in (reply(sent).title or "")
        cap = runtime.content.balance.gear.inventory_cap
        for _ in range(cap):
            definition = runtime.content.items["oni_head_t1_common"]
            await runtime.gear.grant_item(
                TARGET, ItemDrop(definition.id, definition.stats, 100), inventory_cap=cap
            )
        sent = await call(
            cog.rpg_grant, cog, interaction(), user=member(), item="oni_weapon_t1_common"
        )
        assert "Inventory full" in (reply(sent).title or "")
        assert len(await runtime.gear.list_items(TARGET)) == cap and await audit_rows(db) == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_grant_autocomplete_filters_by_name_or_id(tmp_path: Path) -> None:
    db, _, cog = await make_world(tmp_path)
    try:
        choices = await cog.rpg_grant_autocomplete(interaction(), "kanabo")
        assert choices and all("Kanabo" in c.name for c in choices) and len(choices) <= 25
        by_id = await cog.rpg_grant_autocomplete(interaction(), "oni_weapon_t5_epic")
        assert [c.value for c in by_id] == ["oni_weapon_t5_epic"]
        assert await cog.rpg_grant_autocomplete(interaction(), "zzzz") == []
        cog.bot.rpg = None
        assert await cog.rpg_grant_autocomplete(interaction(), "") == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_level_sets_clears_xp_clamps_and_audits(tmp_path: Path) -> None:
    db, runtime, cog = await make_world(tmp_path)
    try:
        async with db.transaction() as conn:
            await conn.execute("UPDATE rpg_characters SET xp = 50 WHERE user_id = ?;", (TARGET,))
        sent = await call(cog.rpg_level, cog, interaction(), user=member(), level=25)
        assert "level **25**" in (reply(sent).description or "")
        character = await runtime.store.get_character(TARGET)
        assert character is not None and (character.level, character.xp) == (25, 0)
        for asked, expected in ((0, 1), (-9, 1), (10_000, 60)):
            await call(cog.rpg_level, cog, interaction(), user=member(), level=asked)
            character = await runtime.store.get_character(TARGET)
            assert character is not None and character.level == expected
        rows = await audit_rows(db)
        assert [r[1] for r in rows] == ["set_level"] * 4
        assert json.loads(rows[0][3]) == {"from": 1, "to": 25}
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_level_for_someone_without_a_character_is_an_error(tmp_path: Path) -> None:
    db, _, cog = await make_world(tmp_path, character=False)
    try:
        sent = await call(cog.rpg_level, cog, interaction(), user=member(), level=5)
        assert "No adventurer yet" in (reply(sent).title or "")
        assert await audit_rows(db) == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_rank_accepts_known_ranks_in_any_case_and_audits(tmp_path: Path) -> None:
    db, runtime, cog = await make_world(tmp_path)
    try:
        sent = await call(cog.rpg_rank, cog, interaction(), user=member(), rank=" ss+ ")
        assert "rank **SS\\+**" in (reply(sent).description or "") or "SS+" in (
            reply(sent).description or ""
        )
        character = await runtime.store.get_character(TARGET)
        assert character is not None and character.rank == "SS+"
        sent = await call(cog.rpg_rank, cog, interaction(), user=member(), rank="Z")
        assert "isn't a guild rank" in (reply(sent).description or "")
        assert "E, D, C, B, A, SS, SS+" in (reply(sent).fields[0].value)
        (row,) = await audit_rows(db)
        assert row[1] == "set_rank" and json.loads(row[3]) == {"from": "E", "to": "SS+"}
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_rank_autocomplete_lists_ranks(tmp_path: Path) -> None:
    db, _, cog = await make_world(tmp_path)
    try:
        assert [c.value for c in await cog.rpg_rank_autocomplete(interaction(), "")] == [
            "E", "D", "C", "B", "A", "SS", "SS+",
        ]  # fmt: skip
        assert [c.value for c in await cog.rpg_rank_autocomplete(interaction(), "s")] == [
            "SS",
            "SS+",
        ]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_reset_needs_confirmation_and_deletes_the_character_and_gear(tmp_path: Path) -> None:
    db, runtime, cog = await make_world(tmp_path)
    try:
        await db.credit_wallet(TARGET, 300, "test", welcome_bonus=0)
        definition = runtime.content.items["oni_weapon_t1_common"]
        item = await runtime.gear.grant_item(
            TARGET, ItemDrop(definition.id, definition.stats, 100), inventory_cap=60
        )
        await runtime.gear.equip_item(TARGET, item.id, level=60, items=runtime.content.items)

        sent = await call(cog.rpg_reset, cog, interaction(), user=member(), confirm=False)
        assert "Nothing was deleted" in (reply(sent).description or "")
        assert await runtime.store.get_character(TARGET) is not None

        runtime.active_users.add(TARGET)
        sent = await call(cog.rpg_reset, cog, interaction(), user=member(), confirm=True)
        assert "in a fight" in (reply(sent).description or "")
        runtime.active_users.discard(TARGET)

        sent = await call(cog.rpg_reset, cog, interaction(), user=member(), confirm=True)
        assert "Adventurer reset" in (reply(sent).title or "")
        assert await runtime.store.get_character(TARGET) is None
        assert await runtime.gear.list_items(TARGET) == []
        assert await runtime.gear.equipped_items(TARGET) == []
        wallet = await db.get_wallet(TARGET)
        assert wallet is not None and wallet.balance == 300
        (row,) = await audit_rows(db)
        assert row[1] == "reset_character" and json.loads(row[3])["items"] == 1

        sent = await call(cog.rpg_reset, cog, interaction(), user=member(), confirm=True)
        assert "Nothing to reset" in (reply(sent).title or "")
        assert len(await audit_rows(db)) == 1
    finally:
        await db.close()
