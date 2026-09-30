# Economy — Core Wallet Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a server-only "Renown" currency to Yokai: a wallet per user, `/balance`, `/daily`, `/pay`, `/leaderboard`, an owner-only `/economy-admin`, and passive earning from voice presence and music-command usage.

**Architecture:** A new `yokai/economy/` package holds pure logic (daily-claim math, a rate limiter, a voice-tick background task); all persistence lives as new methods on the existing `Database` class in `storage/db.py` (two new tables: `wallets`, `renown_transactions`), following the codebase's existing "one Database class, no repository layer" convention. A new `cogs/economy.py` adds the five slash commands, all through the existing `ui/embeds.py` factory and `ui/views.py` view base classes. `bot.py` wires up the background voice tracker and extends the existing `on_app_command_completion` hook to credit music-command usage — no changes to individual music command handlers.

**Tech Stack:** Python 3.12+, discord.py (already a dependency), aiosqlite (already a dependency). No new runtime dependency.

**Spec:** `docs/superpowers/specs/2026-09-29-economy-core-design.md`

## Global Constraints

- Slash commands only (`app_commands`), guild-scoped, no privileged Discord intents (AGENTS.md).
- Defer within 3s for anything that touches the network; SQLite reads are local and fast enough to run *before* a defer when doing so keeps an error ephemeral (AGENTS.md's defer-visibility rule; mirrors the existing `/play` classify-before-defer pattern in `cogs/music.py`).
- Every user-visible message goes through `ui/embeds.py` / `EmbedFactory`; no raw `send_message("text")` or `channel.send("text")` in feature code (enforced by `tests/test_code_standards.py`).
- `allowed_mentions=none` bot-wide; mention users only inside embeds via `<@id>` (AGENTS.md).
- Views must time out, disable their items, and restrict interaction to allowed users — except where the spec explicitly makes a view open to everyone (the leaderboard).
- At most one emoji per embed, at most one short quip, quip kept separate from the plain explanation (AGENTS.md persona rules; enforced by `tests/test_one_emoji_rule.py` and `tests/test_theme.py`).
- SQLite via `aiosqlite`, WAL mode, migrations via `PRAGMA user_version` (SPEC.md §10) — the new migration must not disturb existing `command_log`/`play_events`/`spotify_match_cache` data on an owner's real, already-migrated-to-v1 database.
- No new dependency without the owner's approval (AGENTS.md) — this plan needs none.
- Never block the event loop; nothing in this plan does synchronous I/O off the event loop (SQLite access is already async via `aiosqlite`).
- Commit per task with a clear message; run `pytest` and `ruff` before calling any task done (AGENTS.md).

## Review Focus

- **A real owner database already at schema v1 must upgrade to v2 cleanly**, keeping every existing `command_log`/`play_events`/`spotify_match_cache` row intact — a bot restart against last week's `data/yokai.db` is the most common real-world path and the spec doesn't call it out explicitly. Test added in Task 5.
- **`/pay` must refuse self-payment, bot targets, and non-positive amounts before touching any balance** — an unchecked self-pay or bot-pay would silently create or destroy Renown. Test added in Task 10.
- **`/economy-admin` deducting more than a user's balance must floor at 0, never go negative or raise** — the spec says "floored at 0" but a naive `balance + delta` would go negative. Test added in Task 5.
- **The command-reward rate limiter must allow exactly the configured limit, reject the next call in-window, and resume once the window rolls over** — an off-by-one here either blocks legitimate use or defeats the anti-farming guard entirely. Test added in Task 4.
- **Voice-tick earning must never credit bots and must require at least 2 non-bot humans** — a channel with one human and the bot itself (2 members, 1 human) must NOT pay out; this is the whole point of the anti-AFK-farming rule and is easy to get backwards. Test added in Task 6.

---

### Task 1: Economy configuration

**Files:**
- Modify: `src/yokai/config.py`
- Modify: `.env.example`
- Modify: `tests/test_config.py`

**Interfaces:**
- Produces: `Config.economy_daily_base: int`, `Config.economy_welcome_bonus: int`, `Config.economy_voice_interval_seconds: int`, `Config.economy_voice_reward: int`, `Config.economy_command_reward: int`, `Config.economy_command_rate_limit: int` — every later task that needs an economy default reads these off `bot.config`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_config.py`:

```python
def test_economy_defaults_and_overrides() -> None:
    base = {"DISCORD_TOKEN": "t.o.k", "GUILD_ID": "1", "OWNER_ID": "2"}
    with patch.dict(os.environ, base, clear=True):
        cfg = load_config(env_path=Path("non_existent_env"))
        assert cfg.economy_daily_base == 100
        assert cfg.economy_welcome_bonus == 200
        assert cfg.economy_voice_interval_seconds == 300
        assert cfg.economy_voice_reward == 5
        assert cfg.economy_command_reward == 5
        assert cfg.economy_command_rate_limit == 5

    custom = {
        **base,
        "ECONOMY_DAILY_BASE": "50",
        "ECONOMY_WELCOME_BONUS": "10",
        "ECONOMY_VOICE_INTERVAL_SECONDS": "60",
        "ECONOMY_VOICE_REWARD": "1",
        "ECONOMY_COMMAND_REWARD": "2",
        "ECONOMY_COMMAND_RATE_LIMIT": "3",
    }
    with patch.dict(os.environ, custom, clear=True):
        cfg = load_config(env_path=Path("non_existent_env"))
        assert cfg.economy_daily_base == 50
        assert cfg.economy_welcome_bonus == 10
        assert cfg.economy_voice_interval_seconds == 60
        assert cfg.economy_voice_reward == 1
        assert cfg.economy_command_reward == 2
        assert cfg.economy_command_rate_limit == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config.py::test_economy_defaults_and_overrides -v`
Expected: FAIL with `AttributeError: 'Config' object has no attribute 'economy_daily_base'`

- [ ] **Step 3: Implement the config fields and parsing**

In `src/yokai/config.py`, add to the `Config` dataclass (after `audio_buffer_dir: Path = Path("data/cache")`):

```python
    # Economy: Renown wallet, earning, and rate limits
    economy_daily_base: int = 100
    economy_welcome_bonus: int = 200
    economy_voice_interval_seconds: int = 300
    economy_voice_reward: int = 5
    economy_command_reward: int = 5
    economy_command_rate_limit: int = 5
```

In `load_config`, after the `audio_buffer_dir` line, add:

```python
    economy_daily_base = _parse_int("ECONOMY_DAILY_BASE", os.getenv("ECONOMY_DAILY_BASE"), 100)
    economy_welcome_bonus = _parse_int(
        "ECONOMY_WELCOME_BONUS", os.getenv("ECONOMY_WELCOME_BONUS"), 200
    )
    economy_voice_interval_seconds = _parse_int(
        "ECONOMY_VOICE_INTERVAL_SECONDS", os.getenv("ECONOMY_VOICE_INTERVAL_SECONDS"), 300
    )
    economy_voice_reward = _parse_int(
        "ECONOMY_VOICE_REWARD", os.getenv("ECONOMY_VOICE_REWARD"), 5
    )
    economy_command_reward = _parse_int(
        "ECONOMY_COMMAND_REWARD", os.getenv("ECONOMY_COMMAND_REWARD"), 5
    )
    economy_command_rate_limit = _parse_int(
        "ECONOMY_COMMAND_RATE_LIMIT", os.getenv("ECONOMY_COMMAND_RATE_LIMIT"), 5
    )
```

And add the six matching keyword arguments to the final `return Config(...)` call:

```python
        economy_daily_base=economy_daily_base,
        economy_welcome_bonus=economy_welcome_bonus,
        economy_voice_interval_seconds=economy_voice_interval_seconds,
        economy_voice_reward=economy_voice_reward,
        economy_command_reward=economy_command_reward,
        economy_command_rate_limit=economy_command_rate_limit,
```

In `.env.example`, append:

```
# Economy: Renown wallet, earning, and rate limits
ECONOMY_DAILY_BASE=100
ECONOMY_WELCOME_BONUS=200
ECONOMY_VOICE_INTERVAL_SECONDS=300
ECONOMY_VOICE_REWARD=5
ECONOMY_COMMAND_REWARD=5
ECONOMY_COMMAND_RATE_LIMIT=5
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS (all tests in the file, including the new one)

- [ ] **Step 5: Commit**

```bash
git add src/yokai/config.py .env.example tests/test_config.py
git commit -m "feat(economy): add Renown configuration variables"
```

---

### Task 2: Economy typed errors

**Files:**
- Modify: `src/yokai/errors.py`
- Modify: `tests/test_errors.py`
- Modify: `tests/test_title_case.py`

**Interfaces:**
- Produces: `DailyOnCooldownError(retry_after_s: int)`, `InsufficientFundsError(balance: int, amount: int)`, `InvalidPaymentError(message: str)` — all subclasses of `YokaiError`, consumed by `storage/db.py` (Task 5) and `cogs/economy.py` (Task 10).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_errors.py`:

```python
def test_daily_on_cooldown_error_reports_wait_time() -> None:
    err = err_mod.DailyOnCooldownError(retry_after_s=5400)
    assert err.title == "Already claimed"
    assert "1h 30m" in err.message
    assert err.retry_after_s == 5400


def test_daily_on_cooldown_error_under_an_hour() -> None:
    err = err_mod.DailyOnCooldownError(retry_after_s=120)
    assert "2m" in err.message
    assert "h" not in err.message.split("Try again in ")[1]


def test_insufficient_funds_error_reports_amounts() -> None:
    err = err_mod.InsufficientFundsError(balance=40, amount=100)
    assert err.title == "Not enough Renown"
    assert "40" in err.message
    assert "100" in err.message
    assert err.balance == 40
    assert err.amount == 100


def test_invalid_payment_error_carries_the_message() -> None:
    err = err_mod.InvalidPaymentError("You can only pay another human member of the server.")
    assert err.title == "Invalid payment"
    assert "human member" in err.message
```

Add `"Renown"` to `PROPER_NOUNS` in `tests/test_title_case.py`:

```python
PROPER_NOUNS = {"YouTube", "Spotify", "Yokai", "DAVE", "Renown"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_errors.py tests/test_title_case.py -v`
Expected: FAIL with `AttributeError: module 'yokai.errors' has no attribute 'DailyOnCooldownError'`

- [ ] **Step 3: Implement the errors**

Append to `src/yokai/errors.py`:

```python
class DailyOnCooldownError(YokaiError):
    """Raised when /daily is claimed again before 24h have passed since the last claim."""

    title = "Already claimed"

    def __init__(self, retry_after_s: int) -> None:
        remaining = max(retry_after_s, 0)
        hours, rem = divmod(remaining, 3600)
        minutes = rem // 60
        wait_str = f"{hours}h {minutes}m" if hours else f"{minutes}m"
        super().__init__(
            f"You already claimed your daily reward. Try again in {wait_str}.",
            user_hint="Daily rewards reset 24 hours after your last claim.",
        )
        self.retry_after_s = retry_after_s


class InsufficientFundsError(YokaiError):
    """Raised when a payment or debit would exceed the payer's balance."""

    title = "Not enough Renown"

    def __init__(self, balance: int, amount: int) -> None:
        super().__init__(
            f"You have **{balance:,}** Renown, which isn't enough to send **{amount:,}**.",
            user_hint="Earn more with /daily or by using Yokai in voice.",
        )
        self.balance = balance
        self.amount = amount


class InvalidPaymentError(YokaiError):
    """Raised when a /pay request targets an invalid recipient or amount."""

    title = "Invalid payment"

    def __init__(self, message: str) -> None:
        super().__init__(message, user_hint="Pick a positive whole number and a real member.")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_errors.py tests/test_title_case.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/yokai/errors.py tests/test_errors.py tests/test_title_case.py
git commit -m "feat(economy): add daily-cooldown, insufficient-funds, and invalid-payment errors"
```

---

### Task 3: Pure daily-claim math

**Files:**
- Create: `src/yokai/economy/__init__.py`
- Create: `src/yokai/economy/models.py`
- Create: `src/yokai/economy/daily.py`
- Test: `tests/test_economy_daily.py`

**Interfaces:**
- Produces: `Wallet(user_id, balance, last_daily_ts, daily_streak, created_ts)` dataclass; `DailyClaimResult(payout, streak_day, new_balance)` dataclass; `cooldown_remaining_s(last_daily_ts: Optional[int], now: int) -> Optional[int]`; `next_streak(previous_streak: int, last_daily_ts: Optional[int], now: int) -> int`; `streak_multiplier(streak_day: int) -> float`; `roll_daily_payout(base: int, streak_day: int, rng: random.Random) -> int`. Consumed by `storage/db.py` (Task 5) and `cogs/economy.py` (Task 10).

- [ ] **Step 1: Write the failing test**

Create `tests/test_economy_daily.py`:

```python
"""Tests for pure /daily claim math: cooldown, streak progression, and payout rolls."""

from __future__ import annotations

from unittest.mock import MagicMock

from yokai.economy.daily import (
    cooldown_remaining_s,
    next_streak,
    roll_daily_payout,
    streak_multiplier,
)


def test_cooldown_remaining_is_none_for_a_first_claim() -> None:
    assert cooldown_remaining_s(None, now=1000) is None


def test_cooldown_remaining_blocks_within_24_hours() -> None:
    now = 100_000
    assert cooldown_remaining_s(now - 3600, now) == 86400 - 3600


def test_cooldown_remaining_clears_after_24_hours() -> None:
    now = 100_000
    assert cooldown_remaining_s(now - 86400, now) is None
    assert cooldown_remaining_s(now - 90000, now) is None


def test_next_streak_starts_at_one_for_a_first_claim() -> None:
    assert next_streak(previous_streak=0, last_daily_ts=None, now=100_000) == 1


def test_next_streak_continues_between_24_and_48_hours() -> None:
    now = 200_000
    assert next_streak(previous_streak=3, last_daily_ts=now - 90000, now=now) == 4


def test_next_streak_resets_after_48_hours() -> None:
    now = 300_000
    assert next_streak(previous_streak=6, last_daily_ts=now - 172800, now=now) == 1
    assert next_streak(previous_streak=6, last_daily_ts=now - 200000, now=now) == 1


def test_streak_multiplier_grows_linearly_and_caps_at_day_seven() -> None:
    assert streak_multiplier(1) == 1.0
    assert streak_multiplier(4) == 2.0
    assert streak_multiplier(7) == 3.0
    assert streak_multiplier(8) == 3.0
    assert streak_multiplier(100) == 3.0


def test_roll_daily_payout_applies_random_and_streak_multipliers() -> None:
    rng = MagicMock()
    rng.uniform.return_value = 1.2
    assert roll_daily_payout(base=100, streak_day=1, rng=rng) == round(100 * 1.2 * 1.0)
    assert roll_daily_payout(base=100, streak_day=7, rng=rng) == round(100 * 1.2 * 3.0)
    rng.uniform.assert_called_with(1.0, 1.5)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_economy_daily.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'yokai.economy'`

- [ ] **Step 3: Implement the package**

Create `src/yokai/economy/__init__.py`:

```python
"""The Renown economy: wallets, daily claims, passive earning, and payments."""
```

Create `src/yokai/economy/models.py`:

```python
"""Data models for the Renown economy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(slots=True)
class Wallet:
    """A user's Renown wallet."""

    user_id: int
    balance: int
    last_daily_ts: Optional[int]
    daily_streak: int
    created_ts: int


@dataclass(slots=True)
class DailyClaimResult:
    """The outcome of a successful /daily claim."""

    payout: int
    streak_day: int
    new_balance: int
```

Create `src/yokai/economy/daily.py`:

```python
"""Pure math for the /daily claim: cooldown, streak progression, and payout rolls.

Kept dependency-free and side-effect-free so the numbers can be tested without a database,
mirroring how `music/resolvers/matcher.py` isolates scoring from I/O.
"""

from __future__ import annotations

import random
from typing import Optional

DAILY_COOLDOWN_S = 86400
STREAK_RESET_THRESHOLD_S = 172800  # 48 hours
STREAK_CAP_DAYS = 7
MAX_STREAK_MULTIPLIER = 3.0


def cooldown_remaining_s(last_daily_ts: Optional[int], now: int) -> Optional[int]:
    """Seconds until the next claim is allowed, or None if a claim is allowed right now."""
    if last_daily_ts is None:
        return None
    elapsed = now - last_daily_ts
    if elapsed >= DAILY_COOLDOWN_S:
        return None
    return DAILY_COOLDOWN_S - elapsed


def next_streak(previous_streak: int, last_daily_ts: Optional[int], now: int) -> int:
    """Day count for this claim.

    Continues (+1) if the previous claim was 24-48h ago; resets to day 1 on a first claim or
    if 48h or more have passed. Callers only reach this after `cooldown_remaining_s` has
    already cleared the <24h case.
    """
    if last_daily_ts is None:
        return 1
    elapsed = now - last_daily_ts
    if elapsed >= STREAK_RESET_THRESHOLD_S:
        return 1
    return previous_streak + 1


def streak_multiplier(streak_day: int) -> float:
    """1.0x on day 1, growing linearly to 3.0x at day 7, held at 3.0x from day 7 onward."""
    capped_day = min(max(streak_day, 1), STREAK_CAP_DAYS)
    return 1.0 + (capped_day - 1) * (MAX_STREAK_MULTIPLIER - 1.0) / (STREAK_CAP_DAYS - 1)


def roll_daily_payout(base: int, streak_day: int, rng: random.Random) -> int:
    """Base * a random 1.0-1.5x roll * the streak multiplier, rounded to a whole number."""
    random_mult = rng.uniform(1.0, 1.5)
    return round(base * random_mult * streak_multiplier(streak_day))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_economy_daily.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/yokai/economy/__init__.py src/yokai/economy/models.py src/yokai/economy/daily.py tests/test_economy_daily.py
git commit -m "feat(economy): add wallet models and pure daily-claim math"
```

---

### Task 4: Command-reward rate limiter

**Files:**
- Create: `src/yokai/economy/ratelimit.py`
- Test: `tests/test_economy_ratelimit.py`

**Interfaces:**
- Produces: `CommandRewardLimiter(limit: int, window_s: float)` with `try_consume(self, user_id: int, now: Optional[float] = None) -> bool`. Consumed by `bot.py` (Task 11).

- [ ] **Step 1: Write the failing test**

Create `tests/test_economy_ratelimit.py`:

```python
"""Tests for the music-command Renown reward rate limiter (Review Focus: boundary behavior)."""

from __future__ import annotations

from yokai.economy.ratelimit import CommandRewardLimiter


def test_allows_exactly_the_limit_then_blocks_the_next_call() -> None:
    limiter = CommandRewardLimiter(limit=5, window_s=60.0)
    results = [limiter.try_consume(user_id=1, now=100.0) for _ in range(6)]
    assert results == [True, True, True, True, True, False]


def test_tracks_users_independently() -> None:
    limiter = CommandRewardLimiter(limit=1, window_s=60.0)
    assert limiter.try_consume(user_id=1, now=0.0) is True
    assert limiter.try_consume(user_id=2, now=0.0) is True
    assert limiter.try_consume(user_id=1, now=0.0) is False


def test_window_expiry_allows_new_calls() -> None:
    limiter = CommandRewardLimiter(limit=2, window_s=60.0)
    assert limiter.try_consume(user_id=1, now=0.0) is True
    assert limiter.try_consume(user_id=1, now=1.0) is True
    assert limiter.try_consume(user_id=1, now=2.0) is False
    assert limiter.try_consume(user_id=1, now=61.0) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_economy_ratelimit.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'yokai.economy.ratelimit'`

- [ ] **Step 3: Implement the limiter**

Create `src/yokai/economy/ratelimit.py`:

```python
"""In-memory rate limiter for music-command Renown rewards.

Per-process and not persisted: a bot restart simply clears every window. That is an accepted
trade-off for a single-process bot with about 10 trusted users (see the design spec, Risks).
"""

from __future__ import annotations

import time
from collections import deque
from typing import Optional


class CommandRewardLimiter:
    """Allows at most `limit` rewarded calls per rolling `window_s` seconds, per user."""

    def __init__(self, limit: int, window_s: float) -> None:
        self.limit = limit
        self.window_s = window_s
        self._hits: dict[int, deque[float]] = {}

    def try_consume(self, user_id: int, now: Optional[float] = None) -> bool:
        """Return True and record a hit if `user_id` is under the limit right now."""
        ts = now if now is not None else time.monotonic()
        hits = self._hits.setdefault(user_id, deque())
        cutoff = ts - self.window_s
        while hits and hits[0] <= cutoff:
            hits.popleft()
        if len(hits) >= self.limit:
            return False
        hits.append(ts)
        return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_economy_ratelimit.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/yokai/economy/ratelimit.py tests/test_economy_ratelimit.py
git commit -m "feat(economy): add per-user rolling-window reward rate limiter"
```

---

### Task 5: Wallet storage (migration, wallets, transactions)

**Files:**
- Modify: `src/yokai/storage/db.py`
- Test: `tests/test_economy_storage.py`

**Interfaces:**
- Consumes: `Wallet`, `DailyClaimResult` (Task 3 `economy/models.py`); `cooldown_remaining_s`, `next_streak`, `roll_daily_payout` (Task 3 `economy/daily.py`); `InsufficientFundsError`, `DailyOnCooldownError` (Task 2 `errors.py`).
- Produces on `Database`: `async get_wallet(user_id: int) -> Optional[Wallet]`; `async ensure_wallet(user_id: int, welcome_bonus: int) -> Wallet`; `async credit_wallet(user_id: int, amount: int, reason: str, welcome_bonus: int, counterparty_id: Optional[int] = None) -> int`; `async pay(sender_id: int, recipient_id: int, amount: int, welcome_bonus: int) -> tuple[int, int]`; `async claim_daily(user_id: int, base: int, welcome_bonus: int, now: int, rng: random.Random) -> DailyClaimResult`; `async admin_adjust_wallet(user_id: int, delta: int, welcome_bonus: int) -> tuple[int, int]`; `async get_leaderboard(limit: int, offset: int) -> tuple[list[Wallet], int]`. Consumed by `economy/voice_tracker.py` (Task 6) and `cogs/economy.py` (Task 10) and `bot.py` (Task 11).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_economy_storage.py`:

```python
"""Tests for wallet storage: migration, balances, daily claims, payments, and the leaderboard."""

from __future__ import annotations

import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from yokai.errors import DailyOnCooldownError, InsufficientFundsError
from yokai.storage.db import CURRENT_SCHEMA_VERSION, MIGRATION_V1, Database

WELCOME = 200


async def _fresh_db(tmp_dir: str) -> Database:
    db = Database(Path(tmp_dir) / "test_yokai.db")
    await db.initialize()
    return db


@pytest.mark.asyncio
async def test_schema_upgrades_to_v2_and_is_now_at_the_current_version() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = await _fresh_db(tmp_dir)
        async with db.conn.execute("PRAGMA user_version;") as cursor:
            row = await cursor.fetchone()
            assert row is not None and row[0] == CURRENT_SCHEMA_VERSION
            assert CURRENT_SCHEMA_VERSION >= 2
        await db.close()


@pytest.mark.asyncio
async def test_an_existing_v1_database_upgrades_without_losing_data() -> None:
    """Review Focus: a real owner's already-migrated-to-v1 database must upgrade cleanly."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "existing_v1.db"

        # Simulate a database already at schema v1, with real rows in it.
        v1_only = Database(db_path)
        v1_only._conn = None
        import aiosqlite

        conn = await aiosqlite.connect(db_path)
        await conn.executescript(MIGRATION_V1)
        await conn.execute("PRAGMA user_version = 1;")
        await conn.execute(
            "INSERT INTO command_log (ts, user_id, command) VALUES (1000, 42, 'play');"
        )
        await conn.commit()
        await conn.close()

        # Now open it through the normal Database, as a restart would.
        db = Database(db_path)
        await db.initialize()

        async with db.conn.execute("PRAGMA user_version;") as cursor:
            row = await cursor.fetchone()
            assert row is not None and row[0] == CURRENT_SCHEMA_VERSION

        async with db.conn.execute("SELECT user_id, command FROM command_log;") as cursor:
            rows = await cursor.fetchall()
            assert rows == [(42, "play")]

        # The new tables exist and are usable.
        wallet = await db.ensure_wallet(1, welcome_bonus=WELCOME)
        assert wallet.balance == WELCOME
        await db.close()


@pytest.mark.asyncio
async def test_ensure_wallet_grants_the_welcome_bonus_exactly_once() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = await _fresh_db(tmp_dir)
        first = await db.ensure_wallet(1, welcome_bonus=WELCOME)
        assert first.balance == WELCOME
        second = await db.ensure_wallet(1, welcome_bonus=WELCOME)
        assert second.balance == WELCOME  # not doubled
        await db.close()


@pytest.mark.asyncio
async def test_get_wallet_never_creates_one() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = await _fresh_db(tmp_dir)
        assert await db.get_wallet(999) is None
        await db.close()


@pytest.mark.asyncio
async def test_credit_wallet_auto_creates_with_welcome_bonus_then_adds() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = await _fresh_db(tmp_dir)
        balance = await db.credit_wallet(1, 50, reason="voice", welcome_bonus=WELCOME)
        assert balance == WELCOME + 50
        await db.close()


@pytest.mark.asyncio
async def test_pay_moves_renown_atomically_and_writes_both_transactions() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = await _fresh_db(tmp_dir)
        await db.ensure_wallet(1, welcome_bonus=100)
        sender_balance, recipient_balance = await db.pay(1, 2, 30, welcome_bonus=100)
        assert sender_balance == 70
        assert recipient_balance == 130  # recipient's own welcome bonus + the payment
        async with db.conn.execute(
            "SELECT reason, delta, counterparty_id FROM renown_transactions ORDER BY id;"
        ) as cursor:
            rows = await cursor.fetchall()
        reasons = {r[0]: (r[1], r[2]) for r in rows}
        assert reasons["pay_sent"] == (-30, 2)
        assert reasons["pay_received"] == (30, 1)
        await db.close()


@pytest.mark.asyncio
async def test_pay_raises_and_changes_nothing_when_sender_is_short() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = await _fresh_db(tmp_dir)
        await db.ensure_wallet(1, welcome_bonus=10)
        with pytest.raises(InsufficientFundsError):
            await db.pay(1, 2, 999, welcome_bonus=10)
        assert await db.get_wallet(2) is None  # recipient wallet was never created
        await db.close()


@pytest.mark.asyncio
async def test_claim_daily_pays_out_and_records_streak() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = await _fresh_db(tmp_dir)
        rng = MagicMock()
        rng.uniform.return_value = 1.0
        now = int(time.time())
        result = await db.claim_daily(1, base=100, welcome_bonus=0, now=now, rng=rng)
        assert result.payout == 100
        assert result.streak_day == 1
        assert result.new_balance == 100
        await db.close()


@pytest.mark.asyncio
async def test_claim_daily_raises_when_already_claimed_today() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = await _fresh_db(tmp_dir)
        rng = MagicMock()
        rng.uniform.return_value = 1.0
        now = int(time.time())
        await db.claim_daily(1, base=100, welcome_bonus=0, now=now, rng=rng)
        with pytest.raises(DailyOnCooldownError):
            await db.claim_daily(1, base=100, welcome_bonus=0, now=now + 10, rng=rng)
        wallet = await db.get_wallet(1)
        assert wallet is not None and wallet.balance == 100  # second claim did not pay out
        await db.close()


@pytest.mark.asyncio
async def test_admin_adjust_floors_a_large_deduction_at_zero() -> None:
    """Review Focus: an admin deduction larger than the balance must floor at 0, not go negative."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = await _fresh_db(tmp_dir)
        await db.ensure_wallet(1, welcome_bonus=50)
        applied, new_balance = await db.admin_adjust_wallet(1, delta=-9999, welcome_bonus=50)
        assert new_balance == 0
        assert applied == -50  # only the actual amount removed, not the requested -9999
        await db.close()


@pytest.mark.asyncio
async def test_admin_adjust_can_grant_above_zero() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = await _fresh_db(tmp_dir)
        applied, new_balance = await db.admin_adjust_wallet(1, delta=500, welcome_bonus=0)
        assert (applied, new_balance) == (500, 500)
        await db.close()


@pytest.mark.asyncio
async def test_leaderboard_orders_by_balance_descending_with_a_total_count() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = await _fresh_db(tmp_dir)
        await db.credit_wallet(1, 10, reason="voice", welcome_bonus=0)
        await db.credit_wallet(2, 30, reason="voice", welcome_bonus=0)
        await db.credit_wallet(3, 20, reason="voice", welcome_bonus=0)
        wallets, total = await db.get_leaderboard(limit=2, offset=0)
        assert total == 3
        assert [w.user_id for w in wallets] == [2, 3]
        page2, total2 = await db.get_leaderboard(limit=2, offset=2)
        assert total2 == 3
        assert [w.user_id for w in page2] == [1]
        await db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_economy_storage.py -v`
Expected: FAIL with `ImportError: cannot import name 'MIGRATION_V1'` or `AttributeError: 'Database' object has no attribute 'ensure_wallet'`

- [ ] **Step 3: Implement the storage layer**

In `src/yokai/storage/db.py`, change the imports and version constant at the top:

```python
"""SQLite persistence layer using aiosqlite, WAL mode, and PRAGMA user_version migrations."""

from __future__ import annotations

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
```

After `MIGRATION_V1` (its closing `"""`), add:

```python
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
```

In `_migrate`, after the existing `if version < 1:` block, add:

```python
        if version < 2:
            logger.info("Applying SQLite migration v2...")
            await self.conn.executescript(MIGRATION_V2)
            await self.conn.execute("PRAGMA user_version = 2;")
            await self.conn.commit()
            logger.info("SQLite schema migrated to version 2.")
```

Append the wallet and transaction methods at the end of the `Database` class (after `set_spotify_match`):

```python
    async def _ensure_wallet(self, user_id: int, welcome_bonus: int, now: int) -> None:
        """Create a wallet with the welcome bonus if `user_id` has none yet (idempotent)."""
        cursor = await self.conn.execute(
            "INSERT OR IGNORE INTO wallets (user_id, balance, daily_streak, created_ts) "
            "VALUES (?, ?, 0, ?);",
            (user_id, welcome_bonus, now),
        )
        if cursor.rowcount and cursor.rowcount > 0 and welcome_bonus:
            await self._insert_transaction(user_id, welcome_bonus, "welcome", None, welcome_bonus, now)

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
            "(ts, user_id, delta, reason, counterparty_id, balance_after) VALUES (?, ?, ?, ?, ?, ?);",
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
        return Wallet(user_id=row[0], balance=row[1], last_daily_ts=row[2], daily_streak=row[3], created_ts=row[4])

    async def ensure_wallet(self, user_id: int, welcome_bonus: int) -> Wallet:
        """Get-or-create: creates the wallet with the welcome bonus on first touch."""
        now = int(time.time())
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
        await self._ensure_wallet(user_id, welcome_bonus, now)
        await self.conn.execute(
            "UPDATE wallets SET balance = balance + ? WHERE user_id = ?;", (amount, user_id)
        )
        wallet = await self.get_wallet(user_id)
        assert wallet is not None
        await self._insert_transaction(user_id, amount, reason, counterparty_id, wallet.balance, now)
        await self.conn.commit()
        return wallet.balance

    async def pay(
        self, sender_id: int, recipient_id: int, amount: int, welcome_bonus: int
    ) -> tuple[int, int]:
        """Atomically move `amount` from sender to recipient. Raises InsufficientFundsError."""
        if amount <= 0:
            raise ValueError("pay amount must be positive")
        now = int(time.time())
        await self._ensure_wallet(sender_id, welcome_bonus, now)
        sender = await self.get_wallet(sender_id)
        assert sender is not None
        if sender.balance < amount:
            await self.conn.rollback()
            raise InsufficientFundsError(balance=sender.balance, amount=amount)

        await self._ensure_wallet(recipient_id, welcome_bonus, now)
        await self.conn.execute(
            "UPDATE wallets SET balance = balance - ? WHERE user_id = ?;", (amount, sender_id)
        )
        await self.conn.execute(
            "UPDATE wallets SET balance = balance + ? WHERE user_id = ?;", (amount, recipient_id)
        )
        sender_balance = sender.balance - amount
        recipient = await self.get_wallet(recipient_id)
        assert recipient is not None
        await self._insert_transaction(sender_id, -amount, "pay_sent", recipient_id, sender_balance, now)
        await self._insert_transaction(recipient_id, amount, "pay_received", sender_id, recipient.balance, now)
        await self.conn.commit()
        return sender_balance, recipient.balance

    async def claim_daily(
        self, user_id: int, base: int, welcome_bonus: int, now: int, rng: random.Random
    ) -> DailyClaimResult:
        """Roll and apply a /daily payout, or raise DailyOnCooldownError."""
        await self._ensure_wallet(user_id, welcome_bonus, now)
        wallet = await self.get_wallet(user_id)
        assert wallet is not None
        remaining = cooldown_remaining_s(wallet.last_daily_ts, now)
        if remaining is not None:
            await self.conn.rollback()
            raise DailyOnCooldownError(remaining)

        streak_day = next_streak(wallet.daily_streak, wallet.last_daily_ts, now)
        payout = roll_daily_payout(base, streak_day, rng)
        await self.conn.execute(
            "UPDATE wallets SET balance = balance + ?, last_daily_ts = ?, daily_streak = ? "
            "WHERE user_id = ?;",
            (payout, now, streak_day, user_id),
        )
        new_balance = wallet.balance + payout
        await self._insert_transaction(user_id, payout, "daily", None, new_balance, now)
        await self.conn.commit()
        return DailyClaimResult(payout=payout, streak_day=streak_day, new_balance=new_balance)

    async def admin_adjust_wallet(
        self, user_id: int, delta: int, welcome_bonus: int
    ) -> tuple[int, int]:
        """Apply `delta` (positive or negative), floored at 0. Returns (applied_delta, new_balance)."""
        now = int(time.time())
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
            Wallet(user_id=r[0], balance=r[1], last_daily_ts=r[2], daily_streak=r[3], created_ts=r[4])
            for r in rows
        ]
        return wallets, total
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_economy_storage.py tests/test_storage.py -v`
Expected: PASS (both files — confirms the existing tables/tests still work alongside the new ones)

- [ ] **Step 5: Commit**

```bash
git add src/yokai/storage/db.py tests/test_economy_storage.py
git commit -m "feat(economy): add wallet storage, migration v2, and atomic pay/daily/admin operations"
```

---

### Task 6: Voice-presence earning

**Files:**
- Create: `src/yokai/economy/voice_tracker.py`
- Test: `tests/test_economy_voice_tracker.py`

**Interfaces:**
- Consumes: `Database.credit_wallet` (Task 5).
- Produces: `VoiceEarnTracker(bot, interval_s: int, reward: int)` with `start() -> None`, `async stop() -> None`, `async tick() -> None`. Consumed by `bot.py` (Task 11).

- [ ] **Step 1: Write the failing test**

Create `tests/test_economy_voice_tracker.py`:

```python
"""Tests for the voice-presence Renown tick (Review Focus: 2+ human requirement, bots excluded)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from yokai.economy.voice_tracker import VoiceEarnTracker


def _member(user_id: int, is_bot: bool = False) -> MagicMock:
    member = MagicMock()
    member.id = user_id
    member.bot = is_bot
    return member


def _channel(members: list[MagicMock]) -> MagicMock:
    channel = MagicMock()
    channel.members = members
    return channel


def _bot_with_guild(channels: list[MagicMock]) -> MagicMock:
    bot = MagicMock()
    bot.config.guild_id = 1
    guild = MagicMock()
    guild.voice_channels = channels
    bot.get_guild.return_value = guild
    bot.db.credit_wallet = AsyncMock()
    return bot


@pytest.mark.asyncio
async def test_tick_credits_every_human_in_a_channel_with_two_or_more_humans() -> None:
    channel = _channel([_member(1), _member(2), _member(99, is_bot=True)])
    bot = _bot_with_guild([channel])
    tracker = VoiceEarnTracker(bot, interval_s=300, reward=5)

    await tracker.tick()

    credited_ids = {call.args[0] for call in bot.db.credit_wallet.await_args_list}
    assert credited_ids == {1, 2}
    for call in bot.db.credit_wallet.await_args_list:
        assert call.args[1] == 5
        assert call.kwargs["reason"] == "voice"


@pytest.mark.asyncio
async def test_tick_does_not_credit_a_channel_with_only_one_human() -> None:
    """Review Focus: one human plus the bot itself (2 members, 1 human) must not pay out."""
    channel = _channel([_member(1), _member(99, is_bot=True)])
    bot = _bot_with_guild([channel])
    tracker = VoiceEarnTracker(bot, interval_s=300, reward=5)

    await tracker.tick()

    bot.db.credit_wallet.assert_not_awaited()


@pytest.mark.asyncio
async def test_tick_does_not_credit_an_empty_channel() -> None:
    bot = _bot_with_guild([_channel([])])
    tracker = VoiceEarnTracker(bot, interval_s=300, reward=5)
    await tracker.tick()
    bot.db.credit_wallet.assert_not_awaited()


@pytest.mark.asyncio
async def test_tick_does_nothing_when_the_guild_is_not_found() -> None:
    bot = MagicMock()
    bot.config.guild_id = 1
    bot.get_guild.return_value = None
    bot.db.credit_wallet = AsyncMock()
    tracker = VoiceEarnTracker(bot, interval_s=300, reward=5)
    await tracker.tick()
    bot.db.credit_wallet.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_and_stop_manage_the_background_task() -> None:
    bot = _bot_with_guild([])
    tracker = VoiceEarnTracker(bot, interval_s=300, reward=5)
    tracker.start()
    assert tracker._task is not None
    await tracker.stop()
    assert tracker._task is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_economy_voice_tracker.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'yokai.economy.voice_tracker'`

- [ ] **Step 3: Implement the tracker**

Create `src/yokai/economy/voice_tracker.py`:

```python
"""Background loop crediting Renown to users active in voice with another human present."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from yokai.bot import YokaiBot

logger = logging.getLogger(__name__)


class VoiceEarnTracker:
    """Ticks every `interval_s`, crediting `reward` Renown to each human in a 2+-human channel."""

    def __init__(self, bot: YokaiBot, interval_s: int, reward: int) -> None:
        self.bot = bot
        self.interval_s = interval_s
        self.reward = reward
        self._task: Optional[asyncio.Task[None]] = None

    def start(self) -> None:
        """Start the background tick loop, if not already running."""
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """Cancel the background tick loop and wait for it to finish."""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self.interval_s)
            try:
                await self.tick()
            except Exception as exc:
                logger.error("Voice-earn tick failed: %s", exc)

    async def tick(self) -> None:
        """Credit every non-bot human in a voice channel that has 2 or more non-bot humans."""
        guild = self.bot.get_guild(self.bot.config.guild_id)
        if guild is None:
            return
        for channel in guild.voice_channels:
            humans = [m for m in channel.members if not m.bot]
            if len(humans) < 2:
                continue
            for member in humans:
                await self.bot.db.credit_wallet(
                    member.id,
                    self.reward,
                    reason="voice",
                    welcome_bonus=self.bot.config.economy_welcome_bonus,
                )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_economy_voice_tracker.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/yokai/economy/voice_tracker.py tests/test_economy_voice_tracker.py
git commit -m "feat(economy): add background voice-presence Renown tick"
```

---

### Task 7: Theme additions (emoji, quips)

**Files:**
- Modify: `src/yokai/theme.py`
- Modify: `tests/test_theme.py`

**Interfaces:**
- Produces: `Emoji.COIN`, `QUIP_VARIANTS["daily_claim"]`, `QUIP_VARIANTS["pay_sent"]`. Consumed by `ui/embeds.py` (Task 8).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_theme.py`'s `required_keys` list inside `test_quip_variants_minimum_count`:

```python
    required_keys = [
        "play_start",
        "queued",
        "skip",
        "search_wait",
        "recs_intro",
        "recs_queued",
        "empty_queue",
        "not_in_vc",
        "error_lead",
        "ping_good",
        "idle_leave",
        "daily_claim",
        "pay_sent",
    ]
```

Also add a standalone assertion test:

```python
def test_coin_emoji_exists_for_economy_embeds() -> None:
    assert Emoji.COIN
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_theme.py -v`
Expected: FAIL with `AssertionError: Missing required quip key: daily_claim` (and `AttributeError` for `Emoji.COIN`)

- [ ] **Step 3: Implement the theme additions**

In `src/yokai/theme.py`, add to the `Emoji` class (after `SKIP`):

```python
    COIN: Final[str] = "🪙"
```

Add to `QUIP_VARIANTS` (after `"idle_leave"`'s entry, before the closing `}`):

```python
    "daily_claim": (
        "Renown deposited.",
        "Collected. See you tomorrow.",
        "Logged and paid out.",
    ),
    "pay_sent": (
        "Transaction clean.",
        "Sent without a trace.",
        "Debt settled, presumably.",
    ),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_theme.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/yokai/theme.py tests/test_theme.py
git commit -m "feat(economy): add Renown coin emoji and daily/pay quip variants"
```

---

### Task 8: Economy embed builders

**Files:**
- Modify: `src/yokai/ui/embeds.py`
- Modify: `tests/test_embeds.py`

**Interfaces:**
- Consumes: `Emoji.COIN`, `quip_bank.get("daily_claim")`, `quip_bank.get("pay_sent")` (Task 7).
- Produces: `EmbedFactory.balance(user_mention, balance, bot_avatar_url=None) -> discord.Embed`; `EmbedFactory.daily_claim(user_mention, payout, streak_day, new_balance, bot_avatar_url=None) -> discord.Embed`; `EmbedFactory.pay_sent(sender_mention, recipient_mention, amount, bot_avatar_url=None) -> discord.Embed`; `EmbedFactory.leaderboard_page(entries: Sequence[tuple[int, int, int]], page, total_pages, bot_avatar_url=None) -> discord.Embed` (entries are `(rank, user_id, balance)`); `EmbedFactory.economy_admin(target_mention, delta_applied, new_balance, bot_avatar_url=None) -> discord.Embed`. Consumed by `cogs/economy.py` (Task 10) and `ui/views.py` (Task 9).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_embeds.py`:

```python
def test_balance_embed_shows_mention_and_amount() -> None:
    embed = EmbedFactory.balance(user_mention="<@42>", balance=1234)
    assert "<@42>" in embed.description
    assert "1,234" in embed.description
    assert emoji_count(embed) <= 1


def test_daily_claim_embed_shows_payout_streak_and_new_balance() -> None:
    embed = EmbedFactory.daily_claim(
        user_mention="<@42>", payout=150, streak_day=4, new_balance=350
    )
    assert "150" in embed.description
    fields = {f.name: f.value for f in embed.fields if f.name != "​"}
    assert fields["Streak"] == "Day `4`"
    assert "350" in fields["New balance"]
    assert emoji_count(embed) <= 1


def test_pay_sent_embed_shows_both_parties_and_amount() -> None:
    embed = EmbedFactory.pay_sent(sender_mention="<@1>", recipient_mention="<@2>", amount=75)
    assert "<@1>" in embed.description and "<@2>" in embed.description
    assert "75" in embed.description
    assert emoji_count(embed) <= 1


def test_leaderboard_page_lists_ranked_entries() -> None:
    embed = EmbedFactory.leaderboard_page(
        entries=[(1, 10, 500), (2, 20, 300)], page=1, total_pages=1
    )
    assert "<@10>" in embed.description and "<@20>" in embed.description
    assert "500" in embed.description
    assert embed.footer.text == "Yokai · Page 1/1"
    assert emoji_count(embed) <= 1


def test_leaderboard_page_empty_state() -> None:
    embed = EmbedFactory.leaderboard_page(entries=[], page=1, total_pages=1)
    assert "No wallets yet." in embed.description


def test_economy_admin_embed_shows_grant_and_deduction() -> None:
    granted = EmbedFactory.economy_admin(target_mention="<@5>", delta_applied=100, new_balance=200)
    assert "Granted" in granted.description and "100" in granted.description
    deducted = EmbedFactory.economy_admin(target_mention="<@5>", delta_applied=-50, new_balance=0)
    assert "Deducted" in deducted.description and "50" in deducted.description
    assert emoji_count(granted) <= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_embeds.py -v`
Expected: FAIL with `AttributeError: type object 'EmbedFactory' has no attribute 'balance'`

- [ ] **Step 3: Implement the builders**

Append to the `EmbedFactory` class in `src/yokai/ui/embeds.py` (after `diag`, before the class ends):

```python
    @classmethod
    def balance(
        cls,
        user_mention: str,
        balance: int,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        return cls._create_base(
            title=f"{Emoji.COIN} Renown balance",
            description=f"{user_mention} has **{balance:,}** Renown.",
            color=COLOR_BRAND,
            context="Economy",
            bot_avatar_url=bot_avatar_url,
        )

    @classmethod
    def daily_claim(
        cls,
        user_mention: str,
        payout: int,
        streak_day: int,
        new_balance: int,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        fields: list[FieldSpec] = [
            ("Streak", f"Day `{streak_day}`", True),
            ("New balance", f"**{new_balance:,}**", True),
        ]
        return cls._create_base(
            title=f"{Emoji.COIN} Daily claimed",
            description=f"{user_mention} claimed **{payout:,}** Renown.",
            color=COLOR_SUCCESS,
            context="Economy",
            bot_avatar_url=bot_avatar_url,
            quip=quip_bank.get("daily_claim"),
            fields=_grid(fields),
        )

    @classmethod
    def pay_sent(
        cls,
        sender_mention: str,
        recipient_mention: str,
        amount: int,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        return cls._create_base(
            title=f"{Emoji.COIN} Renown sent",
            description=f"{sender_mention} sent **{amount:,}** Renown to {recipient_mention}.",
            color=COLOR_SUCCESS,
            context="Economy",
            bot_avatar_url=bot_avatar_url,
            quip=quip_bank.get("pay_sent"),
        )

    @classmethod
    def leaderboard_page(
        cls,
        entries: Sequence[tuple[int, int, int]],  # (rank, user_id, balance)
        page: int,
        total_pages: int,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        lines = [f"`#{rank}` <@{uid}> — **{bal:,}** Renown" for rank, uid, bal in entries]
        body = "\n".join(lines) if lines else "No wallets yet."
        return cls._create_base(
            title=f"{Emoji.COIN} Renown leaderboard",
            description=body,
            color=COLOR_BRAND,
            context=f"Page {page}/{max(total_pages, 1)}",
            bot_avatar_url=bot_avatar_url,
        )

    @classmethod
    def economy_admin(
        cls,
        target_mention: str,
        delta_applied: int,
        new_balance: int,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        verb = "Granted" if delta_applied >= 0 else "Deducted"
        fields: list[FieldSpec] = [("New balance", f"**{new_balance:,}**", True)]
        return cls._create_base(
            title=f"{Emoji.COIN} Balance adjusted",
            description=f"{verb} **{abs(delta_applied):,}** Renown for {target_mention}.",
            color=COLOR_SUCCESS,
            context="Economy admin",
            bot_avatar_url=bot_avatar_url,
            fields=_grid(fields),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_embeds.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/yokai/ui/embeds.py tests/test_embeds.py
git commit -m "feat(economy): add balance, daily-claim, pay, leaderboard, and admin embeds"
```

---

### Task 9: Leaderboard pagination view

**Files:**
- Modify: `src/yokai/ui/views.py`
- Create: `tests/test_economy_views.py`

**Interfaces:**
- Consumes: `EmbedFactory.leaderboard_page` (Task 8); `Database.get_leaderboard` (Task 5).
- Produces: `LeaderboardPaginator(bot, author_id, initial_page=1, page_size=10, bot_avatar_url=None, timeout=180.0)` with `async build_embed() -> discord.Embed`. Consumed by `cogs/economy.py` (Task 10). Unlike `QueuePaginator`, this view passes `allowed_user_ids=None` to `BaseView` so anyone can page through it — the leaderboard is public data everyone can already see.

- [ ] **Step 1: Write the failing test**

Create `tests/test_economy_views.py`:

```python
"""Tests for the leaderboard paginator view."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from yokai.economy.models import Wallet
from yokai.ui.views import LeaderboardPaginator


def _bot_with_pages(pages: dict[int, tuple[list[Wallet], int]]) -> MagicMock:
    bot = MagicMock()

    async def get_leaderboard(limit: int, offset: int) -> tuple[list[Wallet], int]:
        page = offset // limit + 1
        return pages[page]

    bot.db.get_leaderboard = AsyncMock(side_effect=get_leaderboard)
    return bot


def _wallet(user_id: int, balance: int) -> Wallet:
    return Wallet(user_id=user_id, balance=balance, last_daily_ts=None, daily_streak=0, created_ts=0)


@pytest.mark.asyncio
async def test_build_embed_shows_ranked_entries_from_the_first_page() -> None:
    bot = _bot_with_pages({1: ([_wallet(10, 500), _wallet(20, 300)], 2)})
    view = LeaderboardPaginator(bot=bot, author_id=1, page_size=10)
    embed = await view.build_embed()
    assert "<@10>" in embed.description
    assert "#1" in embed.description


@pytest.mark.asyncio
async def test_anyone_can_use_the_leaderboard_buttons() -> None:
    bot = _bot_with_pages({1: ([_wallet(10, 500)], 1)})
    view = LeaderboardPaginator(bot=bot, author_id=1, page_size=10)
    interaction = MagicMock()
    interaction.user.id = 999999  # not the author
    assert await view.interaction_check(interaction) is True


@pytest.mark.asyncio
async def test_next_and_prev_buttons_move_between_pages() -> None:
    bot = _bot_with_pages(
        {
            1: ([_wallet(10, 500)], 2),
            2: ([_wallet(20, 300)], 2),
        }
    )
    view = LeaderboardPaginator(bot=bot, author_id=1, page_size=1)
    await view.build_embed()  # establishes total_pages = 2
    interaction = MagicMock()
    interaction.response.edit_message = AsyncMock()

    await view.next_button.callback(interaction)
    assert view.current_page == 2
    embed = interaction.response.edit_message.await_args.kwargs["embed"]
    assert "<@20>" in embed.description

    await view.prev_button.callback(interaction)
    assert view.current_page == 1


@pytest.mark.asyncio
async def test_next_button_does_not_pass_the_last_page() -> None:
    bot = _bot_with_pages({1: ([_wallet(10, 500)], 1)})
    view = LeaderboardPaginator(bot=bot, author_id=1, page_size=10)
    await view.build_embed()
    interaction = MagicMock()
    interaction.response.edit_message = AsyncMock()
    await view.next_button.callback(interaction)
    assert view.current_page == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_economy_views.py -v`
Expected: FAIL with `ImportError: cannot import name 'LeaderboardPaginator'`

- [ ] **Step 3: Implement the view**

In `src/yokai/ui/views.py`, add `import math` to the imports (alongside `asyncio`), add `LIST_PAGE_SIZE` to the existing `from yokai.theme import quip_bank` line (making it `from yokai.theme import LIST_PAGE_SIZE, quip_bank`), and append after `QueuePaginator` (before `RecommendView`):

```python
class LeaderboardPaginator(BaseView):
    """Interactive paginator for the Renown leaderboard. Open to anyone, not just the caller."""

    def __init__(
        self,
        bot: Any,
        author_id: int,
        initial_page: int = 1,
        page_size: int = LIST_PAGE_SIZE,
        bot_avatar_url: Optional[str] = None,
        timeout: float = 180.0,
    ) -> None:
        super().__init__(allowed_user_ids=None, timeout=timeout)
        self.bot = bot
        self.author_id = author_id
        self.current_page = initial_page
        self.page_size = page_size
        self.bot_avatar_url = bot_avatar_url
        self.total_pages = 1

    def _update_buttons(self) -> None:
        self.prev_button.disabled = self.current_page <= 1
        self.next_button.disabled = self.current_page >= self.total_pages

    async def build_embed(self) -> discord.Embed:
        offset = (self.current_page - 1) * self.page_size
        wallets, total = await self.bot.db.get_leaderboard(limit=self.page_size, offset=offset)
        self.total_pages = max(1, math.ceil(total / self.page_size)) if total else 1
        self.current_page = min(max(self.current_page, 1), self.total_pages)
        self._update_buttons()
        rank_start = (self.current_page - 1) * self.page_size + 1
        entries = [(rank_start + i, w.user_id, w.balance) for i, w in enumerate(wallets)]
        return EmbedFactory.leaderboard_page(
            entries=entries,
            page=self.current_page,
            total_pages=self.total_pages,
            bot_avatar_url=self.bot_avatar_url,
        )

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
    async def prev_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if self.current_page > 1:
            self.current_page -= 1
        embed = await self.build_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if self.current_page < self.total_pages:
            self.current_page += 1
        embed = await self.build_embed()
        await interaction.response.edit_message(embed=embed, view=self)
```

Note: the test calls `view.next_button.callback(interaction)` directly (a `MagicMock` interaction is not a real Discord button press), matching how `discord.ui.button`-decorated methods are unit-tested elsewhere in this codebase.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_economy_views.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/yokai/ui/views.py tests/test_economy_views.py
git commit -m "feat(economy): add public leaderboard paginator view"
```

---

### Task 10: Economy slash commands

**Files:**
- Create: `src/yokai/cogs/economy.py`
- Create: `tests/test_economy_cog.py`
- Modify: `tests/test_one_emoji_rule.py`

**Interfaces:**
- Consumes: `Database.get_wallet/ensure_wallet/pay/claim_daily/admin_adjust_wallet` (Task 5); `cooldown_remaining_s` (Task 3); `DailyOnCooldownError, InsufficientFundsError, InvalidPaymentError` (Task 2); `EmbedFactory.balance/daily_claim/pay_sent/economy_admin` (Task 8); `LeaderboardPaginator` (Task 9); `send` (existing `ui/embeds.py`).
- Produces: `EconomyCog` with commands `balance`, `daily`, `pay`, `leaderboard`, `economy-admin`; `async def setup(bot: YokaiBot) -> None`. Consumed by `bot.py` (Task 11), which loads `yokai.cogs.economy` as an extension.

- [ ] **Step 1: Write the failing test**

Create `tests/test_economy_cog.py`:

```python
"""Tests for the economy slash commands."""

from __future__ import annotations

import random
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from yokai.cogs.economy import EconomyCog
from yokai.economy.models import DailyClaimResult, Wallet


def make_bot() -> MagicMock:
    bot = MagicMock()
    bot.user = None
    bot.config.owner_id = 999
    bot.config.economy_welcome_bonus = 200
    bot.config.economy_daily_base = 100
    return bot


def make_interaction(user_id: int = 111) -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    user.bot = False
    interaction.user = user
    interaction.response.defer = AsyncMock()
    return interaction


def _wallet(user_id: int, balance: int) -> Wallet:
    return Wallet(user_id=user_id, balance=balance, last_daily_ts=None, daily_streak=0, created_ts=0)


@pytest.mark.asyncio
async def test_balance_defaults_to_the_caller_and_ensures_their_wallet() -> None:
    bot = make_bot()
    bot.db.ensure_wallet = AsyncMock(return_value=_wallet(111, 200))
    cog = EconomyCog(bot)
    interaction = make_interaction(111)

    with patch("yokai.cogs.economy.send", new=AsyncMock()) as sent:
        await cog.balance.callback(cog, interaction, user=None)

    bot.db.ensure_wallet.assert_awaited_once_with(111, 200)
    embed = sent.await_args.kwargs["embed"]
    assert "200" in embed.description


@pytest.mark.asyncio
async def test_balance_for_another_user_is_read_only_and_shows_zero_if_absent() -> None:
    bot = make_bot()
    bot.db.get_wallet = AsyncMock(return_value=None)
    cog = EconomyCog(bot)
    interaction = make_interaction(111)
    target = MagicMock(spec=discord.Member)
    target.id = 222

    with patch("yokai.cogs.economy.send", new=AsyncMock()) as sent:
        await cog.balance.callback(cog, interaction, user=target)

    bot.db.get_wallet.assert_awaited_once_with(222)
    embed = sent.await_args.kwargs["embed"]
    assert "0" in embed.description


@pytest.mark.asyncio
async def test_daily_on_cooldown_replies_ephemeral_without_deferring() -> None:
    bot = make_bot()
    bot.db.get_wallet = AsyncMock(return_value=_wallet(111, 200))
    with patch("yokai.economy.daily.cooldown_remaining_s", return_value=3600):
        cog = EconomyCog(bot)
        interaction = make_interaction(111)
        with patch("yokai.cogs.economy.send", new=AsyncMock()) as sent:
            await cog.daily.callback(cog, interaction)

    interaction.response.defer.assert_not_awaited()
    assert sent.await_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_daily_success_defers_publicly_and_shows_the_payout() -> None:
    bot = make_bot()
    bot.db.get_wallet = AsyncMock(return_value=None)  # first-ever claim, no cooldown
    bot.db.claim_daily = AsyncMock(return_value=DailyClaimResult(payout=120, streak_day=1, new_balance=320))
    cog = EconomyCog(bot)
    cog._rng = random.Random(0)
    interaction = make_interaction(111)

    with patch("yokai.cogs.economy.send", new=AsyncMock()) as sent:
        await cog.daily.callback(cog, interaction)

    interaction.response.defer.assert_awaited_once()
    assert sent.await_args.kwargs.get("ephemeral", False) is False
    embed = sent.await_args.kwargs["embed"]
    assert "120" in embed.description


@pytest.mark.asyncio
async def test_pay_rejects_self_payment_before_touching_balances() -> None:
    """Review Focus: self-pay must be refused before any wallet is read or changed."""
    bot = make_bot()
    bot.db.ensure_wallet = AsyncMock()
    cog = EconomyCog(bot)
    interaction = make_interaction(111)
    target = MagicMock(spec=discord.Member)
    target.id = 111
    target.bot = False

    with patch("yokai.cogs.economy.send", new=AsyncMock()) as sent:
        await cog.pay.callback(cog, interaction, user=target, amount=10)

    bot.db.ensure_wallet.assert_not_awaited()
    assert sent.await_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_pay_rejects_a_bot_target() -> None:
    bot = make_bot()
    bot.db.ensure_wallet = AsyncMock()
    cog = EconomyCog(bot)
    interaction = make_interaction(111)
    target = MagicMock(spec=discord.Member)
    target.id = 222
    target.bot = True

    with patch("yokai.cogs.economy.send", new=AsyncMock()) as sent:
        await cog.pay.callback(cog, interaction, user=target, amount=10)

    bot.db.ensure_wallet.assert_not_awaited()
    assert sent.await_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_pay_rejects_a_non_positive_amount() -> None:
    bot = make_bot()
    bot.db.ensure_wallet = AsyncMock()
    cog = EconomyCog(bot)
    interaction = make_interaction(111)
    target = MagicMock(spec=discord.Member)
    target.id = 222
    target.bot = False

    with patch("yokai.cogs.economy.send", new=AsyncMock()) as sent:
        await cog.pay.callback(cog, interaction, user=target, amount=0)

    bot.db.ensure_wallet.assert_not_awaited()
    assert sent.await_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_pay_rejects_when_the_sender_cannot_afford_it_before_deferring() -> None:
    bot = make_bot()
    bot.db.ensure_wallet = AsyncMock(return_value=_wallet(111, 5))
    cog = EconomyCog(bot)
    interaction = make_interaction(111)
    target = MagicMock(spec=discord.Member)
    target.id = 222
    target.bot = False

    with patch("yokai.cogs.economy.send", new=AsyncMock()) as sent:
        await cog.pay.callback(cog, interaction, user=target, amount=999)

    interaction.response.defer.assert_not_awaited()
    assert sent.await_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_pay_success_defers_publicly_and_transfers() -> None:
    bot = make_bot()
    bot.db.ensure_wallet = AsyncMock(return_value=_wallet(111, 500))
    bot.db.pay = AsyncMock(return_value=(400, 300))
    cog = EconomyCog(bot)
    interaction = make_interaction(111)
    target = MagicMock(spec=discord.Member)
    target.id = 222
    target.bot = False

    with patch("yokai.cogs.economy.send", new=AsyncMock()) as sent:
        await cog.pay.callback(cog, interaction, user=target, amount=100)

    bot.db.pay.assert_awaited_once_with(111, 222, 100, welcome_bonus=200)
    assert sent.await_args.kwargs.get("ephemeral", False) is False


@pytest.mark.asyncio
async def test_economy_admin_refuses_non_owners() -> None:
    bot = make_bot()
    bot.db.admin_adjust_wallet = AsyncMock()
    cog = EconomyCog(bot)
    interaction = make_interaction(111)  # not the owner (999)
    target = MagicMock(spec=discord.Member)
    target.id = 222

    with patch("yokai.cogs.economy.send", new=AsyncMock()) as sent:
        await cog.economy_admin.callback(cog, interaction, user=target, amount=100)

    bot.db.admin_adjust_wallet.assert_not_awaited()
    interaction.response.defer.assert_not_awaited()
    assert sent.await_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_economy_admin_allows_the_owner() -> None:
    bot = make_bot()
    bot.db.admin_adjust_wallet = AsyncMock(return_value=(100, 300))
    cog = EconomyCog(bot)
    interaction = make_interaction(999)  # the owner
    target = MagicMock(spec=discord.Member)
    target.id = 222

    with patch("yokai.cogs.economy.send", new=AsyncMock()) as sent:
        await cog.economy_admin.callback(cog, interaction, user=target, amount=100)

    bot.db.admin_adjust_wallet.assert_awaited_once_with(222, 100, welcome_bonus=200)
    assert sent.await_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_leaderboard_defers_and_sends_a_paginator_view() -> None:
    from yokai.ui.views import LeaderboardPaginator

    bot = make_bot()
    bot.db.get_leaderboard = AsyncMock(return_value=([_wallet(111, 500)], 1))
    cog = EconomyCog(bot)
    interaction = make_interaction(111)
    message = MagicMock(spec=discord.Message)

    with patch("yokai.cogs.economy.send", new=AsyncMock(return_value=message)) as sent:
        await cog.leaderboard.callback(cog, interaction)

    interaction.response.defer.assert_awaited_once()
    view = sent.await_args.kwargs["view"]
    assert isinstance(view, LeaderboardPaginator)
    assert view.message is message
```

Add `"cogs/economy.py"` to the `CHECKED` list in `tests/test_one_emoji_rule.py`:

```python
CHECKED = ["cogs/meta.py", "cogs/music.py", "cogs/economy.py", "ui/views.py", "music/player.py", "bot.py"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_economy_cog.py tests/test_one_emoji_rule.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'yokai.cogs.economy'`

- [ ] **Step 3: Implement the cog**

Create `src/yokai/cogs/economy.py`:

```python
"""Renown economy slash commands: balance, daily claim, payments, leaderboard, admin tools."""

from __future__ import annotations

import random
import time
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

from yokai.economy.daily import cooldown_remaining_s
from yokai.errors import DailyOnCooldownError, InsufficientFundsError, InvalidPaymentError
from yokai.ui.embeds import EmbedFactory, send
from yokai.ui.views import LeaderboardPaginator

if TYPE_CHECKING:
    from yokai.bot import YokaiBot


class EconomyCog(commands.Cog, name="Economy"):
    """Renown wallet, daily claims, payments, and the leaderboard."""

    def __init__(self, bot: YokaiBot) -> None:
        self.bot = bot
        self._rng = random.Random()

    def _avatar_url(self) -> Optional[str]:
        return str(self.bot.user.display_avatar.url) if self.bot.user else None

    async def _send_domain_error(self, interaction: discord.Interaction, err: Exception) -> None:
        title = getattr(err, "title", "That didn't work")
        message = getattr(err, "message", str(err))
        hint = getattr(err, "user_hint", None)
        embed = EmbedFactory.error(
            message=message, title=title, user_hint=hint, bot_avatar_url=self._avatar_url()
        )
        await send(interaction, embed=embed, ephemeral=True)

    @app_commands.command(
        name="balance", description="Check your Renown balance, or another member's."
    )
    @app_commands.describe(user="Whose balance to check (defaults to you)")
    async def balance(
        self, interaction: discord.Interaction, user: Optional[discord.Member] = None
    ) -> None:
        """Show a Renown balance; only ever creates a wallet for the caller checking their own."""
        await interaction.response.defer(thinking=True)
        target = user or interaction.user
        if target.id == interaction.user.id:
            wallet = await self.bot.db.ensure_wallet(
                target.id, self.bot.config.economy_welcome_bonus
            )
            bal = wallet.balance
        else:
            wallet = await self.bot.db.get_wallet(target.id)
            bal = wallet.balance if wallet else 0
        embed = EmbedFactory.balance(
            user_mention=f"<@{target.id}>", balance=bal, bot_avatar_url=self._avatar_url()
        )
        await send(interaction, embed=embed)

    @app_commands.command(name="daily", description="Claim your daily Renown reward.")
    async def daily(self, interaction: discord.Interaction) -> None:
        """Claim /daily; a cooldown check runs before deferring so it stays ephemeral."""
        user_id = interaction.user.id
        now = int(time.time())
        wallet = await self.bot.db.get_wallet(user_id)
        remaining = cooldown_remaining_s(wallet.last_daily_ts if wallet else None, now)
        if remaining is not None:
            await self._send_domain_error(interaction, DailyOnCooldownError(remaining))
            return

        await interaction.response.defer(thinking=True)
        result = await self.bot.db.claim_daily(
            user_id,
            base=self.bot.config.economy_daily_base,
            welcome_bonus=self.bot.config.economy_welcome_bonus,
            now=now,
            rng=self._rng,
        )
        embed = EmbedFactory.daily_claim(
            user_mention=f"<@{user_id}>",
            payout=result.payout,
            streak_day=result.streak_day,
            new_balance=result.new_balance,
            bot_avatar_url=self._avatar_url(),
        )
        await send(interaction, embed=embed)

    @app_commands.command(name="pay", description="Send Renown to another member.")
    @app_commands.describe(user="Who to pay", amount="How much Renown to send (whole number)")
    async def pay(self, interaction: discord.Interaction, user: discord.Member, amount: int) -> None:
        """All validation happens before defer so a rejection stays ephemeral."""
        sender = interaction.user
        if amount <= 0:
            await self._send_domain_error(
                interaction, InvalidPaymentError("Enter a positive whole number of Renown to send.")
            )
            return
        if user.bot or user.id == sender.id:
            await self._send_domain_error(
                interaction, InvalidPaymentError("You can only pay another human member of the server.")
            )
            return

        sender_wallet = await self.bot.db.ensure_wallet(
            sender.id, self.bot.config.economy_welcome_bonus
        )
        if sender_wallet.balance < amount:
            await self._send_domain_error(
                interaction, InsufficientFundsError(balance=sender_wallet.balance, amount=amount)
            )
            return

        await interaction.response.defer(thinking=True)
        await self.bot.db.pay(
            sender.id, user.id, amount, welcome_bonus=self.bot.config.economy_welcome_bonus
        )
        embed = EmbedFactory.pay_sent(
            sender_mention=f"<@{sender.id}>",
            recipient_mention=f"<@{user.id}>",
            amount=amount,
            bot_avatar_url=self._avatar_url(),
        )
        await send(interaction, embed=embed)

    @app_commands.command(
        name="leaderboard", description="Show the top Renown balances on the server."
    )
    async def leaderboard(self, interaction: discord.Interaction) -> None:
        """Anyone can page through the leaderboard, not just whoever ran the command."""
        await interaction.response.defer(thinking=True)
        view = LeaderboardPaginator(
            bot=self.bot, author_id=interaction.user.id, bot_avatar_url=self._avatar_url()
        )
        embed = await view.build_embed()
        msg = await send(interaction, embed=embed, view=view)
        if isinstance(msg, discord.Message):
            view.message = msg

    @app_commands.command(
        name="economy-admin", description="Owner-only: grant or deduct a member's Renown."
    )
    @app_commands.describe(user="Member to adjust", amount="Renown to add (negative to deduct)")
    async def economy_admin(
        self, interaction: discord.Interaction, user: discord.Member, amount: int
    ) -> None:
        """Owner-only balance correction, always ephemeral, floored at 0."""
        if interaction.user.id != self.bot.config.owner_id:
            embed = EmbedFactory.error(
                message="Access denied: this command is restricted to the bot owner.",
                title="Owner only",
                user_hint="Contact the bot operator if you need a balance corrected.",
                context="Permissions",
                bot_avatar_url=self._avatar_url(),
                include_quip=False,
            )
            await send(interaction, embed=embed, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        applied, new_balance = await self.bot.db.admin_adjust_wallet(
            user.id, amount, welcome_bonus=self.bot.config.economy_welcome_bonus
        )
        embed = EmbedFactory.economy_admin(
            target_mention=f"<@{user.id}>",
            delta_applied=applied,
            new_balance=new_balance,
            bot_avatar_url=self._avatar_url(),
        )
        await send(interaction, embed=embed, ephemeral=True)


async def setup(bot: YokaiBot) -> None:
    """Extension setup entrypoint."""
    await bot.add_cog(EconomyCog(bot))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_economy_cog.py tests/test_one_emoji_rule.py tests/test_code_standards.py tests/test_title_case.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/yokai/cogs/economy.py tests/test_economy_cog.py tests/test_one_emoji_rule.py
git commit -m "feat(economy): add /balance, /daily, /pay, /leaderboard, /economy-admin commands"
```

---

### Task 11: Wire the economy into the bot

**Files:**
- Modify: `src/yokai/bot.py`
- Modify: `tests/test_bot_wiring.py`

**Interfaces:**
- Consumes: `CommandRewardLimiter` (Task 4), `VoiceEarnTracker` (Task 6), `EconomyCog`/`setup` (Task 10), `MUSIC_COMMAND_NAMES` (new, defined in `cogs/music.py` in this task).
- Produces: `YokaiBot.command_reward_limiter`, `YokaiBot.voice_earn_tracker`; the `yokai.cogs.economy` extension is loaded; `on_app_command_completion` credits eligible music commands.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_bot_wiring.py`:

```python
@pytest.mark.asyncio
async def test_music_command_completion_credits_renown_within_the_rate_limit() -> None:
    bot = make_bot()
    bot.db.log_command = AsyncMock()
    bot.db.credit_wallet = AsyncMock()
    interaction = MagicMock()
    interaction.user.id = 111
    command = MagicMock()
    command.qualified_name = "play"

    await bot.on_app_command_completion(interaction, command)

    bot.db.credit_wallet.assert_awaited_once_with(
        111, bot.config.economy_command_reward, reason="command",
        welcome_bonus=bot.config.economy_welcome_bonus,
    )


@pytest.mark.asyncio
async def test_non_music_command_completion_does_not_credit_renown() -> None:
    bot = make_bot()
    bot.db.log_command = AsyncMock()
    bot.db.credit_wallet = AsyncMock()
    interaction = MagicMock()
    interaction.user.id = 111
    command = MagicMock()
    command.qualified_name = "ping"

    await bot.on_app_command_completion(interaction, command)

    bot.db.credit_wallet.assert_not_awaited()


@pytest.mark.asyncio
async def test_music_command_reward_respects_the_rate_limit() -> None:
    bot = make_bot(economy_command_rate_limit=2)
    bot.db.log_command = AsyncMock()
    bot.db.credit_wallet = AsyncMock()
    interaction = MagicMock()
    interaction.user.id = 111
    command = MagicMock()
    command.qualified_name = "skip"

    for _ in range(3):
        await bot.on_app_command_completion(interaction, command)

    assert bot.db.credit_wallet.await_count == 2


@pytest.mark.asyncio
async def test_voice_earn_tracker_starts_and_stops_with_the_bot() -> None:
    bot = make_bot()
    bot.db.initialize = AsyncMock()
    bot.db.close = AsyncMock()
    bot.audio_buffer.start = AsyncMock()
    bot.searcher.warm_up = AsyncMock()
    bot.load_extension = AsyncMock()
    bot.tree.sync = AsyncMock(return_value=[])
    bot.spotify.close = AsyncMock()
    with patch.object(bot.voice_earn_tracker, "start") as start_mock:
        await bot.setup_hook()
    start_mock.assert_called_once()

    with patch.object(bot.voice_earn_tracker, "stop", new=AsyncMock()) as stop_mock:
        await bot.close()
    stop_mock.assert_awaited_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_bot_wiring.py -v`
Expected: FAIL with `AttributeError: 'YokaiBot' object has no attribute 'voice_earn_tracker'`

- [ ] **Step 3: Implement the wiring**

In `src/yokai/cogs/music.py`, add near the top (after the module docstring and imports, before `_observe_task`):

```python
MUSIC_COMMAND_NAMES: frozenset[str] = frozenset(
    {
        "play",
        "pause",
        "resume",
        "skip",
        "stop",
        "queue",
        "nowplaying",
        "remove",
        "clear",
        "shuffle",
        "loop",
        "volume",
        "recommend",
    }
)
```

In `src/yokai/bot.py`, add imports (alongside the existing `from yokai.music...` imports):

```python
from yokai.cogs.music import MUSIC_COMMAND_NAMES
from yokai.economy.ratelimit import CommandRewardLimiter
from yokai.economy.voice_tracker import VoiceEarnTracker
```

In `YokaiBot.__init__`, after `self.error_ring_buffer = collections.deque(maxlen=20)`, add:

```python
        self.command_reward_limiter = CommandRewardLimiter(
            limit=config.economy_command_rate_limit, window_s=60.0
        )
        self.voice_earn_tracker = VoiceEarnTracker(
            bot=self,
            interval_s=config.economy_voice_interval_seconds,
            reward=config.economy_voice_reward,
        )
```

In `setup_hook`, after `await self.load_extension("yokai.cogs.music")`, add:

```python
        await self.load_extension("yokai.cogs.economy")
```

and after the `try/except` block that syncs commands (still inside `setup_hook`), add:

```python
        self.voice_earn_tracker.start()
```

In `close`, after the `for p in list(self.players.values()):` loop and before `try: await self.spotify.close()`, add:

```python
        await self.voice_earn_tracker.stop()
```

Replace the body of `on_app_command_completion` with:

```python
    async def on_app_command_completion(
        self,
        interaction: discord.Interaction,
        command: app_commands.Command | app_commands.ContextMenu,
    ) -> None:
        """Central audit hook: records commands and credits Renown for eligible music commands."""
        cmd_name = command.qualified_name
        try:
            await self.db.log_command(interaction.user.id, cmd_name)
            logger.debug("Logged command /%s by user %d", cmd_name, interaction.user.id)
        except Exception as exc:
            logger.error("Failed to log command execution in database: %s", exc)

        if cmd_name in MUSIC_COMMAND_NAMES and self.command_reward_limiter.try_consume(
            interaction.user.id
        ):
            try:
                await self.db.credit_wallet(
                    interaction.user.id,
                    self.config.economy_command_reward,
                    reason="command",
                    welcome_bonus=self.config.economy_welcome_bonus,
                )
            except Exception as exc:
                logger.error("Failed to credit music-command Renown reward: %s", exc)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_bot_wiring.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/yokai/bot.py src/yokai/cogs/music.py tests/test_bot_wiring.py
git commit -m "feat(economy): wire voice tracker and music-command rewards into the bot"
```

---

### Task 12: Embed preview samples

**Files:**
- Modify: `scripts/preview_embeds.py`

**Interfaces:**
- Consumes: `EmbedFactory.balance/daily_claim/pay_sent/leaderboard_page/economy_admin` (Task 8).

- [ ] **Step 1: Write the failing test**

No new test file: `tests/test_preview_embeds.py::test_every_sample_embed_obeys_the_layout_rules` and `test_render_page_is_a_fragment_containing_every_embed` already generically re-check whatever `build_samples()` returns.

Run: `python -m pytest tests/test_preview_embeds.py -v`
Expected: PASS already (these tests don't fail without the new samples — this task is about visual completeness, verified in Step 4 by checking the new labels are present)

- [ ] **Step 2: (n/a — no new failing assertion; proceed to implementation)**

- [ ] **Step 3: Add the new samples**

In `scripts/preview_embeds.py`, inside `build_samples()`, add to the `samples` list (right before the closing `]` and `return samples`):

```python
        (
            "Renown balance",
            EmbedFactory.balance(user_mention="<@42>", balance=1250, bot_avatar_url=av),
        ),
        (
            "Daily claimed",
            EmbedFactory.daily_claim(
                user_mention="<@42>", payout=180, streak_day=5, new_balance=1430, bot_avatar_url=av
            ),
        ),
        (
            "Renown sent",
            EmbedFactory.pay_sent(
                sender_mention="<@42>", recipient_mention="<@7>", amount=250, bot_avatar_url=av
            ),
        ),
        (
            "Renown leaderboard",
            EmbedFactory.leaderboard_page(
                entries=[(1, 42, 4200), (2, 7, 3100), (3, 13, 900)],
                page=1,
                total_pages=1,
                bot_avatar_url=av,
            ),
        ),
        (
            "Renown leaderboard (empty)",
            EmbedFactory.leaderboard_page(entries=[], page=1, total_pages=1, bot_avatar_url=av),
        ),
        (
            "Economy admin (grant)",
            EmbedFactory.economy_admin(
                target_mention="<@7>", delta_applied=500, new_balance=3600, bot_avatar_url=av
            ),
        ),
        (
            "Economy admin (deduction)",
            EmbedFactory.economy_admin(
                target_mention="<@7>", delta_applied=-200, new_balance=0, bot_avatar_url=av
            ),
        ),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_preview_embeds.py -v`
Expected: PASS. Then regenerate the preview file: `python scripts/preview_embeds.py` and confirm it reports writing `docs/preview/embeds.html`.

- [ ] **Step 5: Commit**

```bash
git add scripts/preview_embeds.py docs/preview/embeds.html
git commit -m "docs(economy): add Renown embeds to the visual preview page"
```

---

### Task 13: Record the phase in TASKS.md and final verification

**Files:**
- Modify: `TASKS.md`

- [ ] **Step 1: Append the phase record**

In `TASKS.md`, insert a new section after the "Phase 6 — Hardening and polish" section's `**STOP — owner review. v1 complete.**` line and before the `## Later` section:

```markdown
---

## Phase 7 — Economy: Core wallet

**Goal:** A server-only Renown currency: a wallet per user, `/balance`, `/daily`, `/pay`,
`/leaderboard`, an owner-only `/economy-admin`, and passive earning from voice presence and
music-command usage. Spec: `docs/superpowers/specs/2026-09-29-economy-core-design.md`. Plan:
`docs/superpowers/plans/2026-09-29-economy-core-plan.md`.

- [x] E1. Economy configuration (`ECONOMY_*` variables in `config.py` and `.env.example`).
- [x] E2. Typed errors: `DailyOnCooldownError`, `InsufficientFundsError`, `InvalidPaymentError`.
- [x] E3. Wallet models and pure `/daily` math (`economy/models.py`, `economy/daily.py`).
- [x] E4. Command-reward rate limiter (`economy/ratelimit.py`).
- [x] E5. Wallet storage: migration v2 (`wallets`, `renown_transactions`), get/ensure/credit/debit/
      pay/claim_daily/admin_adjust/leaderboard on `Database`.
- [x] E6. Background voice-presence Renown tick (`economy/voice_tracker.py`).
- [x] E7. Theme additions: Renown coin emoji, `daily_claim`/`pay_sent` quips.
- [x] E8. Economy embed builders: `balance`, `daily_claim`, `pay_sent`, `leaderboard_page`,
      `economy_admin`.
- [x] E9. Public leaderboard paginator view (`ui/views.py`).
- [x] E10. Economy slash commands (`cogs/economy.py`): `/balance`, `/daily`, `/pay`,
      `/leaderboard`, `/economy-admin`.
- [x] E11. Wired into `bot.py`: voice tracker lifecycle, music-command reward via the existing
      `on_app_command_completion` hook.
- [x] E12. Embed preview page updated with every new embed shape.

**Acceptance criteria**
- A brand-new user's first `/balance` or `/daily` grants the welcome bonus exactly once.
- `/daily` pays out with the random and streak multipliers described in the spec, refuses a
  second claim within 24h (ephemerally), and resets the streak after a 48h gap. *(manual + unit)*
- `/pay` moves Renown between two users atomically, refuses self-pay, bot targets, and
  insufficient balance before deferring.
- Sitting in a voice channel with at least one other human earns Renown on each tick; a lone
  human earns nothing. *(manual, requires two real accounts in a voice channel)*
- Using music commands earns Renown up to 5 rewarded calls per minute; further calls in the
  same window earn nothing but still work normally. *(manual + unit)*
- `/leaderboard` paginates correctly and is usable by anyone, not just the caller.
- `/economy-admin` is refused for non-owners and floors a large deduction at 0.
- An existing pre-Phase-7 database (`data/yokai.db` at schema v1) upgrades to v2 on the next
  startup without losing any `command_log`/`play_events`/`spotify_match_cache` rows.
- `pytest` and `ruff check` and `ruff format --check` are clean.

**STOP — owner review.**
```

- [ ] **Step 2: Run the full verification suite**

Run: `python -m pytest -q`
Expected: all tests pass (0 failures), `live`-marked tests skipped

Run: `python -m ruff check .`
Expected: no issues

Run: `python -m ruff format --check .`
Expected: no reformatting needed (if it reports files needing formatting, run `python -m ruff format .` and re-check)

- [ ] **Step 3: Commit**

```bash
git add TASKS.md
git commit -m "docs: record Phase 7 (Economy: Core wallet) in TASKS.md"
```

---
