"""Tests for the gambling escrow helpers and the active-games registry."""

from __future__ import annotations

import random
import tempfile
from pathlib import Path

import pytest

from yokai.errors import AlreadyInGameError, BetOutOfRangeError
from yokai.gambling.escrow import escrowed_bet, validate_bet
from yokai.gambling.rng import new_rng
from yokai.gambling.sessions import ActiveGames
from yokai.storage.db import Database

WELCOME = 200


def test_validate_bet_accepts_exactly_min_and_max() -> None:
    validate_bet(10, 10, 5000)
    validate_bet(5000, 10, 5000)


@pytest.mark.parametrize("amount", [9, 5001, 0, -5])
def test_validate_bet_rejects_outside_the_range(amount: int) -> None:
    with pytest.raises(BetOutOfRangeError):
        validate_bet(amount, 10, 5000)


def test_new_rng_is_a_system_random() -> None:
    assert isinstance(new_rng(), random.SystemRandom)


def test_active_games_blocks_a_second_claim_until_released() -> None:
    games = ActiveGames()
    games.claim(1)
    assert games.is_active(1)
    with pytest.raises(AlreadyInGameError):
        games.claim(1)
    games.claim(2)  # other users are independent
    games.release(1)
    games.release(1)  # idempotent
    assert not games.is_active(1)
    games.claim(1)


async def _balance(db: Database, user_id: int) -> int:
    wallet = await db.get_wallet(user_id)
    assert wallet is not None
    return wallet.balance


@pytest.mark.asyncio
async def test_escrowed_bet_stays_settleable_when_the_body_succeeds() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = Database(Path(tmp_dir) / "t.db")
        await db.initialize()
        async with escrowed_bet(db, 1, 50, "slots", WELCOME) as bet_id:
            assert await _balance(db, 1) == 150
            await db.settle_bet(bet_id, 100)
        assert await _balance(db, 1) == 250
        await db.close()


@pytest.mark.asyncio
async def test_escrowed_bet_refunds_when_the_body_raises() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = Database(Path(tmp_dir) / "t.db")
        await db.initialize()
        with pytest.raises(RuntimeError):
            async with escrowed_bet(db, 1, 50, "slots", WELCOME):
                raise RuntimeError("boom")
        assert await _balance(db, 1) == WELCOME
        await db.close()


@pytest.mark.asyncio
async def test_escrowed_bet_does_not_refund_a_bet_that_already_settled() -> None:
    """The body settled, then something else failed: the payout must stand, not be undone."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = Database(Path(tmp_dir) / "t.db")
        await db.initialize()
        with pytest.raises(RuntimeError):
            async with escrowed_bet(db, 1, 50, "slots", WELCOME) as bet_id:
                await db.settle_bet(bet_id, 100)
                raise RuntimeError("edit failed")
        assert await _balance(db, 1) == 250
        await db.close()
