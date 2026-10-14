"""Settled bets reach the listener; refunds and listener errors do not matter to the host."""

from __future__ import annotations

from pathlib import Path

import pytest

from yokai.economy.models import BetSettlement
from yokai.storage.db import Database

WELCOME = 200


@pytest.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "t.db")
    try:
        await database.initialize()
        yield database
    finally:
        await database.close()


async def test_settle_bet_notifies_with_the_payout_and_group(db: Database) -> None:
    seen: list[BetSettlement] = []
    db.bet_listener = seen.append
    bet_id = await db.place_bet(1, 50, "blackjack", WELCOME, group_id="g1")
    await db.settle_bet(bet_id, 100)
    assert seen == [BetSettlement(bet_id, 1, "blackjack", 50, 100, "g1")]


async def test_refunds_are_not_notified(db: Database) -> None:
    seen: list[BetSettlement] = []
    db.bet_listener = seen.append
    bet_id = await db.place_bet(1, 50, "slots", WELCOME)
    await db.refund_bet(bet_id)
    assert seen == []


async def test_settle_lobby_notifies_every_seat_including_losers(db: Database) -> None:
    seen: list[BetSettlement] = []
    db.bet_listener = seen.append
    a = await db.place_bet(1, 50, "duel", WELCOME)
    b = await db.place_bet(2, 50, "duel", WELCOME)
    await db.settle_lobby({a: 95, b: 0})
    assert {(s.user_id, s.stake, s.payout) for s in seen} == {(1, 50, 95), (2, 50, 0)}
    assert all(s.game == "duel" for s in seen)


async def test_poker_cash_out_notifies_with_the_buy_in_as_stake(db: Database) -> None:
    seen: list[BetSettlement] = []
    db.bet_listener = seen.append
    a = await db.place_bet(1, 100, "poker", WELCOME, lobby_id="L1")
    b = await db.place_bet(2, 100, "poker", WELCOME, lobby_id="L1")
    await db.cash_out_lobby("L1", {a: 150, b: 50})
    assert {(s.user_id, s.stake, s.payout, s.game) for s in seen} == {
        (1, 100, 150, "poker"),
        (2, 100, 50, "poker"),
    }


async def test_a_failing_listener_never_breaks_settlement(db: Database) -> None:
    def explode(_: BetSettlement) -> None:
        raise RuntimeError("listener bug")

    db.bet_listener = explode
    bet_id = await db.place_bet(1, 50, "slots", WELCOME)
    balance = await db.settle_bet(bet_id, 120)
    wallet = await db.get_wallet(1)
    assert wallet is not None and wallet.balance == balance == WELCOME - 50 + 120
