"""Tests for wallet storage: migration, balances, daily claims, payments, and the leaderboard."""

from __future__ import annotations

import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from yokai.errors import DailyOnCooldownError, InsufficientFundsError
from yokai.storage.db import CURRENT_SCHEMA_VERSION, MIGRATION_V1, Database

WELCOME = 200


async def _fresh_db(tmp_dir: str) -> Database:
    db = Database(Path(tmp_dir) / "test_yokai.db")
    await db.initialize()
    return db


@pytest.mark.asyncio
async def test_schema_upgrades_to_v2_and_is_now_at_the_current_version() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = await _fresh_db(tmp_dir)
        async with db.conn.execute("PRAGMA user_version;") as cursor:
            row = await cursor.fetchone()
            assert row is not None and row[0] == CURRENT_SCHEMA_VERSION
            assert CURRENT_SCHEMA_VERSION >= 2
        await db.close()


@pytest.mark.asyncio
async def test_an_existing_v1_database_upgrades_without_losing_data() -> None:
    """Review Focus: a real owner's already-migrated-to-v1 database must upgrade cleanly."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "existing_v1.db"

        # Simulate a database already at schema v1, with real rows in it.
        v1_only = Database(db_path)
        v1_only._conn = None
        import aiosqlite

        conn = await aiosqlite.connect(db_path)
        await conn.executescript(MIGRATION_V1)
        await conn.execute("PRAGMA user_version = 1;")
        await conn.execute(
            "INSERT INTO command_log (ts, user_id, command) VALUES (1000, 42, 'play');"
        )
        await conn.commit()
        await conn.close()

        # Now open it through the normal Database, as a restart would.
        db = Database(db_path)
        await db.initialize()

        async with db.conn.execute("PRAGMA user_version;") as cursor:
            row = await cursor.fetchone()
            assert row is not None and row[0] == CURRENT_SCHEMA_VERSION

        async with db.conn.execute("SELECT user_id, command FROM command_log;") as cursor:
            rows = await cursor.fetchall()
            assert rows == [(42, "play")]

        # The new tables exist and are usable.
        wallet = await db.ensure_wallet(1, welcome_bonus=WELCOME)
        assert wallet.balance == WELCOME
        await db.close()


@pytest.mark.asyncio
async def test_ensure_wallet_grants_the_welcome_bonus_exactly_once() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = await _fresh_db(tmp_dir)
        first = await db.ensure_wallet(1, welcome_bonus=WELCOME)
        assert first.balance == WELCOME
        second = await db.ensure_wallet(1, welcome_bonus=WELCOME)
        assert second.balance == WELCOME  # not doubled
        await db.close()


@pytest.mark.asyncio
async def test_get_wallet_never_creates_one() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = await _fresh_db(tmp_dir)
        assert await db.get_wallet(999) is None
        await db.close()


@pytest.mark.asyncio
async def test_credit_wallet_auto_creates_with_welcome_bonus_then_adds() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = await _fresh_db(tmp_dir)
        balance = await db.credit_wallet(1, 50, reason="voice", welcome_bonus=WELCOME)
        assert balance == WELCOME + 50
        await db.close()


@pytest.mark.asyncio
async def test_pay_moves_renown_atomically_and_writes_both_transactions() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = await _fresh_db(tmp_dir)
        await db.ensure_wallet(1, welcome_bonus=100)
        sender_balance, recipient_balance = await db.pay(1, 2, 30, welcome_bonus=100)
        assert sender_balance == 70
        assert recipient_balance == 130  # recipient's own welcome bonus + the payment
        async with db.conn.execute(
            "SELECT reason, delta, counterparty_id FROM renown_transactions ORDER BY id;"
        ) as cursor:
            rows = await cursor.fetchall()
        reasons = {r[0]: (r[1], r[2]) for r in rows}
        assert reasons["pay_sent"] == (-30, 2)
        assert reasons["pay_received"] == (30, 1)
        await db.close()


@pytest.mark.asyncio
async def test_pay_raises_and_changes_nothing_when_sender_is_short() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = await _fresh_db(tmp_dir)
        await db.ensure_wallet(1, welcome_bonus=10)
        with pytest.raises(InsufficientFundsError):
            await db.pay(1, 2, 999, welcome_bonus=10)
        assert await db.get_wallet(2) is None  # recipient wallet was never created
        await db.close()


@pytest.mark.asyncio
async def test_claim_daily_pays_out_and_records_streak() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = await _fresh_db(tmp_dir)
        rng = MagicMock()
        rng.uniform.return_value = 1.0
        now = int(time.time())
        result = await db.claim_daily(1, base=100, welcome_bonus=0, now=now, rng=rng)
        assert result.payout == 100
        assert result.streak_day == 1
        assert result.new_balance == 100
        await db.close()


@pytest.mark.asyncio
async def test_claim_daily_raises_when_already_claimed_today() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = await _fresh_db(tmp_dir)
        rng = MagicMock()
        rng.uniform.return_value = 1.0
        now = int(time.time())
        await db.claim_daily(1, base=100, welcome_bonus=0, now=now, rng=rng)
        with pytest.raises(DailyOnCooldownError):
            await db.claim_daily(1, base=100, welcome_bonus=0, now=now + 10, rng=rng)
        wallet = await db.get_wallet(1)
        assert wallet is not None and wallet.balance == 100  # second claim did not pay out
        await db.close()


@pytest.mark.asyncio
async def test_admin_adjust_floors_a_large_deduction_at_zero() -> None:
    """Review Focus: an admin deduction larger than the balance must floor at 0, not go negative."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = await _fresh_db(tmp_dir)
        await db.ensure_wallet(1, welcome_bonus=50)
        applied, new_balance = await db.admin_adjust_wallet(1, delta=-9999, welcome_bonus=50)
        assert new_balance == 0
        assert applied == -50  # only the actual amount removed, not the requested -9999
        await db.close()


@pytest.mark.asyncio
async def test_admin_adjust_can_grant_above_zero() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = await _fresh_db(tmp_dir)
        applied, new_balance = await db.admin_adjust_wallet(1, delta=500, welcome_bonus=0)
        assert (applied, new_balance) == (500, 500)
        await db.close()


@pytest.mark.asyncio
async def test_leaderboard_orders_by_balance_descending_with_a_total_count() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = await _fresh_db(tmp_dir)
        await db.credit_wallet(1, 10, reason="voice", welcome_bonus=0)
        await db.credit_wallet(2, 30, reason="voice", welcome_bonus=0)
        await db.credit_wallet(3, 20, reason="voice", welcome_bonus=0)
        wallets, total = await db.get_leaderboard(limit=2, offset=0)
        assert total == 3
        assert [w.user_id for w in wallets] == [2, 3]
        page2, total2 = await db.get_leaderboard(limit=2, offset=2)
        assert total2 == 3
        assert [w.user_id for w in page2] == [1]
        await db.close()
