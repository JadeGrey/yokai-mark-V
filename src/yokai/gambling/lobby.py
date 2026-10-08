"""Lobby state machine shared by every multiplayer table game. No Discord, no database."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from yokai.errors import LobbyClosedError, NotAtTableError


class LobbyState(Enum):
    OPEN = "open"
    RUNNING = "running"
    SETTLED = "settled"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class GameSpec:
    """Static facts about a table game."""

    name: str
    label: str
    min_players: int
    max_players: int
    equal_stakes: bool = False
    window_factor: float = 1.0  # share of GAMBLING_LOBBY_JOIN_SECONDS this game waits
    rules: str = ""
    available: bool = True  # False until the game's phase ships: listed, but not playable


GAME_SPECS: dict[str, GameSpec] = {
    "duel": GameSpec(
        name="duel",
        label="Duel",
        min_players=2,
        max_players=2,
        equal_stakes=True,
        rules=(
            "Two players, one hidden number from 1 to 10. Each picks a number in secret; "
            "the pick closest to the hidden number takes the pot. A tie refunds both stakes."
        ),
    ),
    # Listed in /admin set_multiplayer already, but refused until their phase (M3, M4) ships.
    "jackpot": GameSpec(
        name="jackpot",
        label="Jackpot",
        min_players=2,
        max_players=10,
        rules=(
            "Everyone puts a stake into one pot. One player is drawn at random, weighted by "
            "stake, and takes the pot minus the house rake."
        ),
        available=False,
    ),
    "crash": GameSpec(
        name="crash",
        label="Crash",
        min_players=1,
        max_players=10,
        window_factor=0.5,
        rules=(
            "A multiplier climbs until it crashes. Cash out before it does to win your stake "
            "times the multiplier."
        ),
        available=False,
    ),
    "bjtable": GameSpec(
        name="bjtable",
        label="Blackjack table",
        min_players=2,
        max_players=5,
        window_factor=0.5,
        rules="Blackjack against one shared dealer. Every player plays their own hand.",
        available=False,
    ),
    "lottery": GameSpec(
        name="lottery",
        label="Lottery",
        min_players=1,
        max_players=1,
        rules=(
            "Buy numbered tickets. A weekly draw picks one number; holders split the pot, "
            "and it rolls over when nobody wins."
        ),
        available=False,
    ),
}


@dataclass(frozen=True)
class Seat:
    user_id: int
    stake: int
    bet_id: int


class Lobby:
    """One table: who is seated, what state it is in, and the lock that serialises changes."""

    def __init__(
        self, lobby_id: str, spec: GameSpec, channel_id: int, host_id: int, closes_at: float
    ) -> None:
        self.id = lobby_id
        self.spec = spec
        self.channel_id = channel_id
        self.host_id = host_id
        self.closes_at = closes_at
        self.state = LobbyState.OPEN
        self.seats: dict[int, Seat] = {}
        self.lock = asyncio.Lock()
        self.message_id: Optional[int] = None

    @property
    def is_active(self) -> bool:
        return self.state in (LobbyState.OPEN, LobbyState.RUNNING)

    @property
    def is_full(self) -> bool:
        return len(self.seats) >= self.spec.max_players

    @property
    def can_start(self) -> bool:
        return len(self.seats) >= self.spec.min_players

    @property
    def pot(self) -> int:
        return sum(seat.stake for seat in self.seats.values())

    @property
    def host_seat(self) -> Seat:
        return self.seats[self.host_id]

    def add_seat(self, seat: Seat) -> None:
        if self.state is not LobbyState.OPEN:
            raise LobbyClosedError()
        if seat.user_id in self.seats:
            raise LobbyClosedError("You are already seated at this table.", user_hint=None)
        if self.is_full:
            raise LobbyClosedError("This table is full.", user_hint=None)
        self.seats[seat.user_id] = seat

    def remove_seat(self, user_id: int) -> Seat:
        if self.state is not LobbyState.OPEN:
            raise LobbyClosedError()
        if user_id not in self.seats:
            raise NotAtTableError()
        if user_id == self.host_id:
            raise LobbyClosedError(
                "The host cannot leave their own table.",
                user_hint="Cancel the table instead.",
            )
        return self.seats.pop(user_id)

    def mark_running(self) -> None:
        if self.state is not LobbyState.OPEN:
            raise LobbyClosedError()
        self.state = LobbyState.RUNNING

    def close(self, state: LobbyState) -> None:
        """Move to SETTLED or CANCELLED; a closed lobby never changes again."""
        if state not in (LobbyState.SETTLED, LobbyState.CANCELLED):
            raise ValueError("close() takes SETTLED or CANCELLED")
        if not self.is_active:
            raise LobbyClosedError()
        self.state = state
