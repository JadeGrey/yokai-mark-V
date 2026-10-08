"""TableManager: opening, filling, running and closing tables against a real database."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from tests.table_helpers import ANNOUNCED_MESSAGE_ID, CHANNEL, NOW, announce_ok, balance_of, world
from yokai.errors import (
    AlreadyInGameError,
    BetOutOfRangeError,
    InsufficientFundsError,
    InvalidGameChoiceError,
    LobbyClosedError,
    NotAtTableError,
    NotHostError,
    TableBusyError,
)
from yokai.gambling.engines.duel import DuelRound
from yokai.gambling.lobby import GameSpec, LobbyState
from yokai.gambling.tables import BeginOutcome

TRIO = GameSpec("duel", "Trio", min_players=2, max_players=3)  # registered under the duel key


# ---- opening ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_opening_a_table_escrows_the_host_and_records_the_message(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, active):
        lobby = await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        assert lobby.state is LobbyState.OPEN and lobby.message_id == ANNOUNCED_MESSAGE_ID
        assert lobby.host_seat.stake == 100 and lobby.closes_at == NOW + 60
        assert await balance_of(db, 1) == 900
        assert active.is_active(1) and manager.lobby_for(CHANNEL) is lobby
        row = await db.get_multiplayer_channel(CHANNEL)
        assert row is not None and (row.table_message_id, row.table_active) == (777, True)


@pytest.mark.asyncio
async def test_a_second_table_is_refused_while_one_is_live_and_costs_nothing(
    tmp_path: Path,
) -> None:
    async with world(tmp_path) as (db, manager, active):
        await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        with pytest.raises(TableBusyError):
            await manager.open_table(CHANNEL, "duel", 2, 100, announce_ok)
        assert await balance_of(db, 2) == 1000 and not active.is_active(2)


@pytest.mark.asyncio
async def test_two_simultaneous_requests_open_exactly_one_table(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, _):
        results = await asyncio.gather(
            manager.open_table(CHANNEL, "duel", 1, 100, announce_ok),
            manager.open_table(CHANNEL, "duel", 2, 100, announce_ok),
            return_exceptions=True,
        )
        assert sum(isinstance(r, TableBusyError) for r in results) == 1
        assert await balance_of(db, 1) + await balance_of(db, 2) == 2000 - 100


@pytest.mark.asyncio
async def test_a_new_table_may_open_once_the_old_one_has_ended(tmp_path: Path) -> None:
    async with world(tmp_path) as (_, manager, _active):
        first = await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        await manager.cancel(first, 1, is_admin=False)
        second = await manager.open_table(CHANNEL, "duel", 2, 100, announce_ok)
        assert manager.lobby_for(CHANNEL) is second


@pytest.mark.asyncio
async def test_a_stake_out_of_range_costs_nothing(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, active):
        for stake in (9, 5001):
            with pytest.raises(BetOutOfRangeError):
                await manager.open_table(CHANNEL, "duel", 1, stake, announce_ok)
        assert await balance_of(db, 1) == 1000 and not active.is_active(1)


@pytest.mark.asyncio
async def test_a_member_already_in_a_game_cannot_host(tmp_path: Path) -> None:
    async with world(tmp_path) as (_, manager, active):
        active.claim(1)
        with pytest.raises(AlreadyInGameError):
            await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)


@pytest.mark.asyncio
async def test_not_enough_renown_to_host_leaves_no_claim(tmp_path: Path) -> None:
    async with world(tmp_path, balance=50) as (db, manager, active):
        with pytest.raises(InsufficientFundsError):
            await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        assert await balance_of(db, 1) == 50 and not active.is_active(1)
        assert manager.lobby_for(CHANNEL) is None


@pytest.mark.asyncio
async def test_a_failed_announcement_refunds_the_host(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, active):
        failing = AsyncMock(side_effect=RuntimeError("discord is down"))
        with pytest.raises(RuntimeError):
            await manager.open_table(CHANNEL, "duel", 1, 100, failing)
        assert await balance_of(db, 1) == 1000 and not active.is_active(1)
        assert not manager.is_busy(CHANNEL)
        assert await db.refund_open_bets() == 0


def test_an_unknown_game_is_an_invalid_choice() -> None:
    from yokai.gambling.tables import TableManager

    manager = TableManager(
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        welcome_bonus=0,
        min_bet=10,
        max_bet=5000,
        rake_percent=2,
        join_seconds=60,
        pick_seconds=45,
    )
    with pytest.raises(InvalidGameChoiceError):
        manager.spec("nonsense")


@pytest.mark.asyncio
async def test_a_game_that_is_not_built_yet_cannot_open_a_table_and_costs_nothing(
    tmp_path: Path,
) -> None:
    async with world(tmp_path) as (db, manager, active):
        for game in ("jackpot", "crash", "bjtable", "lottery"):
            with pytest.raises(InvalidGameChoiceError, match="not open yet"):
                await manager.open_table(CHANNEL, game, 1, 100, announce_ok)
        assert await balance_of(db, 1) == 1000 and not active.is_active(1)
        assert not manager.is_busy(CHANNEL)


@pytest.mark.asyncio
async def test_check_can_open_reports_busy_tables_and_busy_members(tmp_path: Path) -> None:
    async with world(tmp_path) as (_, manager, active):
        manager.check_can_open(CHANNEL, 1)  # fine
        active.claim(2)
        with pytest.raises(AlreadyInGameError):
            manager.check_can_open(CHANNEL, 2)
        await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        with pytest.raises(TableBusyError):
            manager.check_can_open(CHANNEL, 3)


# ---- joining and leaving ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_duel_guest_pays_the_hosts_stake(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, active):
        lobby = await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        seat = await manager.join(lobby, 2, None)
        assert seat.stake == 100 and lobby.is_full and lobby.pot == 200
        assert await balance_of(db, 2) == 900 and active.is_active(2)


@pytest.mark.asyncio
async def test_a_full_table_refuses_more_players(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, active):
        lobby = await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        await manager.join(lobby, 2, None)
        with pytest.raises(LobbyClosedError, match="full"):
            await manager.join(lobby, 3, None)
        assert await balance_of(db, 3) == 1000 and not active.is_active(3)


@pytest.mark.asyncio
async def test_joining_twice_is_refused(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, _):
        lobby = await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        with pytest.raises(AlreadyInGameError):
            await manager.join(lobby, 1, None)
        assert await balance_of(db, 1) == 900


@pytest.mark.asyncio
async def test_a_guest_who_cannot_afford_the_stake_gets_no_seat_and_no_claim(
    tmp_path: Path,
) -> None:
    async with world(tmp_path) as (db, manager, active):
        await db.ensure_wallet(4, 40)
        lobby = await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        with pytest.raises(InsufficientFundsError):
            await manager.join(lobby, 4, None)
        assert 4 not in lobby.seats and not active.is_active(4)
        assert await balance_of(db, 4) == 40


@pytest.mark.asyncio
async def test_a_guest_in_a_variable_stake_game_picks_their_own_stake(tmp_path: Path) -> None:
    async with world(tmp_path, specs={"duel": TRIO}) as (db, manager, _):
        lobby = await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        seat = await manager.join(lobby, 2, 250)
        assert seat.stake == 250 and lobby.pot == 350
        with pytest.raises(BetOutOfRangeError):
            await manager.join(lobby, 3, 5)


@pytest.mark.asyncio
async def test_leaving_refunds_and_frees_the_seat(tmp_path: Path) -> None:
    async with world(tmp_path, specs={"duel": TRIO}) as (db, manager, active):
        lobby = await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        await manager.join(lobby, 2, 100)
        await manager.leave(lobby, 2)
        assert 2 not in lobby.seats and await balance_of(db, 2) == 1000
        assert not active.is_active(2)


@pytest.mark.asyncio
async def test_the_host_cannot_leave_and_strangers_cannot_either(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, _):
        lobby = await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        with pytest.raises(LobbyClosedError, match="host"):
            await manager.leave(lobby, 1)
        with pytest.raises(NotAtTableError):
            await manager.leave(lobby, 3)
        assert await balance_of(db, 1) == 900


# ---- cancelling, beginning, aborting ----------------------------------------------------


@pytest.mark.asyncio
async def test_the_host_can_cancel_an_open_table_and_everyone_is_refunded(tmp_path: Path) -> None:
    async with world(tmp_path, specs={"duel": TRIO}) as (db, manager, active):
        lobby = await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        await manager.join(lobby, 2, 100)
        await manager.cancel(lobby, 1, is_admin=False)
        assert lobby.state is LobbyState.CANCELLED
        assert (await balance_of(db, 1), await balance_of(db, 2)) == (1000, 1000)
        assert not active.is_active(1) and not active.is_active(2)
        row = await db.get_multiplayer_channel(CHANNEL)
        assert row is not None and row.table_active is False
        assert await db.refund_open_bets() == 0


@pytest.mark.asyncio
async def test_an_admin_can_cancel_but_another_member_cannot(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, _):
        lobby = await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        with pytest.raises(NotHostError):
            await manager.cancel(lobby, 3, is_admin=False)
        assert lobby.state is LobbyState.OPEN
        await manager.cancel(lobby, 3, is_admin=True)
        assert lobby.state is LobbyState.CANCELLED and await balance_of(db, 1) == 1000


@pytest.mark.asyncio
async def test_a_started_table_cannot_be_cancelled(tmp_path: Path) -> None:
    async with world(tmp_path) as (_, manager, _active):
        lobby = await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        await manager.join(lobby, 2, None)
        assert await manager.begin(lobby) is BeginOutcome.STARTED
        with pytest.raises(LobbyClosedError, match="started"):
            await manager.cancel(lobby, 1, is_admin=True)
        assert lobby.state is LobbyState.RUNNING


@pytest.mark.asyncio
async def test_beginning_with_enough_players_starts_the_table(tmp_path: Path) -> None:
    async with world(tmp_path) as (_, manager, _active):
        lobby = await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        await manager.join(lobby, 2, None)
        assert await manager.begin(lobby) is BeginOutcome.STARTED
        assert lobby.state is LobbyState.RUNNING
        assert await manager.begin(lobby) is BeginOutcome.IGNORED


@pytest.mark.asyncio
async def test_beginning_below_the_minimum_cancels_and_refunds(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, active):
        lobby = await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        assert await manager.begin(lobby) is BeginOutcome.CANCELLED
        assert lobby.state is LobbyState.CANCELLED
        assert await balance_of(db, 1) == 1000 and not active.is_active(1)
        assert await manager.begin(lobby) is BeginOutcome.IGNORED


@pytest.mark.asyncio
async def test_only_the_host_can_start_early_and_only_with_enough_players(
    tmp_path: Path,
) -> None:
    async with world(tmp_path, specs={"duel": TRIO}) as (_, manager, _active):
        lobby = await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        with pytest.raises(LobbyClosedError, match="Not enough"):
            await manager.start_early(lobby, 1)
        await manager.join(lobby, 2, 100)
        with pytest.raises(NotHostError):
            await manager.start_early(lobby, 2)
        assert await manager.start_early(lobby, 1) is BeginOutcome.STARTED


@pytest.mark.asyncio
async def test_aborting_a_running_table_refunds_everyone_and_is_idempotent(
    tmp_path: Path,
) -> None:
    async with world(tmp_path) as (db, manager, active):
        lobby = await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        await manager.join(lobby, 2, None)
        await manager.begin(lobby)
        await manager.abort(lobby)
        await manager.abort(lobby)
        assert lobby.state is LobbyState.CANCELLED
        assert (await balance_of(db, 1), await balance_of(db, 2)) == (1000, 1000)
        assert not active.is_active(1) and not active.is_active(2)
        assert await db.get_pot() == 0


# ---- settling ---------------------------------------------------------------------------


async def _running_duel(manager, stake: int = 100):  # type: ignore[no-untyped-def]
    lobby = await manager.open_table(CHANNEL, "duel", 1, stake, announce_ok)
    await manager.join(lobby, 2, None)
    await manager.begin(lobby)
    return lobby


@pytest.mark.asyncio
async def test_settling_pays_the_winner_and_credits_only_the_rake(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, active):
        lobby = await _running_duel(manager)
        balances = await manager.settle(lobby, {1: 196, 2: 0})
        assert balances == {1: 1096, 2: 900}
        assert lobby.state is LobbyState.SETTLED
        assert await db.get_pot() == 4
        assert not active.is_active(1) and not active.is_active(2)
        row = await db.get_multiplayer_channel(CHANNEL)
        assert row is not None and row.table_active is False
        assert await db.refund_open_bets() == 0


@pytest.mark.asyncio
async def test_settling_needs_a_payout_for_every_seat(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, _):
        lobby = await _running_duel(manager)
        with pytest.raises(ValueError):
            await manager.settle(lobby, {1: 196})
        assert lobby.state is LobbyState.RUNNING


@pytest.mark.asyncio
async def test_a_failed_settlement_refunds_everyone(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, active):
        lobby = await _running_duel(manager)
        with patch.object(db, "settle_lobby", new=AsyncMock(side_effect=RuntimeError("disk"))):
            with pytest.raises(RuntimeError):
                await manager.settle(lobby, {1: 196, 2: 0})
        assert lobby.state is LobbyState.CANCELLED
        assert (await balance_of(db, 1), await balance_of(db, 2)) == (1000, 1000)
        assert not active.is_active(1) and await db.get_pot() == 0


@pytest.mark.asyncio
async def test_a_settled_table_cannot_be_settled_again(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, _):
        lobby = await _running_duel(manager)
        await manager.settle(lobby, {1: 196, 2: 0})
        with pytest.raises(LobbyClosedError):
            await manager.settle(lobby, {1: 196, 2: 0})
        assert await balance_of(db, 1) == 1096


# ---- the duel ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolving_a_duel_pays_the_closer_pick_minus_the_rake(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, _):
        lobby = await _running_duel(manager)
        duel = DuelRound([1, 2], target=5)
        duel.submit(1, 9)
        duel.submit(2, 4)
        result = await manager.resolve_duel(lobby, duel)
        assert result.outcome.winner_id == 2
        assert (result.prize, result.rake) == (196, 4)
        assert result.balances == {1: 900, 2: 1096}
        assert await db.get_pot() == 4 and lobby.state is LobbyState.SETTLED


@pytest.mark.asyncio
async def test_a_tied_duel_refunds_both_and_adds_nothing_to_the_pot(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, active):
        lobby = await _running_duel(manager)
        duel = DuelRound([1, 2], target=5)
        duel.submit(1, 3)
        duel.submit(2, 7)
        result = await manager.resolve_duel(lobby, duel)
        assert result.outcome.is_tie and (result.prize, result.rake) == (0, 0)
        assert result.balances == {1: 1000, 2: 1000}
        assert await db.get_pot() == 0 and lobby.state is LobbyState.SETTLED
        assert not active.is_active(1) and await db.refund_open_bets() == 0


@pytest.mark.asyncio
async def test_resolving_fills_a_missing_pick_at_random(tmp_path: Path) -> None:
    async with world(tmp_path) as (_, manager, _active):
        lobby = await _running_duel(manager)
        duel = DuelRound([1, 2], target=5)
        duel.submit(1, 5)
        result = await manager.resolve_duel(lobby, duel)
        assert set(result.outcome.picks) == {1, 2}
        assert 1 <= result.outcome.picks[2] <= 10


@pytest.mark.asyncio
async def test_a_zero_rake_pays_the_whole_pot(tmp_path: Path) -> None:
    async with world(tmp_path, rake_percent=0) as (db, manager, _):
        lobby = await _running_duel(manager)
        duel = DuelRound([1, 2], target=5)
        duel.submit(1, 5)
        duel.submit(2, 1)
        result = await manager.resolve_duel(lobby, duel)
        assert (result.prize, result.rake) == (200, 0)
        assert await db.get_pot() == 0 and await balance_of(db, 1) == 1100
