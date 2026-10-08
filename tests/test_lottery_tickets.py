"""A ticket purchase is one all-or-nothing transaction."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from yokai.errors import (
    InsufficientFundsError,
    InvalidGameChoiceError,
    LotteryClosedError,
    TicketLimitError,
)
from yokai.storage.db import Database

NOW = 1_000_000
WEEK = 7 * 24 * 3600


async def _db(tmp_path: Path, balance: int = 1000) -> Database:
    db = Database(tmp_path / "tickets.db")
    await db.initialize()
    await db.ensure_wallet(1, balance)
    await db.ensure_wallet(2, balance)
    await db.ensure_lottery_schedule(NOW, WEEK)
    return db


async def _buy(db: Database, user_id: int, numbers: list[int], *, now: int = NOW + 10, **kw):  # type: ignore[no-untyped-def]
    return await db.buy_lottery_tickets(
        user_id,
        numbers,
        price=kw.get("price", 100),
        max_per_user=kw.get("max_per_user", 10),
        welcome_bonus=0,
        now=now,
    )


async def _balance(db: Database, user_id: int) -> int:
    wallet = await db.get_wallet(user_id)
    assert wallet is not None
    return wallet.balance


@pytest.mark.asyncio
async def test_a_purchase_debits_stores_the_tickets_and_feeds_the_pot(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        purchase = await _buy(db, 1, [3, 7, 19])
        assert (purchase.draw_no, purchase.numbers, purchase.cost) == (1, (3, 7, 19), 300)
        assert purchase.new_balance == 700 and purchase.pot == 300
        assert await _balance(db, 1) == 700
        assert await db.user_lottery_numbers(1) == [3, 7, 19]
        assert await db.lottery_tickets_sold() == 3
        assert await db.get_pot() == 300
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_the_ledger_and_pot_entries_record_the_purchase(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        await _buy(db, 1, [3, 7])
        async with db.conn.execute(
            "SELECT delta, reason, balance_after FROM renown_transactions "
            "WHERE reason = 'ticket:lottery';"
        ) as cur:
            assert await cur.fetchall() == [(-200, "ticket:lottery", 800)]
        async with db.conn.execute(
            "SELECT source, amount FROM lottery_pot_entries WHERE source = 'ticket';"
        ) as cur:
            assert await cur.fetchall() == [("ticket", 200)]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_not_enough_renown_changes_nothing(tmp_path: Path) -> None:
    db = await _db(tmp_path, balance=150)
    try:
        with pytest.raises(InsufficientFundsError):
            await _buy(db, 1, [3, 7])
        assert await _balance(db, 1) == 150 and await db.get_pot() == 0
        assert await db.user_lottery_numbers(1) == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_the_per_draw_cap_counts_what_is_already_held(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        await _buy(db, 1, [1, 2, 3, 4, 5, 6, 7, 8])
        with pytest.raises(TicketLimitError):
            await _buy(db, 1, [9, 10, 11])
        await _buy(db, 1, [9, 10])  # exactly the cap
        assert len(await db.user_lottery_numbers(1)) == 10
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_the_same_number_cannot_be_bought_twice_but_two_members_may_share_one(
    tmp_path: Path,
) -> None:
    db = await _db(tmp_path)
    try:
        await _buy(db, 1, [7])
        with pytest.raises(InvalidGameChoiceError, match="7"):
            await _buy(db, 1, [7, 8])
        assert await db.user_lottery_numbers(1) == [7]  # the 8 was not bought either
        await _buy(db, 2, [7])
        assert await db.lottery_tickets_sold() == 2
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_sales_close_at_the_draw_time_and_before_a_schedule_exists(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        await _buy(db, 1, [1], now=NOW + WEEK - 1)  # the last second
        with pytest.raises(LotteryClosedError):
            await _buy(db, 1, [2], now=NOW + WEEK)
        assert await _balance(db, 1) == 900
    finally:
        await db.close()
    fresh = Database(tmp_path / "fresh.db")
    await fresh.initialize()
    try:
        await fresh.ensure_wallet(1, 1000)
        with pytest.raises(LotteryClosedError):
            await _buy(fresh, 1, [1])
    finally:
        await fresh.close()


@pytest.mark.asyncio
async def test_a_failure_part_way_rolls_everything_back(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        with patch.object(db, "_credit_pot", new=AsyncMock(side_effect=RuntimeError("boom"))):
            with pytest.raises(RuntimeError):
                await _buy(db, 1, [3, 7])
        assert await _balance(db, 1) == 1000 and await db.get_pot() == 0
        assert await db.user_lottery_numbers(1) == []
        await _buy(db, 1, [3, 7])  # the lock was released
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_two_simultaneous_purchases_cannot_pass_the_cap(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        results = await asyncio.gather(
            _buy(db, 1, [1, 2, 3, 4, 5, 6]),
            _buy(db, 1, [11, 12, 13, 14, 15, 16]),
            return_exceptions=True,
        )
        assert sum(isinstance(r, TicketLimitError) for r in results) == 1
        assert len(await db.user_lottery_numbers(1)) == 6
        assert await _balance(db, 1) == 400
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_member_without_a_wallet_gets_the_welcome_bonus_first(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        purchase = await db.buy_lottery_tickets(
            9, [4], price=100, max_per_user=10, welcome_bonus=200, now=NOW + 5
        )
        assert purchase.new_balance == 100
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_buying_nothing_is_a_programming_error(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        with pytest.raises(ValueError):
            await _buy(db, 1, [])
    finally:
        await db.close()
