"""Tests for migration v3 and the bet escrow methods on Database."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest

from yokai.errors import BetNotOpenError, InsufficientFundsError
from yokai.storage.db import CURRENT_SCHEMA_VERSION, MIGRATION_V1, MIGRATION_V2, Database

WELCOME = 200


async def _fresh_db(tmp_dir: str) -> Database:
    db = Database(Path(tmp_dir) / "test_yokai.db")
    await db.initialize()
    return db


async def _tx_rows(db: Database, user_id: int) -> list[tuple[int, str, int]]:
    query = (
        "SELECT delta, reason, balance_after FROM renown_transactions "
        "WHERE user_id = ? ORDER BY id;"
    )
    async with db.conn.execute(query, (user_id,)) as cursor:
        return [(r[0], r[1], r[2]) for r in await cursor.fetchall()]


async def _balance(db: Database, user_id: int) -> int:
    wallet = await db.get_wallet(user_id)
    assert wallet is not None
    return wallet.balance


@pytest.mark.asyncio
async def test_a_v2_database_upgrades_to_v3_without_losing_wallets() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "existing_v2.db"
        conn = await aiosqlite.connect(db_path)
        await conn.executescript(MIGRATION_V1)
        await conn.executescript(MIGRATION_V2)
        await conn.execute("PRAGMA user_version = 2;")
        await conn.execute(
            "INSERT INTO wallets (user_id, balance, daily_streak, created_ts) "
            "VALUES (7, 321, 0, 1);"
        )
        await conn.commit()
        await conn.close()

        db = Database(db_path)
        await db.initialize()
        async with db.conn.execute("PRAGMA user_version;") as cursor:
            row = await cursor.fetchone()
        assert row is not None and row[0] == CURRENT_SCHEMA_VERSION == 3
        assert await _balance(db, 7) == 321
        async with db.conn.execute(
            "SELECT name FROM sqlite_master WHERE name = 'gambling_bets';"
        ) as cursor:
            assert await cursor.fetchone() is not None
        await db.close()


@pytest.mark.asyncio
async def test_place_bet_debits_and_records_an_open_bet() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = await _fresh_db(tmp_dir)
        bet_id = await db.place_bet(1, 50, "slots", WELCOME)
        assert await _balance(db, 1) == 150
        assert await _tx_rows(db, 1) == [(200, "welcome", 200), (-50, "bet:slots", 150)]
        async with db.conn.execute(
            "SELECT user_id, game, stake, status, payout FROM gambling_bets WHERE id = ?;",
            (bet_id,),
        ) as cursor:
            assert await cursor.fetchone() == (1, "slots", 50, "open", 0)
        await db.close()


@pytest.mark.asyncio
async def test_place_bet_refuses_more_than_the_balance_and_changes_nothing() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = await _fresh_db(tmp_dir)
        await db.ensure_wallet(1, WELCOME)
        with pytest.raises(InsufficientFundsError) as excinfo:
            await db.place_bet(1, WELCOME + 1, "slots", WELCOME)
        assert "bet" in excinfo.value.message
        assert await _balance(db, 1) == WELCOME
        async with db.conn.execute("SELECT COUNT(*) FROM gambling_bets;") as cursor:
            row = await cursor.fetchone()
        assert row is not None and row[0] == 0
        await db.close()


@pytest.mark.asyncio
async def test_place_bet_rejects_non_positive_amounts() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = await _fresh_db(tmp_dir)
        with pytest.raises(ValueError):
            await db.place_bet(1, 0, "slots", WELCOME)
        await db.close()


@pytest.mark.asyncio
async def test_concurrent_bets_cannot_overspend() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = await _fresh_db(tmp_dir)
        await db.ensure_wallet(1, WELCOME)
        results = await asyncio.gather(
            *[db.place_bet(1, 100, "slots", WELCOME) for _ in range(5)],
            return_exceptions=True,
        )
        won = [r for r in results if isinstance(r, int)]
        refused = [r for r in results if isinstance(r, InsufficientFundsError)]
        assert len(won) == 2 and len(refused) == 3
        assert await _balance(db, 1) == 0
        await db.close()


@pytest.mark.asyncio
async def test_settle_win_credits_the_payout() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = await _fresh_db(tmp_dir)
        bet_id = await db.place_bet(1, 100, "slots", WELCOME)
        balance = await db.settle_bet(bet_id, 250)
        assert balance == 350
        assert await _tx_rows(db, 1) == [
            (200, "welcome", 200),
            (-100, "bet:slots", 100),
            (250, "win:slots", 350),
        ]
        await db.close()


@pytest.mark.asyncio
async def test_settle_loss_pays_nothing_and_adds_no_transaction() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = await _fresh_db(tmp_dir)
        bet_id = await db.place_bet(1, 100, "slots", WELCOME)
        assert await db.settle_bet(bet_id, 0) == 100
        assert len(await _tx_rows(db, 1)) == 2
        async with db.conn.execute(
            "SELECT status, payout FROM gambling_bets WHERE id = ?;", (bet_id,)
        ) as cursor:
            assert await cursor.fetchone() == ("settled", 0)
        await db.close()


@pytest.mark.asyncio
async def test_settle_push_is_labelled_push() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = await _fresh_db(tmp_dir)
        bet_id = await db.place_bet(1, 100, "blackjack", WELCOME)
        assert await db.settle_bet(bet_id, 100) == 200
        assert (await _tx_rows(db, 1))[-1] == (100, "push:blackjack", 200)
        await db.close()


@pytest.mark.asyncio
async def test_settling_twice_raises_and_never_double_pays() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = await _fresh_db(tmp_dir)
        bet_id = await db.place_bet(1, 100, "slots", WELCOME)
        await db.settle_bet(bet_id, 200)
        before = await _balance(db, 1)
        with pytest.raises(BetNotOpenError):
            await db.settle_bet(bet_id, 200)
        assert await _balance(db, 1) == before
        await db.close()


@pytest.mark.asyncio
async def test_negative_payout_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = await _fresh_db(tmp_dir)
        bet_id = await db.place_bet(1, 100, "slots", WELCOME)
        with pytest.raises(ValueError):
            await db.settle_bet(bet_id, -1)
        await db.close()


@pytest.mark.asyncio
async def test_refund_returns_the_stake_once() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = await _fresh_db(tmp_dir)
        bet_id = await db.place_bet(1, 100, "slots", WELCOME)
        assert await db.refund_bet(bet_id) == 200
        assert (await _tx_rows(db, 1))[-1] == (100, "refund:slots", 200)
        with pytest.raises(BetNotOpenError):
            await db.refund_bet(bet_id)
        with pytest.raises(BetNotOpenError):
            await db.settle_bet(bet_id, 500)
        assert await _balance(db, 1) == 200
        await db.close()


@pytest.mark.asyncio
async def test_startup_sweep_refunds_open_bets_and_leaves_settled_ones() -> None:
    """Review Focus: a restart mid-hand returns every open stake, and only those."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = await _fresh_db(tmp_dir)
        settled = await db.place_bet(1, 40, "slots", WELCOME)
        await db.settle_bet(settled, 0)
        await db.place_bet(1, 60, "blackjack", WELCOME)
        await db.place_bet(2, 100, "roulette", WELCOME)
        assert await db.refund_open_bets() == 2
        assert await _balance(db, 1) == 160  # 200 - 40 (lost); the open 60 came back
        assert await _balance(db, 2) == WELCOME
        assert await db.refund_open_bets() == 0
        await db.close()


@pytest.mark.asyncio
async def test_gamble_stats_sum_settled_bets_per_game() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = await _fresh_db(tmp_dir)
        for game, payout in (("slots", 0), ("slots", 40), ("blackjack", 20)):
            bet_id = await db.place_bet(1, 10, game, WELCOME)
            await db.settle_bet(bet_id, payout)
        await db.place_bet(1, 10, "roulette", WELCOME)  # still open: not counted
        refunded = await db.place_bet(1, 10, "slots", WELCOME)
        await db.refund_bet(refunded)  # refunded: not counted
        assert await db.get_gamble_stats(1) == [("blackjack", 1, 10, 20), ("slots", 2, 20, 40)]
        assert await db.get_gamble_stats(99) == []
        await db.close()


@pytest.mark.asyncio
async def test_failure_mid_place_bet_leaves_nothing_behind() -> None:
    """Review Focus: unexpected exception during place_bet rolls back everything."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = await _fresh_db(tmp_dir)
        with patch.object(db, "_insert_transaction", new_callable=AsyncMock) as mock_tx:
            mock_tx.side_effect = RuntimeError("Simulated failure")
            with pytest.raises(RuntimeError, match="Simulated failure"):
                await db.place_bet(1, 50, "slots", WELCOME)
        assert await _balance(db, 1) == WELCOME
        async with db.conn.execute("SELECT COUNT(*) FROM gambling_bets;") as cursor:
            row = await cursor.fetchone()
        assert row is not None and row[0] == 0
        await db.log_command(1, "x")
        assert await _balance(db, 1) == WELCOME
        await db.close()


@pytest.mark.asyncio
async def test_failure_mid_finish_bet_leaves_nothing_behind() -> None:
    """Review Focus: unexpected exception during settle_bet rolls back the status change."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = await _fresh_db(tmp_dir)
        bet_id = await db.place_bet(1, 100, "slots", WELCOME)
        with patch.object(db, "_insert_transaction", new_callable=AsyncMock) as mock_tx:
            mock_tx.side_effect = RuntimeError("Simulated failure")
            with pytest.raises(RuntimeError, match="Simulated failure"):
                await db.settle_bet(bet_id, 250)
        await db.log_command(1, "x")
        assert await _balance(db, 1) == 100
        async with db.conn.execute(
            "SELECT status FROM gambling_bets WHERE id = ?;", (bet_id,)
        ) as cursor:
            row = await cursor.fetchone()
        assert row is not None and row[0] == "open"
        await db.settle_bet(bet_id, 250)
        assert await _balance(db, 1) == 350
        await db.close()


@pytest.mark.asyncio
async def test_two_concurrent_settles_pay_once() -> None:
    """Two settle_bet calls on the same bet; only one succeeds."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = await _fresh_db(tmp_dir)
        bet_id = await db.place_bet(1, 100, "slots", WELCOME)
        results = await asyncio.gather(
            db.settle_bet(bet_id, 200),
            db.settle_bet(bet_id, 200),
            return_exceptions=True,
        )
        wins = [r for r in results if isinstance(r, int)]
        fails = [r for r in results if isinstance(r, BetNotOpenError)]
        assert len(wins) == 1 and len(fails) == 1
        assert await _balance(db, 1) == WELCOME - 100 + 200
        await db.close()


@pytest.mark.asyncio
async def test_settle_with_partial_payout_writes_payout_reason() -> None:
    """Settlement with 0 < payout < stake should write 'payout:<game>' reason."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = await _fresh_db(tmp_dir)
        bet_id = await db.place_bet(1, 100, "slots", WELCOME)
        await db.settle_bet(bet_id, 40)
        rows = await _tx_rows(db, 1)
        assert rows[-1] == (40, "payout:slots", 140)
        await db.close()


@pytest.mark.asyncio
async def test_place_bet_above_initial_welcome_bonus_fails_cleanly() -> None:
    """Placing a bet larger than welcome bonus when user has no wallet.

    Should fail without creating wallet.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = await _fresh_db(tmp_dir)
        with pytest.raises(InsufficientFundsError):
            await db.place_bet(1, WELCOME + 1, "slots", WELCOME)
        assert await db.get_wallet(1) is None
        rows = await _tx_rows(db, 1)
        assert len(rows) == 0
        await db.close()
