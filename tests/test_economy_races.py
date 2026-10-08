"""Race tests: a second process (the admin panel) writing a wallet in the middle of a bot
operation must never be lost or leave a wrong balance_after on the ledger."""

from __future__ import annotations

import asyncio
import random
import sqlite3
from pathlib import Path
from typing import AsyncIterator

import pytest

from yokai.errors import DailyOnCooldownError, InsufficientFundsError
from yokai.storage.db import Database

WELCOME = 200


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    database = Database(tmp_path / "t.db")
    await database.initialize()
    try:
        yield database
    finally:
        await database.close()


def _panel_edit(path: Path, user_id: int, delta: int, timeout_s: float = 0.3) -> str:
    """What the panel does for a balance edit: take the write lock, change the wallet, add the
    ledger row, commit. Returns 'blocked' if the write lock could not be had in time."""
    conn = sqlite3.connect(path, timeout=timeout_s, isolation_level=None)
    try:
        try:
            conn.execute("BEGIN IMMEDIATE;")
        except sqlite3.OperationalError:
            return "blocked"
        conn.execute(
            "UPDATE wallets SET balance = balance + ? WHERE user_id = ?;", (delta, user_id)
        )
        after = conn.execute(
            "SELECT balance FROM wallets WHERE user_id = ?;", (user_id,)
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO renown_transactions (ts, user_id, delta, reason, counterparty_id, "
            "balance_after) VALUES (1, ?, ?, 'admin', NULL, ?);",
            (user_id, delta, after),
        )
        conn.execute("COMMIT;")
        return "applied"
    finally:
        conn.close()


def _ledger_is_true(path: Path, user_id: int) -> bool:
    conn = sqlite3.connect(path)
    try:
        balance = conn.execute(
            "SELECT balance FROM wallets WHERE user_id = ?;", (user_id,)
        ).fetchone()[0]
        total = conn.execute(
            "SELECT COALESCE(SUM(delta), 0) FROM renown_transactions WHERE user_id = ?;",
            (user_id,),
        ).fetchone()[0]
        last = conn.execute(
            "SELECT balance_after FROM renown_transactions WHERE user_id = ? "
            "ORDER BY id DESC LIMIT 1;",
            (user_id,),
        ).fetchone()[0]
        return bool(balance == total == last)
    finally:
        conn.close()


async def _funded(db: Database, user_id: int, extra: int) -> None:
    await db.ensure_wallet(user_id, WELCOME)
    await db.credit_wallet(user_id, extra, "daily", WELCOME)


def _inject_panel_edit(
    db: Database, monkeypatch: pytest.MonkeyPatch, path: Path, user_id: int, delta: int
) -> list[str]:
    """Run a panel edit right after the bot's first read of this wallet, before its write."""
    outcome: list[str] = []
    original = db.get_wallet

    async def hooked(uid: int):  # type: ignore[no-untyped-def]
        wallet = await original(uid)
        if uid == user_id and not outcome:
            outcome.append(_panel_edit(path, user_id, delta))
        return wallet

    monkeypatch.setattr(db, "get_wallet", hooked)
    return outcome


async def test_an_admin_adjust_cannot_overwrite_a_panel_edit_made_mid_operation(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _funded(db, 1, 300)  # balance 500
    outcome = _inject_panel_edit(db, monkeypatch, tmp_path / "t.db", 1, 500)

    await db.admin_adjust_wallet(1, 100, WELCOME)

    assert outcome == ["blocked"]  # the panel had to wait; it could not slip in between
    assert _ledger_is_true(tmp_path / "t.db", 1)
    wallet = await db.get_wallet(1)
    assert wallet is not None and wallet.balance == 600


async def test_a_daily_claim_records_the_true_balance_when_the_panel_edits_mid_claim(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _funded(db, 1, 300)
    outcome = _inject_panel_edit(db, monkeypatch, tmp_path / "t.db", 1, 500)

    await db.claim_daily(1, 100, WELCOME, now=2_000_000, rng=random.Random(1))

    assert outcome == ["blocked"]
    assert _ledger_is_true(tmp_path / "t.db", 1)


async def test_a_payment_records_the_true_balances_when_the_panel_edits_mid_payment(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _funded(db, 1, 300)
    await _funded(db, 2, 50)
    outcome = _inject_panel_edit(db, monkeypatch, tmp_path / "t.db", 1, 500)

    await db.pay(1, 2, 100, WELCOME)

    assert outcome == ["blocked"]
    assert _ledger_is_true(tmp_path / "t.db", 1) and _ledger_is_true(tmp_path / "t.db", 2)


async def test_a_refused_bot_operation_does_not_leave_the_write_lock_held(
    db: Database, tmp_path: Path
) -> None:
    """Review Focus 2: refusals and failures must release SQLite's write lock."""
    path = tmp_path / "t.db"
    await _funded(db, 1, 10)  # balance 210
    with pytest.raises(InsufficientFundsError):
        await db.pay(1, 2, 5_000, WELCOME)
    assert _panel_edit(path, 1, 1) == "applied"

    await db.claim_daily(1, 100, WELCOME, now=2_000_000, rng=random.Random(1))
    with pytest.raises(DailyOnCooldownError):
        await db.claim_daily(1, 100, WELCOME, now=2_000_001, rng=random.Random(1))
    assert _panel_edit(path, 1, 1) == "applied"


async def test_an_unexpected_failure_inside_a_wallet_operation_releases_the_lock(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "t.db"
    await _funded(db, 1, 10)

    async def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("ledger insert failed")

    monkeypatch.setattr(db, "_insert_transaction", broken)
    with pytest.raises(RuntimeError):
        await db.admin_adjust_wallet(1, 5, WELCOME)
    assert _panel_edit(path, 1, 1) == "applied"
    wallet = await db.get_wallet(1)
    assert wallet is not None and wallet.balance == 211  # the failed adjust left no trace


async def test_many_concurrent_bot_and_panel_operations_keep_every_ledger_true(
    db: Database, tmp_path: Path
) -> None:
    path = tmp_path / "t.db"
    for user_id in (1, 2, 3):
        await _funded(db, user_id, 1_000)

    async def panel_edits() -> None:
        for i in range(8):
            await asyncio.to_thread(_panel_edit, path, 1 + i % 3, 7, 5.0)

    bot_operations = [
        panel_edits(),
        *(db.pay(1 + i % 3, 1 + (i + 1) % 3, 10, WELCOME) for i in range(12)),
        *(db.admin_adjust_wallet(1 + i % 3, 5, WELCOME) for i in range(9)),
        *(db.credit_wallet(1 + i % 3, 3, "voice", WELCOME) for i in range(9)),
    ]
    await asyncio.gather(*bot_operations)

    assert all(_ledger_is_true(path, user_id) for user_id in (1, 2, 3))
