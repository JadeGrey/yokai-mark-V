"""House-take rules and the lottery pot ledger."""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from yokai.gambling.pot import POOLED_GAMES, bet_house_take, lobby_house_take
from yokai.storage.db import (
    CURRENT_SCHEMA_VERSION,
    MIGRATION_V1,
    MIGRATION_V2,
    MIGRATION_V3,
    MIGRATION_V4,
    MIGRATION_V5,
    Database,
)


def test_pooled_games_are_the_ones_where_players_win_each_others_stakes() -> None:
    assert {"duel", "jackpot", "poker"} <= POOLED_GAMES
    assert "slots" not in POOLED_GAMES


def test_a_lost_house_banked_bet_goes_to_the_pot() -> None:
    assert bet_house_take("slots", 100, 0) == 100


def test_a_win_or_a_push_adds_nothing() -> None:
    assert bet_house_take("slots", 100, 250) == 0
    assert bet_house_take("slots", 100, 100) == 0


def test_a_partial_return_leaves_the_difference() -> None:
    assert bet_house_take("roulette", 100, 40) == 60


def test_a_bet_in_a_pooled_game_has_no_take_of_its_own() -> None:
    assert bet_house_take("duel", 100, 0) == 0


def test_a_pooled_lobby_adds_only_the_rake() -> None:
    assert lobby_house_take("duel", [50, 50], [98, 0]) == 2
    assert lobby_house_take("duel", [50, 50], [100, 0]) == 0


def test_a_house_banked_lobby_adds_each_losing_stake() -> None:
    assert lobby_house_take("crash", [100, 100, 100], [250, 0, 100]) == 100


def test_lobby_take_rejects_mismatched_lists() -> None:
    with pytest.raises(ValueError):
        lobby_house_take("duel", [50, 50], [100])


async def _db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "pot.db")
    await db.initialize()
    return db


async def _tables(db: Database) -> set[str]:
    async with db.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table';") as cur:
        return {row[0] for row in await cur.fetchall()}


@pytest.mark.asyncio
async def test_migration_v6_creates_the_tables_and_seeds_an_empty_pot(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        assert CURRENT_SCHEMA_VERSION == 6
        names = await _tables(db)
        assert {"multiplayer_channels", "lottery_state", "lottery_pot_entries"} <= names
        assert await db.get_pot() == 0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_v5_database_upgrades_to_v6_keeping_its_rows(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    raw = await aiosqlite.connect(path)
    for script in (MIGRATION_V1, MIGRATION_V2, MIGRATION_V3, MIGRATION_V4, MIGRATION_V5):
        await raw.executescript(script)
    await raw.execute("PRAGMA user_version = 5;")
    await raw.execute(
        "INSERT INTO wallets (user_id, balance, daily_streak, created_ts) VALUES (7, 321, 0, 1);"
    )
    await raw.commit()
    await raw.close()

    db = Database(path)
    await db.initialize()
    try:
        wallet = await db.get_wallet(7)
        assert wallet is not None and wallet.balance == 321
        assert await db.get_pot() == 0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_losing_a_solo_bet_feeds_the_pot(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        await db.ensure_wallet(1, 1000)
        bet = await db.place_bet(1, 100, "slots", 0)
        await db.settle_bet(bet, 0)
        assert await db.get_pot() == 100
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_win_a_push_and_a_refund_add_nothing(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        await db.ensure_wallet(1, 1000)
        win = await db.place_bet(1, 100, "slots", 0)
        push = await db.place_bet(1, 100, "slots", 0)
        refunded = await db.place_bet(1, 100, "slots", 0)
        await db.settle_bet(win, 250)
        await db.settle_bet(push, 100)
        await db.refund_bet(refunded)
        assert await db.get_pot() == 0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_partial_return_adds_the_difference(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        await db.ensure_wallet(1, 1000)
        bet = await db.place_bet(1, 100, "roulette", 0)
        await db.settle_bet(bet, 40)
        assert await db.get_pot() == 60
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_settling_a_pooled_game_bet_on_its_own_adds_nothing(tmp_path: Path) -> None:
    """A duel stake is settled by settle_lobby; settle_bet must not double count it."""
    db = await _db(tmp_path)
    try:
        await db.ensure_wallet(1, 1000)
        bet = await db.place_bet(1, 100, "duel", 0)
        await db.settle_bet(bet, 0)
        assert await db.get_pot() == 0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_pot_entries_add_up_to_the_pot(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        await db.ensure_wallet(1, 1000)
        for stake in (100, 40):
            bet = await db.place_bet(1, stake, "slots", 0)
            await db.settle_bet(bet, 0)
        async with db.conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(amount), 0), MIN(source) FROM lottery_pot_entries;"
        ) as cur:
            count, total, source = await cur.fetchone()  # type: ignore[misc]
        assert (count, total, source) == (2, 140, "slots")
        assert await db.get_pot() == 140
    finally:
        await db.close()
