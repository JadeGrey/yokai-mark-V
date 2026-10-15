"""Database access for the Guild RPG, on the shared SQLite connection."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Optional, Sequence

import aiosqlite

from yokai.rpg.models import Character

if TYPE_CHECKING:
    from yokai.storage.db import Database

_CHARACTER_COLUMNS = "user_id, level, xp, rank, battles_won, battles_lost, created_ts"


def _character_from_row(row: Sequence[Any]) -> Character:
    return Character(
        user_id=row[0],
        level=row[1],
        xp=row[2],
        rank=row[3],
        battles_won=row[4],
        battles_lost=row[5],
        created_ts=row[6],
    )


async def _fetch_character(conn: aiosqlite.Connection, user_id: int) -> Optional[Character]:
    query = f"SELECT {_CHARACTER_COLUMNS} FROM rpg_characters WHERE user_id = ?;"
    async with conn.execute(query, (user_id,)) as cursor:
        row = await cursor.fetchone()
    return _character_from_row(row) if row else None


class RpgStore:
    """Reads and writes RPG rows. Later phases add items, boards and fights here."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def get_character(self, user_id: int) -> Optional[Character]:
        """Read-only lookup; never creates a character."""
        return await _fetch_character(self._db.conn, user_id)

    async def create_character(
        self, user_id: int, start_rank: str, now: Optional[int] = None
    ) -> tuple[Character, bool]:
        """Create the character if absent. Returns (character, created).

        `created` is True only for the call that inserted the row, so simultaneous joins by one
        user produce exactly one character and exactly one True.
        """
        created_ts = int(time.time()) if now is None else now
        async with self._db.transaction() as conn:
            cursor = await conn.execute(
                "INSERT OR IGNORE INTO rpg_characters (user_id, rank, created_ts) "
                "VALUES (?, ?, ?);",
                (user_id, start_rank, created_ts),
            )
            created = cursor.rowcount == 1
            character = await _fetch_character(conn, user_id)
        assert character is not None
        return character, created
