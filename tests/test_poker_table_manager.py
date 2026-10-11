"""TableManager poker money: seats cash out mid-game, the table settles what is left."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from tests.table_helpers import ALL_PLAYABLE, CHANNEL, announce_ok, balance_of, world
from yokai.errors import LobbyClosedError, NotAtTableError
from yokai.gambling.lobby import Lobby, LobbyState
from yokai.gambling.tables import TableManager


async def _running_poker(manager: TableManager, stake: int = 100) -> Lobby:
    """Players 1, 2 and 3 are seated with `stake` each and the table has started."""
    lobby = await manager.open_table(CHANNEL, "poker", 1, stake, announce_ok)
    await manager.join(lobby, 2, None)
    await manager.join(lobby, 3, None)
    await manager.begin(lobby)
    return lobby


def _poker_world(tmp_path: Path):  # type: ignore[no-untyped-def]
    return world(tmp_path, game="poker", specs=ALL_PLAYABLE)


@pytest.mark.asyncio
async def test_a_busted_player_cashes_out_for_nothing_and_is_freed(tmp_path: Path) -> None:
    async with _poker_world(tmp_path) as (db, manager, active):
        lobby = await _running_poker(manager)
        assert await manager.cash_out(lobby, 2, 0) == 900
        assert lobby.paid_out == {2: 0} and lobby.state is LobbyState.RUNNING
        assert not active.is_active(2) and active.is_active(1) and active.is_active(3)


@pytest.mark.asyncio
async def test_a_winner_cashes_out_with_more_than_their_buy_in(tmp_path: Path) -> None:
    async with _poker_world(tmp_path) as (db, manager, active):
        lobby = await _running_poker(manager)
        assert await manager.cash_out(lobby, 1, 250) == 1150
        assert await balance_of(db, 1) == 1150 and not active.is_active(1)


@pytest.mark.asyncio
async def test_cashing_out_needs_a_running_table_a_seat_and_a_seat_not_yet_paid(
    tmp_path: Path,
) -> None:
    async with _poker_world(tmp_path) as (db, manager, _):
        lobby = await manager.open_table(CHANNEL, "poker", 1, 100, announce_ok)
        await manager.join(lobby, 2, None)
        with pytest.raises(LobbyClosedError):
            await manager.cash_out(lobby, 2, 0)  # still open, not started
        await manager.begin(lobby)
        with pytest.raises(NotAtTableError):
            await manager.cash_out(lobby, 3, 0)
        await manager.cash_out(lobby, 2, 0)
        with pytest.raises(ValueError):
            await manager.cash_out(lobby, 2, 0)
        assert await balance_of(db, 2) == 900


@pytest.mark.asyncio
async def test_settling_pays_the_seats_still_at_the_table_and_closes_it(tmp_path: Path) -> None:
    async with _poker_world(tmp_path) as (db, manager, active):
        lobby = await _running_poker(manager)
        await manager.cash_out(lobby, 2, 0)
        balances = await manager.settle_poker(lobby, {1: 200, 3: 100})
        assert balances == {1: 1100, 3: 1000}
        assert lobby.state is LobbyState.SETTLED
        assert [await balance_of(db, u) for u in (1, 2, 3)] == [1100, 900, 1000]
        assert not any(active.is_active(u) for u in (1, 2, 3))
        assert await db.refund_open_bets() == 0 and await db.get_pot() == 0
        row = await db.get_multiplayer_channel(CHANNEL)
        assert row is not None and row.table_active is False


@pytest.mark.asyncio
async def test_a_table_where_everyone_already_left_settles_with_no_payouts(
    tmp_path: Path,
) -> None:
    async with _poker_world(tmp_path) as (db, manager, active):
        lobby = await _running_poker(manager)
        await manager.cash_out(lobby, 1, 150)
        await manager.cash_out(lobby, 2, 150)
        await manager.cash_out(lobby, 3, 0)
        assert await manager.settle_poker(lobby, {}) == {}
        assert lobby.state is LobbyState.SETTLED


@pytest.mark.asyncio
async def test_settling_must_cover_exactly_the_remaining_seats(tmp_path: Path) -> None:
    async with _poker_world(tmp_path) as (db, manager, _):
        lobby = await _running_poker(manager)
        await manager.cash_out(lobby, 2, 0)
        with pytest.raises(ValueError):
            await manager.settle_poker(lobby, {1: 300})  # seat 3 is missing
        with pytest.raises(ValueError):
            await manager.settle_poker(lobby, {1: 100, 2: 100, 3: 100})  # seat 2 already paid
        assert lobby.state is LobbyState.RUNNING


@pytest.mark.asyncio
async def test_settling_must_pay_out_exactly_what_was_bought_in(tmp_path: Path) -> None:
    async with _poker_world(tmp_path) as (db, manager, _):
        lobby = await _running_poker(manager)
        await manager.cash_out(lobby, 2, 50)
        with pytest.raises(ValueError):
            await manager.settle_poker(lobby, {1: 200, 3: 100})  # 350 for a 300 table
        with pytest.raises(ValueError):
            await manager.settle_poker(lobby, {1: 100, 3: 100})  # 250: chips would vanish
        assert lobby.state is LobbyState.RUNNING
        assert await db.get_pot() == 0


@pytest.mark.asyncio
async def test_a_settled_table_cannot_be_settled_again(tmp_path: Path) -> None:
    async with _poker_world(tmp_path) as (db, manager, _):
        lobby = await _running_poker(manager)
        await manager.settle_poker(lobby, {1: 100, 2: 100, 3: 100})
        with pytest.raises(LobbyClosedError):
            await manager.settle_poker(lobby, {1: 100, 2: 100, 3: 100})
        assert await balance_of(db, 1) == 1000


@pytest.mark.asyncio
async def test_someone_who_left_and_started_another_game_keeps_that_claim(tmp_path: Path) -> None:
    async with _poker_world(tmp_path) as (db, manager, active):
        lobby = await _running_poker(manager)
        await manager.cash_out(lobby, 2, 0)
        active.claim(2)  # player 2 joined something else after leaving the table
        await manager.settle_poker(lobby, {1: 150, 3: 150})
        assert active.is_active(2)


@pytest.mark.asyncio
async def test_a_failed_final_settlement_refunds_only_what_is_left_and_leaves_claims(
    tmp_path: Path,
) -> None:
    async with _poker_world(tmp_path) as (db, manager, active):
        lobby = await _running_poker(manager)
        await manager.cash_out(lobby, 1, 200)  # 100 of the 300 is left for players 2 and 3
        active.claim(1)  # player 1 is already in another game
        with patch.object(db, "cash_out_lobby", new=AsyncMock(side_effect=RuntimeError("disk"))):
            with pytest.raises(RuntimeError):
                await manager.settle_poker(lobby, {2: 50, 3: 50})
        assert lobby.state is LobbyState.CANCELLED
        assert [await balance_of(db, u) for u in (1, 2, 3)] == [1100, 950, 950]
        assert active.is_active(1) and not active.is_active(2) and not active.is_active(3)
        assert await db.refund_open_bets() == 0


@pytest.mark.asyncio
async def test_aborting_after_a_cash_out_never_creates_chips(tmp_path: Path) -> None:
    async with _poker_world(tmp_path) as (db, manager, _):
        lobby = await _running_poker(manager)
        await manager.cash_out(lobby, 1, 300)  # one player won everything and left
        await manager.abort(lobby)
        await manager.abort(lobby)  # idempotent
        assert [await balance_of(db, u) for u in (1, 2, 3)] == [1200, 900, 900]
        assert lobby.state is LobbyState.CANCELLED


@pytest.mark.asyncio
async def test_an_open_poker_table_refunds_in_full_when_cancelled(tmp_path: Path) -> None:
    async with _poker_world(tmp_path) as (db, manager, active):
        lobby = await manager.open_table(CHANNEL, "poker", 1, 100, announce_ok)
        await manager.join(lobby, 2, None)
        await manager.cancel(lobby, 1, is_admin=False)
        assert [await balance_of(db, u) for u in (1, 2)] == [1000, 1000]
        assert not active.is_active(1) and not active.is_active(2)


@pytest.mark.asyncio
async def test_poker_can_be_opened_with_the_real_game_specs(tmp_path: Path) -> None:
    """Guard: poker is playable out of the box (it ships with its table runner in phase P3)."""
    async with world(tmp_path, game="poker") as (db, manager, active):
        lobby = await manager.open_table(CHANNEL, "poker", 1, 100, announce_ok)
        assert lobby.spec.name == "poker" and active.is_active(1)
        assert await balance_of(db, 1) == 900
