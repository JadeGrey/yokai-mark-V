"""cash_out_lobby pays poker seats mid-game; refunds of poker bets never exceed what is left."""

from __future__ import annotations

from pathlib import Path

import pytest

from yokai.errors import BetNotOpenError
from yokai.storage.db import Database


async def _world(tmp_path: Path) -> tuple[Database, int, int, int]:
    """Three players, each with 1000 Renown, each buying in for 100 at one poker table."""
    db = Database(tmp_path / "poker.db")
    await db.initialize()
    bets = []
    for user_id in (1, 2, 3):
        await db.ensure_wallet(user_id, 1000)
        bets.append(await db.place_bet(user_id, 100, "poker", 0, lobby_id="L1"))
    return db, bets[0], bets[1], bets[2]


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
async def test_a_winner_can_leave_with_more_than_their_buy_in(tmp_path: Path) -> None:
    db, a, b, c = await _world(tmp_path)
    try:
        balances = await db.cash_out_lobby("L1", {a: 250})
        assert balances == {a: 1150}
        assert await _balance(db, 1) == 1150
        assert await _status(db, a) == "settled"
        async with db.conn.execute(
            "SELECT user_id, delta, reason FROM renown_transactions WHERE reason LIKE 'win:%';"
        ) as cur:
            assert await cur.fetchall() == [(1, 250, "win:poker")]
        assert await db.get_pot() == 0  # poker has no rake: nothing goes to the lottery
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_busted_player_is_paid_nothing_and_gets_no_transaction_row(tmp_path: Path) -> None:
    db, a, b, c = await _world(tmp_path)
    try:
        assert await db.cash_out_lobby("L1", {b: 0}) == {b: 900}
        assert await _status(db, b) == "settled"
        async with db.conn.execute(
            "SELECT COUNT(*) FROM renown_transactions "
            "WHERE reason IN ('win:poker', 'push:poker', 'payout:poker');"
        ) as cur:
            assert (await cur.fetchone()) == (0,)
        assert await db.get_pot() == 0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_several_seats_can_be_paid_in_one_transaction(tmp_path: Path) -> None:
    db, a, b, c = await _world(tmp_path)
    try:
        balances = await db.cash_out_lobby("L1", {a: 0, b: 100, c: 200})
        assert balances == {a: 900, b: 1000, c: 1100}
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_payouts_can_never_exceed_the_buy_ins_of_the_whole_table(tmp_path: Path) -> None:
    db, a, b, c = await _world(tmp_path)
    try:
        await db.cash_out_lobby("L1", {a: 200})  # 300 in, 200 out so far
        with pytest.raises(ValueError, match="more than the table"):
            await db.cash_out_lobby("L1", {b: 101})
        assert await _status(db, b) == "open" and await _balance(db, 2) == 900
        await db.cash_out_lobby("L1", {b: 100})  # exactly the 300 total is fine
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_batch_that_breaks_the_limit_pays_nobody(tmp_path: Path) -> None:
    db, a, b, c = await _world(tmp_path)
    try:
        with pytest.raises(ValueError):
            await db.cash_out_lobby("L1", {a: 100, b: 300})
        assert (await _status(db, a), await _status(db, b)) == ("open", "open")
        assert (await _balance(db, 1), await _balance(db, 2)) == (900, 900)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_seat_cannot_be_paid_twice(tmp_path: Path) -> None:
    db, a, b, c = await _world(tmp_path)
    try:
        await db.cash_out_lobby("L1", {a: 100})
        with pytest.raises(BetNotOpenError):
            await db.cash_out_lobby("L1", {a: 100})
        assert await _balance(db, 1) == 1000
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_only_poker_bets_of_that_lobby_can_be_cashed_out(tmp_path: Path) -> None:
    db, a, b, c = await _world(tmp_path)
    try:
        await db.ensure_wallet(4, 1000)
        other = await db.place_bet(4, 100, "poker", 0, lobby_id="L2")
        duel = await db.place_bet(4, 100, "duel", 0, lobby_id="L1")
        with pytest.raises(ValueError):
            await db.cash_out_lobby("L1", {other: 50})
        with pytest.raises(ValueError):
            await db.cash_out_lobby("L1", {duel: 50})
        assert await _status(db, other) == "open" and await _status(db, duel) == "open"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_empty_and_negative_payouts_are_rejected(tmp_path: Path) -> None:
    db, a, b, c = await _world(tmp_path)
    try:
        with pytest.raises(ValueError):
            await db.cash_out_lobby("L1", {})
        with pytest.raises(ValueError):
            await db.cash_out_lobby("L1", {a: -1})
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_refunding_a_poker_bet_with_nothing_paid_returns_the_full_buy_in(
    tmp_path: Path,
) -> None:
    db, a, b, c = await _world(tmp_path)
    try:
        assert await db.refund_bet(a) == 1000
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_refunds_after_a_cash_out_share_only_what_is_left(tmp_path: Path) -> None:
    """A won 100 extra and left; a restart must not give B and C their full 100 back."""
    db, a, b, c = await _world(tmp_path)
    try:
        await db.cash_out_lobby("L1", {a: 200})  # 100 of the 300 is left for B and C
        assert await db.refund_bet(b) == 950  # 100 * 100 // 200 = 50 back
        assert await db.refund_bet(c) == 950  # the other 50
        total = sum([await _balance(db, u) for u in (1, 2, 3)])
        assert total == 3000  # the 300 bought in is exactly the 300 paid out; none created
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_the_startup_sweep_applies_the_same_cap(tmp_path: Path) -> None:
    db, a, b, c = await _world(tmp_path)
    try:
        await db.cash_out_lobby("L1", {a: 300})  # everything is left with A
        assert await db.refund_open_bets() == 2
        assert (await _balance(db, 2), await _balance(db, 3)) == (900, 900)
        assert await _balance(db, 1) == 1200
    finally:
        await db.close()
