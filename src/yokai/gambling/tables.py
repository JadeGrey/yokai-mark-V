"""Opens, fills, runs and closes multiplayer tables; the only code that escrows for them."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Awaitable, Callable, Mapping, Optional

import aiosqlite

from yokai.errors import (
    AlreadyInGameError,
    BetNotOpenError,
    InvalidGameChoiceError,
    LobbyClosedError,
    NotHostError,
    TableBusyError,
)
from yokai.gambling.engines.duel import DuelOutcome, DuelRound, prize_after_rake
from yokai.gambling.escrow import validate_bet
from yokai.gambling.lobby import GAME_SPECS, GameSpec, Lobby, LobbyState, Seat
from yokai.gambling.rng import new_rng
from yokai.gambling.sessions import ActiveGames

if TYPE_CHECKING:
    from yokai.storage.db import Database

logger = logging.getLogger(__name__)

# A refund that fails (a busy database file) is tried a few times before giving up; a stake that
# still cannot be returned stays escrowed and the startup sweep returns it.
REFUND_ATTEMPTS = 3
REFUND_RETRY_DELAY_S = 0.2

# Posts the table's message in its channel and returns the new message's id.
Announce = Callable[[Lobby], Awaitable[int]]


class BeginOutcome(Enum):
    STARTED = "started"
    CANCELLED = "cancelled"  # too few players; everyone was refunded
    IGNORED = "ignored"  # the table was no longer open


@dataclass(frozen=True)
class DuelResult:
    outcome: DuelOutcome
    prize: int
    rake: int
    balances: dict[int, int]  # user id -> balance after the duel


class TableManager:
    """Owns every live table. All stake movements for tables go through here."""

    def __init__(
        self,
        db: Database,
        active_games: ActiveGames,
        *,
        welcome_bonus: int,
        min_bet: int,
        max_bet: int,
        rake_percent: int,
        join_seconds: int,
        pick_seconds: int,
        specs: Optional[Mapping[str, GameSpec]] = None,
        clock: Callable[[], float] = time.time,
        rng_factory: Callable[[], random.Random] = new_rng,
    ) -> None:
        self.db = db
        self.active_games = active_games
        self.welcome_bonus = welcome_bonus
        self.min_bet = min_bet
        self.max_bet = max_bet
        self.rake_percent = rake_percent
        self.join_seconds = join_seconds
        self.pick_seconds = pick_seconds
        self.specs: Mapping[str, GameSpec] = specs if specs is not None else GAME_SPECS
        self.clock = clock
        self.rng_factory = rng_factory
        self.lobbies: dict[int, Lobby] = {}
        self._channel_locks: dict[int, asyncio.Lock] = {}

    # ---- lookups -------------------------------------------------------------------------

    def spec(self, game: str) -> GameSpec:
        try:
            return self.specs[game]
        except KeyError:
            raise InvalidGameChoiceError(f"Unknown game: {game}.") from None

    def lobby_for(self, channel_id: int) -> Optional[Lobby]:
        return self.lobbies.get(channel_id)

    def is_busy(self, channel_id: int) -> bool:
        lobby = self.lobbies.get(channel_id)
        return lobby is not None and lobby.is_active

    def check_can_open(self, channel_id: int, user_id: int) -> None:
        """Cheap pre-check used before showing the stake form."""
        if self.is_busy(channel_id):
            raise TableBusyError()
        if self.active_games.is_active(user_id):
            raise AlreadyInGameError()

    def _channel_lock(self, channel_id: int) -> asyncio.Lock:
        return self._channel_locks.setdefault(channel_id, asyncio.Lock())

    # ---- opening -------------------------------------------------------------------------

    async def open_table(
        self, channel_id: int, game: str, host_id: int, stake: int, announce: Announce
    ) -> Lobby:
        """Escrow the host's stake and open a table; `announce` posts its message."""
        spec = self.spec(game)
        if not spec.available:
            raise InvalidGameChoiceError(f"{spec.label} tables are not open yet.")
        validate_bet(stake, self.min_bet, self.max_bet)
        async with self._channel_lock(channel_id):
            if self.is_busy(channel_id):
                raise TableBusyError()
            self.active_games.claim(host_id)
            lobby_id = uuid.uuid4().hex
            try:
                bet_id = await self.db.place_bet(
                    host_id, stake, game, self.welcome_bonus, lobby_id=lobby_id
                )
            except BaseException:
                self.active_games.release(host_id)
                raise
            lobby = Lobby(
                lobby_id,
                spec,
                channel_id,
                host_id,
                closes_at=self.clock() + self.join_seconds * spec.window_factor,
            )
            lobby.add_seat(Seat(host_id, stake, bet_id))
            self.lobbies[channel_id] = lobby
            try:
                lobby.message_id = await announce(lobby)
                await self.db.set_table_message(channel_id, lobby.message_id, active=True)
            except BaseException:
                async with lobby.lock:
                    await self._refund_all_locked(lobby, LobbyState.CANCELLED)
                raise
            return lobby

    # ---- seats ---------------------------------------------------------------------------

    async def join(self, lobby: Lobby, user_id: int, stake: Optional[int] = None) -> Seat:
        """Seat a member, escrowing their stake (the host's stake when the game is equal-stake)."""
        async with lobby.lock:
            if lobby.state is not LobbyState.OPEN:
                raise LobbyClosedError()
            if lobby.is_full:
                raise LobbyClosedError("This table is full.", user_hint=None)
            amount = lobby.host_seat.stake if lobby.spec.equal_stakes or stake is None else stake
            validate_bet(amount, self.min_bet, self.max_bet)
            self.active_games.claim(user_id)
            try:
                bet_id = await self.db.place_bet(
                    user_id, amount, lobby.spec.name, self.welcome_bonus, lobby_id=lobby.id
                )
            except BaseException:
                self.active_games.release(user_id)
                raise
            seat = Seat(user_id, amount, bet_id)
            try:
                lobby.add_seat(seat)
            except BaseException:
                with contextlib.suppress(BetNotOpenError):
                    await self.db.refund_bet(bet_id)
                self.active_games.release(user_id)
                raise
            return seat

    async def leave(self, lobby: Lobby, user_id: int) -> None:
        async with lobby.lock:
            lobby.check_can_leave(user_id)  # raises for strangers, the host, closed tables
            closed, _ = await self._refund_seat(lobby.seats[user_id])
            if not closed:
                # The stake is still escrowed: keep the seat and the claim, let them try again.
                raise LobbyClosedError(
                    "I couldn't return your stake just now.",
                    user_hint="Try Leave again in a moment.",
                )
            lobby.remove_seat(user_id)
            self.active_games.release(user_id)

    # ---- lifecycle -----------------------------------------------------------------------

    async def cancel(self, lobby: Lobby, user_id: int, *, is_admin: bool) -> None:
        """Cancel an unstarted table (host or admin) and refund every seat."""
        async with lobby.lock:
            if lobby.state is LobbyState.RUNNING:
                raise LobbyClosedError(
                    "A table that has started can't be cancelled.", user_hint=None
                )
            if lobby.state is not LobbyState.OPEN:
                raise LobbyClosedError()
            if user_id != lobby.host_id and not is_admin:
                raise NotHostError()
            await self._refund_all_locked(lobby, LobbyState.CANCELLED)

    async def begin(self, lobby: Lobby) -> BeginOutcome:
        """Start an open table, or cancel and refund it when it has too few players."""
        async with lobby.lock:
            if lobby.state is not LobbyState.OPEN:
                return BeginOutcome.IGNORED
            if lobby.can_start:
                lobby.mark_running()
                return BeginOutcome.STARTED
            await self._refund_all_locked(lobby, LobbyState.CANCELLED)
            return BeginOutcome.CANCELLED

    async def start_early(self, lobby: Lobby, user_id: int) -> BeginOutcome:
        if user_id != lobby.host_id:
            raise NotHostError()
        if not lobby.can_start:
            raise LobbyClosedError(
                "Not enough players yet.",
                user_hint=f"This game needs at least {lobby.spec.min_players} players.",
            )
        return await self.begin(lobby)

    async def abort(self, lobby: Lobby) -> None:
        """Refund and cancel a table that hit an error; safe to call more than once."""
        async with lobby.lock:
            if not lobby.is_active:
                return
            await self._refund_all_locked(lobby, LobbyState.CANCELLED)

    async def settle(self, lobby: Lobby, payouts: Mapping[int, int]) -> dict[int, int]:
        """Pay out a running table in one transaction; returns each member's new balance."""
        async with lobby.lock:
            if lobby.state is not LobbyState.RUNNING:
                raise LobbyClosedError()
            if set(payouts) != set(lobby.seats):
                raise ValueError("payouts must cover every seat, and only seats")
            by_bet = {lobby.seats[user_id].bet_id: amount for user_id, amount in payouts.items()}
            try:
                balances = await self.db.settle_lobby(by_bet)
            except BaseException:
                await self._refund_all_locked(lobby, LobbyState.CANCELLED)
                raise
            self._finish(lobby, LobbyState.SETTLED)
            await self._mark_channel_idle(lobby)
            return {user_id: balances[seat.bet_id] for user_id, seat in lobby.seats.items()}

    async def refund_round(self, lobby: Lobby) -> dict[int, int]:
        """End a running table by returning every stake (a tie); returns new balances."""
        async with lobby.lock:
            if lobby.state is not LobbyState.RUNNING:
                raise LobbyClosedError()
            return await self._refund_all_locked(lobby, LobbyState.SETTLED)

    # ---- duel ----------------------------------------------------------------------------

    async def resolve_duel(self, lobby: Lobby, duel: DuelRound) -> DuelResult:
        """Finish a duel: fill missing picks, then pay the winner or refund a tie."""
        duel.fill_missing(self.rng_factory())
        outcome = duel.outcome()
        if outcome.winner_id is None:
            balances = await self.refund_round(lobby)
            return DuelResult(outcome, prize=0, rake=0, balances=balances)
        prize, rake = prize_after_rake(lobby.pot, self.rake_percent)
        payouts = {
            user_id: (prize if user_id == outcome.winner_id else 0) for user_id in lobby.seats
        }
        balances = await self.settle(lobby, payouts)
        return DuelResult(outcome, prize=prize, rake=rake, balances=balances)

    # ---- internals -----------------------------------------------------------------------

    def _finish(self, lobby: Lobby, state: LobbyState) -> None:
        lobby.close(state)
        for user_id in lobby.seats:
            self.active_games.release(user_id)

    async def _refund_seat(self, seat: Seat) -> tuple[bool, Optional[int]]:
        """Return one stake. Gives (closed, balance): `closed` is False when the bet is still open.

        A bet that is already closed counts as closed (with no balance). A failing refund is
        retried; if it never succeeds the stake stays escrowed for the next startup sweep.
        """
        for attempt in range(1, REFUND_ATTEMPTS + 1):
            try:
                return True, await self.db.refund_bet(seat.bet_id)
            except BetNotOpenError:
                return True, None
            except Exception as exc:
                logger.error(
                    "Refund of bet %d failed (attempt %d of %d): %s",
                    seat.bet_id,
                    attempt,
                    REFUND_ATTEMPTS,
                    exc,
                )
                if attempt < REFUND_ATTEMPTS:
                    await asyncio.sleep(REFUND_RETRY_DELAY_S * attempt)
        return False, None

    async def _refund_all_locked(self, lobby: Lobby, state: LobbyState) -> dict[int, int]:
        """Refund every open stake and close the lobby. The caller holds `lobby.lock`.

        A member's one-game claim is released only once their stake is confirmed returned, so a
        refund that keeps failing never frees someone whose Renown is still escrowed.
        """
        balances: dict[int, int] = {}
        confirmed: set[int] = set()
        try:
            for user_id, seat in list(lobby.seats.items()):
                closed, balance = await self._refund_seat(seat)
                if balance is not None:
                    balances[user_id] = balance
                if closed:
                    confirmed.add(user_id)
        finally:
            if lobby.is_active:
                lobby.close(state)
            for user_id in confirmed:
                self.active_games.release(user_id)
            await self._mark_channel_idle(lobby)
        return balances

    async def _mark_channel_idle(self, lobby: Lobby) -> None:
        try:
            await self.db.set_table_inactive(lobby.channel_id)
        except aiosqlite.Error as exc:
            logger.error("Could not mark channel %d idle: %s", lobby.channel_id, exc)
