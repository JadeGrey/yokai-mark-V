"""Shared fixtures for the multiplayer table tests: a real database and a wired TableManager."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Iterable, Mapping, Optional

from yokai.gambling.lobby import GameSpec, Lobby
from yokai.gambling.sessions import ActiveGames
from yokai.gambling.tables import TableManager
from yokai.storage.db import Database

NOW = 1_000_000.0
CHANNEL = 10
ANNOUNCED_MESSAGE_ID = 777


async def announce_ok(lobby: Lobby) -> int:
    return ANNOUNCED_MESSAGE_ID


@asynccontextmanager
async def world(
    tmp_path: Path,
    *,
    users: Iterable[int] = (1, 2, 3),
    balance: int = 1000,
    specs: Optional[Mapping[str, GameSpec]] = None,
    rake_percent: int = 2,
    game: str = "duel",
) -> AsyncIterator[tuple[Database, TableManager, ActiveGames]]:
    db = Database(tmp_path / "tables.db")
    await db.initialize()
    try:
        for user_id in users:
            await db.ensure_wallet(user_id, balance)
        await db.set_multiplayer_channel(CHANNEL, game, 1)
        active = ActiveGames()
        manager = TableManager(
            db,
            active,
            welcome_bonus=0,
            min_bet=10,
            max_bet=5000,
            rake_percent=rake_percent,
            join_seconds=60,
            pick_seconds=45,
            specs=specs,
            clock=lambda: NOW,
        )
        yield db, manager, active
    finally:
        await db.close()


async def balance_of(db: Database, user_id: int) -> int:
    wallet = await db.get_wallet(user_id)
    assert wallet is not None
    return wallet.balance
