# Admin Panel P-A3: Access and Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the owner manage the blacklist and the admin list from the panel, with every change recorded in an audit log, and show people by name instead of bare Discord IDs.

**Architecture:** The panel gets its first write path: a `BEGIN IMMEDIATE` transaction helper that applies a change and its audit row atomically. The bot gains a small name cache (migration v5, table `user_names`, written at most once a day per person) so the panel can show "Sami (1234...)". Two new modules, **Access** and **Audit**, plug into the P-A2 module contract and contribute Dashboard tiles. Forms use HTMX with the existing CSRF header; validation problems come back as normal 200 fragments so HTMX swaps them in.

**Tech Stack:** Python 3.12+, FastAPI + Starlette 1.x, Jinja2, HTMX 2.0.11 (vendored), `python-multipart` (new, for forms), stdlib `sqlite3`, aiosqlite on the bot side, pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-10-05-admin-panel-design.md` (§5 data model, §7 Access and Audit, §8 security, §11 phase P-A3). P-A1 (moderation, schema v4) and P-A2 (panel core, Dashboard) are merged.

## Owner decisions this plan implements

1. **Names:** a bot-side name cache (migration v5). Spec delta: the spec said "IDs only"; the owner chose names now so Access, Audit and the later Users module are readable.
2. **No `OWNER_ID`:** the panel still starts; the Access page shows a note and no forms, and the server refuses edits too.
3. **Safety:** adding to the blacklist needs a reason (3-200 characters); admin adds take an optional note; removing asks for confirmation; every change is audited.

## Deltas and carried requirements

- `python-multipart` is added now (the dependency the spec scheduled for this phase). While touching `pyproject.toml` the plan also adds lower bounds to `fastapi`, `uvicorn` and `jinja2` (a P-A2 deferred minor).
- Two deferred P-A2 minors are folded in because this phase touches the same code: the `main()` tests no longer leak the owner's real `.env` into the pytest process, and the panel's `MIN_SCHEMA_VERSION` rises to 5.
- **Cross-process write rule (P-A1 review, binding here):** panel writes use `BEGIN IMMEDIATE`, read and check inside the same transaction, use relative or conditional statements, and report a busy database as a friendly message.
- Admin-list notes live only in the audit log (the `admins` table has no note column in the approved schema).
- Blacklisting an admin keeps the admin row (the bot already lets the blacklist win) and the page flags it. Adding a blacklisted user as admin is refused.
- Starting the panel against a schema-4 database now says "needs v5, start the bot once to upgrade it".

## Global Constraints

- Python 3.12+; every module starts with `from __future__ import annotations`; fully type-hinted; dataclasses for models.
- Use `logging`; no `print`; no bare `except`. Catch specific errors; the only broad `except Exception` stays the module-import isolation boundary from P-A2.
- All SQL is parameterized. Placeholder lists built with `?` repeated are allowed; no user text goes into SQL strings.
- Every panel write goes through `write_transaction` and records an audit row in the same transaction. The panel never writes outside `blacklist`, `admins`, `admin_audit` in this phase.
- The panel stays loopback-only and offline; all assets are vendored. No new inline scripts or styles (CSP).
- Panel copy is plain, sentence case, no emoji; errors say what happened and what to do. Hostile text (names, reasons, details) is always escaped by Jinja autoescape.
- Bot-side: never let name recording block or fail a command; log and move on.
- Never commit `.env`, tokens, databases or cookie files. Commit per task.
- New dependency approved by the spec: `python-multipart`. Anything else needs owner approval.
- Verification (PowerShell, repo root): `python -m pytest -q`, `python -m ruff check .`, `python -m ruff format --check .`.
- Git commits end with: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`

## Verified facts this plan relies on

- The P-A2 code: `AdminModule(id, title, router, nav_order, templates_dir, tile)`, `render(request, name, *, active, **ctx)`, `render_fragment(request, name, **ctx)`, `panel_client(...)` in `tests/admin_helpers.py`, `SecurityMiddleware` (CSRF header `X-CSRF-Token` required on unsafe methods, set globally by `hx-headers` in `base.html`), `panel.js` marks only `#status` stale on HTMX errors, `readonly(db_path)` and `require_schema` in `yokai_admin/core/db.py`.
- htmx 2.0.11 does not swap 4xx/5xx responses by default, so every user-facing validation problem in this phase returns **HTTP 200** with an error flash in the fragment.
- The bot reads the lists through `ModerationStore` (5 s TTL), so a panel change reaches it within seconds; `Database.get_moderation_lists()` is the read.
- `yokai.moderation.gate.deny_if_blacklisted` is called from `YokaiTree.interaction_check` and from `BaseView.interaction_check` / `RecommendView.interaction_check` (lazy imports in `ui/views.py` lines ~43 and ~255).
- Python's `int()` accepts non-ASCII digits (for example Arabic-Indic digits), so Discord IDs are validated with `isascii()` and `isdigit()` first.
- Tests that hard-code schema version 4 and must change with migration v5: `tests/test_moderation_storage.py` (lines ~33, ~43, ~71), `tests/test_admin_db.py` (~65), `tests/test_admin_dashboard.py` (~59, "needs v4"), `tests/test_admin_dashboard_service.py` (~63), and the docstring in `tests/admin_helpers.py`.

## Design notes (extends the P-A2 design system; no new fonts or colors)

Access and Audit use the same ledger language: a page title in the condensed face, then plain tables (real tabular data), thin rules, one primary action color (sonar). New pieces are forms, buttons, a flash line and an inline confirm row. Status is never color alone: flash lines start with hidden text ("Done:" or "Problem:"), the destructive button says what it does. Buttons use `var(--hull)` text on `--sonar` / `--flare` (measured: at least 5.2:1 in both themes). Forms are labelled in sentence case above each field; a taken-over mobile layout stacks fields.

## Review Focus

Failure modes the spec implies but happy-path tests would miss, most likely first. Each has a test in the task named.

1. The owner's ID, however written (leading zeros, spaces, Arabic-Indic digits, `+`, hex, negative, huge) is never accepted into either list and nothing is written (Task 6).
2. A change and its audit row either both commit or neither does, and two simultaneous adds of the same person produce exactly one row and one audit entry (Task 6).
3. Hostile text in a Discord display name, a reason, or an audit detail is escaped on screen, and control or bidirectional-override characters are stripped before storage (Tasks 2, 6, 7, 8).
4. With `OWNER_ID` missing the server refuses edits (not just hides the forms); with the database busy or old, the page answers with a friendly message and never a 500 (Tasks 3 and 7).
5. The bot actually sees the panel's change (a `ModerationStore` against the same file), and name recording never blocks or fails a Discord interaction, even when its database write fails (Tasks 2 and 6).
6. Every write endpoint is locked without a session and refuses a missing CSRF token (Task 7).

---

## File structure

| File | Responsibility |
|---|---|
| `src/yokai/storage/db.py` (modify) | Migration v5 (`user_names`), `upsert_user_name` |
| `src/yokai/moderation/names.py` (create) | `clean_name`, `UserNameCache` (throttled writes) |
| `src/yokai/moderation/gate.py`, `__init__.py` (modify) | `screen()` = blacklist gate + name recording |
| `src/yokai/bot.py`, `src/yokai/ui/views.py` (modify) | Own the cache; call `screen` |
| `src/yokai_admin/core/settings.py` (modify) | `owner_id` |
| `src/yokai_admin/core/db.py` (modify) | `MIN_SCHEMA_VERSION = 5`, `write_transaction` |
| `src/yokai_admin/core/audit.py` (create) | `record`, `list_entries`, `latest_ts`, `modules_seen` |
| `src/yokai_admin/core/people.py` (create) | `UserRef`, `user_refs` |
| `src/yokai_admin/core/formatting.py` (create) | `describe_age` (moved), `format_when` |
| `src/yokai_admin/core/templating.py` (modify) | `when` filter |
| `src/yokai_admin/modules/access/` (create) | `service.py`, `router.py`, `__init__.py`, templates |
| `src/yokai_admin/modules/audit/` (create) | `service.py`, `router.py`, `__init__.py`, template |
| `src/yokai_admin/modules/__init__.py` (modify) | Register both modules |
| `src/yokai_admin/static/panel.css` (modify) | Forms, tables, buttons, flash |
| `tests/test_user_names.py`, `tests/test_admin_*.py` (create/modify) | Tests |
| `pyproject.toml`, `SPEC.md`, `README.md`, `TASKS.md`, spec (modify) | Docs |

---

### Task 1: Migration v5 and `upsert_user_name` (bot side)

**Files:**
- Modify: `src/yokai/storage/db.py`, `tests/test_moderation_storage.py`, `tests/test_admin_db.py`, `tests/test_admin_dashboard.py`, `tests/test_admin_dashboard_service.py`, `tests/admin_helpers.py`
- Create: `tests/test_user_names_storage.py`

**Interfaces:**
- Produces: `CURRENT_SCHEMA_VERSION == 5`; table `user_names(user_id INTEGER PRIMARY KEY, display_name TEXT NOT NULL, updated_ts INTEGER NOT NULL)`; `async Database.upsert_user_name(user_id: int, display_name: str, now: Optional[int] = None) -> None` (insert or update, under `_write_lock`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_user_names_storage.py`:

```python
"""Tests for migration v5 and the user-name cache table."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import AsyncIterator

import pytest

from yokai.storage.db import CURRENT_SCHEMA_VERSION, Database


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    """An open database that is always closed, even when the test fails."""
    database = Database(tmp_path / "t.db")
    await database.initialize()
    try:
        yield database
    finally:
        await database.close()


async def _names(db: Database) -> list[tuple[int, str, int]]:
    async with db.conn.execute("SELECT user_id, display_name, updated_ts FROM user_names;") as c:
        return [tuple(row) for row in await c.fetchall()]  # type: ignore[misc]


async def test_schema_is_at_v5_with_the_user_names_table(db: Database) -> None:
    assert CURRENT_SCHEMA_VERSION == 5
    async with db.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table';") as c:
        assert "user_names" in {row[0] for row in await c.fetchall()}


async def test_upsert_inserts_then_updates_one_row(db: Database) -> None:
    await db.upsert_user_name(7, "Sami", now=100)
    await db.upsert_user_name(7, "Sami (renamed)", now=200)
    assert await _names(db) == [(7, "Sami (renamed)", 200)]


async def test_a_v4_database_upgrades_to_v5_keeping_its_rows(db: Database, tmp_path: Path) -> None:
    await db.write_heartbeat(pid=1, version="x", now=5)
    await db.conn.execute(
        "INSERT INTO blacklist (user_id, reason, added_ts, added_by) VALUES (9, 'spam', 1, 'x');"
    )
    await db.conn.commit()
    await db.close()

    raw = sqlite3.connect(tmp_path / "t.db")
    try:
        raw.execute("DROP TABLE user_names;")
        raw.execute("PRAGMA user_version = 4;")
        raw.commit()
    finally:
        raw.close()

    upgraded = Database(tmp_path / "t.db")
    await upgraded.initialize()
    try:
        async with upgraded.conn.execute("PRAGMA user_version;") as c:
            row = await c.fetchone()
        assert row is not None and row[0] == 5
        assert await upgraded.get_moderation_lists() == (frozenset({9}), frozenset())
        await upgraded.upsert_user_name(1, "ok", now=1)  # the table exists again
    finally:
        await upgraded.close()
```

Update the tests that hard-code version 4:
- `tests/test_moderation_storage.py`: replace `assert CURRENT_SCHEMA_VERSION == 4` with `assert CURRENT_SCHEMA_VERSION >= 4`, and both `row[0] == 4` with `row[0] == CURRENT_SCHEMA_VERSION`.
- `tests/test_admin_db.py`: change `match=r"v3.*v4"` to `match=r"v3.*v5"`.
- `tests/test_admin_dashboard.py`: change `"needs v4"` to `"needs v5"`.
- `tests/test_admin_dashboard_service.py`: import `CURRENT_SCHEMA_VERSION` from `yokai.storage.db` and change `(3, 2, 4)` to `(3, 2, CURRENT_SCHEMA_VERSION)`.
- `tests/admin_helpers.py`: change the `seed_database` docstring to say "current-schema database".

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_user_names_storage.py -q`
Expected: FAIL (schema is v4, no `user_names`, no `upsert_user_name`).

- [ ] **Step 3: Implement**

In `src/yokai/storage/db.py`: set `CURRENT_SCHEMA_VERSION = 5`; after `MIGRATION_V4` add

```python
MIGRATION_V5 = """
CREATE TABLE IF NOT EXISTS user_names (
  user_id INTEGER PRIMARY KEY,
  display_name TEXT NOT NULL,
  updated_ts INTEGER NOT NULL
);
"""
```

at the end of `_migrate` (after the `version < 4` block) add

```python
        if version < 5:
            logger.info("Applying SQLite migration v5...")
            await self.conn.executescript(MIGRATION_V5)
            await self.conn.execute("PRAGMA user_version = 5;")
            await self.conn.commit()
            logger.info("SQLite schema migrated to version 5.")
```

and add this method next to `write_heartbeat`:

```python
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
```

The panel's `MIN_SCHEMA_VERSION` changes in Task 3, so for now `tests/test_admin_*.py` that expect "needs v5" will fail until then; do not run the whole suite in this task, only the files named in Step 4.

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_user_names_storage.py tests/test_moderation_storage.py tests/test_storage.py tests/test_gambling_storage.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/yokai/storage/db.py tests
git commit -m "feat(admin): add migration v5 for the user-name cache" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Name cache and the `screen` gate (bot side)

**Files:**
- Create: `src/yokai/moderation/names.py`
- Modify: `src/yokai/moderation/gate.py`, `src/yokai/moderation/__init__.py`, `src/yokai/bot.py`, `src/yokai/ui/views.py`
- Test: `tests/test_user_names.py`, `tests/test_moderation_gate.py` (append), `tests/test_bot_wiring.py` (append)

**Interfaces:**
- Consumes: `Database.upsert_user_name` (Task 1); `deny_if_blacklisted` (P-A1).
- Produces:
  - `names.clean_name(raw: object) -> str`: non-strings give `""`; control, zero-width and bidirectional-override characters are removed; whitespace collapsed; at most 80 characters.
  - `names.UserNameCache(db, ttl_s: float = 86_400.0, retry_s: float = 60.0, clock=time.monotonic)` with `async touch(user_id: int, raw_name: object) -> None`: writes when the person is new, their cleaned name changed, or `ttl_s` has passed; a failed write is logged and retried after `retry_s`; it never raises for database errors (`sqlite3.Error`, `RuntimeError`, `ValueError`).
  - `gate.screen(interaction) -> bool`: `True` means stop. Denies a blacklisted user (as before) and otherwise records the user's display name through `interaction.client.names` when that is a real `UserNameCache`.
  - `bot.names: UserNameCache`; `YokaiTree.interaction_check` and both view checks call `screen`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_user_names.py`:

```python
"""Tests for the throttled, sanitising user-name cache."""

from __future__ import annotations

import sqlite3
from unittest.mock import AsyncMock, MagicMock

import pytest

from yokai.moderation.names import MAX_NAME_CHARS, UserNameCache, clean_name


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def make_cache(**kwargs: float) -> tuple[UserNameCache, MagicMock, Clock]:
    db = MagicMock()
    db.upsert_user_name = AsyncMock()
    clock = Clock()
    return UserNameCache(db, clock=clock, **kwargs), db, clock


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Sami", "Sami"),
        ("  Sami \n\t Syverson  ", "Sami Syverson"),
        ("evil‮gnp.exe", "evilgnp.exe"),  # right-to-left override removed
        ("zero​width", "zerowidth"),
        ("bell\x07char", "bellchar"),
        ("", ""),
        (None, ""),
        (12345, ""),
        ("x" * 500, "x" * MAX_NAME_CHARS),
    ],
)
def test_clean_name(raw: object, expected: str) -> None:
    assert clean_name(raw) == expected


async def test_the_first_sighting_is_written_once() -> None:
    cache, db, _ = make_cache()
    await cache.touch(7, "Sami")
    await cache.touch(7, "Sami")
    db.upsert_user_name.assert_awaited_once_with(7, "Sami")


async def test_a_changed_name_is_written_again() -> None:
    cache, db, _ = make_cache()
    await cache.touch(7, "Sami")
    await cache.touch(7, "Sami the Brave")
    assert db.upsert_user_name.await_count == 2


async def test_the_same_name_is_refreshed_after_the_ttl() -> None:
    cache, db, clock = make_cache(ttl_s=100)
    await cache.touch(7, "Sami")
    clock.now += 101
    await cache.touch(7, "Sami")
    assert db.upsert_user_name.await_count == 2


async def test_an_empty_name_is_never_written() -> None:
    cache, db, _ = make_cache()
    await cache.touch(7, "‮​")
    db.upsert_user_name.assert_not_awaited()


@pytest.mark.parametrize("error", [sqlite3.OperationalError("locked"), RuntimeError("x"), ValueError("closed")])
async def test_a_failed_write_never_raises_and_is_retried_soon(error: Exception) -> None:
    """Review Focus 5: recording a name must never break a Discord interaction."""
    cache, db, clock = make_cache(retry_s=60)
    db.upsert_user_name.side_effect = error
    await cache.touch(7, "Sami")  # must not raise
    await cache.touch(7, "Sami")  # inside the retry window: no second attempt
    assert db.upsert_user_name.await_count == 1

    clock.now += 61
    db.upsert_user_name.side_effect = None
    await cache.touch(7, "Sami")
    assert db.upsert_user_name.await_count == 2
```

Append to `tests/test_moderation_gate.py` (add `from yokai.moderation.gate import screen` and `from yokai.moderation.names import UserNameCache` to the imports at the top):

```python
def _named_interaction(user_id: int, name: str, store: object, names: object) -> MagicMock:
    interaction = _interaction(user_id, store)
    interaction.user = MagicMock(id=user_id, display_name=name)
    interaction.client.names = names
    return interaction


async def test_screen_records_the_name_of_an_allowed_user() -> None:
    db = MagicMock()
    db.upsert_user_name = AsyncMock()
    interaction = _named_interaction(10, "Sami", _store(frozenset({5})), UserNameCache(db))
    assert await screen(interaction) is False
    db.upsert_user_name.assert_awaited_once_with(10, "Sami")


async def test_screen_does_not_record_or_allow_a_blacklisted_user() -> None:
    db = MagicMock()
    db.upsert_user_name = AsyncMock()
    interaction = _named_interaction(5, "Mallory", _store(frozenset({5})), UserNameCache(db))
    with patch("yokai.moderation.gate.send", new=AsyncMock()):
        assert await screen(interaction) is True
    db.upsert_user_name.assert_not_awaited()


async def test_screen_works_when_the_client_has_no_name_cache() -> None:
    interaction = _named_interaction(10, "Sami", _store(frozenset()), MagicMock())
    assert await screen(interaction) is False


async def test_screen_survives_a_database_error_while_recording() -> None:
    db = MagicMock()
    db.upsert_user_name = AsyncMock(side_effect=ValueError("closed"))
    interaction = _named_interaction(10, "Sami", _store(frozenset()), UserNameCache(db))
    assert await screen(interaction) is False  # the command still runs
```

Append to `tests/test_bot_wiring.py`:

```python
@pytest.mark.asyncio
async def test_the_bot_owns_a_name_cache_and_the_tree_feeds_it() -> None:
    from yokai.moderation import UserNameCache

    bot = make_bot()
    assert isinstance(bot.names, UserNameCache)
    bot.db.get_moderation_lists = AsyncMock(return_value=(frozenset(), frozenset()))
    bot.db.upsert_user_name = AsyncMock()
    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock(id=7, display_name="Sami")
    interaction.type = discord.InteractionType.application_command
    interaction.client = bot
    assert await bot.tree.interaction_check(interaction) is True
    bot.db.upsert_user_name.assert_awaited_once_with(7, "Sami")
```

Also add one test to `tests/test_moderation_gate.py` proving a live view records names: `BaseView(allowed_user_ids=None).interaction_check(_named_interaction(10, "Sami", _store(frozenset()), UserNameCache(db)))` returns `True` and awaits `upsert_user_name(10, "Sami")` (reuse the `db` mock pattern above).

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_user_names.py tests/test_moderation_gate.py tests/test_bot_wiring.py -q`
Expected: FAIL (`ModuleNotFoundError: yokai.moderation.names`; `screen` missing).

- [ ] **Step 3: Implement**

Create `src/yokai/moderation/names.py`:

```python
"""Remembers each user's latest display name so the admin panel can show people by name."""

from __future__ import annotations

import logging
import re
import sqlite3
import time
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from yokai.storage.db import Database

logger = logging.getLogger(__name__)

MAX_NAME_CHARS = 80
# Control characters, zero-width characters and the bidirectional overrides/isolates that can make
# text on the panel display in a different order than it was typed.
_UNSAFE = re.compile(r"[\x00-\x1f\x7f-\x9f​-‏ -‮⁦-⁩]")


def clean_name(raw: object) -> str:
    """Return a display name that is safe to store and show; empty if nothing usable is left."""
    if not isinstance(raw, str):
        return ""
    text = " ".join(_UNSAFE.sub("", raw).split())
    return text[:MAX_NAME_CHARS]


class UserNameCache:
    """Writes a user's name to the database when it is new, changed, or a day old."""

    def __init__(
        self,
        db: Database,
        ttl_s: float = 86_400.0,
        retry_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._db = db
        self.ttl_s = ttl_s
        self.retry_s = retry_s
        self._clock = clock
        # (name, time of last successful write). About ten people use the bot, so this stays tiny.
        self._seen: dict[int, tuple[str, float]] = {}

    async def touch(self, user_id: int, raw_name: object) -> None:
        """Record the name if it needs recording. Never raises for database problems."""
        name = clean_name(raw_name)
        if not name:
            return
        now = self._clock()
        last = self._seen.get(user_id)
        if last is not None and last[0] == name and now - last[1] < self.ttl_s:
            return
        try:
            await self._db.upsert_user_name(user_id, name)
        except (sqlite3.Error, RuntimeError, ValueError) as exc:
            # ValueError: aiosqlite raises it once its connection is closed.
            logger.warning("Could not remember the name of user %d: %s", user_id, exc)
            # Pretend the write happened `ttl_s - retry_s` ago, so the next attempt is `retry_s` away.
            self._seen[user_id] = (name, now - self.ttl_s + self.retry_s)
            return
        self._seen[user_id] = (name, now)
```

In `src/yokai/moderation/gate.py` add `from yokai.moderation.names import UserNameCache` to the imports, define `screen` after `deny_if_blacklisted`, and change `YokaiTree.interaction_check` to use it:

```python
async def screen(interaction: discord.Interaction) -> bool:
    """Run everything an interaction must pass; True means stop (the user is blacklisted).

    Also remembers the user's current display name for the admin panel.
    """
    if await deny_if_blacklisted(interaction):
        return True
    names = getattr(interaction.client, "names", None)
    if isinstance(names, UserNameCache):
        user = interaction.user
        await names.touch(user.id, getattr(user, "display_name", None) or getattr(user, "name", ""))
    return False
```

```python
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return not await screen(interaction)
```

`src/yokai/moderation/__init__.py`:

```python
"""Blacklist, adminlist, user-name cache and the gates that enforce them."""

from __future__ import annotations

from yokai.moderation.gate import YokaiTree, deny_if_blacklisted, screen
from yokai.moderation.names import UserNameCache
from yokai.moderation.store import ModerationStore

__all__ = ["ModerationStore", "UserNameCache", "YokaiTree", "deny_if_blacklisted", "screen"]
```

`src/yokai/bot.py`: change the import to `from yokai.moderation import ModerationStore, UserNameCache, YokaiTree` and add `self.names = UserNameCache(self.db)` after `self.moderation = ...`.

`src/yokai/ui/views.py`: in both places, change the lazy import to `from yokai.moderation.gate import screen` and the check to `if await screen(interaction):`.

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_user_names.py tests/test_moderation_gate.py tests/test_bot_wiring.py tests/test_recommend.py tests/test_economy_views.py tests/test_gambling_views.py tests/test_blackjack_view.py tests/test_utility.py -q`
Expected: PASS (existing view tests use mock clients without a real name cache).

- [ ] **Step 5: Commit**

```powershell
git add src/yokai tests
git commit -m "feat(admin): remember user display names for the panel" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Panel write path, owner setting, schema 5 and dependencies

**Files:**
- Modify: `src/yokai_admin/core/settings.py`, `src/yokai_admin/core/db.py`, `pyproject.toml`, `tests/test_admin_settings.py`, `tests/test_admin_db.py`, `tests/test_admin_launch.py`, `tests/admin_helpers.py`
- Test: `tests/test_admin_settings.py`, `tests/test_admin_db.py`

**Interfaces:**
- Produces:
  - `PanelSettings.owner_id: Optional[int] = None` (last field); `load_settings` reads `OWNER_ID` (missing or empty gives `None`; non-numeric or not positive raises `PanelConfigError` naming `OWNER_ID`).
  - `core/db.py`: `MIN_SCHEMA_VERSION = 5`; `WRITE_BUSY_TIMEOUT_S = 5.0`; `open_readwrite(db_path, busy_timeout_s) -> sqlite3.Connection` (`mode=rw`, autocommit so transactions are explicit); `write_transaction(db_path, *, busy_timeout_s: Optional[float] = None) -> ContextManager[sqlite3.Connection]`: opens read-write, `BEGIN IMMEDIATE`, checks `require_schema`, yields, commits on success and rolls back on any exception, always closes. A busy database raises `PanelDbError("The database is busy right now. Try again in a moment.")`; a missing file, old schema or non-SQLite file raise `PanelDbError` as in the read path.
  - Test helpers: `panel_client(..., owner_id: Optional[int] = 111)`; `seed_database(..., admins=(), blacklist=(), names=None)` where `blacklist` is a sequence of `(user_id, reason)`, `admins` a sequence of ids and `names` a mapping id to display name.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_admin_settings.py`:

```python
def test_owner_id_is_optional_and_parsed() -> None:
    with patch.dict(os.environ, {}, clear=True):
        assert load_settings(env_path=NO_ENV_FILE).owner_id is None
    with patch.dict(os.environ, {"OWNER_ID": "  1122334455 "}, clear=True):
        assert load_settings(env_path=NO_ENV_FILE).owner_id == 1122334455
    with patch.dict(os.environ, {"OWNER_ID": ""}, clear=True):
        assert load_settings(env_path=NO_ENV_FILE).owner_id is None


@pytest.mark.parametrize("value", ["abc", "0", "-5", "12.5"])
def test_a_bad_owner_id_is_rejected_with_a_clear_message(value: str) -> None:
    with patch.dict(os.environ, {"OWNER_ID": value}, clear=True):
        with pytest.raises(PanelConfigError, match="OWNER_ID"):
            load_settings(env_path=NO_ENV_FILE)
```

Append to `tests/test_admin_db.py` (add `import threading` is not needed; add `from yokai_admin.core import db as panel_db` and `from yokai_admin.core.db import write_transaction` to the imports, plus `from yokai.moderation.store import ModerationStore`):

```python
async def test_a_write_transaction_commits_and_the_bot_sees_it(db: Database, tmp_path: Path) -> None:
    """Review Focus 5: the bot's own connection reads what the panel wrote."""
    with write_transaction(tmp_path / "t.db") as conn:
        conn.execute(
            "INSERT INTO blacklist (user_id, reason, added_ts, added_by) VALUES (5, 'spam', 1, 'panel');"
        )
    assert await db.get_moderation_lists() == (frozenset({5}), frozenset())
    store = ModerationStore(db, owner_id=1, ttl_s=0)
    assert await store.is_blacklisted(5) is True


async def test_a_failing_write_transaction_rolls_everything_back(db: Database, tmp_path: Path) -> None:
    with pytest.raises(RuntimeError):
        with write_transaction(tmp_path / "t.db") as conn:
            conn.execute(
                "INSERT INTO blacklist (user_id, reason, added_ts, added_by) VALUES (5, 'x', 1, 'p');"
            )
            raise RuntimeError("boom")
    assert await db.get_moderation_lists() == (frozenset(), frozenset())


async def test_a_busy_database_gives_a_friendly_error(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(panel_db, "WRITE_BUSY_TIMEOUT_S", 0.2)
    holder = sqlite3.connect(tmp_path / "t.db", isolation_level=None)
    holder.execute("BEGIN IMMEDIATE;")
    try:
        with pytest.raises(PanelDbError, match="busy"):
            with write_transaction(tmp_path / "t.db"):
                pass
    finally:
        holder.execute("ROLLBACK;")
        holder.close()


def test_writing_to_an_old_schema_is_refused_and_changes_nothing(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    raw.execute("PRAGMA user_version = 4;")
    raw.execute("CREATE TABLE blacklist (user_id INTEGER PRIMARY KEY, reason TEXT, added_ts INTEGER, added_by TEXT);")
    raw.commit()
    raw.close()
    with pytest.raises(PanelDbError, match="v4.*v5"):
        with write_transaction(path) as conn:
            conn.execute("INSERT INTO blacklist VALUES (1, 'x', 1, 'p');")
    check = sqlite3.connect(path)
    assert check.execute("SELECT COUNT(*) FROM blacklist;").fetchone()[0] == 0
    check.close()


def test_writing_to_a_missing_or_non_sqlite_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(PanelDbError, match="not found"):
        with write_transaction(tmp_path / "nope.db"):
            pass
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"this is not a database" * 50)
    with pytest.raises(PanelDbError):
        with write_transaction(junk):
            pass
```

(`MIN_SCHEMA_VERSION` in `test_require_schema_accepts_the_current_version` now equals 5; the existing assertion `== MIN_SCHEMA_VERSION` keeps working.)

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_admin_settings.py tests/test_admin_db.py -q`
Expected: FAIL (`write_transaction` and `owner_id` do not exist).

- [ ] **Step 3: Implement**

`src/yokai_admin/core/settings.py`: add `from typing import Optional`, a last field `owner_id: Optional[int] = None` in `PanelSettings`, and in `load_settings` before the final `return`:

```python
    raw_owner = os.getenv("OWNER_ID", "").strip()
    owner_id: Optional[int] = None
    if raw_owner:
        try:
            owner_id = int(raw_owner)
        except ValueError as err:
            raise PanelConfigError(f"OWNER_ID must be a whole number, got {raw_owner!r}.") from err
        if owner_id <= 0:
            raise PanelConfigError(f"OWNER_ID must be a positive number, got {owner_id}.")
```

and `return PanelSettings(db_path=db_path, log_dir=log_dir, port=port, owner_id=owner_id)`.

`src/yokai_admin/core/db.py`: set `MIN_SCHEMA_VERSION = 5`, add `WRITE_BUSY_TIMEOUT_S = 5.0`, add `from typing import Iterator, Optional`, factor the existence check into a helper and add the write path:

```python
def _require_file(db_path: Path) -> None:
    if not db_path.exists():
        raise PanelDbError(
            f"Database not found at {db_path}. Start the bot once so it can create it, "
            "or set DB_PATH in .env."
        )


def open_readwrite(db_path: Path, busy_timeout_s: float) -> sqlite3.Connection:
    """Open the bot's database for writing. Autocommit, so transactions are always explicit."""
    _require_file(db_path)
    uri = db_path.resolve().as_uri() + "?mode=rw"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=busy_timeout_s, isolation_level=None)
    except sqlite3.Error as exc:
        raise PanelDbError(f"Could not open the database for writing: {exc}") from exc
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def write_transaction(
    db_path: Path, *, busy_timeout_s: Optional[float] = None
) -> Iterator[sqlite3.Connection]:
    """One atomic write: BEGIN IMMEDIATE, schema check, commit on success, roll back on error.

    BEGIN IMMEDIATE takes SQLite's write lock up front, so everything the block reads is still
    true when it writes; the bot's in-process lock does not cover this second process.
    """
    timeout = WRITE_BUSY_TIMEOUT_S if busy_timeout_s is None else busy_timeout_s
    conn = open_readwrite(db_path, timeout)
    try:
        try:
            conn.execute("BEGIN IMMEDIATE;")
        except sqlite3.Error as exc:
            text = str(exc).lower()
            if "locked" in text or "busy" in text:
                raise PanelDbError("The database is busy right now. Try again in a moment.") from exc
            raise PanelDbError(f"Could not start a database write: {exc}") from exc
        try:
            require_schema(conn)
            yield conn
            conn.execute("COMMIT;")
        except BaseException:
            try:
                conn.execute("ROLLBACK;")
            except sqlite3.Error:
                logger.warning("Rollback failed; the connection is closing anyway.")
            raise
    finally:
        conn.close()
```

(add `import logging` and `logger = logging.getLogger(__name__)` to the module; make `open_readonly` call `_require_file(db_path)` instead of its inline check.)

`pyproject.toml`: replace the three web lines with

```toml
    "fastapi>=0.138",
    "uvicorn>=0.49",
    "jinja2>=3.1",
    "python-multipart>=0.0.20",
```

then run `python -m pip install -e ".[dev]"` and `python -c "import multipart, importlib.metadata as m; print(m.version('python-multipart'))"`; if the installed version is lower than 0.0.20, lower the bound to what was installed (the code needs only form parsing).

`tests/test_admin_launch.py`: wrap `test_main_exits_1_with_a_message_when_the_database_is_missing` and `test_main_exits_1_on_a_bad_port` in `patch.dict(os.environ, {...}, clear=True)` instead of `monkeypatch.setenv` (add `import os` and `from unittest.mock import patch` is already imported), so the real `.env` that `main()` loads is discarded when the block ends. For example:

```python
def test_main_exits_1_on_a_bad_port(caplog: pytest.LogCaptureFixture) -> None:
    with patch.dict(os.environ, {"ADMIN_PORT": "80"}, clear=True):
        with caplog.at_level(logging.ERROR):
            assert main() == 1
    assert "ADMIN_PORT" in caplog.text
```

`tests/admin_helpers.py`: add `owner_id: Optional[int] = 111` to `panel_client` (pass it into `PanelSettings(..., owner_id=owner_id)`); extend `seed_database` with `admins: Sequence[int] = ()`, `blacklist: Sequence[tuple[int, str]] = ()`, `names: Optional[Mapping[int, str]] = None` and inside its `run()`:

```python
            for user_id in admins:
                await db.conn.execute(
                    "INSERT INTO admins (user_id, added_ts, added_by) VALUES (?, 1, 'test');", (user_id,)
                )
            for user_id, reason in blacklist:
                await db.conn.execute(
                    "INSERT INTO blacklist (user_id, reason, added_ts, added_by) VALUES (?, ?, 1, 'test');",
                    (user_id, reason),
                )
            for user_id, name in (names or {}).items():
                await db.upsert_user_name(user_id, name, now=1)
```

(import `Mapping` from `typing`.)

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_admin_settings.py tests/test_admin_db.py tests/test_admin_launch.py tests/test_admin_dashboard.py tests/test_admin_dashboard_service.py -q`
Expected: PASS. Then run the whole suite once: `python -m pytest -q`; it should be green again now that the panel expects v5.

- [ ] **Step 5: Commit**

```powershell
git add pyproject.toml src/yokai_admin tests
git commit -m "feat(admin): add the panel write path, OWNER_ID setting and schema 5" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Audit core, people helper and formatting

**Files:**
- Create: `src/yokai_admin/core/audit.py`, `src/yokai_admin/core/people.py`, `src/yokai_admin/core/formatting.py`
- Modify: `src/yokai_admin/core/templating.py`, `src/yokai_admin/modules/dashboard/service.py`
- Test: `tests/test_admin_audit_core.py`

**Interfaces:**
- Consumes: `write_transaction`, `readonly` (Task 3); `seed_database` (Task 3).
- Produces:
  - `formatting.describe_age(seconds: int) -> str` (moved from the dashboard service, which re-imports it so existing tests keep working); `formatting.format_when(ts: Optional[int]) -> str` giving local `YYYY-MM-DD HH:MM`, or an en dash for `None`. `build_templates` registers `format_when` as the Jinja filter `when`.
  - `people.UserRef(id: int, name: Optional[str])` with `.display` (`name` or `"Unknown user"`) and `.label` (`"Sami (123)"` or `"Unknown user (123)"`); `people.user_refs(conn, ids: Iterable[int]) -> dict[int, UserRef]` (every requested id present; chunks of 500).
  - `audit.ACTOR = "panel"`; `audit.AuditEntry(id, ts, actor, module, action, target, detail: dict[str, Any], reason)`; `audit.record(conn, *, module, action, target=None, detail=None, reason=None, now=None, actor=ACTOR) -> int`; `audit.list_entries(conn, *, module=None, target=None, limit=25, offset=0) -> tuple[list[AuditEntry], int]` (newest first, total count for paging); `audit.modules_seen(conn) -> list[str]`; `audit.latest_ts(conn) -> Optional[int]`. Malformed `detail_json` yields `{"raw": <text>}`, never an exception.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_admin_audit_core.py`:

```python
"""Tests for the audit log, the people helper and the formatting helpers."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from tests.admin_helpers import seed_database
from yokai_admin.core.audit import ACTOR, latest_ts, list_entries, modules_seen, record
from yokai_admin.core.db import readonly, write_transaction
from yokai_admin.core.formatting import describe_age, format_when
from yokai_admin.core.people import UserRef, user_refs


def _db(tmp_path: Path, **seed: object) -> Path:
    path = tmp_path / "b.db"
    seed_database(path, **seed)  # type: ignore[arg-type]
    return path


def test_record_stores_every_field_and_returns_the_id(tmp_path: Path) -> None:
    path = _db(tmp_path)
    with write_transaction(path) as conn:
        entry_id = record(
            conn, module="access", action="blacklist.add", target="42",
            detail={"name": "Sami", "previous_reason": None}, reason="spam", now=1000,
        )
    with readonly(path) as conn:
        entries, total = list_entries(conn)
    assert total == 1 and entries[0].id == entry_id
    assert (entries[0].ts, entries[0].actor, entries[0].module) == (1000, ACTOR, "access")
    assert (entries[0].action, entries[0].target, entries[0].reason) == ("blacklist.add", "42", "spam")
    assert entries[0].detail == {"name": "Sami", "previous_reason": None}


def test_entries_come_newest_first_with_filters_and_paging(tmp_path: Path) -> None:
    path = _db(tmp_path)
    with write_transaction(path) as conn:
        for i in range(30):
            record(conn, module="access" if i % 2 == 0 else "users", action="x", target=str(i % 3), now=100 + i)
    with readonly(path) as conn:
        everything, total = list_entries(conn, limit=25)
        assert total == 30 and len(everything) == 25 and everything[0].ts == 129
        second_page, _ = list_entries(conn, limit=25, offset=25)
        assert len(second_page) == 5
        only_access, access_total = list_entries(conn, module="access")
        assert access_total == 15 and {e.module for e in only_access} == {"access"}
        one_target, target_total = list_entries(conn, module="access", target="0")
        assert {e.target for e in one_target} == {"0"} and target_total == len(one_target)
        assert modules_seen(conn) == ["access", "users"]
        assert latest_ts(conn) == 129


def test_an_empty_log_has_no_latest_timestamp(tmp_path: Path) -> None:
    with readonly(_db(tmp_path)) as conn:
        assert latest_ts(conn) is None and list_entries(conn) == ([], 0)


def test_malformed_detail_json_never_raises(tmp_path: Path) -> None:
    path = _db(tmp_path)
    raw = sqlite3.connect(path)
    raw.execute(
        "INSERT INTO admin_audit (ts, actor, module, action, detail_json) VALUES (1, 'panel', 'm', 'a', '{oops');"
    )
    raw.commit()
    raw.close()
    with readonly(path) as conn:
        entries, _ = list_entries(conn)
    assert entries[0].detail == {"raw": "{oops"}


def test_user_refs_resolve_known_and_unknown_people(tmp_path: Path) -> None:
    path = _db(tmp_path, names={1: "Sami", 2: "Alex"})
    with readonly(path) as conn:
        refs = user_refs(conn, [1, 3, 1])
        assert user_refs(conn, []) == {}
    assert refs[1] == UserRef(1, "Sami") and refs[3] == UserRef(3, None)
    assert refs[1].label == "Sami (1)" and refs[3].label == "Unknown user (3)"


def test_user_refs_handles_many_ids(tmp_path: Path) -> None:
    path = _db(tmp_path, names={5: "Five"})
    with readonly(path) as conn:
        refs = user_refs(conn, range(1, 1201))
    assert len(refs) == 1200 and refs[5].name == "Five"


@pytest.mark.parametrize(
    ("seconds", "text"), [(0, "0 seconds"), (1, "1 second"), (300, "5 minutes"), (3 * 86400, "3 days")]
)
def test_describe_age_lives_in_formatting(seconds: int, text: str) -> None:
    assert describe_age(seconds) == text


def test_format_when_is_local_time_or_a_dash() -> None:
    ts = 1_790_000_000
    assert format_when(ts) == datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    assert format_when(None) == "–"
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_admin_audit_core.py -q`
Expected: FAIL (`ModuleNotFoundError: yokai_admin.core.audit`).

- [ ] **Step 3: Implement**

`src/yokai_admin/core/formatting.py`:

```python
"""Small display helpers shared by modules."""

from __future__ import annotations

from datetime import datetime
from typing import Optional


def describe_age(seconds: int) -> str:
    """Plain-language age such as '1 second', '5 minutes' or '3 days'."""
    if seconds < 60:
        count, unit = seconds, "second"
    elif seconds < 3600:
        count, unit = seconds // 60, "minute"
    elif seconds < 86_400:
        count, unit = seconds // 3600, "hour"
    else:
        count, unit = seconds // 86_400, "day"
    return f"{count} {unit}{'' if count == 1 else 's'}"


def format_when(ts: Optional[int]) -> str:
    """Local date and time of a Unix timestamp, or an en dash when there is none."""
    if ts is None:
        return "–"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
```

In `src/yokai_admin/modules/dashboard/service.py` delete the local `describe_age` and add `from yokai_admin.core.formatting import describe_age` (the existing `status_note` and tests keep working; keep `describe_age` importable from the service by listing it in a `__all__`-free re-import, which the plain import already provides).

In `src/yokai_admin/core/templating.py`, in `build_templates`, assign the result to a variable, register the filter and return it:

```python
    templates = Jinja2Templates(directory=directories)
    templates.env.filters["when"] = format_when
    return templates
```

(import `format_when` from `yokai_admin.core.formatting`).

`src/yokai_admin/core/people.py`:

```python
"""Resolve Discord user IDs to the display names the bot has seen."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Iterable, Optional

_CHUNK = 500


@dataclass(frozen=True, slots=True)
class UserRef:
    """A person the panel can show: their ID and, when the bot has seen them, their name."""

    id: int
    name: Optional[str]

    @property
    def display(self) -> str:
        return self.name or "Unknown user"

    @property
    def label(self) -> str:
        return f"{self.display} ({self.id})"


def user_refs(conn: sqlite3.Connection, ids: Iterable[int]) -> dict[int, UserRef]:
    """Return a `UserRef` for every requested ID (name is None when the bot never saw them)."""
    unique = sorted(set(ids))
    refs = {user_id: UserRef(user_id, None) for user_id in unique}
    for start in range(0, len(unique), _CHUNK):
        chunk = unique[start : start + _CHUNK]
        # Only "?" placeholders are formatted into the SQL; the IDs travel as parameters.
        marks = ",".join("?" * len(chunk))
        rows = conn.execute(
            f"SELECT user_id, display_name FROM user_names WHERE user_id IN ({marks});", chunk
        )
        for row in rows:
            refs[int(row["user_id"])] = UserRef(int(row["user_id"]), str(row["display_name"]))
    return refs
```

`src/yokai_admin/core/audit.py`:

```python
"""The admin audit log: every panel change is recorded in the same transaction as the change."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Optional

ACTOR = "panel"


@dataclass(frozen=True, slots=True)
class AuditEntry:
    """One recorded admin action."""

    id: int
    ts: int
    actor: str
    module: str
    action: str
    target: Optional[str]
    detail: dict[str, Any]
    reason: Optional[str]


def record(
    conn: sqlite3.Connection,
    *,
    module: str,
    action: str,
    target: Optional[str] = None,
    detail: Optional[dict[str, Any]] = None,
    reason: Optional[str] = None,
    now: Optional[int] = None,
    actor: str = ACTOR,
) -> int:
    """Insert an audit row on the caller's connection (so it commits or rolls back with the change)."""
    ts = int(time.time()) if now is None else now
    payload = json.dumps(detail, sort_keys=True, ensure_ascii=False) if detail else None
    cursor = conn.execute(
        "INSERT INTO admin_audit (ts, actor, module, action, target, detail_json, reason) "
        "VALUES (?, ?, ?, ?, ?, ?, ?);",
        (ts, actor, module, action, target, payload, reason),
    )
    assert cursor.lastrowid is not None
    return int(cursor.lastrowid)


def _parse_detail(text: Optional[str]) -> dict[str, Any]:
    if not text:
        return {}
    try:
        value = json.loads(text)
    except ValueError:
        return {"raw": text}
    return value if isinstance(value, dict) else {"raw": text}


def list_entries(
    conn: sqlite3.Connection,
    *,
    module: Optional[str] = None,
    target: Optional[str] = None,
    limit: int = 25,
    offset: int = 0,
) -> tuple[list[AuditEntry], int]:
    """Newest-first page of audit entries plus the total number matching the filters."""
    clauses: list[str] = []
    params: list[Any] = []
    if module:
        clauses.append("module = ?")
        params.append(module)
    if target:
        clauses.append("target = ?")
        params.append(target)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""  # fixed fragments only
    total = int(conn.execute(f"SELECT COUNT(*) FROM admin_audit{where};", params).fetchone()[0])
    rows = conn.execute(
        "SELECT id, ts, actor, module, action, target, detail_json, reason FROM admin_audit"
        f"{where} ORDER BY id DESC LIMIT ? OFFSET ?;",
        [*params, limit, offset],
    ).fetchall()
    entries = [
        AuditEntry(
            id=int(r["id"]), ts=int(r["ts"]), actor=str(r["actor"]), module=str(r["module"]),
            action=str(r["action"]), target=r["target"], detail=_parse_detail(r["detail_json"]),
            reason=r["reason"],
        )
        for r in rows
    ]
    return entries, total


def modules_seen(conn: sqlite3.Connection) -> list[str]:
    """Module names that have written at least one audit entry, for the filter."""
    return [str(r[0]) for r in conn.execute("SELECT DISTINCT module FROM admin_audit ORDER BY module;")]


def latest_ts(conn: sqlite3.Connection) -> Optional[int]:
    """Timestamp of the most recent audit entry, or None when the log is empty."""
    value = conn.execute("SELECT MAX(ts) FROM admin_audit;").fetchone()[0]
    return None if value is None else int(value)
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_admin_audit_core.py tests/test_admin_dashboard_service.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/yokai_admin tests/test_admin_audit_core.py
git commit -m "feat(admin): add the audit core, people helper and formatting" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Access service (rules and writes)

**Files:**
- Create: `src/yokai_admin/modules/access/__init__.py` (docstring only for now), `src/yokai_admin/modules/access/service.py`
- Test: `tests/test_admin_access_service.py`

**Interfaces:**
- Consumes: `write_transaction` (Task 3); `audit.record` (Task 4); `user_refs`, `UserRef` (Task 4); `PanelError`.
- Produces (in `service.py`):
  - `AccessRefused(PanelError)`: a request the panel refuses; `str(exc)` is shown to the owner.
  - `parse_user_id(raw: str) -> int`: ASCII digits only after stripping; `0 < id < 2**63`.
  - `clean_reason(raw: str, *, required: bool) -> str`: control/zero-width/bidi characters become spaces, whitespace collapsed, 3-200 characters when required, at most 200 otherwise.
  - `Entry(user: UserRef, text: str, added_ts: int, added_by: str, flag: Optional[str])`; `list_blacklist(conn)`, `list_admins(conn)` (newest first; flags: a blacklisted admin shows "Also blacklisted, so the bot ignores this admin entry."; a blacklisted person who is also an admin shows "Also on the admin list. The blacklist wins.").
  - Writers, each one `write_transaction` that also records an audit row (`module="access"`, `target=str(user_id)`): `add_blacklist(db_path, *, owner_id, raw_id, raw_reason, now=None) -> UserRef`, `remove_blacklist(db_path, *, raw_id, now=None) -> UserRef`, `add_admin(db_path, *, owner_id, raw_id, raw_note, now=None) -> UserRef`, `remove_admin(db_path, *, raw_id, now=None) -> UserRef`. Audit actions: `blacklist.add`, `blacklist.remove`, `admin.add`, `admin.remove`; reason column holds the typed reason or note; `detail` holds `{"name": ...}` plus `{"previous_reason": ...}` on blacklist removal.
  - Refusals (exact first words matter to the UI tests): owner ("That ID belongs to the bot owner..."), already listed ("... is already on the blacklist."), not listed ("... is not on the blacklist ..."), admin add for a blacklisted person ("... is blacklisted. Remove them from the blacklist before making them an admin.").

- [ ] **Step 1: Write the failing tests**

Create `tests/test_admin_access_service.py`:

```python
"""Tests for the Access rules: validation, the owner guard, atomic writes and the bot's view."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import AsyncIterator

import pytest

from tests.admin_helpers import seed_database
from yokai.moderation.store import ModerationStore
from yokai.storage.db import Database
from yokai_admin.core.audit import list_entries
from yokai_admin.core.db import readonly
from yokai_admin.errors import PanelDbError
from yokai_admin.modules.access import service
from yokai_admin.modules.access.service import (
    AccessRefused,
    add_admin,
    add_blacklist,
    clean_reason,
    list_admins,
    list_blacklist,
    parse_user_id,
    remove_admin,
    remove_blacklist,
)

OWNER = 111


def _db(tmp_path: Path, **seed: object) -> Path:
    path = tmp_path / "b.db"
    seed_database(path, **seed)  # type: ignore[arg-type]
    return path


def _rows(path: Path, table: str) -> list[tuple[object, ...]]:
    with readonly(path) as conn:
        return [tuple(r) for r in conn.execute(f"SELECT * FROM {table};")]  # test-only fixed names


def _audit(path: Path):  # type: ignore[no-untyped-def]
    with readonly(path) as conn:
        return list_entries(conn)[0]


@pytest.mark.parametrize(("raw", "value"), [("123", 123), ("  456  ", 456), ("000789", 789), (str(2**63 - 1), 2**63 - 1)])
def test_valid_ids_parse(raw: str, value: int) -> None:
    assert parse_user_id(raw) == value


@pytest.mark.parametrize(
    "raw", ["", "  ", "abc", "12 34", "+5", "-5", "1e3", "0x10", "12.5", "0", "00", str(2**63),
            "١٢٣", "１２"],  # Arabic-Indic and full-width digits
)
def test_invalid_ids_are_refused(raw: str) -> None:
    """Review Focus 1: int() would accept the non-ASCII digits; the panel must not."""
    with pytest.raises(AccessRefused):
        parse_user_id(raw)


def test_clean_reason_strips_unsafe_characters_and_enforces_length() -> None:
    assert clean_reason("  spam \n\t in‮ chat  ", required=True) == "spam in chat"
    assert clean_reason("", required=False) == ""
    with pytest.raises(AccessRefused, match="reason"):
        clean_reason("ab", required=True)
    with pytest.raises(AccessRefused, match="200"):
        clean_reason("x" * 201, required=False)


def test_adding_to_the_blacklist_writes_the_row_and_the_audit_entry_together(tmp_path: Path) -> None:
    path = _db(tmp_path, names={42: "Mallory"})
    ref = add_blacklist(path, owner_id=OWNER, raw_id="42", raw_reason="spamming  the queue", now=500)
    assert ref.label == "Mallory (42)"
    assert _rows(path, "blacklist") == [(42, "spamming the queue", 500, "panel")]
    (entry,) = _audit(path)
    assert (entry.module, entry.action, entry.target) == ("access", "blacklist.add", "42")
    assert entry.reason == "spamming the queue" and entry.detail == {"name": "Mallory"}


@pytest.mark.parametrize("raw", ["111", " 111 ", "000111"])
def test_the_owner_can_never_be_added_to_either_list(tmp_path: Path, raw: str) -> None:
    """Review Focus 1: refused however it is written, and nothing is written at all."""
    path = _db(tmp_path)
    with pytest.raises(AccessRefused, match="owner"):
        add_blacklist(path, owner_id=OWNER, raw_id=raw, raw_reason="because")
    with pytest.raises(AccessRefused, match="owner"):
        add_admin(path, owner_id=OWNER, raw_id=raw, raw_note="")
    assert _rows(path, "blacklist") == [] and _rows(path, "admins") == [] and _audit(path) == []


def test_duplicates_and_missing_entries_are_refused(tmp_path: Path) -> None:
    path = _db(tmp_path, blacklist=[(5, "spam")], admins=[7])
    with pytest.raises(AccessRefused, match="already on the blacklist"):
        add_blacklist(path, owner_id=OWNER, raw_id="5", raw_reason="again")
    with pytest.raises(AccessRefused, match="already an admin"):
        add_admin(path, owner_id=OWNER, raw_id="7", raw_note="")
    with pytest.raises(AccessRefused, match="not on the blacklist"):
        remove_blacklist(path, raw_id="99")
    with pytest.raises(AccessRefused, match="not an admin"):
        remove_admin(path, raw_id="99")
    assert _audit(path) == []


def test_a_blacklisted_person_cannot_be_made_an_admin(tmp_path: Path) -> None:
    path = _db(tmp_path, blacklist=[(5, "spam")])
    with pytest.raises(AccessRefused, match="Remove them from the blacklist"):
        add_admin(path, owner_id=OWNER, raw_id="5", raw_note="")
    assert _rows(path, "admins") == []


def test_blacklisting_an_admin_keeps_the_admin_row_and_flags_both_lists(tmp_path: Path) -> None:
    path = _db(tmp_path, admins=[7])
    add_blacklist(path, owner_id=OWNER, raw_id="7", raw_reason="compromised account")
    assert _rows(path, "admins")[0][0] == 7  # the bot lets the blacklist win
    with readonly(path) as conn:
        (blacklisted,) = list_blacklist(conn)
        (admin,) = list_admins(conn)
    assert "admin list" in (blacklisted.flag or "") and "blacklisted" in (admin.flag or "").lower()


def test_removals_record_what_was_removed(tmp_path: Path) -> None:
    path = _db(tmp_path, blacklist=[(5, "spam")], admins=[7], names={5: "Mallory", 7: "Alex"})
    remove_blacklist(path, raw_id="5", now=900)
    remove_admin(path, raw_id="7", now=901)
    assert _rows(path, "blacklist") == [] and _rows(path, "admins") == []
    newest, oldest = _audit(path)
    assert (oldest.action, oldest.detail) == ("blacklist.remove", {"name": "Mallory", "previous_reason": "spam"})
    assert (newest.action, newest.detail) == ("admin.remove", {"name": "Alex"})


def test_an_admin_note_goes_to_the_audit_log_only(tmp_path: Path) -> None:
    path = _db(tmp_path)
    add_admin(path, owner_id=OWNER, raw_id="7", raw_note="helps with moderation")
    assert _rows(path, "admins")[0][0] == 7
    (entry,) = _audit(path)
    assert (entry.action, entry.reason) == ("admin.add", "helps with moderation")


def test_a_failing_audit_write_rolls_the_change_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review Focus 2: no change without its audit row."""
    path = _db(tmp_path)

    def broken_record(*args: object, **kwargs: object) -> int:
        raise RuntimeError("audit table is broken")

    monkeypatch.setattr(service, "record", broken_record)
    with pytest.raises(RuntimeError):
        add_blacklist(path, owner_id=OWNER, raw_id="42", raw_reason="spamming")
    assert _rows(path, "blacklist") == []


def test_two_simultaneous_adds_leave_one_row_and_one_audit_entry(tmp_path: Path) -> None:
    """Review Focus 2: BEGIN IMMEDIATE serialises the writers; the loser is refused cleanly."""
    path = _db(tmp_path)
    outcomes: list[str] = []
    barrier = threading.Barrier(2)

    def attempt() -> None:
        barrier.wait()
        try:
            add_blacklist(path, owner_id=OWNER, raw_id="42", raw_reason="spamming")
            outcomes.append("added")
        except AccessRefused:
            outcomes.append("refused")

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
    assert sorted(outcomes) == ["added", "refused"]
    assert len(_rows(path, "blacklist")) == 1 and len(_audit(path)) == 1


def test_writes_to_an_old_schema_are_refused(tmp_path: Path) -> None:
    import sqlite3

    old = tmp_path / "old.db"
    raw = sqlite3.connect(old)
    raw.execute("PRAGMA user_version = 4;")
    raw.commit()
    raw.close()
    with pytest.raises(PanelDbError):
        add_blacklist(old, owner_id=OWNER, raw_id="42", raw_reason="spamming")


async def test_the_bot_sees_the_blacklist_change_made_by_the_panel(tmp_path: Path) -> None:
    """Review Focus 5: a ModerationStore on the same file picks the change up."""
    path = tmp_path / "b.db"
    bot_db = Database(path)
    await bot_db.initialize()
    try:
        add_blacklist(path, owner_id=OWNER, raw_id="42", raw_reason="spamming")
        add_admin(path, owner_id=OWNER, raw_id="7", raw_note="")
        store = ModerationStore(bot_db, owner_id=OWNER, ttl_s=0)
        assert await store.is_blacklisted(42) is True and await store.is_admin(7) is True
        remove_blacklist(path, raw_id="42")
        assert await store.is_blacklisted(42) is False
    finally:
        await bot_db.close()
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_admin_access_service.py -q`
Expected: FAIL (`ModuleNotFoundError: yokai_admin.modules.access`).

- [ ] **Step 3: Implement**

Create `src/yokai_admin/modules/access/__init__.py` containing only `"""Access module: the blacklist and the admin list."""` (Task 6 fills it in).

Create `src/yokai_admin/modules/access/service.py`:

```python
"""Access rules: who may be blacklisted or made an admin, and the audited writes that do it."""

from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from yokai_admin.core.audit import record
from yokai_admin.core.db import write_transaction
from yokai_admin.core.people import UserRef, user_refs
from yokai_admin.errors import PanelError

MIN_REASON = 3
MAX_REASON = 200
# Same set the bot strips from display names: control, zero-width and bidi override characters.
_UNSAFE = re.compile(r"[\x00-\x1f\x7f-\x9f​-‏ -‮⁦-⁩]")
_OWNER_MESSAGE = (
    "That ID belongs to the bot owner, who can never be blacklisted or added to the admin list."
)


class AccessRefused(PanelError):
    """A request the panel refuses on purpose; the message is shown to the owner."""


@dataclass(frozen=True, slots=True)
class Entry:
    """One row of the blacklist or the admin list."""

    user: UserRef
    text: str
    added_ts: int
    added_by: str
    flag: Optional[str]


def parse_user_id(raw: str) -> int:
    """Turn typed text into a Discord user ID, or refuse with a message that says what to type."""
    text = raw.strip()
    # int() would also accept digits from other scripts, so check for plain ASCII digits first.
    if not (text.isascii() and text.isdigit()):
        raise AccessRefused(
            "Enter the Discord user ID as digits only, for example 123456789012345678."
        )
    value = int(text)
    if not 0 < value < 2**63:
        raise AccessRefused("That is not a valid Discord user ID.")
    return value


def clean_reason(raw: str, *, required: bool) -> str:
    """Normalise a reason or note; refuse when it is too short (if required) or too long."""
    text = " ".join(_UNSAFE.sub(" ", raw).split())
    if required and len(text) < MIN_REASON:
        raise AccessRefused(f"Give a short reason, at least {MIN_REASON} characters.")
    if len(text) > MAX_REASON:
        raise AccessRefused(f"Keep the reason under {MAX_REASON} characters.")
    return text


def list_blacklist(conn: sqlite3.Connection) -> list[Entry]:
    rows = conn.execute(
        "SELECT user_id, reason, added_ts, added_by FROM blacklist ORDER BY added_ts DESC, user_id;"
    ).fetchall()
    admin_ids = {int(r[0]) for r in conn.execute("SELECT user_id FROM admins;")}
    refs = user_refs(conn, [int(r["user_id"]) for r in rows])
    return [
        Entry(
            refs[int(r["user_id"])], str(r["reason"]), int(r["added_ts"]), str(r["added_by"]),
            "Also on the admin list. The blacklist wins." if int(r["user_id"]) in admin_ids else None,
        )
        for r in rows
    ]


def list_admins(conn: sqlite3.Connection) -> list[Entry]:
    rows = conn.execute(
        "SELECT user_id, added_ts, added_by FROM admins ORDER BY added_ts DESC, user_id;"
    ).fetchall()
    blacklisted = {int(r[0]) for r in conn.execute("SELECT user_id FROM blacklist;")}
    refs = user_refs(conn, [int(r["user_id"]) for r in rows])
    return [
        Entry(
            refs[int(r["user_id"])], "", int(r["added_ts"]), str(r["added_by"]),
            "Also blacklisted, so the bot ignores this admin entry."
            if int(r["user_id"]) in blacklisted else None,
        )
        for r in rows
    ]


def _refuse_owner(user_id: int, owner_id: int) -> None:
    if user_id == owner_id:
        raise AccessRefused(_OWNER_MESSAGE)


def add_blacklist(
    db_path: Path, *, owner_id: int, raw_id: str, raw_reason: str, now: Optional[int] = None
) -> UserRef:
    user_id = parse_user_id(raw_id)
    _refuse_owner(user_id, owner_id)
    reason = clean_reason(raw_reason, required=True)
    ts = int(time.time()) if now is None else now
    with write_transaction(db_path) as conn:
        ref = user_refs(conn, [user_id])[user_id]
        if conn.execute("SELECT 1 FROM blacklist WHERE user_id = ?;", (user_id,)).fetchone():
            raise AccessRefused(f"{ref.label} is already on the blacklist.")
        conn.execute(
            "INSERT INTO blacklist (user_id, reason, added_ts, added_by) VALUES (?, ?, ?, 'panel');",
            (user_id, reason, ts),
        )
        record(conn, module="access", action="blacklist.add", target=str(user_id),
               detail={"name": ref.name}, reason=reason, now=ts)
    return ref


def remove_blacklist(db_path: Path, *, raw_id: str, now: Optional[int] = None) -> UserRef:
    user_id = parse_user_id(raw_id)
    ts = int(time.time()) if now is None else now
    with write_transaction(db_path) as conn:
        ref = user_refs(conn, [user_id])[user_id]
        row = conn.execute("SELECT reason FROM blacklist WHERE user_id = ?;", (user_id,)).fetchone()
        if row is None:
            raise AccessRefused(
                f"{ref.label} is not on the blacklist. It may have been removed already."
            )
        conn.execute("DELETE FROM blacklist WHERE user_id = ?;", (user_id,))
        record(conn, module="access", action="blacklist.remove", target=str(user_id),
               detail={"name": ref.name, "previous_reason": str(row["reason"])}, now=ts)
    return ref


def add_admin(
    db_path: Path, *, owner_id: int, raw_id: str, raw_note: str, now: Optional[int] = None
) -> UserRef:
    user_id = parse_user_id(raw_id)
    _refuse_owner(user_id, owner_id)
    note = clean_reason(raw_note, required=False)
    ts = int(time.time()) if now is None else now
    with write_transaction(db_path) as conn:
        ref = user_refs(conn, [user_id])[user_id]
        if conn.execute("SELECT 1 FROM admins WHERE user_id = ?;", (user_id,)).fetchone():
            raise AccessRefused(f"{ref.label} is already an admin.")
        if conn.execute("SELECT 1 FROM blacklist WHERE user_id = ?;", (user_id,)).fetchone():
            raise AccessRefused(
                f"{ref.label} is blacklisted. Remove them from the blacklist before making them an admin."
            )
        conn.execute(
            "INSERT INTO admins (user_id, added_ts, added_by) VALUES (?, ?, 'panel');", (user_id, ts)
        )
        record(conn, module="access", action="admin.add", target=str(user_id),
               detail={"name": ref.name}, reason=note or None, now=ts)
    return ref


def remove_admin(db_path: Path, *, raw_id: str, now: Optional[int] = None) -> UserRef:
    user_id = parse_user_id(raw_id)
    ts = int(time.time()) if now is None else now
    with write_transaction(db_path) as conn:
        ref = user_refs(conn, [user_id])[user_id]
        if not conn.execute("SELECT 1 FROM admins WHERE user_id = ?;", (user_id,)).fetchone():
            raise AccessRefused(f"{ref.label} is not an admin. They may have been removed already.")
        conn.execute("DELETE FROM admins WHERE user_id = ?;", (user_id,))
        record(conn, module="access", action="admin.remove", target=str(user_id),
               detail={"name": ref.name}, now=ts)
    return ref
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_admin_access_service.py -q`
Expected: PASS. If the two-thread test is flaky on timing, widen the `join` timeout; never weaken the assertions.

- [ ] **Step 5: Commit**

```powershell
git add src/yokai_admin/modules/access tests/test_admin_access_service.py
git commit -m "feat(admin): add the Access rules and audited writes" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Access module (routes, templates, styles, tile)

**Files:**
- Create: `src/yokai_admin/modules/access/router.py`, `src/yokai_admin/modules/access/templates/access/page.html`, `.../_lists.html`, `.../_confirm.html`
- Modify: `src/yokai_admin/modules/access/__init__.py`, `src/yokai_admin/modules/__init__.py`, `src/yokai_admin/static/panel.css`, `tests/admin_helpers.py` (add `post` helper), `tests/test_admin_static.py`
- Test: `tests/test_admin_access.py`

**Interfaces:**
- Consumes: Task 5 service; `render`, `render_fragment` (P-A2); `PanelSettings.owner_id`.
- Produces: `MODULE = AdminModule(id="access", title="Access", nav_order=10, tile=access_tile, ...)`. Routes under `/m/access`: `GET ""` (page), `GET "/lists"` (section fragment), `POST "/blacklist"` (form `user_id`, `reason`), `GET "/blacklist/{user_id}/confirm"` (confirm row), `POST "/blacklist/{user_id}/remove"`, and the same four for `/admins` (form `user_id`, `note`). Every response is HTTP 200 with the whole `#access` section (flash line first); `confirm` returns one table row. Path `user_id` is a `str` (parsed by the service, so a bad value is a flash message, not a 422). `access_tile(ctx) -> Tile("Access lists", "<n> blacklisted, <m> admins")` (neutral status; error row if the database is unreadable). Test helper: `post(client, csrf, path, **form) -> httpx.Response` sending form data with `X-CSRF-Token` and `HX-Request: true`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/admin_helpers.py`:

```python
def post(client: TestClient, csrf: str, path: str, **form: str) -> Response:
    """POST form data the way HTMX does: CSRF header plus HX-Request."""
    return client.post(path, data=form, headers={"X-CSRF-Token": csrf, "HX-Request": "true"})
```

(with `from httpx import Response`.)

Create `tests/test_admin_access.py`:

```python
"""Tests for the Access module through the real app."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from tests.admin_helpers import panel_client, post, seed_database
from yokai_admin.core import db as panel_db

ACCESS = "yokai_admin.modules.access:MODULE"


def _count(path: Path, table: str) -> int:
    raw = sqlite3.connect(path)
    try:
        return int(raw.execute(f"SELECT COUNT(*) FROM {table};").fetchone()[0])  # fixed names
    finally:
        raw.close()


def test_the_page_lists_both_lists_with_names_and_ids(tmp_path: Path) -> None:
    db = tmp_path / "yokai.db"
    seed_database(db, blacklist=[(5, "spamming the queue")], admins=[7], names={5: "Mallory", 7: "Alex"})
    with panel_client(tmp_path, [ACCESS], db_path=db) as (client, _):
        page = client.get("/m/access").text
    assert "<strong>Mallory</strong>" in page and '<span class="id">5</span>' in page
    assert "spamming the queue" in page and "<strong>Alex</strong>" in page
    assert 'aria-current="page">Access</a>' in page
    assert 'hx-post="/m/access/blacklist"' in page and 'hx-post="/m/access/admins"' in page


def test_empty_lists_say_so(tmp_path: Path) -> None:
    db = tmp_path / "yokai.db"
    seed_database(db)
    with panel_client(tmp_path, [ACCESS], db_path=db) as (client, _):
        page = client.get("/m/access").text
    assert "Nobody is blacklisted." in page and "No admins yet." in page


def test_adding_to_the_blacklist_returns_the_refreshed_section_with_a_confirmation(tmp_path: Path) -> None:
    db = tmp_path / "yokai.db"
    seed_database(db, names={42: "Mallory"})
    with panel_client(tmp_path, [ACCESS], db_path=db) as (client, csrf):
        response = post(client, csrf, "/m/access/blacklist", user_id="42", reason="spamming the queue")
    assert response.status_code == 200 and "<html" not in response.text
    assert 'id="access"' in response.text and "Mallory (42) is blacklisted" in response.text
    assert "within a few seconds" in response.text
    assert _count(db, "blacklist") == 1 and _count(db, "admin_audit") == 1


@pytest.mark.parametrize(
    ("form", "message"),
    [
        ({"user_id": "abc", "reason": "spamming"}, "digits only"),
        ({"user_id": "42", "reason": "x"}, "reason"),
        ({"user_id": "111", "reason": "spamming"}, "bot owner"),
        ({"user_id": "١٢٣", "reason": "spamming"}, "digits only"),
        ({}, "digits only"),
    ],
)
def test_refusals_are_shown_as_a_normal_response_and_change_nothing(
    tmp_path: Path, form: dict[str, str], message: str
) -> None:
    db = tmp_path / "yokai.db"
    seed_database(db)
    with panel_client(tmp_path, [ACCESS], db_path=db) as (client, csrf):
        response = post(client, csrf, "/m/access/blacklist", **form)
    assert response.status_code == 200 and message in response.text  # HTMX swaps 200s only
    assert 'data-kind="error"' in response.text
    assert _count(db, "blacklist") == 0 and _count(db, "admin_audit") == 0


def test_with_no_owner_id_the_server_refuses_edits_and_the_page_hides_the_forms(tmp_path: Path) -> None:
    """Review Focus 4: hiding the forms is not enough; the endpoints refuse too."""
    db = tmp_path / "yokai.db"
    seed_database(db, blacklist=[(5, "spam")])
    with panel_client(tmp_path, [ACCESS], db_path=db, owner_id=None) as (client, csrf):
        page = client.get("/m/access").text
        response = post(client, csrf, "/m/access/blacklist", user_id="42", reason="spamming")
        removal = post(client, csrf, "/m/access/blacklist/5/remove")
    assert "Set OWNER_ID" in page and "<form" not in page and "hx-post" not in page
    assert "Set OWNER_ID" in response.text and "Set OWNER_ID" in removal.text
    assert _count(db, "blacklist") == 1 and _count(db, "admin_audit") == 0


def test_removal_asks_first_then_removes_and_audits(tmp_path: Path) -> None:
    db = tmp_path / "yokai.db"
    seed_database(db, blacklist=[(5, "spam")], names={5: "Mallory"})
    with panel_client(tmp_path, [ACCESS], db_path=db) as (client, csrf):
        confirm = client.get("/m/access/blacklist/5/confirm", headers={"HX-Request": "true"})
        assert "Remove" in confirm.text and "Mallory" in confirm.text and "<html" not in confirm.text
        assert _count(db, "blacklist") == 1  # asking changes nothing
        done = post(client, csrf, "/m/access/blacklist/5/remove")
    assert "Mallory (5) was removed from the blacklist" in done.text
    assert _count(db, "blacklist") == 0 and _count(db, "admin_audit") == 1


def test_confirming_a_person_who_is_not_listed_explains_instead_of_failing(tmp_path: Path) -> None:
    db = tmp_path / "yokai.db"
    seed_database(db)
    with panel_client(tmp_path, [ACCESS], db_path=db) as (client, csrf):
        confirm = client.get("/m/access/blacklist/99/confirm", headers={"HX-Request": "true"})
        gone = post(client, csrf, "/m/access/admins/99/remove")
    assert confirm.status_code == 200 and "not on the blacklist" in confirm.text
    assert gone.status_code == 200 and "not an admin" in gone.text


def test_admins_can_be_added_with_a_note_and_removed(tmp_path: Path) -> None:
    db = tmp_path / "yokai.db"
    seed_database(db, names={7: "Alex"})
    with panel_client(tmp_path, [ACCESS], db_path=db) as (client, csrf):
        added = post(client, csrf, "/m/access/admins", user_id="7", note="helps moderate")
        assert "Alex (7) is now an admin" in added.text and _count(db, "admins") == 1
        removed = post(client, csrf, "/m/access/admins/7/remove")
    assert "Alex (7) was removed from the admin list" in removed.text and _count(db, "admins") == 0


def test_hostile_names_and_reasons_are_escaped(tmp_path: Path) -> None:
    """Review Focus 3."""
    db = tmp_path / "yokai.db"
    seed_database(db, blacklist=[(5, "<script>alert(1)</script>")], names={5: "<img src=x onerror=alert(1)>"})
    with panel_client(tmp_path, [ACCESS], db_path=db) as (client, _):
        page = client.get("/m/access").text
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page
    assert "<img src=x" not in page and "&lt;img src=x" in page


def test_write_endpoints_need_a_session_and_the_csrf_token(tmp_path: Path) -> None:
    """Review Focus 6."""
    db = tmp_path / "yokai.db"
    seed_database(db)
    with panel_client(tmp_path, [ACCESS], db_path=db, login=False) as (client, _):
        assert client.post("/m/access/blacklist", data={"user_id": "42", "reason": "spamming"}).status_code == 401
    with panel_client(tmp_path, [ACCESS], db_path=db) as (client, csrf):
        assert client.post("/m/access/blacklist", data={"user_id": "42", "reason": "spamming"}).status_code == 403
        assert client.post("/m/access/admins/7/remove").status_code == 403
        assert client.get("/m/access/blacklist/5/remove").status_code == 405  # removal is POST-only
    assert _count(db, "blacklist") == 0


def test_a_busy_database_answers_with_a_friendly_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review Focus 4."""
    db = tmp_path / "yokai.db"
    seed_database(db)
    monkeypatch.setattr(panel_db, "WRITE_BUSY_TIMEOUT_S", 0.2)
    holder = sqlite3.connect(db, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE;")
    try:
        with panel_client(tmp_path, [ACCESS], db_path=db) as (client, csrf):
            response = post(client, csrf, "/m/access/blacklist", user_id="42", reason="spamming")
    finally:
        holder.execute("ROLLBACK;")
        holder.close()
    assert response.status_code == 200 and "busy right now" in response.text


def test_a_missing_or_old_database_renders_the_page_with_a_reason(tmp_path: Path) -> None:
    with panel_client(tmp_path, [ACCESS], db_path=tmp_path / "absent.db") as (client, csrf):
        page = client.get("/m/access")
        attempt = post(client, csrf, "/m/access/blacklist", user_id="42", reason="spamming")
    assert page.status_code == 200 and "not found" in page.text
    assert attempt.status_code == 200 and "not found" in attempt.text


def test_the_dashboard_tile_counts_both_lists(tmp_path: Path) -> None:
    db = tmp_path / "yokai.db"
    seed_database(db, blacklist=[(5, "a"), (6, "b")], admins=[7], heartbeat_ts=int(time.time()))
    paths = ["yokai_admin.modules.dashboard:MODULE", ACCESS]
    with panel_client(tmp_path, paths, db_path=db) as (client, _):
        page = client.get("/m/dashboard").text
    assert "Access lists" in page and "2 blacklisted, 1 admin" in page
```

Append to `tests/test_admin_static.py`:

```python
def test_form_and_table_styles_exist_and_use_the_palette_only() -> None:
    css = _css()
    for selector in (".btn", ".btn.danger", ".table", ".flash", ".add", ".link-danger", ".page-title"):
        assert selector in css
    assert "#" not in css.split(".btn {")[1].split("}")[0]  # buttons use tokens, not literal colors
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_admin_access.py tests/test_admin_static.py -q`
Expected: FAIL (module not registered, no router, no styles).

- [ ] **Step 3: Implement**

Create `src/yokai_admin/modules/access/router.py`:

```python
"""Access routes: blacklist and admin list, edited through HTMX forms."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from typing import Any, Callable, Optional

from fastapi import APIRouter, Form, Request
from fastapi.responses import Response

from yokai_admin.core.db import readonly, require_schema
from yokai_admin.core.people import UserRef
from yokai_admin.core.settings import PanelSettings
from yokai_admin.core.templating import render, render_fragment
from yokai_admin.errors import PanelDbError
from yokai_admin.modules.access.service import (
    AccessRefused,
    Entry,
    add_admin,
    add_blacklist,
    list_admins,
    list_blacklist,
    remove_admin,
    remove_blacklist,
)

logger = logging.getLogger(__name__)
router = APIRouter()
EDITING_OFF = (
    "Editing is off. Set OWNER_ID in .env and restart the panel, so the owner can be protected."
)
_SAVE_FAILED = "The database could not be changed right now. Nothing was saved."


@dataclass(frozen=True, slots=True)
class Flash:
    """A one-line result shown above the lists."""

    kind: str  # "ok" or "error"
    text: str


def _context(request: Request, flash: Optional[Flash]) -> dict[str, Any]:
    settings: PanelSettings = request.app.state.settings
    blacklist: list[Entry] = []
    admins: list[Entry] = []
    db_error: Optional[str] = None
    try:
        with readonly(settings.db_path) as conn:
            require_schema(conn)
            blacklist, admins = list_blacklist(conn), list_admins(conn)
    except PanelDbError as exc:
        db_error = str(exc)
    except sqlite3.Error as exc:
        logger.warning("Access could not read the database: %s", exc)
        db_error = f"The database could not be read right now ({exc}). It will retry shortly."
    return {
        "flash": flash,
        "db_error": db_error,
        "blacklist": blacklist,
        "admins": admins,
        "can_edit": settings.owner_id is not None,
    }


def _section(request: Request, flash: Optional[Flash] = None) -> Response:
    return render_fragment(request, "access/_lists.html", **_context(request, flash))


def _mutate(
    request: Request,
    action: Callable[[PanelSettings, int], UserRef],
    success: str,
) -> Response:
    """Run one write and answer with the refreshed section. Problems come back as HTTP 200 with
    an error line, because HTMX does not swap 4xx/5xx responses."""
    settings: PanelSettings = request.app.state.settings
    if settings.owner_id is None:
        return _section(request, Flash("error", EDITING_OFF))
    try:
        ref = action(settings, settings.owner_id)
    except (AccessRefused, PanelDbError) as exc:
        return _section(request, Flash("error", str(exc)))
    except sqlite3.Error as exc:
        logger.warning("Access write failed: %s", exc)
        return _section(request, Flash("error", _SAVE_FAILED))
    return _section(request, Flash("ok", success.replace("{who}", ref.label)))


def _confirm(request: Request, kind: str, raw_id: str) -> Response:
    context = _context(request, None)
    entries: list[Entry] = context["blacklist"] if kind == "blacklist" else context["admins"]
    match = next((e for e in entries if str(e.user.id) == raw_id.strip()), None)
    if match is None or not context["can_edit"]:
        who = "blacklist" if kind == "blacklist" else "admin list"
        flash = Flash("error", EDITING_OFF if not context["can_edit"] else f"That person is not on the {who}.")
        return _section(request, flash)
    return render_fragment(
        request,
        "access/_confirm.html",
        entry=match,
        kind=kind,
        list_name="blacklist" if kind == "blacklist" else "admin list",
    )


@router.get("")
def page(request: Request) -> Response:
    return render(request, "access/page.html", active="access", **_context(request, None))


@router.get("/lists")
def lists(request: Request) -> Response:
    return _section(request)


@router.post("/blacklist")
def blacklist_add(request: Request, user_id: str = Form(""), reason: str = Form("")) -> Response:
    return _mutate(
        request,
        lambda s, owner: add_blacklist(s.db_path, owner_id=owner, raw_id=user_id, raw_reason=reason),
        "{who} is blacklisted. The bot picks this up within a few seconds.",
    )


@router.get("/blacklist/{user_id}/confirm")
def blacklist_confirm(request: Request, user_id: str) -> Response:
    return _confirm(request, "blacklist", user_id)


@router.post("/blacklist/{user_id}/remove")
def blacklist_remove(request: Request, user_id: str) -> Response:
    return _mutate(
        request,
        lambda s, owner: remove_blacklist(s.db_path, raw_id=user_id),
        "{who} was removed from the blacklist. The bot picks this up within a few seconds.",
    )


@router.post("/admins")
def admin_add(request: Request, user_id: str = Form(""), note: str = Form("")) -> Response:
    return _mutate(
        request,
        lambda s, owner: add_admin(s.db_path, owner_id=owner, raw_id=user_id, raw_note=note),
        "{who} is now an admin. The bot picks this up within a few seconds.",
    )


@router.get("/admins/{user_id}/confirm")
def admin_confirm(request: Request, user_id: str) -> Response:
    return _confirm(request, "admins", user_id)


@router.post("/admins/{user_id}/remove")
def admin_remove(request: Request, user_id: str) -> Response:
    return _mutate(
        request,
        lambda s, owner: remove_admin(s.db_path, raw_id=user_id),
        "{who} was removed from the admin list. The bot picks this up within a few seconds.",
    )
```

Create `src/yokai_admin/modules/access/templates/access/page.html`:

```html
{% extends "base.html" %}
{% block title %}Access - Yokai admin{% endblock %}
{% block content %}
  <h1 class="page-title">Access</h1>
  <p class="lede">Choose who can use the bot and who can run its admin commands. The owner is always allowed and can never be blocked.</p>
  {% include "access/_lists.html" %}
{% endblock %}
```

Create `src/yokai_admin/modules/access/templates/access/_lists.html`:

```html
<section id="access">
  {% if flash %}
  <p class="flash" data-kind="{{ flash.kind }}" role="{{ 'alert' if flash.kind == 'error' else 'status' }}"><span class="sr-only">{{ 'Problem' if flash.kind == 'error' else 'Done' }}: </span>{{ flash.text }}</p>
  {% endif %}
  {% if db_error %}<p class="flash" data-kind="error" role="alert"><span class="sr-only">Problem: </span>{{ db_error }}</p>{% endif %}
  {% if not can_edit %}
  <div class="notice" role="status"><strong>Editing is off.</strong> Set OWNER_ID in .env and restart the panel, so the owner can be protected.</div>
  {% endif %}

  <div class="section">
    <h2 class="section-title">Blacklist</h2>
    <p class="hint">People here cannot use the bot at all. The bot notices changes within a few seconds.</p>
    {% if blacklist %}
    <div class="table-wrap"><table class="table">
      <thead><tr><th scope="col">Person</th><th scope="col">Reason</th><th scope="col">Added</th><th scope="col"><span class="sr-only">Actions</span></th></tr></thead>
      <tbody>
      {% for e in blacklist %}
        <tr>
          <td class="who"><strong>{{ e.user.display }}</strong><span class="id">{{ e.user.id }}</span></td>
          <td>{{ e.text }}{% if e.flag %}<span class="flagged">{{ e.flag }}</span>{% endif %}</td>
          <td>{{ e.added_ts|when }}</td>
          <td class="actions">{% if can_edit %}<button type="button" class="link-danger" hx-get="/m/access/blacklist/{{ e.user.id }}/confirm" hx-target="closest tr" hx-swap="outerHTML" aria-label="Remove {{ e.user.display }} from the blacklist">Remove</button>{% endif %}</td>
        </tr>
      {% endfor %}
      </tbody>
    </table></div>
    {% else %}
    <p class="empty">Nobody is blacklisted.</p>
    {% endif %}
    {% if can_edit %}
    <form class="add" hx-post="/m/access/blacklist" hx-target="#access" hx-swap="outerHTML">
      <label>Discord user ID<input name="user_id" inputmode="numeric" autocomplete="off" required></label>
      <label>Reason<input name="reason" maxlength="200" autocomplete="off" required></label>
      <button type="submit" class="btn">Add to blacklist</button>
    </form>
    {% endif %}
  </div>

  <div class="section">
    <h2 class="section-title">Admins</h2>
    <p class="hint">Admins can run the bot's admin commands. Only you can change this list.</p>
    {% if admins %}
    <div class="table-wrap"><table class="table">
      <thead><tr><th scope="col">Person</th><th scope="col">Added</th><th scope="col"><span class="sr-only">Actions</span></th></tr></thead>
      <tbody>
      {% for e in admins %}
        <tr>
          <td class="who"><strong>{{ e.user.display }}</strong><span class="id">{{ e.user.id }}</span>{% if e.flag %}<span class="flagged">{{ e.flag }}</span>{% endif %}</td>
          <td>{{ e.added_ts|when }}</td>
          <td class="actions">{% if can_edit %}<button type="button" class="link-danger" hx-get="/m/access/admins/{{ e.user.id }}/confirm" hx-target="closest tr" hx-swap="outerHTML" aria-label="Remove {{ e.user.display }} from the admin list">Remove</button>{% endif %}</td>
        </tr>
      {% endfor %}
      </tbody>
    </table></div>
    {% else %}
    <p class="empty">No admins yet.</p>
    {% endif %}
    {% if can_edit %}
    <form class="add" hx-post="/m/access/admins" hx-target="#access" hx-swap="outerHTML">
      <label>Discord user ID<input name="user_id" inputmode="numeric" autocomplete="off" required></label>
      <label>Note (optional, kept in the audit log)<input name="note" maxlength="200" autocomplete="off"></label>
      <button type="submit" class="btn">Add admin</button>
    </form>
    {% endif %}
  </div>
</section>
```

Create `src/yokai_admin/modules/access/templates/access/_confirm.html`:

```html
<tr class="confirm">
  <td colspan="4">
    <div class="confirm-row">
      <span>Remove <strong>{{ entry.user.display }}</strong> ({{ entry.user.id }}) from the {{ list_name }}?</span>
      <button type="button" class="btn danger" hx-post="/m/access/{{ kind }}/{{ entry.user.id }}/remove" hx-target="#access" hx-swap="outerHTML">Remove</button>
      <button type="button" class="btn plain" hx-get="/m/access/lists" hx-target="#access" hx-swap="outerHTML">Cancel</button>
    </div>
  </td>
</tr>
```

Replace `src/yokai_admin/modules/access/__init__.py`:

```python
"""Access module: the blacklist and the admin list."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from yokai_admin.core.db import readonly
from yokai_admin.core.module import AdminModule, Tile, TileContext
from yokai_admin.modules.access.router import router


def access_tile(ctx: TileContext) -> Tile:
    """Dashboard readout: how many people are blacklisted and how many are admins."""
    with readonly(ctx.settings.db_path) as conn:
        blocked = int(conn.execute("SELECT COUNT(*) FROM blacklist;").fetchone()[0])
        admins = int(conn.execute("SELECT COUNT(*) FROM admins;").fetchone()[0])
    noun = "admin" if admins == 1 else "admins"
    return Tile(title="Access lists", value=f"{blocked} blacklisted, {admins} {noun}")


MODULE = AdminModule(
    id="access",
    title="Access",
    router=router,
    nav_order=10,
    templates_dir=Path(__file__).parent / "templates",
    tile=access_tile,
)
```

(drop the unused `sqlite3` import if ruff flags it.) Add `"yokai_admin.modules.access:MODULE"` to `MODULE_PATHS` in `src/yokai_admin/modules/__init__.py`.

Append to `src/yokai_admin/static/panel.css`:

```css
/* Pages with forms and tables (Access, Audit) */

.page-title {
  font-stretch: 62%;
  font-weight: 800;
  font-size: clamp(2.25rem, 5vw, 3.25rem);
  line-height: 1;
}

.lede {
  max-width: 40rem;
  margin: 0.5rem 0 2rem;
  color: var(--slate);
}

.section-title {
  font-size: 1.5rem;
}

.hint {
  max-width: 40rem;
  margin: 0.25rem 0 1rem;
  color: var(--slate);
}

.table-wrap {
  margin-bottom: 1.25rem;
  overflow-x: auto;
}

.table {
  width: 100%;
  border-collapse: collapse;
}

.table th {
  padding: 0.5rem 0.75rem 0.5rem 0;
  font-weight: 400;
  text-align: left;
  color: var(--slate);
  border-bottom: 1px solid var(--line);
}

.table td {
  padding: 0.7rem 0.75rem 0.7rem 0;
  vertical-align: top;
  overflow-wrap: anywhere;
  border-bottom: 1px solid var(--line);
}

.who strong {
  font-weight: 600;
}

.who .id {
  display: block;
  font-size: 0.8125rem;
  color: var(--slate);
}

.table .actions {
  text-align: right;
  white-space: nowrap;
}

.flagged {
  display: block;
  font-size: 0.8125rem;
  color: var(--amber);
}

.confirm td {
  background: color-mix(in srgb, var(--flare) 8%, transparent);
}

.confirm-row {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem 1rem;
  align-items: center;
}

.add {
  display: grid;
  grid-template-columns: minmax(12rem, 16rem) minmax(14rem, 1fr) auto;
  gap: 0.75rem 1rem;
  align-items: end;
  max-width: 56rem;
}

.add label,
.filters label {
  display: grid;
  gap: 0.25rem;
  font-size: 0.8125rem;
  color: var(--slate);
}

.add input,
.filters input,
.filters select {
  min-width: 0;
  padding: 0.55rem 0.7rem;
  font: inherit;
  color: var(--ink);
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 0.375rem;
}

.btn {
  padding: 0.55rem 1rem;
  font: inherit;
  font-weight: 600;
  color: var(--hull);
  cursor: pointer;
  background: var(--sonar);
  border: 1px solid transparent;
  border-radius: 0.375rem;
}

.btn:hover {
  filter: brightness(1.1);
}

.btn.danger {
  background: var(--flare);
}

.btn.plain {
  color: var(--ink);
  background: transparent;
  border-color: var(--line);
}

.link-danger {
  padding: 0.25rem 0;
  font: inherit;
  color: var(--flare);
  cursor: pointer;
  background: none;
  border: 0;
  text-decoration: underline;
  text-underline-offset: 0.18em;
}

.flash {
  margin: 0 0 1.5rem;
  padding: 0.6rem 1rem;
  border-left: 3px solid var(--sonar);
}

.flash[data-kind="error"] {
  border-left-color: var(--flare);
}

@media (max-width: 48rem) {
  .add {
    grid-template-columns: 1fr;
  }
}
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_admin_access.py tests/test_admin_static.py tests/test_admin_app.py tests/test_admin_dashboard.py -q`
Expected: PASS. If the `"(5)" not in page...` assertion in the first test proves brittle against the markup, replace it with an assertion that the row has the name in a `<strong>` and the ID in a `class="id"` span; keep the intent (name and ID shown separately).

- [ ] **Step 5: Commit**

```powershell
git add src/yokai_admin tests
git commit -m "feat(admin): add the Access module with audited blacklist and admin editing" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Audit module

**Files:**
- Create: `src/yokai_admin/modules/audit/__init__.py`, `service.py`, `router.py`, `templates/audit/page.html`
- Modify: `src/yokai_admin/modules/__init__.py`
- Test: `tests/test_admin_audit.py`

**Interfaces:**
- Consumes: `list_entries`, `modules_seen`, `latest_ts` (Task 4); `user_refs`; `describe_age`; Access writers (to create realistic entries in tests).
- Produces: `MODULE = AdminModule(id="audit", title="Audit log", nav_order=20, tile=audit_tile, ...)`; `GET /m/audit?module=&target=&page=` (read-only, plain GET form). `service.PAGE_SIZE = 25`; `service.action_label(action: str) -> str` (`blacklist.add` is "Added to the blacklist", `blacklist.remove` "Removed from the blacklist", `admin.add` "Made an admin", `admin.remove` "Removed from the admin list"; anything else is shown as written); `service.detail_text(detail: dict) -> str` ("previous reason: spam"); `service.parse_page(raw: str) -> int` (anything not a positive integer gives 1). `audit_tile(ctx) -> Tile("Last admin action", "<n> ago" or "None yet")`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_admin_audit.py`:

```python
"""Tests for the Audit module."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from tests.admin_helpers import panel_client, seed_database
from yokai_admin.modules.access.service import add_admin, add_blacklist, remove_blacklist
from yokai_admin.modules.audit.service import action_label, detail_text, parse_page

AUDIT = "yokai_admin.modules.audit:MODULE"
OWNER = 111


def _history(db: Path) -> None:
    seed_database(db, names={42: "Mallory", 7: "Alex"})
    add_blacklist(db, owner_id=OWNER, raw_id="42", raw_reason="spamming the queue", now=1_000)
    add_admin(db, owner_id=OWNER, raw_id="7", raw_note="helps moderate", now=2_000)
    remove_blacklist(db, raw_id="42", now=3_000)


def test_the_log_lists_entries_newest_first_with_names_and_reasons(tmp_path: Path) -> None:
    db = tmp_path / "yokai.db"
    _history(db)
    with panel_client(tmp_path, [AUDIT], db_path=db) as (client, _):
        page = client.get("/m/audit").text
    assert page.index("Removed from the blacklist") < page.index("Made an admin") < page.index("Added to the blacklist")
    assert "Mallory" in page and "Alex" in page and "spamming the queue" in page
    assert "helps moderate" in page and "previous reason: spamming the queue" in page
    assert 'aria-current="page">Audit log</a>' in page


def test_filters_narrow_the_log_and_the_form_keeps_its_values(tmp_path: Path) -> None:
    db = tmp_path / "yokai.db"
    _history(db)
    with panel_client(tmp_path, [AUDIT], db_path=db) as (client, _):
        mine = client.get("/m/audit", params={"module": "access", "target": "7"}).text
        none = client.get("/m/audit", params={"module": "nothing"}).text
    assert "Made an admin" in mine and "Added to the blacklist" not in mine
    assert 'value="7"' in mine
    assert "No matching entries." in none


def test_paging_shows_older_and_newer_links(tmp_path: Path) -> None:
    db = tmp_path / "yokai.db"
    seed_database(db)
    for i in range(1, 28):  # 27 entries: one full page of 25 plus 2
        add_admin(db, owner_id=OWNER, raw_id=str(1000 + i), raw_note="", now=i)
    with panel_client(tmp_path, [AUDIT], db_path=db) as (client, _):
        first = client.get("/m/audit").text
        second = client.get("/m/audit", params={"page": "2"}).text
    assert "page=2" in first and "Older" in first and "Newer" not in first
    assert "Newer" in second and "Older" not in second


@pytest.mark.parametrize("raw", ["", "abc", "0", "-3", "1.5", "99999999999999999999"])
def test_a_bad_page_number_falls_back_to_the_first_page(tmp_path: Path, raw: str) -> None:
    db = tmp_path / "yokai.db"
    _history(db)
    assert parse_page(raw) == 1
    with panel_client(tmp_path, [AUDIT], db_path=db) as (client, _):
        assert client.get("/m/audit", params={"page": raw}).status_code == 200


def test_hostile_text_in_an_entry_is_escaped(tmp_path: Path) -> None:
    db = tmp_path / "yokai.db"
    seed_database(db, names={42: "<img src=x onerror=alert(1)>"})
    add_blacklist(db, owner_id=OWNER, raw_id="42", raw_reason="<script>alert(1)</script> bad", now=1)
    with panel_client(tmp_path, [AUDIT], db_path=db) as (client, _):
        page = client.get("/m/audit").text
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page
    assert "<img src=x" not in page


def test_an_empty_log_and_a_missing_database_both_render(tmp_path: Path) -> None:
    db = tmp_path / "yokai.db"
    seed_database(db)
    with panel_client(tmp_path, [AUDIT], db_path=db) as (client, _):
        assert "No admin actions have been recorded yet." in client.get("/m/audit").text
    with panel_client(tmp_path, [AUDIT], db_path=tmp_path / "absent.db") as (client, _):
        page = client.get("/m/audit")
        assert page.status_code == 200 and "not found" in page.text


def test_the_audit_log_is_read_only(tmp_path: Path) -> None:
    db = tmp_path / "yokai.db"
    seed_database(db)
    with panel_client(tmp_path, [AUDIT], db_path=db) as (client, csrf):
        for method in ("post", "put", "delete"):
            response = getattr(client, method)("/m/audit", headers={"X-CSRF-Token": csrf})
            assert response.status_code == 405


def test_helpers_describe_actions_and_details_plainly() -> None:
    assert action_label("blacklist.add") == "Added to the blacklist"
    assert action_label("something.new") == "something.new"
    assert detail_text({"name": "Sami", "previous_reason": "spam"}) == "previous reason: spam"
    assert detail_text({"raw": "{oops"}) == "raw: {oops"
    assert detail_text({}) == ""


def test_the_dashboard_tile_shows_how_long_ago_the_last_action_was(tmp_path: Path) -> None:
    db = tmp_path / "yokai.db"
    seed_database(db, heartbeat_ts=int(time.time()))
    paths = ["yokai_admin.modules.dashboard:MODULE", AUDIT]
    with panel_client(tmp_path, paths, db_path=db) as (client, _):
        assert "None yet" in client.get("/m/dashboard").text
    add_admin(db, owner_id=OWNER, raw_id="7", raw_note="", now=int(time.time()) - 120)
    with panel_client(tmp_path, paths, db_path=db) as (client, _):
        page = client.get("/m/dashboard").text
    assert "Last admin action" in page and "2 minutes ago" in page
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_admin_audit.py -q`
Expected: FAIL (`ModuleNotFoundError: yokai_admin.modules.audit`).

- [ ] **Step 3: Implement**

`src/yokai_admin/modules/audit/service.py`:

```python
"""Audit page helpers: plain-language labels, paging and the view model."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Optional

from yokai_admin.core.audit import AuditEntry, list_entries, modules_seen
from yokai_admin.core.people import UserRef, user_refs

PAGE_SIZE = 25
_LABELS = {
    "blacklist.add": "Added to the blacklist",
    "blacklist.remove": "Removed from the blacklist",
    "admin.add": "Made an admin",
    "admin.remove": "Removed from the admin list",
}


def action_label(action: str) -> str:
    """Plain wording for known actions; anything else is shown exactly as recorded."""
    return _LABELS.get(action, action)


def detail_text(detail: dict[str, Any]) -> str:
    """Compact text for the interesting details (the person's name is already in the Target column)."""
    parts = [f"{key.replace('_', ' ')}: {value}" for key, value in detail.items() if key != "name" and value is not None]
    return ", ".join(parts)


def parse_page(raw: str) -> int:
    """A positive page number, or 1 for anything else (empty, text, zero, negative, absurd)."""
    text = raw.strip()
    if not (text.isascii() and text.isdigit()):
        return 1
    value = int(text)
    return value if 0 < value <= 100_000 else 1


@dataclass(frozen=True, slots=True)
class Row:
    """One audit entry ready to display."""

    entry: AuditEntry
    label: str
    person: Optional[UserRef]
    details: str


@dataclass(frozen=True, slots=True)
class AuditPage:
    rows: list[Row]
    total: int
    page: int
    has_older: bool
    has_newer: bool
    modules: list[str]


def build_page(conn: sqlite3.Connection, *, module: str, target: str, page: int) -> AuditPage:
    entries, total = list_entries(
        conn, module=module or None, target=target or None, limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE
    )
    ids = [int(e.target) for e in entries if e.module == "access" and e.target and e.target.isascii() and e.target.isdigit()]
    refs = user_refs(conn, ids)
    rows = [
        Row(
            entry=e,
            label=action_label(e.action),
            person=refs.get(int(e.target)) if e.module == "access" and e.target and e.target.isascii() and e.target.isdigit() else None,
            details=detail_text(e.detail),
        )
        for e in entries
    ]
    return AuditPage(rows, total, page, page * PAGE_SIZE < total, page > 1, modules_seen(conn))
```

`src/yokai_admin/modules/audit/router.py`:

```python
"""Audit routes: a read-only, filterable, paged view of the admin log."""

from __future__ import annotations

import logging
import sqlite3
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import Response

from yokai_admin.core.db import readonly, require_schema
from yokai_admin.core.templating import render
from yokai_admin.errors import PanelDbError
from yokai_admin.modules.audit.service import AuditPage, build_page, parse_page

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("")
def page(request: Request, module: str = "", target: str = "", page: str = "1") -> Response:
    settings = request.app.state.settings
    number = parse_page(page)
    view: Optional[AuditPage] = None
    db_error: Optional[str] = None
    try:
        with readonly(settings.db_path) as conn:
            require_schema(conn)
            view = build_page(conn, module=module.strip(), target=target.strip(), page=number)
    except PanelDbError as exc:
        db_error = str(exc)
    except sqlite3.Error as exc:
        logger.warning("Audit could not read the database: %s", exc)
        db_error = f"The database could not be read right now ({exc}). Reload in a moment."
    return render(
        request, "audit/page.html", active="audit", view=view, db_error=db_error,
        module=module.strip(), target=target.strip(), page_number=number,
    )
```

`src/yokai_admin/modules/audit/templates/audit/page.html`:

```html
{% extends "base.html" %}
{% block title %}Audit log - Yokai admin{% endblock %}
{% block content %}
  <h1 class="page-title">Audit log</h1>
  <p class="lede">Every change made from this panel, newest first.</p>
  {% if db_error %}<p class="flash" data-kind="error" role="alert"><span class="sr-only">Problem: </span>{{ db_error }}</p>{% endif %}
  {% if view %}
  <form class="filters" method="get" action="/m/audit">
    <label>Module
      <select name="module">
        <option value="">All modules</option>
        {% for name in view.modules %}<option value="{{ name }}"{% if name == module %} selected{% endif %}>{{ name }}</option>{% endfor %}
      </select>
    </label>
    <label>Target (Discord user ID)<input name="target" value="{{ target }}" inputmode="numeric" autocomplete="off"></label>
    <button type="submit" class="btn">Filter</button>
    <a href="/m/audit">Clear</a>
  </form>

  {% if view.rows %}
  <div class="table-wrap"><table class="table">
    <thead><tr><th scope="col">When</th><th scope="col">Action</th><th scope="col">Target</th><th scope="col">Reason</th><th scope="col">Details</th></tr></thead>
    <tbody>
    {% for row in view.rows %}
      <tr>
        <td>{{ row.entry.ts|when }}</td>
        <td>{{ row.label }}</td>
        <td class="who">{% if row.person %}<strong>{{ row.person.display }}</strong><span class="id">{{ row.person.id }}</span>{% else %}{{ row.entry.target or "-" }}{% endif %}</td>
        <td>{{ row.entry.reason or "-" }}</td>
        <td>{{ row.details }}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table></div>
  <p class="pager">
    {% if view.has_newer %}<a href="/m/audit?module={{ module|urlencode }}&amp;target={{ target|urlencode }}&amp;page={{ view.page - 1 }}">Newer</a>{% endif %}
    {% if view.has_older %}<a href="/m/audit?module={{ module|urlencode }}&amp;target={{ target|urlencode }}&amp;page={{ view.page + 1 }}">Older</a>{% endif %}
  </p>
  {% elif module or target %}
  <p class="empty">No matching entries.</p>
  {% else %}
  <p class="empty">No admin actions have been recorded yet.</p>
  {% endif %}
  {% endif %}
{% endblock %}
```

Add to `panel.css`:

```css
.filters {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem 1rem;
  align-items: end;
  margin-bottom: 1.5rem;
}

.pager {
  display: flex;
  gap: 1.5rem;
}
```

`src/yokai_admin/modules/audit/__init__.py`:

```python
"""Audit module: a read-only view of everything changed from the panel."""

from __future__ import annotations

import time
from pathlib import Path

from yokai_admin.core.audit import latest_ts
from yokai_admin.core.db import readonly
from yokai_admin.core.formatting import describe_age
from yokai_admin.core.module import AdminModule, Tile, TileContext
from yokai_admin.modules.audit.router import router


def audit_tile(ctx: TileContext) -> Tile:
    """Dashboard readout: how long ago the panel last changed something."""
    with readonly(ctx.settings.db_path) as conn:
        last = latest_ts(conn)
    if last is None:
        return Tile(title="Last admin action", value="None yet")
    return Tile(title="Last admin action", value=f"{describe_age(max(0, ctx.now - last))} ago")


MODULE = AdminModule(
    id="audit",
    title="Audit log",
    router=router,
    nav_order=20,
    templates_dir=Path(__file__).parent / "templates",
    tile=audit_tile,
)
```

(drop the unused `time` import if ruff flags it.) Add `"yokai_admin.modules.audit:MODULE"` to `MODULE_PATHS`.

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_admin_audit.py tests/test_admin_access.py tests/test_admin_dashboard.py -q`
Expected: PASS. If the paging link assertion is brittle, assert on the presence of the `page=2` link on page one and the absence of "Older" on page two, which are the behaviors that matter.

- [ ] **Step 5: Commit**

```powershell
git add src/yokai_admin tests/test_admin_audit.py
git commit -m "feat(admin): add the Audit module" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Docs, visual check and full verification

**Files:**
- Modify: `SPEC.md` (§11), `README.md`, `TASKS.md`, `docs/superpowers/specs/2026-10-05-admin-panel-design.md`

- [ ] **Step 1: Update the docs**

- `SPEC.md` §11: change the admin-panel dependency clause to ``fastapi``, ``uvicorn``, ``jinja2``, ``python-multipart``, add `OWNER_ID` to the note that the panel also reads it (`Owner-only commands; the panel uses it to protect the owner`), and mention schema v5 in any schema list.
- Design spec: in §5 add the `user_names` table and the sentence "Display names are cached by the bot at most once a day per person; the panel only reads them." In §7 Access replace "Discord IDs validated as positive integers" with the real rules (ASCII digits, owner guard, reason 3-200 characters, blacklisted people cannot be made admins, blacklisting an admin keeps the admin row) and note "Admin notes are kept in the audit log only". In the "Non-goals" list remove "Discord username lookup" (now cached by the bot).
- `README.md` Admin panel section: add one sentence that Access and Audit are available and that `OWNER_ID` in `.env` enables editing.
- `TASKS.md` Phase 9: tick P-A3 and add the P-A3 acceptance criteria below.

```markdown
**Acceptance criteria (P-A3)**
- Schema is v5; the bot remembers each person's display name (at most one write a day per person)
  and a failing name write never affects a command.
- Access: blacklist and admin list can be added to and removed from. A blacklist add needs a reason
  (3-200 characters); every removal asks for confirmation; the owner can never be added to either
  list however the ID is typed; a blacklisted person cannot be made an admin; blacklisting an admin
  keeps the admin row and flags both lists.
- Every change and its audit entry commit together or not at all; two simultaneous adds leave one
  row; a busy database gives a friendly message.
- Without OWNER_ID the panel still starts, the Access page shows a note and no forms, and the
  server refuses edits.
- The bot honours a panel change within a few seconds, without a restart.
- Audit shows entries newest first with names, reasons and details, filters by module and person,
  and pages by 25; both modules add a Dashboard readout.
- Hostile names, reasons and details are escaped; write endpoints need a session and the CSRF token.
- `pytest`, `ruff check` and `ruff format --check` are clean.
- *(manual, real browser and real bot)* Add a test account to the blacklist from the panel; within
  about five seconds that account is refused on every command and button; remove it and access
  returns. Make an account an admin and confirm `/diag` works for it. Try to add your own ID to
  each list. Confirm the Audit page lists each step with names. Stop the bot, change a list,
  restart the bot and confirm it is honoured.
```

- [ ] **Step 2: Look at the real pages**

With the QA technique from P-A2 (a throwaway server script outside the repo that seeds a database and serves the panel on a free port; headless Edge screenshots with `--virtual-time-budget`), take screenshots of Access (with entries, with an error flash, with the confirm row open, owner-less notice) and Audit (with entries, filtered) in dark and light at 1280 px and 500 px. Check left alignment with the P-A2 pages, that the form fields and buttons read clearly, that nothing clips at 500 px, and that the confirm row is unmistakable. Fix any CSS problems in a separate `fix(admin):` commit with a note of what was found. Delete the throwaway files and stop the servers afterwards.

- [ ] **Step 3: Run the full verification**

```powershell
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
```

Expected: all green. Report any failure by name; do not mark P-A3 done on a red run.

- [ ] **Step 4: Commit and stop for owner review**

```powershell
git add SPEC.md README.md TASKS.md docs/superpowers/specs/2026-10-05-admin-panel-design.md
git commit -m "docs(admin): record admin panel phase P-A3" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

Then summarize what changed and list what is unverified (the manual real-bot checks above), per `AGENTS.md`, remind the owner that `pip install -e ".[dev]"` must be re-run inside `.venv` (new dependency), and wait for review. Do not start P-A4.

---

## Self-review

**Spec coverage (§5, §7 Access and Audit, §8, §11 P-A3):** blacklist and adminlist add/remove with reasons: Tasks 5-6. Owner rejection and ID validation: Task 5. Audit table written for every change, read-only Audit view filterable by module and target: Tasks 4, 5, 7. Display names (owner decision, spec delta): Tasks 1-2 and 4. Dashboard readouts through the tile contract: Tasks 6-7. Cross-process write rule: Task 3 (`write_transaction`) and Task 5 tests. Schema/`MIN_SCHEMA_VERSION` rise and hard-coded-version fixes: Tasks 1 and 3. CSRF and session on writes: Task 6 tests (generic middleware already enforces it). `python-multipart`: Task 3. Folded-in P-A2 minors (env leak in `main()` tests, dependency lower bounds): Task 3.

**Placeholders:** none. Two steps give an explicit fallback when a test assertion proves brittle against markup (Tasks 6 and 7); both name the behavior to keep.

**Type consistency:** `PanelSettings.owner_id: Optional[int]`; `write_transaction(db_path, *, busy_timeout_s=None)`; `WRITE_BUSY_TIMEOUT_S` read at call time; `UserRef(id, name)` with `.display` / `.label`; `Entry(user, text, added_ts, added_by, flag)`; service writers return `UserRef`; `AuditEntry` fields; `UserNameCache.touch(user_id, raw_name)`; `screen(interaction) -> bool`; `MODULE_PATHS` order does not matter (nav sorts by `nav_order`: dashboard 0, access 10, audit 20).

**Review Focus coverage:** 1 to Task 5 (parametrized IDs including non-ASCII digits and owner spellings); 2 to Task 5 (rollback and two-thread tests); 3 to Tasks 2, 5, 6, 7; 4 to Tasks 3 and 6; 5 to Tasks 2, 3 and 5; 6 to Task 6.

**Known risks for the implementer:** (a) Task 1 intentionally leaves the panel's dashboard tests red until Task 3 raises `MIN_SCHEMA_VERSION`; run only the named files until then. (b) Task 3 must re-run `pip install -e ".[dev]"` for `python-multipart`; routes using `Form` fail at import time without it. (c) `str.replace("{who}", ...)` is used for flash messages on purpose, so a name containing braces cannot break a format string. (d) The two-thread test depends on SQLite lock waiting (5 s); if it times out on a slow machine, raise its join timeout, not its assertions.
