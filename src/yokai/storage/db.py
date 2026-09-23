"""SQLite persistence layer using aiosqlite, WAL mode, and PRAGMA user_version migrations."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

import aiosqlite

logger = logging.getLogger(__name__)

CURRENT_SCHEMA_VERSION = 1

MIGRATION_V1 = """
CREATE TABLE IF NOT EXISTS command_log (
  id INTEGER PRIMARY KEY,
  ts INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  command TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_command_log_ts ON command_log(ts);
CREATE INDEX IF NOT EXISTS idx_command_log_user ON command_log(user_id);

CREATE TABLE IF NOT EXISTS play_events (
  id INTEGER PRIMARY KEY,
  ts INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  video_id TEXT NOT NULL,
  title TEXT NOT NULL,
  artist TEXT,
  duration_s INTEGER,
  origin TEXT NOT NULL,
  outcome TEXT NOT NULL,
  listened_s INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_play_events_ts ON play_events(ts);
CREATE INDEX IF NOT EXISTS idx_play_events_user ON play_events(user_id);

CREATE TABLE IF NOT EXISTS spotify_match_cache (
  spotify_id TEXT PRIMARY KEY,
  video_id TEXT NOT NULL,
  confidence REAL NOT NULL,
  ts INTEGER NOT NULL
);
"""


class Database:
    """Async SQLite database manager."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self._conn: Optional[aiosqlite.Connection] = None

    async def initialize(self) -> None:
        """Open connection, enable WAL mode, and apply migrations."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.db_path)

        # Enable WAL mode for high concurrency
        async with self._conn.execute("PRAGMA journal_mode=WAL;") as cursor:
            row = await cursor.fetchone()
            journal_mode = row[0] if row else "unknown"
            logger.info("SQLite journal mode set to: %s", journal_mode)

        # Apply migrations
        await self._migrate()

    async def close(self) -> None:
        """Close database connection."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database connection is not open. Call initialize() first.")
        return self._conn

    async def _migrate(self) -> None:
        """Check user_version and apply incremental migrations."""
        async with self.conn.execute("PRAGMA user_version;") as cursor:
            row = await cursor.fetchone()
            version = row[0] if row else 0

        logger.info("Current SQLite schema version: %d", version)

        if version < 1:
            logger.info("Applying SQLite migration v1...")
            await self.conn.executescript(MIGRATION_V1)
            await self.conn.execute("PRAGMA user_version = 1;")
            await self.conn.commit()
            logger.info("SQLite schema migrated to version 1.")

    async def ping(self) -> float:
        """Execute a quick SELECT 1 and return roundtrip latency in milliseconds."""
        start = time.perf_counter()
        async with self.conn.execute("SELECT 1;") as cursor:
            await cursor.fetchone()
        return (time.perf_counter() - start) * 1000.0

    async def log_command(self, user_id: int, command: str) -> None:
        """Record command execution to command_log."""
        now = int(time.time())
        query = "INSERT INTO command_log (ts, user_id, command) VALUES (?, ?, ?);"
        await self.conn.execute(query, (now, user_id, command))
        await self.conn.commit()

    async def record_play_event(
        self,
        user_id: int,
        video_id: str,
        title: str,
        artist: Optional[str],
        duration_s: Optional[int],
        origin: str,
        outcome: str,
        listened_s: int = 0,
    ) -> None:
        """Record track playback outcome to play_events (YouTube-derived metadata only)."""
        now = int(time.time())
        query = """
        INSERT INTO play_events (
            ts, user_id, video_id, title, artist, duration_s, origin, outcome, listened_s
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
        await self.conn.execute(
            query,
            (now, user_id, video_id, title, artist, duration_s, origin, outcome, listened_s),
        )
        await self.conn.commit()

    async def get_usage_stats(
        self, period_days: Optional[int] = None
    ) -> tuple[list[tuple[int, int, int]], list[tuple[str, int]]]:
        """Compute usage ranking: top 10 users by command count and top 5 commands.

        Returns:
            (top_users: list of (user_id, command_count, track_count),
             top_commands: list of (command_name, count))
        """
        where_cmd = ""
        where_play = ""
        params: tuple[int, ...] = ()

        if period_days is not None:
            since = int(time.time()) - (period_days * 86400)
            where_cmd = "WHERE ts >= ?"
            where_play = "WHERE ts >= ?"
            params = (since,)

        # 1. Top 10 users by command count
        user_query = f"""
        SELECT user_id, COUNT(*) as cmd_count
        FROM command_log
        {where_cmd}
        GROUP BY user_id
        ORDER BY cmd_count DESC, user_id ASC
        LIMIT 10;
        """
        async with self.conn.execute(user_query, params) as cursor:
            user_rows = await cursor.fetchall()

        # Get track play counts for those top users
        top_users: list[tuple[int, int, int]] = []
        for uid, cmd_cnt in user_rows:
            play_query = f"""
            SELECT COUNT(*) FROM play_events
            {where_play} {"AND" if where_play else "WHERE"} user_id = ?;
            """
            play_params = params + (uid,) if params else (uid,)
            async with self.conn.execute(play_query, play_params) as cursor:
                p_row = await cursor.fetchone()
                track_cnt = p_row[0] if p_row else 0
            top_users.append((uid, cmd_cnt, track_cnt))

        # 2. Top 5 commands
        cmd_query = f"""
        SELECT command, COUNT(*) as count
        FROM command_log
        {where_cmd}
        GROUP BY command
        ORDER BY count DESC, command ASC
        LIMIT 5;
        """
        async with self.conn.execute(cmd_query, params) as cursor:
            top_commands = [(row[0], row[1]) for row in await cursor.fetchall()]

        return top_users, top_commands

    async def get_recent_completed_track(
        self, user_id: int
    ) -> Optional[tuple[str, str, Optional[str]]]:
        """Fetch the most recently completed track by a user (video_id, title, artist)."""
        query = """
        SELECT video_id, title, artist
        FROM play_events
        WHERE user_id = ? AND outcome = 'completed'
        ORDER BY ts DESC
        LIMIT 1;
        """
        async with self.conn.execute(query, (user_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return row[0], row[1], row[2]
        return None

    async def get_recent_played_video_ids(self, limit: int = 100) -> set[str]:
        """Fetch a set of the last N played video IDs for recommendation deduplication."""
        query = "SELECT video_id FROM play_events ORDER BY ts DESC LIMIT ?;"
        async with self.conn.execute(query, (limit,)) as cursor:
            rows = await cursor.fetchall()
            return {r[0] for r in rows}

    async def get_spotify_match(self, spotify_id: str) -> Optional[tuple[str, float]]:
        """Retrieve cached YouTube match for a Spotify track ID (video_id, confidence)."""
        query = "SELECT video_id, confidence FROM spotify_match_cache WHERE spotify_id = ?;"
        async with self.conn.execute(query, (spotify_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return row[0], row[1]
        return None

    async def set_spotify_match(self, spotify_id: str, video_id: str, confidence: float) -> None:
        """Cache a YouTube match for a Spotify track ID."""
        now = int(time.time())
        query = """
        INSERT OR REPLACE INTO spotify_match_cache (spotify_id, video_id, confidence, ts)
        VALUES (?, ?, ?, ?);
        """
        await self.conn.execute(query, (spotify_id, video_id, confidence, now))
        await self.conn.commit()
