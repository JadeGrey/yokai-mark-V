# Admin Panel P-A1: Bot-side Moderation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the bot a blacklist, an adminlist and a heartbeat, all stored in SQLite and enforced or written by the bot, so the later admin panel has something real to manage. Useful on its own before any panel exists.

**Architecture:** Migration v4 adds `blacklist`, `admins`, `admin_audit` and `bot_heartbeat`. A `ModerationStore` reads both lists with a ~5 s TTL cache (no IPC; a panel edit lands within seconds). A `YokaiTree` subclass plus the view base class deny blacklisted users with an ephemeral embed. The two owner-only commands widen to "owner or admin". A `HeartbeatWriter` upserts one row every 30 s.

**Tech Stack:** Python 3.12+, discord.py 2.7.1 (`commands.Bot(tree_cls=...)`, `CommandTree.interaction_check`), aiosqlite, pytest (`asyncio_mode = "auto"`), ruff. **No new dependencies in this phase.**

**Spec:** `docs/superpowers/specs/2026-10-05-admin-panel-design.md` (§5 data model, §6 bot-side enforcement, §11 phase P-A1). Phases P-A2 to P-A6 each get their own plan when they start; they depend on what this phase ships.

## Global Constraints

- Python 3.12+, `discord.py>=2.7.1`; every module starts with `from __future__ import annotations`, fully type-hinted.
- Use `logging`; no `print`. No bare `except`: catch specific errors.
- Every user-visible message goes through `ui/embeds.py` (`EmbedFactory`, `send`); no raw `send_message("text")`/`channel.send("text")` (enforced by `tests/test_code_standards.py`). Error embeds are ephemeral.
- No message-content or other privileged intents; `allowed_mentions` stays none.
- Persona: error text is plain and useful; no quip on error embeds (`include_quip=False`). At most one emoji per embed (the error embed's cross is the one).
- Never commit `.env`, tokens, databases or cookie files. Commit per task.
- Ask before adding any dependency not in `SPEC.md` §11 (none are needed here).
- Verification commands (PowerShell, repo root): `python -m pytest -q`, `python -m ruff check .`, `python -m ruff format --check .`.
- Git commits end with: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`

## Verified facts this plan relies on

- discord.py 2.7.1: `commands.Bot.__init__` accepts `tree_cls`. When `CommandTree.interaction_check` returns `False`, the tree only sets `interaction.command_failed = True` and returns. It does **not** call `on_error`, so the check must send the denial itself. Autocomplete interactions cannot receive a message reply.
- `RecommendView.interaction_check` (`ui/views.py:245`) overrides `BaseView.interaction_check` without calling `super()`, so it needs its own gate.
- `send()` in `ui/embeds.py` catches and logs its own errors; callers need no try/except around it.
- Today `/diag` (`cogs/meta.py:188`) and `/economy-admin` (`cogs/economy.py:161`) compare `interaction.user.id != self.bot.config.owner_id`. `DiagView` locks its buttons to `[owner_id]`, which would lock out an admin who legitimately ran `/diag`.
- The bot's `Database._write_lock` is in-process only. Heartbeat writes take it like every other write.

## Review Focus

Inputs and conditions the spec implies but plain happy-path tests would miss, most likely first. Each has a test in the task named.

1. An existing v3 database with real rows upgrades to v4 without losing data (Task 1).
2. The owner appears in the `blacklist` table (hand-edited or a panel bug): the owner must still never be blocked and still be admin (Task 3).
3. A blacklisted user triggers an **autocomplete** interaction: the gate must deny without trying to send a message (Task 4).
4. The moderation read fails (DB closed or locked): commands must keep working on the last good copy, fail open if there never was one, and not hammer the DB on every command (Task 3).
5. A user present in both `admins` and `blacklist` is **not** an admin, and a blacklisted user clicking an old button on a still-live view is denied (Tasks 3 and 6).
6. A blacklisted human sharing a voice channel with one other human: nobody pays out the blacklisted user, and the other human is still paid (Task 8).

---

## File structure

| File | Responsibility |
|---|---|
| `src/yokai/storage/db.py` (modify) | Migration v4; `get_moderation_lists`, `write_heartbeat`, `clear_heartbeat` |
| `src/yokai/moderation/__init__.py` (create) | Re-exports `ModerationStore`, `YokaiTree`, `deny_if_blacklisted` |
| `src/yokai/moderation/store.py` (create) | `ModerationStore`: TTL cache, owner rules, failure fallback |
| `src/yokai/moderation/gate.py` (create) | `blocked_embed`, `deny_if_blacklisted`, `YokaiTree` |
| `src/yokai/heartbeat.py` (create) | `HeartbeatWriter` background task |
| `src/yokai/bot.py` (modify) | Own the store and heartbeat, install `YokaiTree`, start/stop heartbeat |
| `src/yokai/ui/views.py` (modify) | Blacklist gate in `BaseView` and `RecommendView`; `DiagView(admin_id=...)` |
| `src/yokai/cogs/meta.py`, `src/yokai/cogs/economy.py` (modify) | Owner check becomes admin check |
| `src/yokai/economy/voice_tracker.py` (modify) | Skip crediting blacklisted users |
| `tests/moderation_helpers.py` (create) | `fake_moderation(...)` for cog and tracker tests |
| `tests/test_moderation_storage.py`, `test_moderation_store.py`, `test_moderation_gate.py`, `test_heartbeat.py` (create) | New tests |
| `tests/test_bot_wiring.py`, `test_economy_cog.py`, `test_utility.py`, `test_economy_voice_tracker.py` (modify) | Adapt fakes, add cases |
| `TASKS.md` (modify) | Phase 9 entry |

---

### Task 1: Migration v4

**Files:**
- Modify: `src/yokai/storage/db.py` (schema constants near line 20, `_migrate` near line 136)
- Test: `tests/test_moderation_storage.py`

**Interfaces:**
- Produces: `CURRENT_SCHEMA_VERSION == 4`; tables `blacklist(user_id PK, reason, added_ts, added_by)`, `admins(user_id PK, added_ts, added_by)`, `admin_audit(id, ts, actor, module, action, target, detail_json, reason)`, `bot_heartbeat(id CHECK id=1, ts, pid, version)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_moderation_storage.py`:

```python
"""Tests for migration v4 and the moderation/heartbeat Database methods."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from yokai.storage.db import CURRENT_SCHEMA_VERSION, Database


async def _open(tmp_path: Path) -> Database:
    db = Database(tmp_path / "t.db")
    await db.initialize()
    return db


async def test_migration_v4_creates_the_moderation_tables(tmp_path: Path) -> None:
    db = await _open(tmp_path)
    async with db.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table';") as cursor:
        names = {row[0] for row in await cursor.fetchall()}
    assert {"blacklist", "admins", "admin_audit", "bot_heartbeat"} <= names
    assert CURRENT_SCHEMA_VERSION == 4
    await db.close()


async def test_reopening_a_v4_database_is_a_no_op(tmp_path: Path) -> None:
    db = await _open(tmp_path)
    await db.close()
    db = await _open(tmp_path)
    async with db.conn.execute("PRAGMA user_version;") as cursor:
        row = await cursor.fetchone()
    assert row is not None and row[0] == 4
    await db.close()


async def test_a_v3_database_with_data_upgrades_without_losing_rows(tmp_path: Path) -> None:
    """Review Focus 1: a real v3 file keeps its rows when it gains the v4 tables."""
    db = await _open(tmp_path)
    await db.log_command(user_id=101, command="play")
    await db.credit_wallet(101, 50, reason="daily", welcome_bonus=200)
    await db.close()

    # Rewind the version marker and drop the v4 tables to mimic a genuine v3 file.
    raw = sqlite3.connect(tmp_path / "t.db")
    for table in ("blacklist", "admins", "admin_audit", "bot_heartbeat"):
        raw.execute(f"DROP TABLE {table};")
    raw.execute("PRAGMA user_version = 3;")
    raw.commit()
    raw.close()

    db = await _open(tmp_path)
    async with db.conn.execute("PRAGMA user_version;") as cursor:
        row = await cursor.fetchone()
    assert row is not None and row[0] == 4
    async with db.conn.execute("SELECT COUNT(*) FROM command_log;") as cursor:
        assert (await cursor.fetchone())[0] == 1  # type: ignore[index]
    wallet = await db.get_wallet(101)
    assert wallet is not None and wallet.balance == 250
    await db.close()


async def test_bot_heartbeat_allows_only_one_row(tmp_path: Path) -> None:
    db = await _open(tmp_path)
    await db.conn.execute("INSERT INTO bot_heartbeat (id, ts, pid, version) VALUES (1, 1, 1, 'x');")
    with pytest.raises(sqlite3.IntegrityError):
        await db.conn.execute(
            "INSERT INTO bot_heartbeat (id, ts, pid, version) VALUES (2, 1, 1, 'x');"
        )
    await db.close()
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_moderation_storage.py -v`
Expected: FAIL (`CURRENT_SCHEMA_VERSION` is 3, tables missing).

- [ ] **Step 3: Implement**

In `src/yokai/storage/db.py` change `CURRENT_SCHEMA_VERSION = 3` to `4`, add after `MIGRATION_V3`:

```python
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
```

and at the end of `_migrate`, following the existing pattern:

```python
        if version < 4:
            logger.info("Applying SQLite migration v4...")
            await self.conn.executescript(MIGRATION_V4)
            await self.conn.execute("PRAGMA user_version = 4;")
            await self.conn.commit()
            logger.info("SQLite schema migrated to version 4.")
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_moderation_storage.py tests/test_storage.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/yokai/storage/db.py tests/test_moderation_storage.py
git commit -m "feat(moderation): add migration v4 for blacklist, admins, audit and heartbeat tables" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Database moderation and heartbeat methods

**Files:**
- Modify: `src/yokai/storage/db.py` (add methods after `ping`)
- Test: `tests/test_moderation_storage.py` (append)

**Interfaces:**
- Consumes: Task 1 tables.
- Produces (on `Database`):
  - `async def get_moderation_lists(self) -> tuple[frozenset[int], frozenset[int]]` returns `(blacklisted_ids, admin_ids)`.
  - `async def write_heartbeat(self, pid: int, version: str, now: Optional[int] = None) -> None` upserts row id=1.
  - `async def clear_heartbeat(self) -> None` deletes row id=1.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_moderation_storage.py`)

```python
async def test_get_moderation_lists_is_empty_by_default(tmp_path: Path) -> None:
    db = await _open(tmp_path)
    assert await db.get_moderation_lists() == (frozenset(), frozenset())
    await db.close()


async def test_get_moderation_lists_returns_both_lists(tmp_path: Path) -> None:
    db = await _open(tmp_path)
    await db.conn.execute(
        "INSERT INTO blacklist (user_id, reason, added_ts, added_by) VALUES (5, 'spam', 1, 'panel');"
    )
    await db.conn.execute("INSERT INTO admins (user_id, added_ts, added_by) VALUES (7, 1, 'panel');")
    await db.conn.commit()
    assert await db.get_moderation_lists() == (frozenset({5}), frozenset({7}))
    await db.close()


async def test_write_heartbeat_upserts_a_single_row(tmp_path: Path) -> None:
    db = await _open(tmp_path)
    await db.write_heartbeat(pid=10, version="0.1.0", now=100)
    await db.write_heartbeat(pid=11, version="0.1.1", now=130)
    async with db.conn.execute("SELECT id, ts, pid, version FROM bot_heartbeat;") as cursor:
        assert await cursor.fetchall() == [(1, 130, 11, "0.1.1")]
    await db.close()


async def test_clear_heartbeat_removes_the_row(tmp_path: Path) -> None:
    db = await _open(tmp_path)
    await db.write_heartbeat(pid=10, version="0.1.0", now=100)
    await db.clear_heartbeat()
    async with db.conn.execute("SELECT COUNT(*) FROM bot_heartbeat;") as cursor:
        assert (await cursor.fetchone())[0] == 0  # type: ignore[index]
    await db.close()
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_moderation_storage.py -v`
Expected: FAIL with `AttributeError` for the new methods.

- [ ] **Step 3: Implement** (in `Database`, after `ping`)

```python
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

    async def clear_heartbeat(self) -> None:
        """Remove the liveness row so the panel shows offline immediately after a clean stop."""
        async with self._write_lock:
            await self.conn.execute("DELETE FROM bot_heartbeat WHERE id = 1;")
            await self.conn.commit()
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_moderation_storage.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/yokai/storage/db.py tests/test_moderation_storage.py
git commit -m "feat(moderation): add moderation list reads and heartbeat writes to Database" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: ModerationStore

**Files:**
- Create: `src/yokai/moderation/__init__.py`, `src/yokai/moderation/store.py`
- Test: `tests/test_moderation_store.py`

**Interfaces:**
- Consumes: `Database.get_moderation_lists()` (Task 2).
- Produces: `ModerationStore(db, owner_id: int, ttl_s: float = 5.0, clock: Callable[[], float] = time.monotonic)` with `async is_blacklisted(user_id: int) -> bool` and `async is_admin(user_id: int) -> bool`. Rules: the owner is never blacklisted and always admin; a blacklisted user is never admin; a failed read keeps the last good copy (fail open if none) and waits one TTL before retrying.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_moderation_store.py`:

```python
"""Tests for the ModerationStore cache, owner rules and failure fallback."""

from __future__ import annotations

import asyncio
import sqlite3
from unittest.mock import AsyncMock, MagicMock

from yokai.moderation.store import ModerationStore

OWNER = 1


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def make_store(
    black: frozenset[int] = frozenset(),
    admins: frozenset[int] = frozenset(),
    ttl_s: float = 5.0,
) -> tuple[ModerationStore, MagicMock, FakeClock]:
    db = MagicMock()
    db.get_moderation_lists = AsyncMock(return_value=(black, admins))
    clock = FakeClock()
    return ModerationStore(db, owner_id=OWNER, ttl_s=ttl_s, clock=clock), db, clock


async def test_blacklisted_user_is_reported() -> None:
    store, _, _ = make_store(black=frozenset({5}))
    assert await store.is_blacklisted(5) is True
    assert await store.is_blacklisted(6) is False


async def test_admin_user_is_reported() -> None:
    store, _, _ = make_store(admins=frozenset({7}))
    assert await store.is_admin(7) is True
    assert await store.is_admin(8) is False


async def test_owner_is_never_blacklisted_and_always_admin() -> None:
    """Review Focus 2: the owner in the blacklist table is still unblockable and admin."""
    store, db, _ = make_store(black=frozenset({OWNER}))
    assert await store.is_blacklisted(OWNER) is False
    assert await store.is_admin(OWNER) is True
    db.get_moderation_lists.assert_not_awaited()  # owner rules need no DB read


async def test_a_user_in_both_lists_is_not_an_admin() -> None:
    """Review Focus 5: blacklist wins over adminlist."""
    store, _, _ = make_store(black=frozenset({9}), admins=frozenset({9}))
    assert await store.is_blacklisted(9) is True
    assert await store.is_admin(9) is False


async def test_reads_are_cached_within_the_ttl_and_refreshed_after() -> None:
    store, db, clock = make_store(black=frozenset({5}))
    await store.is_blacklisted(5)
    await store.is_blacklisted(5)
    assert db.get_moderation_lists.await_count == 1

    clock.now += 5.1
    db.get_moderation_lists.return_value = (frozenset(), frozenset())
    assert await store.is_blacklisted(5) is False
    assert db.get_moderation_lists.await_count == 2


async def test_concurrent_checks_share_one_refresh() -> None:
    store, db, _ = make_store(black=frozenset({5}))
    results = await asyncio.gather(*(store.is_blacklisted(5) for _ in range(20)))
    assert all(results)
    assert db.get_moderation_lists.await_count == 1


async def test_failed_refresh_keeps_the_last_good_copy() -> None:
    """Review Focus 4: a DB hiccup must not unblock users or crash commands."""
    store, db, clock = make_store(black=frozenset({5}))
    assert await store.is_blacklisted(5) is True

    clock.now += 6
    db.get_moderation_lists.side_effect = sqlite3.OperationalError("database is locked")
    assert await store.is_blacklisted(5) is True


async def test_failed_first_load_fails_open_and_does_not_hammer_the_db() -> None:
    """Review Focus 4: no copy yet means nobody is blocked; retry only after one TTL."""
    store, db, clock = make_store()
    db.get_moderation_lists.side_effect = RuntimeError("Database connection is not open.")

    assert await store.is_blacklisted(5) is False
    assert await store.is_admin(7) is False
    assert db.get_moderation_lists.await_count == 1  # second call reused the failed attempt

    clock.now += 5.1
    db.get_moderation_lists.side_effect = None
    db.get_moderation_lists.return_value = (frozenset({5}), frozenset())
    assert await store.is_blacklisted(5) is True
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_moderation_store.py -v`
Expected: FAIL (`ModuleNotFoundError: yokai.moderation`).

- [ ] **Step 3: Implement**

Create `src/yokai/moderation/store.py`:

```python
"""Cached, read-only view of the blacklist and adminlist the admin panel manages."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from yokai.storage.db import Database

logger = logging.getLogger(__name__)


class ModerationStore:
    """Answers "is this user blacklisted / an admin?" from a short-lived cache.

    The panel edits the tables from another process, so there is no push channel: the cache
    simply expires after `ttl_s` seconds and the next check reloads. The owner is special-cased
    and never touches the database.
    """

    def __init__(
        self,
        db: Database,
        owner_id: int,
        ttl_s: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._db = db
        self.owner_id = owner_id
        self.ttl_s = ttl_s
        self._clock = clock
        self._lock = asyncio.Lock()
        self._loaded_at: Optional[float] = None
        self._blacklisted: frozenset[int] = frozenset()
        self._admins: frozenset[int] = frozenset()

    async def is_blacklisted(self, user_id: int) -> bool:
        """True if the user is on the blacklist. The owner never is."""
        if user_id == self.owner_id:
            return False
        await self._ensure_fresh()
        return user_id in self._blacklisted

    async def is_admin(self, user_id: int) -> bool:
        """True for the owner, or for an adminlist member who is not also blacklisted."""
        if user_id == self.owner_id:
            return True
        await self._ensure_fresh()
        return user_id in self._admins and user_id not in self._blacklisted

    def _is_fresh(self) -> bool:
        return self._loaded_at is not None and self._clock() - self._loaded_at < self.ttl_s

    async def _ensure_fresh(self) -> None:
        if self._is_fresh():
            return
        async with self._lock:
            if self._is_fresh():  # another waiter refreshed while we queued for the lock
                return
            try:
                blacklisted, admins = await self._db.get_moderation_lists()
            except (sqlite3.Error, RuntimeError) as exc:
                # Keep the last good copy (empty, i.e. fail open, if there never was one) and
                # wait one TTL before trying again so a broken DB is not hit on every command.
                logger.warning("Could not refresh moderation lists, using the last copy: %s", exc)
                self._loaded_at = self._clock()
                return
            self._blacklisted = blacklisted
            self._admins = admins
            self._loaded_at = self._clock()
```

Create `src/yokai/moderation/__init__.py`:

```python
"""Blacklist, adminlist and the gates that enforce them."""

from __future__ import annotations

from yokai.moderation.store import ModerationStore

__all__ = ["ModerationStore"]
```

(Task 4 extends this file's exports.)

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_moderation_store.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/yokai/moderation tests/test_moderation_store.py
git commit -m "feat(moderation): add cached ModerationStore with owner and failure rules" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Gate and YokaiTree

**Files:**
- Create: `src/yokai/moderation/gate.py`
- Modify: `src/yokai/moderation/__init__.py`
- Test: `tests/test_moderation_gate.py`

**Interfaces:**
- Consumes: `ModerationStore.is_blacklisted` (Task 3); `EmbedFactory.error(...)` and `send(interaction, embed, view=None, ephemeral=False)` from `yokai.ui.embeds`.
- Produces: `blocked_embed(bot_avatar_url: Optional[str]) -> discord.Embed`; `async deny_if_blacklisted(interaction: discord.Interaction) -> bool` (True means denied; a blocked embed was sent unless the interaction is autocomplete); `class YokaiTree(app_commands.CommandTree)` whose `interaction_check` returns `not await deny_if_blacklisted(interaction)`. An `interaction.client` that has no real `ModerationStore` on `.moderation` is never denied (keeps every existing mock-based test working).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_moderation_gate.py`:

```python
"""Tests for the blacklist gate used by the command tree and views."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import discord

from yokai.moderation.gate import YokaiTree, deny_if_blacklisted
from yokai.moderation.store import ModerationStore


def _store(black: frozenset[int]) -> ModerationStore:
    db = MagicMock()
    db.get_moderation_lists = AsyncMock(return_value=(black, frozenset()))
    return ModerationStore(db, owner_id=1)


def _interaction(user_id: int, store: object | None) -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock(id=user_id)
    interaction.type = discord.InteractionType.application_command
    interaction.client = MagicMock()
    interaction.client.user = None
    interaction.client.moderation = store
    return interaction


async def test_allowed_user_passes_without_a_reply() -> None:
    interaction = _interaction(10, _store(frozenset({5})))
    with patch("yokai.moderation.gate.send", new=AsyncMock()) as sent:
        assert await deny_if_blacklisted(interaction) is False
    sent.assert_not_awaited()


async def test_blacklisted_user_gets_an_ephemeral_blocked_embed() -> None:
    interaction = _interaction(5, _store(frozenset({5})))
    with patch("yokai.moderation.gate.send", new=AsyncMock()) as sent:
        assert await deny_if_blacklisted(interaction) is True
    embed = sent.await_args.kwargs["embed"]
    assert "Access blocked" in (embed.title or "")
    assert sent.await_args.kwargs["ephemeral"] is True


async def test_blacklisted_autocomplete_is_denied_without_a_reply() -> None:
    """Review Focus 3: autocomplete cannot receive a message, so just deny quietly."""
    interaction = _interaction(5, _store(frozenset({5})))
    interaction.type = discord.InteractionType.autocomplete
    with patch("yokai.moderation.gate.send", new=AsyncMock()) as sent:
        assert await deny_if_blacklisted(interaction) is True
    sent.assert_not_awaited()


async def test_a_client_without_a_real_store_is_never_denied() -> None:
    interaction = _interaction(5, MagicMock())  # e.g. a MagicMock bot in unrelated tests
    with patch("yokai.moderation.gate.send", new=AsyncMock()) as sent:
        assert await deny_if_blacklisted(interaction) is False
    sent.assert_not_awaited()


async def test_tree_interaction_check_follows_the_gate() -> None:
    tree = YokaiTree(discord.Client(intents=discord.Intents.none()))
    blocked = _interaction(5, _store(frozenset({5})))
    allowed = _interaction(10, _store(frozenset({5})))
    with patch("yokai.moderation.gate.send", new=AsyncMock()):
        assert await tree.interaction_check(blocked) is False
        assert await tree.interaction_check(allowed) is True
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_moderation_gate.py -v`
Expected: FAIL (`ModuleNotFoundError: yokai.moderation.gate`).

- [ ] **Step 3: Implement**

Create `src/yokai/moderation/gate.py`:

```python
"""Blacklist enforcement for slash commands, autocomplete and component interactions."""

from __future__ import annotations

import logging
from typing import Optional

import discord
from discord import app_commands

from yokai.moderation.store import ModerationStore
from yokai.ui.embeds import EmbedFactory, send

logger = logging.getLogger(__name__)


def blocked_embed(bot_avatar_url: Optional[str] = None) -> discord.Embed:
    """Plain, ephemeral-friendly explanation shown to a blacklisted user."""
    return EmbedFactory.error(
        message="You're blocked from using Yokai.",
        title="Access blocked",
        user_hint="If you think this is a mistake, ask the bot owner.",
        context="Permissions",
        bot_avatar_url=bot_avatar_url,
        include_quip=False,
    )


def _store_of(interaction: discord.Interaction) -> Optional[ModerationStore]:
    store = getattr(interaction.client, "moderation", None)
    return store if isinstance(store, ModerationStore) else None


async def deny_if_blacklisted(interaction: discord.Interaction) -> bool:
    """Return True if the user is blacklisted, after telling them (when Discord allows it).

    A client with no real `ModerationStore` is treated as "nobody is blacklisted".
    """
    store = _store_of(interaction)
    if store is None or not await store.is_blacklisted(interaction.user.id):
        return False

    logger.info("Blocked blacklisted user %d", interaction.user.id)
    # Autocomplete interactions accept only choices, never a message.
    if interaction.type is not discord.InteractionType.autocomplete:
        client_user = interaction.client.user
        avatar_url = str(client_user.display_avatar.url) if client_user else None
        await send(interaction, embed=blocked_embed(avatar_url), ephemeral=True)
    return True


class YokaiTree(app_commands.CommandTree):
    """Command tree whose global check rejects blacklisted users before any command runs."""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return not await deny_if_blacklisted(interaction)
```

Replace `src/yokai/moderation/__init__.py`:

```python
"""Blacklist, adminlist and the gates that enforce them."""

from __future__ import annotations

from yokai.moderation.gate import YokaiTree, deny_if_blacklisted
from yokai.moderation.store import ModerationStore

__all__ = ["ModerationStore", "YokaiTree", "deny_if_blacklisted"]
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_moderation_gate.py tests/test_moderation_store.py tests/test_code_standards.py -v`
Expected: PASS. (If importing `yokai.moderation` raises a circular import via `yokai.ui.embeds`, make the `gate` import lazy in `__init__` rather than reordering other modules.)

- [ ] **Step 5: Commit**

```powershell
git add src/yokai/moderation tests/test_moderation_gate.py
git commit -m "feat(moderation): add blacklist gate and YokaiTree" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Wire the store and tree into the bot

**Files:**
- Modify: `src/yokai/bot.py` (imports; `__init__` near lines 46 and 96)
- Test: `tests/test_bot_wiring.py` (append)

**Interfaces:**
- Consumes: `ModerationStore`, `YokaiTree`.
- Produces: `bot.moderation: ModerationStore` (owner id from config) and `bot.tree` is a `YokaiTree`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_bot_wiring.py`)

```python
@pytest.mark.asyncio
async def test_bot_owns_a_moderation_store_and_the_yokai_tree() -> None:
    from yokai.moderation import ModerationStore, YokaiTree

    bot = make_bot()
    assert isinstance(bot.moderation, ModerationStore)
    assert bot.moderation.owner_id == 456
    assert isinstance(bot.tree, YokaiTree)


@pytest.mark.asyncio
async def test_a_blacklisted_user_is_stopped_at_the_tree() -> None:
    bot = make_bot()
    bot.db.get_moderation_lists = AsyncMock(return_value=(frozenset({7}), frozenset()))
    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock(id=7)
    interaction.type = discord.InteractionType.application_command
    interaction.client = bot
    with patch("yokai.moderation.gate.send", new=AsyncMock()) as sent:
        assert await bot.tree.interaction_check(interaction) is False
    sent.assert_awaited_once()
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_bot_wiring.py -v -k "moderation or blacklisted"`
Expected: FAIL (`AttributeError: moderation`).

- [ ] **Step 3: Implement**

In `src/yokai/bot.py`: add `from yokai.moderation import ModerationStore, YokaiTree` with the other `yokai.*` imports (alphabetical, before `yokai.music...`). After `self.db = Database(config.db_path)` add:

```python
        self.moderation = ModerationStore(self.db, config.owner_id)
```

and add `tree_cls=YokaiTree,` to the `super().__init__(...)` call (after `activity=...`).

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_bot_wiring.py -v`
Expected: PASS (all existing wiring tests too).

- [ ] **Step 5: Commit**

```powershell
git add src/yokai/bot.py tests/test_bot_wiring.py
git commit -m "feat(moderation): install ModerationStore and YokaiTree on the bot" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Gate the views

**Files:**
- Modify: `src/yokai/ui/views.py` (imports; `BaseView.interaction_check` line 39; `RecommendView.interaction_check` line 245)
- Test: `tests/test_moderation_gate.py` (append)

**Interfaces:**
- Consumes: `deny_if_blacklisted` (Task 4).
- Produces: every `BaseView` subclass (and `RecommendView`, which overrides the check) refuses blacklisted users before any other permission logic runs.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_moderation_gate.py`)

```python
from yokai.music.models import MusicQueue, Track  # noqa: E402  (grouped with the view tests)
from yokai.music.player import GuildPlayer  # noqa: E402
from yokai.ui.views import BaseView, RecommendView  # noqa: E402


async def test_blacklisted_user_is_refused_by_a_live_base_view() -> None:
    """Review Focus 5: an old button on a still-live view cannot be used once blacklisted."""
    view = BaseView(allowed_user_ids=None)  # open to everyone, so only the gate can refuse
    interaction = _interaction(5, _store(frozenset({5})))
    with patch("yokai.moderation.gate.send", new=AsyncMock()) as sent:
        assert await view.interaction_check(interaction) is False
    sent.assert_awaited_once()


async def test_non_blacklisted_user_still_passes_an_open_base_view() -> None:
    view = BaseView(allowed_user_ids=None)
    interaction = _interaction(10, _store(frozenset({5})))
    assert await view.interaction_check(interaction) is True


async def test_blacklisted_requester_is_refused_by_the_recommend_view() -> None:
    bot = MagicMock()
    bot.config.max_queue_size = 50
    bot.presence = AsyncMock()
    player = GuildPlayer(guild_id=1, bot=bot, resolver=MagicMock())
    player.queue = MusicQueue(max_size=50)
    view = RecommendView(
        player=player,
        tracks=[Track(video_id="r1", title="Rec 1", duration_s=100)],
        requester_id=5,
        seed_title="Seed",
    )
    interaction = _interaction(5, _store(frozenset({5})))
    interaction.user = MagicMock(spec=discord.Member, id=5)
    with patch("yokai.moderation.gate.send", new=AsyncMock()):
        assert await view.interaction_check(interaction) is False
```

(If ruff objects to the late imports, move them to the top of the file with the others.)

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_moderation_gate.py -v -k "view"`
Expected: FAIL (the gate is not consulted yet).

- [ ] **Step 3: Implement**

In `src/yokai/ui/views.py` add `from yokai.moderation.gate import deny_if_blacklisted` to the imports, and make the first statement of `BaseView.interaction_check` and of `RecommendView.interaction_check`:

```python
        if await deny_if_blacklisted(interaction):
            return False
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_moderation_gate.py tests/test_recommend.py tests/test_economy_views.py tests/test_gambling_views.py tests/test_blackjack_view.py tests/test_utility.py -v`
Expected: PASS (existing view tests use mock clients without a real store).

- [ ] **Step 5: Commit**

```powershell
git add src/yokai/ui/views.py tests/test_moderation_gate.py
git commit -m "feat(moderation): refuse blacklisted users in every view" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Widen owner-only commands to admins

**Files:**
- Create: `tests/moderation_helpers.py`
- Modify: `src/yokai/cogs/meta.py` (`/diag`, lines ~179-198 and ~280), `src/yokai/cogs/economy.py` (`/economy-admin`, lines ~153-171), `src/yokai/ui/views.py` (`DiagView`, lines ~326-336)
- Test: `tests/test_economy_cog.py`, `tests/test_utility.py`

**Interfaces:**
- Consumes: `bot.moderation.is_admin(user_id) -> bool` (Task 3).
- Produces: `/diag` and `/economy-admin` run for the owner or any adminlist member; the denial text keeps the words "Access denied" but says "restricted to Yokai admins" with title "Admins only". `DiagView(admin_id: int, bot_avatar_url=None, timeout=180.0)` locks its buttons to the admin who ran `/diag`.

- [ ] **Step 1: Create the test helper**

`tests/moderation_helpers.py`:

```python
"""Shared fake for the bot's ModerationStore in cog and tracker tests."""

from __future__ import annotations

from typing import Iterable
from unittest.mock import AsyncMock, MagicMock


def fake_moderation(
    owner_id: int,
    admins: Iterable[int] = (),
    blacklisted: Iterable[int] = (),
) -> MagicMock:
    """A stand-in with the same async surface and rules as `ModerationStore`."""
    admin_ids = set(admins)
    black_ids = set(blacklisted)
    store = MagicMock()
    store.is_admin = AsyncMock(
        side_effect=lambda uid: uid == owner_id or (uid in admin_ids and uid not in black_ids)
    )
    store.is_blacklisted = AsyncMock(side_effect=lambda uid: uid != owner_id and uid in black_ids)
    return store
```

- [ ] **Step 2: Update the existing fakes and add the failing tests**

`tests/test_economy_cog.py`: add `from tests.moderation_helpers import fake_moderation` and, in `make_bot()`, after `bot.config.owner_id = 999` add `bot.moderation = fake_moderation(owner_id=999, admins={555})`. Append:

```python
@pytest.mark.asyncio
async def test_economy_admin_allows_an_adminlist_member() -> None:
    bot = make_bot()
    bot.db.admin_adjust_wallet = AsyncMock(return_value=(100, 300))
    cog = EconomyCog(bot)
    interaction = make_interaction(555)  # admin, not the owner
    target = MagicMock(spec=discord.Member)
    target.id = 222
    target.bot = False

    with patch("yokai.cogs.economy.send", new=AsyncMock()):
        await cog.economy_admin.callback(cog, interaction, user=target, amount=100)

    bot.db.admin_adjust_wallet.assert_awaited_once()
```

`tests/test_utility.py`: add the same import; in the three `/diag` tests that set `mock_bot.config.owner_id = 123456789` (lines ~349, ~371) and `bot.config.owner_id = 42` (~515) add `mock_bot.moderation = fake_moderation(owner_id=123456789)` (resp. `bot.moderation = fake_moderation(owner_id=42)`). Change the two `DiagView(owner_id=...)` calls (lines ~282, ~303) to `DiagView(admin_id=...)`. Append:

```python
@pytest.mark.asyncio
async def test_diag_command_access_denial_message_names_admins() -> None:
    mock_bot = MagicMock()
    mock_bot.config.owner_id = 123456789
    mock_bot.moderation = fake_moderation(owner_id=123456789)
    mock_bot.user.display_avatar.url = "https://example.com/avatar.png"
    cog = MetaCog(mock_bot)
    mock_interaction = MagicMock()
    mock_interaction.user.id = 999999999
    mock_interaction.response.is_done.return_value = False
    mock_interaction.response.send_message = AsyncMock()

    await cog.diag.callback(cog, mock_interaction)

    embed = mock_interaction.response.send_message.call_args[1]["embed"]
    assert "Yokai admins" in (embed.description or "")
    assert "Admins only" in (embed.title or "")
```

Also append an admin-allowed `/diag` test by copying `test_diag_command_owner_execution` (lines ~368-420), setting `mock_bot.moderation = fake_moderation(owner_id=123456789, admins={555})`, `mock_interaction.user.id = 555`, and asserting the same success outcome that test asserts for the owner, plus that the sent view's `admin_id == 555`.

- [ ] **Step 3: Run to verify failure**

Run: `python -m pytest tests/test_economy_cog.py tests/test_utility.py -v`
Expected: FAIL (cogs still read `config.owner_id`; `DiagView` has no `admin_id`).

- [ ] **Step 4: Implement**

`src/yokai/cogs/meta.py`: change the `/diag` description to `"Run comprehensive system diagnostics (Admins only)."`, the docstring to `"""Admin-only diagnostic overview and yt-dlp updater."""`, and replace the owner check block with:

```python
        # Admin authorization: the owner or a member of the adminlist
        if not await self.bot.moderation.is_admin(interaction.user.id):
            embed = EmbedFactory.error(
                message="Access denied: this command is restricted to Yokai admins.",
                title="Admins only",
                user_hint="Ask the bot owner if you need diagnostics.",
                context="Permissions",
                bot_avatar_url=avatar_url,
                include_quip=False,
            )
            await send(interaction, embed=embed, ephemeral=True)
            return
```

and construct the view with `DiagView(admin_id=interaction.user.id, bot_avatar_url=avatar_url)`.

`src/yokai/cogs/economy.py`: description `"Admin-only: grant or deduct a member's Renown."`, docstring `"""Admin-only balance correction, always ephemeral, floored at 0."""`, replace the check with `if not await self.bot.moderation.is_admin(interaction.user.id):` and the embed with `message="Access denied: this command is restricted to Yokai admins."`, `title="Admins only"`, `user_hint="Ask the bot owner if you need a balance corrected."`.

`src/yokai/ui/views.py` `DiagView`: docstring `"""Admin-only diagnostic view featuring an in-place yt-dlp package updater."""`, rename the `owner_id` parameter to `admin_id`, use `allowed_user_ids=[admin_id]` and `self.admin_id = admin_id`. Run `grep -n "owner_id" src/yokai/ui/views.py` afterwards; the result must be empty.

- [ ] **Step 5: Run to verify pass, then commit**

Run: `python -m pytest tests/test_economy_cog.py tests/test_utility.py tests/test_embeds.py -v`
Expected: PASS.

```powershell
git add src/yokai/cogs src/yokai/ui/views.py tests/moderation_helpers.py tests/test_economy_cog.py tests/test_utility.py
git commit -m "feat(moderation): let adminlist members run the owner-only commands" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Skip blacklisted users in voice earning

**Files:**
- Modify: `src/yokai/economy/voice_tracker.py` (`tick`, the per-member loop at line ~66)
- Test: `tests/test_economy_voice_tracker.py`

**Interfaces:**
- Consumes: `bot.moderation.is_blacklisted(user_id) -> bool`.
- Produces: blacklisted humans are never credited, but still count toward the 2-human threshold so the other human keeps earning.

- [ ] **Step 1: Write the failing tests**

In `tests/test_economy_voice_tracker.py` add `from tests.moderation_helpers import fake_moderation` and in `_bot_with_guild` add `bot.moderation = fake_moderation(owner_id=0)` (nobody blacklisted). Append:

```python
@pytest.mark.asyncio
async def test_tick_does_not_credit_a_blacklisted_member() -> None:
    """Review Focus 6: a blacklisted human earns nothing; the other human still does."""
    channel = _channel([_member(1), _member(2)])
    bot = _bot_with_guild([channel])
    bot.moderation = fake_moderation(owner_id=0, blacklisted={1})
    tracker = VoiceEarnTracker(bot, interval_s=300, reward=5)

    await tracker.tick()

    credited_ids = {call.args[0] for call in bot.db.credit_wallet.await_args_list}
    assert credited_ids == {2}
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_economy_voice_tracker.py -v`
Expected: the new test FAILS (member 1 is still credited).

- [ ] **Step 3: Implement** (in `tick`, first statement inside `for member in humans:`)

```python
                if await self.bot.moderation.is_blacklisted(member.id):
                    continue
```

Update the `tick` docstring with: `Blacklisted members still count toward the 2-human minimum but are never credited.`

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_economy_voice_tracker.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/yokai/economy/voice_tracker.py tests/test_economy_voice_tracker.py
git commit -m "feat(moderation): stop crediting blacklisted members for voice time" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Heartbeat writer

**Files:**
- Create: `src/yokai/heartbeat.py`
- Modify: `src/yokai/bot.py` (`__init__`, `on_ready`, `close`)
- Test: `tests/test_heartbeat.py`, `tests/test_bot_wiring.py` (append)

**Interfaces:**
- Consumes: `Database.write_heartbeat` / `clear_heartbeat` (Task 2); `yokai.__version__`.
- Produces: `HeartbeatWriter(db, version: str, interval_s: float = 30.0, pid: Optional[int] = None)` with `start()` (idempotent), `async stop()` (cancels; clears the row only if it was running), `async beat()`. `bot.heartbeat` starts in `on_ready` and stops in `close()` before the DB closes.

- [ ] **Step 1: Write the failing tests**

`tests/test_heartbeat.py`:

```python
"""Tests for the heartbeat writer the admin panel uses to show bot online/offline."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from yokai.heartbeat import HeartbeatWriter
from yokai.storage.db import Database


async def test_beat_writes_pid_and_version(tmp_path: Path) -> None:
    db = Database(tmp_path / "t.db")
    await db.initialize()
    writer = HeartbeatWriter(db, version="9.9.9", pid=4242)

    await writer.beat()

    async with db.conn.execute("SELECT pid, version FROM bot_heartbeat;") as cursor:
        assert await cursor.fetchall() == [(4242, "9.9.9")]
    await db.close()


async def test_stop_clears_the_row_when_it_was_running(tmp_path: Path) -> None:
    db = Database(tmp_path / "t.db")
    await db.initialize()
    writer = HeartbeatWriter(db, version="1", interval_s=60, pid=1)
    writer.start()
    for _ in range(100):  # let the first beat land
        async with db.conn.execute("SELECT COUNT(*) FROM bot_heartbeat;") as cursor:
            if (await cursor.fetchone())[0] == 1:  # type: ignore[index]
                break
        await asyncio.sleep(0.01)

    await writer.stop()

    async with db.conn.execute("SELECT COUNT(*) FROM bot_heartbeat;") as cursor:
        assert (await cursor.fetchone())[0] == 0  # type: ignore[index]
    await db.close()


async def test_stop_without_start_touches_nothing() -> None:
    db = MagicMock()
    db.clear_heartbeat = AsyncMock()
    writer = HeartbeatWriter(db, version="1")
    await writer.stop()
    db.clear_heartbeat.assert_not_awaited()


async def test_start_is_idempotent() -> None:
    db = MagicMock()
    db.write_heartbeat = AsyncMock()
    db.clear_heartbeat = AsyncMock()
    writer = HeartbeatWriter(db, version="1", interval_s=60)
    writer.start()
    first = writer._task
    writer.start()
    assert writer._task is first
    await writer.stop()


async def test_a_failed_beat_does_not_kill_the_loop() -> None:
    db = MagicMock()
    db.write_heartbeat = AsyncMock(side_effect=[sqlite3.OperationalError("locked"), None, None])
    db.clear_heartbeat = AsyncMock()
    writer = HeartbeatWriter(db, version="1", interval_s=0.01)
    writer.start()
    for _ in range(200):
        if db.write_heartbeat.await_count >= 2:
            break
        await asyncio.sleep(0.01)
    await writer.stop()
    assert db.write_heartbeat.await_count >= 2
```

Append to `tests/test_bot_wiring.py`:

```python
@pytest.mark.asyncio
async def test_on_ready_starts_the_heartbeat_and_close_stops_it() -> None:
    bot = make_bot()
    bot.db.close = AsyncMock()
    bot.spotify.close = AsyncMock()
    with patch.object(bot.heartbeat, "start") as start_mock:
        await bot.on_ready()
    start_mock.assert_called_once()

    with (
        patch.object(bot.heartbeat, "stop", new=AsyncMock()) as stop_mock,
        patch.object(bot.voice_earn_tracker, "stop", new=AsyncMock()),
    ):
        await bot.close()
    stop_mock.assert_awaited_once()
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_heartbeat.py tests/test_bot_wiring.py -v`
Expected: FAIL (`ModuleNotFoundError: yokai.heartbeat`).

- [ ] **Step 3: Implement**

Create `src/yokai/heartbeat.py`:

```python
"""Liveness heartbeat: one row the admin panel reads to show whether the bot is online."""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from yokai.storage.db import Database

logger = logging.getLogger(__name__)


class HeartbeatWriter:
    """Upserts `bot_heartbeat` every `interval_s` seconds while the bot is connected."""

    def __init__(
        self,
        db: Database,
        version: str,
        interval_s: float = 30.0,
        pid: Optional[int] = None,
    ) -> None:
        self._db = db
        self._version = version
        self.interval_s = interval_s
        self._pid = os.getpid() if pid is None else pid
        self._task: Optional[asyncio.Task[None]] = None

    def start(self) -> None:
        """Start the loop, if not already running (on_ready can fire again after a reconnect)."""
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """Cancel the loop and clear the row so the panel shows offline right away."""
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        try:
            await self._db.clear_heartbeat()
        except (sqlite3.Error, RuntimeError) as exc:
            logger.warning("Could not clear the heartbeat on shutdown: %s", exc)

    async def beat(self) -> None:
        """Write one heartbeat now."""
        await self._db.write_heartbeat(self._pid, self._version)

    async def _run(self) -> None:
        while True:
            try:
                await self.beat()
            except (sqlite3.Error, RuntimeError) as exc:
                logger.warning("Heartbeat write failed: %s", exc)
            await asyncio.sleep(self.interval_s)
```

In `src/yokai/bot.py`: add `from yokai import __version__` and `from yokai.heartbeat import HeartbeatWriter` to the imports; after `self.moderation = ...` add `self.heartbeat = HeartbeatWriter(self.db, __version__)`; at the end of `on_ready` add `self.heartbeat.start()`; in `close()` add `await self.heartbeat.stop()` immediately after `await self.voice_earn_tracker.stop()` (before the Spotify/DB closes).

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_heartbeat.py tests/test_bot_wiring.py -v`
Expected: PASS. If `from yokai import __version__` is flagged circular, use `import yokai` and `yokai.__version__` instead.

- [ ] **Step 5: Commit**

```powershell
git add src/yokai/heartbeat.py src/yokai/bot.py tests/test_heartbeat.py tests/test_bot_wiring.py
git commit -m "feat(moderation): add a 30s heartbeat so the panel can show bot status" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Docs and full verification

**Files:**
- Modify: `TASKS.md` (insert before the `## Later` section)

- [ ] **Step 1: Add the phase entry to `TASKS.md`**

```markdown
## Phase 9 — Admin panel

**Goal:** A local, modular web admin panel for the owner, plus the moderation primitives it
manages. Spec: `docs/superpowers/specs/2026-10-05-admin-panel-design.md`. Phase P-A1 plan:
`docs/superpowers/plans/2026-10-05-admin-panel-pa1-moderation.md`. P-A2 to P-A6 each get their
own plan after the previous phase is reviewed.

- [ ] P-A1 Bot-side moderation: migration v4, `ModerationStore`, `YokaiTree` and view gates,
      admin widening, voice-earning skip, heartbeat.
- [ ] P-A2 Panel core and Dashboard (FastAPI + Jinja2 + HTMX, auth/CSRF, module contract).
- [ ] P-A3 Access (blacklist/adminlist) and Audit modules.
- [ ] P-A4 Users module (profiles, ledger-safe balance edits).
- [ ] P-A5 Database module (browse, integrity, VACUUM, backup/restore).
- [ ] P-A6 Logs module (current and rotated, live tail).

**Acceptance criteria (P-A1)**
- Schema version is 4; a v3 database upgrades with its rows intact.
- A blacklisted user gets an ephemeral "Access blocked" embed on every slash command and view
  button; autocomplete is denied silently; the owner can never be blocked.
- `/diag` and `/economy-admin` work for the owner and for adminlist members, and refuse others
  with an "Admins only" embed; a user on both lists is not an admin.
- A change to either table takes effect within about 5 seconds, with no restart.
- A moderation read failure never breaks commands (last good copy, else fail open).
- Blacklisted members earn no voice Renown; the other human in the channel still does.
- `bot_heartbeat` is refreshed every 30 s while connected and cleared on a clean shutdown.
- `pytest`, `ruff check` and `ruff format --check` are clean.
- *(manual, real Discord)* Insert a row into `blacklist` for a test account (for example with
  `python -c "import sqlite3,time; c=sqlite3.connect('data/yokai.db'); c.execute(\"INSERT INTO blacklist VALUES (<id>,'test',?, 'manual')\",(int(time.time()),)); c.commit()"`),
  wait 5 seconds, run `/ping` and press a button as that account: both are refused. Delete the
  row and confirm access returns. Add an account to `admins` and confirm `/diag` works for it.
  Watch `bot_heartbeat.ts` advance every 30 s, and vanish after stopping the bot with Ctrl+C.

**STOP — owner review.**

---
```

- [ ] **Step 2: Run the full verification**

Run each from the repo root and confirm clean output:

```powershell
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
```

Expected: all tests pass (live tests stay skipped), ruff reports no issues. Fix anything that fails before committing; if `ruff format --check` lists files, run `python -m ruff format <those files>`, then re-run all three.

- [ ] **Step 3: Commit**

```powershell
git add TASKS.md
git commit -m "docs(admin): record admin panel phase P-A1 in TASKS.md" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 4: Stop for owner review** (AGENTS.md working agreement). Summarize what changed and list what is unverified: the manual real-Discord checks above.

---

## Self-review

**Spec coverage (§5, §6, §11 P-A1):** migration v4 with all four tables → Task 1. Store with ~5 s TTL, last-good-copy fallback, owner rules → Task 3. `YokaiTree.interaction_check` with ephemeral embed → Task 4 and 5. View gates (`ui/views.py`, `ui/game_views.py`) → Task 6; `game_views.py` classes subclass `BaseView`, so the one change covers them. Admin widening at `meta.py` and `economy.py` → Task 7. Voice-tracker skip → Task 8. Heartbeat (30 s, start on ready, stop on close, offline after 90 s is the panel's rule, P-A2) → Task 9. Docs → Task 10. Panel-only list management: nothing added on the bot side, as specified.

**Placeholders:** none. The two spots that say "copy the existing test" in Task 7 name the exact source test and the exact edits.

**Type consistency:** `ModerationStore(db, owner_id, ttl_s, clock)`, `is_blacklisted`/`is_admin`, `deny_if_blacklisted`, `YokaiTree`, `HeartbeatWriter(db, version, interval_s, pid)`, `DiagView(admin_id=...)`, `fake_moderation(owner_id, admins, blacklisted)` are spelled the same in every task that uses them.

**Review Focus coverage:** items 1 to 6 map to tests in Tasks 1, 3, 4, 3, 3/6 and 8.

**Known risks for the implementer:** a possible circular import between `yokai.moderation` and `yokai.ui.embeds` (fallback noted in Task 4); `tests` imports (`from tests.moderation_helpers import ...`) rely on `tests/__init__.py`, which exists.
