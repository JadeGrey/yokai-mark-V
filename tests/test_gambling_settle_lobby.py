"""settle_lobby settles every bet of a table in one all-or-nothing transaction."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from yokai.errors import BetNotOpenError
from yokai.storage.db import Database


async def _world(tmp_path: Path) -> tuple[Database, int, int]:
    db = Database(tmp_path / "lobby.db")
    await db.initialize()
    await db.ensure_wallet(1, 1000)
    await db.ensure_wallet(2, 1000)
    a = await db.place_bet(1, 100, "duel", 0, lobby_id="L1")
    b = await db.place_bet(2, 100, "duel", 0, lobby_id="L1")
    return db, a, b


async def _balance(db: Database, user_id: int) -> int:
    wallet = await db.get_wallet(user_id)
    assert wallet is not None
    return wallet.balance


async def _status(db: Database, bet_id: int) -> str:
    async with db.conn.execute("SELECT status FROM gambling_bets WHERE id = ?;", (bet_id,)) as cur:
        row = await cur.fetchone()
    assert row is not None
    return row[0]


@pytest.mark.asyncio
async def test_settles_winner_and_loser_and_sends_only_the_rake_to_the_pot(tmp_path: Path) -> None:
    db, a, b = await _world(tmp_path)
    try:
        balances = await db.settle_lobby({a: 196, b: 0})
        assert balances == {a: 1096, b: 900}
        assert (await _balance(db, 1), await _balance(db, 2)) == (1096, 900)
        assert await db.get_pot() == 4
        assert (await _status(db, a), await _status(db, b)) == ("settled", "settled")
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_the_winner_gets_a_win_row_and_the_loser_none(tmp_path: Path) -> None:
    db, a, b = await _world(tmp_path)
    try:
        await db.settle_lobby({a: 196, b: 0})
        async with db.conn.execute(
            "SELECT user_id, delta, reason FROM renown_transactions WHERE reason LIKE 'win:%';"
        ) as cur:
            assert await cur.fetchall() == [(1, 196, "win:duel")]
        async with db.conn.execute(
            "SELECT COUNT(*) FROM renown_transactions WHERE user_id = 2 AND reason LIKE 'win:%';"
        ) as cur:
            assert (await cur.fetchone())[0] == 0  # type: ignore[index]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_bet_that_is_not_open_aborts_everything(tmp_path: Path) -> None:
    db, a, b = await _world(tmp_path)
    try:
        await db.refund_bet(b)  # b is no longer open
        before = (await _balance(db, 1), await _balance(db, 2), await db.get_pot())
        with pytest.raises(BetNotOpenError):
            await db.settle_lobby({a: 196, b: 0})
        assert (await _balance(db, 1), await _balance(db, 2), await db.get_pot()) == before
        assert await _status(db, a) == "open"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_failure_crediting_the_pot_rolls_everything_back(tmp_path: Path) -> None:
    db, a, b = await _world(tmp_path)
    try:
        before = (await _balance(db, 1), await _balance(db, 2))
        with patch.object(db, "_credit_pot", new=AsyncMock(side_effect=RuntimeError("boom"))):
            with pytest.raises(RuntimeError):
                await db.settle_lobby({a: 196, b: 0})
        assert (await _balance(db, 1), await _balance(db, 2)) == before
        assert (await _status(db, a), await _status(db, b)) == ("open", "open")
        assert await db.get_pot() == 0
        # and the lock was released, so the table can still be refunded
        assert await db.refund_open_bets() == 2
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_pooled_game_cannot_pay_out_more_than_was_staked(tmp_path: Path) -> None:
    db, a, b = await _world(tmp_path)
    try:
        with pytest.raises(ValueError):
            await db.settle_lobby({a: 201, b: 0})
        assert (await _status(db, a), await _status(db, b)) == ("open", "open")
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_settling_twice_pays_once(tmp_path: Path) -> None:
    db, a, b = await _world(tmp_path)
    try:
        await db.settle_lobby({a: 196, b: 0})
        with pytest.raises(BetNotOpenError):
            await db.settle_lobby({a: 196, b: 0})
        assert await _balance(db, 1) == 1096
        assert await db.get_pot() == 4
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_two_concurrent_settlements_pay_exactly_once(tmp_path: Path) -> None:
    db, a, b = await _world(tmp_path)
    try:
        results = await asyncio.gather(
            db.settle_lobby({a: 196, b: 0}),
            db.settle_lobby({a: 0, b: 196}),
            return_exceptions=True,
        )
        assert sum(isinstance(r, BetNotOpenError) for r in results) == 1
        assert await _balance(db, 1) + await _balance(db, 2) == 2000 - 4
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_the_startup_sweep_finds_nothing_after_a_settlement(tmp_path: Path) -> None:
    db, a, b = await _world(tmp_path)
    try:
        await db.settle_lobby({a: 196, b: 0})
        assert await db.refund_open_bets() == 0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_rejects_empty_negative_and_mixed_game_payouts(tmp_path: Path) -> None:
    db, a, b = await _world(tmp_path)
    try:
        await db.ensure_wallet(3, 1000)
        other = await db.place_bet(3, 50, "slots", 0)
        with pytest.raises(ValueError):
            await db.settle_lobby({})
        with pytest.raises(ValueError):
            await db.settle_lobby({a: -1, b: 0})
        with pytest.raises(ValueError):
            await db.settle_lobby({a: 100, other: 0})
        assert await _status(db, a) == "open" and await _status(db, other) == "open"
    finally:
        await db.close()
