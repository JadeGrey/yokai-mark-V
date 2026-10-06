# Admin Panel P-A4: Users (People) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A People page where the owner can find anyone the bot knows, read their economy, music and command history, correct a balance, and reset a daily cooldown, with every change on the ledger and in the audit log, and with the bot's own wallet code made safe against a second writer.

**Architecture:** Task 1 first hardens the bot: the three wallet paths that read, compute, then write (`admin_adjust_wallet`, `claim_daily`, `pay`) start with `BEGIN IMMEDIATE`, so the panel can never slip a write into the gap. The panel then gains a People module: a read side (list with search, profile aggregates) and a write side (balance adjustment with preview, confirm and a stale-preview check; daily reset) built on the P-A3 `write_transaction`. HTMX swaps three small regions of one profile section; refusals come back as HTTP 200 fragments, as in Access.

**Tech Stack:** Python 3.12+, FastAPI/Starlette, Jinja2, HTMX 2.0.11 (vendored), stdlib `sqlite3` (panel), aiosqlite (bot), pytest, ruff. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-05-admin-panel-design.md` (§7 Users, §8 security, §10 testing, §11 phase P-A4, and the cross-process write rule added after the P-A1 review). P-A1 to P-A3 are merged (schema v5).

## Owner decisions this plan implements

1. **Fix the bot's read-then-write races now** (Task 1), not just on the panel side.
2. **List plus search** by name or ID, not ID-only (spec delta).
3. **Adjustments:** a required reason, a preview with a confirm step, and a **100,000 cap** per adjustment. Deductions never take a balance below 0 (same as the bot's `/economy-admin`).

## What the race audit found (the reason for Task 1)

The bot's single connection reads a wallet *outside* a write transaction and writes later, so another process can commit in between:
- `admin_adjust_wallet` writes an **absolute** balance computed from the earlier read: a panel edit in the gap is silently **overwritten** (a lost update).
- `claim_daily` and `pay` use relative updates but record `balance_after` computed from the earlier read: a panel edit in the gap leaves a **wrong number on the ledger**.
- `credit_wallet`, `place_bet` and `_finish_bet` re-read after their update (inside the transaction) and are already correct.

The fix is minimal: `BEGIN IMMEDIATE` at the top of those three paths (through one helper that also rolls back on any error, so a failure can never leave the write lock held). The tests inject a panel write into the gap (after the bot's read, before its write) and assert the ledger invariant, so they fail before the fix and pass after.

## Deltas and carried requirements

- **Daily reset keeps the streak.** Setting `last_daily_ts` to NULL would make the bot treat the next claim as day 1. The panel instead back-dates the last claim to exactly 24 hours ago: the cooldown is clear and the streak continues (+1) on the next claim. Refused when the person never claimed, or can already claim.
- **No wallet, no adjustment.** The bot creates a wallet lazily (with the welcome bonus from its config); the panel does not know that setting, so it refuses to adjust someone with no wallet and says why.
- **Stale-preview check.** The confirm step carries the balance the preview saw; the write re-reads inside its `BEGIN IMMEDIATE` transaction and refuses if it changed.
- The module is named **People** in the nav (id `users`, as in the spec).
- **Folded in from the P-A3 deferred minors** because the new money forms need them: refused forms keep what was typed (Access gets the same), and a generic "that request failed" notice for unexpected htmx failures (a silent 500 on a balance confirm would be dangerous).
- **Not** folded in (still deferred): the invisible-character filter refinements, `format_when` hardening (P-A5 restore), the old-schema tile inconsistency, the phone Remove-link position.

## Global Constraints

- Python 3.12+; every module starts with `from __future__ import annotations`; fully type-hinted; dataclasses for models.
- `logging`, no `print`, no bare `except`. SQL is parameterized; f-string SQL fragments only from fixed text or `?` placeholder lists.
- Every panel write goes through `write_transaction` and records an audit row in the same transaction. In this phase the panel writes `wallets`, `renown_transactions`, `admin_audit` only.
- Ledger invariant: after any operation, `wallets.balance` equals the sum of that person's `renown_transactions.delta`, and the newest `balance_after` equals the balance (for ledgers that started consistent).
- The panel stays loopback-only and offline; no new inline scripts/styles (CSP). Hostile text is escaped by Jinja autoescape; user text never goes into an HTML attribute without autoescape.
- Panel copy is plain, sentence case, no emoji; refusals say what happened and what to do.
- Never commit `.env`, tokens, databases or cookie files. Commit per task. No new dependencies.
- Verification (PowerShell, repo root): `python -m pytest -q`, `python -m ruff check .`, `python -m ruff format --check .`.
- Git commits end with: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`

## Verified facts this plan relies on

- `src/yokai/storage/db.py`: one `aiosqlite` connection with Python's legacy transaction control; `Database._write_lock` is in-process only; wallet methods call `await self.conn.commit()`/`rollback()`; `admin_adjust_wallet`, `claim_daily`, `pay` start with `async with self._write_lock:` and read via `get_wallet` (a plain SELECT, no transaction). SQLite 3.50.4 here; Python 3.14.
- `src/yokai/economy/daily.py`: `DAILY_COOLDOWN_S = 86400`; a claim is allowed when `elapsed >= 86400`; the streak continues when `elapsed < 172800`, else restarts at 1; `last_daily_ts is None` means "claim allowed, streak 1".
- Ledger rows: `renown_transactions(id, ts, user_id, delta, reason, counterparty_id, balance_after)`; the bot uses reason `admin` for `/economy-admin`; other reasons are `welcome`, `daily`, `voice`, `command`, `pay_sent`, `pay_received`, and `<prefix>:<game>` for bets (`bet`, `win`, `push`, `payout`, `refund`).
- Panel pieces to reuse: `write_transaction`, `readonly`, `require_schema` (`core/db.py`); `audit.record` / `list_entries` (`core/audit.py`); `user_refs`, `UserRef` (`core/people.py`); `format_when` and the `when` filter, `render`, `render_fragment` (`core/templating.py`); `AdminModule`, `Tile` (`core/module.py`); the Access module as the pattern for routers, flash lines and forms; `panel_client`, `post`, `seed_database` in `tests/admin_helpers.py`.
- htmx 2.0.11 swaps only 2xx responses by default, supports `HX-Retarget` / `HX-Reswap` response headers and `autofocus` after a swap.
- Python's `int()` accepts non-ASCII digits and raises past 4300 digits, so every parser checks `isascii()`, `isdigit()` and length before converting.

## Review Focus

Failure modes the spec implies but happy-path tests would miss, most likely first. Each has a test in the task named.

1. **Ledger integrity:** every panel balance change writes the wallet, the ledger row and the audit row atomically, and after any mix of panel and bot operations the ledger invariant holds (Tasks 1 and 4).
2. **Races:** a panel edit landing in the middle of a bot admin-adjust, daily claim or payment cannot be lost or leave a wrong `balance_after`; a bot operation that is refused or fails never leaves the write lock held; two panel adjustments confirmed from the same preview apply exactly once (Tasks 1 and 4).
3. **Amount parsing and the cap:** `+500`, `-0`, `0`, empty, a Unicode minus, full-width or Arabic digits, `1e3`, `1,000`, 100,001, a 5000-digit string (Task 4).
4. **Clamping:** deducting more than the balance ends at 0, the preview says so, the ledger records the applied amount (not the requested one), and deducting from 0 is refused (Task 4).
5. **Hostile text and search:** names, track titles, artists, command names and reasons are escaped; a search for `%`, `_` or `\` is literal, not a wildcard (Tasks 3 and 5).
6. **Unknown or broken input:** unknown ID, no wallet, missing or old database, non-numeric or huge ID, all answer with a message, never a 500; write endpoints are locked without a session and a CSRF token; GET routes change nothing; an unexpected failed request shows a visible notice (Tasks 2 and 5).

---

## File structure

| File | Responsibility |
|---|---|
| `src/yokai/storage/db.py` (modify) | `_write_tx` helper; used by `admin_adjust_wallet`, `claim_daily`, `pay` |
| `src/yokai_admin/core/inputs.py` (create) | `InputRefused`, `parse_user_id`, `clean_reason` (moved from Access) |
| `src/yokai_admin/core/formatting.py`, `templating.py` (modify) | `format_number`, `format_signed`; `number` and `signed` filters |
| `src/yokai_admin/modules/access/` (modify) | Import the shared helpers; keep typed values after a refusal |
| `src/yokai_admin/templates/base.html`, `static/panel.js`, `panel.css` (modify) | Generic "request failed" notice; new styles |
| `src/yokai_admin/modules/users/service.py` (create) | Read side: `list_people`, `load_profile`, `reason_label` |
| `src/yokai_admin/modules/users/actions.py` (create) | Write side: `parse_amount`, `preview_adjustment`, `adjust_balance`, `reset_daily` |
| `src/yokai_admin/modules/users/router.py`, `__init__.py`, `templates/users/*` (create) | Routes, templates, tile, `MODULE` |
| `src/yokai_admin/modules/audit/service.py`, `modules/__init__.py` (modify) | Labels for new actions, names for `users` targets; register the module |
| `tests/test_economy_races.py` (create) | Bot-side race tests with an injected panel write |
| `tests/test_admin_*.py`, `tests/admin_helpers.py` (create/modify) | Panel tests and seed helpers |
| `SPEC.md` is untouched; `README.md`, `TASKS.md`, design spec (modify) | Docs |

---

### Task 1: Make the bot's wallet read-modify-write paths race-safe

**Files:**
- Modify: `src/yokai/storage/db.py`
- Test: `tests/test_economy_races.py`

**Interfaces:**
- Produces: `Database._write_tx()`, an async context manager: takes `_write_lock`, runs `BEGIN IMMEDIATE`, yields, and on any exception rolls back before re-raising (callers still `commit()` on success, exactly as today). `admin_adjust_wallet`, `claim_daily` and `pay` use it instead of `async with self._write_lock:`; no other method changes.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_economy_races.py`:

```python
"""Race tests: a second process (the admin panel) writing a wallet in the middle of a bot
operation must never be lost or leave a wrong balance_after on the ledger."""

from __future__ import annotations

import asyncio
import random
import sqlite3
from pathlib import Path
from typing import AsyncIterator

import pytest

from yokai.errors import DailyOnCooldownError, InsufficientFundsError
from yokai.storage.db import Database

WELCOME = 200


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    database = Database(tmp_path / "t.db")
    await database.initialize()
    try:
        yield database
    finally:
        await database.close()


def _panel_edit(path: Path, user_id: int, delta: int, timeout_s: float = 0.3) -> str:
    """What the panel does for a balance edit: take the write lock, change the wallet, add the
    ledger row, commit. Returns 'blocked' if the write lock could not be had in time."""
    conn = sqlite3.connect(path, timeout=timeout_s, isolation_level=None)
    try:
        try:
            conn.execute("BEGIN IMMEDIATE;")
        except sqlite3.OperationalError:
            return "blocked"
        conn.execute("UPDATE wallets SET balance = balance + ? WHERE user_id = ?;", (delta, user_id))
        after = conn.execute("SELECT balance FROM wallets WHERE user_id = ?;", (user_id,)).fetchone()[0]
        conn.execute(
            "INSERT INTO renown_transactions (ts, user_id, delta, reason, counterparty_id, "
            "balance_after) VALUES (1, ?, ?, 'admin', NULL, ?);",
            (user_id, delta, after),
        )
        conn.execute("COMMIT;")
        return "applied"
    finally:
        conn.close()


def _ledger_is_true(path: Path, user_id: int) -> bool:
    conn = sqlite3.connect(path)
    try:
        balance = conn.execute("SELECT balance FROM wallets WHERE user_id = ?;", (user_id,)).fetchone()[0]
        total = conn.execute(
            "SELECT COALESCE(SUM(delta), 0) FROM renown_transactions WHERE user_id = ?;", (user_id,)
        ).fetchone()[0]
        last = conn.execute(
            "SELECT balance_after FROM renown_transactions WHERE user_id = ? ORDER BY id DESC LIMIT 1;",
            (user_id,),
        ).fetchone()[0]
        return bool(balance == total == last)
    finally:
        conn.close()


async def _funded(db: Database, user_id: int, extra: int) -> None:
    await db.ensure_wallet(user_id, WELCOME)
    await db.credit_wallet(user_id, extra, "daily", WELCOME)


def _inject_panel_edit(
    db: Database, monkeypatch: pytest.MonkeyPatch, path: Path, user_id: int, delta: int
) -> list[str]:
    """Run a panel edit right after the bot's first read of this wallet, before its write."""
    outcome: list[str] = []
    original = db.get_wallet

    async def hooked(uid: int):  # type: ignore[no-untyped-def]
        wallet = await original(uid)
        if uid == user_id and not outcome:
            outcome.append(_panel_edit(path, user_id, delta))
        return wallet

    monkeypatch.setattr(db, "get_wallet", hooked)
    return outcome


async def test_an_admin_adjust_cannot_overwrite_a_panel_edit_made_mid_operation(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _funded(db, 1, 300)  # balance 500
    outcome = _inject_panel_edit(db, monkeypatch, tmp_path / "t.db", 1, 500)

    await db.admin_adjust_wallet(1, 100, WELCOME)

    assert outcome == ["blocked"]  # the panel had to wait; it could not slip in between
    assert _ledger_is_true(tmp_path / "t.db", 1)
    wallet = await db.get_wallet(1)
    assert wallet is not None and wallet.balance == 600


async def test_a_daily_claim_records_the_true_balance_when_the_panel_edits_mid_claim(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _funded(db, 1, 300)
    outcome = _inject_panel_edit(db, monkeypatch, tmp_path / "t.db", 1, 500)

    await db.claim_daily(1, 100, WELCOME, now=2_000_000, rng=random.Random(1))

    assert outcome == ["blocked"]
    assert _ledger_is_true(tmp_path / "t.db", 1)


async def test_a_payment_records_the_true_balances_when_the_panel_edits_mid_payment(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _funded(db, 1, 300)
    await _funded(db, 2, 50)
    outcome = _inject_panel_edit(db, monkeypatch, tmp_path / "t.db", 1, 500)

    await db.pay(1, 2, 100, WELCOME)

    assert outcome == ["blocked"]
    assert _ledger_is_true(tmp_path / "t.db", 1) and _ledger_is_true(tmp_path / "t.db", 2)


async def test_a_refused_bot_operation_does_not_leave_the_write_lock_held(
    db: Database, tmp_path: Path
) -> None:
    """Review Focus 2: refusals and failures must release SQLite's write lock."""
    path = tmp_path / "t.db"
    await _funded(db, 1, 10)  # balance 210
    with pytest.raises(InsufficientFundsError):
        await db.pay(1, 2, 5_000, WELCOME)
    assert _panel_edit(path, 1, 1) == "applied"

    await db.claim_daily(1, 100, WELCOME, now=2_000_000, rng=random.Random(1))
    with pytest.raises(DailyOnCooldownError):
        await db.claim_daily(1, 100, WELCOME, now=2_000_001, rng=random.Random(1))
    assert _panel_edit(path, 1, 1) == "applied"


async def test_an_unexpected_failure_inside_a_wallet_operation_releases_the_lock(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "t.db"
    await _funded(db, 1, 10)

    async def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("ledger insert failed")

    monkeypatch.setattr(db, "_insert_transaction", broken)
    with pytest.raises(RuntimeError):
        await db.admin_adjust_wallet(1, 5, WELCOME)
    assert _panel_edit(path, 1, 1) == "applied"
    wallet = await db.get_wallet(1)
    assert wallet is not None and wallet.balance == 211  # the failed adjust left no trace


async def test_many_concurrent_bot_and_panel_operations_keep_every_ledger_true(
    db: Database, tmp_path: Path
) -> None:
    path = tmp_path / "t.db"
    for user_id in (1, 2, 3):
        await _funded(db, user_id, 1_000)

    async def panel_edits() -> None:
        for i in range(8):
            await asyncio.to_thread(_panel_edit, path, 1 + i % 3, 7, 5.0)

    bot_operations = [
        panel_edits(),
        *(db.pay(1 + i % 3, 1 + (i + 1) % 3, 10, WELCOME) for i in range(12)),
        *(db.admin_adjust_wallet(1 + i % 3, 5, WELCOME) for i in range(9)),
        *(db.credit_wallet(1 + i % 3, 3, "voice", WELCOME) for i in range(9)),
    ]
    await asyncio.gather(*bot_operations)

    assert all(_ledger_is_true(path, user_id) for user_id in (1, 2, 3))
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_economy_races.py -q`
Expected: the three "mid-operation" tests FAIL (the panel edit is `applied`, not `blocked`, and the ledger is wrong or the edit is lost); the release-the-lock tests may already pass (no explicit transaction yet).

- [ ] **Step 3: Implement**

In `src/yokai/storage/db.py` add `from contextlib import asynccontextmanager` and `AsyncIterator` (from `typing`) to the imports, and add this method to `Database` just above `_ensure_wallet`:

```python
    @asynccontextmanager
    async def _write_tx(self) -> AsyncIterator[None]:
        """Hold the in-process write lock *and* SQLite's write lock for a read-modify-write.

        `get_wallet` is a plain SELECT, so without this another process (the admin panel) could
        commit a change between the read and the write that depends on it. BEGIN IMMEDIATE takes
        the file's write lock before the first read. Any error rolls back, so a refusal or a
        failure can never leave the lock held; callers still `commit()` on success.
        """
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE;")
            try:
                yield
            except BaseException:
                await self.conn.rollback()
                raise
```

Then, **only in `admin_adjust_wallet`, `claim_daily` and `pay`**, replace the line `async with self._write_lock:` with `async with self._write_tx():`. (The same line appears in other methods; leave those unchanged.)

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_economy_races.py tests/test_economy_storage.py tests/test_gambling_storage.py tests/test_storage.py tests/test_economy_cog.py -q`
Expected: PASS. Then run the whole suite once (`python -m pytest -q`): a path that forgot to commit or roll back would now fail loudly with "cannot start a transaction within a transaction".

- [ ] **Step 5: Mutation-check, then commit**

Temporarily change `admin_adjust_wallet` back to `async with self._write_lock:` and confirm `test_an_admin_adjust_cannot_overwrite_a_panel_edit_made_mid_operation` fails; restore the file and confirm it is identical (`git diff` shows only the intended change). Then:

```powershell
git add src/yokai/storage/db.py tests/test_economy_races.py
git commit -m "fix(economy): take the write lock before reading a wallet in adjust, daily and pay" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Shared input helpers, number formatting, kept form values, request-failed notice

**Files:**
- Create: `src/yokai_admin/core/inputs.py`, `tests/test_admin_inputs.py`
- Modify: `src/yokai_admin/modules/access/service.py`, `src/yokai_admin/modules/access/router.py`, `src/yokai_admin/modules/access/templates/access/_lists.html`, `src/yokai_admin/core/formatting.py`, `src/yokai_admin/core/templating.py`, `src/yokai_admin/templates/base.html`, `src/yokai_admin/static/panel.js`, `src/yokai_admin/static/panel.css`, `tests/admin_helpers.py`
- Test: `tests/test_admin_inputs.py`, `tests/test_admin_access.py` (append), `tests/test_admin_static.py` (append), `tests/test_admin_app.py` (append), `tests/test_admin_audit_core.py` (append)

**Interfaces:**
- Produces:
  - `core/inputs.py`: `InputRefused(PanelError)`, `MIN_REASON = 3`, `MAX_REASON = 200`, `parse_user_id(raw: str) -> int`, `clean_reason(raw: str, *, required: bool) -> str`, `parse_page(raw: str) -> int`; behavior unchanged from P-A3 (moved, not rewritten). `modules/access/service.py` imports its helpers and keeps `AccessRefused = InputRefused`; `modules/audit/service.py` imports `parse_page` (and keeps the name importable), so every existing import and test keeps working.
  - `formatting.format_number(n: Optional[int]) -> str` (thousands separators, en dash for `None`); `formatting.format_signed(n: int) -> str` (`+500`, `-250`, `0`); Jinja filters `number`, `signed` and `age` (the existing `describe_age`).
  - Access forms re-render with what was typed after a refusal (only the form that was submitted; cleared on success).
  - `base.html` gains `<p id="request-error" class="flash" data-kind="error" role="alert" hidden>...</p>`; `panel.js` unhides it when an htmx request fails with a response error, a send error or a timeout **and the failing element is not the Dashboard status block** (that one has its own stale marker).
  - Test helpers: `seed_database(..., credits: Optional[Mapping[int, int]] = None)` (each person gets a wallet: 200 welcome plus the credit) and `seed_activity(path, *, plays=(), bets=(), user_commands=(), transactions=(), daily=None)` (raw inserts; see Step 3).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_admin_inputs.py`:

```python
"""The input helpers moved to core/inputs.py keep their behavior and their old import names."""

from __future__ import annotations

import pytest

from yokai_admin.core import inputs
from yokai_admin.core.formatting import format_number, format_signed
from yokai_admin.modules.access import service


def test_access_and_audit_still_expose_the_same_helpers_and_error_type() -> None:
    from yokai_admin.modules.audit import service as audit_service

    assert audit_service.parse_page is inputs.parse_page
    assert service.parse_user_id is inputs.parse_user_id
    assert service.clean_reason is inputs.clean_reason
    assert service.AccessRefused is inputs.InputRefused


@pytest.mark.parametrize("raw", ["", "abc", "١٢", "+5", "9" * 5000])
def test_user_id_validation_is_unchanged(raw: str) -> None:
    with pytest.raises(inputs.InputRefused):
        inputs.parse_user_id(raw)


@pytest.mark.parametrize(
    ("value", "text"), [(0, "0"), (999, "999"), (1_500, "1,500"), (-1_234_567, "-1,234,567"), (None, "–")]
)
def test_format_number(value: object, text: str) -> None:
    assert format_number(value) == text  # type: ignore[arg-type]


@pytest.mark.parametrize(("value", "text"), [(500, "+500"), (-250, "-250"), (0, "0"), (12_345, "+12,345")])
def test_format_signed(value: int, text: str) -> None:
    assert format_signed(value) == text
```

Append to `tests/test_admin_access.py`:

```python
def test_a_refused_form_keeps_what_was_typed_and_only_in_that_form(tmp_path: Path) -> None:
    """P-A3 deferred minor: a too-short reason used to wipe the whole form."""
    db = tmp_path / "yokai.db"
    seed_database(db)
    with panel_client(tmp_path, [ACCESS], db_path=db) as (client, csrf):
        refused = post(client, csrf, "/m/access/blacklist", user_id="42", reason="x")
        ok = post(client, csrf, "/m/access/blacklist", user_id="42", reason="spamming the queue")
    assert 'name="user_id" inputmode="numeric" autocomplete="off" value="42"' in refused.text
    assert 'value="x"' in refused.text
    admin_form = refused.text.split('hx-post="/m/access/admins"')[1]
    assert 'value="42"' not in admin_form  # the other form is untouched
    assert 'value="42"' not in ok.text and "is blacklisted" in ok.text  # cleared after success


def test_a_kept_value_is_escaped(tmp_path: Path) -> None:
    db = tmp_path / "yokai.db"
    seed_database(db)
    with panel_client(tmp_path, [ACCESS], db_path=db) as (client, csrf):
        refused = post(client, csrf, "/m/access/blacklist", user_id='"><script>x</script>', reason="y")
    assert "<script>x</script>" not in refused.text and "&lt;script&gt;" in refused.text
```

Append to `tests/test_admin_static.py`:

```python
def test_the_panel_script_shows_a_notice_for_failed_requests_outside_the_status_block() -> None:
    script = (STATIC / "panel.js").read_text(encoding="utf-8")
    assert "request-error" in script and "hidden" in script
    assert "closest" in script or "contains" in script  # the status block is excluded
```

Append to `tests/test_admin_app.py`:

```python
def test_the_shell_has_a_hidden_request_failed_notice(tmp_path: Path) -> None:
    with panel_client(tmp_path, [f"{H}:DEMO"]) as (client, _):
        page = client.get("/m/demo").text
    assert 'id="request-error"' in page and "hidden" in page.split('id="request-error"')[1].split(">")[0]
    assert "That request failed" in page


def test_number_filters_are_available_to_every_template(tmp_path: Path) -> None:
    with panel_client(tmp_path, [f"{H}:DEMO"]) as (client, _):
        env = client.app.state.templates.env  # type: ignore[attr-defined]
    assert env.filters["number"](1500) == "1,500" and env.filters["signed"](-3) == "-3"
    assert env.filters["age"](300) == "5 minutes"
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_admin_inputs.py tests/test_admin_access.py tests/test_admin_static.py tests/test_admin_app.py -q`
Expected: FAIL (`yokai_admin.core.inputs` missing; kept values and notice not implemented).

- [ ] **Step 3: Implement**

Create `src/yokai_admin/core/inputs.py` by **moving** `MIN_REASON`, `MAX_REASON`, `_UNSAFE`, `parse_user_id` and `clean_reason` out of `modules/access/service.py` and `parse_page` out of `modules/audit/service.py`, unchanged (the audit service then does `from yokai_admin.core.inputs import parse_page`), adding:

```python
class InputRefused(PanelError):
    """A request the panel refuses on purpose; the message is shown to the owner."""
```

(with `from yokai_admin.errors import PanelError`). In `modules/access/service.py` delete the moved code and the old `AccessRefused` class, then add

```python
from yokai_admin.core.inputs import InputRefused, clean_reason, parse_user_id

AccessRefused = InputRefused  # kept so existing imports and `except AccessRefused` keep working

__all__ = [...]  # not needed; ruff F401 is avoided because every name above is used in this file
```

(`parse_user_id`, `clean_reason` and `InputRefused` are all used by the writers; if ruff flags a name as unused, import only what is used and re-export the rest explicitly with `# noqa: F401`.)

In `core/formatting.py` add:

```python
def format_number(value: Optional[int]) -> str:
    """A whole number with thousands separators, or an en dash when there is none."""
    return "–" if value is None else f"{value:,}"


def format_signed(value: int) -> str:
    """A change shown with its sign: +500, -250, or 0."""
    return f"{value:+,}" if value else "0"
```

and in `core/templating.py::build_templates` register `templates.env.filters["number"] = format_number`, `["signed"] = format_signed` and `["age"] = describe_age` (import all three).

Access kept values. In `modules/access/router.py` give `_section` a `form` parameter and thread it through:

```python
def _section(request, flash=None, context=None, form: Optional[dict[str, str]] = None) -> Response:
    data = {**(context if context is not None else _context(request, None)), "flash": flash, "form": form or {}}
    return render_fragment(request, "access/_lists.html", **data)
```

`_mutate` takes a new `form: dict[str, str]` argument and passes `form=form` to `_section` **only in the error branches**; the two add routes pass `{"kind": "blacklist", "user_id": user_id, "reason": reason}` and `{"kind": "admins", "user_id": user_id, "note": note}`; the remove routes pass `{}`. In `_lists.html` change the four inputs to carry values, using a per-form dict so the other form is untouched:

```html
{% set bf = form if form and form.kind == 'blacklist' else {} %}
...
<label>Discord user ID<input name="user_id" inputmode="numeric" autocomplete="off" value="{{ bf.get('user_id', '') }}" required></label>
<label>Reason<input name="reason" maxlength="200" autocomplete="off" value="{{ bf.get('reason', '') }}" required></label>
```

and the same with `{% set af = form if form and form.kind == 'admins' else {} %}` for the admin form (`user_id`, `note`). (Jinja autoescape escapes the values; do not use `|safe`.)

Request-failed notice. In `templates/base.html`, inside `<main class="main">` just before `{% block content %}`:

```html
<p id="request-error" class="flash" data-kind="error" role="alert" hidden><span class="sr-only">Problem: </span>That request failed. Reload the page and check the current state before trying again.</p>
```

Replace `static/panel.js` with:

```js
// Two small jobs for the HTMX pages.
// 1. When polling fails, mark the live status block stale so an old "Online" is never trusted.
// 2. When any other HTMX request fails (a server error, a lost connection, a timeout), show the
//    request-failed notice: HTMX itself swaps nothing on an error, so without this a failed
//    action would look like nothing happened.
(function () {
  function markStale() {
    var status = document.getElementById("status");
    if (status) {
      status.setAttribute("data-stale", "true");
    }
  }

  function showRequestFailed(event) {
    var status = document.getElementById("status");
    var source = event.target;
    if (status && source && (source === status || status.contains(source))) {
      return; // the status block handles its own polling failures
    }
    var notice = document.getElementById("request-error");
    if (notice) {
      notice.hidden = false;
    }
  }

  ["htmx:sendError", "htmx:responseError", "htmx:timeout"].forEach(function (name) {
    document.addEventListener(name, markStale);
    document.addEventListener(name, showRequestFailed);
  });
})();
```

(`markStale` keeps its P-A2 behavior; the `data-stale` test from P-A2 still passes.)

Test helpers in `tests/admin_helpers.py`: add `credits: Optional[Mapping[int, int]] = None` to `seed_database` (inside `run()`, after the wallets loop: `for user_id, amount in (credits or {}).items(): await db.credit_wallet(user_id, amount, reason="daily", welcome_bonus=200)`), and add:

```python
def seed_activity(
    path: Path,
    *,
    plays: Sequence[tuple[int, str, str, Optional[str], int]] = (),
    bets: Sequence[tuple[int, str, int, int, str, Optional[str]]] = (),
    user_commands: Sequence[tuple[int, str, int]] = (),
    transactions: Sequence[tuple[int, int, str, Optional[int], int, int]] = (),
    daily: Optional[Mapping[int, tuple[Optional[int], int]]] = None,
) -> None:
    """Insert raw activity rows: plays (user, video_id, title, artist, ts), bets (user, game,
    stake, payout, status, group_id), commands (user, command, ts), ledger rows (user, delta,
    reason, counterparty, balance_after, ts) and daily state ({user: (last_daily_ts, streak)})."""
    conn = sqlite3.connect(path)
    try:
        for user_id, video_id, title, artist, ts in plays:
            conn.execute(
                "INSERT INTO play_events (ts, user_id, video_id, title, artist, duration_s, origin, "
                "outcome, listened_s) VALUES (?, ?, ?, ?, ?, 180, 'search', 'completed', 180);",
                (ts, user_id, video_id, title, artist),
            )
        for user_id, game, stake, payout, status, group_id in bets:
            conn.execute(
                "INSERT INTO gambling_bets (ts, user_id, game, stake, status, payout, group_id) "
                "VALUES (1, ?, ?, ?, ?, ?, ?);",
                (user_id, game, stake, status, payout, group_id),
            )
        for user_id, command, ts in user_commands:
            conn.execute(
                "INSERT INTO command_log (ts, user_id, command) VALUES (?, ?, ?);", (ts, user_id, command)
            )
        for user_id, delta, reason, counterparty, after, ts in transactions:
            conn.execute(
                "INSERT INTO renown_transactions (ts, user_id, delta, reason, counterparty_id, "
                "balance_after) VALUES (?, ?, ?, ?, ?, ?);",
                (ts, user_id, delta, reason, counterparty, after),
            )
        for user_id, (last_daily_ts, streak) in (daily or {}).items():
            conn.execute(
                "UPDATE wallets SET last_daily_ts = ?, daily_streak = ? WHERE user_id = ?;",
                (last_daily_ts, streak, user_id),
            )
        conn.commit()
    finally:
        conn.close()
```

(add `import sqlite3` to the helper imports.)

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_admin_inputs.py tests/test_admin_access.py tests/test_admin_access_service.py tests/test_admin_static.py tests/test_admin_app.py tests/test_admin_audit_core.py -q`
Expected: PASS (all existing Access tests keep passing through the re-exported names).

- [ ] **Step 5: Commit**

```powershell
git add src tests
git commit -m "feat(admin): share input helpers, keep refused form values, show a notice for failed requests" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: People read side (list, search, profile)

**Files:**
- Create: `src/yokai_admin/modules/users/__init__.py` (docstring only for now), `src/yokai_admin/modules/users/service.py`
- Test: `tests/test_admin_users_service.py`

**Interfaces:**
- Consumes: `user_refs`/`UserRef` (P-A3), `schema_version` is not needed, `format_when` not needed here; seed helpers from Task 2.
- Produces (in `service.py`):
  - `DAILY_COOLDOWN_S = 86_400` (mirrors the bot; a test asserts the two are equal).
  - `PAGE_SIZE = 25`; `Person(user: UserRef, balance: Optional[int], last_seen_ts: Optional[int])`; `list_people(conn, *, query: str, limit: int, offset: int) -> tuple[list[Person], int]`: everyone with a wallet or a remembered name, newest activity first (people with no recorded command last, then by balance desc, then ID); `query` (already cleaned, at most 80 characters): a name fragment (case-insensitive for ASCII, LIKE wildcards `%` `_` `\` treated literally) or, when it is plain ASCII digits, also an ID fragment.
  - `reason_label(reason: str) -> str` (plain words for ledger reasons).
  - `WalletView(balance, last_daily_ts, daily_streak, created_ts, daily_ready_in: Optional[int])`, `Txn(ts, delta, reason, label, balance_after, counterparty: Optional[UserRef])`, `GameStat(game, plays, wagered, returned)` with a `net` property, `TrackStat(title, artist, plays)`, `CommandStat(command, count)`, `Profile(user, wallet: Optional[WalletView], transactions, games, tracks, commands, commands_total, first_seen_ts, last_seen_ts)`.
  - `load_profile(conn, user_id: int, now: int) -> Optional[Profile]`: `None` when the bot knows nothing about the ID (no wallet, no remembered name, no commands). Transactions: newest 25. Games: settled bets only, plays counted per `COALESCE(group_id, id)`. Tracks: top 5 by plays. Commands: top 5 plus the total.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_admin_users_service.py`:

```python
"""Tests for the People read side: the list, the search and the profile aggregates."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.admin_helpers import seed_activity, seed_database
from yokai.economy.daily import DAILY_COOLDOWN_S as BOT_DAILY_COOLDOWN_S
from yokai_admin.core.db import readonly
from yokai_admin.modules.users.service import (
    DAILY_COOLDOWN_S,
    list_people,
    load_profile,
    reason_label,
)

NOW = 1_000_000


def _people_db(tmp_path: Path) -> Path:
    path = tmp_path / "b.db"
    seed_database(path, credits={1: 300, 2: 1}, names={1: "Alex", 3: "Riley", 5: "Mal_lory 100%"})
    seed_activity(path, user_commands=[(1, "play", NOW - 100), (3, "skip", NOW - 50)])
    return path


def _ids(rows: list) -> list[int]:  # type: ignore[type-arg]
    return [row.user.id for row in rows]


def test_the_daily_cooldown_matches_the_bot() -> None:
    """The panel mirrors the bot's constant; this test catches the two drifting apart."""
    assert DAILY_COOLDOWN_S == BOT_DAILY_COOLDOWN_S


def test_the_list_covers_wallet_holders_and_named_people_most_recent_first(tmp_path: Path) -> None:
    path = _people_db(tmp_path)
    with readonly(path) as conn:
        rows, total = list_people(conn, query="", limit=25, offset=0)
    assert total == 4 and set(_ids(rows)) == {1, 2, 3, 5}
    assert _ids(rows)[:2] == [3, 1]  # Riley (50 s ago), then Alex (100 s ago); the rest have no commands
    by_id = {row.user.id: row for row in rows}
    assert by_id[1].balance == 500 and by_id[3].balance is None  # Riley has a name but no wallet
    assert by_id[1].user.name == "Alex" and by_id[2].user.name is None


def test_search_matches_a_name_fragment_or_an_id_fragment(tmp_path: Path) -> None:
    path = _people_db(tmp_path)
    with readonly(path) as conn:
        assert _ids(list_people(conn, query="ale", limit=25, offset=0)[0]) == [1]  # case-insensitive
        assert _ids(list_people(conn, query="RILEY", limit=25, offset=0)[0]) == [3]
        assert _ids(list_people(conn, query="2", limit=25, offset=0)[0]) == [2]  # an ID fragment
        assert list_people(conn, query="nobody", limit=25, offset=0) == ([], 0)


@pytest.mark.parametrize(
    ("query", "expected"),
    [("%", [5]), ("_", [5]), ("\\", []), ("100%", [5]), ("l_l", [5]), ("a_e", [])],
)
def test_search_wildcards_are_literal(tmp_path: Path, query: str, expected: list[int]) -> None:
    """Review Focus 5: '%' and '_' must not match everything. Only "Mal_lory 100%" contains
    those characters; without escaping, '%' would match all four people and 'a_e' would match
    'Alex'."""
    path = _people_db(tmp_path)
    with readonly(path) as conn:
        assert _ids(list_people(conn, query=query, limit=25, offset=0)[0]) == expected


def test_the_list_pages_with_a_total(tmp_path: Path) -> None:
    path = tmp_path / "b.db"
    seed_database(path, names={i: f"Person {i:02d}" for i in range(1, 31)})
    with readonly(path) as conn:
        first, total = list_people(conn, query="", limit=25, offset=0)
        second, _ = list_people(conn, query="", limit=25, offset=25)
    assert total == 30 and len(first) == 25 and len(second) == 5
    assert not set(_ids(first)) & set(_ids(second))


def test_an_unknown_id_has_no_profile(tmp_path: Path) -> None:
    path = _people_db(tmp_path)
    with readonly(path) as conn:
        assert load_profile(conn, 999, NOW) is None


def test_a_named_person_without_a_wallet_still_has_a_profile(tmp_path: Path) -> None:
    path = _people_db(tmp_path)
    with readonly(path) as conn:
        profile = load_profile(conn, 3, NOW)
    assert profile is not None and profile.wallet is None and profile.user.name == "Riley"
    assert profile.commands_total == 1 and profile.last_seen_ts == NOW - 50


def test_the_wallet_view_includes_streak_and_daily_readiness(tmp_path: Path) -> None:
    path = tmp_path / "b.db"
    seed_database(path, credits={1: 100, 2: 100, 3: 100})
    seed_activity(path, daily={1: (NOW - 3_600, 4), 2: (NOW - DAILY_COOLDOWN_S, 2), 3: (None, 0)})
    with readonly(path) as conn:
        cooling = load_profile(conn, 1, NOW)
        ready = load_profile(conn, 2, NOW)
        never = load_profile(conn, 3, NOW)
    assert cooling and cooling.wallet and cooling.wallet.balance == 300
    assert cooling.wallet.daily_streak == 4 and cooling.wallet.daily_ready_in == DAILY_COOLDOWN_S - 3_600
    assert ready and ready.wallet and ready.wallet.daily_ready_in is None  # exactly 24 h: allowed
    assert never and never.wallet and never.wallet.daily_ready_in is None


def test_transactions_are_newest_first_labelled_and_name_the_other_person(tmp_path: Path) -> None:
    path = tmp_path / "b.db"
    seed_database(path, credits={1: 300}, names={1: "Alex", 2: "Mallory"})
    seed_activity(
        path,
        transactions=[
            (1, -50, "pay_sent", 2, 450, NOW - 10),
            (1, 25, "win:slots", None, 475, NOW - 5),
        ],
    )
    with readonly(path) as conn:
        profile = load_profile(conn, 1, NOW)
    assert profile is not None
    newest = profile.transactions[0]
    assert (newest.delta, newest.label, newest.balance_after) == (25, "Win (slots)", 475)
    sent = profile.transactions[1]
    assert sent.label == "Sent to" and sent.counterparty is not None and sent.counterparty.name == "Mallory"
    assert [t.reason for t in profile.transactions][-2:] == ["daily", "welcome"]


def test_only_the_newest_25_transactions_are_loaded(tmp_path: Path) -> None:
    path = tmp_path / "b.db"
    seed_database(path, credits={1: 1})
    seed_activity(path, transactions=[(1, 1, "voice", None, 202 + i, NOW + i) for i in range(40)])
    with readonly(path) as conn:
        profile = load_profile(conn, 1, NOW)
    assert profile is not None and len(profile.transactions) == 25


def test_gambling_counts_settled_bets_only_and_groups_multi_bet_games(tmp_path: Path) -> None:
    path = tmp_path / "b.db"
    seed_database(path, credits={1: 1})
    seed_activity(
        path,
        bets=[
            (1, "slots", 50, 0, "settled", None),
            (1, "slots", 50, 120, "settled", None),
            (1, "roulette", 10, 20, "settled", "g1"),
            (1, "roulette", 10, 0, "settled", "g1"),  # same group: one play
            (1, "blackjack", 100, 0, "open", None),  # unfinished: ignored
            (1, "slots", 5, 5, "refunded", None),  # refunded: ignored
        ],
    )
    with readonly(path) as conn:
        profile = load_profile(conn, 1, NOW)
    assert profile is not None
    games = {g.game: g for g in profile.games}
    assert set(games) == {"slots", "roulette"}
    assert (games["slots"].plays, games["slots"].wagered, games["slots"].returned) == (2, 100, 120)
    assert games["slots"].net == 20
    assert (games["roulette"].plays, games["roulette"].wagered, games["roulette"].returned) == (1, 20, 20)


def test_tracks_and_commands_are_ranked_and_capped_at_five(tmp_path: Path) -> None:
    path = tmp_path / "b.db"
    seed_database(path, credits={1: 1})
    plays = [(1, "v1", "Song One", "Artist A", NOW - i) for i in range(3)]
    plays += [(1, "v2", "Song Two", None, NOW - 10)]
    plays += [(1, f"x{i}", f"Other {i}", "Z", NOW - 20 - i) for i in range(6)]
    commands = [(1, "play", NOW)] * 4 + [(1, "skip", NOW)] * 2 + [(1, f"c{i}", NOW) for i in range(6)]
    seed_activity(path, plays=plays, user_commands=commands)
    with readonly(path) as conn:
        profile = load_profile(conn, 1, NOW)
    assert profile is not None
    assert (profile.tracks[0].title, profile.tracks[0].artist, profile.tracks[0].plays) == ("Song One", "Artist A", 3)
    assert profile.tracks[1].title == "Song Two" and profile.tracks[1].artist is None
    assert len(profile.tracks) == 5
    assert (profile.commands[0].command, profile.commands[0].count) == ("play", 4)
    assert len(profile.commands) == 5 and profile.commands_total == 12


@pytest.mark.parametrize(
    ("reason", "label"),
    [
        ("daily", "Daily reward"),
        ("welcome", "Welcome bonus"),
        ("admin", "Adjusted by an admin"),
        ("pay_received", "Received from"),
        ("bet:slots", "Bet (slots)"),
        ("payout:roulette", "Payout (roulette)"),
        ("refund:blackjack", "Refund (blackjack)"),
        ("something_new", "something_new"),
    ],
)
def test_reason_labels(reason: str, label: str) -> None:
    assert reason_label(reason) == label
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_admin_users_service.py -q`
Expected: FAIL (`ModuleNotFoundError: yokai_admin.modules.users`).

- [ ] **Step 3: Implement**

Create `src/yokai_admin/modules/users/__init__.py` containing only `"""People module: who the bot knows, and their wallets."""`.

Create `src/yokai_admin/modules/users/service.py`:

```python
"""People read side: the list with search, and the aggregates shown on a profile."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional

from yokai_admin.core.people import UserRef, user_refs

# Mirrors yokai.economy.daily.DAILY_COOLDOWN_S (the panel does not import the bot package);
# tests/test_admin_users_service.py fails if the two ever differ.
DAILY_COOLDOWN_S = 86_400
PAGE_SIZE = 25
TRANSACTION_LIMIT = 25
TOP_LIMIT = 5
_LABELS = {
    "daily": "Daily reward",
    "welcome": "Welcome bonus",
    "voice": "Voice time",
    "command": "Music command",
    "admin": "Adjusted by an admin",
    "pay_sent": "Sent to",
    "pay_received": "Received from",
}
_GAME_LABELS = {
    "bet": "Bet",
    "win": "Win",
    "push": "Push (stake returned)",
    "payout": "Payout",
    "refund": "Refund",
}


def reason_label(reason: str) -> str:
    """Plain words for a ledger reason; unknown reasons are shown as written."""
    if reason in _LABELS:
        return _LABELS[reason]
    prefix, separator, game = reason.partition(":")
    if separator and prefix in _GAME_LABELS and game:
        return f"{_GAME_LABELS[prefix]} ({game})"
    return reason


@dataclass(frozen=True, slots=True)
class Person:
    """One row of the people list."""

    user: UserRef
    balance: Optional[int]
    last_seen_ts: Optional[int]


def _like(fragment: str) -> str:
    """A LIKE pattern that matches the fragment literally (escape character is backslash)."""
    escaped = fragment.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def list_people(
    conn: sqlite3.Connection, *, query: str, limit: int, offset: int
) -> tuple[list[Person], int]:
    """Everyone with a wallet or a remembered name, most recently active first."""
    base = (
        "FROM (SELECT user_id FROM wallets UNION SELECT user_id FROM user_names) p "
        "LEFT JOIN user_names n ON n.user_id = p.user_id "
        "LEFT JOIN wallets w ON w.user_id = p.user_id"
    )
    where = ""
    params: list[object] = []
    if query:
        pattern = _like(query)
        if query.isascii() and query.isdigit():
            where = " WHERE (n.display_name LIKE ? ESCAPE '\\' OR CAST(p.user_id AS TEXT) LIKE ? ESCAPE '\\')"
            params = [pattern, pattern]
        else:
            where = " WHERE n.display_name LIKE ? ESCAPE '\\'"
            params = [pattern]
    total = int(conn.execute(f"SELECT COUNT(*) {base}{where};", params).fetchone()[0])
    rows = conn.execute(
        "SELECT p.user_id AS user_id, n.display_name AS name, w.balance AS balance, "
        "(SELECT MAX(c.ts) FROM command_log c WHERE c.user_id = p.user_id) AS last_seen "
        f"{base}{where} "
        "ORDER BY last_seen IS NULL, last_seen DESC, w.balance IS NULL, w.balance DESC, p.user_id "
        "LIMIT ? OFFSET ?;",
        [*params, limit, offset],
    ).fetchall()
    people = [
        Person(
            UserRef(int(r["user_id"]), None if r["name"] is None else str(r["name"])),
            None if r["balance"] is None else int(r["balance"]),
            None if r["last_seen"] is None else int(r["last_seen"]),
        )
        for r in rows
    ]
    return people, total


@dataclass(frozen=True, slots=True)
class WalletView:
    balance: int
    last_daily_ts: Optional[int]
    daily_streak: int
    created_ts: int
    daily_ready_in: Optional[int]  # seconds until /daily works; None means it works now


@dataclass(frozen=True, slots=True)
class Txn:
    ts: int
    delta: int
    reason: str
    label: str
    balance_after: int
    counterparty: Optional[UserRef]


@dataclass(frozen=True, slots=True)
class GameStat:
    game: str
    plays: int
    wagered: int
    returned: int

    @property
    def net(self) -> int:
        return self.returned - self.wagered


@dataclass(frozen=True, slots=True)
class TrackStat:
    title: str
    artist: Optional[str]
    plays: int


@dataclass(frozen=True, slots=True)
class CommandStat:
    command: str
    count: int


@dataclass(frozen=True, slots=True)
class Profile:
    user: UserRef
    wallet: Optional[WalletView]
    transactions: list[Txn]
    games: list[GameStat]
    tracks: list[TrackStat]
    commands: list[CommandStat]
    commands_total: int
    first_seen_ts: Optional[int]
    last_seen_ts: Optional[int]


def _daily_ready_in(last_daily_ts: Optional[int], now: int) -> Optional[int]:
    if last_daily_ts is None:
        return None
    elapsed = now - last_daily_ts
    return None if elapsed >= DAILY_COOLDOWN_S else DAILY_COOLDOWN_S - elapsed


def load_profile(conn: sqlite3.Connection, user_id: int, now: int) -> Optional[Profile]:
    """Everything shown on a person's page, or None if the bot knows nothing about the ID."""
    user = user_refs(conn, [user_id])[user_id]
    wallet_row = conn.execute(
        "SELECT balance, last_daily_ts, daily_streak, created_ts FROM wallets WHERE user_id = ?;",
        (user_id,),
    ).fetchone()
    count, first, last = conn.execute(
        "SELECT COUNT(*), MIN(ts), MAX(ts) FROM command_log WHERE user_id = ?;", (user_id,)
    ).fetchone()
    if wallet_row is None and user.name is None and int(count) == 0:
        return None

    wallet: Optional[WalletView] = None
    if wallet_row is not None:
        last_daily = None if wallet_row["last_daily_ts"] is None else int(wallet_row["last_daily_ts"])
        wallet = WalletView(
            balance=int(wallet_row["balance"]),
            last_daily_ts=last_daily,
            daily_streak=int(wallet_row["daily_streak"]),
            created_ts=int(wallet_row["created_ts"]),
            daily_ready_in=_daily_ready_in(last_daily, now),
        )

    txn_rows = conn.execute(
        "SELECT ts, delta, reason, counterparty_id, balance_after FROM renown_transactions "
        "WHERE user_id = ? ORDER BY id DESC LIMIT ?;",
        (user_id, TRANSACTION_LIMIT),
    ).fetchall()
    others = user_refs(conn, [int(r["counterparty_id"]) for r in txn_rows if r["counterparty_id"] is not None])
    transactions = [
        Txn(
            ts=int(r["ts"]),
            delta=int(r["delta"]),
            reason=str(r["reason"]),
            label=reason_label(str(r["reason"])),
            balance_after=int(r["balance_after"]),
            counterparty=others.get(int(r["counterparty_id"])) if r["counterparty_id"] is not None else None,
        )
        for r in txn_rows
    ]

    games = [
        GameStat(str(r[0]), int(r[1]), int(r[2]), int(r[3]))
        for r in conn.execute(
            "SELECT game, COUNT(DISTINCT COALESCE(group_id, id)), SUM(stake), SUM(payout) "
            "FROM gambling_bets WHERE user_id = ? AND status = 'settled' GROUP BY game ORDER BY game;",
            (user_id,),
        )
    ]
    tracks = [
        TrackStat(str(r["title"]), None if r["artist"] is None else str(r["artist"]), int(r["plays"]))
        for r in conn.execute(
            "SELECT MAX(title) AS title, MAX(artist) AS artist, COUNT(*) AS plays FROM play_events "
            "WHERE user_id = ? GROUP BY video_id ORDER BY plays DESC, MAX(ts) DESC LIMIT ?;",
            (user_id, TOP_LIMIT),
        )
    ]
    commands = [
        CommandStat(str(r[0]), int(r[1]))
        for r in conn.execute(
            "SELECT command, COUNT(*) AS n FROM command_log WHERE user_id = ? "
            "GROUP BY command ORDER BY n DESC, command LIMIT ?;",
            (user_id, TOP_LIMIT),
        )
    ]
    return Profile(
        user=user,
        wallet=wallet,
        transactions=transactions,
        games=games,
        tracks=tracks,
        commands=commands,
        commands_total=int(count),
        first_seen_ts=None if first is None else int(first),
        last_seen_ts=None if last is None else int(last),
    )
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_admin_users_service.py -q`
Expected: PASS. If the wildcard test's loose assertions prove brittle, replace it with exact expectations for the seeded names (`"Mal_lory 100%"` matches `100%` and `l_l` and nothing else); never loosen what it proves (literal matching).

- [ ] **Step 5: Commit**

```powershell
git add src/yokai_admin/modules/users tests/test_admin_users_service.py
git commit -m "feat(admin): add the People list, search and profile read side" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: People write side (balance adjustment and daily reset)

**Files:**
- Create: `src/yokai_admin/modules/users/actions.py`
- Test: `tests/test_admin_users_actions.py`

**Interfaces:**
- Consumes: `write_transaction`, `readonly` (P-A3); `record` (audit); `InputRefused`, `parse_user_id`, `clean_reason` (Task 2); `user_refs`; `DAILY_COOLDOWN_S` (Task 3).
- Produces (in `actions.py`):
  - `ADJUST_CAP = 100_000`.
  - `parse_amount(raw: str) -> int`: optional leading `+` or `-`, then ASCII digits only; nonzero; at most 7 digits; absolute value at most `ADJUST_CAP`; otherwise `InputRefused` with a message that says what to type.
  - `AdjustPreview(user: UserRef, before: int, requested: int, applied: int, after: int)` with a `clamped` property (`applied != requested`); `preview_adjustment(conn, user_id: int, amount: int) -> AdjustPreview` (read-only; refuses a person with no wallet, and a deduction from 0).
  - `AdjustResult(user, requested, applied, before, after)`; `adjust_balance(db_path, *, raw_id, raw_amount, raw_reason, expected_balance: int, now=None) -> AdjustResult`: one `write_transaction` that re-reads the wallet, refuses if the balance differs from `expected_balance`, applies `max(0, balance + amount)` as a relative update, inserts a `renown_transactions` row (`reason='admin'`, `delta` = the applied amount, `balance_after` = the new balance) and an audit row (`module="users"`, `action="balance.adjust"`, `target=str(user_id)`, `reason` = the typed reason, `detail={"name","requested","applied","before","after"}`).
  - `DailyResetResult(user, previous_last_daily_ts, streak)`; `reset_daily(db_path, *, raw_id, now=None) -> DailyResetResult`: back-dates `last_daily_ts` to `now - DAILY_COOLDOWN_S` (keeps the streak); refuses a person with no wallet, one who never claimed, and one who can already claim; audit `action="daily.reset"` with `detail={"name","previous_last_daily_ts","streak"}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_admin_users_actions.py`:

```python
"""Tests for the People write side: parsing, clamping, atomicity, stale previews and races."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from tests.admin_helpers import seed_activity, seed_database
from yokai.storage.db import Database
from yokai_admin.core.audit import AuditEntry, list_entries
from yokai_admin.core.db import readonly
from yokai_admin.core.inputs import InputRefused
from yokai_admin.errors import PanelDbError
from yokai_admin.modules.users import actions
from yokai_admin.modules.users.actions import (
    ADJUST_CAP,
    adjust_balance,
    parse_amount,
    preview_adjustment,
    reset_daily,
)

NOW = 2_000_000


def _db(tmp_path: Path, **seed: object) -> Path:
    path = tmp_path / "b.db"
    seed_database(path, **seed)  # type: ignore[arg-type]
    return path


def _balance(path: Path, user_id: int) -> int:
    with readonly(path) as conn:
        return int(conn.execute("SELECT balance FROM wallets WHERE user_id = ?;", (user_id,)).fetchone()[0])


def _ledger(path: Path, user_id: int) -> list[tuple[int, str, int]]:
    with readonly(path) as conn:
        rows = conn.execute(
            "SELECT delta, reason, balance_after FROM renown_transactions WHERE user_id = ? ORDER BY id;",
            (user_id,),
        )
        return [(int(r[0]), str(r[1]), int(r[2])) for r in rows]


def _ledger_is_true(path: Path, user_id: int) -> bool:
    ledger = _ledger(path, user_id)
    return _balance(path, user_id) == sum(d for d, _, _ in ledger) == ledger[-1][2]


def _audit(path: Path) -> list[AuditEntry]:
    with readonly(path) as conn:
        return list_entries(conn)[0]


@pytest.mark.parametrize(
    ("raw", "value"),
    [("500", 500), ("+500", 500), ("-250", -250), (" 42 ", 42), ("000123", 123), (str(ADJUST_CAP), ADJUST_CAP), (f"-{ADJUST_CAP}", -ADJUST_CAP)],
)
def test_valid_amounts_parse(raw: str, value: int) -> None:
    assert parse_amount(raw) == value


@pytest.mark.parametrize(
    "raw",
    ["", " ", "0", "+0", "-0", "abc", "1e3", "1,000", "1 000", "12.5", "--5", "+-5", "−5", "٥٠٠", "５００", str(ADJUST_CAP + 1), f"-{ADJUST_CAP + 1}", "9" * 8, "9" * 5000],
)
def test_invalid_amounts_are_refused(raw: str) -> None:
    """Review Focus 3: Unicode minus, other-script digits, separators, zero, over the cap, huge."""
    with pytest.raises(InputRefused):
        parse_amount(raw)


def test_the_cap_message_names_the_limit() -> None:
    with pytest.raises(InputRefused, match="100,000"):
        parse_amount("100001")


def test_a_positive_adjustment_changes_the_wallet_the_ledger_and_the_audit_log_together(tmp_path: Path) -> None:
    path = _db(tmp_path, credits={1: 300}, names={1: "Alex"})  # balance 500
    result = adjust_balance(path, raw_id="1", raw_amount="+250", raw_reason="  prize for the event ", expected_balance=500, now=NOW)
    assert (result.before, result.applied, result.after, result.user.name) == (500, 250, 750, "Alex")
    assert _balance(path, 1) == 750 and _ledger_is_true(path, 1)
    assert _ledger(path, 1)[-1] == (250, "admin", 750)
    (entry,) = _audit(path)
    assert (entry.module, entry.action, entry.target, entry.reason) == ("users", "balance.adjust", "1", "prize for the event")
    assert entry.detail == {"name": "Alex", "requested": 250, "applied": 250, "before": 500, "after": 750}


def test_a_deduction_larger_than_the_balance_ends_at_zero_and_records_the_applied_amount(tmp_path: Path) -> None:
    """Review Focus 4."""
    path = _db(tmp_path, credits={1: 300})  # 500
    result = adjust_balance(path, raw_id="1", raw_amount="-900", raw_reason="reset after exploit", expected_balance=500)
    assert (result.requested, result.applied, result.after) == (-900, -500, 0)
    assert _balance(path, 1) == 0 and _ledger_is_true(path, 1)
    assert _ledger(path, 1)[-1] == (-500, "admin", 0)
    assert _audit(path)[0].detail["requested"] == -900 and _audit(path)[0].detail["applied"] == -500


def test_deducting_from_an_empty_wallet_is_refused_and_writes_nothing(tmp_path: Path) -> None:
    path = _db(tmp_path, credits={1: 300})
    adjust_balance(path, raw_id="1", raw_amount="-500", raw_reason="to zero", expected_balance=500)
    before = (_balance(path, 1), len(_ledger(path, 1)), len(_audit(path)))
    with pytest.raises(InputRefused, match="nothing to deduct"):
        adjust_balance(path, raw_id="1", raw_amount="-1", raw_reason="again", expected_balance=0)
    assert (_balance(path, 1), len(_ledger(path, 1)), len(_audit(path))) == before


def test_a_reason_is_required_and_cleaned(tmp_path: Path) -> None:
    path = _db(tmp_path, credits={1: 300})
    with pytest.raises(InputRefused, match="reason"):
        adjust_balance(path, raw_id="1", raw_amount="5", raw_reason="x", expected_balance=500)
    assert _balance(path, 1) == 500


def test_a_person_without_a_wallet_is_refused_with_an_explanation(tmp_path: Path) -> None:
    path = _db(tmp_path, names={3: "Riley"})
    with pytest.raises(InputRefused, match="no wallet"):
        adjust_balance(path, raw_id="3", raw_amount="5", raw_reason="gift", expected_balance=0)
    with readonly(path) as conn:
        with pytest.raises(InputRefused, match="no wallet"):
            preview_adjustment(conn, 3, 5)


def test_a_stale_preview_is_refused_and_changes_nothing(tmp_path: Path) -> None:
    """The confirm carries the balance the preview saw; if it moved, nothing is written."""
    path = _db(tmp_path, credits={1: 300})
    with pytest.raises(InputRefused, match="changed from 400 to 500"):
        adjust_balance(path, raw_id="1", raw_amount="5", raw_reason="gift", expected_balance=400)
    assert _balance(path, 1) == 500 and _audit(path) == []


def test_two_confirms_from_the_same_preview_apply_exactly_once(tmp_path: Path) -> None:
    """Review Focus 2: BEGIN IMMEDIATE plus the expected-balance check make a double click safe."""
    path = _db(tmp_path, credits={1: 300})
    outcomes: list[str] = []
    barrier = threading.Barrier(2)

    def confirm() -> None:
        barrier.wait()
        try:
            adjust_balance(path, raw_id="1", raw_amount="+100", raw_reason="double click", expected_balance=500)
            outcomes.append("applied")
        except InputRefused:
            outcomes.append("refused")

    threads = [threading.Thread(target=confirm) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
    assert sorted(outcomes) == ["applied", "refused"]
    assert _balance(path, 1) == 600 and _ledger_is_true(path, 1) and len(_audit(path)) == 1


def test_a_failing_audit_write_rolls_back_the_wallet_and_the_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review Focus 1."""
    path = _db(tmp_path, credits={1: 300})
    ledger_before = _ledger(path, 1)

    def broken(*args: object, **kwargs: object) -> int:
        raise RuntimeError("audit table is broken")

    monkeypatch.setattr(actions, "record", broken)
    with pytest.raises(RuntimeError):
        adjust_balance(path, raw_id="1", raw_amount="+100", raw_reason="gift", expected_balance=500)
    assert _balance(path, 1) == 500 and _ledger(path, 1) == ledger_before


def test_writes_to_an_old_schema_or_a_busy_database_are_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    old = tmp_path / "old.db"
    raw = sqlite3.connect(old)
    raw.execute("PRAGMA user_version = 4;")
    raw.commit()
    raw.close()
    with pytest.raises(PanelDbError):
        adjust_balance(old, raw_id="1", raw_amount="5", raw_reason="gift", expected_balance=0)

    from yokai_admin.core import db as panel_db

    busy = tmp_path / "busy"
    busy.mkdir()
    path = _db(busy, credits={1: 300})
    monkeypatch.setattr(panel_db, "WRITE_BUSY_TIMEOUT_S", 0.2)
    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE;")
    try:
        with pytest.raises(PanelDbError, match="busy"):
            adjust_balance(path, raw_id="1", raw_amount="5", raw_reason="gift", expected_balance=500)
    finally:
        holder.execute("ROLLBACK;")
        holder.close()


async def test_panel_and_bot_adjustments_keep_one_consistent_ledger(tmp_path: Path) -> None:
    """The bot's /economy-admin path and the panel, in turn, on one file."""
    path = tmp_path / "b.db"
    bot_db = Database(path)
    await bot_db.initialize()
    try:
        await bot_db.ensure_wallet(1, 200)
        await bot_db.credit_wallet(1, 300, "daily", 200)
        adjust_balance(path, raw_id="1", raw_amount="+100", raw_reason="panel gift", expected_balance=500)
        await bot_db.admin_adjust_wallet(1, -50, 200)
        adjust_balance(path, raw_id="1", raw_amount="-1000", raw_reason="panel wipe", expected_balance=550)
        wallet = await bot_db.get_wallet(1)
    finally:
        await bot_db.close()
    assert wallet is not None and wallet.balance == 0 and _ledger_is_true(path, 1)


def test_the_preview_reports_clamping_without_writing(tmp_path: Path) -> None:
    path = _db(tmp_path, credits={1: 300}, names={1: "Alex"})
    with readonly(path) as conn:
        clamped = preview_adjustment(conn, 1, -900)
        plain = preview_adjustment(conn, 1, 100)
    assert (clamped.before, clamped.requested, clamped.applied, clamped.after, clamped.clamped) == (500, -900, -500, 0, True)
    assert (plain.after, plain.clamped) == (600, False)
    assert _balance(path, 1) == 500 and _audit(path) == []


def test_daily_reset_back_dates_the_last_claim_by_exactly_a_day_and_keeps_the_streak(tmp_path: Path) -> None:
    path = _db(tmp_path, credits={1: 300}, names={1: "Alex"})
    seed_activity(path, daily={1: (NOW - 3_600, 4)})
    result = reset_daily(path, raw_id="1", now=NOW)
    assert (result.previous_last_daily_ts, result.streak, result.user.name) == (NOW - 3_600, 4, "Alex")
    with readonly(path) as conn:
        row = conn.execute("SELECT last_daily_ts, daily_streak, balance FROM wallets WHERE user_id = 1;").fetchone()
    assert (row[0], row[1], row[2]) == (NOW - 86_400, 4, 500)  # claimable now, streak intact, balance untouched
    (entry,) = _audit(path)
    assert (entry.action, entry.target) == ("daily.reset", "1")
    assert entry.detail == {"name": "Alex", "previous_last_daily_ts": NOW - 3_600, "streak": 4}


def test_a_reset_that_would_do_nothing_is_refused(tmp_path: Path) -> None:
    path = _db(tmp_path, credits={1: 300, 2: 300, 3: 300}, names={4: "Riley"})
    seed_activity(path, daily={1: (None, 0), 2: (NOW - 90_000, 3)})
    with pytest.raises(InputRefused, match="never claimed"):
        reset_daily(path, raw_id="1", now=NOW)
    with pytest.raises(InputRefused, match="already claim"):
        reset_daily(path, raw_id="2", now=NOW)
    with pytest.raises(InputRefused, match="no wallet"):
        reset_daily(path, raw_id="4", now=NOW)
    assert _audit(path) == []


async def test_after_a_panel_reset_the_bot_lets_the_person_claim_and_continue_the_streak(tmp_path: Path) -> None:
    import random

    path = tmp_path / "b.db"
    bot_db = Database(path)
    await bot_db.initialize()
    try:
        await bot_db.ensure_wallet(1, 200)
        await bot_db.claim_daily(1, 100, 200, now=NOW - 7_200, rng=random.Random(1))  # streak day 1
        reset_daily(path, raw_id="1", now=NOW)
        claim = await bot_db.claim_daily(1, 100, 200, now=NOW, rng=random.Random(1))
    finally:
        await bot_db.close()
    assert claim.streak_day == 2  # the streak continued instead of restarting
    assert _ledger_is_true(path, 1)
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_admin_users_actions.py -q`
Expected: FAIL (`ModuleNotFoundError: yokai_admin.modules.users.actions`).

- [ ] **Step 3: Implement**

Create `src/yokai_admin/modules/users/actions.py`:

```python
"""People write side: balance adjustments and daily-cooldown resets, each ledger-safe and audited."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from yokai_admin.core.audit import record
from yokai_admin.core.db import write_transaction
from yokai_admin.core.inputs import InputRefused, clean_reason, parse_user_id
from yokai_admin.core.people import UserRef, user_refs
from yokai_admin.modules.users.service import DAILY_COOLDOWN_S

ADJUST_CAP = 100_000
_MAX_AMOUNT_DIGITS = 7


def parse_amount(raw: str) -> int:
    """A signed whole number of Renown: optional + or -, then plain ASCII digits."""
    text = raw.strip()
    sign = -1 if text.startswith("-") else 1
    digits = text[1:] if text[:1] in ("+", "-") else text
    if not (digits.isascii() and digits.isdigit()):
        raise InputRefused("Enter the amount as a whole number, such as 500 or -250.")
    if len(digits) > _MAX_AMOUNT_DIGITS:  # also keeps int() away from absurdly long input
        raise InputRefused(f"One adjustment is limited to {ADJUST_CAP:,} Renown.")
    value = sign * int(digits)
    if value == 0:
        raise InputRefused("An adjustment cannot be zero.")
    if abs(value) > ADJUST_CAP:
        raise InputRefused(f"One adjustment is limited to {ADJUST_CAP:,} Renown.")
    return value


@dataclass(frozen=True, slots=True)
class AdjustPreview:
    """What an adjustment would do, shown before anything is written."""

    user: UserRef
    before: int
    requested: int
    applied: int
    after: int

    @property
    def clamped(self) -> bool:
        return self.applied != self.requested


@dataclass(frozen=True, slots=True)
class AdjustResult:
    user: UserRef
    requested: int
    applied: int
    before: int
    after: int


@dataclass(frozen=True, slots=True)
class DailyResetResult:
    user: UserRef
    previous_last_daily_ts: int
    streak: int


def _no_wallet(ref: UserRef) -> InputRefused:
    return InputRefused(
        f"{ref.label} has no wallet yet. They get one the first time they use an economy command."
    )


def _outcome(before: int, amount: int) -> tuple[int, int]:
    """(applied, after) for a requested change; a balance never goes below 0."""
    after = max(0, before + amount)
    return after - before, after


def preview_adjustment(conn: sqlite3.Connection, user_id: int, amount: int) -> AdjustPreview:
    """Work out an adjustment without writing anything (used by the preview step)."""
    ref = user_refs(conn, [user_id])[user_id]
    row = conn.execute("SELECT balance FROM wallets WHERE user_id = ?;", (user_id,)).fetchone()
    if row is None:
        raise _no_wallet(ref)
    before = int(row["balance"])
    applied, after = _outcome(before, amount)
    if applied == 0:
        raise InputRefused(f"{ref.label} already has 0 Renown, so there is nothing to deduct.")
    return AdjustPreview(ref, before, amount, applied, after)


def adjust_balance(
    db_path: Path,
    *,
    raw_id: str,
    raw_amount: str,
    raw_reason: str,
    expected_balance: int,
    now: Optional[int] = None,
) -> AdjustResult:
    """Change a balance. Wallet, ledger row and audit row commit together, and only if the
    balance is still what the owner saw in the preview."""
    user_id = parse_user_id(raw_id)
    amount = parse_amount(raw_amount)
    reason = clean_reason(raw_reason, required=True)
    ts = int(time.time()) if now is None else now
    with write_transaction(db_path) as conn:
        ref = user_refs(conn, [user_id])[user_id]
        row = conn.execute("SELECT balance FROM wallets WHERE user_id = ?;", (user_id,)).fetchone()
        if row is None:
            raise _no_wallet(ref)
        before = int(row["balance"])
        if before != expected_balance:
            raise InputRefused(
                f"The balance changed from {expected_balance:,} to {before:,} after you previewed "
                "this. Check the new balance and try again."
            )
        applied, after = _outcome(before, amount)
        if applied == 0:
            raise InputRefused(f"{ref.label} already has 0 Renown, so there is nothing to deduct.")
        conn.execute(
            "UPDATE wallets SET balance = balance + ? WHERE user_id = ?;", (applied, user_id)
        )
        conn.execute(
            "INSERT INTO renown_transactions (ts, user_id, delta, reason, counterparty_id, "
            "balance_after) VALUES (?, ?, ?, 'admin', NULL, ?);",
            (ts, user_id, applied, after),
        )
        record(
            conn,
            module="users",
            action="balance.adjust",
            target=str(user_id),
            detail={
                "name": ref.name,
                "requested": amount,
                "applied": applied,
                "before": before,
                "after": after,
            },
            reason=reason,
            now=ts,
        )
    return AdjustResult(ref, amount, applied, before, after)


def reset_daily(db_path: Path, *, raw_id: str, now: Optional[int] = None) -> DailyResetResult:
    """Let a person claim /daily now without losing their streak.

    The last claim is back-dated to exactly one cooldown ago. Clearing it to NULL instead would
    make the bot treat the next claim as day 1 and restart the streak.
    """
    user_id = parse_user_id(raw_id)
    ts = int(time.time()) if now is None else now
    with write_transaction(db_path) as conn:
        ref = user_refs(conn, [user_id])[user_id]
        row = conn.execute(
            "SELECT last_daily_ts, daily_streak FROM wallets WHERE user_id = ?;", (user_id,)
        ).fetchone()
        if row is None:
            raise _no_wallet(ref)
        if row["last_daily_ts"] is None:
            raise InputRefused(f"{ref.label} has never claimed a daily reward, so there is nothing to reset.")
        previous = int(row["last_daily_ts"])
        if ts - previous >= DAILY_COOLDOWN_S:
            raise InputRefused(f"{ref.label} can already claim their daily reward.")
        conn.execute(
            "UPDATE wallets SET last_daily_ts = ? WHERE user_id = ?;", (ts - DAILY_COOLDOWN_S, user_id)
        )
        streak = int(row["daily_streak"])
        record(
            conn,
            module="users",
            action="daily.reset",
            target=str(user_id),
            detail={"name": ref.name, "previous_last_daily_ts": previous, "streak": streak},
            now=ts,
        )
    return DailyResetResult(ref, previous, streak)
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_admin_users_actions.py tests/test_economy_races.py -q`
Expected: PASS.

- [ ] **Step 5: Mutation-check, then commit**

Temporarily (a) remove the `before != expected_balance` check and confirm the stale-preview and double-confirm tests fail; (b) change `_outcome` to not clamp (`after = before + amount`) and confirm the clamping tests fail; (c) change `parse_amount`'s `isascii()` check to `isdigit()` only and confirm the Unicode-digit cases fail. Restore each (diff must be empty) and run the file again. Then:

```powershell
git add src/yokai_admin/modules/users tests/test_admin_users_actions.py
git commit -m "feat(admin): add ledger-safe balance adjustments and daily resets" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: People module UI (routes, templates, audit labels, tile)

**Files:**
- Create: `src/yokai_admin/modules/users/router.py`, `src/yokai_admin/modules/users/templates/users/page.html`, `_list.html`, `_person.html`, `_adjust_form.html`, `_adjust_confirm.html`, `_daily_button.html`, `_daily_confirm.html`
- Modify: `src/yokai_admin/modules/users/__init__.py`, `src/yokai_admin/modules/__init__.py`, `src/yokai_admin/modules/audit/service.py`, `src/yokai_admin/static/panel.css`, `tests/test_admin_static.py`
- Test: `tests/test_admin_users.py`, `tests/test_admin_audit.py` (append)

**Interfaces:**
- Consumes: Tasks 2 to 4; `render`, `render_fragment`; `parse_user_id`, `clean_reason`, `InputRefused`.
- Produces: `MODULE = AdminModule(id="users", title="People", nav_order=5, tile=users_tile, ...)`; routes under `/m/users`:
  - `GET ""` (list; query params `q`, `page`; the search text is cleaned and capped at 80 characters),
  - `GET "/{user_id}"` (profile page; `user_id` is a `str` parsed by `parse_user_id`),
  - `GET "/{user_id}/adjust/form"` (fragment, target `#adjust`),
  - `POST "/{user_id}/adjust/preview"` (form `amount`, `reason`; answers the confirm fragment or the form with an error line and the typed values),
  - `POST "/{user_id}/adjust"` (form `amount`, `reason`, `expected`; answers the whole `#person` section with a flash),
  - `GET "/{user_id}/daily/button"` and `GET "/{user_id}/daily/confirm"` (fragments, target `#daily`),
  - `POST "/{user_id}/daily/reset"` (answers `#person` with a flash).
  Every response is HTTP 200; refusals are flash lines. `users_tile(ctx) -> Tile("People", "<n> known, <m> with wallets")`. Audit: `action_label` knows `balance.adjust` ("Adjusted Renown") and `daily.reset` ("Reset the daily cooldown"); `_person_id` also resolves names for `module == "users"` targets.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_admin_users.py`:

```python
"""Tests for the People module through the real app."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from tests.admin_helpers import panel_client, post, seed_activity, seed_database
from yokai_admin.core import db as panel_db

USERS = "yokai_admin.modules.users:MODULE"
NOW = int(time.time())


def _scalar(path: Path, sql: str, *params: object) -> object:
    raw = sqlite3.connect(path)
    try:
        return raw.execute(sql, params).fetchone()[0]
    finally:
        raw.close()


def _seeded(tmp_path: Path) -> Path:
    db = tmp_path / "yokai.db"
    seed_database(db, credits={1: 300, 2: 50}, names={1: "Alex", 2: "Mallory", 3: "Riley"})
    seed_activity(
        db,
        plays=[(1, "v1", "Song One", "Artist A", NOW - 5)] * 2,
        bets=[(1, "slots", 50, 120, "settled", None)],
        user_commands=[(1, "play", NOW - 60), (3, "skip", NOW - 30)],
        transactions=[(1, -50, "pay_sent", 2, 450, NOW - 20)],
        daily={1: (NOW - 3_600, 4)},
    )
    return db


def test_the_list_shows_people_with_balances_and_links(tmp_path: Path) -> None:
    db = _seeded(tmp_path)
    with panel_client(tmp_path, [USERS], db_path=db) as (client, _):
        page = client.get("/m/users").text
    assert "<strong>Alex</strong>" in page and "<strong>Riley</strong>" in page
    assert 'href="/m/users/1"' in page and "500" in page
    assert 'aria-current="page">People</a>' in page
    assert 'name="q"' in page


def test_search_filters_and_a_miss_says_so(tmp_path: Path) -> None:
    db = _seeded(tmp_path)
    with panel_client(tmp_path, [USERS], db_path=db) as (client, _):
        hit = client.get("/m/users", params={"q": "mall"}).text
        miss = client.get("/m/users", params={"q": "zzz"}).text
        wildcard = client.get("/m/users", params={"q": "%"}).text
    assert "<strong>Mallory</strong>" in hit and "<strong>Alex</strong>" not in hit
    assert "No one matches" in miss
    assert "<strong>Alex</strong>" not in wildcard  # '%' is literal


def test_the_list_pages(tmp_path: Path) -> None:
    db = tmp_path / "yokai.db"
    seed_database(db, names={i: f"Person {i:02d}" for i in range(1, 31)})
    with panel_client(tmp_path, [USERS], db_path=db) as (client, _):
        first = client.get("/m/users").text
        second = client.get("/m/users", params={"page": "2"}).text
        junk = client.get("/m/users", params={"page": "9" * 5000})
    assert "Older" in first and "Newer" not in first and "Newer" in second
    assert junk.status_code == 200


def test_a_profile_shows_wallet_ledger_games_tracks_and_commands(tmp_path: Path) -> None:
    db = _seeded(tmp_path)
    with panel_client(tmp_path, [USERS], db_path=db) as (client, _):
        page = client.get("/m/users/1").text
    assert "Alex" in page and "500" in page  # balance
    assert "Daily streak" in page and ">4<" in page
    assert "Sent to" in page and "Mallory" in page  # ledger row with the other person
    assert "Slots" in page and "+70" in page  # settled bet: wagered 50, returned 120
    assert "Song One" in page and "Artist A" in page
    assert "play" in page
    assert 'id="person"' in page


def test_a_person_with_no_wallet_gets_a_profile_without_money_controls(tmp_path: Path) -> None:
    db = _seeded(tmp_path)
    with panel_client(tmp_path, [USERS], db_path=db) as (client, _):
        page = client.get("/m/users/3").text
    assert "Riley" in page and "No wallet yet" in page
    assert "/adjust/preview" not in page and "/daily/" not in page


@pytest.mark.parametrize("raw", ["999", "abc", "١٢", "9" * 5000, "0", "-5"])
def test_an_unknown_or_malformed_id_answers_with_a_message_not_an_error(tmp_path: Path, raw: str) -> None:
    """Review Focus 6."""
    db = _seeded(tmp_path)
    with panel_client(tmp_path, [USERS], db_path=db) as (client, _):
        response = client.get(f"/m/users/{raw}")
    assert response.status_code == 200 and "No one" in response.text


def test_the_adjust_flow_previews_then_confirms_then_applies(tmp_path: Path) -> None:
    db = _seeded(tmp_path)
    with panel_client(tmp_path, [USERS], db_path=db) as (client, csrf):
        preview = post(client, csrf, "/m/users/1/adjust/preview", amount="+250", reason="event prize")
        assert "500" in preview.text and "750" in preview.text and "+250" in preview.text
        assert 'name="expected" value="500"' in preview.text and "Confirm" in preview.text
        assert _scalar(db, "SELECT balance FROM wallets WHERE user_id = 1") == 500  # nothing yet
        done = post(client, csrf, "/m/users/1/adjust", amount="+250", reason="event prize", expected="500")
    assert done.status_code == 200 and 'id="person"' in done.text and "750" in done.text
    assert _scalar(db, "SELECT balance FROM wallets WHERE user_id = 1") == 750
    assert _scalar(db, "SELECT COUNT(*) FROM admin_audit WHERE module = 'users'") == 1


def test_a_clamped_deduction_is_explained_in_the_preview(tmp_path: Path) -> None:
    db = _seeded(tmp_path)
    with panel_client(tmp_path, [USERS], db_path=db) as (client, csrf):
        preview = post(client, csrf, "/m/users/1/adjust/preview", amount="-900", reason="reset exploit")
    assert "below 0" in preview.text and "-500" in preview.text


@pytest.mark.parametrize(
    ("form", "message"),
    [
        ({"amount": "abc", "reason": "gift"}, "whole number"),
        ({"amount": "0", "reason": "gift"}, "cannot be zero"),
        ({"amount": "100001", "reason": "gift"}, "100,000"),
        ({"amount": "50", "reason": "x"}, "reason"),
        ({"amount": "50"}, "reason"),
        ({}, "whole number"),
    ],
)
def test_a_refused_preview_keeps_the_typed_values_and_changes_nothing(
    tmp_path: Path, form: dict[str, str], message: str
) -> None:
    db = _seeded(tmp_path)
    with panel_client(tmp_path, [USERS], db_path=db) as (client, csrf):
        response = post(client, csrf, "/m/users/1/adjust/preview", **form)
    assert response.status_code == 200 and message in response.text
    assert 'data-kind="error"' in response.text
    if form.get("reason"):
        assert f'value="{form["reason"]}"' in response.text
    assert _scalar(db, "SELECT balance FROM wallets WHERE user_id = 1") == 500


def test_a_stale_confirm_explains_and_refreshes(tmp_path: Path) -> None:
    db = _seeded(tmp_path)
    with panel_client(tmp_path, [USERS], db_path=db) as (client, csrf):
        response = post(client, csrf, "/m/users/1/adjust", amount="+10", reason="gift", expected="400")
    assert "changed from 400 to 500" in response.text and 'id="person"' in response.text
    assert _scalar(db, "SELECT balance FROM wallets WHERE user_id = 1") == 500


def test_the_daily_reset_flow_asks_first(tmp_path: Path) -> None:
    db = _seeded(tmp_path)
    with panel_client(tmp_path, [USERS], db_path=db) as (client, csrf):
        asked = client.get("/m/users/1/daily/confirm", headers={"HX-Request": "true"})
        assert "Reset" in asked.text and "<html" not in asked.text
        before = _scalar(db, "SELECT last_daily_ts FROM wallets WHERE user_id = 1")
        done = post(client, csrf, "/m/users/1/daily/reset")
    assert "can claim" in done.text or "daily" in done.text.lower()
    after = _scalar(db, "SELECT last_daily_ts FROM wallets WHERE user_id = 1")
    assert before == NOW - 3_600 and after is not None and after <= int(time.time()) - 86_399
    assert _scalar(db, "SELECT daily_streak FROM wallets WHERE user_id = 1") == 4
    assert _scalar(db, "SELECT COUNT(*) FROM admin_audit WHERE action = 'daily.reset'") == 1


def test_write_endpoints_need_a_session_and_the_csrf_token_and_gets_change_nothing(tmp_path: Path) -> None:
    """Review Focus 6."""
    db = _seeded(tmp_path)
    form = {"amount": "+10", "reason": "gift", "expected": "500"}
    with panel_client(tmp_path, [USERS], db_path=db, login=False) as (client, _):
        assert client.post("/m/users/1/adjust", data=form).status_code == 401
    with panel_client(tmp_path, [USERS], db_path=db) as (client, csrf):
        assert client.post("/m/users/1/adjust", data=form).status_code == 403
        assert client.post("/m/users/1/daily/reset").status_code == 403
        for path in ("/m/users/1", "/m/users/1/adjust/form", "/m/users/1/daily/button", "/m/users/1/daily/confirm"):
            assert client.get(path, headers={"HX-Request": "true"}).status_code == 200
        assert client.get("/m/users/1/adjust").status_code == 405
        assert client.get("/m/users/1/daily/reset").status_code == 405
    assert _scalar(db, "SELECT balance FROM wallets WHERE user_id = 1") == 500
    assert _scalar(db, "SELECT COUNT(*) FROM admin_audit") == 0


def test_hostile_names_titles_commands_and_reasons_are_escaped(tmp_path: Path) -> None:
    """Review Focus 5."""
    db = tmp_path / "yokai.db"
    evil = "<img src=x onerror=alert(1)>"
    seed_database(db, credits={1: 300}, names={1: evil})
    seed_activity(
        db,
        plays=[(1, "v1", "<script>alert(1)</script>", evil, NOW)],
        user_commands=[(1, "<b>play</b>", NOW)],
        transactions=[(1, 5, "<i>odd</i>", None, 505, NOW)],
    )
    with panel_client(tmp_path, [USERS], db_path=db) as (client, csrf):
        profile = client.get("/m/users/1").text
        lst = client.get("/m/users", params={"q": "img"}).text
        refused = post(client, csrf, "/m/users/1/adjust/preview", amount="5", reason=f'"{evil}')
    for page in (profile, lst, refused.text):
        assert "<script>alert(1)</script>" not in page and "<img src=x" not in page
        assert "<b>play</b>" not in page and "<i>odd</i>" not in page


def test_a_busy_or_missing_database_answers_with_a_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review Focus 6."""
    db = _seeded(tmp_path)
    monkeypatch.setattr(panel_db, "WRITE_BUSY_TIMEOUT_S", 0.2)
    holder = sqlite3.connect(db, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE;")
    try:
        with panel_client(tmp_path, [USERS], db_path=db) as (client, csrf):
            busy = post(client, csrf, "/m/users/1/adjust", amount="+10", reason="gift", expected="500")
    finally:
        holder.execute("ROLLBACK;")
        holder.close()
    assert busy.status_code == 200 and "busy right now" in busy.text
    with panel_client(tmp_path, [USERS], db_path=tmp_path / "absent.db") as (client, csrf):
        assert "not found" in client.get("/m/users").text
        assert "not found" in client.get("/m/users/1").text
        assert "not found" in post(client, csrf, "/m/users/1/adjust", amount="5", reason="gift", expected="1").text


def test_the_dashboard_tile_counts_people(tmp_path: Path) -> None:
    db = _seeded(tmp_path)
    paths = ["yokai_admin.modules.dashboard:MODULE", USERS]
    with panel_client(tmp_path, paths, db_path=db) as (client, _):
        page = client.get("/m/dashboard").text
    assert "People" in page and "3 known, 2 with wallets" in page
```

Append to `tests/test_admin_audit.py`:

```python
def test_the_audit_log_labels_and_names_people_for_balance_changes(tmp_path: Path) -> None:
    from yokai_admin.modules.users.actions import adjust_balance, reset_daily

    db = tmp_path / "yokai.db"
    seed_database(db, credits={1: 300}, names={1: "Alex"})
    adjust_balance(db, raw_id="1", raw_amount="-900", raw_reason="reset exploit", expected_balance=500, now=1_000)
    with panel_client(tmp_path, [AUDIT], db_path=db) as (client, _):
        page = client.get("/m/audit").text
    assert "Adjusted Renown" in page and "<strong>Alex</strong>" in page and "reset exploit" in page
    assert "applied: -500" in page and "requested: -900" in page
    assert action_label("daily.reset") == "Reset the daily cooldown"
```

Append to `tests/test_admin_static.py`:

```python
def test_people_page_styles_exist() -> None:
    css = _css()
    for selector in (".num", ".pos", ".neg", ".person-head", ".confirm-box", ".search"):
        assert selector in css
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_admin_users.py tests/test_admin_audit.py tests/test_admin_static.py -q`
Expected: FAIL (no router, templates, styles; audit labels missing).

- [ ] **Step 3: Implement**

Create `src/yokai_admin/modules/users/router.py`:

```python
"""People routes: the list, a person's profile, and the two money actions behind a confirm step."""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from fastapi import APIRouter, Form, Request
from fastapi.responses import Response

from yokai_admin.core.db import readonly, require_schema
from yokai_admin.core.inputs import InputRefused, clean_reason, parse_page, parse_user_id
from yokai_admin.core.settings import PanelSettings
from yokai_admin.core.templating import render, render_fragment
from yokai_admin.errors import PanelDbError
from yokai_admin.modules.users.actions import (
    AdjustPreview,
    adjust_balance,
    parse_amount,
    preview_adjustment,
    reset_daily,
)
from yokai_admin.modules.users.service import (
    PAGE_SIZE,
    Person,
    Profile,
    list_people,
    load_profile,
)

logger = logging.getLogger(__name__)
router = APIRouter()
_SAVE_FAILED = "The database could not be changed right now. Nothing was saved."
_NOT_KNOWN = "No one with that ID is known to the bot yet."
_MAX_QUERY = 80


@dataclass(frozen=True, slots=True)
class Flash:
    """A one-line result shown above the profile."""

    kind: str  # "ok" or "error"
    text: str


def _clean_query(raw: str) -> str:
    return " ".join(raw.split())[:_MAX_QUERY]


def _db_message(exc: Exception) -> str:
    if isinstance(exc, PanelDbError):
        return str(exc)
    logger.warning("People could not read the database: %s", exc)
    return f"The database could not be read right now ({exc}). It will retry shortly."


@router.get("")
def people(request: Request, q: str = "", page: str = "1") -> Response:
    settings: PanelSettings = request.app.state.settings
    query, number = _clean_query(q), parse_page(page)
    rows: list[Person] = []
    total = 0
    db_error: Optional[str] = None
    try:
        with readonly(settings.db_path) as conn:
            require_schema(conn)
            rows, total = list_people(
                conn, query=query, limit=PAGE_SIZE, offset=(number - 1) * PAGE_SIZE
            )
    except (PanelDbError, sqlite3.Error) as exc:
        db_error = _db_message(exc)
    return render(
        request,
        "users/page.html",
        active="users",
        rows=rows,
        total=total,
        query=query,
        page_number=number,
        has_older=number * PAGE_SIZE < total,
        has_newer=number > 1,
        db_error=db_error,
        profile_view=None,
    )


def _profile_context(
    request: Request, raw_id: str, flash: Optional[Flash], **extra: Any
) -> dict[str, Any]:
    settings: PanelSettings = request.app.state.settings
    profile: Optional[Profile] = None
    problem: Optional[str] = None
    user_id: Optional[int] = None
    try:
        user_id = parse_user_id(raw_id)
        with readonly(settings.db_path) as conn:
            require_schema(conn)
            profile = load_profile(conn, user_id, int(time.time()))
        if profile is None:
            problem = _NOT_KNOWN
    except InputRefused:
        problem = _NOT_KNOWN
    except (PanelDbError, sqlite3.Error) as exc:
        problem = _db_message(exc)
    return {"profile": profile, "problem": problem, "flash": flash, "user_id": user_id, **extra}


def _person(request: Request, raw_id: str, flash: Optional[Flash] = None) -> Response:
    return render_fragment(request, "users/_person.html", **_profile_context(request, raw_id, flash))


@router.get("/{user_id}")
def person_page(request: Request, user_id: str) -> Response:
    context = _profile_context(request, user_id, None)
    return render(request, "users/page.html", active="users", profile_view=True, **context)


@router.get("/{user_id}/adjust/form")
def adjust_form(request: Request, user_id: str) -> Response:
    return render_fragment(request, "users/_adjust_form.html", user_id=user_id, form={})


@router.post("/{user_id}/adjust/preview")
def adjust_preview(
    request: Request, user_id: str, amount: str = Form(""), reason: str = Form("")
) -> Response:
    settings: PanelSettings = request.app.state.settings
    form = {"amount": amount, "reason": reason}
    try:
        uid = parse_user_id(user_id)
        value = parse_amount(amount)
        clean = clean_reason(reason, required=True)
        with readonly(settings.db_path) as conn:
            require_schema(conn)
            preview: AdjustPreview = preview_adjustment(conn, uid, value)
    except (InputRefused, PanelDbError, sqlite3.Error) as exc:
        message = str(exc) if isinstance(exc, (InputRefused, PanelDbError)) else _SAVE_FAILED
        return render_fragment(
            request,
            "users/_adjust_form.html",
            user_id=user_id,
            form=form,
            form_flash=Flash("error", message),
        )
    return render_fragment(
        request, "users/_adjust_confirm.html", user_id=uid, preview=preview, reason=clean
    )


def _act(
    request: Request, raw_id: str, action: Callable[[PanelSettings], str]
) -> Response:
    """Run one write; answer with the refreshed profile and a one-line result."""
    settings: PanelSettings = request.app.state.settings
    try:
        message = action(settings)
    except (InputRefused, PanelDbError) as exc:
        return _person(request, raw_id, Flash("error", str(exc)))
    except sqlite3.Error as exc:
        logger.warning("People write failed: %s", exc)
        return _person(request, raw_id, Flash("error", _SAVE_FAILED))
    return _person(request, raw_id, Flash("ok", message))


@router.post("/{user_id}/adjust")
def adjust(
    request: Request,
    user_id: str,
    amount: str = Form(""),
    reason: str = Form(""),
    expected: str = Form(""),
) -> Response:
    def run(settings: PanelSettings) -> str:
        stamp = expected.strip()
        if not (stamp.isascii() and stamp.isdigit()) or len(stamp) > 19:
            raise InputRefused("That confirmation is out of date. Preview the change again.")
        result = adjust_balance(
            settings.db_path,
            raw_id=user_id,
            raw_amount=amount,
            raw_reason=reason,
            expected_balance=int(stamp),
        )
        note = " (the balance cannot go below 0)" if result.applied != result.requested else ""
        return (
            f"{result.user.label}: {result.before:,} to {result.after:,} Renown "
            f"({result.applied:+,}){note}."
        )

    return _act(request, user_id, run)


@router.get("/{user_id}/daily/button")
def daily_button(request: Request, user_id: str) -> Response:
    return render_fragment(request, "users/_daily_button.html", user_id=user_id)


@router.get("/{user_id}/daily/confirm")
def daily_confirm(request: Request, user_id: str) -> Response:
    context = _profile_context(request, user_id, None)
    if context["profile"] is None or context["profile"].wallet is None:
        return render_fragment(request, "users/_person.html", **context)
    return render_fragment(request, "users/_daily_confirm.html", **context)


@router.post("/{user_id}/daily/reset")
def daily_reset(request: Request, user_id: str) -> Response:
    def run(settings: PanelSettings) -> str:
        result = reset_daily(settings.db_path, raw_id=user_id)
        return f"{result.user.label} can claim their daily reward now. Their streak of {result.streak} is kept."

    return _act(request, user_id, run)


def users_counts(conn: sqlite3.Connection) -> tuple[int, int]:
    """(people known, people with wallets) for the Dashboard readout."""
    known = int(
        conn.execute(
            "SELECT COUNT(*) FROM (SELECT user_id FROM wallets UNION SELECT user_id FROM user_names);"
        ).fetchone()[0]
    )
    wallets = int(conn.execute("SELECT COUNT(*) FROM wallets;").fetchone()[0])
    return known, wallets
```

Because the stale-confirm and confirm-fragment routes reuse the profile section, `_person.html` receives the keys `profile`, `problem`, `flash`, `user_id`. The `/{user_id}/adjust/preview` confirm template receives `user_id` as the parsed integer.

Create the templates (`src/yokai_admin/modules/users/templates/users/`):

`page.html`:

```html
{% extends "base.html" %}
{% block title %}People - Yokai admin{% endblock %}
{% block content %}
  {% if profile_view %}
    <p class="crumb"><a href="/m/users">All people</a></p>
    {% include "users/_person.html" %}
  {% else %}
    <h1 class="page-title">People</h1>
    <p class="lede">Everyone the bot has met. Open a person to see their wallet, music and commands, and to correct a balance.</p>
    {% include "users/_list.html" %}
  {% endif %}
{% endblock %}
```

`_list.html`:

```html
{% if db_error %}<p class="flash" data-kind="error" role="alert"><span class="sr-only">Problem: </span>{{ db_error }}</p>{% endif %}
<form class="filters search" method="get" action="/m/users">
  <label>Search by name or Discord user ID<input name="q" value="{{ query }}" maxlength="80" autocomplete="off"></label>
  <button type="submit" class="btn">Search</button>
  {% if query %}<a href="/m/users">Clear</a>{% endif %}
</form>
{% if rows %}
<div class="table-wrap"><table class="table">
  <thead><tr><th scope="col">Person</th><th scope="col" class="num">Renown</th><th scope="col">Last command</th></tr></thead>
  <tbody>
  {% for p in rows %}
    <tr>
      <td class="who"><a href="/m/users/{{ p.user.id }}"><strong>{{ p.user.display }}</strong></a><span class="id">{{ p.user.id }}</span></td>
      <td class="num">{{ p.balance|number if p.balance is not none else "No wallet" }}</td>
      <td class="when">{{ p.last_seen_ts|when }}</td>
    </tr>
  {% endfor %}
  </tbody>
</table></div>
<p class="pager">
  {% if has_newer %}<a href="/m/users?q={{ query|urlencode }}&amp;page={{ page_number - 1 }}">Newer</a>{% endif %}
  {% if has_older %}<a href="/m/users?q={{ query|urlencode }}&amp;page={{ page_number + 1 }}">Older</a>{% endif %}
</p>
{% elif not db_error %}
<p class="empty">{% if query %}No one matches that search.{% else %}The bot has not met anyone yet.{% endif %}</p>
{% endif %}
```

(`when` renders an en dash for `None`, so people with no commands show "-".)

`_person.html`:

```html
<section id="person">
  {% if flash %}<p class="flash" data-kind="{{ flash.kind }}" role="{{ 'alert' if flash.kind == 'error' else 'status' }}"><span class="sr-only">{{ 'Problem' if flash.kind == 'error' else 'Done' }}: </span>{{ flash.text }}</p>{% endif %}
  {% if not profile %}
    <h1 class="page-title">No one here</h1>
    <p class="lede">{{ problem }}</p>
  {% else %}
  <div class="person-head">
    <h1 class="page-title">{{ profile.user.display }}</h1>
    <p class="lede">Discord ID {{ profile.user.id }}. {{ profile.commands_total|number }} commands{% if profile.last_seen_ts %}, last one {{ profile.last_seen_ts|when }}{% endif %}.</p>
  </div>

  <div class="section">
    <h2 class="section-title">Wallet</h2>
    {% if profile.wallet %}
    <dl class="readouts">
      <dt>Renown</dt><dd>{{ profile.wallet.balance|number }}</dd>
      <dt>Daily streak</dt><dd>{{ profile.wallet.daily_streak }}</dd>
      <dt>Last daily reward</dt><dd>{{ profile.wallet.last_daily_ts|when }}</dd>
      <dt>Next daily reward</dt><dd>{% if profile.wallet.daily_ready_in is none %}Ready now{% else %}In {{ profile.wallet.daily_ready_in|age }}{% endif %}</dd>
      <dt>Wallet created</dt><dd>{{ profile.wallet.created_ts|when }}</dd>
    </dl>
    {% include "users/_adjust_form.html" %}
    {% include "users/_daily_button.html" %}
    {% else %}
    <p class="empty">No wallet yet. They get one the first time they use an economy command.</p>
    {% endif %}
  </div>

  {% if profile.wallet %}
  <div class="section">
    <h2 class="section-title">Recent Renown activity</h2>
    {% if profile.transactions %}
    <div class="table-wrap"><table class="table">
      <thead><tr><th scope="col">When</th><th scope="col">What</th><th scope="col" class="num">Change</th><th scope="col" class="num">Balance after</th></tr></thead>
      <tbody>
      {% for t in profile.transactions %}
        <tr>
          <td class="when">{{ t.ts|when }}</td>
          <td>{{ t.label }}{% if t.counterparty %} <a href="/m/users/{{ t.counterparty.id }}">{{ t.counterparty.display }}</a>{% endif %}</td>
          <td class="num {{ 'pos' if t.delta > 0 else 'neg' if t.delta < 0 else '' }}">{{ t.delta|signed }}</td>
          <td class="num">{{ t.balance_after|number }}</td>
        </tr>
      {% endfor %}
      </tbody>
    </table></div>
    {% else %}<p class="empty">Nothing on the ledger yet.</p>{% endif %}
  </div>
  {% endif %}

  <div class="section">
    <h2 class="section-title">Gambling</h2>
    {% if profile.games %}
    <div class="table-wrap"><table class="table">
      <thead><tr><th scope="col">Game</th><th scope="col" class="num">Plays</th><th scope="col" class="num">Wagered</th><th scope="col" class="num">Returned</th><th scope="col" class="num">Net</th></tr></thead>
      <tbody>
      {% for g in profile.games %}
        <tr><td>{{ g.game|capitalize }}</td><td class="num">{{ g.plays|number }}</td><td class="num">{{ g.wagered|number }}</td><td class="num">{{ g.returned|number }}</td><td class="num {{ 'pos' if g.net > 0 else 'neg' if g.net < 0 else '' }}">{{ g.net|signed }}</td></tr>
      {% endfor %}
      </tbody>
    </table></div>
    {% else %}<p class="empty">No finished games yet.</p>{% endif %}
  </div>

  <div class="section">
    <h2 class="section-title">Most played tracks</h2>
    {% if profile.tracks %}
    <ol class="plain-list">{% for t in profile.tracks %}<li><strong>{{ t.title }}</strong>{% if t.artist %} by {{ t.artist }}{% endif %}, {{ t.plays|number }} {{ 'play' if t.plays == 1 else 'plays' }}</li>{% endfor %}</ol>
    {% else %}<p class="empty">No tracks played yet.</p>{% endif %}
  </div>

  <div class="section">
    <h2 class="section-title">Most used commands</h2>
    {% if profile.commands %}
    <ul class="plain-list">{% for c in profile.commands %}<li><strong>{{ c.command }}</strong>, {{ c.count|number }}</li>{% endfor %}</ul>
    {% else %}<p class="empty">No commands used yet.</p>{% endif %}
  </div>
  {% endif %}
</section>
```

(An ordered list for tracks is a ranking, so numbering is real content here.)

`_adjust_form.html`:

```html
{% set typed = form or {} %}
<div id="adjust">
  <form class="add" hx-post="/m/users/{{ user_id }}/adjust/preview" hx-target="#adjust" hx-swap="outerHTML">
    {% if form_flash %}<p class="flash" data-kind="{{ form_flash.kind }}" role="alert"><span class="sr-only">Problem: </span>{{ form_flash.text }}</p>{% endif %}
    <label>Change in Renown (for example +500 or -250)<input name="amount" inputmode="numeric" autocomplete="off" value="{{ typed.get('amount', '') }}" required></label>
    <label>Reason (kept in the audit log)<input name="reason" maxlength="200" autocomplete="off" value="{{ typed.get('reason', '') }}" required></label>
    <button type="submit" class="btn">Preview change</button>
  </form>
</div>
```

The error line inside the form is laid out by the CSS rule `.add .flash { grid-column: 1 / -1; }` (added below); the CSP forbids inline `style` attributes.

`_adjust_confirm.html`:

```html
<div id="adjust" class="confirm-box">
  <p><strong>{{ preview.user.display }}</strong> ({{ preview.user.id }}): {{ preview.before|number }} to <strong>{{ preview.after|number }}</strong> Renown ({{ preview.applied|signed }}).</p>
  {% if preview.clamped %}<p class="hint">{{ preview.requested|signed }} would take the balance below 0, so the change is limited to {{ preview.applied|signed }} and the balance ends at 0.</p>{% endif %}
  <p class="hint">Reason: {{ reason }}</p>
  <form hx-post="/m/users/{{ user_id }}/adjust" hx-target="#person" hx-swap="outerHTML" class="confirm-row">
    <input type="hidden" name="amount" value="{{ preview.requested }}">
    <input type="hidden" name="reason" value="{{ reason }}">
    <input type="hidden" name="expected" value="{{ preview.before }}">
    <button type="submit" class="btn danger" autofocus>Confirm change</button>
    <button type="button" class="btn plain" hx-get="/m/users/{{ user_id }}/adjust/form" hx-target="#adjust" hx-swap="outerHTML">Cancel</button>
  </form>
</div>
```

(`name="expected" value="500"` exactly, as the test expects: Jinja prints the integer without separators when not piped through `number`.)

The region wrappers (`<div id="adjust">`, `<div id="daily">`) live **inside** the fragment templates, because the swaps use `hx-swap="outerHTML"` and would otherwise drop the id. `_person.html` therefore just includes `users/_adjust_form.html` and `users/_daily_button.html` with no wrapper of its own.

`_daily_button.html`:

```html
<div id="daily">
  <button type="button" class="link-danger" hx-get="/m/users/{{ user_id }}/daily/confirm" hx-target="#daily" hx-swap="outerHTML">Reset daily cooldown</button>
</div>
```

`_daily_confirm.html`:

```html
<div id="daily" class="confirm-box">
  <p>Let <strong>{{ profile.user.display }}</strong> claim their daily reward now? Their streak of {{ profile.wallet.daily_streak }} is kept.</p>
  <div class="confirm-row">
    <button type="button" class="btn danger" hx-post="/m/users/{{ profile.user.id }}/daily/reset" hx-target="#person" hx-swap="outerHTML">Reset cooldown</button>
    <button type="button" class="btn plain" hx-get="/m/users/{{ profile.user.id }}/daily/button" hx-target="#daily" hx-swap="outerHTML" autofocus>Cancel</button>
  </div>
</div>
```

`modules/users/__init__.py`:

```python
"""People module: who the bot knows, their wallets, and the two money corrections."""

from __future__ import annotations

from pathlib import Path

from yokai_admin.core.db import readonly
from yokai_admin.core.module import AdminModule, Tile, TileContext
from yokai_admin.modules.users.router import router, users_counts


def users_tile(ctx: TileContext) -> Tile:
    """Dashboard readout: how many people the bot knows."""
    with readonly(ctx.settings.db_path) as conn:
        known, wallets = users_counts(conn)
    return Tile(title="People", value=f"{known} known, {wallets} with wallets")


MODULE = AdminModule(
    id="users",
    title="People",
    router=router,
    nav_order=5,
    templates_dir=Path(__file__).parent / "templates",
    tile=users_tile,
)
```

Register `"yokai_admin.modules.users:MODULE"` in `MODULE_PATHS` (after the dashboard entry).

Audit: in `modules/audit/service.py` add to `_LABELS`: `"balance.adjust": "Adjusted Renown"` and `"daily.reset": "Reset the daily cooldown"`; change `_person_id` to `if entry.module in ("access", "users") and ...` and the matching condition in `build_page`.

CSS (append to `panel.css`):

```css
/* People */

.crumb {
  margin: 0 0 1rem;
}

.person-head {
  margin-bottom: 2rem;
}

.search {
  margin-bottom: 1.5rem;
}

.num {
  text-align: right;
}

.pos {
  color: var(--lichen);
}

.neg {
  color: var(--flare);
}

.plain-list {
  margin: 0;
  padding-left: 1.25rem;
}

.confirm-box {
  max-width: 56rem;
  margin-top: 1rem;
  padding: 0.75rem 1rem;
  border-left: 3px solid var(--flare);
  background: color-mix(in srgb, var(--flare) 8%, transparent);
}

.add .flash {
  grid-column: 1 / -1;
  margin-bottom: 0;
}
```

(The `.add .flash` rule replaces the inline style.)

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_admin_users.py tests/test_admin_audit.py tests/test_admin_access.py tests/test_admin_static.py tests/test_admin_app.py tests/test_admin_dashboard.py -q`
Expected: PASS. If an assertion is brittle against markup (for example the Gambling "+70" line), keep its intent: the settled bet's wagered 50 and returned 120 appear with a net of +70.

- [ ] **Step 5: Commit**

```powershell
git add src/yokai_admin tests
git commit -m "feat(admin): add the People module with ledger-safe balance edits" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Docs, real-browser check, full verification

**Files:**
- Modify: `README.md`, `TASKS.md`, `docs/superpowers/specs/2026-10-05-admin-panel-design.md`

- [ ] **Step 1: Update the docs**

- Design spec §7 Users: replace the paragraph with what was built: People page (list with search by name or ID, paged by 25); profile sections (wallet, recent Renown activity, gambling per game, most played tracks, most used commands); balance adjustment (signed amount, reason of 3 to 200 characters, preview with before and after, confirm, a 100,000 cap, never below 0, applied amount recorded, refused when the person has no wallet or the balance changed since the preview); daily reset (back-dates the last claim by exactly 24 hours so the streak is kept; refused when never claimed or already claimable); every change is one `BEGIN IMMEDIATE` transaction (wallet, `renown_transactions` row with reason `admin`, audit row). Add a sentence to the cross-process rule: "The bot's own `admin_adjust_wallet`, `claim_daily` and `pay` take the write lock before reading (`Database._write_tx`), so a panel edit can never fall between their read and write."
- `README.md` Admin panel section: add that People lets the owner correct a balance or reset a daily cooldown, and that these changes are on the Audit page.
- `TASKS.md`: tick P-A4 and add this block before the final `**STOP — owner review.**`:

```markdown
**Acceptance criteria (P-A4)**
- The bot's `/economy-admin`, `/daily` and `/pay` paths take SQLite's write lock before reading a
  wallet; a panel edit injected between their read and write is blocked until they finish, and the
  ledger invariant (balance equals the sum of deltas, newest balance_after equals the balance)
  holds after any mix of panel and bot operations; a refused or failed bot operation never leaves
  the lock held.
- People lists everyone with a wallet or a remembered name, newest activity first, paged by 25, and
  searches by name fragment or ID fragment (wildcards are literal).
- A profile shows wallet, daily streak and readiness, the 25 newest ledger rows (with the other
  person's name), settled gambling per game, the five most played tracks and most used commands.
- Balance adjustment: signed whole number up to 100,000, reason required, preview showing before and
  after and any clamping, confirm step, refused when the balance changed since the preview or the
  person has no wallet; a deduction never goes below 0 and the ledger records the applied amount;
  wallet, ledger row and audit entry commit together; two confirms from one preview apply once.
- Daily reset keeps the streak (the bot lets the person claim and continues it) and refuses when
  there is nothing to reset.
- Unknown or malformed IDs, missing or old databases and busy databases answer with a message;
  unexpected failed requests show a visible notice; refused forms keep what was typed.
- Hostile names, titles, commands and reasons are escaped; write endpoints need a session and CSRF.
- `pytest`, `ruff check` and `ruff format --check` are clean.
- *(driven in headless Edge while building)* the real search, profile, preview, confirm, stale
  preview, daily reset and failed-request notice flows work against a real database in dark, light
  and 390 px layouts.
- *(manual, real browser and real bot)* Adjust a test account's balance up and down from the panel
  and confirm `/balance` (or the bot's wallet command) shows the new amount immediately and the
  Audit page lists it with the reason; try to deduct more than the balance and confirm it stops at 0;
  reset a cooldown and confirm `/daily` works and the streak continues; run `/pay` or `/daily` while
  editing the same account from the panel a few times and confirm the Renown activity list on the
  profile always shows a balance-after that matches the next row.
```

- [ ] **Step 2: Look at the real pages and drive the real flows**

Use the technique from P-A3 (a throwaway server script and a CDP driver, both outside the repo; headless Edge with `--remote-debugging-port`; screenshots; clean up by literal paths, never `rm -rf $(...)`; kill only `python.exe`/`msedge.exe` by name). Drive: open People, search a name, open a profile, preview a clamped deduction (the explanation must be visible), confirm, then preview and let the balance change underneath (edit it with a raw sqlite write between preview and confirm) and confirm to see the stale message; reset a daily cooldown; force one request to fail (a route that returns 500) and confirm the request-failed notice appears; load the Audit page and check the new labels. Screenshot dark 1280, light 1280 and dark 390. Fix any problem in a separate `fix(admin):` commit, with a regression test where a test can see it.

- [ ] **Step 3: Run the full verification**

```powershell
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
```

Expected: all green. Report any failure by name; do not mark P-A4 done on a red run.

- [ ] **Step 4: Commit and stop for owner review**

```powershell
git add README.md TASKS.md docs/superpowers/specs/2026-10-05-admin-panel-design.md
git commit -m "docs(admin): record admin panel phase P-A4" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

Summarize what changed and list what is unverified (the manual real-bot checks above), remind the owner that the bot must be restarted to pick up the wallet-lock change, and wait for review. Do not start P-A5.

---

## Self-review

**Spec coverage (§7 Users, the cross-process write rule, §10, §11 P-A4):** profile with wallet, streak, last daily, recent transactions, gambling per game, top tracks, command counts: Task 3 and 5. Balance adjustment (delta, required reason, floored at 0, `renown_transactions` row with reason `admin`, audit row, one `BEGIN IMMEDIATE` transaction, relative update, re-read inside): Task 4. Daily reset audited: Task 4. "The P-A4 plan must also review whether the bot's read-then-write paths need the same treatment": Task 1 plus the audit table in this plan. Search by ID, extended to list and name search (owner decision). Names via `user_names`: Task 3.

**Placeholders:** none.

**Type consistency:** `parse_user_id`, `clean_reason`, `InputRefused` (core/inputs); `AccessRefused is InputRefused`; `Person`, `Profile`, `WalletView`, `Txn`, `GameStat`, `TrackStat`, `CommandStat`, `list_people`, `load_profile`, `reason_label`, `DAILY_COOLDOWN_S`, `PAGE_SIZE` (service); `parse_amount`, `ADJUST_CAP`, `AdjustPreview`, `AdjustResult`, `DailyResetResult`, `preview_adjustment`, `adjust_balance`, `reset_daily` (actions); routes and template keys (`profile`, `problem`, `flash`, `user_id`, `preview`, `reason`, `form`) are spelled the same in router and templates.

**Review Focus coverage:** 1 to Tasks 1 and 4; 2 to Tasks 1 and 4; 3 to Task 4; 4 to Task 4 and 5; 5 to Tasks 3 and 5; 6 to Tasks 2 and 5.

**Known risks for the implementer:** (a) Task 1 changes money code: the whole suite must stay green and the mutation check is mandatory; if any bot path fails with "cannot start a transaction within a transaction", a code path left a transaction open and that is the bug to fix, not the helper. (b) The race tests block the event loop for about 0.3 s each while the injected panel edit waits; that is intended. (c) Templates return fragments whose root element carries the swap target's id (`adjust`, `daily`, `person`); keep that, or `hx-swap="outerHTML"` will drop the id and the next swap will miss. (d) `htmx` does not swap non-2xx responses, so no route in this module returns a 4xx for a user-visible refusal. (e) The bot must be restarted to pick up Task 1.
