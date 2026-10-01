# Gambling G1 (Escrow + Solo Games) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the gambling foundation (migration v3, bet escrow with crash-safe refunds, pure game engines) and the three solo games: `/slots`, `/blackjack`, `/roulette`, plus `/gamble-stats`.

**Architecture:** A new `yokai/gambling/` package holds the shared escrow helpers, a one-game-per-user registry, and pure seedable engines (`engines/slots.py`, `roulette.py`, `blackjack.py`) that never import `discord`. Persistence is new methods on the existing `Database` class (`place_bet`, `settle_bet`, `refund_bet`, `refund_open_bets`, `get_gamble_stats`) over one new table, `gambling_bets`. A thin `cogs/gambling.py` validates input, runs an engine, and renders through `EmbedFactory`; interactive blackjack lives in `ui/game_views.py`. `bot.py` loads the cog and refunds any bet left open by a previous process at startup.

**Tech Stack:** Python 3.12+, discord.py 2.7.1 (already installed), aiosqlite (already installed). No new dependency.

**Spec:** `docs/superpowers/specs/2026-09-30-gambling-design.md` (this plan implements phase G1 of §11; G2–G4 get their own plans after G1's review, so they match the interfaces actually built).

## Global Constraints

- Python 3.12+, `discord.py>=2.7.1`; **no new dependency** (AGENTS.md rule 5).
- Slash commands only (`app_commands`), guild-scoped, no privileged intents.
- Every user-visible message goes through `EmbedFactory` / `send`; no raw `send_message("text")` / `channel.send("text")` (enforced by `tests/test_code_standards.py`). Error embeds are ephemeral.
- **At most one emoji per embed** (enforced by `tests/test_embeds.py`-style helpers and `tests/test_one_emoji_rule.py`). Card suit glyphs (`♠♥♦♣`) and slot-reel emoji count as emoji, so cards render in poker notation (`As`, `Kh`, `10d`) and reels as text names (`[CHERRY]`). New emoji constants must match `tests/embed_helpers.EMOJI_RE` (`tests/test_theme.py` enforces it).
- Validate bet range, balance and "already in a game" **before** `defer`, so rejections stay ephemeral (AGENTS.md defer-visibility rule).
- `allowed_mentions` none; mention users only inside embeds via `<@id>`.
- Buttons and views time out, disable themselves, and only act for allowed users (`BaseView`).
- Defaults: `GAMBLING_MIN_BET=10`, `GAMBLING_MAX_BET=5000`. Whole-number bets only.
- Escrow: stakes are debited when a game starts; every debit/credit writes a `renown_transactions` row; open bets from a previous process are refunded at startup.
- Production randomness is `random.SystemRandom()` (`gambling/rng.new_rng`); tests inject `random.Random(seed)`.
- Type-hinted, `from __future__ import annotations`, no `print`, no bare `except`, `logging` only, ruff line length 100.
- Commit per task (message + trailer `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`); never commit `.env`, databases, tokens. Run `python -m pytest -q` and `python -m ruff check .` / `python -m ruff format --check .` before calling any task done.
- Work happens on `main` with an annotated checkpoint tag first (owner preference); stop for owner review after the last task.

## Review Focus

- **Bet-range boundaries**: exactly min and exactly max are accepted; min-1 and max+1 are rejected. Test in Task 4.
- **Odd stakes never over-pay**: a 3:2 blackjack on a stake of 15 pays 37 (floor), a 1.4x slots win on 15 pays 21, never rounded up. Tests in Tasks 5 and 7.
- **Double-click on a blackjack button is processed once** (the second press is a no-op and cannot double-settle). Test in Task 10.
- **Pressing Double after spending the balance elsewhere** (e.g. `/pay` mid-hand) shows an ephemeral error and the hand continues; it never goes negative. Test in Task 10.
- **Restart mid-hand**: every `open` bet is refunded at startup and settled bets are untouched; the in-memory active-game registry starts empty. Tests in Tasks 3 and 9.
- **A brand-new user can bet their whole welcome bonus**, and a loss leaves a balance of exactly 0, never negative. Test in Task 9.

---

### Task 0: Checkpoint tag

**Files:** none (git only).

- [ ] **Step 1: Confirm a clean tree and tag the starting point**

Run: `git status --short` (expect no output) then:

```bash
git tag -a checkpoint/pre-gambling-g1 -m "State before gambling G1 (escrow + solo games)"
git tag --list "checkpoint/pre-gambling*"
```

Expected: prints `checkpoint/pre-gambling-g1`.

---

### Task 1: Gambling configuration

**Files:**
- Modify: `src/yokai/config.py`
- Modify: `.env.example`
- Test: `tests/test_gambling_config.py`

**Interfaces:**
- Produces: `Config.gambling_min_bet: int` (default 10, floored at 1), `Config.gambling_max_bet: int` (default 5000, must be >= min or `ConfigError`).

- [ ] **Step 1: Write the failing test** — create `tests/test_gambling_config.py`:

```python
"""Tests for the gambling configuration variables."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from yokai.config import Config, ConfigError, load_config

BASE_ENV = {"DISCORD_TOKEN": "t", "GUILD_ID": "1", "OWNER_ID": "2"}


def _load(extra: dict[str, str] | None = None) -> Config:
    with patch.dict(os.environ, {**BASE_ENV, **(extra or {})}, clear=True):
        return load_config(env_path=Path("non_existent_env"))


def test_gambling_defaults() -> None:
    cfg = _load()
    assert cfg.gambling_min_bet == 10
    assert cfg.gambling_max_bet == 5000


def test_gambling_overrides() -> None:
    cfg = _load({"GAMBLING_MIN_BET": "25", "GAMBLING_MAX_BET": "900"})
    assert (cfg.gambling_min_bet, cfg.gambling_max_bet) == (25, 900)


def test_min_bet_is_floored_at_one() -> None:
    assert _load({"GAMBLING_MIN_BET": "0"}).gambling_min_bet == 1


def test_max_bet_below_min_bet_is_a_config_error() -> None:
    with pytest.raises(ConfigError, match="GAMBLING_MAX_BET"):
        _load({"GAMBLING_MIN_BET": "100", "GAMBLING_MAX_BET": "50"})
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_gambling_config.py -q`
Expected: FAIL (`AttributeError: ... 'gambling_min_bet'`).

- [ ] **Step 3: Implement.** In `src/yokai/config.py`, add after the economy fields of `Config`:

```python
    # Gambling: stake limits
    gambling_min_bet: int = 10
    gambling_max_bet: int = 5000
```

In `load_config`, after the `economy_command_rate_limit = ...` statement and before `return Config(`:

```python
    # Gambling: stake limits (a bet of 0 or less is never valid, so the floor is 1)
    gambling_min_bet = max(1, _parse_int("GAMBLING_MIN_BET", os.getenv("GAMBLING_MIN_BET"), 10))
    gambling_max_bet = _parse_int("GAMBLING_MAX_BET", os.getenv("GAMBLING_MAX_BET"), 5000)
    if gambling_max_bet < gambling_min_bet:
        raise ConfigError("GAMBLING_MAX_BET must be greater than or equal to GAMBLING_MIN_BET.")
```

and add to the `Config(...)` call: `gambling_min_bet=gambling_min_bet, gambling_max_bet=gambling_max_bet,`.

Append to `.env.example`:

```
# Gambling: stake limits (whole Renown)
GAMBLING_MIN_BET=10
GAMBLING_MAX_BET=5000
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_gambling_config.py tests/test_config.py -q; python -m ruff check . ; python -m ruff format --check .`
Expected: all pass, ruff clean (run `python -m ruff format .` if the format check fails, then re-check).

- [ ] **Step 5: Commit**

```bash
git add src/yokai/config.py .env.example tests/test_gambling_config.py
git commit -m "feat(gambling): add GAMBLING_MIN_BET and GAMBLING_MAX_BET config" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Typed gambling errors

**Files:**
- Modify: `src/yokai/errors.py`
- Test: `tests/test_gambling_errors.py`

**Interfaces:**
- Produces: `InsufficientFundsError(balance: int, amount: int, action: str = "send")` (backward compatible), `BetOutOfRangeError(minimum: int, maximum: int)`, `AlreadyInGameError()`, `BetNotOpenError(bet_id: int)`, `InvalidGameChoiceError(message: str)`. All subclass `YokaiError`.

- [ ] **Step 1: Write the failing test** — create `tests/test_gambling_errors.py`:

```python
"""Tests for the gambling error types."""

from __future__ import annotations

from yokai.errors import (
    AlreadyInGameError,
    BetNotOpenError,
    BetOutOfRangeError,
    InsufficientFundsError,
    InvalidGameChoiceError,
    YokaiError,
)


def test_insufficient_funds_default_wording_is_unchanged() -> None:
    err = InsufficientFundsError(balance=5, amount=10)
    assert err.message == "You have **5** Renown, which isn't enough to send **10**."


def test_insufficient_funds_can_describe_a_bet() -> None:
    err = InsufficientFundsError(balance=5, amount=10, action="bet")
    assert err.message == "You have **5** Renown, which isn't enough to bet **10**."


def test_bet_out_of_range_shows_the_limits() -> None:
    err = BetOutOfRangeError(10, 5000)
    assert "10" in err.message and "5,000" in err.message
    assert err.user_hint


def test_every_new_error_is_a_yokai_error_with_a_title() -> None:
    for err in (
        BetOutOfRangeError(1, 2),
        AlreadyInGameError(),
        BetNotOpenError(3),
        InvalidGameChoiceError("nope"),
    ):
        assert isinstance(err, YokaiError)
        assert err.title and err.message
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_gambling_errors.py -q`
Expected: FAIL (`ImportError: cannot import name 'AlreadyInGameError'`).

- [ ] **Step 3: Implement.** In `src/yokai/errors.py`, replace the `InsufficientFundsError` class with:

```python
class InsufficientFundsError(YokaiError):
    """Raised when a payment, bet or debit would exceed the payer's balance."""

    title = "Not enough Renown"

    def __init__(self, balance: int, amount: int, action: str = "send") -> None:
        super().__init__(
            f"You have **{balance:,}** Renown, which isn't enough to {action} **{amount:,}**.",
            user_hint="Earn more with /daily or by using Yokai in voice.",
        )
        self.balance = balance
        self.amount = amount
```

and append to the end of the file:

```python
class BetOutOfRangeError(YokaiError):
    """Raised when a stake is outside the configured minimum and maximum."""

    title = "Bet out of range"

    def __init__(self, minimum: int, maximum: int) -> None:
        super().__init__(
            f"Bets must be a whole number from **{minimum:,}** to **{maximum:,}** Renown.",
            user_hint="Pick an amount inside that range and try again.",
        )
        self.minimum = minimum
        self.maximum = maximum


class AlreadyInGameError(YokaiError):
    """Raised when a member starts a game while another of theirs is still running."""

    title = "Already in a game"

    def __init__(self) -> None:
        super().__init__(
            "You already have a game in progress.",
            user_hint="Finish your current game first.",
        )


class BetNotOpenError(YokaiError):
    """Raised when settling or refunding a bet that is not open (settled twice, or unknown)."""

    title = "Bet already settled"

    def __init__(self, bet_id: int) -> None:
        super().__init__(f"Bet #{bet_id} is not open.")
        self.bet_id = bet_id


class InvalidGameChoiceError(YokaiError):
    """Raised when a game option (such as a roulette bet type or pick) is invalid."""

    title = "Invalid choice"

    def __init__(self, message: str) -> None:
        super().__init__(message, user_hint="Check the command options and try again.")
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_gambling_errors.py tests/test_errors.py tests/test_economy_cog.py -q; python -m ruff check .; python -m ruff format --check .`
Expected: pass, clean.

- [ ] **Step 5: Commit**

```bash
git add src/yokai/errors.py tests/test_gambling_errors.py
git commit -m "feat(gambling): add typed gambling errors" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Migration v3 and bet escrow storage

**Files:**
- Modify: `src/yokai/storage/db.py`
- Test: `tests/test_gambling_storage.py`

**Interfaces:**
- Consumes: `Database._write_lock`, `_ensure_wallet`, `_insert_transaction`, `get_wallet`; errors from Task 2.
- Produces on `Database`:
  - `await place_bet(user_id: int, amount: int, game: str, welcome_bonus: int, lobby_id: str | None = None, group_id: str | None = None) -> int` (bet id; raises `InsufficientFundsError(action="bet")`, `ValueError` for amount <= 0)
  - `await settle_bet(bet_id: int, payout: int) -> int` (new balance; `BetNotOpenError` if not open; `ValueError` if payout < 0)
  - `await refund_bet(bet_id: int) -> int` (new balance; `BetNotOpenError` if not open)
  - `await refund_open_bets() -> int` (count refunded)
  - `await get_gamble_stats(user_id: int) -> list[tuple[str, int, int, int]]` rows of `(game, plays, wagered, returned)` over settled bets, ordered by game.
  - Transaction reasons: `bet:<game>`, `win:<game>` (payout > stake), `push:<game>` (payout == stake), `payout:<game>` (0 < payout < stake), `refund:<game>`. A loss (payout 0) writes no extra transaction row; the `gambling_bets` row records it.

- [ ] **Step 1: Write the failing tests** — create `tests/test_gambling_storage.py`:

```python
"""Tests for migration v3 and the bet escrow methods on Database."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import aiosqlite
import pytest

from yokai.errors import BetNotOpenError, InsufficientFundsError
from yokai.storage.db import CURRENT_SCHEMA_VERSION, MIGRATION_V1, MIGRATION_V2, Database

WELCOME = 200


async def _fresh_db(tmp_dir: str) -> Database:
    db = Database(Path(tmp_dir) / "test_yokai.db")
    await db.initialize()
    return db


async def _tx_rows(db: Database, user_id: int) -> list[tuple[int, str, int]]:
    query = (
        "SELECT delta, reason, balance_after FROM renown_transactions "
        "WHERE user_id = ? ORDER BY id;"
    )
    async with db.conn.execute(query, (user_id,)) as cursor:
        return [(r[0], r[1], r[2]) for r in await cursor.fetchall()]


async def _balance(db: Database, user_id: int) -> int:
    wallet = await db.get_wallet(user_id)
    assert wallet is not None
    return wallet.balance


@pytest.mark.asyncio
async def test_a_v2_database_upgrades_to_v3_without_losing_wallets() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "existing_v2.db"
        conn = await aiosqlite.connect(db_path)
        await conn.executescript(MIGRATION_V1)
        await conn.executescript(MIGRATION_V2)
        await conn.execute("PRAGMA user_version = 2;")
        await conn.execute(
            "INSERT INTO wallets (user_id, balance, daily_streak, created_ts) "
            "VALUES (7, 321, 0, 1);"
        )
        await conn.commit()
        await conn.close()

        db = Database(db_path)
        await db.initialize()
        async with db.conn.execute("PRAGMA user_version;") as cursor:
            row = await cursor.fetchone()
        assert row is not None and row[0] == CURRENT_SCHEMA_VERSION == 3
        assert await _balance(db, 7) == 321
        async with db.conn.execute(
            "SELECT name FROM sqlite_master WHERE name = 'gambling_bets';"
        ) as cursor:
            assert await cursor.fetchone() is not None
        await db.close()


@pytest.mark.asyncio
async def test_place_bet_debits_and_records_an_open_bet() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = await _fresh_db(tmp_dir)
        bet_id = await db.place_bet(1, 50, "slots", WELCOME)
        assert await _balance(db, 1) == 150
        assert await _tx_rows(db, 1) == [(200, "welcome", 200), (-50, "bet:slots", 150)]
        async with db.conn.execute(
            "SELECT user_id, game, stake, status, payout FROM gambling_bets WHERE id = ?;",
            (bet_id,),
        ) as cursor:
            assert await cursor.fetchone() == (1, "slots", 50, "open", 0)
        await db.close()


@pytest.mark.asyncio
async def test_place_bet_refuses_more_than_the_balance_and_changes_nothing() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = await _fresh_db(tmp_dir)
        await db.ensure_wallet(1, WELCOME)
        with pytest.raises(InsufficientFundsError) as excinfo:
            await db.place_bet(1, WELCOME + 1, "slots", WELCOME)
        assert "bet" in excinfo.value.message
        assert await _balance(db, 1) == WELCOME
        async with db.conn.execute("SELECT COUNT(*) FROM gambling_bets;") as cursor:
            row = await cursor.fetchone()
        assert row is not None and row[0] == 0
        await db.close()


@pytest.mark.asyncio
async def test_place_bet_rejects_non_positive_amounts() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = await _fresh_db(tmp_dir)
        with pytest.raises(ValueError):
            await db.place_bet(1, 0, "slots", WELCOME)
        await db.close()


@pytest.mark.asyncio
async def test_concurrent_bets_cannot_overspend() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = await _fresh_db(tmp_dir)
        await db.ensure_wallet(1, WELCOME)
        results = await asyncio.gather(
            *[db.place_bet(1, 100, "slots", WELCOME) for _ in range(5)],
            return_exceptions=True,
        )
        won = [r for r in results if isinstance(r, int)]
        refused = [r for r in results if isinstance(r, InsufficientFundsError)]
        assert len(won) == 2 and len(refused) == 3
        assert await _balance(db, 1) == 0
        await db.close()


@pytest.mark.asyncio
async def test_settle_win_credits_the_payout() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = await _fresh_db(tmp_dir)
        bet_id = await db.place_bet(1, 100, "slots", WELCOME)
        balance = await db.settle_bet(bet_id, 250)
        assert balance == 350
        assert await _tx_rows(db, 1) == [
            (200, "welcome", 200),
            (-100, "bet:slots", 100),
            (250, "win:slots", 350),
        ]
        await db.close()


@pytest.mark.asyncio
async def test_settle_loss_pays_nothing_and_adds_no_transaction() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = await _fresh_db(tmp_dir)
        bet_id = await db.place_bet(1, 100, "slots", WELCOME)
        assert await db.settle_bet(bet_id, 0) == 100
        assert len(await _tx_rows(db, 1)) == 2
        async with db.conn.execute(
            "SELECT status, payout FROM gambling_bets WHERE id = ?;", (bet_id,)
        ) as cursor:
            assert await cursor.fetchone() == ("settled", 0)
        await db.close()


@pytest.mark.asyncio
async def test_settle_push_is_labelled_push() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = await _fresh_db(tmp_dir)
        bet_id = await db.place_bet(1, 100, "blackjack", WELCOME)
        assert await db.settle_bet(bet_id, 100) == 200
        assert (await _tx_rows(db, 1))[-1] == (100, "push:blackjack", 200)
        await db.close()


@pytest.mark.asyncio
async def test_settling_twice_raises_and_never_double_pays() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = await _fresh_db(tmp_dir)
        bet_id = await db.place_bet(1, 100, "slots", WELCOME)
        await db.settle_bet(bet_id, 200)
        before = await _balance(db, 1)
        with pytest.raises(BetNotOpenError):
            await db.settle_bet(bet_id, 200)
        assert await _balance(db, 1) == before
        await db.close()


@pytest.mark.asyncio
async def test_negative_payout_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = await _fresh_db(tmp_dir)
        bet_id = await db.place_bet(1, 100, "slots", WELCOME)
        with pytest.raises(ValueError):
            await db.settle_bet(bet_id, -1)
        await db.close()


@pytest.mark.asyncio
async def test_refund_returns_the_stake_once() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = await _fresh_db(tmp_dir)
        bet_id = await db.place_bet(1, 100, "slots", WELCOME)
        assert await db.refund_bet(bet_id) == 200
        assert (await _tx_rows(db, 1))[-1] == (100, "refund:slots", 200)
        with pytest.raises(BetNotOpenError):
            await db.refund_bet(bet_id)
        with pytest.raises(BetNotOpenError):
            await db.settle_bet(bet_id, 500)
        assert await _balance(db, 1) == 200
        await db.close()


@pytest.mark.asyncio
async def test_startup_sweep_refunds_open_bets_and_leaves_settled_ones() -> None:
    """Review Focus: a restart mid-hand returns every open stake, and only those."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = await _fresh_db(tmp_dir)
        settled = await db.place_bet(1, 40, "slots", WELCOME)
        await db.settle_bet(settled, 0)
        await db.place_bet(1, 60, "blackjack", WELCOME)
        await db.place_bet(2, 100, "roulette", WELCOME)
        assert await db.refund_open_bets() == 2
        assert await _balance(db, 1) == 160  # 200 - 40 (lost); the open 60 came back
        assert await _balance(db, 2) == WELCOME
        assert await db.refund_open_bets() == 0
        await db.close()


@pytest.mark.asyncio
async def test_gamble_stats_sum_settled_bets_per_game() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = await _fresh_db(tmp_dir)
        for game, payout in (("slots", 0), ("slots", 40), ("blackjack", 20)):
            bet_id = await db.place_bet(1, 10, game, WELCOME)
            await db.settle_bet(bet_id, payout)
        await db.place_bet(1, 10, "roulette", WELCOME)  # still open: not counted
        refunded = await db.place_bet(1, 10, "slots", WELCOME)
        await db.refund_bet(refunded)  # refunded: not counted
        assert await db.get_gamble_stats(1) == [("blackjack", 1, 10, 20), ("slots", 2, 20, 40)]
        assert await db.get_gamble_stats(99) == []
        await db.close()
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_gambling_storage.py -q`
Expected: FAIL (`AttributeError: 'Database' object has no attribute 'place_bet'`, and the schema-version assertion).

- [ ] **Step 3: Implement.** In `src/yokai/storage/db.py`:

1. Change the errors import to `from yokai.errors import BetNotOpenError, DailyOnCooldownError, InsufficientFundsError` and set `CURRENT_SCHEMA_VERSION = 3`.

2. After `MIGRATION_V2`, add:

```python
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
```

3. In `_migrate`, after the `if version < 2:` block:

```python
        if version < 3:
            logger.info("Applying SQLite migration v3...")
            await self.conn.executescript(MIGRATION_V3)
            await self.conn.execute("PRAGMA user_version = 3;")
            await self.conn.commit()
            logger.info("SQLite schema migrated to version 3.")
```

4. Add these methods to `Database` (after `admin_adjust_wallet`, before `get_leaderboard`):

```python
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
            await self._ensure_wallet(user_id, welcome_bonus, now)
            cursor = await self.conn.execute(
                "UPDATE wallets SET balance = balance - ? WHERE user_id = ? AND balance >= ?;",
                (amount, user_id, amount),
            )
            wallet = await self.get_wallet(user_id)
            assert wallet is not None
            if cursor.rowcount == 0:
                await self.conn.rollback()
                raise InsufficientFundsError(balance=wallet.balance, amount=amount, action="bet")
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
        assert bet_id is not None
        return bet_id

    async def _finish_bet(self, bet_id: int, payout: int, refund: bool) -> int:
        """Close an open bet exactly once, crediting `payout` (the stake when refunding)."""
        now = int(time.time())
        async with self._write_lock:
            async with self.conn.execute(
                "SELECT user_id, game, stake FROM gambling_bets WHERE id = ? AND status = 'open';",
                (bet_id,),
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                raise BetNotOpenError(bet_id)
            user_id, game, stake = row
            credit = stake if refund else payout
            status = "refunded" if refund else "settled"
            # Conditioned on status = 'open', so a racing second close can never double-pay.
            cursor = await self.conn.execute(
                "UPDATE gambling_bets SET status = ?, payout = ? WHERE id = ? AND status = 'open';",
                (status, credit, bet_id),
            )
            if cursor.rowcount == 0:
                await self.conn.rollback()
                raise BetNotOpenError(bet_id)
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
        return wallet.balance

    async def settle_bet(self, bet_id: int, payout: int) -> int:
        """Settle an open bet with a total `payout` (0 = loss); returns the new balance."""
        if payout < 0:
            raise ValueError("settle_bet payout must not be negative")
        return await self._finish_bet(bet_id, payout, refund=False)

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
            "SELECT game, COUNT(*), SUM(stake), SUM(payout) FROM gambling_bets "
            "WHERE user_id = ? AND status = 'settled' GROUP BY game ORDER BY game ASC;"
        )
        async with self.conn.execute(query, (user_id,)) as cursor:
            return [(r[0], r[1], r[2], r[3]) for r in await cursor.fetchall()]
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_gambling_storage.py tests/test_storage.py tests/test_economy_storage.py -q; python -m ruff check .; python -m ruff format --check .`
Expected: all pass, clean.

- [ ] **Step 5: Commit**

```bash
git add src/yokai/storage/db.py tests/test_gambling_storage.py
git commit -m "feat(gambling): migration v3 and crash-safe bet escrow storage" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Gambling package scaffolding (rng, escrow helper, active games)

**Files:**
- Create: `src/yokai/gambling/__init__.py`
- Create: `src/yokai/gambling/rng.py`
- Create: `src/yokai/gambling/escrow.py`
- Create: `src/yokai/gambling/sessions.py`
- Test: `tests/test_gambling_escrow.py`

**Interfaces:**
- Consumes: `Database.place_bet/refund_bet` (Task 3); `BetOutOfRangeError`, `AlreadyInGameError`, `BetNotOpenError` (Task 2).
- Produces:
  - `new_rng() -> random.Random` (a `random.SystemRandom`).
  - `validate_bet(amount: int, minimum: int, maximum: int) -> None` (raises `BetOutOfRangeError`).
  - `escrowed_bet(db, user_id, amount, game, welcome_bonus, group_id=None)`: async context manager yielding the bet id; on any exception in its body the bet is refunded (a `BetNotOpenError` from an already-settled bet is ignored) and the exception re-raised.
  - `ActiveGames` with `claim(user_id) -> None` (raises `AlreadyInGameError`), `release(user_id) -> None` (idempotent), `is_active(user_id) -> bool`.

- [ ] **Step 1: Write the failing tests** — create `tests/test_gambling_escrow.py`:

```python
"""Tests for the gambling escrow helpers and the active-games registry."""

from __future__ import annotations

import random
import tempfile
from pathlib import Path

import pytest

from yokai.errors import AlreadyInGameError, BetOutOfRangeError
from yokai.gambling.escrow import escrowed_bet, validate_bet
from yokai.gambling.rng import new_rng
from yokai.gambling.sessions import ActiveGames
from yokai.storage.db import Database

WELCOME = 200


def test_validate_bet_accepts_exactly_min_and_max() -> None:
    validate_bet(10, 10, 5000)
    validate_bet(5000, 10, 5000)


@pytest.mark.parametrize("amount", [9, 5001, 0, -5])
def test_validate_bet_rejects_outside_the_range(amount: int) -> None:
    with pytest.raises(BetOutOfRangeError):
        validate_bet(amount, 10, 5000)


def test_new_rng_is_a_system_random() -> None:
    assert isinstance(new_rng(), random.SystemRandom)


def test_active_games_blocks_a_second_claim_until_released() -> None:
    games = ActiveGames()
    games.claim(1)
    assert games.is_active(1)
    with pytest.raises(AlreadyInGameError):
        games.claim(1)
    games.claim(2)  # other users are independent
    games.release(1)
    games.release(1)  # idempotent
    assert not games.is_active(1)
    games.claim(1)


async def _balance(db: Database, user_id: int) -> int:
    wallet = await db.get_wallet(user_id)
    assert wallet is not None
    return wallet.balance


@pytest.mark.asyncio
async def test_escrowed_bet_stays_settleable_when_the_body_succeeds() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = Database(Path(tmp_dir) / "t.db")
        await db.initialize()
        async with escrowed_bet(db, 1, 50, "slots", WELCOME) as bet_id:
            assert await _balance(db, 1) == 150
            await db.settle_bet(bet_id, 100)
        assert await _balance(db, 1) == 250
        await db.close()


@pytest.mark.asyncio
async def test_escrowed_bet_refunds_when_the_body_raises() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = Database(Path(tmp_dir) / "t.db")
        await db.initialize()
        with pytest.raises(RuntimeError):
            async with escrowed_bet(db, 1, 50, "slots", WELCOME):
                raise RuntimeError("boom")
        assert await _balance(db, 1) == WELCOME
        await db.close()


@pytest.mark.asyncio
async def test_escrowed_bet_does_not_refund_a_bet_that_already_settled() -> None:
    """The body settled, then something else failed: the payout must stand, not be undone."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db = Database(Path(tmp_dir) / "t.db")
        await db.initialize()
        with pytest.raises(RuntimeError):
            async with escrowed_bet(db, 1, 50, "slots", WELCOME) as bet_id:
                await db.settle_bet(bet_id, 100)
                raise RuntimeError("edit failed")
        assert await _balance(db, 1) == 250
        await db.close()
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_gambling_escrow.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'yokai.gambling'`).

- [ ] **Step 3: Implement.**

`src/yokai/gambling/__init__.py`:

```python
"""Renown gambling: escrowed bets, pure game engines, and shared helpers."""
```

`src/yokai/gambling/rng.py`:

```python
"""Randomness source for games: cryptographic in production, seedable in tests."""

from __future__ import annotations

import random


def new_rng() -> random.Random:
    """Return the production RNG. Tests pass their own seeded `random.Random` instead."""
    return random.SystemRandom()
```

`src/yokai/gambling/escrow.py`:

```python
"""Bet validation and the escrow context manager that guarantees a stake is never lost."""

from __future__ import annotations

import contextlib
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, AsyncIterator, Optional

from yokai.errors import BetNotOpenError, BetOutOfRangeError

if TYPE_CHECKING:
    from yokai.storage.db import Database


def validate_bet(amount: int, minimum: int, maximum: int) -> None:
    """Raise BetOutOfRangeError unless `minimum <= amount <= maximum`."""
    if amount < minimum or amount > maximum:
        raise BetOutOfRangeError(minimum, maximum)


@asynccontextmanager
async def escrowed_bet(
    db: Database,
    user_id: int,
    amount: int,
    game: str,
    welcome_bonus: int,
    group_id: Optional[str] = None,
) -> AsyncIterator[int]:
    """Escrow a stake for the body of the block; refund it if the body raises.

    The body is expected to settle the bet itself. A bet it already settled is left alone.
    """
    bet_id = await db.place_bet(user_id, amount, game, welcome_bonus, group_id=group_id)
    try:
        yield bet_id
    except BaseException:
        with contextlib.suppress(BetNotOpenError):
            await db.refund_bet(bet_id)
        raise
```

`src/yokai/gambling/sessions.py`:

```python
"""In-memory registry enforcing one active game per member."""

from __future__ import annotations

from yokai.errors import AlreadyInGameError


class ActiveGames:
    """Tracks which members are mid-game. Deliberately not persisted: a restart clears it."""

    def __init__(self) -> None:
        self._users: set[int] = set()

    def claim(self, user_id: int) -> None:
        if user_id in self._users:
            raise AlreadyInGameError()
        self._users.add(user_id)

    def release(self, user_id: int) -> None:
        self._users.discard(user_id)

    def is_active(self, user_id: int) -> bool:
        return user_id in self._users
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_gambling_escrow.py -q; python -m ruff check .; python -m ruff format --check .`
Expected: pass, clean.

- [ ] **Step 5: Commit**

```bash
git add src/yokai/gambling tests/test_gambling_escrow.py
git commit -m "feat(gambling): add escrow helper, bet validation and active-games registry" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Slots engine

**Files:**
- Create: `src/yokai/gambling/engines/__init__.py`
- Create: `src/yokai/gambling/engines/slots.py`
- Test: `tests/test_slots_engine.py`

**Interfaces:**
- Produces: `SYMBOLS: tuple[str, ...]`, `SlotsResult(reels: tuple[str, str, str], multiplier_tenths: int)` with `.payout(stake: int) -> int` (`stake * multiplier_tenths // 10`, floored), `score(reels) -> int` (multiplier in tenths), `spin(rng: random.Random) -> SlotsResult`, `exact_rtp() -> Fraction`.
- Paytable (tenths of the stake): three of a kind CHERRY 50, LEMON 60, BELL 130, BAR 150, SEVEN 500; exactly two CHERRY anywhere 14. Reel weights CHERRY 40, LEMON 30, BELL 15, BAR 10, SEVEN 5 (out of 100). Exact RTP is 0.950325 (verified with exact fractions while writing this plan). This deviates from spec §5 only in the two-cherry payout (1.4x instead of 1.5x), which the spec's "tuned for RTP ~95%" requirement forces.

- [ ] **Step 1: Write the failing tests** — create `tests/test_slots_engine.py`:

```python
"""Tests for the pure slots engine."""

from __future__ import annotations

import random

import pytest

from yokai.gambling.engines import slots


@pytest.mark.parametrize(
    ("reels", "tenths"),
    [
        (("SEVEN", "SEVEN", "SEVEN"), 500),
        (("BAR", "BAR", "BAR"), 150),
        (("BELL", "BELL", "BELL"), 130),
        (("LEMON", "LEMON", "LEMON"), 60),
        (("CHERRY", "CHERRY", "CHERRY"), 50),  # three cherries is a triple, not "two cherries"
        (("CHERRY", "CHERRY", "LEMON"), 14),
        (("CHERRY", "LEMON", "CHERRY"), 14),
        (("LEMON", "CHERRY", "CHERRY"), 14),
        (("CHERRY", "LEMON", "BELL"), 0),
        (("LEMON", "LEMON", "BELL"), 0),
        (("BAR", "SEVEN", "BELL"), 0),
    ],
)
def test_score_table(reels: tuple[str, str, str], tenths: int) -> None:
    assert slots.score(reels) == tenths


def test_payout_floors_and_never_over_pays() -> None:
    """Review Focus: odd stakes round down."""
    result = slots.SlotsResult(("CHERRY", "CHERRY", "LEMON"), 14)
    assert result.payout(10) == 14
    assert result.payout(15) == 21  # 15 * 1.4 = 21 exactly
    assert result.payout(11) == 15  # 15.4 floors to 15
    assert slots.SlotsResult(("BAR", "LEMON", "BELL"), 0).payout(100) == 0


def test_exact_rtp_is_about_ninety_five_percent() -> None:
    rtp = float(slots.exact_rtp())
    assert 0.94 <= rtp <= 0.96
    assert rtp == pytest.approx(0.950325)


def test_spin_is_deterministic_for_a_seed_and_uses_known_symbols() -> None:
    first = slots.spin(random.Random(1))
    second = slots.spin(random.Random(1))
    assert first == second
    assert all(symbol in slots.SYMBOLS for symbol in first.reels)
    assert first.multiplier_tenths == slots.score(first.reels)


def test_simulated_return_is_near_the_exact_rtp() -> None:
    rng = random.Random(12345)
    spins = 20000
    returned = sum(slots.spin(rng).multiplier_tenths for _ in range(spins)) / 10
    assert 0.85 <= returned / spins <= 1.05
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_slots_engine.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'yokai.gambling.engines'`).

- [ ] **Step 3: Implement.**

`src/yokai/gambling/engines/__init__.py`:

```python
"""Pure game engines: no Discord imports, randomness injected for testability."""
```

`src/yokai/gambling/engines/slots.py`:

```python
"""Three-reel slots with a weighted symbol table and a fixed paytable (about 95% RTP)."""

from __future__ import annotations

import random
from dataclasses import dataclass
from fractions import Fraction
from itertools import product

SYMBOLS: tuple[str, ...] = ("CHERRY", "LEMON", "BELL", "BAR", "SEVEN")
WEIGHTS: dict[str, int] = {"CHERRY": 40, "LEMON": 30, "BELL": 15, "BAR": 10, "SEVEN": 5}

# Payouts are in tenths of the stake so they stay integers (14 = 1.4x).
TRIPLE_TENTHS: dict[str, int] = {
    "CHERRY": 50,
    "LEMON": 60,
    "BELL": 130,
    "BAR": 150,
    "SEVEN": 500,
}
TWO_CHERRY_TENTHS = 14


@dataclass(frozen=True, slots=True)
class SlotsResult:
    reels: tuple[str, str, str]
    multiplier_tenths: int

    def payout(self, stake: int) -> int:
        """Total returned for `stake` (stake included), floored to a whole Renown."""
        return stake * self.multiplier_tenths // 10


def score(reels: tuple[str, str, str]) -> int:
    """Payout multiplier in tenths of the stake for a set of reels."""
    if reels[0] == reels[1] == reels[2]:
        return TRIPLE_TENTHS[reels[0]]
    if reels.count("CHERRY") == 2:
        return TWO_CHERRY_TENTHS
    return 0


def spin(rng: random.Random) -> SlotsResult:
    """Spin three independent reels."""
    drawn = rng.choices(SYMBOLS, weights=[WEIGHTS[s] for s in SYMBOLS], k=3)
    reels = (drawn[0], drawn[1], drawn[2])
    return SlotsResult(reels=reels, multiplier_tenths=score(reels))


def exact_rtp() -> Fraction:
    """Exact expected return per unit staked, computed by enumerating every reel outcome."""
    total_weight = sum(WEIGHTS.values())
    expected = Fraction(0)
    for combo in product(SYMBOLS, repeat=3):
        probability = Fraction(1)
        for symbol in combo:
            probability *= Fraction(WEIGHTS[symbol], total_weight)
        expected += probability * score(combo)
    return expected / 10
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_slots_engine.py -q; python -m ruff check .; python -m ruff format --check .`
Expected: pass, clean.

- [ ] **Step 5: Commit**

```bash
git add src/yokai/gambling/engines tests/test_slots_engine.py
git commit -m "feat(gambling): add the slots engine" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Roulette engine

**Files:**
- Create: `src/yokai/gambling/engines/roulette.py`
- Test: `tests/test_roulette_engine.py`

**Interfaces:**
- Consumes: `InvalidGameChoiceError` (Task 2).
- Produces: `BetKind` (str Enum: `red black odd even low high dozen column straight`), `RouletteBet(kind: BetKind, pick: int | None = None)` with `.label -> str`, `parse_bet(choice: str, pick: int | None) -> RouletteBet`, `colour(pocket: int) -> str` (`"green" | "red" | "black"`), `spin(rng) -> int` (0..36), `return_multiplier(bet, pocket) -> int` (total return as a multiple of the stake: 0, 2, 3, or 36), `expected_return(bet) -> Fraction`.

- [ ] **Step 1: Write the failing tests** — create `tests/test_roulette_engine.py`:

```python
"""Tests for the pure roulette engine (single-zero European wheel)."""

from __future__ import annotations

import random
from fractions import Fraction

import pytest

from yokai.errors import InvalidGameChoiceError
from yokai.gambling.engines import roulette
from yokai.gambling.engines.roulette import BetKind, RouletteBet


def test_wheel_has_eighteen_red_eighteen_black_and_one_green() -> None:
    colours = [roulette.colour(p) for p in range(37)]
    assert colours.count("red") == 18
    assert colours.count("black") == 18
    assert colours.count("green") == 1
    assert roulette.colour(0) == "green"
    assert roulette.colour(1) == "red"
    assert roulette.colour(2) == "black"


@pytest.mark.parametrize(
    ("bet", "pocket", "expected"),
    [
        (RouletteBet(BetKind.RED), 1, 2),
        (RouletteBet(BetKind.RED), 2, 0),
        (RouletteBet(BetKind.BLACK), 2, 2),
        (RouletteBet(BetKind.ODD), 17, 2),
        (RouletteBet(BetKind.EVEN), 18, 2),
        (RouletteBet(BetKind.LOW), 18, 2),
        (RouletteBet(BetKind.LOW), 19, 0),
        (RouletteBet(BetKind.HIGH), 19, 2),
        (RouletteBet(BetKind.DOZEN, 2), 13, 3),
        (RouletteBet(BetKind.DOZEN, 2), 24, 3),
        (RouletteBet(BetKind.DOZEN, 2), 25, 0),
        (RouletteBet(BetKind.COLUMN, 1), 34, 3),
        (RouletteBet(BetKind.COLUMN, 3), 36, 3),
        (RouletteBet(BetKind.COLUMN, 3), 35, 0),
        (RouletteBet(BetKind.STRAIGHT, 17), 17, 36),
        (RouletteBet(BetKind.STRAIGHT, 17), 18, 0),
        (RouletteBet(BetKind.STRAIGHT, 0), 0, 36),
    ],
)
def test_return_multiplier(bet: RouletteBet, pocket: int, expected: int) -> None:
    assert roulette.return_multiplier(bet, pocket) == expected


@pytest.mark.parametrize(
    "bet",
    [RouletteBet(k) for k in (BetKind.RED, BetKind.BLACK, BetKind.ODD, BetKind.EVEN)]
    + [RouletteBet(BetKind.LOW), RouletteBet(BetKind.HIGH)]
    + [RouletteBet(BetKind.DOZEN, n) for n in (1, 2, 3)]
    + [RouletteBet(BetKind.COLUMN, n) for n in (1, 2, 3)]
    + [RouletteBet(BetKind.STRAIGHT, n) for n in (0, 1, 17, 36)],
)
def test_every_bet_type_has_the_same_two_point_seven_percent_edge(bet: RouletteBet) -> None:
    assert roulette.expected_return(bet) == Fraction(36, 37)


def test_zero_loses_every_outside_bet() -> None:
    for kind in (BetKind.RED, BetKind.BLACK, BetKind.ODD, BetKind.EVEN, BetKind.LOW, BetKind.HIGH):
        assert roulette.return_multiplier(RouletteBet(kind), 0) == 0


def test_parse_bet_accepts_valid_choices() -> None:
    assert roulette.parse_bet("red", None) == RouletteBet(BetKind.RED)
    assert roulette.parse_bet("dozen", 3) == RouletteBet(BetKind.DOZEN, 3)
    assert roulette.parse_bet("straight", 0) == RouletteBet(BetKind.STRAIGHT, 0)


@pytest.mark.parametrize(
    ("choice", "pick"),
    [
        ("purple", None),  # unknown type
        ("red", 5),  # outside bets take no pick
        ("dozen", None),  # missing pick
        ("dozen", 4),
        ("column", 0),
        ("straight", 37),
        ("straight", -1),
        ("straight", None),
    ],
)
def test_parse_bet_rejects_invalid_choices(choice: str, pick: int | None) -> None:
    with pytest.raises(InvalidGameChoiceError):
        roulette.parse_bet(choice, pick)


def test_label_includes_the_pick() -> None:
    assert RouletteBet(BetKind.RED).label == "red"
    assert RouletteBet(BetKind.STRAIGHT, 17).label == "straight 17"


def test_spin_is_in_range_and_deterministic_for_a_seed() -> None:
    assert roulette.spin(random.Random(7)) == roulette.spin(random.Random(7))
    pockets = {roulette.spin(random.Random(seed)) for seed in range(300)}
    assert pockets <= set(range(37))
    assert len(pockets) > 20
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_roulette_engine.py -q`
Expected: FAIL (`ModuleNotFoundError ... roulette`).

- [ ] **Step 3: Implement** `src/yokai/gambling/engines/roulette.py`:

```python
"""European single-zero roulette: one bet per spin, 2.7% house edge on every bet type."""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum
from fractions import Fraction
from typing import Optional

from yokai.errors import InvalidGameChoiceError

POCKETS = 37
RED_POCKETS: frozenset[int] = frozenset(
    {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}
)


class BetKind(str, Enum):
    RED = "red"
    BLACK = "black"
    ODD = "odd"
    EVEN = "even"
    LOW = "low"
    HIGH = "high"
    DOZEN = "dozen"
    COLUMN = "column"
    STRAIGHT = "straight"


# Bet kinds that need a pick, with the inclusive range it must fall in.
_PICK_BOUNDS: dict[BetKind, tuple[int, int]] = {
    BetKind.DOZEN: (1, 3),
    BetKind.COLUMN: (1, 3),
    BetKind.STRAIGHT: (0, 36),
}


@dataclass(frozen=True, slots=True)
class RouletteBet:
    kind: BetKind
    pick: Optional[int] = None

    @property
    def label(self) -> str:
        return self.kind.value if self.pick is None else f"{self.kind.value} {self.pick}"


def parse_bet(choice: str, pick: Optional[int]) -> RouletteBet:
    """Validate a bet type and pick from user input; raises InvalidGameChoiceError."""
    try:
        kind = BetKind(choice)
    except ValueError:
        raise InvalidGameChoiceError("That isn't a roulette bet type.") from None
    bounds = _PICK_BOUNDS.get(kind)
    if bounds is None:
        if pick is not None:
            raise InvalidGameChoiceError(f"A {kind.value} bet doesn't take a pick.")
        return RouletteBet(kind)
    low, high = bounds
    if pick is None or not low <= pick <= high:
        raise InvalidGameChoiceError(f"A {kind.value} bet needs a pick from {low} to {high}.")
    return RouletteBet(kind, pick)


def colour(pocket: int) -> str:
    if pocket == 0:
        return "green"
    return "red" if pocket in RED_POCKETS else "black"


def spin(rng: random.Random) -> int:
    return rng.randrange(POCKETS)


def return_multiplier(bet: RouletteBet, pocket: int) -> int:
    """Total returned as a multiple of the stake (0 = lost, 2 = even money, 36 = straight)."""
    kind = bet.kind
    if kind is BetKind.STRAIGHT:
        return 36 if pocket == bet.pick else 0
    if pocket == 0:
        return 0
    if kind is BetKind.RED:
        return 2 if pocket in RED_POCKETS else 0
    if kind is BetKind.BLACK:
        return 2 if pocket not in RED_POCKETS else 0
    if kind is BetKind.ODD:
        return 2 if pocket % 2 == 1 else 0
    if kind is BetKind.EVEN:
        return 2 if pocket % 2 == 0 else 0
    if kind is BetKind.LOW:
        return 2 if pocket <= 18 else 0
    if kind is BetKind.HIGH:
        return 2 if pocket >= 19 else 0
    if kind is BetKind.DOZEN:
        return 3 if (pocket - 1) // 12 + 1 == bet.pick else 0
    # Column 1 holds 1,4,7...; column 3 holds 3,6,9... (pocket % 3 == 0).
    assert bet.pick is not None
    return 3 if pocket % 3 == bet.pick % 3 else 0


def expected_return(bet: RouletteBet) -> Fraction:
    """Exact expected return per unit staked over all 37 pockets."""
    return Fraction(sum(return_multiplier(bet, p) for p in range(POCKETS)), POCKETS)
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_roulette_engine.py -q; python -m ruff check .; python -m ruff format --check .`
Expected: pass, clean.

- [ ] **Step 5: Commit**

```bash
git add src/yokai/gambling/engines/roulette.py tests/test_roulette_engine.py
git commit -m "feat(gambling): add the roulette engine" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Blackjack engine

**Files:**
- Create: `src/yokai/gambling/engines/blackjack.py`
- Create: `tests/card_helpers.py`
- Test: `tests/test_blackjack_engine.py`

**Interfaces:**
- Produces: `Card(rank: int, suit: str)` (rank 1=Ace..13=King; `str()` is poker notation like `As`, `10d`), `hand_value(cards) -> tuple[int, bool]` (total, is_soft), `is_blackjack(cards) -> bool`, `build_shoe(rng, decks=6) -> list[Card]`, `Outcome` (str Enum: `blackjack win push lose`), `payout_for(outcome, stake) -> int` (total returned: BLACKJACK `stake + stake*3//2`, WIN `2*stake`, PUSH `stake`, LOSE `0`), and `BlackjackGame(shoe: Sequence[Card])` with attributes `player: list[Card]`, `dealer: list[Card]`, `finished: bool`, `outcome: Outcome | None`, `doubled: bool`, `player_busted: bool`, property `can_double`, methods `hit()`, `stand()`, `double()` (the last three raise `ValueError` when not allowed).
- Deal order from the shoe: player, dealer, player, dealer. If either side has a natural the hand is finished at construction (dealer peeks). Dealer stands on all 17s. Player auto-stands on 21.
- `tests/card_helpers.py` produces `cards("As Kd 10h") -> list[Card]` for stacking a shoe in tests.

- [ ] **Step 1: Write the failing tests.** Create `tests/card_helpers.py`:

```python
"""Helpers for building stacked shoes in blackjack tests."""

from __future__ import annotations

from yokai.gambling.engines.blackjack import Card

_FACE_RANKS = {"A": 1, "J": 11, "Q": 12, "K": 13}


def cards(text: str) -> list[Card]:
    """Parse poker notation such as ``"As Kd 10h"`` into cards, in order."""
    parsed: list[Card] = []
    for token in text.split():
        rank_text, suit = token[:-1], token[-1]
        rank = _FACE_RANKS.get(rank_text) or int(rank_text)
        parsed.append(Card(rank=rank, suit=suit))
    return parsed
```

Create `tests/test_blackjack_engine.py`:

```python
"""Tests for the pure blackjack engine."""

from __future__ import annotations

import random

import pytest

from tests.card_helpers import cards
from yokai.gambling.engines.blackjack import (
    BlackjackGame,
    Card,
    Outcome,
    build_shoe,
    hand_value,
    is_blackjack,
    payout_for,
)

# Shoe order: player1, dealer1, player2, dealer2, then draws.


def test_card_str_is_poker_notation_without_suit_glyphs() -> None:
    assert str(Card(1, "s")) == "As"
    assert str(Card(10, "d")) == "10d"
    assert str(Card(13, "h")) == "Kh"


def test_hand_value_handles_soft_and_hard_aces() -> None:
    assert hand_value(cards("As Ad")) == (12, True)
    assert hand_value(cards("As Ad 9c")) == (21, True)
    assert hand_value(cards("As Kd 5c")) == (16, False)
    assert hand_value(cards("As Kd")) == (21, True)
    assert hand_value(cards("10h 9d 5c")) == (24, False)


def test_is_blackjack_needs_exactly_two_cards() -> None:
    assert is_blackjack(cards("As Kd"))
    assert not is_blackjack(cards("7h 7d 7c"))


def test_payout_for_floors_the_three_to_two_bonus() -> None:
    """Review Focus: odd stakes never over-pay."""
    assert payout_for(Outcome.BLACKJACK, 10) == 25
    assert payout_for(Outcome.BLACKJACK, 15) == 37
    assert payout_for(Outcome.WIN, 10) == 20
    assert payout_for(Outcome.PUSH, 10) == 10
    assert payout_for(Outcome.LOSE, 10) == 0


def test_player_natural_against_a_non_natural_dealer_pays_blackjack() -> None:
    game = BlackjackGame(cards("As Kd Ks 9h"))
    assert game.finished and game.outcome is Outcome.BLACKJACK


def test_both_naturals_push() -> None:
    game = BlackjackGame(cards("As Ad Kc Kh"))
    assert game.finished and game.outcome is Outcome.PUSH


def test_dealer_natural_ends_the_hand_immediately() -> None:
    game = BlackjackGame(cards("10h As 9c Kd"))
    assert game.finished and game.outcome is Outcome.LOSE
    assert not game.player_busted


def test_stand_makes_the_dealer_draw_to_seventeen() -> None:
    game = BlackjackGame(cards("10h 6d 9c 5s 7c"))
    assert not game.finished
    game.stand()
    assert [str(c) for c in game.dealer] == ["6d", "5s", "7c"]
    assert game.outcome is Outcome.WIN  # 19 against 18


def test_dealer_stands_on_soft_seventeen() -> None:
    game = BlackjackGame(cards("10h As 9c 6d 5c"))
    game.stand()
    assert len(game.dealer) == 2
    assert game.outcome is Outcome.WIN  # 19 against soft 17


def test_dealer_bust_is_a_win() -> None:
    game = BlackjackGame(cards("10h 6d 8c 10s 9c"))
    game.stand()
    assert hand_value(game.dealer)[0] == 25
    assert game.outcome is Outcome.WIN


def test_equal_totals_push_and_a_lower_total_loses() -> None:
    push = BlackjackGame(cards("10h 10d 8c 8s"))
    push.stand()
    assert push.outcome is Outcome.PUSH
    lose = BlackjackGame(cards("10h 10d 7c 9s"))
    lose.stand()
    assert lose.outcome is Outcome.LOSE


def test_hit_bust_loses_without_the_dealer_drawing() -> None:
    game = BlackjackGame(cards("10h 9d 6c 8s 10c"))
    game.hit()
    assert game.finished and game.player_busted and game.outcome is Outcome.LOSE
    assert len(game.dealer) == 2


def test_hitting_to_twenty_one_stands_automatically() -> None:
    game = BlackjackGame(cards("5h 9d 6c 8s 10c"))
    game.hit()
    assert game.finished and game.outcome is Outcome.WIN  # 21 against 17


def test_double_draws_exactly_one_card_then_the_dealer_plays() -> None:
    game = BlackjackGame(cards("5h 9d 6c 8s 10c"))
    assert game.can_double
    game.double()
    assert game.doubled and game.finished
    assert len(game.player) == 3
    assert game.outcome is Outcome.WIN


def test_double_bust_loses() -> None:
    game = BlackjackGame(cards("10h 9d 6c 8s 10c"))
    game.double()
    assert game.player_busted and game.outcome is Outcome.LOSE


def test_double_is_not_allowed_after_a_hit() -> None:
    game = BlackjackGame(cards("2h 9d 3c 8s 2c 2d"))
    game.hit()
    assert not game.can_double
    with pytest.raises(ValueError):
        game.double()


def test_actions_after_the_hand_is_finished_raise() -> None:
    game = BlackjackGame(cards("As Kd Ks 9h"))
    for action in (game.hit, game.stand):
        with pytest.raises(ValueError):
            action()


def test_shoe_has_six_decks_and_is_deterministic_for_a_seed() -> None:
    shoe = build_shoe(random.Random(3))
    assert len(shoe) == 312
    assert sum(1 for c in shoe if c == Card(1, "s")) == 6
    assert build_shoe(random.Random(3)) == build_shoe(random.Random(3))
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_blackjack_engine.py -q`
Expected: FAIL (`ModuleNotFoundError ... blackjack`).

- [ ] **Step 3: Implement** `src/yokai/gambling/engines/blackjack.py`:

```python
"""Blackjack: 6-deck shoe, 3:2 naturals, dealer stands on all 17s, no splits or insurance."""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Sequence

DECKS = 6
SUITS = "cdhs"
_RANK_LABELS = {1: "A", 11: "J", 12: "Q", 13: "K"}


@dataclass(frozen=True, slots=True)
class Card:
    rank: int  # 1 = Ace ... 13 = King
    suit: str  # one of "cdhs"

    def __str__(self) -> str:
        return f"{_RANK_LABELS.get(self.rank, str(self.rank))}{self.suit}"


def hand_value(cards: Sequence[Card]) -> tuple[int, bool]:
    """Best total for a hand and whether an ace is currently counted as 11 (soft)."""
    total = sum(min(card.rank, 10) for card in cards)
    if any(card.rank == 1 for card in cards) and total + 10 <= 21:
        return total + 10, True
    return total, False


def is_blackjack(cards: Sequence[Card]) -> bool:
    return len(cards) == 2 and hand_value(cards)[0] == 21


def build_shoe(rng: random.Random, decks: int = DECKS) -> list[Card]:
    shoe = [Card(rank, suit) for _ in range(decks) for suit in SUITS for rank in range(1, 14)]
    rng.shuffle(shoe)
    return shoe


class Outcome(str, Enum):
    BLACKJACK = "blackjack"
    WIN = "win"
    PUSH = "push"
    LOSE = "lose"


def payout_for(outcome: Outcome, stake: int) -> int:
    """Total returned for `stake` (stake included), floored to a whole Renown."""
    if outcome is Outcome.BLACKJACK:
        return stake + stake * 3 // 2
    if outcome is Outcome.WIN:
        return stake * 2
    if outcome is Outcome.PUSH:
        return stake
    return 0


class BlackjackGame:
    """One hand against the dealer. Draw order from `shoe`: P, D, P, D, then play."""

    def __init__(self, shoe: Sequence[Card]) -> None:
        self._shoe = list(shoe)
        self._next = 0
        self.player: list[Card] = []
        self.dealer: list[Card] = []
        self.finished = False
        self.outcome: Optional[Outcome] = None
        self.doubled = False
        self.player_busted = False

        self.player.append(self._draw())
        self.dealer.append(self._draw())
        self.player.append(self._draw())
        self.dealer.append(self._draw())
        if is_blackjack(self.player) or is_blackjack(self.dealer):
            self._compare()

    def _draw(self) -> Card:
        card = self._shoe[self._next]
        self._next += 1
        return card

    @property
    def can_double(self) -> bool:
        return not self.finished and len(self.player) == 2

    def _require_active(self) -> None:
        if self.finished:
            raise ValueError("The hand is already finished.")

    def _bust(self) -> None:
        self.player_busted = True
        self.outcome = Outcome.LOSE
        self.finished = True

    def hit(self) -> None:
        self._require_active()
        self.player.append(self._draw())
        total = hand_value(self.player)[0]
        if total > 21:
            self._bust()
        elif total == 21:
            self.stand()

    def stand(self) -> None:
        self._require_active()
        while hand_value(self.dealer)[0] < 17:
            self.dealer.append(self._draw())
        self._compare()

    def double(self) -> None:
        if not self.can_double:
            raise ValueError("Double is only allowed on the first two cards.")
        self.doubled = True
        self.player.append(self._draw())
        if hand_value(self.player)[0] > 21:
            self._bust()
        else:
            self.stand()

    def _compare(self) -> None:
        player_total = hand_value(self.player)[0]
        dealer_total = hand_value(self.dealer)[0]
        player_natural = is_blackjack(self.player)
        dealer_natural = is_blackjack(self.dealer)
        if player_natural and dealer_natural:
            self.outcome = Outcome.PUSH
        elif player_natural:
            self.outcome = Outcome.BLACKJACK
        elif dealer_natural:
            self.outcome = Outcome.LOSE
        elif dealer_total > 21 or player_total > dealer_total:
            self.outcome = Outcome.WIN
        elif player_total == dealer_total:
            self.outcome = Outcome.PUSH
        else:
            self.outcome = Outcome.LOSE
        self.finished = True
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_blackjack_engine.py -q; python -m ruff check .; python -m ruff format --check .`
Expected: pass, clean.

- [ ] **Step 5: Commit**

```bash
git add src/yokai/gambling/engines/blackjack.py tests/card_helpers.py tests/test_blackjack_engine.py
git commit -m "feat(gambling): add the blackjack engine" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Theme additions, game embeds, preview samples

**Files:**
- Modify: `src/yokai/theme.py`
- Modify: `src/yokai/ui/embeds.py`
- Modify: `scripts/preview_embeds.py`
- Test: `tests/test_gambling_embeds.py`

**Interfaces:**
- Produces in `theme.py`: `Emoji.SLOTS` (`🎰`), `Emoji.DICE` (`🎲`); quip keys `game_win`, `game_loss`, `game_push`, `game_blackjack`, `game_bust` (4 variants each).
- Produces on `EmbedFactory` (all return `discord.Embed`, all `bot_avatar_url: Optional[str] = None` last):
  - `spinning(title: str, description: str, icon: str, bot_avatar_url=None)`
  - `slots_result(user_mention: str, reels: Sequence[str], stake: int, payout: int, new_balance: int, bot_avatar_url=None)`
  - `roulette_result(user_mention: str, bet_label: str, pocket: int, pocket_colour: str, stake: int, payout: int, new_balance: int, bot_avatar_url=None)`
  - `blackjack_hand(user_mention: str, player_cards: Sequence[str], player_total: int, dealer_cards: Sequence[str], dealer_total: Optional[int], stake: int, verdict: Optional[str] = None, payout: Optional[int] = None, new_balance: Optional[int] = None, bot_avatar_url=None)`, where `verdict` is `None` while playing, else one of `blackjack|win|push|lose|bust`.
  - `gamble_stats(user_mention: str, rows: Sequence[tuple[str, int, int, int]], bot_avatar_url=None)`
- Design notes: reels render as `[CHERRY] [BELL] [SEVEN]` and cards as `As Kd 10h` (no emoji and no suit glyphs, which the one-emoji test counts as emoji). One emoji per embed (the title's). Net is signed (`+490`, `-10`, `0`).

- [ ] **Step 1: Write the failing tests** — create `tests/test_gambling_embeds.py`:

```python
"""Tests for the gambling theme additions and embed builders."""

from __future__ import annotations

import discord

from tests.embed_helpers import all_embed_text, emoji_count, total_embed_length
from yokai.theme import QUIP_VARIANTS, Emoji
from yokai.ui.embeds import EmbedFactory

SUIT_GLYPHS = "♠♥♦♣♤♡♢♧"


def _all_samples() -> list[discord.Embed]:
    return [
        EmbedFactory.spinning("Slots", "`[ ? ] [ ? ] [ ? ]`", Emoji.SLOTS),
        EmbedFactory.slots_result("<@1>", ("SEVEN", "SEVEN", "SEVEN"), 10, 500, 690),
        EmbedFactory.slots_result("<@1>", ("BAR", "LEMON", "BELL"), 10, 0, 190),
        EmbedFactory.slots_result("<@1>", ("CHERRY", "CHERRY", "LEMON"), 10, 14, 204),
        EmbedFactory.roulette_result("<@1>", "straight 17", 17, "black", 10, 360, 550),
        EmbedFactory.roulette_result("<@1>", "red", 0, "green", 10, 0, 190),
        EmbedFactory.blackjack_hand("<@1>", ["10h", "6c"], 16, ["9d", "??"], None, 10),
        EmbedFactory.blackjack_hand(
            "<@1>", ["As", "Kd"], 21, ["Ks", "9h"], 19, 10, "blackjack", 25, 215
        ),
        EmbedFactory.blackjack_hand(
            "<@1>", ["10h", "6c", "10d"], 26, ["9d", "8s"], 17, 10, "bust", 0, 190
        ),
        EmbedFactory.blackjack_hand(
            "<@1>", ["10h", "8c"], 18, ["10d", "8s"], 18, 10, "push", 10, 200
        ),
        EmbedFactory.gamble_stats("<@1>", [("blackjack", 1, 10, 20), ("slots", 2, 20, 40)]),
        EmbedFactory.gamble_stats("<@1>", []),
    ]


def test_new_emoji_and_quip_keys_exist() -> None:
    assert Emoji.SLOTS and Emoji.DICE
    for key in ("game_win", "game_loss", "game_push", "game_blackjack", "game_bust"):
        assert len(QUIP_VARIANTS[key]) >= 3


def test_every_gambling_embed_obeys_the_layout_rules() -> None:
    for embed in _all_samples():
        assert emoji_count(embed) <= 1, embed.title
        assert total_embed_length(embed) <= 6000
        assert not any(glyph in all_embed_text(embed) for glyph in SUIT_GLYPHS)


def test_slots_win_shows_text_reels_and_a_signed_net() -> None:
    embed = EmbedFactory.slots_result("<@1>", ("SEVEN", "SEVEN", "SEVEN"), 10, 500, 690)
    assert "[SEVEN] [SEVEN] [SEVEN]" in (embed.description or "")
    text = all_embed_text(embed)
    assert "+490" in text and "690" in text


def test_slots_loss_and_push_net_text() -> None:
    loss = all_embed_text(EmbedFactory.slots_result("<@1>", ("BAR", "LEMON", "BELL"), 10, 0, 190))
    assert "-10" in loss
    push = EmbedFactory.slots_result("<@1>", ("BAR", "LEMON", "BELL"), 10, 10, 200)
    assert any(f.name == "Net" and f.value == "**0**" for f in push.fields)


def test_blackjack_in_play_shows_the_hidden_dealer_card_and_no_result() -> None:
    embed = EmbedFactory.blackjack_hand("<@1>", ["10h", "6c"], 16, ["9d", "??"], None, 10)
    assert "`9d ??`" in (embed.description or "")
    assert "10h 6c" in (embed.description or "")
    assert not any(f.name == "Net" for f in embed.fields)


def test_blackjack_finished_shows_verdict_net_and_balance() -> None:
    embed = EmbedFactory.blackjack_hand(
        "<@1>", ["As", "Kd"], 21, ["Ks", "9h"], 19, 10, "blackjack", 25, 215
    )
    assert "Blackjack!" in (embed.description or "")
    text = all_embed_text(embed)
    assert "+15" in text and "215" in text


def test_roulette_result_names_the_pocket_and_colour() -> None:
    embed = EmbedFactory.roulette_result("<@1>", "straight 17", 17, "black", 10, 360, 550)
    description = embed.description or ""
    assert "17" in description and "black" in description and "straight 17" in description


def test_gamble_stats_lists_games_with_net_or_says_none_yet() -> None:
    filled = EmbedFactory.gamble_stats("<@1>", [("slots", 2, 20, 40)])
    assert "`slots`" in (filled.description or "") and "+20" in all_embed_text(filled)
    empty = EmbedFactory.gamble_stats("<@1>", [])
    assert "No settled games yet" in (empty.description or "")
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_gambling_embeds.py -q`
Expected: FAIL (`AttributeError: type object 'Emoji' has no attribute 'SLOTS'`).

- [ ] **Step 3: Implement.**

In `src/yokai/theme.py`, add to `class Emoji` after `COIN`:

```python
    SLOTS: Final[str] = "🎰"
    DICE: Final[str] = "🎲"
```

and add to `QUIP_VARIANTS`, after the `pay_sent` entry:

```python
    "game_win": (
        "Paid out. Don't spend it all in one place.",
        "The house flinches.",
        "Clean win.",
        "Lucky. Or skilled. Probably lucky.",
    ),
    "game_loss": (
        "The house thanks you.",
        "Not this round.",
        "Better odds next time.",
        "That one's on the house, technically.",
    ),
    "game_push": (
        "Dead even.",
        "Stake returned. Nobody wins.",
        "A wash.",
        "Nothing gained, nothing lost.",
    ),
    "game_blackjack": (
        "Twenty-one on the deal. Smooth.",
        "Natural. Textbook.",
        "Blackjack. Three to two.",
        "Straight to the top.",
    ),
    "game_bust": (
        "Over the line.",
        "Too greedy.",
        "Bust. It happens.",
        "One card too many.",
    ),
```

In `src/yokai/ui/embeds.py`, add these module-level helpers directly after the `_list_line` function:

```python
def _signed(amount: int) -> str:
    """Signed whole number with thousands separators: +490, -10, 0."""
    if amount > 0:
        return f"+{amount:,}"
    return f"{amount:,}" if amount < 0 else "0"


def _result_style(stake: int, payout: int) -> tuple[int, str]:
    """Accent colour and quip key for a settled game, from stake versus total payout."""
    if payout > stake:
        return COLOR_SUCCESS, "game_win"
    if payout == stake:
        return COLOR_BRAND, "game_push"
    return COLOR_WARNING, "game_loss"


_BLACKJACK_VERDICTS = {
    "blackjack": "Blackjack!",
    "win": "You win",
    "push": "Push",
    "lose": "Dealer wins",
    "bust": "Bust",
}
```

and add these classmethods to `EmbedFactory`, immediately after `economy_admin` (before the module-level `_render_progress_bar`):

```python
    @classmethod
    def spinning(
        cls,
        title: str,
        description: str,
        icon: str,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        return cls._create_base(
            title=f"{icon} {title}",
            description=description,
            color=COLOR_BRAND,
            context="Gambling",
            bot_avatar_url=bot_avatar_url,
        )

    @classmethod
    def slots_result(
        cls,
        user_mention: str,
        reels: Sequence[str],
        stake: int,
        payout: int,
        new_balance: int,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        color, quip_key = _result_style(stake, payout)
        reel_text = " ".join(f"[{name}]" for name in reels)
        fields: list[FieldSpec] = [
            ("Stake", f"**{stake:,}**", True),
            ("Net", f"**{_signed(payout - stake)}**", True),
            ("Balance", f"**{new_balance:,}**", True),
        ]
        return cls._create_base(
            title=f"{Emoji.SLOTS} Slots",
            description=f"{user_mention}\n`{reel_text}`",
            color=color,
            context="Gambling",
            bot_avatar_url=bot_avatar_url,
            quip=quip_bank.get(quip_key),
            fields=_grid(fields),
        )

    @classmethod
    def roulette_result(
        cls,
        user_mention: str,
        bet_label: str,
        pocket: int,
        pocket_colour: str,
        stake: int,
        payout: int,
        new_balance: int,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        color, quip_key = _result_style(stake, payout)
        fields: list[FieldSpec] = [
            ("Stake", f"**{stake:,}**", True),
            ("Net", f"**{_signed(payout - stake)}**", True),
            ("Balance", f"**{new_balance:,}**", True),
        ]
        return cls._create_base(
            title=f"{Emoji.DICE} Roulette",
            description=(
                f"{user_mention}\nBet: **{escape_md(bet_label)}**\n"
                f"The ball landed on **{pocket}** ({pocket_colour})."
            ),
            color=color,
            context="Gambling",
            bot_avatar_url=bot_avatar_url,
            quip=quip_bank.get(quip_key),
            fields=_grid(fields),
        )

    @classmethod
    def blackjack_hand(
        cls,
        user_mention: str,
        player_cards: Sequence[str],
        player_total: int,
        dealer_cards: Sequence[str],
        dealer_total: Optional[int],
        stake: int,
        verdict: Optional[str] = None,
        payout: Optional[int] = None,
        new_balance: Optional[int] = None,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        dealer_suffix = f" ({dealer_total})" if dealer_total is not None else ""
        lines = [
            user_mention,
            f"**Your hand** `{' '.join(player_cards)}` ({player_total})",
            f"**Dealer** `{' '.join(dealer_cards)}`{dealer_suffix}",
        ]
        fields: list[FieldSpec] = [("Stake", f"**{stake:,}**", True)]
        color = COLOR_BRAND
        quip: Optional[str] = None
        if verdict is not None:
            returned = payout if payout is not None else 0
            color, quip_key = _result_style(stake, returned)
            if verdict == "blackjack":
                quip_key = "game_blackjack"
            elif verdict == "bust":
                quip_key = "game_bust"
            quip = quip_bank.get(quip_key)
            lines.append(f"**{_BLACKJACK_VERDICTS[verdict]}**")
            fields.append(("Net", f"**{_signed(returned - stake)}**", True))
            if new_balance is not None:
                fields.append(("Balance", f"**{new_balance:,}**", True))
        return cls._create_base(
            title=f"{Emoji.DICE} Blackjack",
            description="\n".join(lines),
            color=color,
            context="Gambling",
            bot_avatar_url=bot_avatar_url,
            quip=quip,
            fields=_grid(fields),
        )

    @classmethod
    def gamble_stats(
        cls,
        user_mention: str,
        rows: Sequence[tuple[str, int, int, int]],  # (game, plays, wagered, returned)
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        lines = [
            f"`{game}` — **{plays:,}** plays · wagered **{wagered:,}** · "
            f"net **{_signed(returned - wagered)}**"
            for game, plays, wagered, returned in rows
        ]
        body = "\n".join(lines) if lines else "No settled games yet."
        total_net = sum(returned - wagered for _, _, wagered, returned in rows)
        fields: list[FieldSpec] = [("Total net", f"**{_signed(total_net)}**", True)] if rows else []
        return cls._create_base(
            title=f"{Emoji.DICE} Gambling record",
            description=f"{user_mention}\n{body}",
            color=COLOR_BRAND,
            context="Gambling",
            bot_avatar_url=bot_avatar_url,
            fields=_grid(fields),
        )
```

In `scripts/preview_embeds.py`, replace the end of `build_samples`:

```python
        (
            "Economy admin (deduction)",
            EmbedFactory.economy_admin(
                target_mention="<@7>", delta_applied=-200, new_balance=0, bot_avatar_url=av
            ),
        ),
    ]
    return samples
```

with:

```python
        (
            "Economy admin (deduction)",
            EmbedFactory.economy_admin(
                target_mention="<@7>", delta_applied=-200, new_balance=0, bot_avatar_url=av
            ),
        ),
        (
            "Slots (spinning)",
            EmbedFactory.spinning("Slots", "`[ ? ] [ ? ] [ ? ]`", Emoji.SLOTS, bot_avatar_url=av),
        ),
        (
            "Slots (win)",
            EmbedFactory.slots_result(
                "<@42>", ("SEVEN", "SEVEN", "SEVEN"), 10, 500, 690, bot_avatar_url=av
            ),
        ),
        (
            "Slots (loss)",
            EmbedFactory.slots_result(
                "<@42>", ("BAR", "LEMON", "BELL"), 10, 0, 190, bot_avatar_url=av
            ),
        ),
        (
            "Roulette (straight win)",
            EmbedFactory.roulette_result(
                "<@42>", "straight 17", 17, "black", 10, 360, 550, bot_avatar_url=av
            ),
        ),
        (
            "Blackjack (in play)",
            EmbedFactory.blackjack_hand(
                "<@42>", ["10h", "6c"], 16, ["9d", "??"], None, 10, bot_avatar_url=av
            ),
        ),
        (
            "Blackjack (natural)",
            EmbedFactory.blackjack_hand(
                "<@42>", ["As", "Kd"], 21, ["Ks", "9h"], 19, 10, "blackjack", 25, 215,
                bot_avatar_url=av,
            ),
        ),
        (
            "Blackjack (bust)",
            EmbedFactory.blackjack_hand(
                "<@42>", ["10h", "6c", "10d"], 26, ["9d", "8s"], 17, 10, "bust", 0, 190,
                bot_avatar_url=av,
            ),
        ),
        (
            "Gambling record",
            EmbedFactory.gamble_stats(
                "<@42>", [("blackjack", 1, 10, 20), ("slots", 2, 20, 40)], bot_avatar_url=av
            ),
        ),
    ]
    return samples
```

Check the top of `scripts/preview_embeds.py` imports `Emoji`; if it does not, add `from yokai.theme import Emoji` beside the existing `yokai` imports.

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_gambling_embeds.py tests/test_theme.py tests/test_preview_embeds.py tests/test_embeds.py -q; python -m ruff check .; python -m ruff format .; python -m ruff format --check .`
Expected: pass, clean (`ruff format .` normalizes the long preview tuples).

- [ ] **Step 5: Commit**

```bash
git add src/yokai/theme.py src/yokai/ui/embeds.py scripts/preview_embeds.py tests/test_gambling_embeds.py
git commit -m "feat(gambling): add game embeds, quips and preview samples" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Slots, roulette and stats commands, plus bot wiring

**Files:**
- Create: `src/yokai/ui/game_views.py`
- Create: `src/yokai/cogs/gambling.py`
- Modify: `src/yokai/bot.py`
- Modify: `tests/test_one_emoji_rule.py`
- Modify: `tests/test_bot_wiring.py`
- Test: `tests/test_gambling_cog.py`, `tests/test_gambling_views.py`

**Interfaces:**
- Consumes: everything from Tasks 1–8.
- Produces: `SlotsView(user_id: int, stake: int, replay: Callable[[discord.Interaction, int], Awaitable[None]], timeout: float = 120.0)`; `GamblingCog` with commands `/slots bet`, `/roulette bet choice [pick]`, `/gamble-stats [user]`; helpers `_begin(interaction, bet) -> bool`, `_welcome() -> int`, `_defer(interaction)`, `_reveal(interaction, pending, embed, view)`, `_send_domain_error(interaction, err)`; module constant `SPIN_DELAY_S = 1.2`. `YokaiBot.active_games: ActiveGames`; startup refunds open bets and loads `yokai.cogs.gambling`.
- Behavior: validation (range, balance, already-in-game) happens before `defer` and replies ephemerally. The stake is escrowed with `escrowed_bet`, so any failure after the debit refunds it. The active-game claim is released in a `finally`.

- [ ] **Step 1: Write the failing tests.** Create `tests/test_gambling_views.py`:

```python
"""Tests for the slots 'Spin again' view."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from yokai.ui.game_views import SlotsView


def _interaction(user_id: int) -> MagicMock:
    interaction = MagicMock()
    interaction.user.id = user_id
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.response.edit_message = AsyncMock()
    interaction.response.send_message = AsyncMock()
    return interaction


@pytest.mark.asyncio
async def test_only_the_player_can_press_spin_again() -> None:
    view = SlotsView(user_id=1, stake=10, replay=AsyncMock())
    assert await view.interaction_check(_interaction(1)) is True
    stranger = _interaction(2)
    assert await view.interaction_check(stranger) is False
    stranger.response.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_spin_again_disables_the_button_and_replays_the_same_stake_once() -> None:
    replay = AsyncMock()
    view = SlotsView(user_id=1, stake=25, replay=replay)
    interaction = _interaction(1)
    await view.spin_again.callback(interaction)
    assert view.spin_again.disabled is True
    interaction.response.edit_message.assert_awaited_once()
    replay.assert_awaited_once_with(interaction, 25)
```

Create `tests/test_gambling_cog.py`:

```python
"""Tests for the slots, roulette and stats commands against a real database."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest
from discord import app_commands

from yokai.cogs.gambling import GamblingCog
from yokai.gambling.engines.slots import SlotsResult
from yokai.gambling.sessions import ActiveGames
from yokai.storage.db import Database

WELCOME = 200
SEVENS = SlotsResult(("SEVEN", "SEVEN", "SEVEN"), 500)  # 50x
MISS = SlotsResult(("BAR", "LEMON", "BELL"), 0)


async def _setup(tmp_dir: str) -> tuple[GamblingCog, MagicMock, Database]:
    db = Database(Path(tmp_dir) / "t.db")
    await db.initialize()
    bot = MagicMock()
    bot.user = None
    bot.db = db
    bot.active_games = ActiveGames()
    bot.config.gambling_min_bet = 10
    bot.config.gambling_max_bet = 5000
    bot.config.economy_welcome_bonus = WELCOME
    return GamblingCog(bot), bot, db


def _interaction(user_id: int = 111) -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    user.bot = False
    interaction.user = user
    interaction.response.defer = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)
    return interaction


def _pending() -> MagicMock:
    message = MagicMock(spec=discord.Message)
    message.edit = AsyncMock()
    return message


async def _balance(db: Database, user_id: int) -> int:
    wallet = await db.get_wallet(user_id)
    assert wallet is not None
    return wallet.balance


@pytest.mark.asyncio
async def test_a_bet_below_the_minimum_is_rejected_ephemerally_before_deferring() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        cog, bot, db = await _setup(tmp_dir)
        interaction = _interaction()
        with patch("yokai.cogs.gambling.send", new=AsyncMock()) as sent:
            await cog.slots.callback(cog, interaction, bet=5)
        assert sent.await_args.kwargs["ephemeral"] is True
        interaction.response.defer.assert_not_awaited()
        assert await db.get_wallet(111) is None
        assert not bot.active_games.is_active(111)
        await db.close()


@pytest.mark.asyncio
async def test_a_bet_above_the_balance_is_rejected_and_costs_nothing() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        cog, bot, db = await _setup(tmp_dir)
        interaction = _interaction()
        with patch("yokai.cogs.gambling.send", new=AsyncMock()) as sent:
            await cog.slots.callback(cog, interaction, bet=WELCOME + 1)
        assert sent.await_args.kwargs["ephemeral"] is True
        interaction.response.defer.assert_not_awaited()
        assert await _balance(db, 111) == WELCOME
        assert not bot.active_games.is_active(111)
        await db.close()


@pytest.mark.asyncio
async def test_a_second_game_while_one_is_running_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        cog, bot, db = await _setup(tmp_dir)
        bot.active_games.claim(111)
        interaction = _interaction()
        with patch("yokai.cogs.gambling.send", new=AsyncMock()) as sent:
            await cog.slots.callback(cog, interaction, bet=10)
        assert sent.await_args.kwargs["ephemeral"] is True
        assert "in progress" in sent.await_args.kwargs["embed"].description
        assert bot.active_games.is_active(111)  # the running game keeps its claim
        assert await _balance(db, 111) == WELCOME
        await db.close()


@pytest.mark.asyncio
async def test_a_slots_win_pays_out_and_reveals_the_reels() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        cog, bot, db = await _setup(tmp_dir)
        interaction = _interaction()
        pending = _pending()
        with (
            patch("yokai.cogs.gambling.SPIN_DELAY_S", 0),
            patch("yokai.cogs.gambling.slots_engine.spin", return_value=SEVENS),
            patch("yokai.cogs.gambling.send", new=AsyncMock(return_value=pending)),
        ):
            await cog.slots.callback(cog, interaction, bet=10)
        assert await _balance(db, 111) == WELCOME - 10 + 500
        embed = pending.edit.await_args.kwargs["embed"]
        assert "[SEVEN] [SEVEN] [SEVEN]" in embed.description
        assert pending.edit.await_args.kwargs["view"] is not None
        assert not bot.active_games.is_active(111)
        await db.close()


@pytest.mark.asyncio
async def test_a_new_user_can_bet_the_whole_welcome_bonus_and_lose_to_exactly_zero() -> None:
    """Review Focus: a loss never leaves a negative balance."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        cog, bot, db = await _setup(tmp_dir)
        interaction = _interaction(222)
        with (
            patch("yokai.cogs.gambling.SPIN_DELAY_S", 0),
            patch("yokai.cogs.gambling.slots_engine.spin", return_value=MISS),
            patch("yokai.cogs.gambling.send", new=AsyncMock(return_value=_pending())),
        ):
            await cog.slots.callback(cog, interaction, bet=WELCOME)
        assert await _balance(db, 222) == 0
        assert not bot.active_games.is_active(222)
        await db.close()


@pytest.mark.asyncio
async def test_a_failure_after_the_debit_refunds_the_stake_and_frees_the_player() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        cog, bot, db = await _setup(tmp_dir)
        interaction = _interaction()
        with (
            patch("yokai.cogs.gambling.SPIN_DELAY_S", 0),
            patch("yokai.cogs.gambling.slots_engine.spin", return_value=SEVENS),
            patch("yokai.cogs.gambling.send", new=AsyncMock(return_value=_pending())),
            patch.object(db, "settle_bet", new=AsyncMock(side_effect=RuntimeError("db down"))),
            pytest.raises(RuntimeError),
        ):
            await cog.slots.callback(cog, interaction, bet=10)
        assert await _balance(db, 111) == WELCOME
        assert not bot.active_games.is_active(111)
        await db.close()


@pytest.mark.asyncio
async def test_roulette_rejects_a_bad_pick_before_touching_the_wallet() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        cog, bot, db = await _setup(tmp_dir)
        interaction = _interaction()
        choice = app_commands.Choice(name="dozen", value="dozen")
        with patch("yokai.cogs.gambling.send", new=AsyncMock()) as sent:
            await cog.roulette.callback(cog, interaction, bet=10, choice=choice, pick=9)
        assert sent.await_args.kwargs["ephemeral"] is True
        interaction.response.defer.assert_not_awaited()
        assert await db.get_wallet(111) is None
        await db.close()


@pytest.mark.asyncio
async def test_a_roulette_straight_up_win_pays_thirty_six_to_one_total() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        cog, bot, db = await _setup(tmp_dir)
        interaction = _interaction()
        pending = _pending()
        choice = app_commands.Choice(name="straight", value="straight")
        with (
            patch("yokai.cogs.gambling.SPIN_DELAY_S", 0),
            patch("yokai.cogs.gambling.roulette_engine.spin", return_value=17),
            patch("yokai.cogs.gambling.send", new=AsyncMock(return_value=pending)),
        ):
            await cog.roulette.callback(cog, interaction, bet=10, choice=choice, pick=17)
        assert await _balance(db, 111) == WELCOME - 10 + 360
        assert "17" in pending.edit.await_args.kwargs["embed"].description
        assert pending.edit.await_args.kwargs["view"] is None
        await db.close()


@pytest.mark.asyncio
async def test_a_roulette_loss_keeps_only_the_remainder() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        cog, bot, db = await _setup(tmp_dir)
        interaction = _interaction()
        choice = app_commands.Choice(name="red", value="red")
        with (
            patch("yokai.cogs.gambling.SPIN_DELAY_S", 0),
            patch("yokai.cogs.gambling.roulette_engine.spin", return_value=0),
            patch("yokai.cogs.gambling.send", new=AsyncMock(return_value=_pending())),
        ):
            await cog.roulette.callback(cog, interaction, bet=50, choice=choice, pick=None)
        assert await _balance(db, 111) == WELCOME - 50
        await db.close()


@pytest.mark.asyncio
async def test_gamble_stats_reports_settled_games() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        cog, bot, db = await _setup(tmp_dir)
        bet_id = await db.place_bet(111, 10, "slots", WELCOME)
        await db.settle_bet(bet_id, 40)
        with patch("yokai.cogs.gambling.send", new=AsyncMock()) as sent:
            await cog.gamble_stats.callback(cog, _interaction(111), user=None)
        embed = sent.await_args.kwargs["embed"]
        assert "`slots`" in embed.description and "+30" in embed.description
        await db.close()
```

Append to `tests/test_bot_wiring.py` (and add `bot.db.refund_open_bets = AsyncMock(return_value=0)` to the existing `test_voice_earn_tracker_starts_and_stops_with_the_bot`, right after its `bot.db.initialize = AsyncMock()` line, because `setup_hook` now calls it):

```python
@pytest.mark.asyncio
async def test_setup_hook_refunds_open_bets_and_loads_the_gambling_cog() -> None:
    """Review Focus: a restart returns every stake left open by the previous process."""
    bot = make_bot()
    bot.db.initialize = AsyncMock()
    bot.db.refund_open_bets = AsyncMock(return_value=3)
    bot.audio_buffer.start = AsyncMock()
    bot.searcher.warm_up = AsyncMock()
    bot.load_extension = AsyncMock()
    bot.tree.sync = AsyncMock(return_value=[])
    with patch.object(bot.voice_earn_tracker, "start"):
        await bot.setup_hook()
    bot.db.refund_open_bets.assert_awaited_once()
    loaded = [call.args[0] for call in bot.load_extension.await_args_list]
    assert "yokai.cogs.gambling" in loaded
    assert not bot.active_games.is_active(1)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_gambling_views.py tests/test_gambling_cog.py tests/test_bot_wiring.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'yokai.ui.game_views'` / `yokai.cogs.gambling`).

- [ ] **Step 3: Implement.**

`src/yokai/ui/game_views.py` (the blackjack view is added in Task 10):

```python
"""Interactive views for the gambling games."""

from __future__ import annotations

from typing import Awaitable, Callable

import discord

from yokai.ui.views import BaseView

ReplayFn = Callable[[discord.Interaction, int], Awaitable[None]]


class SlotsView(BaseView):
    """A single-use 'Spin again' button that replays the same stake for the same player."""

    def __init__(self, user_id: int, stake: int, replay: ReplayFn, timeout: float = 120.0) -> None:
        super().__init__(allowed_user_ids=[user_id], timeout=timeout)
        self._stake = stake
        self._replay = replay

    @discord.ui.button(label="Spin again", style=discord.ButtonStyle.primary)
    async def spin_again(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.disable_all_items()
        await interaction.response.edit_message(view=self)
        await self._replay(interaction, self._stake)
```

`src/yokai/cogs/gambling.py`:

```python
"""Gambling slash commands: slots, roulette, blackjack, and personal gambling stats."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

from yokai.errors import InsufficientFundsError, InvalidGameChoiceError, YokaiError
from yokai.gambling.engines import roulette as roulette_engine
from yokai.gambling.engines import slots as slots_engine
from yokai.gambling.escrow import escrowed_bet, validate_bet
from yokai.gambling.rng import new_rng
from yokai.theme import Emoji
from yokai.ui.embeds import EmbedFactory, send
from yokai.ui.game_views import SlotsView
from yokai.ui.views import BaseView

if TYPE_CHECKING:
    from yokai.bot import YokaiBot

# Pause between the "spinning" message and the result; one edit keeps us far from rate limits.
SPIN_DELAY_S = 1.2

ROULETTE_CHOICES = [
    app_commands.Choice(name=name, value=name)
    for name in ("red", "black", "odd", "even", "low", "high", "dozen", "column", "straight")
]


class GamblingCog(commands.Cog, name="Gambling"):
    """Solo casino games that spend and win Renown."""

    def __init__(self, bot: YokaiBot) -> None:
        self.bot = bot

    def _avatar_url(self) -> Optional[str]:
        return str(self.bot.user.display_avatar.url) if self.bot.user else None

    def _welcome(self) -> int:
        return self.bot.config.economy_welcome_bonus

    async def _send_domain_error(self, interaction: discord.Interaction, err: Exception) -> None:
        embed = EmbedFactory.error(
            message=getattr(err, "message", str(err)),
            title=getattr(err, "title", "That didn't work"),
            user_hint=getattr(err, "user_hint", None),
            bot_avatar_url=self._avatar_url(),
        )
        await send(interaction, embed=embed, ephemeral=True)

    async def _begin(self, interaction: discord.Interaction, bet: int) -> bool:
        """Validate range, balance and one-game-at-a-time, then claim the player's game slot.

        Runs before any defer, so every rejection is ephemeral. On success the caller owns the
        claim and must release it. Returns False after replying with an error.
        """
        config = self.bot.config
        user_id = interaction.user.id
        try:
            validate_bet(bet, config.gambling_min_bet, config.gambling_max_bet)
            wallet = await self.bot.db.ensure_wallet(user_id, self._welcome())
            if wallet.balance < bet:
                raise InsufficientFundsError(balance=wallet.balance, amount=bet, action="bet")
            self.bot.active_games.claim(user_id)
        except YokaiError as err:
            await self._send_domain_error(interaction, err)
            return False
        return True

    async def _defer(self, interaction: discord.Interaction) -> None:
        # A button replay has already answered the interaction, so only defer when it has not.
        if not interaction.response.is_done():
            await interaction.response.defer(thinking=True)

    async def _reveal(
        self,
        interaction: discord.Interaction,
        pending: Optional[discord.Message],
        embed: discord.Embed,
        view: Optional[BaseView],
    ) -> None:
        """Replace the 'spinning' message with the result (or send it fresh if that failed)."""
        message: Optional[discord.Message]
        if isinstance(pending, discord.Message):
            await pending.edit(embed=embed, view=view)
            message = pending
        else:
            message = await send(interaction, embed=embed, view=view)
        if view is not None and isinstance(message, discord.Message):
            view.message = message

    async def _play_slots(self, interaction: discord.Interaction, bet: int) -> None:
        if not await self._begin(interaction, bet):
            return
        user_id = interaction.user.id
        try:
            async with escrowed_bet(self.bot.db, user_id, bet, "slots", self._welcome()) as bet_id:
                await self._defer(interaction)
                result = slots_engine.spin(new_rng())
                payout = result.payout(bet)
                pending = await send(
                    interaction,
                    embed=EmbedFactory.spinning(
                        title="Slots",
                        description="`[ ? ] [ ? ] [ ? ]`",
                        icon=Emoji.SLOTS,
                        bot_avatar_url=self._avatar_url(),
                    ),
                )
                await asyncio.sleep(SPIN_DELAY_S)
                balance = await self.bot.db.settle_bet(bet_id, payout)
                embed = EmbedFactory.slots_result(
                    user_mention=f"<@{user_id}>",
                    reels=result.reels,
                    stake=bet,
                    payout=payout,
                    new_balance=balance,
                    bot_avatar_url=self._avatar_url(),
                )
                view = SlotsView(user_id, bet, replay=self._play_slots)
                await self._reveal(interaction, pending, embed, view)
        except YokaiError as err:
            await self._send_domain_error(interaction, err)
        finally:
            self.bot.active_games.release(user_id)

    @app_commands.command(name="slots", description="Spin the slots for Renown.")
    @app_commands.describe(bet="Renown to wager (whole number)")
    async def slots(self, interaction: discord.Interaction, bet: int) -> None:
        await self._play_slots(interaction, bet)

    @app_commands.command(name="roulette", description="Bet on a single spin of the wheel.")
    @app_commands.describe(
        bet="Renown to wager (whole number)",
        choice="What to bet on",
        pick="Number 0-36 for straight, or 1-3 for dozen/column",
    )
    @app_commands.choices(choice=ROULETTE_CHOICES)
    async def roulette(
        self,
        interaction: discord.Interaction,
        bet: int,
        choice: app_commands.Choice[str],
        pick: Optional[int] = None,
    ) -> None:
        try:
            wheel_bet = roulette_engine.parse_bet(choice.value, pick)
        except InvalidGameChoiceError as err:
            await self._send_domain_error(interaction, err)
            return
        if not await self._begin(interaction, bet):
            return
        user_id = interaction.user.id
        try:
            async with escrowed_bet(
                self.bot.db, user_id, bet, "roulette", self._welcome()
            ) as bet_id:
                await self._defer(interaction)
                pocket = roulette_engine.spin(new_rng())
                payout = bet * roulette_engine.return_multiplier(wheel_bet, pocket)
                pending = await send(
                    interaction,
                    embed=EmbedFactory.spinning(
                        title="Roulette",
                        description=f"Bet: **{wheel_bet.label}**\nThe wheel is spinning…",
                        icon=Emoji.DICE,
                        bot_avatar_url=self._avatar_url(),
                    ),
                )
                await asyncio.sleep(SPIN_DELAY_S)
                balance = await self.bot.db.settle_bet(bet_id, payout)
                embed = EmbedFactory.roulette_result(
                    user_mention=f"<@{user_id}>",
                    bet_label=wheel_bet.label,
                    pocket=pocket,
                    pocket_colour=roulette_engine.colour(pocket),
                    stake=bet,
                    payout=payout,
                    new_balance=balance,
                    bot_avatar_url=self._avatar_url(),
                )
                await self._reveal(interaction, pending, embed, None)
        except YokaiError as err:
            await self._send_domain_error(interaction, err)
        finally:
            self.bot.active_games.release(user_id)

    @app_commands.command(
        name="gamble-stats", description="Show your (or another member's) gambling record."
    )
    @app_commands.describe(user="Whose record to show (defaults to you)")
    async def gamble_stats(
        self, interaction: discord.Interaction, user: Optional[discord.Member] = None
    ) -> None:
        await interaction.response.defer(thinking=True)
        target = user or interaction.user
        rows = await self.bot.db.get_gamble_stats(target.id)
        embed = EmbedFactory.gamble_stats(
            user_mention=f"<@{target.id}>", rows=rows, bot_avatar_url=self._avatar_url()
        )
        await send(interaction, embed=embed)


async def setup(bot: YokaiBot) -> None:
    """Extension setup entrypoint."""
    await bot.add_cog(GamblingCog(bot))
```

`src/yokai/bot.py`: add `from yokai.gambling.sessions import ActiveGames` with the other `yokai` imports (keep them alphabetical: after `yokai.errors`); in `__init__` after `self.players: dict[int, GuildPlayer] = {}` add `self.active_games = ActiveGames()`; in `setup_hook`, directly after `await self.db.initialize()` add:

```python
        refunded = await self.db.refund_open_bets()
        if refunded:
            logger.info("Refunded %d gambling bet(s) left open by the previous run.", refunded)
```

and after `await self.load_extension("yokai.cogs.economy")` add `await self.load_extension("yokai.cogs.gambling")`.

`tests/test_one_emoji_rule.py`: add `"cogs/gambling.py",` and `"ui/game_views.py",` to the `CHECKED` list.

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_gambling_views.py tests/test_gambling_cog.py tests/test_bot_wiring.py tests/test_one_emoji_rule.py tests/test_code_standards.py -q; python -m ruff check .; python -m ruff format .; python -m ruff format --check .`
Expected: pass, clean. Also run `Grep` for other tests that call `setup_hook` (`python -m pytest -q` in Task 11 covers them); if any fails because `refund_open_bets` is called on a closed DB, add the same `AsyncMock` line there.

- [ ] **Step 5: Commit**

```bash
git add src/yokai/ui/game_views.py src/yokai/cogs/gambling.py src/yokai/bot.py tests/test_gambling_views.py tests/test_gambling_cog.py tests/test_bot_wiring.py tests/test_one_emoji_rule.py
git commit -m "feat(gambling): add /slots, /roulette and /gamble-stats with startup bet refunds" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Interactive blackjack (`/blackjack`)

**Files:**
- Modify: `src/yokai/ui/game_views.py`
- Modify: `src/yokai/cogs/gambling.py`
- Test: `tests/test_blackjack_view.py`, and new tests appended to `tests/test_gambling_cog.py`

**Interfaces:**
- Consumes: `BlackjackGame`, `Outcome`, `hand_value`, `payout_for`, `build_shoe` (Task 7); `Database.place_bet/settle_bet/refund_bet` (Task 3); `EmbedFactory.blackjack_hand` (Task 8); `BaseView`.
- Produces: `BlackjackView(*, game, db, user_id, user_mention, stake, bet_id, group_id, welcome_bonus, can_double, release, bot_avatar_url=None, timeout=180.0)` with:
  - `await start() -> discord.Embed` (settles immediately when the deal is a natural),
  - `await abort() -> None` (refunds every open bet of the hand, releases the player, idempotent),
  - `finished: bool` property (true once settled),
  - `message: Optional[discord.Message]` (inherited, set by the cog),
  - buttons `hit_button`, `stand_button`, `double_button`.
- The view owns the hand's bets (`bet_id`, plus a second bet placed on Double under the same `group_id`) and calls `release()` exactly once when the hand ends or is aborted. All presses are serialized by one `asyncio.Lock`; a press after the hand ended is acknowledged and ignored. On view timeout the hand auto-stands and settles.
- `/blackjack bet`: validates and claims via `_begin`, escrows with `place_bet(..., group_id=<uuid hex>)`, builds the game from `build_shoe(new_rng())`. A failure after the bet is placed aborts the view (or refunds directly if the view was never built) and re-raises.

- [ ] **Step 1: Write the failing tests.** Create `tests/test_blackjack_view.py`:

```python
"""Tests for the interactive blackjack view against a real database."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.card_helpers import cards
from yokai.gambling.engines.blackjack import BlackjackGame
from yokai.storage.db import Database
from yokai.ui.game_views import BlackjackView

WELCOME = 200


async def _make(
    tmp_dir: str, shoe: str, stake: int = 10, can_double: bool = True
) -> tuple[Database, BlackjackView, list[int]]:
    db = Database(Path(tmp_dir) / "t.db")
    await db.initialize()
    bet_id = await db.place_bet(1, stake, "blackjack", WELCOME, group_id="g1")
    released: list[int] = []
    view = BlackjackView(
        game=BlackjackGame(cards(shoe)),
        db=db,
        user_id=1,
        user_mention="<@1>",
        stake=stake,
        bet_id=bet_id,
        group_id="g1",
        welcome_bonus=WELCOME,
        can_double=can_double,
        release=lambda: released.append(1),
    )
    return db, view, released


def _interaction(user_id: int = 1) -> MagicMock:
    interaction = MagicMock()
    interaction.user.id = user_id
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.response.edit_message = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    return interaction


async def _balance(db: Database) -> int:
    wallet = await db.get_wallet(1)
    assert wallet is not None
    return wallet.balance


IN_PLAY = "10h 9d 6c 8s 10c"  # player 16 vs dealer 17: hit busts, double busts, stand loses
WIN_ON_STAND = "10h 6d 9c 5s 7c"  # player 19 vs dealer 18 after one draw
DOUBLE_WINS = "5h 9d 6c 8s 10c"  # player 11 doubles into 21 vs dealer 17


@pytest.mark.asyncio
async def test_a_natural_settles_at_the_deal_without_buttons() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db, view, released = await _make(tmp_dir, "As Kd Ks 9h")
        embed = await view.start()
        assert view.finished and released == [1]
        assert await _balance(db) == WELCOME - 10 + 25
        assert "Blackjack!" in (embed.description or "")
        assert all(item.disabled for item in view.children)
        await db.close()


@pytest.mark.asyncio
async def test_a_hand_in_play_hides_the_dealer_hole_card_and_holds_the_stake() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db, view, released = await _make(tmp_dir, IN_PLAY)
        embed = await view.start()
        assert not view.finished and released == []
        assert "??" in (embed.description or "")
        assert await _balance(db) == WELCOME - 10
        await db.close()


@pytest.mark.asyncio
async def test_stand_settles_a_win() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db, view, released = await _make(tmp_dir, WIN_ON_STAND)
        await view.start()
        interaction = _interaction()
        await view.stand_button.callback(interaction)
        assert await _balance(db) == WELCOME - 10 + 20
        assert released == [1]
        interaction.response.edit_message.assert_awaited_once()
        await db.close()


@pytest.mark.asyncio
async def test_hit_bust_loses_the_stake() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db, view, released = await _make(tmp_dir, IN_PLAY)
        await view.start()
        interaction = _interaction()
        await view.hit_button.callback(interaction)
        assert view.finished and released == [1]
        assert await _balance(db) == WELCOME - 10
        embed = interaction.response.edit_message.await_args.kwargs["embed"]
        assert "Bust" in (embed.description or "")
        await db.close()


@pytest.mark.asyncio
async def test_double_places_a_second_bet_and_pays_both_on_a_win() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db, view, released = await _make(tmp_dir, DOUBLE_WINS)
        await view.start()
        await view.double_button.callback(_interaction())
        assert view.finished and released == [1]
        assert await _balance(db) == WELCOME - 20 + 40
        await db.close()


@pytest.mark.asyncio
async def test_double_is_disabled_up_front_when_the_player_cannot_afford_it() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db, view, _ = await _make(tmp_dir, IN_PLAY, can_double=False)
        assert view.double_button.disabled is True
        await db.close()


@pytest.mark.asyncio
async def test_double_after_spending_the_balance_elsewhere_errors_and_the_hand_continues() -> None:
    """Review Focus: a mid-hand /pay must not let Double push the balance negative."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db, view, released = await _make(tmp_dir, DOUBLE_WINS)
        await view.start()
        await db.pay(1, 2, WELCOME - 10, WELCOME)  # player pays away everything left
        interaction = _interaction()
        await view.double_button.callback(interaction)
        assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True
        assert not view.finished and released == []
        assert view.double_button.disabled is True
        assert await _balance(db) == 0
        await db.close()


@pytest.mark.asyncio
async def test_a_double_click_settles_once() -> None:
    """Review Focus: two near-simultaneous presses must not double-settle."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db, view, released = await _make(tmp_dir, WIN_ON_STAND)
        await view.start()
        first, second = _interaction(), _interaction()
        await asyncio.gather(
            view.stand_button.callback(first), view.stand_button.callback(second)
        )
        assert await _balance(db) == WELCOME - 10 + 20  # paid once, not twice
        assert released == [1]
        first.response.edit_message.assert_awaited_once()
        second.response.edit_message.assert_not_awaited()
        second.response.defer.assert_awaited_once()
        await db.close()


@pytest.mark.asyncio
async def test_timeout_auto_stands_and_settles() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db, view, released = await _make(tmp_dir, WIN_ON_STAND)
        await view.start()
        view.message = MagicMock()
        view.message.edit = AsyncMock()
        await view.on_timeout()
        assert view.finished and released == [1]
        assert await _balance(db) == WELCOME - 10 + 20
        view.message.edit.assert_awaited_once()
        await db.close()


@pytest.mark.asyncio
async def test_only_the_player_can_use_the_buttons() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db, view, _ = await _make(tmp_dir, IN_PLAY)
        assert await view.interaction_check(_interaction(1)) is True
        assert await view.interaction_check(_interaction(2)) is False
        await db.close()


@pytest.mark.asyncio
async def test_abort_refunds_the_stake_and_frees_the_player_once() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db, view, released = await _make(tmp_dir, IN_PLAY)
        await view.start()
        await view.abort()
        await view.abort()
        assert await _balance(db) == WELCOME
        assert released == [1]
        await db.close()


@pytest.mark.asyncio
async def test_a_settlement_failure_refunds_and_frees_the_player() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db, view, released = await _make(tmp_dir, WIN_ON_STAND)
        await view.start()
        with (
            patch.object(db, "settle_bet", new=AsyncMock(side_effect=RuntimeError("db down"))),
            pytest.raises(RuntimeError),
        ):
            await view.stand_button.callback(_interaction())
        assert await _balance(db) == WELCOME
        assert released == [1]
        await db.close()
```

Append to `tests/test_gambling_cog.py` (add `from tests.card_helpers import cards` and `from yokai.ui.game_views import BlackjackView` to its imports):

```python
@pytest.mark.asyncio
async def test_blackjack_natural_pays_immediately_and_sends_no_buttons() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        cog, bot, db = await _setup(tmp_dir)
        interaction = _interaction()
        with (
            patch(
                "yokai.cogs.gambling.blackjack_engine.build_shoe",
                return_value=cards("As Kd Ks 9h"),
            ),
            patch("yokai.cogs.gambling.send", new=AsyncMock(return_value=_pending())) as sent,
        ):
            await cog.blackjack.callback(cog, interaction, bet=10)
        assert sent.await_args.kwargs["view"] is None
        assert await _balance(db, 111) == WELCOME - 10 + 25
        assert not bot.active_games.is_active(111)
        await db.close()


@pytest.mark.asyncio
async def test_blackjack_in_play_keeps_the_player_claimed_until_the_hand_ends() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        cog, bot, db = await _setup(tmp_dir)
        interaction = _interaction()
        pending = _pending()
        with (
            patch(
                "yokai.cogs.gambling.blackjack_engine.build_shoe",
                return_value=cards("10h 6d 9c 5s 7c"),
            ),
            patch("yokai.cogs.gambling.send", new=AsyncMock(return_value=pending)) as sent,
        ):
            await cog.blackjack.callback(cog, interaction, bet=10)
        view = sent.await_args.kwargs["view"]
        assert isinstance(view, BlackjackView)
        assert view.message is pending
        assert bot.active_games.is_active(111)
        assert await _balance(db, 111) == WELCOME - 10

        press = MagicMock()
        press.user.id = 111
        press.response.is_done = MagicMock(return_value=False)
        press.response.edit_message = AsyncMock()
        await view.stand_button.callback(press)
        assert await _balance(db, 111) == WELCOME - 10 + 20
        assert not bot.active_games.is_active(111)
        await db.close()


@pytest.mark.asyncio
async def test_blackjack_rejects_a_bad_bet_before_deferring() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        cog, bot, db = await _setup(tmp_dir)
        interaction = _interaction()
        with patch("yokai.cogs.gambling.send", new=AsyncMock()) as sent:
            await cog.blackjack.callback(cog, interaction, bet=1)
        assert sent.await_args.kwargs["ephemeral"] is True
        interaction.response.defer.assert_not_awaited()
        assert await db.get_wallet(111) is None
        await db.close()


@pytest.mark.asyncio
async def test_blackjack_failure_after_the_bet_refunds_it() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        cog, bot, db = await _setup(tmp_dir)
        interaction = _interaction()
        with (
            patch(
                "yokai.cogs.gambling.blackjack_engine.build_shoe",
                return_value=cards("10h 6d 9c 5s 7c"),
            ),
            patch("yokai.cogs.gambling.send", new=AsyncMock(side_effect=RuntimeError("boom"))),
            pytest.raises(RuntimeError),
        ):
            await cog.blackjack.callback(cog, interaction, bet=10)
        assert await _balance(db, 111) == WELCOME
        assert not bot.active_games.is_active(111)
        await db.close()
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_blackjack_view.py tests/test_gambling_cog.py -q`
Expected: FAIL (`ImportError: cannot import name 'BlackjackView'`).

- [ ] **Step 3: Implement.**

Replace the imports at the top of `src/yokai/ui/game_views.py` with:

```python
from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Awaitable, Callable, Optional

import discord

from yokai.errors import BetNotOpenError, InsufficientFundsError
from yokai.gambling.engines.blackjack import BlackjackGame, Outcome, hand_value, payout_for
from yokai.ui.embeds import EmbedFactory
from yokai.ui.views import BaseView

if TYPE_CHECKING:
    from yokai.storage.db import Database

logger = logging.getLogger(__name__)

ReplayFn = Callable[[discord.Interaction, int], Awaitable[None]]
```

and append to the end of the file:

```python
def _verdict(game: BlackjackGame) -> str:
    if game.player_busted:
        return "bust"
    if game.outcome is Outcome.BLACKJACK:
        return "blackjack"
    if game.outcome is Outcome.WIN:
        return "win"
    if game.outcome is Outcome.PUSH:
        return "push"
    return "lose"


class BlackjackView(BaseView):
    """Hit / Stand / Double buttons for one blackjack hand, owning the hand's escrowed bets."""

    def __init__(
        self,
        *,
        game: BlackjackGame,
        db: Database,
        user_id: int,
        user_mention: str,
        stake: int,
        bet_id: int,
        group_id: str,
        welcome_bonus: int,
        can_double: bool,
        release: Callable[[], None],
        bot_avatar_url: Optional[str] = None,
        timeout: float = 180.0,
    ) -> None:
        super().__init__(allowed_user_ids=[user_id], timeout=timeout)
        self.game = game
        self.db = db
        self.user_id = user_id
        self.user_mention = user_mention
        self.stake = stake
        self.bet_id = bet_id
        self.group_id = group_id
        self.welcome_bonus = welcome_bonus
        self.bot_avatar_url = bot_avatar_url
        self._release = release
        self._double_bet_id: Optional[int] = None
        self._lock = asyncio.Lock()
        self._closed = False
        self._result: Optional[tuple[int, int, int]] = None  # (total stake, payout, balance)
        self.double_button.disabled = not can_double

    @property
    def finished(self) -> bool:
        """True once the hand has been settled."""
        return self._result is not None

    def _open_bet_ids(self) -> list[int]:
        if self._double_bet_id is None:
            return [self.bet_id]
        return [self.bet_id, self._double_bet_id]

    def _close(self) -> None:
        self._closed = True
        self.disable_all_items()
        self.stop()
        self._release()

    async def _refund_all(self) -> None:
        for bet_id in self._open_bet_ids():
            with contextlib.suppress(BetNotOpenError):
                await self.db.refund_bet(bet_id)

    def build_embed(self) -> discord.Embed:
        game = self.game
        player_cards = [str(card) for card in game.player]
        player_total = hand_value(game.player)[0]
        if self._result is None:
            return EmbedFactory.blackjack_hand(
                user_mention=self.user_mention,
                player_cards=player_cards,
                player_total=player_total,
                dealer_cards=[str(game.dealer[0]), "??"],
                dealer_total=None,
                stake=self.stake * len(self._open_bet_ids()),
                bot_avatar_url=self.bot_avatar_url,
            )
        total_stake, total_payout, balance = self._result
        return EmbedFactory.blackjack_hand(
            user_mention=self.user_mention,
            player_cards=player_cards,
            player_total=player_total,
            dealer_cards=[str(card) for card in game.dealer],
            dealer_total=hand_value(game.dealer)[0],
            stake=total_stake,
            verdict=_verdict(game),
            payout=total_payout,
            new_balance=balance,
            bot_avatar_url=self.bot_avatar_url,
        )

    async def _finish(self) -> discord.Embed:
        """Settle every bet of the finished hand, free the player, and return the result embed."""
        outcome = self.game.outcome
        assert outcome is not None
        per_bet = payout_for(outcome, self.stake)
        bet_ids = self._open_bet_ids()
        try:
            balance = 0
            for bet_id in bet_ids:
                balance = await self.db.settle_bet(bet_id, per_bet)
        except Exception:
            await self._refund_all()
            self._close()
            raise
        self._result = (self.stake * len(bet_ids), per_bet * len(bet_ids), balance)
        self._close()
        return self.build_embed()

    async def start(self) -> discord.Embed:
        """First render; settles at once when the deal was a natural."""
        async with self._lock:
            if self.game.finished:
                return await self._finish()
            return self.build_embed()

    async def abort(self) -> None:
        """Refund the hand's bets and free the player (used when sending the hand failed)."""
        async with self._lock:
            if self._closed:
                return
            await self._refund_all()
            self._close()

    async def _ack_if_closed(self, interaction: discord.Interaction) -> bool:
        """Swallow a press that arrives after the hand ended (e.g. a double click)."""
        if not self._closed:
            return False
        if not interaction.response.is_done():
            await interaction.response.defer()
        return True

    async def _advance(self, interaction: discord.Interaction) -> None:
        if self.game.finished:
            embed = await self._finish()
        else:
            self.double_button.disabled = self.double_button.disabled or not self.game.can_double
            embed = self.build_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Hit", style=discord.ButtonStyle.primary)
    async def hit_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        async with self._lock:
            if await self._ack_if_closed(interaction):
                return
            self.game.hit()
            self.double_button.disabled = True
            await self._advance(interaction)

    @discord.ui.button(label="Stand", style=discord.ButtonStyle.secondary)
    async def stand_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        async with self._lock:
            if await self._ack_if_closed(interaction):
                return
            self.game.stand()
            await self._advance(interaction)

    @discord.ui.button(label="Double", style=discord.ButtonStyle.success)
    async def double_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        async with self._lock:
            if await self._ack_if_closed(interaction):
                return
            if not self.game.can_double:
                if not interaction.response.is_done():
                    await interaction.response.defer()
                return
            try:
                self._double_bet_id = await self.db.place_bet(
                    self.user_id,
                    self.stake,
                    "blackjack",
                    self.welcome_bonus,
                    group_id=self.group_id,
                )
            except InsufficientFundsError as err:
                self.double_button.disabled = True
                embed = EmbedFactory.error(
                    message=err.message,
                    title=err.title,
                    user_hint=err.user_hint,
                    bot_avatar_url=self.bot_avatar_url,
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
            self.game.double()
            await self._advance(interaction)

    async def on_timeout(self) -> None:
        """Walking away must not forfeit the hand: auto-stand, settle, and disable the buttons."""
        async with self._lock:
            if self._closed:
                return
            if not self.game.finished:
                self.game.stand()
            embed = await self._finish()
        if self.message:
            try:
                await self.message.edit(embed=embed, view=self)
            except discord.HTTPException as exc:
                logger.debug("Could not update blackjack message on timeout: %s", exc)
```

In `src/yokai/cogs/gambling.py`: add to the imports `import contextlib`, `import uuid`; extend the errors import to `from yokai.errors import (BetNotOpenError, InsufficientFundsError, InvalidGameChoiceError, YokaiError)`; add `from yokai.gambling.engines import blackjack as blackjack_engine`; change the views import to `from yokai.ui.game_views import BlackjackView, SlotsView`; then add this command to `GamblingCog`, between `roulette` and `gamble_stats`:

```python
    @app_commands.command(name="blackjack", description="Play a hand of blackjack for Renown.")
    @app_commands.describe(bet="Renown to wager (whole number)")
    async def blackjack(self, interaction: discord.Interaction, bet: int) -> None:
        if not await self._begin(interaction, bet):
            return
        user_id = interaction.user.id
        group_id = uuid.uuid4().hex
        try:
            bet_id = await self.bot.db.place_bet(
                user_id, bet, "blackjack", self._welcome(), group_id=group_id
            )
        except YokaiError as err:
            self.bot.active_games.release(user_id)
            await self._send_domain_error(interaction, err)
            return

        view: Optional[BlackjackView] = None
        try:
            wallet = await self.bot.db.get_wallet(user_id)
            game = blackjack_engine.BlackjackGame(blackjack_engine.build_shoe(new_rng()))
            view = BlackjackView(
                game=game,
                db=self.bot.db,
                user_id=user_id,
                user_mention=f"<@{user_id}>",
                stake=bet,
                bet_id=bet_id,
                group_id=group_id,
                welcome_bonus=self._welcome(),
                can_double=wallet is not None and wallet.balance >= bet,
                release=lambda: self.bot.active_games.release(user_id),
                bot_avatar_url=self._avatar_url(),
            )
            await self._defer(interaction)
            embed = await view.start()
            message = await send(interaction, embed=embed, view=None if view.finished else view)
            if not view.finished and isinstance(message, discord.Message):
                view.message = message
        except BaseException:
            if view is not None:
                await view.abort()
            else:
                with contextlib.suppress(BetNotOpenError):
                    await self.bot.db.refund_bet(bet_id)
                self.bot.active_games.release(user_id)
            raise
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_blackjack_view.py tests/test_gambling_cog.py tests/test_one_emoji_rule.py tests/test_code_standards.py -q; python -m ruff check .; python -m ruff format .; python -m ruff format --check .`
Expected: pass, clean.

- [ ] **Step 5: Commit**

```bash
git add src/yokai/ui/game_views.py src/yokai/cogs/gambling.py tests/test_blackjack_view.py tests/test_gambling_cog.py
git commit -m "feat(gambling): add interactive /blackjack with hit, stand and double" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Docs, spec corrections, full verification, stop

**Files:**
- Modify: `TASKS.md`
- Modify: `docs/superpowers/specs/2026-09-30-gambling-design.md`

- [ ] **Step 1: Correct the spec where the project's rules forced a different choice.** Make these exact replacements in `docs/superpowers/specs/2026-09-30-gambling-design.md`:

1. In §5 Slots, replace `- Three reels from a weighted symbol table; paytable is a constant (three of a kind 5x–50x by symbol, any two cherries 1.5x, else 0).` with `- Three reels from a weighted symbol table (CHERRY 40, LEMON 30, BELL 15, BAR 10, SEVEN 5 per 100); paytable is a constant (three of a kind 5x–50x by symbol, exactly two cherries 1.4x, else 0). Reels render as text names (`[CHERRY] [BELL] [SEVEN]`), never emoji, because AGENTS.md allows one emoji per embed. Exact RTP 95.03%.`
2. In §7, replace `Cards render as text (`A♠ K♥`).` with `Cards render in poker notation (`As Kh 10d`, suit letters c/d/h/s), never suit glyphs, because the one-emoji test counts `♠♥♦♣` as emoji.`
3. In §9, add a bullet: `- Embeds: every gambling embed has at most one emoji and contains no suit glyphs.`

- [ ] **Step 2: Record the phase in `TASKS.md`.** Insert this block immediately before the `## Later (do not start without owner approval)` heading:

```markdown
## Phase 8 — Economy: Gambling G1 (escrow + solo games)

**Goal:** The shared bet-escrow foundation and the three solo games. Spec:
`docs/superpowers/specs/2026-09-30-gambling-design.md` (§11, phase G1). Plan:
`docs/superpowers/plans/2026-10-01-gambling-g1-plan.md`. G2 (lobbies, duel, jackpot), G3 (crash,
blackjack table) and G4 (poker) get their own plans after this phase is reviewed.

- [ ] G1.1 `GAMBLING_MIN_BET` / `GAMBLING_MAX_BET` configuration.
- [ ] G1.2 Typed errors: `BetOutOfRangeError`, `AlreadyInGameError`, `BetNotOpenError`,
      `InvalidGameChoiceError`; `InsufficientFundsError` gains an `action` word.
- [ ] G1.3 Migration v3 (`gambling_bets`) and `Database` escrow: `place_bet`, `settle_bet`,
      `refund_bet`, `refund_open_bets`, `get_gamble_stats`.
- [ ] G1.4 `gambling/` package: `rng`, `escrow` (`validate_bet`, `escrowed_bet`), `sessions`.
- [ ] G1.5 Pure engines: slots, roulette, blackjack.
- [ ] G1.6 Theme emoji/quips and game embeds; preview page samples.
- [ ] G1.7 `/slots`, `/roulette`, `/gamble-stats`; startup refund of open bets; cog loaded.
- [ ] G1.8 Interactive `/blackjack` (hit, stand, double, timeout auto-stand).

**Acceptance criteria**
- Every stake is debited when a game starts; every win, push and refund credits through an audited
  `renown_transactions` row; a loss is recorded in `gambling_bets`.
- A bet outside the min/max, above the balance, or while another game is running is rejected
  ephemerally before any defer, and costs nothing.
- Restarting the bot mid-hand returns the stake of every open bet at startup.
- Slots RTP is 94–96% (exact enumeration test); every roulette bet type has a 36/37 return;
  blackjack follows the spec's rules (3:2, dealer stands on all 17s, double on two cards, no
  splits or insurance).
- A blackjack hand left idle auto-stands and settles when the view times out.
- No embed has more than one emoji; cards and reels use text notation.
- `pytest`, `ruff check` and `ruff format --check` are clean.
- *(manual, real Discord)* `/slots`, `/roulette` (each bet type), `/blackjack` (hit, stand,
  double, natural, bust) and `/gamble-stats` behave as described; a second game is refused while
  one is running; kill the process mid-hand and confirm the stake is back after restart.

**STOP — owner review.**

---

```

- [ ] **Step 3: Full verification**

Run each and confirm the output:

```powershell
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
```

Expected: the whole suite passes (nothing skipped except `live` tests), ruff clean. If an unrelated test fails because it calls `setup_hook` against a closed database, add `bot.db.refund_open_bets = AsyncMock(return_value=0)` to that test and re-run. Do not tick any `TASKS.md` box until this output has been seen.

- [ ] **Step 4: Tick the completed `TASKS.md` boxes (G1.1–G1.8) and commit**

```bash
git add TASKS.md docs/superpowers/specs/2026-09-30-gambling-design.md
git commit -m "docs(gambling): record G1 in TASKS.md and align the spec with the one-emoji rule" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 5: Stop and report (AGENTS.md rule 2).** Summarize what changed, list anything unverified (the manual checklist in the acceptance criteria needs a real Discord session), and wait for the owner's review. After approval, tag the checkpoint:

```bash
git tag -a checkpoint/gambling-g1-complete -m "Gambling G1 (escrow + solo games) complete"
```
