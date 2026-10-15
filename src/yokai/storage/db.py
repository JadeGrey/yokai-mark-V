"""SQLite persistence layer using aiosqlite, WAL mode, and PRAGMA user_version migrations."""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Callable, Mapping, Optional, Sequence

import aiosqlite

from yokai.challenges.models import Advance
from yokai.economy.daily import cooldown_remaining_s, next_streak, roll_daily_payout
from yokai.economy.models import BetSettlement, DailyClaimResult, Wallet
from yokai.errors import (
    BetNotOpenError,
    DailyOnCooldownError,
    InsufficientFundsError,
    InvalidGameChoiceError,
    LotteryClosedError,
    TicketLimitError,
)
from yokai.gambling.engines.lottery import split_pot
from yokai.gambling.models import (
    LotteryDraw,
    LotteryPurchase,
    LotteryState,
    MultiplayerChannel,
)
from yokai.gambling.pot import POOLED_GAMES, bet_house_take, lobby_house_take

logger = logging.getLogger(__name__)

CURRENT_SCHEMA_VERSION = 9

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

MIGRATION_V3 = """
CREATE TABLE IF NOT EXISTS gambling_bets (
  id INTEGER PRIMARY KEY,
  ts INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  game TEXT NOT NULL,
  stake INTEGER NOT NULL,
  status TEXT NOT NULL,
  payout INTEGER NOT NULL DEFAULT 0,
  lobby_id TEXT,
  group_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_gambling_bets_user ON gambling_bets(user_id);
CREATE INDEX IF NOT EXISTS idx_gambling_bets_status ON gambling_bets(status);
"""

MIGRATION_V4 = """
CREATE TABLE IF NOT EXISTS blacklist (
  user_id INTEGER PRIMARY KEY,
  reason TEXT NOT NULL DEFAULT '',
  added_ts INTEGER NOT NULL,
  added_by TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS admins (
  user_id INTEGER PRIMARY KEY,
  added_ts INTEGER NOT NULL,
  added_by TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS admin_audit (
  id INTEGER PRIMARY KEY,
  ts INTEGER NOT NULL,
  actor TEXT NOT NULL,
  module TEXT NOT NULL,
  action TEXT NOT NULL,
  target TEXT,
  detail_json TEXT,
  reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_admin_audit_ts ON admin_audit(ts);

CREATE TABLE IF NOT EXISTS bot_heartbeat (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  ts INTEGER NOT NULL,
  pid INTEGER NOT NULL,
  version TEXT NOT NULL
);
"""

MIGRATION_V5 = """
CREATE TABLE IF NOT EXISTS user_names (
  user_id INTEGER PRIMARY KEY,
  display_name TEXT NOT NULL,
  updated_ts INTEGER NOT NULL
);
"""

MIGRATION_V6 = """
CREATE TABLE IF NOT EXISTS multiplayer_channels (
  channel_id INTEGER PRIMARY KEY,
  game TEXT NOT NULL UNIQUE,
  play_message_id INTEGER,
  table_message_id INTEGER,
  table_active INTEGER NOT NULL DEFAULT 0,
  updated_ts INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS lottery_state (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  pot INTEGER NOT NULL DEFAULT 0
);

INSERT OR IGNORE INTO lottery_state (id, pot) VALUES (1, 0);

CREATE TABLE IF NOT EXISTS lottery_pot_entries (
  id INTEGER PRIMARY KEY,
  ts INTEGER NOT NULL,
  source TEXT NOT NULL,
  amount INTEGER NOT NULL CHECK (amount > 0)
);

CREATE INDEX IF NOT EXISTS idx_lottery_pot_entries_ts ON lottery_pot_entries(ts);
"""


_CHANNEL_SELECT = (
    "SELECT channel_id, game, play_message_id, table_message_id, table_active "
    "FROM multiplayer_channels"
)


def _channel_from_row(
    row: tuple[int, str, Optional[int], Optional[int], int],
) -> MultiplayerChannel:
    return MultiplayerChannel(
        channel_id=row[0],
        game=row[1],
        play_message_id=row[2],
        table_message_id=row[3],
        table_active=bool(row[4]),
    )


MIGRATION_V7 = """
CREATE TABLE IF NOT EXISTS lottery_tickets (
  draw_no INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  number INTEGER NOT NULL,
  ts INTEGER NOT NULL,
  PRIMARY KEY (draw_no, user_id, number)
);

CREATE INDEX IF NOT EXISTS idx_lottery_tickets_number ON lottery_tickets(draw_no, number);

CREATE TABLE IF NOT EXISTS lottery_draws (
  draw_no INTEGER PRIMARY KEY,
  ts INTEGER NOT NULL,
  winning_number INTEGER NOT NULL,
  pot INTEGER NOT NULL,
  winners TEXT NOT NULL,
  prize_each INTEGER NOT NULL,
  announced INTEGER NOT NULL DEFAULT 0
);
"""

# lottery_state gains two columns; they are added in _migrate only when missing, because
# ALTER TABLE ... ADD COLUMN cannot be repeated and a crash mid-upgrade replays the step.

MIGRATION_V8 = """
CREATE TABLE IF NOT EXISTS challenge_slates (
  period TEXT NOT NULL,
  period_key TEXT NOT NULL,
  template_ids TEXT NOT NULL,
  created_ts INTEGER NOT NULL,
  overridden INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (period, period_key)
);

CREATE TABLE IF NOT EXISTS challenge_progress (
  user_id INTEGER NOT NULL,
  period TEXT NOT NULL,
  period_key TEXT NOT NULL,
  template_id TEXT NOT NULL,
  progress INTEGER NOT NULL DEFAULT 0,
  seen TEXT,
  completed_ts INTEGER,
  rewarded INTEGER NOT NULL DEFAULT 0,
  updated_ts INTEGER NOT NULL,
  PRIMARY KEY (user_id, period, period_key, template_id)
);
"""

MIGRATION_V9 = """
CREATE TABLE IF NOT EXISTS rpg_characters (
  user_id INTEGER PRIMARY KEY,
  level INTEGER NOT NULL DEFAULT 1,
  xp INTEGER NOT NULL DEFAULT 0,
  rank TEXT NOT NULL,
  battles_won INTEGER NOT NULL DEFAULT 0,
  battles_lost INTEGER NOT NULL DEFAULT 0,
  created_ts INTEGER NOT NULL
);
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
        self.bet_listener: Optional[Callable[[BetSettlement], None]] = None

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

        if version < 3:
            logger.info("Applying SQLite migration v3...")
            await self.conn.executescript(MIGRATION_V3)
            await self.conn.execute("PRAGMA user_version = 3;")
            await self.conn.commit()
            logger.info("SQLite schema migrated to version 3.")

        if version < 4:
            logger.info("Applying SQLite migration v4...")
            await self.conn.executescript(MIGRATION_V4)
            await self.conn.execute("PRAGMA user_version = 4;")
            await self.conn.commit()
            logger.info("SQLite schema migrated to version 4.")

        if version < 5:
            logger.info("Applying SQLite migration v5...")
            await self.conn.executescript(MIGRATION_V5)
            await self.conn.execute("PRAGMA user_version = 5;")
            await self.conn.commit()
            logger.info("SQLite schema migrated to version 5.")

        if version < 6:
            logger.info("Applying SQLite migration v6...")
            await self.conn.executescript(MIGRATION_V6)
            await self.conn.execute("PRAGMA user_version = 6;")
            await self.conn.commit()
            logger.info("SQLite schema migrated to version 6.")

        if version < 7:
            logger.info("Applying SQLite migration v7...")
            await self.conn.executescript(MIGRATION_V7)
            await self._add_column_if_missing(
                "lottery_state", "draw_no", "INTEGER NOT NULL DEFAULT 1"
            )
            await self._add_column_if_missing("lottery_state", "next_draw_ts", "INTEGER")
            await self.conn.execute("PRAGMA user_version = 7;")
            await self.conn.commit()
            logger.info("SQLite schema migrated to version 7.")

        if version < 8:
            logger.info("Applying SQLite migration v8...")
            await self.conn.executescript(MIGRATION_V8)
            await self.conn.execute("PRAGMA user_version = 8;")
            await self.conn.commit()
            logger.info("SQLite schema migrated to version 8.")

        if version < 9:
            logger.info("Applying SQLite migration v9...")
            await self.conn.executescript(MIGRATION_V9)
            await self.conn.execute("PRAGMA user_version = 9;")
            await self.conn.commit()
            logger.info("SQLite schema migrated to version 9.")

    async def _add_column_if_missing(self, table: str, column: str, definition: str) -> None:
        """ALTER TABLE ... ADD COLUMN, but only if the column is not there yet (replay-safe).

        Names are constants chosen by the migration code, never user input.
        """
        async with self.conn.execute(f"PRAGMA table_info({table});") as cursor:
            existing = {row[1] for row in await cursor.fetchall()}
        if column not in existing:
            await self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition};")

    async def ping(self) -> float:
        """Execute a quick SELECT 1 and return roundtrip latency in milliseconds."""
        start = time.perf_counter()
        async with self.conn.execute("SELECT 1;") as cursor:
            await cursor.fetchone()
        return (time.perf_counter() - start) * 1000.0

    async def get_moderation_lists(self) -> tuple[frozenset[int], frozenset[int]]:
        """Return (blacklisted user ids, admin user ids). Read-only; the panel writes these."""
        async with self.conn.execute("SELECT user_id FROM blacklist;") as cursor:
            blacklisted = frozenset(row[0] for row in await cursor.fetchall())
        async with self.conn.execute("SELECT user_id FROM admins;") as cursor:
            admins = frozenset(row[0] for row in await cursor.fetchall())
        return blacklisted, admins

    async def write_heartbeat(self, pid: int, version: str, now: Optional[int] = None) -> None:
        """Upsert the single liveness row the admin panel reads to show bot online/offline."""
        ts = int(time.time()) if now is None else now
        async with self._write_lock:
            await self.conn.execute(
                "INSERT INTO bot_heartbeat (id, ts, pid, version) VALUES (1, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET ts = excluded.ts, pid = excluded.pid, "
                "version = excluded.version;",
                (ts, pid, version),
            )
            await self.conn.commit()

    async def upsert_user_name(
        self, user_id: int, display_name: str, now: Optional[int] = None
    ) -> None:
        """Remember a user's latest display name so the admin panel can show people by name."""
        ts = int(time.time()) if now is None else now
        async with self._write_lock:
            await self.conn.execute(
                "INSERT INTO user_names (user_id, display_name, updated_ts) VALUES (?, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET display_name = excluded.display_name, "
                "updated_ts = excluded.updated_ts;",
                (user_id, display_name, ts),
            )
            await self.conn.commit()

    async def clear_heartbeat(self) -> None:
        """Remove the liveness row so the panel shows offline immediately after a clean stop."""
        async with self._write_lock:
            await self.conn.execute("DELETE FROM bot_heartbeat WHERE id = 1;")
            await self.conn.commit()

    async def log_command(self, user_id: int, command: str) -> None:
        """Record command execution to command_log."""
        now = int(time.time())
        query = "INSERT INTO command_log (ts, user_id, command) VALUES (?, ?, ?);"
        async with self._write_lock:
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
        async with self._write_lock:
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
        async with self._write_lock:
            await self.conn.execute(query, (spotify_id, video_id, confidence, now))
            await self.conn.commit()

    @asynccontextmanager
    async def _write_tx(self) -> AsyncIterator[None]:
        """Hold the in-process write lock and SQLite's write lock for a read-modify-write.

        `BEGIN IMMEDIATE` takes the file's write lock before the first read, so another process
        (the admin panel) cannot change a wallet between this code's read and the write that
        depends on it, and the protection no longer relies on a leading `INSERT OR IGNORE`
        happening to take the lock. Any error rolls back, so a failure can never leave a
        half-finished transaction open (it would hold the lock, and the next operation's commit
        would commit the leftovers). Callers still `commit()` on success.
        """
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE;")
            try:
                yield
            except BaseException:
                await self.conn.rollback()
                raise

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        """Run a block of reads and writes as one atomic transaction on the shared connection.

        Takes the same locks as the wallet code (`_write_tx`), commits when the block ends
        normally and rolls back if it raises. Feature modules such as the RPG use this so item
        and Renown changes can never be half-applied.
        """
        async with self._write_tx():
            yield self.conn
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
        async with self._write_tx():
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
        async with self._write_tx():
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
        async with self._write_tx():
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
        async with self._write_tx():
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

    # ------------------------------------------------------------------ challenges

    async def get_challenge_slate(self, period: str, period_key: str) -> Optional[list[str]]:
        """The stored template ids for a period, or None when no slate exists yet."""
        async with self.conn.execute(
            "SELECT template_ids FROM challenge_slates WHERE period = ? AND period_key = ?;",
            (period, period_key),
        ) as cursor:
            row = await cursor.fetchone()
        return None if row is None else list(json.loads(row[0]))

    async def create_challenge_slate(
        self, period: str, period_key: str, template_ids: Sequence[str]
    ) -> list[str]:
        """Store a slate unless one exists (first writer wins); returns what is stored."""
        async with self._write_lock:
            await self.conn.execute(
                "INSERT OR IGNORE INTO challenge_slates "
                "(period, period_key, template_ids, created_ts, overridden) "
                "VALUES (?, ?, ?, ?, 0);",
                (period, period_key, json.dumps(list(template_ids)), int(time.time())),
            )
            await self.conn.commit()
        stored = await self.get_challenge_slate(period, period_key)
        assert stored is not None
        return stored

    async def replace_challenge_slate(
        self, period: str, period_key: str, template_ids: Sequence[str]
    ) -> list[str]:
        """Override a slate. Incomplete progress on removed templates is dropped; completed
        rows stay, so a paid template re-added later cannot be earned twice."""
        ids = list(template_ids)
        if not ids:
            raise ValueError("a challenge slate needs at least one template")
        async with self._write_tx():
            await self.conn.execute(
                "INSERT INTO challenge_slates "
                "(period, period_key, template_ids, created_ts, overridden) VALUES (?, ?, ?, ?, 1) "
                "ON CONFLICT(period, period_key) DO UPDATE SET "
                "template_ids = excluded.template_ids, overridden = 1;",
                (period, period_key, json.dumps(ids), int(time.time())),
            )
            marks = ",".join("?" for _ in ids)
            await self.conn.execute(
                "DELETE FROM challenge_progress WHERE period = ? AND period_key = ? "
                f"AND completed_ts IS NULL AND template_id NOT IN ({marks});",
                (period, period_key, *ids),
            )
            await self.conn.commit()
        return ids

    async def advance_challenge(
        self,
        user_id: int,
        period: str,
        period_key: str,
        template_id: str,
        *,
        target: int,
        reward: int,
        welcome_bonus: int,
        delta: int = 0,
        item: Optional[str] = None,
    ) -> Advance:
        """Advance one progress row and, on reaching `target`, pay `reward` in the same
        transaction. `item` makes the metric distinct-count; otherwise `delta` is added."""
        now = int(time.time())
        async with self._write_tx():
            async with self.conn.execute(
                "SELECT progress, seen, completed_ts FROM challenge_progress "
                "WHERE user_id = ? AND period = ? AND period_key = ? AND template_id = ?;",
                (user_id, period, period_key, template_id),
            ) as cursor:
                row = await cursor.fetchone()
            if row is not None and row[2] is not None:
                await self.conn.commit()
                return Advance(progress=row[0], completed_now=False)
            progress = row[0] if row is not None else 0
            seen: list[str] = json.loads(row[1]) if row is not None and row[1] else []
            if item is not None:
                key = item[:100]
                if key not in seen:
                    seen.append(key)
                progress = len(seen)
            else:
                progress += delta
            progress = min(progress, target)
            done = progress >= target
            await self.conn.execute(
                "INSERT INTO challenge_progress "
                "(user_id, period, period_key, template_id, progress, seen, completed_ts, "
                "rewarded, updated_ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(user_id, period, period_key, template_id) DO UPDATE SET "
                "progress = excluded.progress, seen = excluded.seen, "
                "completed_ts = excluded.completed_ts, rewarded = excluded.rewarded, "
                "updated_ts = excluded.updated_ts;",
                (
                    user_id,
                    period,
                    period_key,
                    template_id,
                    progress,
                    json.dumps(seen) if seen else None,
                    now if done else None,
                    1 if done else 0,
                    now,
                ),
            )
            if done:
                await self._ensure_wallet(user_id, welcome_bonus, now)
                await self.conn.execute(
                    "UPDATE wallets SET balance = balance + ? WHERE user_id = ?;",
                    (reward, user_id),
                )
                wallet = await self.get_wallet(user_id)
                assert wallet is not None
                await self._insert_transaction(
                    user_id, reward, f"challenge:{template_id}", None, wallet.balance, now
                )
            await self.conn.commit()
        return Advance(progress=progress, completed_now=done)

    async def get_challenge_progress(
        self, user_id: int, period: str, period_key: str
    ) -> dict[str, tuple[int, bool]]:
        """{template_id: (progress, completed)} for one user and period."""
        async with self.conn.execute(
            "SELECT template_id, progress, completed_ts FROM challenge_progress "
            "WHERE user_id = ? AND period = ? AND period_key = ?;",
            (user_id, period, period_key),
        ) as cursor:
            rows = await cursor.fetchall()
        return {row[0]: (row[1], row[2] is not None) for row in rows}

    async def purge_challenges(self, older_than_ts: int) -> int:
        """Delete slates and progress last touched before `older_than_ts`; returns row count."""
        async with self._write_lock:
            first = await self.conn.execute(
                "DELETE FROM challenge_progress WHERE updated_ts < ?;", (older_than_ts,)
            )
            second = await self.conn.execute(
                "DELETE FROM challenge_slates WHERE created_ts < ?;", (older_than_ts,)
            )
            await self.conn.commit()
        return first.rowcount + second.rowcount

    async def place_bet(
        self,
        user_id: int,
        amount: int,
        game: str,
        welcome_bonus: int,
        lobby_id: Optional[str] = None,
        group_id: Optional[str] = None,
    ) -> int:
        """Escrow `amount` from the wallet into a new open bet; returns the bet id.

        Raises InsufficientFundsError (action "bet") when the balance is too low.
        """
        if amount <= 0:
            raise ValueError("place_bet amount must be positive")
        now = int(time.time())
        async with self._write_lock:
            wallet = await self.get_wallet(user_id)
            if wallet is None:
                if amount > welcome_bonus:
                    raise InsufficientFundsError(balance=0, amount=amount, action="bet")
                await self._ensure_wallet(user_id, welcome_bonus, now)
                await self.conn.commit()
                wallet = await self.get_wallet(user_id)
                assert wallet is not None
            try:
                cursor = await self.conn.execute(
                    "UPDATE wallets SET balance = balance - ? WHERE user_id = ? AND balance >= ?;",
                    (amount, user_id, amount),
                )
                wallet = await self.get_wallet(user_id)
                assert wallet is not None
                if cursor.rowcount == 0:
                    await self.conn.rollback()
                    raise InsufficientFundsError(
                        balance=wallet.balance, amount=amount, action="bet"
                    )
                insert = await self.conn.execute(
                    "INSERT INTO gambling_bets "
                    "(ts, user_id, game, stake, status, payout, lobby_id, group_id) "
                    "VALUES (?, ?, ?, ?, 'open', 0, ?, ?);",
                    (now, user_id, game, amount, lobby_id, group_id),
                )
                bet_id = insert.lastrowid
                await self._insert_transaction(
                    user_id, -amount, f"bet:{game}", None, wallet.balance, now
                )
                await self.conn.commit()
            except BaseException:
                await self.conn.rollback()
                raise
        assert bet_id is not None
        return bet_id

    async def get_lottery_state(self) -> LotteryState:
        async with self.conn.execute(
            "SELECT pot, draw_no, next_draw_ts FROM lottery_state WHERE id = 1;"
        ) as cursor:
            row = await cursor.fetchone()
        assert row is not None
        return LotteryState(pot=row[0], draw_no=row[1], next_draw_ts=row[2])

    async def ensure_lottery_schedule(self, now: int, interval_s: int) -> int:
        """Start the draw clock if it is not running yet; returns the next draw time."""
        async with self._write_lock:
            state = await self.get_lottery_state()
            if state.next_draw_ts is not None:
                return state.next_draw_ts
            next_ts = now + interval_s
            await self.conn.execute(
                "UPDATE lottery_state SET next_draw_ts = ? WHERE id = 1 AND next_draw_ts IS NULL;",
                (next_ts,),
            )
            await self.conn.commit()
            return next_ts

    async def lottery_tickets_sold(self) -> int:
        """Tickets sold for the draw currently on sale."""
        async with self.conn.execute(
            "SELECT COUNT(*) FROM lottery_tickets "
            "WHERE draw_no = (SELECT draw_no FROM lottery_state WHERE id = 1);"
        ) as cursor:
            row = await cursor.fetchone()
        return row[0] if row else 0

    async def user_lottery_numbers(self, user_id: int) -> list[int]:
        """The numbers a member holds for the draw currently on sale, sorted."""
        async with self.conn.execute(
            "SELECT number FROM lottery_tickets WHERE user_id = ? "
            "AND draw_no = (SELECT draw_no FROM lottery_state WHERE id = 1) ORDER BY number;",
            (user_id,),
        ) as cursor:
            return [row[0] for row in await cursor.fetchall()]

    async def buy_lottery_tickets(
        self,
        user_id: int,
        numbers: Sequence[int],
        price: int,
        max_per_user: int,
        welcome_bonus: int,
        now: Optional[int] = None,
    ) -> LotteryPurchase:
        """Buy one ticket per number for the draw on sale, all or nothing.

        Debits the wallet, stores the tickets, credits the pot and writes the ledger row in
        one transaction. Raises LotteryClosedError, TicketLimitError, InvalidGameChoiceError
        (a number already held) or InsufficientFundsError; any failure changes nothing.
        """
        if not numbers:
            raise ValueError("buy_lottery_tickets needs at least one number")
        ts = int(time.time()) if now is None else now
        cost = price * len(numbers)
        async with self._write_tx():
            await self._ensure_wallet(user_id, welcome_bonus, ts)
            state = await self.get_lottery_state()
            if state.next_draw_ts is None:
                raise LotteryClosedError(
                    "Tickets are not on sale yet.", user_hint="Ask an admin to set up the lottery."
                )
            if ts >= state.next_draw_ts:
                raise LotteryClosedError()
            async with self.conn.execute(
                "SELECT number FROM lottery_tickets WHERE draw_no = ? AND user_id = ?;",
                (state.draw_no, user_id),
            ) as cursor:
                held = {row[0] for row in await cursor.fetchall()}
            if len(held) + len(numbers) > max_per_user:
                raise TicketLimitError(len(held), max_per_user)
            clash = sorted(held & set(numbers))
            if clash:
                raise InvalidGameChoiceError(
                    f"You already hold {', '.join(str(n) for n in clash)}."
                )
            cursor = await self.conn.execute(
                "UPDATE wallets SET balance = balance - ? WHERE user_id = ? AND balance >= ?;",
                (cost, user_id, cost),
            )
            wallet = await self.get_wallet(user_id)
            assert wallet is not None
            if cursor.rowcount == 0:
                raise InsufficientFundsError(
                    balance=wallet.balance, amount=cost, action="buy tickets"
                )
            await self.conn.executemany(
                "INSERT INTO lottery_tickets (draw_no, user_id, number, ts) VALUES (?, ?, ?, ?);",
                [(state.draw_no, user_id, number, ts) for number in numbers],
            )
            await self._insert_transaction(
                user_id, -cost, "ticket:lottery", None, wallet.balance, ts
            )
            await self._credit_pot("ticket", cost, ts)
            await self.conn.commit()
        return LotteryPurchase(
            draw_no=state.draw_no,
            numbers=tuple(numbers),
            cost=cost,
            new_balance=wallet.balance,
            pot=state.pot + cost,
        )

    @staticmethod
    def _draw_from_row(row: tuple[int, int, int, int, str, int, int]) -> LotteryDraw:
        return LotteryDraw(
            draw_no=row[0],
            ts=row[1],
            winning_number=row[2],
            pot=row[3],
            winners=tuple(json.loads(row[4])),
            prize_each=row[5],
            announced=bool(row[6]),
        )

    _DRAW_COLUMNS = "draw_no, ts, winning_number, pot, winners, prize_each, announced"

    async def run_lottery_draw(
        self, winning_number: int, interval_s: int, now: Optional[int] = None
    ) -> Optional[LotteryDraw]:
        """Make the weekly draw if it is due; None when it is not (or the clock never started).

        One transaction: winners are credited, the pot is reduced to the leftover remainder,
        the draw is recorded, and the draw number and next draw time move on. The next draw
        time jumps by whole intervals past `now`, so a bot that was offline draws once.
        """
        ts = int(time.time()) if now is None else now
        async with self._write_tx():
            state = await self.get_lottery_state()
            if state.next_draw_ts is None or ts < state.next_draw_ts:
                await self.conn.rollback()
                return None
            async with self.conn.execute(
                "SELECT user_id FROM lottery_tickets WHERE draw_no = ? AND number = ? "
                "ORDER BY user_id;",
                (state.draw_no, winning_number),
            ) as cursor:
                winners = [row[0] for row in await cursor.fetchall()]
            each, remainder = split_pot(state.pot, len(winners))
            if each > 0:
                for user_id in winners:
                    await self._ensure_wallet(user_id, 0, ts)
                    await self.conn.execute(
                        "UPDATE wallets SET balance = balance + ? WHERE user_id = ?;",
                        (each, user_id),
                    )
                    wallet = await self.get_wallet(user_id)
                    assert wallet is not None
                    await self._insert_transaction(
                        user_id, each, "win:lottery", None, wallet.balance, ts
                    )
            next_ts = state.next_draw_ts + interval_s
            while next_ts <= ts:
                next_ts += interval_s
            await self.conn.execute(
                "UPDATE lottery_state SET pot = ?, draw_no = draw_no + 1, next_draw_ts = ? "
                "WHERE id = 1;",
                (remainder, next_ts),
            )
            await self.conn.execute(
                "INSERT INTO lottery_draws "
                "(draw_no, ts, winning_number, pot, winners, prize_each, announced) "
                "VALUES (?, ?, ?, ?, ?, ?, 0);",
                (state.draw_no, ts, winning_number, state.pot, json.dumps(winners), each),
            )
            await self.conn.commit()
        return LotteryDraw(
            draw_no=state.draw_no,
            ts=ts,
            winning_number=winning_number,
            pot=state.pot,
            winners=tuple(winners),
            prize_each=each,
            announced=False,
        )

    async def get_unannounced_draws(self) -> list[LotteryDraw]:
        async with self.conn.execute(
            f"SELECT {self._DRAW_COLUMNS} FROM lottery_draws WHERE announced = 0 ORDER BY draw_no;"
        ) as cursor:
            return [self._draw_from_row(r) for r in await cursor.fetchall()]

    async def mark_draw_announced(self, draw_no: int) -> None:
        async with self._write_lock:
            await self.conn.execute(
                "UPDATE lottery_draws SET announced = 1 WHERE draw_no = ?;", (draw_no,)
            )
            await self.conn.commit()

    async def get_last_draw(self) -> Optional[LotteryDraw]:
        async with self.conn.execute(
            f"SELECT {self._DRAW_COLUMNS} FROM lottery_draws ORDER BY draw_no DESC LIMIT 1;"
        ) as cursor:
            row = await cursor.fetchone()
        return self._draw_from_row(row) if row else None

    async def _credit_pot(self, source: str, amount: int, now: int) -> None:
        """Add the house's take to the lottery pot inside the caller's open transaction.

        Does not commit: it must land in the same transaction as the settlement that earned it.
        """
        if amount <= 0:
            return
        await self.conn.execute("UPDATE lottery_state SET pot = pot + ? WHERE id = 1;", (amount,))
        await self.conn.execute(
            "INSERT INTO lottery_pot_entries (ts, source, amount) VALUES (?, ?, ?);",
            (now, source, amount),
        )

    async def get_pot(self) -> int:
        """Current lottery pot."""
        async with self.conn.execute("SELECT pot FROM lottery_state WHERE id = 1;") as cursor:
            row = await cursor.fetchone()
        return row[0] if row else 0

    async def set_multiplayer_channel(
        self, channel_id: int, game: str, play_message_id: int, now: Optional[int] = None
    ) -> list[MultiplayerChannel]:
        """Dedicate a channel to a game; returns the rows it displaced.

        A game has one channel, so the same game elsewhere is displaced, and so is whatever this
        channel hosted before. The caller deletes the displaced rows' Discord messages.
        """
        ts = int(time.time()) if now is None else now
        async with self._write_lock:
            try:
                async with self.conn.execute(
                    _CHANNEL_SELECT + " WHERE game = ? OR channel_id = ? ORDER BY channel_id;",
                    (game, channel_id),
                ) as cursor:
                    removed = [_channel_from_row(r) for r in await cursor.fetchall()]
                await self.conn.execute(
                    "DELETE FROM multiplayer_channels WHERE game = ? OR channel_id = ?;",
                    (game, channel_id),
                )
                await self.conn.execute(
                    "INSERT INTO multiplayer_channels "
                    "(channel_id, game, play_message_id, table_message_id, table_active, "
                    "updated_ts) VALUES (?, ?, ?, NULL, 0, ?);",
                    (channel_id, game, play_message_id, ts),
                )
                await self.conn.commit()
            except BaseException:
                await self.conn.rollback()
                raise
        return removed

    async def get_multiplayer_channel(self, channel_id: int) -> Optional[MultiplayerChannel]:
        async with self.conn.execute(
            _CHANNEL_SELECT + " WHERE channel_id = ?;", (channel_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return _channel_from_row(row) if row else None

    async def list_multiplayer_channels(self) -> list[MultiplayerChannel]:
        async with self.conn.execute(_CHANNEL_SELECT + " ORDER BY channel_id;") as cursor:
            return [_channel_from_row(r) for r in await cursor.fetchall()]

    async def _update_channel(self, sql: str, params: tuple[object, ...]) -> None:
        async with self._write_lock:
            await self.conn.execute(sql, params)
            await self.conn.commit()

    async def set_play_message(self, channel_id: int, message_id: int) -> None:
        await self._update_channel(
            "UPDATE multiplayer_channels SET play_message_id = ?, updated_ts = ? "
            "WHERE channel_id = ?;",
            (message_id, int(time.time()), channel_id),
        )

    async def set_table_message(self, channel_id: int, message_id: int, active: bool) -> None:
        await self._update_channel(
            "UPDATE multiplayer_channels SET table_message_id = ?, table_active = ?, "
            "updated_ts = ? WHERE channel_id = ?;",
            (message_id, 1 if active else 0, int(time.time()), channel_id),
        )

    async def set_table_inactive(self, channel_id: int) -> None:
        await self._update_channel(
            "UPDATE multiplayer_channels SET table_active = 0, updated_ts = ? "
            "WHERE channel_id = ?;",
            (int(time.time()), channel_id),
        )

    async def clear_multiplayer_channel(self, channel_id: int) -> Optional[MultiplayerChannel]:
        """Stop dedicating a channel; returns the removed row, or None if it had none."""
        async with self._write_lock:
            async with self.conn.execute(
                _CHANNEL_SELECT + " WHERE channel_id = ?;", (channel_id,)
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                return None
            await self.conn.execute(
                "DELETE FROM multiplayer_channels WHERE channel_id = ?;", (channel_id,)
            )
            await self.conn.commit()
        return _channel_from_row(row)

    async def _finish_bet(self, bet_id: int, payout: int, refund: bool) -> int:
        """Close an open bet exactly once, crediting `payout` (the stake when refunding)."""
        now = int(time.time())
        async with self._write_lock:
            try:
                async with self.conn.execute(
                    "SELECT user_id, game, stake, lobby_id, group_id FROM gambling_bets "
                    "WHERE id = ? AND status = 'open';",
                    (bet_id,),
                ) as cursor:
                    row = await cursor.fetchone()
                if row is None:
                    raise BetNotOpenError(bet_id)
                user_id, game, stake, lobby_id, group_id = row
                if not refund:
                    credit = payout
                elif game == "poker" and lobby_id is not None:
                    credit = await self._poker_refund_amount(lobby_id, stake)
                else:
                    credit = stake
                status = "refunded" if refund else "settled"
                # Conditioned on status = 'open', so a racing second close can never
                # double-pay.
                cursor = await self.conn.execute(
                    "UPDATE gambling_bets SET status = ?, payout = ? "
                    "WHERE id = ? AND status = 'open';",
                    (status, credit, bet_id),
                )
                if cursor.rowcount == 0:
                    await self.conn.rollback()
                    raise BetNotOpenError(bet_id)
                if not refund:
                    await self._credit_pot(game, bet_house_take(game, stake, credit), now)
                if credit > 0:
                    await self.conn.execute(
                        "UPDATE wallets SET balance = balance + ? WHERE user_id = ?;",
                        (credit, user_id),
                    )
                wallet = await self.get_wallet(user_id)
                assert wallet is not None
                if credit > 0:
                    if refund:
                        prefix = "refund"
                    elif credit > stake:
                        prefix = "win"
                    elif credit == stake:
                        prefix = "push"
                    else:
                        prefix = "payout"
                    await self._insert_transaction(
                        user_id, credit, f"{prefix}:{game}", None, wallet.balance, now
                    )
                await self.conn.commit()
            except BaseException:
                await self.conn.rollback()
                raise
        if not refund:
            self._notify_settled([BetSettlement(bet_id, user_id, game, stake, credit, group_id)])
        return wallet.balance

    def _notify_settled(self, settlements: Sequence[BetSettlement]) -> None:
        """Tell the listener about settled bets. A listener error is logged, never raised."""
        listener = self.bet_listener
        if listener is None:
            return
        for settlement in settlements:
            try:
                listener(settlement)
            except Exception:
                logger.exception("Bet listener failed for bet %d", settlement.bet_id)

    async def settle_bet(self, bet_id: int, payout: int) -> int:
        """Settle an open bet with a total `payout` (0 = loss); returns the new balance."""
        if payout < 0:
            raise ValueError("settle_bet payout must not be negative")
        return await self._finish_bet(bet_id, payout, refund=False)

    async def _close_bet_with_payout(
        self, bet_id: int, user_id: int, game: str, stake: int, payout: int, now: int
    ) -> int:
        """Settle one open bet inside a caller-held transaction; returns the owner's balance.

        Credits `payout`, records a win/push/payout row when it is above zero, and raises
        BetNotOpenError if the bet is no longer open. The caller commits.
        """
        cursor = await self.conn.execute(
            "UPDATE gambling_bets SET status = 'settled', payout = ? "
            "WHERE id = ? AND status = 'open';",
            (payout, bet_id),
        )
        if cursor.rowcount == 0:
            raise BetNotOpenError(bet_id)
        if payout > 0:
            await self.conn.execute(
                "UPDATE wallets SET balance = balance + ? WHERE user_id = ?;",
                (payout, user_id),
            )
        wallet = await self.get_wallet(user_id)
        assert wallet is not None
        if payout > 0:
            if payout > stake:
                prefix = "win"
            elif payout == stake:
                prefix = "push"
            else:
                prefix = "payout"
            await self._insert_transaction(
                user_id, payout, f"{prefix}:{game}", None, wallet.balance, now
            )
        return wallet.balance

    async def settle_lobby(self, payouts: Mapping[int, int]) -> dict[int, int]:
        """Settle every bet of one table in ONE transaction; returns {bet_id: owner's new balance}.

        All or nothing: if any bet is not open, a payout is invalid, or anything fails midway,
        nothing changes, so a crash can never leave a pot half paid.
        """
        if not payouts:
            raise ValueError("settle_lobby needs at least one bet")
        if any(amount < 0 for amount in payouts.values()):
            raise ValueError("settle_lobby payouts must not be negative")
        now = int(time.time())
        async with self._write_tx():
            rows: dict[int, tuple[int, str, int]] = {}
            for bet_id in payouts:
                async with self.conn.execute(
                    "SELECT user_id, game, stake FROM gambling_bets "
                    "WHERE id = ? AND status = 'open';",
                    (bet_id,),
                ) as cursor:
                    row = await cursor.fetchone()
                if row is None:
                    raise BetNotOpenError(bet_id)
                rows[bet_id] = (row[0], row[1], row[2])
            games = {game for _, game, _ in rows.values()}
            if len(games) != 1:
                raise ValueError("a lobby settles one game at a time")
            game = games.pop()
            stakes = [rows[bet_id][2] for bet_id in payouts]
            amounts = list(payouts.values())
            if game in POOLED_GAMES and sum(amounts) > sum(stakes):
                raise ValueError("a pooled game cannot pay out more than was staked")

            balances: dict[int, int] = {}
            for bet_id, payout in payouts.items():
                user_id, _, stake = rows[bet_id]
                balances[bet_id] = await self._close_bet_with_payout(
                    bet_id, user_id, game, stake, payout, now
                )
            await self._credit_pot(game, lobby_house_take(game, stakes, amounts), now)
            await self.conn.commit()
        self._notify_settled(
            [
                BetSettlement(bet_id, rows[bet_id][0], game, rows[bet_id][2], payout, None)
                for bet_id, payout in payouts.items()
            ]
        )
        return balances

    async def cash_out_lobby(self, lobby_id: str, payouts: Mapping[int, int]) -> dict[int, int]:
        """Pay out poker seats of one table (one seat leaving, or the final settle) atomically.

        `payouts` maps bet id to the Renown paid for that seat's chips. Unlike `settle_lobby`, a
        seat may be paid more than its own buy-in; the limit is the whole table: everything paid
        so far plus this call may not exceed the sum of all buy-ins. Poker has no rake, so
        nothing goes to the lottery pot. Returns {bet_id: owner's new balance}.
        """
        if not payouts:
            raise ValueError("cash_out_lobby needs at least one bet")
        if any(amount < 0 for amount in payouts.values()):
            raise ValueError("cash_out_lobby payouts must not be negative")
        now = int(time.time())
        async with self._write_tx():
            rows: dict[int, tuple[int, int]] = {}
            for bet_id in payouts:
                async with self.conn.execute(
                    "SELECT user_id, game, stake, lobby_id FROM gambling_bets "
                    "WHERE id = ? AND status = 'open';",
                    (bet_id,),
                ) as cursor:
                    row = await cursor.fetchone()
                if row is None:
                    raise BetNotOpenError(bet_id)
                if row[1] != "poker" or row[3] != lobby_id:
                    raise ValueError("only poker bets of this table can be cashed out here")
                rows[bet_id] = (row[0], row[2])
            total, paid = await self._poker_table_totals(lobby_id)
            if paid + sum(payouts.values()) > total:
                raise ValueError("that would pay out more than the table holds")
            balances: dict[int, int] = {}
            for bet_id, payout in payouts.items():
                user_id, stake = rows[bet_id]
                balances[bet_id] = await self._close_bet_with_payout(
                    bet_id, user_id, "poker", stake, payout, now
                )
            await self.conn.commit()
        self._notify_settled(
            [
                BetSettlement(bet_id, rows[bet_id][0], "poker", rows[bet_id][1], payout, None)
                for bet_id, payout in payouts.items()
            ]
        )
        return balances

    async def _poker_table_totals(self, lobby_id: str) -> tuple[int, int]:
        """(all buy-ins, Renown already paid or refunded) for one poker table."""
        async with self.conn.execute(
            "SELECT COALESCE(SUM(stake), 0), "
            "COALESCE(SUM(CASE WHEN status = 'open' THEN 0 ELSE payout END), 0) "
            "FROM gambling_bets WHERE lobby_id = ?;",
            (lobby_id,),
        ) as cursor:
            row = await cursor.fetchone()
        assert row is not None
        return row[0], row[1]

    async def _poker_refund_amount(self, lobby_id: str, stake: int) -> int:
        """What an open poker bet is refunded: its share (by buy-in) of everything left.

        Seats already paid took their chips out, so the open seats split the rest of the table:
        less than their buy-ins when someone left with winnings, more when someone busted. With
        nothing paid yet this is exactly the buy-in. The shares never add up to more than what
        is left, so a restart cannot create Renown, and nothing is lost to busted seats.
        """
        total, paid = await self._poker_table_totals(lobby_id)
        async with self.conn.execute(
            "SELECT COALESCE(SUM(stake), 0) FROM gambling_bets "
            "WHERE lobby_id = ? AND status = 'open';",
            (lobby_id,),
        ) as cursor:
            row = await cursor.fetchone()
        assert row is not None
        open_stakes = row[0]
        if open_stakes <= 0:
            return 0
        return max(0, (total - paid) * stake // open_stakes)

    async def refund_bet(self, bet_id: int) -> int:
        """Return an open bet's stake to the wallet; returns the new balance."""
        return await self._finish_bet(bet_id, 0, refund=True)

    async def refund_open_bets(self) -> int:
        """Refund every bet still open (left behind by a previous process); returns the count."""
        async with self.conn.execute(
            "SELECT id FROM gambling_bets WHERE status = 'open' ORDER BY id;"
        ) as cursor:
            bet_ids = [r[0] for r in await cursor.fetchall()]
        refunded = 0
        for bet_id in bet_ids:
            try:
                await self.refund_bet(bet_id)
            except BetNotOpenError:
                continue
            refunded += 1
        return refunded

    async def get_gamble_stats(self, user_id: int) -> list[tuple[str, int, int, int]]:
        """Per-game (game, plays, wagered, returned) over a user's settled bets."""
        query = (
            "SELECT game, COUNT(DISTINCT COALESCE(group_id, id)), SUM(stake), SUM(payout) "
            "FROM gambling_bets "
            "WHERE user_id = ? AND status = 'settled' GROUP BY game ORDER BY game ASC;"
        )
        async with self.conn.execute(query, (user_id,)) as cursor:
            return [(r[0], r[1], r[2], r[3]) for r in await cursor.fetchall()]

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
