"""Tests for the RPG schema, the public transaction hook, the store and the runtime."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from yokai.rpg.runtime import RpgRuntime
from yokai.rpg.store import RpgStore
from yokai.storage.db import CURRENT_SCHEMA_VERSION, Database


async def fresh_db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "rpg_test.db")
    await db.initialize()
    return db


@pytest.mark.asyncio
async def test_schema_is_at_least_v9_and_has_the_characters_table(tmp_path: Path) -> None:
    db = await fresh_db(tmp_path)
    try:
        assert CURRENT_SCHEMA_VERSION >= 9
        async with db.conn.execute("PRAGMA user_version;") as cursor:
            row = await cursor.fetchone()
        assert row is not None and row[0] == CURRENT_SCHEMA_VERSION
        async with db.conn.execute("PRAGMA table_info(rpg_characters);") as cursor:
            columns = {r[1] for r in await cursor.fetchall()}
        assert columns == {
            "user_id",
            "level",
            "xp",
            "rank",
            "battles_won",
            "battles_lost",
            "created_ts",
        }
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_v8_database_upgrades_to_v9_without_losing_other_data(tmp_path: Path) -> None:
    db = await fresh_db(tmp_path)
    try:
        await db.credit_wallet(1, 50, "test", welcome_bonus=200)
        await db.conn.execute("DROP TABLE rpg_characters;")
        await db.conn.execute("PRAGMA user_version = 8;")
        await db.conn.commit()
    finally:
        await db.close()

    db = Database(tmp_path / "rpg_test.db")
    await db.initialize()
    try:
        wallet = await db.get_wallet(1)
        assert wallet is not None and wallet.balance == 250
        created = await RpgStore(db).create_character(1, "E", now=100)
        assert created[1] is True
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_transaction_commits_when_the_block_succeeds(tmp_path: Path) -> None:
    db = await fresh_db(tmp_path)
    try:
        async with db.transaction() as conn:
            await conn.execute(
                "INSERT INTO rpg_characters (user_id, rank, created_ts) VALUES (7, 'E', 1);"
            )
        assert await RpgStore(db).get_character(7) is not None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_transaction_rolls_back_when_the_block_raises(tmp_path: Path) -> None:
    db = await fresh_db(tmp_path)
    try:
        with pytest.raises(RuntimeError):
            async with db.transaction() as conn:
                await conn.execute(
                    "INSERT INTO rpg_characters (user_id, rank, created_ts) VALUES (7, 'E', 1);"
                )
                raise RuntimeError("boom")
        assert await RpgStore(db).get_character(7) is None
        # The write lock was released and no transaction was left open.
        await RpgStore(db).create_character(8, "E", now=1)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_get_character_returns_none_when_absent(tmp_path: Path) -> None:
    db = await fresh_db(tmp_path)
    try:
        assert await RpgStore(db).get_character(123) is None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_create_character_starts_at_level_one_in_the_given_rank(tmp_path: Path) -> None:
    db = await fresh_db(tmp_path)
    try:
        character, created = await RpgStore(db).create_character(5, "E", now=1234)
        assert created is True
        assert (character.user_id, character.level, character.xp) == (5, 1, 0)
        assert (character.rank, character.battles_won, character.battles_lost) == ("E", 0, 0)
        assert character.created_ts == 1234
        assert await RpgStore(db).get_character(5) == character
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_creating_twice_keeps_the_original_character(tmp_path: Path) -> None:
    db = await fresh_db(tmp_path)
    try:
        store = RpgStore(db)
        first, created_first = await store.create_character(5, "E", now=1000)
        second, created_second = await store.create_character(5, "D", now=2000)
        assert created_first is True and created_second is False
        assert second == first and second.rank == "E" and second.created_ts == 1000
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_simultaneous_joins_create_exactly_one_character(tmp_path: Path) -> None:
    """Review Focus 1: two /guild join calls at once must not make two rows or two 'created'."""
    db = await fresh_db(tmp_path)
    try:
        store = RpgStore(db)
        results = await asyncio.gather(*[store.create_character(9, "E", now=500) for _ in range(8)])
        assert sum(1 for _, created in results if created) == 1
        assert len({character for character, _ in results}) == 1
        async with db.conn.execute("SELECT COUNT(*) FROM rpg_characters;") as cursor:
            row = await cursor.fetchone()
        assert row is not None and row[0] == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_runtime_create_loads_shipped_content_and_the_store(tmp_path: Path) -> None:
    db = await fresh_db(tmp_path)
    try:
        runtime = RpgRuntime.create(db, Path("content/rpg"))
        assert runtime.content.ranks[0].id == "E"
        assert isinstance(runtime.store, RpgStore)
    finally:
        await db.close()
