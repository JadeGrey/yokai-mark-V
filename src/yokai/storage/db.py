"""SQLite persistence layer using aiosqlite, WAL mode, and PRAGMA user_version migrations."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from pathlib import Path
from typing import Optional

import aiosqlite

from yokai.economy.daily import cooldown_remaining_s, next_streak, roll_daily_payout
from yokai.economy.models import DailyClaimResult, Wallet
from yokai.errors import DailyOnCooldownError, InsufficientFundsError

logger = logging.getLogger(__name__)

CURRENT_SCHEMA_VERSION = 2

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

MIGRATION_V2 = """
CREATE TABLE IF NOT EXISTS wallets (
  user_id INTEGER PRIMARY KEY,
  balance INTEGER NOT NULL DEFAULT 0,
  last_daily_ts INTEGER,
  daily_streak INTEGER NOT NULL DEFAULT 0,
  created_ts INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS renown_transactions (
  id INTEGER PRIMARY KEY,
  ts INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  delta INTEGER NOT NULL,
  reason TEXT NOT NULL,
  counterparty_id INTEGER,
  balance_after INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_renown_tx_user ON renown_transactions(user_id);
CREATE INDEX IF NOT EXISTS idx_renown_tx_ts ON renown_transactions(ts);
"""


class Database:
    """Async SQLite database manager."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self._conn: Optional[aiosqlite.Connection] = None
        # Serializes every wallet read-modify-write sequence. aiosqlite uses a single
        # connection shared by all coroutines; without this, two coroutines can interleave
        # between the read and the write of a wallet balance (see the concurrency
        # regression tests in tests/test_economy_storage.py for what that allows).
        self._write_lock = asyncio.Lock()

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

        if version < 2:
            logger.info("Applying SQLite migration v2...")
            await self.conn.executescript(MIGRATION_V2)
            await self.conn.execute("PRAGMA user_version = 2;")
            await self.conn.commit()
            logger.info("SQLite schema migrated to version 2.")

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

    async def _ensure_wallet(self, user_id: int, welcome_bonus: int, now: int) -> None:
        """Create a wallet with the welcome bonus if `user_id` has none yet (idempotent).

        Private helper: does NOT take `_write_lock` itself. Every caller must already hold
        it for the duration of its own read-modify-write sequence.
        """
        cursor = await self.conn.execute(
            "INSERT OR IGNORE INTO wallets (user_id, balance, daily_streak, created_ts) "
            "VALUES (?, ?, 0, ?);",
            (user_id, welcome_bonus, now),
        )
        if cursor.rowcount and cursor.rowcount > 0 and welcome_bonus:
            await self._insert_transaction(
                user_id, welcome_bonus, "welcome", None, welcome_bonus, now
            )

    async def _insert_transaction(
        self,
        user_id: int,
        delta: int,
        reason: str,
        counterparty_id: Optional[int],
        balance_after: int,
        ts: int,
    ) -> None:
        await self.conn.execute(
            "INSERT INTO renown_transactions "
            "(ts, user_id, delta, reason, counterparty_id, balance_after) "
            "VALUES (?, ?, ?, ?, ?, ?);",
            (ts, user_id, delta, reason, counterparty_id, balance_after),
        )

    async def get_wallet(self, user_id: int) -> Optional[Wallet]:
        """Read-only lookup. Does NOT create a wallet or grant the welcome bonus."""
        query = (
            "SELECT user_id, balance, last_daily_ts, daily_streak, created_ts "
            "FROM wallets WHERE user_id = ?;"
        )
        async with self.conn.execute(query, (user_id,)) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return Wallet(
            user_id=row[0],
            balance=row[1],
            last_daily_ts=row[2],
            daily_streak=row[3],
            created_ts=row[4],
        )

    async def ensure_wallet(self, user_id: int, welcome_bonus: int) -> Wallet:
        """Get-or-create: creates the wallet with the welcome bonus on first touch."""
        now = int(time.time())
        async with self._write_lock:
            await self._ensure_wallet(user_id, welcome_bonus, now)
            await self.conn.commit()
            wallet = await self.get_wallet(user_id)
        assert wallet is not None
        return wallet

    async def credit_wallet(
        self,
        user_id: int,
        amount: int,
        reason: str,
        welcome_bonus: int,
        counterparty_id: Optional[int] = None,
    ) -> int:
        """Add `amount` (must be positive) to a wallet, creating it with the welcome bonus first."""
        if amount <= 0:
            raise ValueError("credit_wallet amount must be positive")
        now = int(time.time())
        async with self._write_lock:
            await self._ensure_wallet(user_id, welcome_bonus, now)
            await self.conn.execute(
                "UPDATE wallets SET balance = balance + ? WHERE user_id = ?;", (amount, user_id)
            )
            wallet = await self.get_wallet(user_id)
            assert wallet is not None
            await self._insert_transaction(
                user_id, amount, reason, counterparty_id, wallet.balance, now
            )
            await self.conn.commit()
        return wallet.balance

    async def pay(
        self, sender_id: int, recipient_id: int, amount: int, welcome_bonus: int
    ) -> tuple[int, int]:
        """Atomically move `amount` from sender to recipient. Raises InsufficientFundsError."""
        if amount <= 0:
            raise ValueError("pay amount must be positive")
        now = int(time.time())
        async with self._write_lock:
            await self._ensure_wallet(sender_id, welcome_bonus, now)
            sender = await self.get_wallet(sender_id)
            assert sender is not None
            if sender.balance < amount:
                await self.conn.rollback()
                raise InsufficientFundsError(balance=sender.balance, amount=amount)

            await self._ensure_wallet(recipient_id, welcome_bonus, now)

            # Defense-in-depth: even though `_write_lock` already serializes every wallet
            # mutation, guard the debit with a conditional UPDATE so a future caller that
            # forgets to take the lock still cannot push the sender negative.
            cursor = await self.conn.execute(
                "UPDATE wallets SET balance = balance - ? WHERE user_id = ? AND balance >= ?;",
                (amount, sender_id, amount),
            )
            if cursor.rowcount == 0:
                await self.conn.rollback()
                raise InsufficientFundsError(balance=sender.balance, amount=amount)

            await self.conn.execute(
                "UPDATE wallets SET balance = balance + ? WHERE user_id = ?;",
                (amount, recipient_id),
            )
            sender_balance = sender.balance - amount
            recipient = await self.get_wallet(recipient_id)
            assert recipient is not None
            await self._insert_transaction(
                sender_id, -amount, "pay_sent", recipient_id, sender_balance, now
            )
            await self._insert_transaction(
                recipient_id, amount, "pay_received", sender_id, recipient.balance, now
            )
            await self.conn.commit()
        return sender_balance, recipient.balance

    async def claim_daily(
        self, user_id: int, base: int, welcome_bonus: int, now: int, rng: random.Random
    ) -> DailyClaimResult:
        """Roll and apply a /daily payout, or raise DailyOnCooldownError."""
        async with self._write_lock:
            await self._ensure_wallet(user_id, welcome_bonus, now)
            wallet = await self.get_wallet(user_id)
            assert wallet is not None
            remaining = cooldown_remaining_s(wallet.last_daily_ts, now)
            if remaining is not None:
                await self.conn.rollback()
                raise DailyOnCooldownError(remaining)

            streak_day = next_streak(wallet.daily_streak, wallet.last_daily_ts, now)
            payout = roll_daily_payout(base, streak_day, rng)

            # Defense-in-depth: condition the update on the `last_daily_ts` we just read, so
            # a claim that somehow raced past `_write_lock` still cannot double-pay.
            cursor = await self.conn.execute(
                "UPDATE wallets SET balance = balance + ?, last_daily_ts = ?, daily_streak = ? "
                "WHERE user_id = ? AND last_daily_ts IS ?;",
                (payout, now, streak_day, user_id, wallet.last_daily_ts),
            )
            if cursor.rowcount == 0:
                await self.conn.rollback()
                fresh = await self.get_wallet(user_id)
                fresh_remaining = cooldown_remaining_s(fresh.last_daily_ts, now) if fresh else None
                raise DailyOnCooldownError(fresh_remaining or 0)

            new_balance = wallet.balance + payout
            await self._insert_transaction(user_id, payout, "daily", None, new_balance, now)
            await self.conn.commit()
        return DailyClaimResult(payout=payout, streak_day=streak_day, new_balance=new_balance)

    async def admin_adjust_wallet(
        self, user_id: int, delta: int, welcome_bonus: int
    ) -> tuple[int, int]:
        """Apply `delta` (positive or negative), floored at 0.

        Returns (applied_delta, new_balance).
        """
        now = int(time.time())
        async with self._write_lock:
            await self._ensure_wallet(user_id, welcome_bonus, now)
            wallet = await self.get_wallet(user_id)
            assert wallet is not None
            new_balance = max(0, wallet.balance + delta)
            applied = new_balance - wallet.balance
            await self.conn.execute(
                "UPDATE wallets SET balance = ? WHERE user_id = ?;", (new_balance, user_id)
            )
            await self._insert_transaction(user_id, applied, "admin", None, new_balance, now)
            await self.conn.commit()
        return applied, new_balance

    async def get_leaderboard(self, limit: int, offset: int) -> tuple[list[Wallet], int]:
        """Wallets ordered by balance desc (ties by user_id asc), plus the total wallet count."""
        async with self.conn.execute("SELECT COUNT(*) FROM wallets;") as cursor:
            total_row = await cursor.fetchone()
            total = total_row[0] if total_row else 0
        query = (
            "SELECT user_id, balance, last_daily_ts, daily_streak, created_ts FROM wallets "
            "ORDER BY balance DESC, user_id ASC LIMIT ? OFFSET ?;"
        )
        async with self.conn.execute(query, (limit, offset)) as cursor:
            rows = await cursor.fetchall()
        wallets = [
            Wallet(
                user_id=r[0], balance=r[1], last_daily_ts=r[2], daily_streak=r[3], created_ts=r[4]
            )
            for r in rows
        ]
        return wallets, total
