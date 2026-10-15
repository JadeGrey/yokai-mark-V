"""Database access for the Guild RPG, on the shared SQLite connection."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Mapping, Optional, Sequence

import aiosqlite

from yokai.errors import NoCharacterError
from yokai.rpg import guild
from yokai.rpg.audit import record_admin_action
from yokai.rpg.gear_store import (
    DropResult,
    GearSettlement,
    GearWear,
    add_drops,
    apply_wear,
)
from yokai.rpg.models import Character, StreakRules, XpCurve

if TYPE_CHECKING:
    from yokai.storage.db import Database

_CHARACTER_COLUMNS = (
    "user_id, level, xp, rank, battles_won, battles_lost, created_ts, "
    "attempts_used, attempts_day, streak, last_active_day"
)

FIGHT_OPEN = "open"
FIGHT_WON = "won"
FIGHT_LOST = "lost"
FIGHT_ABORTED = "aborted"


@dataclass(frozen=True, slots=True)
class Settlement:
    """What one participant received when a fight was settled."""

    user_id: int
    xp_gained: int
    renown_gained: int
    level_before: int
    level_after: int
    xp_now: int
    wear: tuple[GearWear, ...] = ()
    drops: tuple[DropResult, ...] = ()

    @property
    def levels_gained(self) -> int:
        return self.level_after - self.level_before


def _character_from_row(row: Sequence[Any]) -> Character:
    return Character(
        user_id=row[0],
        level=row[1],
        xp=row[2],
        rank=row[3],
        battles_won=row[4],
        battles_lost=row[5],
        created_ts=row[6],
        attempts_used=row[7],
        attempts_day=row[8],
        streak=row[9],
        last_active_day=row[10],
    )


async def _fetch_character(conn: aiosqlite.Connection, user_id: int) -> Optional[Character]:
    query = f"SELECT {_CHARACTER_COLUMNS} FROM rpg_characters WHERE user_id = ?;"
    async with conn.execute(query, (user_id,)) as cursor:
        row = await cursor.fetchone()
    return _character_from_row(row) if row else None


class RpgStore:
    """Reads and writes RPG rows. Later phases add items, shop stock and trades here."""

    def __init__(self, db: Database) -> None:
        self._db = db

    # ------------------------------------------------------------------ characters

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

    # ------------------------------------------------------------------ admin tools

    async def admin_set_level(self, actor_id: int, user_id: int, level: int, cap: int) -> Character:
        """Set a member's level (clamped to 1..cap) and clear their XP. Audited."""
        new_level = max(1, min(level, cap))
        async with self._db.transaction() as conn:
            before = await _fetch_character(conn, user_id)
            if before is None:
                raise NoCharacterError(own=False)
            await conn.execute(
                "UPDATE rpg_characters SET level = ?, xp = 0 WHERE user_id = ?;",
                (new_level, user_id),
            )
            await record_admin_action(
                conn, actor_id, "set_level", user_id, {"from": before.level, "to": new_level}
            )
            after = await _fetch_character(conn, user_id)
        assert after is not None
        return after

    async def admin_set_rank(self, actor_id: int, user_id: int, rank: str) -> Character:
        """Set a member's rank. The caller validates that the rank exists. Audited."""
        async with self._db.transaction() as conn:
            before = await _fetch_character(conn, user_id)
            if before is None:
                raise NoCharacterError(own=False)
            await conn.execute(
                "UPDATE rpg_characters SET rank = ? WHERE user_id = ?;", (rank, user_id)
            )
            await record_admin_action(
                conn, actor_id, "set_rank", user_id, {"from": before.rank, "to": rank}
            )
            after = await _fetch_character(conn, user_id)
        assert after is not None
        return after

    async def admin_reset_character(self, actor_id: int, user_id: int) -> bool:
        """Delete a member's character and all their gear (never their Renown). Audited."""
        async with self._db.transaction() as conn:
            before = await _fetch_character(conn, user_id)
            if before is None:
                return False
            async with conn.execute(
                "SELECT COUNT(*) FROM rpg_items WHERE owner_id = ?;", (user_id,)
            ) as cursor:
                row = await cursor.fetchone()
            await conn.execute("DELETE FROM rpg_equipment WHERE user_id = ?;", (user_id,))
            await conn.execute("DELETE FROM rpg_items WHERE owner_id = ?;", (user_id,))
            await conn.execute("DELETE FROM rpg_characters WHERE user_id = ?;", (user_id,))
            await record_admin_action(
                conn,
                actor_id,
                "reset_character",
                user_id,
                {"level": before.level, "rank": before.rank, "items": int(row[0]) if row else 0},
            )
        return True

    # ------------------------------------------------------------------ the board

    async def get_board(self, day: str) -> Optional[list[str]]:
        """The stored board for a day, or None if nobody has opened it yet (read-only)."""
        async with self._db.conn.execute(
            "SELECT mission_ids FROM rpg_boards WHERE day_key = ?;", (day,)
        ) as cursor:
            row = await cursor.fetchone()
        return list(json.loads(row[0])) if row else None

    async def get_or_create_board(
        self, day: str, build: Callable[[], list[str]], now: Optional[int] = None
    ) -> list[str]:
        """The day's mission ids. The first caller of the day draws and stores them."""
        created_ts = int(time.time()) if now is None else now
        async with self._db.transaction() as conn:
            async with conn.execute(
                "SELECT mission_ids FROM rpg_boards WHERE day_key = ?;", (day,)
            ) as cursor:
                row = await cursor.fetchone()
            if row:
                return list(json.loads(row[0]))
            ids = build()
            await conn.execute(
                "INSERT INTO rpg_boards (day_key, mission_ids, created_ts) VALUES (?, ?, ?);",
                (day, json.dumps(ids), created_ts),
            )
        return ids

    # ------------------------------------------------------------------ fights

    async def begin_fight(
        self,
        *,
        mission_id: str,
        kind: str,
        user_ids: Sequence[int],
        cost: int,
        today: str,
        base_attempts: int,
        streak_rules: StreakRules,
        resets_ts: Optional[int] = None,
        now: Optional[int] = None,
    ) -> int:
        """Spend `cost` attempts for every member and open a fight row, all or nothing.

        Raises NoAttemptsError or NoCharacterError (changing nothing) if any member cannot.
        """
        ts = int(time.time()) if now is None else now
        participants: dict[str, dict[str, Any]] = {}
        async with self._db.transaction() as conn:
            for user_id in user_ids:
                character = await _fetch_character(conn, user_id)
                if character is None:
                    raise NoCharacterError(own=True)
                plan = guild.plan_spend(
                    character, cost, today, base_attempts, streak_rules, resets_ts
                )
                await conn.execute(
                    "UPDATE rpg_characters SET attempts_used = ?, attempts_day = ?, "
                    "streak = ?, last_active_day = ? WHERE user_id = ?;",
                    (plan.used, plan.day, plan.streak, plan.last_active_day, user_id),
                )
                participants[str(user_id)] = {"cost": cost, "day": today}
            cursor = await conn.execute(
                "INSERT INTO rpg_fights (ts, mission_id, kind, status, participants) "
                "VALUES (?, ?, ?, ?, ?);",
                (ts, mission_id, kind, FIGHT_OPEN, json.dumps(participants)),
            )
            fight_id = cursor.lastrowid
        assert fight_id is not None
        return fight_id

    async def set_fight_thread(self, fight_id: int, thread_id: int) -> None:
        async with self._db.transaction() as conn:
            await conn.execute(
                "UPDATE rpg_fights SET thread_id = ? WHERE id = ?;", (thread_id, fight_id)
            )

    async def settle_fight(
        self,
        fight_id: int,
        *,
        won: bool,
        rewards: Mapping[int, guild.Reward],
        level_cap: int,
        xp_curve: XpCurve,
        welcome_bonus: int,
        gear: Optional[GearSettlement] = None,
        now: Optional[int] = None,
    ) -> Optional[dict[int, Settlement]]:
        """Close an open fight and pay the participants, once.

        Returns None (and changes nothing) if the fight is not open any more, so a duplicate
        call can never pay twice.
        """
        status = FIGHT_WON if won else FIGHT_LOST
        results: dict[int, Settlement] = {}
        async with self._db.transaction() as conn:
            async with conn.execute(
                "SELECT participants FROM rpg_fights WHERE id = ? AND status = ?;",
                (fight_id, FIGHT_OPEN),
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                return None
            await conn.execute("UPDATE rpg_fights SET status = ? WHERE id = ?;", (status, fight_id))
            for raw_id in json.loads(row[0]):
                user_id = int(raw_id)
                character = await _fetch_character(conn, user_id)
                if character is None:
                    continue
                if not won:
                    await conn.execute(
                        "UPDATE rpg_characters SET battles_lost = battles_lost + 1 "
                        "WHERE user_id = ?;",
                        (user_id,),
                    )
                    wear = await self._wear(conn, user_id, gear, won=False)
                    results[user_id] = Settlement(
                        user_id,
                        0,
                        0,
                        character.level,
                        character.level,
                        character.xp,
                        wear=tuple(wear),
                    )
                    continue
                reward = rewards.get(user_id, guild.Reward(0, 0))
                level, xp, _ = guild.apply_xp(
                    character.level, character.xp, reward.xp, level_cap, xp_curve
                )
                await conn.execute(
                    "UPDATE rpg_characters SET level = ?, xp = ?, battles_won = battles_won + 1 "
                    "WHERE user_id = ?;",
                    (level, xp, user_id),
                )
                if reward.renown > 0:
                    await self._db.credit_within_transaction(
                        user_id, reward.renown, "rpg_reward", welcome_bonus
                    )
                wear = await self._wear(conn, user_id, gear, won=True)
                dropped: list[DropResult] = []
                if gear is not None and gear.drops.get(user_id):
                    dropped = await add_drops(
                        conn,
                        self._db,
                        user_id,
                        gear.drops[user_id],
                        gear,
                        welcome_bonus,
                        int(time.time()) if now is None else now,
                    )
                results[user_id] = Settlement(
                    user_id,
                    reward.xp,
                    reward.renown,
                    character.level,
                    level,
                    xp,
                    wear=tuple(wear),
                    drops=tuple(dropped),
                )
        return results

    @staticmethod
    async def _wear(
        conn: aiosqlite.Connection, user_id: int, gear: Optional[GearSettlement], *, won: bool
    ) -> list[GearWear]:
        """Wear down the member's equipped gear for a finished fight (a loss wears more)."""
        if gear is None:
            return []
        points = gear.rules.win_wear if won else gear.rules.loss_wear
        return await apply_wear(conn, user_id, points, gear.items)

    async def abort_fight(self, fight_id: int) -> bool:
        """Cancel an open fight and refund its attempts. False if it was not open."""
        async with self._db.transaction() as conn:
            return await self._abort_in_transaction(conn, fight_id)

    async def abort_open_fights(self) -> int:
        """Startup recovery: abort every fight a restart left open. Returns how many."""
        async with self._db.transaction() as conn:
            async with conn.execute(
                "SELECT id FROM rpg_fights WHERE status = ?;", (FIGHT_OPEN,)
            ) as cursor:
                ids = [row[0] for row in await cursor.fetchall()]
            for fight_id in ids:
                await self._abort_in_transaction(conn, fight_id)
        return len(ids)

    @staticmethod
    async def _abort_in_transaction(conn: aiosqlite.Connection, fight_id: int) -> bool:
        async with conn.execute(
            "SELECT participants FROM rpg_fights WHERE id = ? AND status = ?;",
            (fight_id, FIGHT_OPEN),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return False
        for raw_id, info in json.loads(row[0]).items():
            # Only refund while the counter still belongs to the day the attempts were spent on.
            await conn.execute(
                "UPDATE rpg_characters SET attempts_used = MAX(0, attempts_used - ?) "
                "WHERE user_id = ? AND attempts_day = ?;",
                (info["cost"], int(raw_id), info["day"]),
            )
        await conn.execute(
            "UPDATE rpg_fights SET status = ? WHERE id = ?;", (FIGHT_ABORTED, fight_id)
        )
        return True

    async def purge_history(self, older_than_ts: int) -> tuple[int, int]:
        """Delete old boards and finished fights. Returns (boards, fights) removed."""
        async with self._db.transaction() as conn:
            boards = await conn.execute(
                "DELETE FROM rpg_boards WHERE created_ts < ?;", (older_than_ts,)
            )
            await conn.execute("DELETE FROM rpg_shops WHERE created_ts < ?;", (older_than_ts,))
            fights = await conn.execute(
                "DELETE FROM rpg_fights WHERE ts < ? AND status != ?;",
                (older_than_ts, FIGHT_OPEN),
            )
            return boards.rowcount, fights.rowcount
