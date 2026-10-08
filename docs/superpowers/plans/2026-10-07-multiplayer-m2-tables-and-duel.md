# Multiplayer Phase M2: Tables, Lobbies, Pot Accounting and Duel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an admin dedicate a channel to a game with `/admin set_multiplayer`, give it a persistent **Play** embed, run ticket-style tables through a shared lobby framework with atomic settlement, feed the house's take into a lottery pot, and ship the first game: **duel** (pick a number 1 to 10, closest to a hidden number wins, a tie refunds both).

**Architecture:** Pure pieces (`gambling/lobby.py` state machine, `gambling/engines/duel.py`, `gambling/pot.py`) are tested without Discord. `gambling/tables.py` (`TableManager`) is the only code that escrows and settles for tables, using new `Database` methods (`settle_lobby`, pot credit, channel storage). The Discord layer (`ui/table_views.py`, `ui/duel_views.py`, `cogs/tables.py`, two new `/admin` subcommands) is thin glue. A persistent `PlayView` (`timeout=None`, fixed `custom_id`) is registered once at startup.

**Tech Stack:** Python 3.12, discord.py 2.7.1 (`ui.View`, `ui.Modal`, `ui.Select`, `Bot.add_view`), aiosqlite, pytest (`asyncio_mode = "auto"`), ruff (line length 100).

**Spec:** `docs/superpowers/specs/2026-10-07-multiplayer-tables-design.md` (§4 channel storage, §5 tables and duel, §6 pot accounting, §9 config, §10 errors, §11 testing). M3 (jackpot, crash, blackjack table) and M4 (lottery) are separate plans.

## Global Constraints

- Python 3.12+, `discord.py>=2.7.1`; slash commands guild-scoped; no privileged intents (`AGENTS.md`).
- Every user-visible message goes through `ui/embeds.py` (`EmbedFactory`); at most one emoji per embed, emoji only from `theme.Emoji`, never hand-written in feature code; no suit glyphs; errors are ephemeral and plain.
- `allowed_mentions` none everywhere; members are mentioned only inside embeds.
- Defer within 3 s for anything that touches the network; validate before deferring so rejections are ephemeral.
- Buttons and views block blacklisted users (`moderation.gate.screen`); the persistent view has `timeout=None` and a fixed `custom_id`.
- Never block the event loop; no bare `except`; `logging` not `print`; `from __future__ import annotations`; full type hints.
- Stakes are whole Renown within `GAMBLING_MIN_BET` to `GAMBLING_MAX_BET`. Duel rake `GAMBLING_DUEL_RAKE_PERCENT` default 2, rounded down; join window `GAMBLING_LOBBY_JOIN_SECONDS` default 60; pick timer `GAMBLING_TURN_SECONDS` default 45.
- A bot crash must never pay a pot twice or lose a stake: tables settle with one atomic `settle_lobby`; open bets are refunded at startup.
- No new dependency. Schema version 6 (migration v6) is the only schema change in this phase.
- Work directly on `main`; checkpoint tag first; commit per task; finish with `pytest -q`, `ruff check .`, `ruff format --check .` clean, then stop for owner review.

## Review Focus

- A failure in the middle of `settle_lobby` (a bad bet, an error crediting the pot) leaves every bet open, every wallet unchanged and the pot unchanged; settling twice or concurrently pays once (Task 3).
- Two members pressing Play at once open exactly one table; the loser gets the busy error and loses no Renown (Task 7).
- A table stake is refunded on every exit path: announce failure, join window ending below the minimum, host or admin cancel, abort during the duel, a failing settlement (Task 7).
- The pot never double counts: a duel adds only the rake to the pot, a tie adds nothing, a refund adds nothing (Tasks 2, 3, 7).
- Only seated players can pick; a pick is locked once made; a pick outside 1 to 10 is refused (Tasks 6, 9).
- A restart must not overwrite a finished game's result: only a table still marked `table_active` is rewritten as "closed by the restart" (Task 10).
- The host cannot cancel a table that has started, and cannot "leave" their own table (Tasks 5, 7).

---

## File Structure

| File | Responsibility |
|---|---|
| `src/yokai/config.py` (modify) | duel rake, join window, turn timer |
| `src/yokai/errors.py` (modify) | `TableBusyError`, `ChannelNotConfiguredError`, `LobbyClosedError`, `NotAtTableError`, `NotHostError`, `TablePermissionError` |
| `src/yokai/storage/db.py` (modify) | migration v6, pot credit, `settle_lobby`, channel storage |
| `src/yokai/gambling/models.py` (create) | `MultiplayerChannel` dataclass |
| `src/yokai/gambling/pot.py` (create) | pure house-take rules |
| `src/yokai/gambling/lobby.py` (create) | `GameSpec`, `GAME_SPECS`, `Seat`, `LobbyState`, `Lobby` |
| `src/yokai/gambling/engines/duel.py` (create) | `DuelRound`, `decide`, `draw_target`, `prize_after_rake` |
| `src/yokai/gambling/tables.py` (create) | `TableManager`, `BeginOutcome`, `DuelResult` |
| `src/yokai/ui/embeds.py` (modify) | `table_play`, `table_lobby`, `table_notice`, `duel_picking`, `duel_result` |
| `src/yokai/ui/table_common.py` (create) | error replies, dismiss, quiet delete helpers |
| `src/yokai/ui/table_views.py` (create) | `PlayView`, `StakeModal`, `LobbyView`, `GAME_RUNNERS` |
| `src/yokai/ui/duel_views.py` (create) | `DuelView`, `PickSelect`, `run_duel` |
| `src/yokai/cogs/tables.py` (create) | `TablesCog`: persistent view, set/unset channel, startup reconcile |
| `src/yokai/cogs/admin.py` (modify) | `set_multiplayer`, `unset_multiplayer` |
| `src/yokai/bot.py` (modify) | `bot.tables`, load extension, reconcile on ready |
| `scripts/preview_embeds.py` (modify) | sample embeds |
| tests | `tests/table_helpers.py`, `test_gambling_pot.py`, `test_gambling_settle_lobby.py`, `test_multiplayer_channels.py`, `test_lobby.py`, `test_duel_engine.py`, `test_table_manager.py`, `test_table_embeds.py`, `test_table_views.py`, `test_duel_views.py`, `test_tables_cog.py`, plus edits to existing tests |

---

### Task 0: Checkpoint tag

- [ ] **Step 1: Tag a clean main**

```powershell
git status --short; git branch --show-current
git tag -a checkpoint/pre-multiplayer-m2 -m "Before multiplayer M2 (tables, pot accounting, duel)"
git tag | Select-String multiplayer
```
Expected: no modified files, branch `main`, both multiplayer tags listed.

---

### Task 1: Configuration, errors and spec amendments

**Files:**
- Modify: `src/yokai/config.py` (Config fields near line 61-63, parsing near line 172-176, `Config(...)` call near line 205)
- Modify: `src/yokai/errors.py` (append)
- Modify: `.env.example`
- Modify: `docs/superpowers/specs/2026-10-07-multiplayer-tables-design.md`
- Test: `tests/test_gambling_config.py`, `tests/test_gambling_errors.py`

**Interfaces:**
- Produces: `Config.gambling_duel_rake_percent: int`, `Config.gambling_lobby_join_seconds: int`, `Config.gambling_turn_seconds: int`; errors `TableBusyError()`, `ChannelNotConfiguredError()`, `LobbyClosedError(message=..., user_hint=...)`, `NotAtTableError()`, `NotHostError()`, `TablePermissionError(missing: Sequence[str], channel_name: str)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gambling_config.py`:
```python
def test_table_defaults() -> None:
    cfg = _load()
    assert cfg.gambling_duel_rake_percent == 2
    assert cfg.gambling_lobby_join_seconds == 60
    assert cfg.gambling_turn_seconds == 45


def test_table_overrides() -> None:
    cfg = _load(
        {
            "GAMBLING_DUEL_RAKE_PERCENT": "5",
            "GAMBLING_LOBBY_JOIN_SECONDS": "90",
            "GAMBLING_TURN_SECONDS": "30",
        }
    )
    assert (
        cfg.gambling_duel_rake_percent,
        cfg.gambling_lobby_join_seconds,
        cfg.gambling_turn_seconds,
    ) == (5, 90, 30)


def test_rake_is_clamped_between_zero_and_fifty() -> None:
    assert _load({"GAMBLING_DUEL_RAKE_PERCENT": "-3"}).gambling_duel_rake_percent == 0
    assert _load({"GAMBLING_DUEL_RAKE_PERCENT": "0"}).gambling_duel_rake_percent == 0
    assert _load({"GAMBLING_DUEL_RAKE_PERCENT": "99"}).gambling_duel_rake_percent == 50


def test_timers_have_a_five_second_floor() -> None:
    cfg = _load({"GAMBLING_LOBBY_JOIN_SECONDS": "0", "GAMBLING_TURN_SECONDS": "1"})
    assert (cfg.gambling_lobby_join_seconds, cfg.gambling_turn_seconds) == (5, 5)
```
Append to `tests/test_gambling_errors.py` (add the imports to its existing import block):
```python
from yokai.errors import (
    ChannelNotConfiguredError,
    LobbyClosedError,
    NotAtTableError,
    NotHostError,
    TableBusyError,
    TablePermissionError,
    YokaiError,
)


def test_table_errors_are_plain_domain_errors() -> None:
    for err in (
        TableBusyError(),
        ChannelNotConfiguredError(),
        LobbyClosedError(),
        NotAtTableError(),
        NotHostError(),
        TablePermissionError(["Send Messages", "Embed Links"], "tables"),
    ):
        assert isinstance(err, YokaiError)
        assert err.message and err.title


def test_lobby_closed_error_accepts_a_custom_message_without_a_hint() -> None:
    err = LobbyClosedError("This table is full.", user_hint=None)
    assert err.message == "This table is full." and err.user_hint is None


def test_table_permission_error_names_what_is_missing() -> None:
    err = TablePermissionError(["Send Messages", "Embed Links"], "tables")
    assert "Send Messages" in err.message and "Embed Links" in err.message
    assert "#tables" in err.message
```
If `tests/test_gambling_errors.py` already imports from `yokai.errors`, merge the names into the existing import instead of adding a second statement.

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_gambling_config.py tests/test_gambling_errors.py -q`
Expected: failures: `AttributeError: 'Config' object has no attribute 'gambling_duel_rake_percent'` and an `ImportError` for the new error names.

- [ ] **Step 3: Implement**

`src/yokai/config.py`: add the three fields to the `Config` dataclass after `gambling_max_bet`:
```python
    # Gambling: multiplayer tables
    gambling_duel_rake_percent: int = 2
    gambling_lobby_join_seconds: int = 60
    gambling_turn_seconds: int = 45
```
In `load_config`, after the `GAMBLING_MAX_BET` check and before `return Config(`:
```python
    gambling_duel_rake_percent = min(
        50,
        max(
            0,
            _parse_int(
                "GAMBLING_DUEL_RAKE_PERCENT", os.getenv("GAMBLING_DUEL_RAKE_PERCENT"), 2
            ),
        ),
    )
    gambling_lobby_join_seconds = max(
        5,
        _parse_int(
            "GAMBLING_LOBBY_JOIN_SECONDS", os.getenv("GAMBLING_LOBBY_JOIN_SECONDS"), 60
        ),
    )
    gambling_turn_seconds = max(
        5, _parse_int("GAMBLING_TURN_SECONDS", os.getenv("GAMBLING_TURN_SECONDS"), 45)
    )
```
and pass `gambling_duel_rake_percent=gambling_duel_rake_percent, gambling_lobby_join_seconds=gambling_lobby_join_seconds, gambling_turn_seconds=gambling_turn_seconds,` in the `Config(...)` call after `gambling_max_bet=gambling_max_bet,`.

`src/yokai/errors.py` (add `from typing import Optional, Sequence` to the existing typing import, then append):
```python
class TableBusyError(YokaiError):
    """Raised when a table is requested in a channel that already has a live one."""

    title = "Table in use"

    def __init__(self) -> None:
        super().__init__(
            "A table is already open in this channel.",
            user_hint="Wait for it to finish, or ask the host to cancel it if it has not started.",
        )


class ChannelNotConfiguredError(YokaiError):
    """Raised when Play is pressed in a channel that is not a table channel."""

    title = "Not a table channel"

    def __init__(self) -> None:
        super().__init__(
            "This channel is not set up for a multiplayer game.",
            user_hint="An admin can set one up with /admin set_multiplayer.",
        )


class LobbyClosedError(YokaiError):
    """Raised when a table action is not possible in the table's current state."""

    title = "Table closed"

    def __init__(
        self,
        message: str = "This table is no longer open.",
        user_hint: Optional[str] = "Press Play in the table channel to start a new one.",
    ) -> None:
        super().__init__(message, user_hint=user_hint)


class NotAtTableError(YokaiError):
    """Raised when someone who is not seated tries to act as a player."""

    title = "Not at this table"

    def __init__(self) -> None:
        super().__init__(
            "You are not seated at this table.",
            user_hint="Only players at the table can do that.",
        )


class NotHostError(YokaiError):
    """Raised when a host-only table action is attempted by someone else."""

    title = "Host only"

    def __init__(self) -> None:
        super().__init__(
            "Only the host or an admin can do that.",
            user_hint="Ask the host of this table.",
        )


class TablePermissionError(YokaiError):
    """Raised when the bot lacks permissions in a channel chosen as a table channel."""

    title = "Missing permission"

    def __init__(self, missing: Sequence[str], channel_name: str) -> None:
        listed = ", ".join(missing)
        super().__init__(
            f"I am missing these permissions in #{channel_name}: {listed}.",
            user_hint="Grant them to Yokai in that channel's settings and run the command again.",
        )
        self.missing = list(missing)
        self.channel_name = channel_name
```
`.env.example`: after the `GAMBLING_MAX_BET=5000` line append:
```
# Multiplayer tables
# Percent of a duel pot kept by the house (0 disables); it feeds the lottery pot
GAMBLING_DUEL_RAKE_PERCENT=2
# Seconds a table stays open for players to join
GAMBLING_LOBBY_JOIN_SECONDS=60
# Seconds a player has to act on their turn (duel: to pick a number)
GAMBLING_TURN_SECONDS=45
```
Spec amendments in `docs/superpowers/specs/2026-10-07-multiplayer-tables-design.md`: replace
```
  table_message_id INTEGER,       -- current or last table message (NULL for lottery)
  updated_ts INTEGER NOT NULL
);
CREATE TABLE lottery_state (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  pot INTEGER NOT NULL DEFAULT 0
);
```
with
```
  table_message_id INTEGER,       -- current or last table message (NULL for lottery)
  table_active INTEGER NOT NULL DEFAULT 0,  -- 1 while a table is open or running
  updated_ts INTEGER NOT NULL
);
CREATE TABLE lottery_state (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  pot INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO lottery_state (id, pot) VALUES (1, 0);
```
and replace `a stale table message is edited to say the table was closed by the restart (its stakes are already refunded by the startup sweep).` with `a table message whose channel is still marked \`table_active\` is edited to say the table was closed by the restart (its stakes are already refunded by the startup sweep); a finished table is never rewritten, so its result stays on screen.`

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_gambling_config.py tests/test_gambling_errors.py -q`
Expected: all pass.

- [ ] **Step 5: Suite, lint, commit**

```powershell
python -m pytest -q; python -m ruff check .; python -m ruff format --check .
git add src/yokai/config.py src/yokai/errors.py .env.example docs/superpowers/specs tests/test_gambling_config.py tests/test_gambling_errors.py
git commit -m "feat(tables): add table configuration and errors" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>" -m "Claude-Session: https://claude.ai/code/session_01BxUkTAJGP4uNByTtyvj8n9"
```
Expected: suite green, lint clean.

---

### Task 2: Migration v6 and pot accounting on solo settlement

**Files:**
- Create: `src/yokai/gambling/pot.py`
- Modify: `src/yokai/storage/db.py` (`CURRENT_SCHEMA_VERSION`, new `MIGRATION_V6`, `_migrate`, `_finish_bet`, new `_credit_pot`, `get_pot`)
- Create: `tests/test_gambling_pot.py`
- Modify: `tests/test_admin_database_backups.py:60` (schema assertion)

**Interfaces:**
- Produces: `POOLED_GAMES: frozenset[str]`; `bet_house_take(game: str, stake: int, payout: int) -> int`; `lobby_house_take(game: str, stakes: Sequence[int], payouts: Sequence[int]) -> int`; `Database._credit_pot(source: str, amount: int, now: int) -> None` (runs inside the caller's open transaction, no commit); `Database.get_pot() -> int`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_gambling_pot.py`:
```python
"""House-take rules and the lottery pot ledger."""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from yokai.gambling.pot import POOLED_GAMES, bet_house_take, lobby_house_take
from yokai.storage.db import (
    CURRENT_SCHEMA_VERSION,
    MIGRATION_V1,
    MIGRATION_V2,
    MIGRATION_V3,
    MIGRATION_V4,
    MIGRATION_V5,
    Database,
)


def test_pooled_games_are_the_ones_where_players_win_each_others_stakes() -> None:
    assert {"duel", "jackpot", "poker"} <= POOLED_GAMES
    assert "slots" not in POOLED_GAMES


def test_a_lost_house_banked_bet_goes_to_the_pot() -> None:
    assert bet_house_take("slots", 100, 0) == 100


def test_a_win_or_a_push_adds_nothing() -> None:
    assert bet_house_take("slots", 100, 250) == 0
    assert bet_house_take("slots", 100, 100) == 0


def test_a_partial_return_leaves_the_difference() -> None:
    assert bet_house_take("roulette", 100, 40) == 60


def test_a_bet_in_a_pooled_game_has_no_take_of_its_own() -> None:
    assert bet_house_take("duel", 100, 0) == 0


def test_a_pooled_lobby_adds_only_the_rake() -> None:
    assert lobby_house_take("duel", [50, 50], [98, 0]) == 2
    assert lobby_house_take("duel", [50, 50], [100, 0]) == 0


def test_a_house_banked_lobby_adds_each_losing_stake() -> None:
    assert lobby_house_take("crash", [100, 100, 100], [250, 0, 100]) == 100


def test_lobby_take_rejects_mismatched_lists() -> None:
    with pytest.raises(ValueError):
        lobby_house_take("duel", [50, 50], [100])


async def _db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "pot.db")
    await db.initialize()
    return db


async def _tables(db: Database) -> set[str]:
    async with db.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table';") as cur:
        return {row[0] for row in await cur.fetchall()}


@pytest.mark.asyncio
async def test_migration_v6_creates_the_tables_and_seeds_an_empty_pot(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        assert CURRENT_SCHEMA_VERSION == 6
        names = await _tables(db)
        assert {"multiplayer_channels", "lottery_state", "lottery_pot_entries"} <= names
        assert await db.get_pot() == 0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_v5_database_upgrades_to_v6_keeping_its_rows(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    raw = await aiosqlite.connect(path)
    for script in (MIGRATION_V1, MIGRATION_V2, MIGRATION_V3, MIGRATION_V4, MIGRATION_V5):
        await raw.executescript(script)
    await raw.execute("PRAGMA user_version = 5;")
    await raw.execute(
        "INSERT INTO wallets (user_id, balance, daily_streak, created_ts) VALUES (7, 321, 0, 1);"
    )
    await raw.commit()
    await raw.close()

    db = Database(path)
    await db.initialize()
    try:
        wallet = await db.get_wallet(7)
        assert wallet is not None and wallet.balance == 321
        assert await db.get_pot() == 0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_losing_a_solo_bet_feeds_the_pot(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        await db.ensure_wallet(1, 1000)
        bet = await db.place_bet(1, 100, "slots", 0)
        await db.settle_bet(bet, 0)
        assert await db.get_pot() == 100
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_win_a_push_and_a_refund_add_nothing(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        await db.ensure_wallet(1, 1000)
        win = await db.place_bet(1, 100, "slots", 0)
        push = await db.place_bet(1, 100, "slots", 0)
        refunded = await db.place_bet(1, 100, "slots", 0)
        await db.settle_bet(win, 250)
        await db.settle_bet(push, 100)
        await db.refund_bet(refunded)
        assert await db.get_pot() == 0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_partial_return_adds_the_difference(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        await db.ensure_wallet(1, 1000)
        bet = await db.place_bet(1, 100, "roulette", 0)
        await db.settle_bet(bet, 40)
        assert await db.get_pot() == 60
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_settling_a_pooled_game_bet_on_its_own_adds_nothing(tmp_path: Path) -> None:
    """A duel stake is settled by settle_lobby; settle_bet must not double count it."""
    db = await _db(tmp_path)
    try:
        await db.ensure_wallet(1, 1000)
        bet = await db.place_bet(1, 100, "duel", 0)
        await db.settle_bet(bet, 0)
        assert await db.get_pot() == 0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_pot_entries_add_up_to_the_pot(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        await db.ensure_wallet(1, 1000)
        for stake in (100, 40):
            bet = await db.place_bet(1, stake, "slots", 0)
            await db.settle_bet(bet, 0)
        async with db.conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(amount), 0), MIN(source) FROM lottery_pot_entries;"
        ) as cur:
            count, total, source = await cur.fetchone()  # type: ignore[misc]
        assert (count, total, source) == (2, 140, "slots")
        assert await db.get_pot() == 140
    finally:
        await db.close()
```
In `tests/test_admin_database_backups.py` line 60 change `== 5` to `== CURRENT_SCHEMA_VERSION` and add `from yokai.storage.db import CURRENT_SCHEMA_VERSION` to that file's imports (merge into an existing `yokai.storage.db` import if one exists).

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_gambling_pot.py -q`
Expected: collection ERROR `ModuleNotFoundError: No module named 'yokai.gambling.pot'`.

- [ ] **Step 3: Implement**

Create `src/yokai/gambling/pot.py`:
```python
"""What the house keeps from a settled game; the lottery pot is fed from it."""

from __future__ import annotations

from typing import Sequence

# Games where players win each other's stakes. Only the rake (what the payouts leave of the
# pot) is the house's; counting the losing stakes too would count them twice.
POOLED_GAMES: frozenset[str] = frozenset({"duel", "jackpot", "poker"})


def bet_house_take(game: str, stake: int, payout: int) -> int:
    """House take from one bet settled on its own (a pooled game's bets are settled together)."""
    if game in POOLED_GAMES:
        return 0
    return max(0, stake - payout)


def lobby_house_take(game: str, stakes: Sequence[int], payouts: Sequence[int]) -> int:
    """House take from a whole lobby settled in one transaction."""
    if len(stakes) != len(payouts):
        raise ValueError("stakes and payouts must have the same length")
    if game in POOLED_GAMES:
        return max(0, sum(stakes) - sum(payouts))
    return sum(max(0, stake - payout) for stake, payout in zip(stakes, payouts))
```
`src/yokai/storage/db.py`: set `CURRENT_SCHEMA_VERSION = 6`; add import `from yokai.gambling.pot import bet_house_take`; add after `MIGRATION_V5`:
```python
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
```
In `_migrate` after the `version < 5` block add:
```python
        if version < 6:
            logger.info("Applying SQLite migration v6...")
            await self.conn.executescript(MIGRATION_V6)
            await self.conn.execute("PRAGMA user_version = 6;")
            await self.conn.commit()
            logger.info("SQLite schema migrated to version 6.")
```
Add these methods to `Database` (before `_finish_bet`):
```python
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
```
In `_finish_bet`, immediately after the `if cursor.rowcount == 0: ... raise BetNotOpenError(bet_id)` block that follows the `UPDATE gambling_bets` and before `if credit > 0:` wallet update, insert:
```python
                if not refund:
                    await self._credit_pot(game, bet_house_take(game, stake, credit), now)
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_gambling_pot.py -q`
Expected: all pass. Then `python -m pytest -q`: fix any other test that hard-codes schema version 5 by using `CURRENT_SCHEMA_VERSION` (the grep earlier found only `tests/test_admin_database_backups.py:60`).

- [ ] **Step 5: Lint and commit**

```powershell
python -m ruff check .; python -m ruff format --check .
git add src/yokai/gambling/pot.py src/yokai/storage/db.py tests/test_gambling_pot.py tests/test_admin_database_backups.py
git commit -m "feat(tables): add migration v6 and lottery pot accounting for solo games" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>" -m "Claude-Session: https://claude.ai/code/session_01BxUkTAJGP4uNByTtyvj8n9"
```

---

### Task 3: Atomic `settle_lobby`

**Files:**
- Modify: `src/yokai/storage/db.py` (new method after `settle_bet`)
- Create: `tests/test_gambling_settle_lobby.py`

**Interfaces:**
- Consumes: `lobby_house_take`, `POOLED_GAMES` (Task 2), `Database._credit_pot`, `Database._write_tx`.
- Produces: `Database.settle_lobby(payouts: Mapping[int, int]) -> dict[int, int]`, keyed by bet id, returning each bet owner's new balance. Raises `BetNotOpenError` (a bet is not open, nothing changes) or `ValueError` (empty, negative payout, mixed games, or a pooled game paying out more than was staked).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_gambling_settle_lobby.py`:
```python
"""settle_lobby settles every bet of a table in one all-or-nothing transaction."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from yokai.errors import BetNotOpenError
from yokai.storage.db import Database


async def _world(tmp_path: Path) -> tuple[Database, int, int]:
    db = Database(tmp_path / "lobby.db")
    await db.initialize()
    await db.ensure_wallet(1, 1000)
    await db.ensure_wallet(2, 1000)
    a = await db.place_bet(1, 100, "duel", 0, lobby_id="L1")
    b = await db.place_bet(2, 100, "duel", 0, lobby_id="L1")
    return db, a, b


async def _balance(db: Database, user_id: int) -> int:
    wallet = await db.get_wallet(user_id)
    assert wallet is not None
    return wallet.balance


async def _status(db: Database, bet_id: int) -> str:
    async with db.conn.execute("SELECT status FROM gambling_bets WHERE id = ?;", (bet_id,)) as cur:
        row = await cur.fetchone()
    assert row is not None
    return row[0]


@pytest.mark.asyncio
async def test_settles_winner_and_loser_and_sends_only_the_rake_to_the_pot(tmp_path: Path) -> None:
    db, a, b = await _world(tmp_path)
    try:
        balances = await db.settle_lobby({a: 196, b: 0})
        assert balances == {a: 1096, b: 900}
        assert (await _balance(db, 1), await _balance(db, 2)) == (1096, 900)
        assert await db.get_pot() == 4
        assert (await _status(db, a), await _status(db, b)) == ("settled", "settled")
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_the_winner_gets_a_win_row_and_the_loser_none(tmp_path: Path) -> None:
    db, a, b = await _world(tmp_path)
    try:
        await db.settle_lobby({a: 196, b: 0})
        async with db.conn.execute(
            "SELECT user_id, delta, reason FROM renown_transactions WHERE reason LIKE 'win:%';"
        ) as cur:
            assert await cur.fetchall() == [(1, 196, "win:duel")]
        async with db.conn.execute(
            "SELECT COUNT(*) FROM renown_transactions WHERE user_id = 2 AND reason LIKE 'win:%';"
        ) as cur:
            assert (await cur.fetchone())[0] == 0  # type: ignore[index]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_bet_that_is_not_open_aborts_everything(tmp_path: Path) -> None:
    db, a, b = await _world(tmp_path)
    try:
        await db.refund_bet(b)  # b is no longer open
        before = (await _balance(db, 1), await _balance(db, 2), await db.get_pot())
        with pytest.raises(BetNotOpenError):
            await db.settle_lobby({a: 196, b: 0})
        assert (await _balance(db, 1), await _balance(db, 2), await db.get_pot()) == before
        assert await _status(db, a) == "open"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_failure_crediting_the_pot_rolls_everything_back(tmp_path: Path) -> None:
    db, a, b = await _world(tmp_path)
    try:
        before = (await _balance(db, 1), await _balance(db, 2))
        with patch.object(db, "_credit_pot", new=AsyncMock(side_effect=RuntimeError("boom"))):
            with pytest.raises(RuntimeError):
                await db.settle_lobby({a: 196, b: 0})
        assert (await _balance(db, 1), await _balance(db, 2)) == before
        assert (await _status(db, a), await _status(db, b)) == ("open", "open")
        assert await db.get_pot() == 0
        # and the lock was released, so the table can still be refunded
        assert await db.refund_open_bets() == 2
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_pooled_game_cannot_pay_out_more_than_was_staked(tmp_path: Path) -> None:
    db, a, b = await _world(tmp_path)
    try:
        with pytest.raises(ValueError):
            await db.settle_lobby({a: 201, b: 0})
        assert (await _status(db, a), await _status(db, b)) == ("open", "open")
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_settling_twice_pays_once(tmp_path: Path) -> None:
    db, a, b = await _world(tmp_path)
    try:
        await db.settle_lobby({a: 196, b: 0})
        with pytest.raises(BetNotOpenError):
            await db.settle_lobby({a: 196, b: 0})
        assert await _balance(db, 1) == 1096
        assert await db.get_pot() == 4
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_two_concurrent_settlements_pay_exactly_once(tmp_path: Path) -> None:
    db, a, b = await _world(tmp_path)
    try:
        results = await asyncio.gather(
            db.settle_lobby({a: 196, b: 0}),
            db.settle_lobby({a: 0, b: 196}),
            return_exceptions=True,
        )
        assert sum(isinstance(r, BetNotOpenError) for r in results) == 1
        assert await _balance(db, 1) + await _balance(db, 2) == 2000 - 4
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_the_startup_sweep_finds_nothing_after_a_settlement(tmp_path: Path) -> None:
    db, a, b = await _world(tmp_path)
    try:
        await db.settle_lobby({a: 196, b: 0})
        assert await db.refund_open_bets() == 0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_rejects_empty_negative_and_mixed_game_payouts(tmp_path: Path) -> None:
    db, a, b = await _world(tmp_path)
    try:
        await db.ensure_wallet(3, 1000)
        other = await db.place_bet(3, 50, "slots", 0)
        with pytest.raises(ValueError):
            await db.settle_lobby({})
        with pytest.raises(ValueError):
            await db.settle_lobby({a: -1, b: 0})
        with pytest.raises(ValueError):
            await db.settle_lobby({a: 100, other: 0})
        assert await _status(db, a) == "open" and await _status(db, other) == "open"
    finally:
        await db.close()
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_gambling_settle_lobby.py -q`
Expected: failures with `AttributeError: 'Database' object has no attribute 'settle_lobby'`.

- [ ] **Step 3: Implement**

In `src/yokai/storage/db.py` add `from typing import AsyncIterator, Mapping, Optional` (extend the existing typing import), `from yokai.gambling.pot import POOLED_GAMES, bet_house_take, lobby_house_take` (replacing the Task 2 import), and add after `settle_bet`:
```python
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
                balances[bet_id] = wallet.balance
            await self._credit_pot(game, lobby_house_take(game, stakes, amounts), now)
            await self.conn.commit()
        return balances
```
(Keep `bet_house_take` imported for `_finish_bet`; `POOLED_GAMES` and `lobby_house_take` are new uses.)

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_gambling_settle_lobby.py -q`
Expected: 9 passed. If `test_two_concurrent_settlements_pay_exactly_once` fails on ordering, the second call must raise `BetNotOpenError` from its first SELECT (the first call already settled both bets inside `_write_tx`).

- [ ] **Step 5: Suite, lint, commit**

```powershell
python -m pytest -q; python -m ruff check .; python -m ruff format --check .
git add src/yokai/storage/db.py tests/test_gambling_settle_lobby.py
git commit -m "feat(tables): settle a whole table atomically with settle_lobby" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>" -m "Claude-Session: https://claude.ai/code/session_01BxUkTAJGP4uNByTtyvj8n9"
```

---

### Task 4: Table channel storage

**Files:**
- Create: `src/yokai/gambling/models.py`
- Modify: `src/yokai/storage/db.py` (new methods after `get_pot`)
- Create: `tests/test_multiplayer_channels.py`

**Interfaces:**
- Produces: `MultiplayerChannel(channel_id: int, game: str, play_message_id: Optional[int], table_message_id: Optional[int], table_active: bool)` (frozen dataclass); on `Database`: `set_multiplayer_channel(channel_id, game, play_message_id, now=None) -> list[MultiplayerChannel]` (returns the rows it displaced), `get_multiplayer_channel(channel_id) -> Optional[MultiplayerChannel]`, `list_multiplayer_channels() -> list[MultiplayerChannel]`, `set_play_message(channel_id, message_id) -> None`, `set_table_message(channel_id, message_id, active) -> None`, `set_table_inactive(channel_id) -> None`, `clear_multiplayer_channel(channel_id) -> Optional[MultiplayerChannel]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_multiplayer_channels.py`:
```python
"""Storage for dedicated table channels."""

from __future__ import annotations

from pathlib import Path

import pytest

from yokai.gambling.models import MultiplayerChannel
from yokai.storage.db import Database


async def _db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "channels.db")
    await db.initialize()
    return db


@pytest.mark.asyncio
async def test_a_channel_can_be_dedicated_and_read_back(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        removed = await db.set_multiplayer_channel(10, "duel", 555)
        assert removed == []
        assert await db.get_multiplayer_channel(10) == MultiplayerChannel(10, "duel", 555, None, False)
        assert await db.get_multiplayer_channel(11) is None
        assert await db.list_multiplayer_channels() == [
            MultiplayerChannel(10, "duel", 555, None, False)
        ]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_dedicating_the_same_game_elsewhere_moves_it_and_returns_the_old_row(
    tmp_path: Path,
) -> None:
    db = await _db(tmp_path)
    try:
        await db.set_multiplayer_channel(10, "duel", 555)
        await db.set_table_message(10, 600, active=False)
        removed = await db.set_multiplayer_channel(20, "duel", 777)
        assert removed == [MultiplayerChannel(10, "duel", 555, 600, False)]
        assert await db.get_multiplayer_channel(10) is None
        assert (await db.get_multiplayer_channel(20)).play_message_id == 777  # type: ignore[union-attr]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_repointing_a_channel_to_another_game_replaces_its_row(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        await db.set_multiplayer_channel(10, "duel", 555)
        removed = await db.set_multiplayer_channel(10, "jackpot", 888)
        assert [r.game for r in removed] == ["duel"]
        current = await db.get_multiplayer_channel(10)
        assert current is not None and current.game == "jackpot"
        assert len(await db.list_multiplayer_channels()) == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_table_message_and_active_flag_round_trip(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        await db.set_multiplayer_channel(10, "duel", 555)
        await db.set_table_message(10, 900, active=True)
        row = await db.get_multiplayer_channel(10)
        assert row is not None and (row.table_message_id, row.table_active) == (900, True)
        await db.set_table_inactive(10)
        row = await db.get_multiplayer_channel(10)
        assert row is not None and (row.table_message_id, row.table_active) == (900, False)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_set_play_message_replaces_only_the_play_message(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        await db.set_multiplayer_channel(10, "duel", 555)
        await db.set_table_message(10, 900, active=False)
        await db.set_play_message(10, 556)
        row = await db.get_multiplayer_channel(10)
        assert row == MultiplayerChannel(10, "duel", 556, 900, False)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_clearing_returns_the_row_once(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        await db.set_multiplayer_channel(10, "duel", 555)
        assert (await db.clear_multiplayer_channel(10)) == MultiplayerChannel(
            10, "duel", 555, None, False
        )
        assert await db.clear_multiplayer_channel(10) is None
        assert await db.list_multiplayer_channels() == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_updating_an_unknown_channel_is_a_quiet_no_op(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        await db.set_table_message(99, 1, active=True)
        await db.set_table_inactive(99)
        await db.set_play_message(99, 1)
        assert await db.list_multiplayer_channels() == []
    finally:
        await db.close()
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_multiplayer_channels.py -q`
Expected: collection ERROR `No module named 'yokai.gambling.models'`.

- [ ] **Step 3: Implement**

Create `src/yokai/gambling/models.py`:
```python
"""Plain data shared by the multiplayer table code."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class MultiplayerChannel:
    """A channel dedicated to one multiplayer game."""

    channel_id: int
    game: str
    play_message_id: Optional[int]
    table_message_id: Optional[int]
    table_active: bool
```
In `src/yokai/storage/db.py` add `from yokai.gambling.models import MultiplayerChannel`, a module-level constant and helper above `class Database`:
```python
_CHANNEL_SELECT = (
    "SELECT channel_id, game, play_message_id, table_message_id, table_active "
    "FROM multiplayer_channels"
)


def _channel_from_row(row: tuple[int, str, Optional[int], Optional[int], int]) -> MultiplayerChannel:
    return MultiplayerChannel(
        channel_id=row[0],
        game=row[1],
        play_message_id=row[2],
        table_message_id=row[3],
        table_active=bool(row[4]),
    )
```
and these methods after `get_pot`:
```python
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
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_multiplayer_channels.py -q`
Expected: 7 passed.

- [ ] **Step 5: Suite, lint, commit**

```powershell
python -m pytest -q; python -m ruff check .; python -m ruff format .; python -m ruff format --check .
git add src/yokai/gambling/models.py src/yokai/storage/db.py tests/test_multiplayer_channels.py
git commit -m "feat(tables): store dedicated table channels" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>" -m "Claude-Session: https://claude.ai/code/session_01BxUkTAJGP4uNByTtyvj8n9"
```

---

### Task 5: Lobby state machine

**Files:**
- Create: `src/yokai/gambling/lobby.py`
- Create: `tests/test_lobby.py`

**Interfaces:**
- Produces: `LobbyState` (`OPEN`, `RUNNING`, `SETTLED`, `CANCELLED`); `GameSpec(name, label, min_players, max_players, equal_stakes=False, window_factor=1.0, rules="")`; `GAME_SPECS: dict[str, GameSpec]` (M2: `"duel"`); `Seat(user_id, stake, bet_id)`; `Lobby(lobby_id, spec, channel_id, host_id, closes_at)` with attributes `id, spec, channel_id, host_id, closes_at, state, seats, lock, message_id`, properties `is_active, is_full, can_start, pot, host_seat`, and methods `add_seat(seat)`, `remove_seat(user_id) -> Seat`, `mark_running()`, `close(state)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_lobby.py`:
```python
"""The pure lobby state machine."""

from __future__ import annotations

import pytest

from yokai.errors import LobbyClosedError, NotAtTableError
from yokai.gambling.lobby import GAME_SPECS, GameSpec, Lobby, LobbyState, Seat

TRIO = GameSpec("trio", "Trio", min_players=2, max_players=3)


def _lobby(spec: GameSpec = TRIO) -> Lobby:
    lobby = Lobby("L1", spec, channel_id=10, host_id=1, closes_at=1000.0)
    lobby.add_seat(Seat(1, 100, 11))
    return lobby


def test_the_duel_spec_is_a_two_seat_equal_stake_game() -> None:
    spec = GAME_SPECS["duel"]
    assert (spec.min_players, spec.max_players, spec.equal_stakes) == (2, 2, True)
    assert spec.label == "Duel" and spec.rules


def test_a_new_lobby_is_open_with_the_host_seated() -> None:
    lobby = _lobby()
    assert lobby.state is LobbyState.OPEN and lobby.is_active
    assert lobby.host_seat == Seat(1, 100, 11)
    assert lobby.pot == 100 and not lobby.can_start and not lobby.is_full


def test_seats_fill_up_to_the_maximum() -> None:
    lobby = _lobby()
    lobby.add_seat(Seat(2, 50, 12))
    assert lobby.can_start and lobby.pot == 150
    lobby.add_seat(Seat(3, 25, 13))
    assert lobby.is_full
    with pytest.raises(LobbyClosedError, match="full"):
        lobby.add_seat(Seat(4, 10, 14))


def test_the_same_member_cannot_sit_twice() -> None:
    lobby = _lobby()
    with pytest.raises(LobbyClosedError, match="already seated"):
        lobby.add_seat(Seat(1, 100, 99))


def test_a_guest_can_leave_but_the_host_cannot() -> None:
    lobby = _lobby()
    lobby.add_seat(Seat(2, 50, 12))
    assert lobby.remove_seat(2) == Seat(2, 50, 12)
    with pytest.raises(LobbyClosedError, match="host"):
        lobby.remove_seat(1)
    with pytest.raises(NotAtTableError):
        lobby.remove_seat(3)


def test_seats_only_change_while_open() -> None:
    lobby = _lobby()
    lobby.add_seat(Seat(2, 50, 12))
    lobby.mark_running()
    with pytest.raises(LobbyClosedError):
        lobby.add_seat(Seat(3, 25, 13))
    with pytest.raises(LobbyClosedError):
        lobby.remove_seat(2)


def test_transitions_follow_open_running_settled() -> None:
    lobby = _lobby()
    lobby.mark_running()
    assert lobby.state is LobbyState.RUNNING and lobby.is_active
    lobby.close(LobbyState.SETTLED)
    assert lobby.state is LobbyState.SETTLED and not lobby.is_active


def test_an_open_or_running_lobby_can_be_cancelled_but_a_closed_one_cannot_change() -> None:
    open_lobby = _lobby()
    open_lobby.close(LobbyState.CANCELLED)
    assert open_lobby.state is LobbyState.CANCELLED
    with pytest.raises(LobbyClosedError):
        open_lobby.close(LobbyState.SETTLED)
    with pytest.raises(LobbyClosedError):
        open_lobby.mark_running()


def test_close_only_accepts_terminal_states() -> None:
    lobby = _lobby()
    with pytest.raises(ValueError):
        lobby.close(LobbyState.RUNNING)


def test_a_lobby_cannot_start_running_twice() -> None:
    lobby = _lobby()
    lobby.mark_running()
    with pytest.raises(LobbyClosedError):
        lobby.mark_running()
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_lobby.py -q`
Expected: collection ERROR `No module named 'yokai.gambling.lobby'`.

- [ ] **Step 3: Implement**

Create `src/yokai/gambling/lobby.py`:
```python
"""Lobby state machine shared by every multiplayer table game. No Discord, no database."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from yokai.errors import LobbyClosedError, NotAtTableError


class LobbyState(Enum):
    OPEN = "open"
    RUNNING = "running"
    SETTLED = "settled"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class GameSpec:
    """Static facts about a table game."""

    name: str
    label: str
    min_players: int
    max_players: int
    equal_stakes: bool = False
    window_factor: float = 1.0  # share of GAMBLING_LOBBY_JOIN_SECONDS this game waits
    rules: str = ""


GAME_SPECS: dict[str, GameSpec] = {
    "duel": GameSpec(
        name="duel",
        label="Duel",
        min_players=2,
        max_players=2,
        equal_stakes=True,
        rules=(
            "Two players, one hidden number from 1 to 10. Each picks a number in secret; "
            "the pick closest to the hidden number takes the pot. A tie refunds both stakes."
        ),
    ),
}


@dataclass(frozen=True)
class Seat:
    user_id: int
    stake: int
    bet_id: int


class Lobby:
    """One table: who is seated, what state it is in, and the lock that serialises changes."""

    def __init__(
        self, lobby_id: str, spec: GameSpec, channel_id: int, host_id: int, closes_at: float
    ) -> None:
        self.id = lobby_id
        self.spec = spec
        self.channel_id = channel_id
        self.host_id = host_id
        self.closes_at = closes_at
        self.state = LobbyState.OPEN
        self.seats: dict[int, Seat] = {}
        self.lock = asyncio.Lock()
        self.message_id: Optional[int] = None

    @property
    def is_active(self) -> bool:
        return self.state in (LobbyState.OPEN, LobbyState.RUNNING)

    @property
    def is_full(self) -> bool:
        return len(self.seats) >= self.spec.max_players

    @property
    def can_start(self) -> bool:
        return len(self.seats) >= self.spec.min_players

    @property
    def pot(self) -> int:
        return sum(seat.stake for seat in self.seats.values())

    @property
    def host_seat(self) -> Seat:
        return self.seats[self.host_id]

    def add_seat(self, seat: Seat) -> None:
        if self.state is not LobbyState.OPEN:
            raise LobbyClosedError()
        if seat.user_id in self.seats:
            raise LobbyClosedError("You are already seated at this table.", user_hint=None)
        if self.is_full:
            raise LobbyClosedError("This table is full.", user_hint=None)
        self.seats[seat.user_id] = seat

    def remove_seat(self, user_id: int) -> Seat:
        if self.state is not LobbyState.OPEN:
            raise LobbyClosedError()
        if user_id not in self.seats:
            raise NotAtTableError()
        if user_id == self.host_id:
            raise LobbyClosedError(
                "The host cannot leave their own table.",
                user_hint="Cancel the table instead.",
            )
        return self.seats.pop(user_id)

    def mark_running(self) -> None:
        if self.state is not LobbyState.OPEN:
            raise LobbyClosedError()
        self.state = LobbyState.RUNNING

    def close(self, state: LobbyState) -> None:
        """Move to SETTLED or CANCELLED; a closed lobby never changes again."""
        if state not in (LobbyState.SETTLED, LobbyState.CANCELLED):
            raise ValueError("close() takes SETTLED or CANCELLED")
        if not self.is_active:
            raise LobbyClosedError()
        self.state = state
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_lobby.py -q`
Expected: 10 passed.

- [ ] **Step 5: Lint and commit**

```powershell
python -m ruff check .; python -m ruff format --check .
git add src/yokai/gambling/lobby.py tests/test_lobby.py
git commit -m "feat(tables): add the lobby state machine" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>" -m "Claude-Session: https://claude.ai/code/session_01BxUkTAJGP4uNByTtyvj8n9"
```

---

### Task 6: Duel engine

**Files:**
- Create: `src/yokai/gambling/engines/duel.py`
- Create: `tests/test_duel_engine.py`

**Interfaces:**
- Produces: `NUMBER_MIN = 1`, `NUMBER_MAX = 10`; `draw_target(rng) -> int`; `prize_after_rake(pot: int, rake_percent: int) -> tuple[int, int]` returning `(prize, rake)`; `decide(picks: Mapping[int, int], target: int) -> Optional[int]` (winner id, or `None` for a tie); `DuelOutcome(target, picks, winner_id)` with `is_tie`; `DuelRound(user_ids: Sequence[int], target: int)` with `submit(user_id, number)`, `has_picked(user_id)`, `picked_ids`, `ready`, `fill_missing(rng) -> list[int]`, `outcome() -> DuelOutcome`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_duel_engine.py`:
```python
"""The pure duel rules: closest pick to a hidden number wins, a tie refunds."""

from __future__ import annotations

import random

import pytest

from yokai.errors import InvalidGameChoiceError, NotAtTableError
from yokai.gambling.engines.duel import (
    NUMBER_MAX,
    NUMBER_MIN,
    DuelRound,
    decide,
    draw_target,
    prize_after_rake,
)


def test_the_hidden_number_is_always_in_range() -> None:
    rng = random.Random(7)
    drawn = {draw_target(rng) for _ in range(500)}
    assert drawn <= set(range(NUMBER_MIN, NUMBER_MAX + 1))
    assert drawn == set(range(NUMBER_MIN, NUMBER_MAX + 1))


def test_the_closer_pick_wins() -> None:
    assert decide({1: 4, 2: 9}, target=5) == 1
    assert decide({1: 4, 2: 6}, target=7) == 2
    assert decide({1: 10, 2: 1}, target=10) == 1


def test_equal_distance_is_a_tie() -> None:
    assert decide({1: 3, 2: 7}, target=5) is None


def test_the_same_pick_is_a_tie() -> None:
    assert decide({1: 6, 2: 6}, target=2) is None


def test_the_rake_is_a_percentage_rounded_down() -> None:
    assert prize_after_rake(100, 2) == (98, 2)
    assert prize_after_rake(101, 2) == (99, 2)
    assert prize_after_rake(20, 2) == (20, 0)
    assert prize_after_rake(200, 0) == (200, 0)


def test_picks_are_locked_once_made() -> None:
    duel = DuelRound([1, 2], target=5)
    duel.submit(1, 4)
    with pytest.raises(InvalidGameChoiceError, match="already"):
        duel.submit(1, 9)
    assert duel.has_picked(1) and not duel.has_picked(2)
    assert duel.picked_ids == [1] and not duel.ready


def test_only_the_two_players_can_pick() -> None:
    duel = DuelRound([1, 2], target=5)
    with pytest.raises(NotAtTableError):
        duel.submit(3, 5)


@pytest.mark.parametrize("bad", [0, 11, -1, 100])
def test_a_pick_outside_one_to_ten_is_refused(bad: int) -> None:
    duel = DuelRound([1, 2], target=5)
    with pytest.raises(InvalidGameChoiceError):
        duel.submit(1, bad)
    assert not duel.has_picked(1)


def test_the_round_is_ready_when_both_have_picked() -> None:
    duel = DuelRound([1, 2], target=5)
    duel.submit(1, 4)
    duel.submit(2, 8)
    assert duel.ready
    outcome = duel.outcome()
    assert (outcome.target, outcome.picks, outcome.winner_id) == (5, {1: 4, 2: 8}, 1)
    assert not outcome.is_tie


def test_missing_picks_are_filled_at_random_and_reported() -> None:
    duel = DuelRound([1, 2], target=5)
    duel.submit(1, 4)
    filled = duel.fill_missing(random.Random(3))
    assert filled == [2] and duel.ready
    assert NUMBER_MIN <= duel.outcome().picks[2] <= NUMBER_MAX


def test_the_outcome_needs_both_picks() -> None:
    duel = DuelRound([1, 2], target=5)
    duel.submit(1, 4)
    with pytest.raises(ValueError):
        duel.outcome()


def test_a_round_needs_exactly_two_distinct_players() -> None:
    with pytest.raises(ValueError):
        DuelRound([1], target=5)
    with pytest.raises(ValueError):
        DuelRound([1, 1], target=5)
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_duel_engine.py -q`
Expected: collection ERROR `No module named 'yokai.gambling.engines.duel'`.

- [ ] **Step 3: Implement**

Create `src/yokai/gambling/engines/duel.py`:
```python
"""Duel rules: two players each pick 1 to 10; the pick closest to a hidden number wins."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence

from yokai.errors import InvalidGameChoiceError, NotAtTableError

NUMBER_MIN = 1
NUMBER_MAX = 10


def draw_target(rng: random.Random) -> int:
    """The hidden number, drawn once when the seats fill."""
    return rng.randint(NUMBER_MIN, NUMBER_MAX)


def prize_after_rake(pot: int, rake_percent: int) -> tuple[int, int]:
    """Split a pot into (prize, rake); the rake is a percentage rounded down."""
    rake = pot * rake_percent // 100
    return pot - rake, rake


def decide(picks: Mapping[int, int], target: int) -> Optional[int]:
    """The winner's user id, or None when both picks are equally close."""
    (first, first_pick), (second, second_pick) = picks.items()
    first_distance = abs(first_pick - target)
    second_distance = abs(second_pick - target)
    if first_distance == second_distance:
        return None
    return first if first_distance < second_distance else second


@dataclass(frozen=True)
class DuelOutcome:
    target: int
    picks: dict[int, int]
    winner_id: Optional[int]

    @property
    def is_tie(self) -> bool:
        return self.winner_id is None


class DuelRound:
    """The secret picks of one duel."""

    def __init__(self, user_ids: Sequence[int], target: int) -> None:
        if len(user_ids) != 2 or len(set(user_ids)) != 2:
            raise ValueError("a duel needs exactly two distinct players")
        self.user_ids = list(user_ids)
        self.target = target
        self._picks: dict[int, int] = {}

    def submit(self, user_id: int, number: int) -> None:
        if user_id not in self.user_ids:
            raise NotAtTableError()
        if not NUMBER_MIN <= number <= NUMBER_MAX:
            raise InvalidGameChoiceError(
                f"Pick a whole number from {NUMBER_MIN} to {NUMBER_MAX}."
            )
        if user_id in self._picks:
            raise InvalidGameChoiceError("You already locked in a number.")
        self._picks[user_id] = number

    def has_picked(self, user_id: int) -> bool:
        return user_id in self._picks

    @property
    def picked_ids(self) -> list[int]:
        return [uid for uid in self.user_ids if uid in self._picks]

    @property
    def ready(self) -> bool:
        return len(self._picks) == len(self.user_ids)

    def fill_missing(self, rng: random.Random) -> list[int]:
        """Give every player who has not picked a random number; returns who was filled."""
        filled = [uid for uid in self.user_ids if uid not in self._picks]
        for uid in filled:
            self._picks[uid] = rng.randint(NUMBER_MIN, NUMBER_MAX)
        return filled

    def outcome(self) -> DuelOutcome:
        if not self.ready:
            raise ValueError("both players must have picked")
        picks = {uid: self._picks[uid] for uid in self.user_ids}
        return DuelOutcome(self.target, picks, decide(picks, self.target))
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_duel_engine.py -q`
Expected: all pass (the parametrized test counts as four).

- [ ] **Step 5: Lint and commit**

```powershell
python -m ruff check .; python -m ruff format .; python -m ruff format --check .
git add src/yokai/gambling/engines/duel.py tests/test_duel_engine.py
git commit -m "feat(tables): add the pick-a-number duel engine" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>" -m "Claude-Session: https://claude.ai/code/session_01BxUkTAJGP4uNByTtyvj8n9"
```

---

### Task 7: `TableManager`

**Files:**
- Create: `src/yokai/gambling/tables.py`
- Create: `tests/table_helpers.py`
- Create: `tests/test_table_manager.py`

**Interfaces:**
- Consumes: `Lobby`, `LobbyState`, `GameSpec`, `GAME_SPECS`, `Seat` (Task 5); `DuelRound`, `DuelOutcome`, `draw_target`, `prize_after_rake` (Task 6); `Database.place_bet/refund_bet/settle_lobby/set_table_message/set_table_inactive` (Tasks 3-4); `ActiveGames`; `validate_bet`; `new_rng`.
- Produces:
  - `BeginOutcome` enum: `STARTED`, `CANCELLED`, `IGNORED`.
  - `DuelResult(outcome: DuelOutcome, prize: int, rake: int, balances: dict[int, int])`.
  - `TableManager(db, active_games, *, welcome_bonus, min_bet, max_bet, rake_percent, join_seconds, pick_seconds, specs=None, clock=time.time, rng_factory=new_rng)` with public attributes `lobbies`, `rake_percent`, `pick_seconds`, `clock`, `rng_factory` and methods:
    `spec(game) -> GameSpec`, `lobby_for(channel_id) -> Optional[Lobby]`, `is_busy(channel_id) -> bool`, `check_can_open(channel_id, user_id) -> None`, `open_table(channel_id, game, host_id, stake, announce) -> Lobby`, `join(lobby, user_id, stake=None) -> Seat`, `leave(lobby, user_id) -> None`, `cancel(lobby, user_id, *, is_admin) -> None`, `begin(lobby) -> BeginOutcome`, `start_early(lobby, user_id) -> BeginOutcome`, `abort(lobby) -> None`, `settle(lobby, payouts: Mapping[int, int]) -> dict[int, int]` (user id to new balance), `refund_round(lobby) -> dict[int, int]`, `resolve_duel(lobby, duel) -> DuelResult`.
  - `announce` is `Callable[[Lobby], Awaitable[int]]`: posts the lobby message and returns its id.

- [ ] **Step 1: Write the test helpers and failing tests**

Create `tests/table_helpers.py`:
```python
"""Shared fixtures for the multiplayer table tests: a real database and a wired TableManager."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Iterable, Mapping, Optional

from yokai.gambling.lobby import GameSpec, Lobby
from yokai.gambling.sessions import ActiveGames
from yokai.gambling.tables import TableManager
from yokai.storage.db import Database

NOW = 1_000_000.0
CHANNEL = 10
ANNOUNCED_MESSAGE_ID = 777


async def announce_ok(lobby: Lobby) -> int:
    return ANNOUNCED_MESSAGE_ID


@asynccontextmanager
async def world(
    tmp_path: Path,
    *,
    users: Iterable[int] = (1, 2, 3),
    balance: int = 1000,
    specs: Optional[Mapping[str, GameSpec]] = None,
    rake_percent: int = 2,
    game: str = "duel",
) -> AsyncIterator[tuple[Database, TableManager, ActiveGames]]:
    db = Database(tmp_path / "tables.db")
    await db.initialize()
    try:
        for user_id in users:
            await db.ensure_wallet(user_id, balance)
        await db.set_multiplayer_channel(CHANNEL, game, 1)
        active = ActiveGames()
        manager = TableManager(
            db,
            active,
            welcome_bonus=0,
            min_bet=10,
            max_bet=5000,
            rake_percent=rake_percent,
            join_seconds=60,
            pick_seconds=45,
            specs=specs,
            clock=lambda: NOW,
        )
        yield db, manager, active
    finally:
        await db.close()


async def balance_of(db: Database, user_id: int) -> int:
    wallet = await db.get_wallet(user_id)
    assert wallet is not None
    return wallet.balance
```
Create `tests/test_table_manager.py`:
```python
"""TableManager: opening, filling, running and closing tables against a real database."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from tests.table_helpers import ANNOUNCED_MESSAGE_ID, CHANNEL, NOW, announce_ok, balance_of, world
from yokai.errors import (
    AlreadyInGameError,
    BetOutOfRangeError,
    InsufficientFundsError,
    InvalidGameChoiceError,
    LobbyClosedError,
    NotAtTableError,
    NotHostError,
    TableBusyError,
)
from yokai.gambling.engines.duel import DuelRound
from yokai.gambling.lobby import GameSpec, LobbyState
from yokai.gambling.tables import BeginOutcome

TRIO = GameSpec("duel", "Trio", min_players=2, max_players=3)  # registered under the duel key


# ---- opening ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_opening_a_table_escrows_the_host_and_records_the_message(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, active):
        lobby = await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        assert lobby.state is LobbyState.OPEN and lobby.message_id == ANNOUNCED_MESSAGE_ID
        assert lobby.host_seat.stake == 100 and lobby.closes_at == NOW + 60
        assert await balance_of(db, 1) == 900
        assert active.is_active(1) and manager.lobby_for(CHANNEL) is lobby
        row = await db.get_multiplayer_channel(CHANNEL)
        assert row is not None and (row.table_message_id, row.table_active) == (777, True)


@pytest.mark.asyncio
async def test_a_second_table_is_refused_while_one_is_live_and_costs_nothing(
    tmp_path: Path,
) -> None:
    async with world(tmp_path) as (db, manager, active):
        await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        with pytest.raises(TableBusyError):
            await manager.open_table(CHANNEL, "duel", 2, 100, announce_ok)
        assert await balance_of(db, 2) == 1000 and not active.is_active(2)


@pytest.mark.asyncio
async def test_two_simultaneous_requests_open_exactly_one_table(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, _):
        results = await asyncio.gather(
            manager.open_table(CHANNEL, "duel", 1, 100, announce_ok),
            manager.open_table(CHANNEL, "duel", 2, 100, announce_ok),
            return_exceptions=True,
        )
        assert sum(isinstance(r, TableBusyError) for r in results) == 1
        assert await balance_of(db, 1) + await balance_of(db, 2) == 2000 - 100


@pytest.mark.asyncio
async def test_a_new_table_may_open_once_the_old_one_has_ended(tmp_path: Path) -> None:
    async with world(tmp_path) as (_, manager, _active):
        first = await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        await manager.cancel(first, 1, is_admin=False)
        second = await manager.open_table(CHANNEL, "duel", 2, 100, announce_ok)
        assert manager.lobby_for(CHANNEL) is second


@pytest.mark.asyncio
async def test_a_stake_out_of_range_costs_nothing(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, active):
        for stake in (9, 5001):
            with pytest.raises(BetOutOfRangeError):
                await manager.open_table(CHANNEL, "duel", 1, stake, announce_ok)
        assert await balance_of(db, 1) == 1000 and not active.is_active(1)


@pytest.mark.asyncio
async def test_a_member_already_in_a_game_cannot_host(tmp_path: Path) -> None:
    async with world(tmp_path) as (_, manager, active):
        active.claim(1)
        with pytest.raises(AlreadyInGameError):
            await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)


@pytest.mark.asyncio
async def test_not_enough_renown_to_host_leaves_no_claim(tmp_path: Path) -> None:
    async with world(tmp_path, balance=50) as (db, manager, active):
        with pytest.raises(InsufficientFundsError):
            await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        assert await balance_of(db, 1) == 50 and not active.is_active(1)
        assert manager.lobby_for(CHANNEL) is None


@pytest.mark.asyncio
async def test_a_failed_announcement_refunds_the_host(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, active):
        failing = AsyncMock(side_effect=RuntimeError("discord is down"))
        with pytest.raises(RuntimeError):
            await manager.open_table(CHANNEL, "duel", 1, 100, failing)
        assert await balance_of(db, 1) == 1000 and not active.is_active(1)
        assert not manager.is_busy(CHANNEL)
        assert await db.refund_open_bets() == 0


def test_an_unknown_game_is_an_invalid_choice() -> None:
    from yokai.gambling.tables import TableManager

    manager = TableManager(
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        welcome_bonus=0,
        min_bet=10,
        max_bet=5000,
        rake_percent=2,
        join_seconds=60,
        pick_seconds=45,
    )
    with pytest.raises(InvalidGameChoiceError):
        manager.spec("nonsense")


@pytest.mark.asyncio
async def test_check_can_open_reports_busy_tables_and_busy_members(tmp_path: Path) -> None:
    async with world(tmp_path) as (_, manager, active):
        manager.check_can_open(CHANNEL, 1)  # fine
        active.claim(2)
        with pytest.raises(AlreadyInGameError):
            manager.check_can_open(CHANNEL, 2)
        await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        with pytest.raises(TableBusyError):
            manager.check_can_open(CHANNEL, 3)


# ---- joining and leaving ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_duel_guest_pays_the_hosts_stake(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, active):
        lobby = await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        seat = await manager.join(lobby, 2, None)
        assert seat.stake == 100 and lobby.is_full and lobby.pot == 200
        assert await balance_of(db, 2) == 900 and active.is_active(2)


@pytest.mark.asyncio
async def test_a_full_table_refuses_more_players(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, active):
        lobby = await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        await manager.join(lobby, 2, None)
        with pytest.raises(LobbyClosedError, match="full"):
            await manager.join(lobby, 3, None)
        assert await balance_of(db, 3) == 1000 and not active.is_active(3)


@pytest.mark.asyncio
async def test_joining_twice_is_refused(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, _):
        lobby = await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        with pytest.raises(AlreadyInGameError):
            await manager.join(lobby, 1, None)
        assert await balance_of(db, 1) == 900


@pytest.mark.asyncio
async def test_a_guest_who_cannot_afford_the_stake_gets_no_seat_and_no_claim(
    tmp_path: Path,
) -> None:
    async with world(tmp_path) as (db, manager, active):
        await db.ensure_wallet(4, 40)
        lobby = await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        with pytest.raises(InsufficientFundsError):
            await manager.join(lobby, 4, None)
        assert 4 not in lobby.seats and not active.is_active(4)
        assert await balance_of(db, 4) == 40


@pytest.mark.asyncio
async def test_a_guest_in_a_variable_stake_game_picks_their_own_stake(tmp_path: Path) -> None:
    async with world(tmp_path, specs={"duel": TRIO}) as (db, manager, _):
        lobby = await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        seat = await manager.join(lobby, 2, 250)
        assert seat.stake == 250 and lobby.pot == 350
        with pytest.raises(BetOutOfRangeError):
            await manager.join(lobby, 3, 5)


@pytest.mark.asyncio
async def test_leaving_refunds_and_frees_the_seat(tmp_path: Path) -> None:
    async with world(tmp_path, specs={"duel": TRIO}) as (db, manager, active):
        lobby = await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        await manager.join(lobby, 2, 100)
        await manager.leave(lobby, 2)
        assert 2 not in lobby.seats and await balance_of(db, 2) == 1000
        assert not active.is_active(2)


@pytest.mark.asyncio
async def test_the_host_cannot_leave_and_strangers_cannot_either(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, _):
        lobby = await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        with pytest.raises(LobbyClosedError, match="host"):
            await manager.leave(lobby, 1)
        with pytest.raises(NotAtTableError):
            await manager.leave(lobby, 3)
        assert await balance_of(db, 1) == 900


# ---- cancelling, beginning, aborting ----------------------------------------------------


@pytest.mark.asyncio
async def test_the_host_can_cancel_an_open_table_and_everyone_is_refunded(tmp_path: Path) -> None:
    async with world(tmp_path, specs={"duel": TRIO}) as (db, manager, active):
        lobby = await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        await manager.join(lobby, 2, 100)
        await manager.cancel(lobby, 1, is_admin=False)
        assert lobby.state is LobbyState.CANCELLED
        assert (await balance_of(db, 1), await balance_of(db, 2)) == (1000, 1000)
        assert not active.is_active(1) and not active.is_active(2)
        row = await db.get_multiplayer_channel(CHANNEL)
        assert row is not None and row.table_active is False
        assert await db.refund_open_bets() == 0


@pytest.mark.asyncio
async def test_an_admin_can_cancel_but_another_member_cannot(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, _):
        lobby = await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        with pytest.raises(NotHostError):
            await manager.cancel(lobby, 3, is_admin=False)
        assert lobby.state is LobbyState.OPEN
        await manager.cancel(lobby, 3, is_admin=True)
        assert lobby.state is LobbyState.CANCELLED and await balance_of(db, 1) == 1000


@pytest.mark.asyncio
async def test_a_started_table_cannot_be_cancelled(tmp_path: Path) -> None:
    async with world(tmp_path) as (_, manager, _active):
        lobby = await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        await manager.join(lobby, 2, None)
        assert await manager.begin(lobby) is BeginOutcome.STARTED
        with pytest.raises(LobbyClosedError, match="started"):
            await manager.cancel(lobby, 1, is_admin=True)
        assert lobby.state is LobbyState.RUNNING


@pytest.mark.asyncio
async def test_beginning_with_enough_players_starts_the_table(tmp_path: Path) -> None:
    async with world(tmp_path) as (_, manager, _active):
        lobby = await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        await manager.join(lobby, 2, None)
        assert await manager.begin(lobby) is BeginOutcome.STARTED
        assert lobby.state is LobbyState.RUNNING
        assert await manager.begin(lobby) is BeginOutcome.IGNORED


@pytest.mark.asyncio
async def test_beginning_below_the_minimum_cancels_and_refunds(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, active):
        lobby = await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        assert await manager.begin(lobby) is BeginOutcome.CANCELLED
        assert lobby.state is LobbyState.CANCELLED
        assert await balance_of(db, 1) == 1000 and not active.is_active(1)
        assert await manager.begin(lobby) is BeginOutcome.IGNORED


@pytest.mark.asyncio
async def test_only_the_host_can_start_early_and_only_with_enough_players(
    tmp_path: Path,
) -> None:
    async with world(tmp_path, specs={"duel": TRIO}) as (_, manager, _active):
        lobby = await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        with pytest.raises(LobbyClosedError, match="Not enough"):
            await manager.start_early(lobby, 1)
        await manager.join(lobby, 2, 100)
        with pytest.raises(NotHostError):
            await manager.start_early(lobby, 2)
        assert await manager.start_early(lobby, 1) is BeginOutcome.STARTED


@pytest.mark.asyncio
async def test_aborting_a_running_table_refunds_everyone_and_is_idempotent(
    tmp_path: Path,
) -> None:
    async with world(tmp_path) as (db, manager, active):
        lobby = await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        await manager.join(lobby, 2, None)
        await manager.begin(lobby)
        await manager.abort(lobby)
        await manager.abort(lobby)
        assert lobby.state is LobbyState.CANCELLED
        assert (await balance_of(db, 1), await balance_of(db, 2)) == (1000, 1000)
        assert not active.is_active(1) and not active.is_active(2)
        assert await db.get_pot() == 0


# ---- settling ---------------------------------------------------------------------------


async def _running_duel(manager, stake: int = 100):  # type: ignore[no-untyped-def]
    lobby = await manager.open_table(CHANNEL, "duel", 1, stake, announce_ok)
    await manager.join(lobby, 2, None)
    await manager.begin(lobby)
    return lobby


@pytest.mark.asyncio
async def test_settling_pays_the_winner_and_credits_only_the_rake(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, active):
        lobby = await _running_duel(manager)
        balances = await manager.settle(lobby, {1: 196, 2: 0})
        assert balances == {1: 1096, 2: 900}
        assert lobby.state is LobbyState.SETTLED
        assert await db.get_pot() == 4
        assert not active.is_active(1) and not active.is_active(2)
        row = await db.get_multiplayer_channel(CHANNEL)
        assert row is not None and row.table_active is False
        assert await db.refund_open_bets() == 0


@pytest.mark.asyncio
async def test_settling_needs_a_payout_for_every_seat(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, _):
        lobby = await _running_duel(manager)
        with pytest.raises(ValueError):
            await manager.settle(lobby, {1: 196})
        assert lobby.state is LobbyState.RUNNING


@pytest.mark.asyncio
async def test_a_failed_settlement_refunds_everyone(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, active):
        lobby = await _running_duel(manager)
        with patch.object(db, "settle_lobby", new=AsyncMock(side_effect=RuntimeError("disk"))):
            with pytest.raises(RuntimeError):
                await manager.settle(lobby, {1: 196, 2: 0})
        assert lobby.state is LobbyState.CANCELLED
        assert (await balance_of(db, 1), await balance_of(db, 2)) == (1000, 1000)
        assert not active.is_active(1) and await db.get_pot() == 0


@pytest.mark.asyncio
async def test_a_settled_table_cannot_be_settled_again(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, _):
        lobby = await _running_duel(manager)
        await manager.settle(lobby, {1: 196, 2: 0})
        with pytest.raises(LobbyClosedError):
            await manager.settle(lobby, {1: 196, 2: 0})
        assert await balance_of(db, 1) == 1096


# ---- the duel ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolving_a_duel_pays_the_closer_pick_minus_the_rake(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, _):
        lobby = await _running_duel(manager)
        duel = DuelRound([1, 2], target=5)
        duel.submit(1, 9)
        duel.submit(2, 4)
        result = await manager.resolve_duel(lobby, duel)
        assert result.outcome.winner_id == 2
        assert (result.prize, result.rake) == (196, 4)
        assert result.balances == {1: 900, 2: 1096}
        assert await db.get_pot() == 4 and lobby.state is LobbyState.SETTLED


@pytest.mark.asyncio
async def test_a_tied_duel_refunds_both_and_adds_nothing_to_the_pot(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, active):
        lobby = await _running_duel(manager)
        duel = DuelRound([1, 2], target=5)
        duel.submit(1, 3)
        duel.submit(2, 7)
        result = await manager.resolve_duel(lobby, duel)
        assert result.outcome.is_tie and (result.prize, result.rake) == (0, 0)
        assert result.balances == {1: 1000, 2: 1000}
        assert await db.get_pot() == 0 and lobby.state is LobbyState.SETTLED
        assert not active.is_active(1) and await db.refund_open_bets() == 0


@pytest.mark.asyncio
async def test_resolving_fills_a_missing_pick_at_random(tmp_path: Path) -> None:
    async with world(tmp_path) as (_, manager, _active):
        lobby = await _running_duel(manager)
        duel = DuelRound([1, 2], target=5)
        duel.submit(1, 5)
        result = await manager.resolve_duel(lobby, duel)
        assert set(result.outcome.picks) == {1, 2}
        assert 1 <= result.outcome.picks[2] <= 10


@pytest.mark.asyncio
async def test_a_zero_rake_pays_the_whole_pot(tmp_path: Path) -> None:
    async with world(tmp_path, rake_percent=0) as (db, manager, _):
        lobby = await _running_duel(manager)
        duel = DuelRound([1, 2], target=5)
        duel.submit(1, 5)
        duel.submit(2, 1)
        result = await manager.resolve_duel(lobby, duel)
        assert (result.prize, result.rake) == (200, 0)
        assert await db.get_pot() == 0 and await balance_of(db, 1) == 1100
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_table_manager.py -q`
Expected: collection ERROR `No module named 'yokai.gambling.tables'`.

- [ ] **Step 3: Implement**

Create `src/yokai/gambling/tables.py`:
```python
"""Opens, fills, runs and closes multiplayer tables; the only code that escrows for them."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Awaitable, Callable, Mapping, Optional

import aiosqlite

from yokai.errors import (
    AlreadyInGameError,
    BetNotOpenError,
    InvalidGameChoiceError,
    LobbyClosedError,
    NotAtTableError,
    NotHostError,
    TableBusyError,
)
from yokai.gambling.engines.duel import DuelOutcome, DuelRound, prize_after_rake
from yokai.gambling.escrow import validate_bet
from yokai.gambling.lobby import GAME_SPECS, GameSpec, Lobby, LobbyState, Seat
from yokai.gambling.rng import new_rng
from yokai.gambling.sessions import ActiveGames

if TYPE_CHECKING:
    from yokai.storage.db import Database

logger = logging.getLogger(__name__)

# Posts the table's message in its channel and returns the new message's id.
Announce = Callable[[Lobby], Awaitable[int]]


class BeginOutcome(Enum):
    STARTED = "started"
    CANCELLED = "cancelled"  # too few players; everyone was refunded
    IGNORED = "ignored"  # the table was no longer open


@dataclass(frozen=True)
class DuelResult:
    outcome: DuelOutcome
    prize: int
    rake: int
    balances: dict[int, int]  # user id -> balance after the duel


class TableManager:
    """Owns every live table. All stake movements for tables go through here."""

    def __init__(
        self,
        db: Database,
        active_games: ActiveGames,
        *,
        welcome_bonus: int,
        min_bet: int,
        max_bet: int,
        rake_percent: int,
        join_seconds: int,
        pick_seconds: int,
        specs: Optional[Mapping[str, GameSpec]] = None,
        clock: Callable[[], float] = time.time,
        rng_factory: Callable[[], random.Random] = new_rng,
    ) -> None:
        self.db = db
        self.active_games = active_games
        self.welcome_bonus = welcome_bonus
        self.min_bet = min_bet
        self.max_bet = max_bet
        self.rake_percent = rake_percent
        self.join_seconds = join_seconds
        self.pick_seconds = pick_seconds
        self.specs: Mapping[str, GameSpec] = specs if specs is not None else GAME_SPECS
        self.clock = clock
        self.rng_factory = rng_factory
        self.lobbies: dict[int, Lobby] = {}
        self._channel_locks: dict[int, asyncio.Lock] = {}

    # ---- lookups -------------------------------------------------------------------------

    def spec(self, game: str) -> GameSpec:
        try:
            return self.specs[game]
        except KeyError:
            raise InvalidGameChoiceError(f"Unknown game: {game}.") from None

    def lobby_for(self, channel_id: int) -> Optional[Lobby]:
        return self.lobbies.get(channel_id)

    def is_busy(self, channel_id: int) -> bool:
        lobby = self.lobbies.get(channel_id)
        return lobby is not None and lobby.is_active

    def check_can_open(self, channel_id: int, user_id: int) -> None:
        """Cheap pre-check used before showing the stake form."""
        if self.is_busy(channel_id):
            raise TableBusyError()
        if self.active_games.is_active(user_id):
            raise AlreadyInGameError()

    def _channel_lock(self, channel_id: int) -> asyncio.Lock:
        return self._channel_locks.setdefault(channel_id, asyncio.Lock())

    # ---- opening -------------------------------------------------------------------------

    async def open_table(
        self, channel_id: int, game: str, host_id: int, stake: int, announce: Announce
    ) -> Lobby:
        """Escrow the host's stake and open a table; `announce` posts its message."""
        spec = self.spec(game)
        validate_bet(stake, self.min_bet, self.max_bet)
        async with self._channel_lock(channel_id):
            if self.is_busy(channel_id):
                raise TableBusyError()
            self.active_games.claim(host_id)
            lobby_id = uuid.uuid4().hex
            try:
                bet_id = await self.db.place_bet(
                    host_id, stake, game, self.welcome_bonus, lobby_id=lobby_id
                )
            except BaseException:
                self.active_games.release(host_id)
                raise
            lobby = Lobby(
                lobby_id,
                spec,
                channel_id,
                host_id,
                closes_at=self.clock() + self.join_seconds * spec.window_factor,
            )
            lobby.add_seat(Seat(host_id, stake, bet_id))
            self.lobbies[channel_id] = lobby
            try:
                lobby.message_id = await announce(lobby)
                await self.db.set_table_message(channel_id, lobby.message_id, active=True)
            except BaseException:
                async with lobby.lock:
                    await self._refund_all_locked(lobby, LobbyState.CANCELLED)
                raise
            return lobby

    # ---- seats ---------------------------------------------------------------------------

    async def join(self, lobby: Lobby, user_id: int, stake: Optional[int] = None) -> Seat:
        """Seat a member, escrowing their stake (the host's stake when the game is equal-stake)."""
        async with lobby.lock:
            if lobby.state is not LobbyState.OPEN:
                raise LobbyClosedError()
            if lobby.is_full:
                raise LobbyClosedError("This table is full.", user_hint=None)
            amount = lobby.host_seat.stake if lobby.spec.equal_stakes or stake is None else stake
            validate_bet(amount, self.min_bet, self.max_bet)
            self.active_games.claim(user_id)
            try:
                bet_id = await self.db.place_bet(
                    user_id, amount, lobby.spec.name, self.welcome_bonus, lobby_id=lobby.id
                )
            except BaseException:
                self.active_games.release(user_id)
                raise
            seat = Seat(user_id, amount, bet_id)
            try:
                lobby.add_seat(seat)
            except BaseException:
                with contextlib.suppress(BetNotOpenError):
                    await self.db.refund_bet(bet_id)
                self.active_games.release(user_id)
                raise
            return seat

    async def leave(self, lobby: Lobby, user_id: int) -> None:
        async with lobby.lock:
            seat = lobby.remove_seat(user_id)  # raises for strangers, the host, closed tables
            try:
                with contextlib.suppress(BetNotOpenError):
                    await self.db.refund_bet(seat.bet_id)
            finally:
                self.active_games.release(user_id)

    # ---- lifecycle -----------------------------------------------------------------------

    async def cancel(self, lobby: Lobby, user_id: int, *, is_admin: bool) -> None:
        """Cancel an unstarted table (host or admin) and refund every seat."""
        async with lobby.lock:
            if lobby.state is LobbyState.RUNNING:
                raise LobbyClosedError("A table that has started can't be cancelled.", user_hint=None)
            if lobby.state is not LobbyState.OPEN:
                raise LobbyClosedError()
            if user_id != lobby.host_id and not is_admin:
                raise NotHostError()
            await self._refund_all_locked(lobby, LobbyState.CANCELLED)

    async def begin(self, lobby: Lobby) -> BeginOutcome:
        """Start an open table, or cancel and refund it when it has too few players."""
        async with lobby.lock:
            if lobby.state is not LobbyState.OPEN:
                return BeginOutcome.IGNORED
            if lobby.can_start:
                lobby.mark_running()
                return BeginOutcome.STARTED
            await self._refund_all_locked(lobby, LobbyState.CANCELLED)
            return BeginOutcome.CANCELLED

    async def start_early(self, lobby: Lobby, user_id: int) -> BeginOutcome:
        if user_id != lobby.host_id:
            raise NotHostError()
        if not lobby.can_start:
            raise LobbyClosedError(
                "Not enough players yet.",
                user_hint=f"This game needs at least {lobby.spec.min_players} players.",
            )
        return await self.begin(lobby)

    async def abort(self, lobby: Lobby) -> None:
        """Refund and cancel a table that hit an error; safe to call more than once."""
        async with lobby.lock:
            if not lobby.is_active:
                return
            await self._refund_all_locked(lobby, LobbyState.CANCELLED)

    async def settle(self, lobby: Lobby, payouts: Mapping[int, int]) -> dict[int, int]:
        """Pay out a running table in one transaction; returns each member's new balance."""
        async with lobby.lock:
            if lobby.state is not LobbyState.RUNNING:
                raise LobbyClosedError()
            if set(payouts) != set(lobby.seats):
                raise ValueError("payouts must cover every seat, and only seats")
            by_bet = {lobby.seats[user_id].bet_id: amount for user_id, amount in payouts.items()}
            try:
                balances = await self.db.settle_lobby(by_bet)
            except BaseException:
                await self._refund_all_locked(lobby, LobbyState.CANCELLED)
                raise
            self._finish(lobby, LobbyState.SETTLED)
            await self._mark_channel_idle(lobby)
            return {user_id: balances[seat.bet_id] for user_id, seat in lobby.seats.items()}

    async def refund_round(self, lobby: Lobby) -> dict[int, int]:
        """End a running table by returning every stake (a tie); returns new balances."""
        async with lobby.lock:
            if lobby.state is not LobbyState.RUNNING:
                raise LobbyClosedError()
            return await self._refund_all_locked(lobby, LobbyState.SETTLED)

    # ---- duel ----------------------------------------------------------------------------

    async def resolve_duel(self, lobby: Lobby, duel: DuelRound) -> DuelResult:
        """Finish a duel: fill missing picks, then pay the winner or refund a tie."""
        duel.fill_missing(self.rng_factory())
        outcome = duel.outcome()
        if outcome.winner_id is None:
            balances = await self.refund_round(lobby)
            return DuelResult(outcome, prize=0, rake=0, balances=balances)
        prize, rake = prize_after_rake(lobby.pot, self.rake_percent)
        payouts = {
            user_id: (prize if user_id == outcome.winner_id else 0) for user_id in lobby.seats
        }
        balances = await self.settle(lobby, payouts)
        return DuelResult(outcome, prize=prize, rake=rake, balances=balances)

    # ---- internals -----------------------------------------------------------------------

    def _finish(self, lobby: Lobby, state: LobbyState) -> None:
        lobby.close(state)
        for user_id in lobby.seats:
            self.active_games.release(user_id)

    async def _refund_all_locked(self, lobby: Lobby, state: LobbyState) -> dict[int, int]:
        """Refund every open stake and close the lobby. The caller holds `lobby.lock`."""
        balances: dict[int, int] = {}
        try:
            for user_id, seat in list(lobby.seats.items()):
                with contextlib.suppress(BetNotOpenError):
                    balances[user_id] = await self.db.refund_bet(seat.bet_id)
        finally:
            if lobby.is_active:
                self._finish(lobby, state)
            await self._mark_channel_idle(lobby)
        return balances

    async def _mark_channel_idle(self, lobby: Lobby) -> None:
        try:
            await self.db.set_table_inactive(lobby.channel_id)
        except aiosqlite.Error as exc:
            logger.error("Could not mark channel %d idle: %s", lobby.channel_id, exc)
```
(`NotAtTableError` is imported for the type of errors raised through `Lobby`; remove the import if ruff flags it as unused.)

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_table_manager.py -q`
Expected: all pass. If `test_two_simultaneous_requests_open_exactly_one_table` fails with both succeeding, the `is_busy` check must happen inside `_channel_lock` after the lock is taken (it is). If a test reports `LobbyClosedError` from `close()` inside `_refund_all_locked`, check `lobby.is_active` guard order.

- [ ] **Step 5: Suite, lint, commit**

```powershell
python -m pytest -q; python -m ruff check .; python -m ruff format .; python -m ruff format --check .
git add src/yokai/gambling/tables.py tests/table_helpers.py tests/test_table_manager.py
git commit -m "feat(tables): add the TableManager with atomic settlement and duel resolution" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>" -m "Claude-Session: https://claude.ai/code/session_01BxUkTAJGP4uNByTtyvj8n9"
```

---

### Task 8: Table embeds and preview samples

**Files:**
- Modify: `src/yokai/ui/embeds.py` (new builders after `gamble_stats`)
- Modify: `scripts/preview_embeds.py` (append samples before the closing `]` of the `samples` list)
- Create: `tests/test_table_embeds.py`

**Interfaces:**
- Produces on `EmbedFactory`: `table_play(game_label, rules, min_bet, max_bet, bot_avatar_url=None)`, `table_lobby(game_label, seats: Sequence[tuple[int, int]], min_players, max_players, closes_ts, pot, bot_avatar_url=None)`, `table_notice(title, description, bot_avatar_url=None)`, `duel_picking(seat_ids: Sequence[int], stake, pot, rake_percent, deadline_ts, picked: Sequence[int], bot_avatar_url=None)`, `duel_result(picks: Mapping[int, int], target, winner_id: Optional[int], prize, rake, bot_avatar_url=None)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_table_embeds.py`:
```python
"""The table embeds obey the layout rules and carry the facts people need."""

from __future__ import annotations

import pytest

from tests.embed_helpers import all_embed_text, emoji_count, total_embed_length
from yokai.theme import COLOR_BRAND, COLOR_IDLE, COLOR_SUCCESS
from yokai.ui.embeds import EmbedFactory


def _embeds() -> list[tuple[str, object]]:
    return [
        ("play", EmbedFactory.table_play("Duel", "Rules here.", 10, 5000)),
        (
            "lobby",
            EmbedFactory.table_lobby("Duel", [(1, 100)], 2, 2, closes_ts=1_700_000_060, pot=100),
        ),
        ("lobby-empty", EmbedFactory.table_lobby("Duel", [], 2, 4, closes_ts=1_700_000_060, pot=0)),
        ("notice", EmbedFactory.table_notice("Table cancelled", "Stakes were refunded.")),
        ("picking", EmbedFactory.duel_picking([1, 2], 100, 200, 2, 1_700_000_045, [1])),
        ("win", EmbedFactory.duel_result({1: 4, 2: 9}, 5, 1, 196, 4)),
        ("tie", EmbedFactory.duel_result({1: 3, 2: 7}, 5, None, 0, 0)),
    ]


@pytest.mark.parametrize("label,embed", _embeds())
def test_every_table_embed_obeys_the_layout_rules(label: str, embed) -> None:  # type: ignore[no-untyped-def]
    assert emoji_count(embed) <= 1, label
    assert total_embed_length(embed) <= 6000, label
    assert len(embed.fields) <= 25, label
    assert "♠" not in all_embed_text(embed) and "♥" not in all_embed_text(embed)


def test_the_play_embed_explains_the_game_and_the_stake_range() -> None:
    embed = EmbedFactory.table_play("Duel", "Rules here.", 10, 5000)
    text = all_embed_text(embed)
    assert "Duel" in text and "Rules here." in text and "Play" in text
    assert "10" in text and "5,000" in text


def test_the_lobby_lists_players_pot_and_a_live_countdown() -> None:
    embed = EmbedFactory.table_lobby("Duel", [(1, 100)], 2, 2, closes_ts=1_700_000_060, pot=100)
    text = all_embed_text(embed)
    assert "<@1>" in text and "100" in text
    assert "<t:1700000060:R>" in text
    assert "1/2" in text


def test_a_notice_is_plain_and_idle_coloured() -> None:
    embed = EmbedFactory.table_notice("Table cancelled", "Stakes were refunded.")
    assert embed.color is not None and embed.color.value == COLOR_IDLE
    assert emoji_count(embed) == 0


def test_the_picking_embed_hides_the_picks_and_shows_who_locked_in() -> None:
    embed = EmbedFactory.duel_picking([1, 2], 100, 200, 2, 1_700_000_045, [1])
    text = all_embed_text(embed)
    assert "<@1>" in text and "<@2>" in text
    assert "Locked in" in text and "<t:1700000045:R>" in text
    assert "hidden" in text.lower() or "secret" in text.lower()


def test_the_result_names_the_winner_the_prize_and_the_hidden_number() -> None:
    embed = EmbedFactory.duel_result({1: 4, 2: 9}, 5, 1, 196, 4)
    text = all_embed_text(embed)
    assert "<@1>" in text and "196" in text and "**5**" in text
    assert "picked **4**" in text and "picked **9**" in text
    assert embed.color is not None and embed.color.value == COLOR_SUCCESS


def test_a_tied_result_says_the_stakes_came_back() -> None:
    embed = EmbedFactory.duel_result({1: 3, 2: 7}, 5, None, 0, 0)
    text = all_embed_text(embed).lower()
    assert "tie" in text and "returned" in text
    assert embed.color is not None and embed.color.value == COLOR_BRAND


def test_a_result_with_no_rake_does_not_mention_one() -> None:
    embed = EmbedFactory.duel_result({1: 4, 2: 9}, 5, 1, 200, 0)
    assert "rake" not in all_embed_text(embed).lower()
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_table_embeds.py -q`
Expected: failures `AttributeError: type object 'EmbedFactory' has no attribute 'table_play'`.

- [ ] **Step 3: Implement**

In `src/yokai/ui/embeds.py` add `Mapping` to the `typing` import if absent, then append these classmethods inside `EmbedFactory` after `gamble_stats`:
```python
    @classmethod
    def table_play(
        cls,
        game_label: str,
        rules: str,
        min_bet: int,
        max_bet: int,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        """The persistent embed in a table channel, above its Play button."""
        fields: list[FieldSpec] = [
            ("Stake", f"**{min_bet:,}** to **{max_bet:,}** Renown", True),
        ]
        return cls._create_base(
            title=f"{Emoji.DICE} {game_label} table",
            description=f"{rules}\
\
Press **Play** to open a table.",
            color=COLOR_BRAND,
            context="Gambling",
            bot_avatar_url=bot_avatar_url,
            fields=_grid(fields),
        )

    @classmethod
    def table_lobby(
        cls,
        game_label: str,
        seats: Sequence[tuple[int, int]],  # (user_id, stake)
        min_players: int,
        max_players: int,
        closes_ts: int,
        pot: int,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        """An open table: who is seated, the pot, and a live countdown to the start."""
        listed = "\
".join(f"<@{user_id}> — **{stake:,}**" for user_id, stake in seats)
        fields: list[FieldSpec] = [
            ("Players", f"**{len(seats)}/{max_players}**", True),
            ("Pot", f"**{pot:,}**", True),
            ("Starts", f"<t:{closes_ts}:R>", True),
        ]
        return cls._create_base(
            title=f"{Emoji.DICE} {game_label} table",
            description=(
                f"**Seats**\
{listed or 'Nobody yet.'}\
\
"
                f"Press **Join** to take a seat. Needs {min_players} to start."
            ),
            color=COLOR_BRAND,
            context="Gambling",
            bot_avatar_url=bot_avatar_url,
            fields=_grid(fields),
        )

    @classmethod
    def table_notice(
        cls, title: str, description: str, bot_avatar_url: Optional[str] = None
    ) -> discord.Embed:
        """A table that ended without a game: cancelled, or closed by a restart."""
        return cls._create_base(
            title=title,
            description=description,
            color=COLOR_IDLE,
            context="Gambling",
            bot_avatar_url=bot_avatar_url,
        )

    @classmethod
    def duel_picking(
        cls,
        seat_ids: Sequence[int],
        stake: int,
        pot: int,
        rake_percent: int,
        deadline_ts: int,
        picked: Sequence[int],
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        """The duel while players choose; picks stay secret, only who has locked in shows."""
        locked = " ".join(f"<@{user_id}>" for user_id in picked) or "Nobody yet."
        fields: list[FieldSpec] = [
            ("Stake", f"**{stake:,}** each", True),
            ("Pot", f"**{pot:,}**", True),
            ("Picks close", f"<t:{deadline_ts}:R>", True),
        ]
        rake_note = f" The house keeps {rake_percent}%." if rake_percent else ""
        return cls._create_base(
            title=f"{Emoji.DICE} Duel",
            description=(
                f"{' vs '.join(f'<@{user_id}>' for user_id in seat_ids)}\
"
                "A hidden number from **1** to **10** has been drawn. Press **Pick your number** "
                "to lock in a secret pick. The pick closest to the hidden number wins the pot."
                f"{rake_note}\
\
**Locked in:** {locked}"
            ),
            color=COLOR_BRAND,
            context="Gambling",
            bot_avatar_url=bot_avatar_url,
            fields=_grid(fields),
        )

    @classmethod
    def duel_result(
        cls,
        picks: Mapping[int, int],
        target: int,
        winner_id: Optional[int],
        prize: int,
        rake: int,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        """A finished duel: the hidden number, both picks, and who won or that it tied."""
        lines = [f"The hidden number was **{target}**."]
        lines.extend(f"<@{user_id}> picked **{number}**" for user_id, number in picks.items())
        fields: list[FieldSpec] = []
        if winner_id is None:
            lines.append("**It's a tie.** Both stakes were returned.")
            color, quip_key = COLOR_BRAND, "game_push"
        else:
            lines.append(f"**<@{winner_id}> wins {prize:,} Renown.**")
            color, quip_key = COLOR_SUCCESS, "game_win"
            fields.append(("Prize", f"**{prize:,}**", True))
            if rake:
                fields.append(("Rake", f"**{rake:,}**", True))
        return cls._create_base(
            title=f"{Emoji.DICE} Duel",
            description="\
".join(lines),
            color=color,
            context="Gambling",
            bot_avatar_url=bot_avatar_url,
            quip=quip_bank.get(quip_key),
            fields=_grid(fields),
        )
```
Ensure `COLOR_IDLE` is imported in `embeds.py` from `yokai.theme` alongside the other colour constants.

`scripts/preview_embeds.py`: before the closing `]` of the `samples` list (right after the `"Gambling record"` entry) add:
```python
        (
            "Table channel (Play embed)",
            EmbedFactory.table_play(
                "Duel", "Two players, one hidden number from 1 to 10.", 10, 5000, bot_avatar_url=av
            ),
        ),
        (
            "Table lobby (open)",
            EmbedFactory.table_lobby(
                "Duel", [(42, 100)], 2, 2, closes_ts=1_900_000_000, pot=100, bot_avatar_url=av
            ),
        ),
        (
            "Table cancelled",
            EmbedFactory.table_notice(
                "Table cancelled",
                "Not enough players joined in time. Every stake was refunded.",
                bot_avatar_url=av,
            ),
        ),
        (
            "Duel (picking)",
            EmbedFactory.duel_picking(
                [42, 43], 100, 200, 2, 1_900_000_045, [42], bot_avatar_url=av
            ),
        ),
        (
            "Duel (win)",
            EmbedFactory.duel_result({42: 4, 43: 9}, 5, 42, 196, 4, bot_avatar_url=av),
        ),
        (
            "Duel (tie)",
            EmbedFactory.duel_result({42: 3, 43: 7}, 5, None, 0, 0, bot_avatar_url=av),
        ),
```
Regenerate the checked-in preview page: `python scripts/preview_embeds.py` (it writes `docs/preview/embeds.html`).

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_table_embeds.py tests/test_preview_embeds.py -q`
Expected: all pass (the preview test requires every sample to obey the layout rules and unique labels).

- [ ] **Step 5: Suite, lint, commit**

```powershell
python -m pytest -q; python -m ruff check .; python -m ruff format .; python -m ruff format --check .
git add src/yokai/ui/embeds.py scripts/preview_embeds.py docs/preview/embeds.html tests/test_table_embeds.py
git commit -m "feat(tables): add the table and duel embeds" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>" -m "Claude-Session: https://claude.ai/code/session_01BxUkTAJGP4uNByTtyvj8n9"
```

---

### Task 9: Discord views (Play, stake form, lobby, duel)

**Files:**
- Create: `src/yokai/ui/table_common.py`
- Create: `src/yokai/ui/table_views.py`
- Create: `src/yokai/ui/duel_views.py`
- Create: `tests/test_table_views.py`
- Create: `tests/test_duel_views.py`

**Interfaces:**
- Consumes: `TableManager` and friends (Task 7), embeds (Task 8), `screen` from `yokai.moderation.gate`, `bot.tables`, `bot.db`, `bot.config`, `bot.moderation`.
- Produces:
  - `table_common`: `avatar_url(bot) -> Optional[str]`, `async reply_error(bot, interaction, err) -> None` (ephemeral error embed), `async dismiss(interaction) -> None` (delete the deferred ephemeral reply), `async delete_message_quietly(channel, message_id) -> None`, `parse_stake(text: str) -> int` (raises `InvalidGameChoiceError`), `NO_MENTIONS = discord.AllowedMentions.none()`.
  - `table_views`: `PLAY_CUSTOM_ID = "yokai:table:play"`; `PlayView(bot)` (persistent); `async handle_play(bot, interaction)`; `StakeModal(bot, title, on_stake)`; `LobbyView(bot, lobby)` with `message`, `build_embed()`, `start()`; `GAME_RUNNERS: dict[str, Callable[[YokaiBot, Lobby, discord.Message], Awaitable[None]]]`.
  - `duel_views`: `DuelView(bot, lobby, duel, deadline_ts)`; `async run_duel(bot, lobby, message) -> None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_table_views.py`:
```python
"""Play button, stake form and lobby view, against a real database and mocked Discord."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from tests.moderation_helpers import fake_moderation
from tests.table_helpers import CHANNEL, announce_ok, balance_of, world
from yokai.errors import InvalidGameChoiceError
from yokai.gambling.lobby import LobbyState
from yokai.ui.table_common import parse_stake
from yokai.ui.table_views import (
    GAME_RUNNERS,
    PLAY_CUSTOM_ID,
    LobbyView,
    PlayView,
    StakeModal,
    handle_play,
)


def make_bot(db, manager) -> MagicMock:  # type: ignore[no-untyped-def]
    bot = MagicMock()
    bot.user = None
    bot.db = db
    bot.tables = manager
    bot.config.economy_welcome_bonus = 0
    bot.config.gambling_min_bet = 10
    bot.moderation = fake_moderation(owner_id=999, admins={555})
    return bot


def make_interaction(user_id: int, channel_id: int = CHANNEL) -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock(spec=discord.Member)
    interaction.user.id = user_id
    interaction.channel_id = channel_id
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.response.send_modal = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    interaction.delete_original_response = AsyncMock()
    return interaction


def make_view(bot, lobby) -> LobbyView:  # type: ignore[no-untyped-def]
    view = LobbyView(bot, lobby)
    view.message = MagicMock()
    view.message.edit = AsyncMock()
    return view


def error_embed_of(mock_send: AsyncMock):  # type: ignore[no-untyped-def]
    return mock_send.await_args.kwargs["embed"]


# ---- parsing ----------------------------------------------------------------------------


@pytest.mark.parametrize("text,expected", [("100", 100), (" 1,000 ", 1000), ("25 ", 25)])
def test_parse_stake_accepts_whole_numbers(text: str, expected: int) -> None:
    assert parse_stake(text) == expected


@pytest.mark.parametrize("text", ["", "abc", "10.5", "-", "1e3"])
def test_parse_stake_rejects_anything_else(text: str) -> None:
    with pytest.raises(InvalidGameChoiceError):
        parse_stake(text)


# ---- the persistent Play button ---------------------------------------------------------


def test_the_play_view_is_persistent_with_a_fixed_custom_id() -> None:
    view = PlayView(MagicMock())
    assert view.timeout is None and view.is_persistent()
    assert [item.custom_id for item in view.children] == [PLAY_CUSTOM_ID]  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_pressing_play_in_an_unconfigured_channel_is_refused_ephemerally(
    tmp_path: Path,
) -> None:
    async with world(tmp_path) as (db, manager, _):
        bot = make_bot(db, manager)
        interaction = make_interaction(1, channel_id=12345)
        with patch("yokai.ui.table_common.send", new=AsyncMock()) as sent:
            await handle_play(bot, interaction)
        interaction.response.send_modal.assert_not_awaited()
        assert sent.await_args.kwargs["ephemeral"] is True
        assert "not set up" in error_embed_of(sent).description


@pytest.mark.asyncio
async def test_pressing_play_while_a_table_is_live_is_refused(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, _):
        await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        bot = make_bot(db, manager)
        interaction = make_interaction(2)
        with patch("yokai.ui.table_common.send", new=AsyncMock()) as sent:
            await handle_play(bot, interaction)
        interaction.response.send_modal.assert_not_awaited()
        assert "already open" in error_embed_of(sent).description
        assert await balance_of(db, 2) == 1000


@pytest.mark.asyncio
async def test_pressing_play_with_too_little_renown_is_refused(tmp_path: Path) -> None:
    async with world(tmp_path, balance=5) as (db, manager, _):
        bot = make_bot(db, manager)
        interaction = make_interaction(1)
        with patch("yokai.ui.table_common.send", new=AsyncMock()) as sent:
            await handle_play(bot, interaction)
        interaction.response.send_modal.assert_not_awaited()
        assert "isn't enough" in error_embed_of(sent).description


@pytest.mark.asyncio
async def test_pressing_play_opens_the_stake_form(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, _):
        bot = make_bot(db, manager)
        interaction = make_interaction(1)
        await handle_play(bot, interaction)
        interaction.response.send_modal.assert_awaited_once()
        assert isinstance(interaction.response.send_modal.await_args.args[0], StakeModal)


@pytest.mark.asyncio
async def test_submitting_a_stake_opens_the_table_and_clears_the_old_message(
    tmp_path: Path,
) -> None:
    async with world(tmp_path) as (db, manager, active):
        await db.set_table_message(CHANNEL, 4242, active=False)
        bot = make_bot(db, manager)
        interaction = make_interaction(1)
        channel = MagicMock()
        sent_message = MagicMock(spec=discord.Message)
        sent_message.id = 9001
        channel.send = AsyncMock(return_value=sent_message)
        channel.get_partial_message = MagicMock()
        old = MagicMock()
        old.delete = AsyncMock()
        channel.get_partial_message.return_value = old
        interaction.channel = channel

        await handle_play(bot, interaction)
        modal = interaction.response.send_modal.await_args.args[0]
        modal.stake._value = "150"  # type: ignore[attr-defined]
        submit = make_interaction(1)
        submit.channel = channel
        with patch.object(LobbyView, "start") as start_window:
            await modal.on_submit(submit)
        start_window.assert_called_once()

        channel.get_partial_message.assert_called_once_with(4242)
        old.delete.assert_awaited_once()
        lobby = manager.lobby_for(CHANNEL)
        assert lobby is not None and lobby.message_id == 9001
        assert await balance_of(db, 1) == 850 and active.is_active(1)
        row = await db.get_multiplayer_channel(CHANNEL)
        assert row is not None and (row.table_message_id, row.table_active) == (9001, True)
        submit.delete_original_response.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_bad_stake_is_an_ephemeral_error_and_costs_nothing(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, _):
        bot = make_bot(db, manager)
        interaction = make_interaction(1)
        await handle_play(bot, interaction)
        modal = interaction.response.send_modal.await_args.args[0]
        modal.stake._value = "lots"  # type: ignore[attr-defined]
        submit = make_interaction(1)
        with patch("yokai.ui.table_common.send", new=AsyncMock()) as sent:
            await modal.on_submit(submit)
        assert sent.await_args.kwargs["ephemeral"] is True
        assert manager.lobby_for(CHANNEL) is None and await balance_of(db, 1) == 1000


# ---- the lobby --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_duel_lobby_has_no_start_button(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, _):
        lobby = await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        view = make_view(make_bot(db, manager), lobby)
        labels = [getattr(item, "label", None) for item in view.children]
        assert "Start" not in labels and {"Join", "Leave", "Cancel"} <= set(labels)


@pytest.mark.asyncio
async def test_joining_a_duel_fills_the_table_and_starts_the_game(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, _):
        lobby = await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        bot = make_bot(db, manager)
        view = make_view(bot, lobby)
        runner = AsyncMock()
        with patch.dict(GAME_RUNNERS, {"duel": runner}):
            await view.join_button.callback(make_interaction(2))
        assert lobby.state is LobbyState.RUNNING and lobby.is_full
        runner.assert_awaited_once()
        assert await balance_of(db, 2) == 900


@pytest.mark.asyncio
async def test_joining_with_too_little_renown_replies_with_an_error_and_keeps_the_table_open(
    tmp_path: Path,
) -> None:
    async with world(tmp_path) as (db, manager, _):
        await db.ensure_wallet(4, 30)
        lobby = await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        view = make_view(make_bot(db, manager), lobby)
        with patch("yokai.ui.table_common.send", new=AsyncMock()) as sent:
            await view.join_button.callback(make_interaction(4))
        assert sent.await_args.kwargs["ephemeral"] is True
        assert lobby.state is LobbyState.OPEN and 4 not in lobby.seats


@pytest.mark.asyncio
async def test_only_the_host_or_an_admin_can_cancel(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, _):
        lobby = await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        view = make_view(make_bot(db, manager), lobby)
        with patch("yokai.ui.table_common.send", new=AsyncMock()) as sent:
            await view.cancel_button.callback(make_interaction(3))
        assert "Only the host" in error_embed_of(sent).description
        assert lobby.state is LobbyState.OPEN

        await view.cancel_button.callback(make_interaction(555))  # an admin
        assert lobby.state is LobbyState.CANCELLED and await balance_of(db, 1) == 1000
        view.message.edit.assert_awaited()


@pytest.mark.asyncio
async def test_the_window_ending_below_the_minimum_cancels_and_refunds(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, active):
        lobby = await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        view = make_view(make_bot(db, manager), lobby)
        await view._begin()
        assert lobby.state is LobbyState.CANCELLED
        assert await balance_of(db, 1) == 1000 and not active.is_active(1)
        embed = view.message.edit.await_args.kwargs["embed"]
        assert "cancelled" in embed.title.lower()


@pytest.mark.asyncio
async def test_leaving_removes_the_guest_and_refunds(tmp_path: Path) -> None:
    from tests.test_table_manager import TRIO

    async with world(tmp_path, specs={"duel": TRIO}) as (db, manager, _):
        lobby = await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        await manager.join(lobby, 2, 100)
        view = make_view(make_bot(db, manager), lobby)
        await view.leave_button.callback(make_interaction(2))
        assert 2 not in lobby.seats and await balance_of(db, 2) == 1000


@pytest.mark.asyncio
async def test_a_variable_stake_table_shows_a_start_button_and_the_host_can_use_it(
    tmp_path: Path,
) -> None:
    from tests.test_table_manager import TRIO

    async with world(tmp_path, specs={"duel": TRIO}) as (db, manager, _):
        lobby = await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        await manager.join(lobby, 2, 100)
        view = make_view(make_bot(db, manager), lobby)
        assert "Start" in [getattr(item, "label", None) for item in view.children]
        runner = AsyncMock()
        with patch.dict(GAME_RUNNERS, {"duel": runner}):
            await view.start_button.callback(make_interaction(1))
        assert lobby.state is LobbyState.RUNNING
        runner.assert_awaited_once()
```
Create `tests/test_duel_views.py`:
```python
"""The duel picking phase and its resolution."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from tests.moderation_helpers import fake_moderation
from tests.table_helpers import CHANNEL, announce_ok, balance_of, world
from yokai.gambling.engines.duel import DuelRound
from yokai.gambling.lobby import LobbyState
from yokai.ui.duel_views import DuelView, PickSelect, run_duel


def make_bot(db, manager) -> MagicMock:  # type: ignore[no-untyped-def]
    bot = MagicMock()
    bot.user = None
    bot.db = db
    bot.tables = manager
    bot.moderation = fake_moderation(owner_id=999)
    return bot


def make_message() -> MagicMock:
    message = MagicMock(spec=discord.Message)
    message.edit = AsyncMock()
    return message


def make_interaction(user_id: int) -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock(spec=discord.Member)
    interaction.user.id = user_id
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.edit_message = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


async def _running(manager):  # type: ignore[no-untyped-def]
    lobby = await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
    await manager.join(lobby, 2, None)
    await manager.begin(lobby)
    return lobby


@pytest.mark.asyncio
async def test_a_stranger_cannot_open_the_pick_prompt(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, _):
        lobby = await _running(manager)
        view = DuelView(make_bot(db, manager), lobby, DuelRound([1, 2], 5), deadline_ts=1)
        view.message = make_message()
        with patch("yokai.ui.table_common.send", new=AsyncMock()) as sent:
            await view.pick_button.callback(make_interaction(3))
        assert sent.await_args.kwargs["ephemeral"] is True
        assert "not seated" in sent.await_args.kwargs["embed"].description


@pytest.mark.asyncio
async def test_a_seated_player_gets_a_private_prompt(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, _):
        lobby = await _running(manager)
        view = DuelView(make_bot(db, manager), lobby, DuelRound([1, 2], 5), deadline_ts=1)
        view.message = make_message()
        interaction = make_interaction(1)
        with patch("yokai.ui.duel_views.send", new=AsyncMock()) as sent:
            await view.pick_button.callback(interaction)
        assert sent.await_args.kwargs["ephemeral"] is True
        assert sent.await_args.kwargs["view"] is not None


@pytest.mark.asyncio
async def test_choosing_a_number_locks_it_in_and_updates_the_table(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, _):
        lobby = await _running(manager)
        duel = DuelRound([1, 2], 5)
        view = DuelView(make_bot(db, manager), lobby, duel, deadline_ts=1)
        view.message = make_message()
        select = PickSelect(view)
        select._values = ["7"]  # type: ignore[attr-defined]
        interaction = make_interaction(1)
        await select.callback(interaction)
        assert duel.has_picked(1) and not view.all_picked.is_set()
        interaction.response.edit_message.assert_awaited_once()
        view.message.edit.assert_awaited()  # "locked in" shown on the table, the number is not


@pytest.mark.asyncio
async def test_the_second_pick_wakes_the_runner(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, _):
        lobby = await _running(manager)
        duel = DuelRound([1, 2], 5)
        view = DuelView(make_bot(db, manager), lobby, duel, deadline_ts=1)
        view.message = make_message()
        duel.submit(1, 3)
        select = PickSelect(view)
        select._values = ["8"]  # type: ignore[attr-defined]
        await select.callback(make_interaction(2))
        assert view.all_picked.is_set()


@pytest.mark.asyncio
async def test_run_duel_resolves_when_both_players_pick(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, active):
        manager.rng_factory = lambda: __import__("random").Random(1)
        lobby = await _running(manager)
        message = make_message()
        bot = make_bot(db, manager)

        original_init = DuelView.__init__

        def init(self, *a, **kw):  # type: ignore[no-untyped-def]
            original_init(self, *a, **kw)
            self.duel.submit(1, 5)
            self.duel.submit(2, 5)  # same pick: always a tie
            self.all_picked.set()

        with patch.object(DuelView, "__init__", init):
            await run_duel(bot, lobby, message)

        assert lobby.state is LobbyState.SETTLED
        assert (await balance_of(db, 1), await balance_of(db, 2)) == (1000, 1000)
        final = message.edit.await_args.kwargs
        assert "tie" in final["embed"].description.lower() and final["view"] is None
        assert not active.is_active(1)


@pytest.mark.asyncio
async def test_run_duel_fills_missing_picks_when_time_runs_out(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, _):
        manager.pick_seconds = 0.05  # type: ignore[assignment]
        lobby = await _running(manager)
        message = make_message()
        await run_duel(make_bot(db, manager), lobby, message)
        assert lobby.state is LobbyState.SETTLED
        assert await balance_of(db, 1) + await balance_of(db, 2) in (2000, 2000 - 4)


@pytest.mark.asyncio
async def test_run_duel_refunds_everyone_if_something_breaks(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, active):
        lobby = await _running(manager)
        message = make_message()
        message.edit = AsyncMock(side_effect=RuntimeError("discord broke"))
        with pytest.raises(RuntimeError):
            await run_duel(make_bot(db, manager), lobby, message)
        assert lobby.state is LobbyState.CANCELLED
        assert (await balance_of(db, 1), await balance_of(db, 2)) == (1000, 1000)
        assert not active.is_active(1) and not active.is_active(2)
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_table_views.py tests/test_duel_views.py -q`
Expected: collection ERRORs `No module named 'yokai.ui.table_common'`.

- [ ] **Step 3: Implement the shared helpers**

Create `src/yokai/ui/table_common.py`:
```python
"""Small helpers shared by the table views."""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING, Any, Optional

import discord

from yokai.errors import InvalidGameChoiceError
from yokai.ui.embeds import EmbedFactory, send

if TYPE_CHECKING:
    from yokai.bot import YokaiBot

logger = logging.getLogger(__name__)

NO_MENTIONS = discord.AllowedMentions.none()


def avatar_url(bot: YokaiBot) -> Optional[str]:
    return str(bot.user.display_avatar.url) if bot.user else None


async def reply_error(bot: YokaiBot, interaction: discord.Interaction, err: Exception) -> None:
    """Answer an interaction with an ephemeral error embed built from a domain error."""
    embed = EmbedFactory.error(
        message=getattr(err, "message", str(err)),
        title=getattr(err, "title", "That didn't work"),
        user_hint=getattr(err, "user_hint", None),
        bot_avatar_url=avatar_url(bot),
    )
    await send(interaction, embed=embed, ephemeral=True)


async def dismiss(interaction: discord.Interaction) -> None:
    """Remove the deferred ephemeral 'thinking' reply once the real message is up."""
    with contextlib.suppress(discord.HTTPException):
        await interaction.delete_original_response()


async def delete_message_quietly(channel: Any, message_id: int) -> None:
    """Delete one of the bot's own messages; a message that is already gone is fine."""
    try:
        await channel.get_partial_message(message_id).delete()
    except discord.NotFound:
        pass
    except discord.HTTPException as exc:
        logger.warning("Could not delete message %d: %s", message_id, exc)


def parse_stake(text: str) -> int:
    """Turn the text typed into the stake form into a whole number of Renown."""
    cleaned = text.strip().replace(",", "")
    if not cleaned.isdigit():
        raise InvalidGameChoiceError("Enter your stake as a whole number, like 100.")
    return int(cleaned)
```

- [ ] **Step 4: Implement `table_views.py`**

Create `src/yokai/ui/table_views.py`:
```python
"""The persistent Play embed's button, the stake form, and the lobby of an open table."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Awaitable, Callable, Optional

import discord

from yokai.errors import (
    ChannelNotConfiguredError,
    InsufficientFundsError,
    YokaiError,
)
from yokai.gambling.lobby import Lobby
from yokai.gambling.tables import BeginOutcome
from yokai.moderation.gate import screen
from yokai.ui.duel_views import run_duel
from yokai.ui.embeds import EmbedFactory
from yokai.ui.table_common import (
    NO_MENTIONS,
    avatar_url,
    delete_message_quietly,
    dismiss,
    parse_stake,
    reply_error,
)

if TYPE_CHECKING:
    from yokai.bot import YokaiBot

logger = logging.getLogger(__name__)

PLAY_CUSTOM_ID = "yokai:table:play"

StakeCallback = Callable[[discord.Interaction, int], Awaitable[None]]
GameRunner = Callable[["YokaiBot", Lobby, discord.Message], Awaitable[None]]

# One entry per table game: what runs once the table has started.
GAME_RUNNERS: dict[str, GameRunner] = {"duel": run_duel}


class StakeModal(discord.ui.Modal):
    """Asks for a stake, then hands the number to `on_stake`."""

    stake = discord.ui.TextInput(
        label="Your stake (Renown)", placeholder="e.g. 100", min_length=1, max_length=12
    )

    def __init__(self, bot: YokaiBot, title: str, on_stake: StakeCallback) -> None:
        super().__init__(title=title, timeout=300)
        self.bot = bot
        self._on_stake = on_stake

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            await self._on_stake(interaction, parse_stake(self.stake.value))
        except YokaiError as err:
            await reply_error(self.bot, interaction, err)


async def handle_play(bot: YokaiBot, interaction: discord.Interaction) -> None:
    """Play was pressed: check the channel, the table and the member, then ask for a stake."""
    channel_id = interaction.channel_id
    user_id = interaction.user.id
    try:
        row = await bot.db.get_multiplayer_channel(channel_id) if channel_id else None
        if row is None or channel_id is None:
            raise ChannelNotConfiguredError()
        bot.tables.check_can_open(channel_id, user_id)
        wallet = await bot.db.ensure_wallet(user_id, bot.config.economy_welcome_bonus)
        minimum = bot.config.gambling_min_bet
        if wallet.balance < minimum:
            raise InsufficientFundsError(balance=wallet.balance, amount=minimum, action="bet")
        spec = bot.tables.spec(row.game)
    except YokaiError as err:
        await reply_error(bot, interaction, err)
        return

    async def open_table(inter: discord.Interaction, amount: int) -> None:
        holder: dict[str, LobbyView] = {}

        async def announce(lobby: Lobby) -> int:
            channel = inter.channel
            assert channel is not None
            current = await bot.db.get_multiplayer_channel(lobby.channel_id)
            if current is not None and current.table_message_id:
                await delete_message_quietly(channel, current.table_message_id)
            view = LobbyView(bot, lobby)
            message = await channel.send(  # type: ignore[union-attr]
                embed=view.build_embed(), view=view, allowed_mentions=NO_MENTIONS
            )
            view.message = message
            holder["view"] = view
            return message.id

        await bot.tables.open_table(channel_id, row.game, user_id, amount, announce)
        holder["view"].start()
        await dismiss(inter)

    await interaction.response.send_modal(
        StakeModal(bot, f"Open a {spec.label} table", open_table)
    )


class PlayView(discord.ui.View):
    """The always-on Play button under a table channel's embed."""

    def __init__(self, bot: YokaiBot) -> None:
        super().__init__(timeout=None)
        self.bot = bot

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return not await screen(interaction)

    @discord.ui.button(label="Play", style=discord.ButtonStyle.success, custom_id=PLAY_CUSTOM_ID)
    async def play_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await handle_play(self.bot, interaction)


class LobbyView(discord.ui.View):
    """Join / Leave / (Start) / Cancel for one open table; ends when the game starts or closes."""

    def __init__(self, bot: YokaiBot, lobby: Lobby) -> None:
        super().__init__(timeout=None)
        self.bot = bot
        self.lobby = lobby
        self.message: Optional[discord.Message] = None
        self._window: Optional[asyncio.Task[None]] = None
        if lobby.spec.min_players == lobby.spec.max_players:
            self.remove_item(self.start_button)  # nothing to start early on a full-or-nothing game

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return not await screen(interaction)

    def build_embed(self) -> discord.Embed:
        lobby = self.lobby
        return EmbedFactory.table_lobby(
            game_label=lobby.spec.label,
            seats=[(seat.user_id, seat.stake) for seat in lobby.seats.values()],
            min_players=lobby.spec.min_players,
            max_players=lobby.spec.max_players,
            closes_ts=int(lobby.closes_at),
            pot=lobby.pot,
            bot_avatar_url=avatar_url(self.bot),
        )

    def start(self) -> None:
        """Begin counting down the join window."""
        self._window = asyncio.create_task(self._run_window())

    # ---- window and start ----------------------------------------------------------------

    async def _run_window(self) -> None:
        await asyncio.sleep(max(0.0, self.lobby.closes_at - self.bot.tables.clock()))
        await self._begin()

    def _stop_window(self) -> None:
        task, self._window = self._window, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    async def _begin(self) -> None:
        await self._after_begin(await self.bot.tables.begin(self.lobby))

    async def _after_begin(self, outcome: BeginOutcome) -> None:
        if outcome is BeginOutcome.IGNORED:
            return
        self._stop_window()
        self.stop()
        if outcome is BeginOutcome.CANCELLED:
            await self._show(
                EmbedFactory.table_notice(
                    "Table cancelled",
                    "Not enough players joined in time. Every stake was refunded.",
                    bot_avatar_url=avatar_url(self.bot),
                )
            )
            return
        assert self.message is not None
        await GAME_RUNNERS[self.lobby.spec.name](self.bot, self.lobby, self.message)

    async def _show(self, embed: discord.Embed) -> None:
        """Replace the table message with `embed` and no buttons."""
        if self.message is None:
            return
        try:
            await self.message.edit(embed=embed, view=None)
        except discord.HTTPException as exc:
            logger.warning("Could not update table message: %s", exc)

    async def _refresh(self) -> None:
        if self.message is None:
            return
        try:
            await self.message.edit(embed=self.build_embed(), view=self)
        except discord.HTTPException as exc:
            logger.warning("Could not refresh table message: %s", exc)

    # ---- buttons -------------------------------------------------------------------------

    async def _do_join(
        self, interaction: discord.Interaction, stake: Optional[int], *, from_modal: bool
    ) -> None:
        try:
            await self.bot.tables.join(self.lobby, interaction.user.id, stake)
        except YokaiError as err:
            await reply_error(self.bot, interaction, err)
            return
        if from_modal:
            await dismiss(interaction)
        await self._refresh()
        if self.lobby.is_full:
            await self._after_begin(await self.bot.tables.begin(self.lobby))

    @discord.ui.button(label="Join", style=discord.ButtonStyle.success)
    async def join_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        spec = self.lobby.spec
        if spec.equal_stakes:
            await interaction.response.defer()
            await self._do_join(interaction, None, from_modal=False)
            return

        async def on_stake(inter: discord.Interaction, amount: int) -> None:
            await self._do_join(inter, amount, from_modal=True)

        await interaction.response.send_modal(
            StakeModal(self.bot, f"Join the {spec.label} table", on_stake)
        )

    @discord.ui.button(label="Leave", style=discord.ButtonStyle.secondary)
    async def leave_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.defer()
        try:
            await self.bot.tables.leave(self.lobby, interaction.user.id)
        except YokaiError as err:
            await reply_error(self.bot, interaction, err)
            return
        await self._refresh()

    @discord.ui.button(label="Start", style=discord.ButtonStyle.primary)
    async def start_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.defer()
        try:
            outcome = await self.bot.tables.start_early(self.lobby, interaction.user.id)
        except YokaiError as err:
            await reply_error(self.bot, interaction, err)
            return
        await self._after_begin(outcome)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.defer()
        is_admin = await self.bot.moderation.is_admin(interaction.user.id)
        try:
            await self.bot.tables.cancel(self.lobby, interaction.user.id, is_admin=is_admin)
        except YokaiError as err:
            await reply_error(self.bot, interaction, err)
            return
        self._stop_window()
        self.stop()
        await self._show(
            EmbedFactory.table_notice(
                "Table cancelled",
                f"<@{interaction.user.id}> cancelled the table. Every stake was refunded.",
                bot_avatar_url=avatar_url(self.bot),
            )
        )
```
Note: `YokaiError` raised with a non-`done` response uses `send()` (inside `reply_error`), which follows up because the interaction was deferred.

- [ ] **Step 5: Implement `duel_views.py`**

Create `src/yokai/ui/duel_views.py`:
```python
"""The duel's picking phase: a private number prompt, then the reveal."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING

import discord

from yokai.errors import NotAtTableError, YokaiError
from yokai.gambling.engines.duel import NUMBER_MAX, NUMBER_MIN, DuelRound, draw_target
from yokai.gambling.lobby import Lobby
from yokai.moderation.gate import screen
from yokai.ui.embeds import EmbedFactory, send
from yokai.ui.table_common import avatar_url, reply_error

if TYPE_CHECKING:
    from yokai.bot import YokaiBot

logger = logging.getLogger(__name__)


class DuelView(discord.ui.View):
    """The table message while players pick; the pick prompt itself is private to each player."""

    def __init__(self, bot: YokaiBot, lobby: Lobby, duel: DuelRound, deadline_ts: int) -> None:
        super().__init__(timeout=None)
        self.bot = bot
        self.lobby = lobby
        self.duel = duel
        self.deadline_ts = deadline_ts
        self.message: "discord.Message | None" = None
        self.all_picked = asyncio.Event()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return not await screen(interaction)

    def build_embed(self) -> discord.Embed:
        return EmbedFactory.duel_picking(
            seat_ids=list(self.lobby.seats),
            stake=self.lobby.host_seat.stake,
            pot=self.lobby.pot,
            rake_percent=self.bot.tables.rake_percent,
            deadline_ts=self.deadline_ts,
            picked=self.duel.picked_ids,
            bot_avatar_url=avatar_url(self.bot),
        )

    async def record_pick(self, user_id: int, number: int) -> None:
        self.duel.submit(user_id, number)
        if self.duel.ready:
            self.all_picked.set()
        if self.message is not None:
            try:
                await self.message.edit(embed=self.build_embed(), view=self)
            except discord.HTTPException as exc:
                logger.warning("Could not refresh the duel message: %s", exc)

    @discord.ui.button(label="Pick your number", style=discord.ButtonStyle.primary)
    async def pick_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        user_id = interaction.user.id
        if user_id not in self.lobby.seats:
            await reply_error(self.bot, interaction, NotAtTableError())
            return
        if self.duel.has_picked(user_id):
            embed = EmbedFactory.info(
                title="Already locked in",
                description="You already picked. Picks can't be changed.",
                context="Gambling",
                bot_avatar_url=avatar_url(self.bot),
            )
            await send(interaction, embed=embed, ephemeral=True)
            return
        prompt = EmbedFactory.info(
            title="Pick your number",
            description=(
                f"Choose a number from **{NUMBER_MIN}** to **{NUMBER_MAX}**. "
                "It stays secret until the reveal, and you can't change it."
            ),
            context="Gambling",
            bot_avatar_url=avatar_url(self.bot),
        )
        picker = discord.ui.View(timeout=self.bot.tables.pick_seconds)
        picker.add_item(PickSelect(self))
        await send(interaction, embed=prompt, view=picker, ephemeral=True)


class PickSelect(discord.ui.Select):
    """The 1 to 10 dropdown inside a player's private prompt."""

    def __init__(self, duel_view: DuelView) -> None:
        super().__init__(
            placeholder="Choose your number",
            options=[
                discord.SelectOption(label=str(n), value=str(n))
                for n in range(NUMBER_MIN, NUMBER_MAX + 1)
            ],
        )
        self.duel_view = duel_view

    async def callback(self, interaction: discord.Interaction) -> None:
        number = int(self.values[0])
        try:
            await self.duel_view.record_pick(interaction.user.id, number)
        except YokaiError as err:
            await reply_error(self.duel_view.bot, interaction, err)
            return
        locked = EmbedFactory.info(
            title="Locked in",
            description=f"You picked **{number}**. Wait for the reveal.",
            context="Gambling",
            bot_avatar_url=avatar_url(self.duel_view.bot),
        )
        await interaction.response.edit_message(embed=locked, view=None)


async def run_duel(bot: YokaiBot, lobby: Lobby, message: discord.Message) -> None:
    """Run a started duel: collect picks, resolve, reveal. Any failure refunds both stakes."""
    tables = bot.tables
    duel = DuelRound(list(lobby.seats), draw_target(tables.rng_factory()))
    deadline_ts = int(tables.clock() + tables.pick_seconds)
    view = DuelView(bot, lobby, duel, deadline_ts)
    view.message = message
    try:
        await message.edit(embed=view.build_embed(), view=view)
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(view.all_picked.wait(), timeout=tables.pick_seconds)
        result = await tables.resolve_duel(lobby, duel)
    except BaseException:
        await tables.abort(lobby)
        view.stop()
        raise
    view.stop()
    final = EmbedFactory.duel_result(
        picks=result.outcome.picks,
        target=result.outcome.target,
        winner_id=result.outcome.winner_id,
        prize=result.prize,
        rake=result.rake,
        bot_avatar_url=avatar_url(bot),
    )
    try:
        await message.edit(embed=final, view=None)
    except discord.HTTPException as exc:
        logger.warning("Could not show the duel result (stakes are already settled): %s", exc)
```
In `view.message: "discord.Message | None"` the quoted annotation is fine under `from __future__ import annotations`; use `Optional[discord.Message]` with `from typing import Optional` to match the codebase if ruff objects.

- [ ] **Step 6: Run to verify they pass**

Run: `python -m pytest tests/test_table_views.py tests/test_duel_views.py -q`
Expected: all pass. Typical fixes at this step are the private-attribute names noted above, and `patch("yokai.ui.table_common.send", ...)` targets (all error replies go through `table_common.reply_error`, which uses `table_common.send`).

- [ ] **Step 7: Suite, lint, commit**

```powershell
python -m pytest -q; python -m ruff check .; python -m ruff format .; python -m ruff format --check .
git add src/yokai/ui/table_common.py src/yokai/ui/table_views.py src/yokai/ui/duel_views.py tests/test_table_views.py tests/test_duel_views.py
git commit -m "feat(tables): add the Play button, stake form, lobby and duel views" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>" -m "Claude-Session: https://claude.ai/code/session_01BxUkTAJGP4uNByTtyvj8n9"
```

---

### Task 10: `TablesCog`, the `/admin` subcommands and bot wiring

**Files:**
- Create: `src/yokai/cogs/tables.py`
- Modify: `src/yokai/cogs/admin.py` (two subcommands)
- Modify: `src/yokai/bot.py` (`self.tables`, extension load, reconcile on ready)
- Modify: `tests/test_one_emoji_rule.py` (CHECKED list)
- Modify: `tests/test_command_tree.py` (expected admin children), `tests/test_bot_wiring.py`
- Create: `tests/test_tables_cog.py`
- Modify: `tests/test_admin_cog.py` (new tests)

**Interfaces:**
- Consumes: Tasks 1-9.
- Produces: `TablesCog(bot)` with `cog_load()` (registers `PlayView`), `async set_channel(channel: discord.TextChannel, game: str) -> MultiplayerChannel`, `async unset_channel(channel_id: int) -> bool`, `async reconcile() -> None`; `AdminCog.set_multiplayer(interaction, game: Choice[str])`, `AdminCog.unset_multiplayer(interaction)`; `YokaiBot.tables: TableManager`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tables_cog.py`:
```python
"""TablesCog: provisioning a table channel and healing state after a restart."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from tests.table_helpers import CHANNEL, announce_ok, world
from yokai.cogs.tables import TablesCog
from yokai.errors import InvalidGameChoiceError, TableBusyError, TablePermissionError
from yokai.gambling.models import MultiplayerChannel
from yokai.ui.table_views import PLAY_CUSTOM_ID, PlayView


def make_bot(db, manager) -> MagicMock:  # type: ignore[no-untyped-def]
    bot = MagicMock()
    bot.user = None
    bot.db = db
    bot.tables = manager
    bot.config.gambling_min_bet = 10
    bot.config.gambling_max_bet = 5000
    bot.add_view = MagicMock()
    return bot


def make_channel(
    channel_id: int = CHANNEL, *, perms: tuple[bool, bool, bool] = (True, True, True)
) -> MagicMock:
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = channel_id
    channel.name = "tables"
    permissions = MagicMock()
    permissions.view_channel, permissions.send_messages, permissions.embed_links = perms
    channel.permissions_for = MagicMock(return_value=permissions)
    channel.guild = MagicMock()
    sent = MagicMock(spec=discord.Message)
    sent.id = 5000 + channel_id
    channel.send = AsyncMock(return_value=sent)
    partial = MagicMock()
    partial.delete = AsyncMock()
    partial.edit = AsyncMock()
    channel.get_partial_message = MagicMock(return_value=partial)
    channel.fetch_message = AsyncMock()
    return channel


@pytest.mark.asyncio
async def test_loading_the_cog_registers_the_persistent_play_view(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, _):
        bot = make_bot(db, manager)
        await TablesCog(bot).cog_load()
        view = bot.add_view.call_args.args[0]
        assert isinstance(view, PlayView) and view.is_persistent()
        assert view.children[0].custom_id == PLAY_CUSTOM_ID  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_setting_a_channel_posts_the_play_embed_and_stores_it(tmp_path: Path) -> None:
    async with world(tmp_path, users=()) as (db, manager, _):
        await db.clear_multiplayer_channel(CHANNEL)
        bot = make_bot(db, manager)
        channel = make_channel()
        row = await TablesCog(bot).set_channel(channel, "duel")
        assert row == MultiplayerChannel(CHANNEL, "duel", 5000 + CHANNEL, None, False)
        kwargs = channel.send.await_args.kwargs
        assert isinstance(kwargs["view"], PlayView)
        assert kwargs["allowed_mentions"].everyone is False
        assert await db.get_multiplayer_channel(CHANNEL) == row


@pytest.mark.asyncio
async def test_a_missing_permission_is_reported_before_anything_is_posted(tmp_path: Path) -> None:
    async with world(tmp_path, users=()) as (db, manager, _):
        bot = make_bot(db, manager)
        channel = make_channel(perms=(True, False, False))
        with pytest.raises(TablePermissionError) as caught:
            await TablesCog(bot).set_channel(channel, "duel")
        assert "Send Messages" in caught.value.message and "Embed Links" in caught.value.message
        channel.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_unknown_game_is_refused(tmp_path: Path) -> None:
    async with world(tmp_path, users=()) as (db, manager, _):
        bot = make_bot(db, manager)
        with pytest.raises(InvalidGameChoiceError):
            await TablesCog(bot).set_channel(make_channel(), "nonsense")


@pytest.mark.asyncio
async def test_moving_a_game_deletes_the_old_play_and_table_messages(tmp_path: Path) -> None:
    async with world(tmp_path, users=()) as (db, manager, _):
        await db.set_table_message(CHANNEL, 4242, active=False)
        bot = make_bot(db, manager)
        old_channel = make_channel(CHANNEL)
        bot.get_channel = MagicMock(return_value=old_channel)
        new_channel = make_channel(20)
        await TablesCog(bot).set_channel(new_channel, "duel")
        deleted = [c.args[0] for c in old_channel.get_partial_message.call_args_list]
        assert sorted(deleted) == [1, 4242]
        assert await db.get_multiplayer_channel(CHANNEL) is None
        assert (await db.get_multiplayer_channel(20)) is not None


@pytest.mark.asyncio
async def test_a_live_table_blocks_moving_or_unsetting_its_channel(tmp_path: Path) -> None:
    async with world(tmp_path) as (db, manager, _):
        await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        bot = make_bot(db, manager)
        cog = TablesCog(bot)
        with pytest.raises(TableBusyError):
            await cog.set_channel(make_channel(20), "duel")
        with pytest.raises(TableBusyError):
            await cog.unset_channel(CHANNEL)
        assert await db.get_multiplayer_channel(CHANNEL) is not None


@pytest.mark.asyncio
async def test_unsetting_removes_the_row_and_its_messages(tmp_path: Path) -> None:
    async with world(tmp_path, users=()) as (db, manager, _):
        await db.set_table_message(CHANNEL, 4242, active=False)
        bot = make_bot(db, manager)
        channel = make_channel(CHANNEL)
        bot.get_channel = MagicMock(return_value=channel)
        cog = TablesCog(bot)
        assert await cog.unset_channel(CHANNEL) is True
        assert await cog.unset_channel(CHANNEL) is False
        assert sorted(c.args[0] for c in channel.get_partial_message.call_args_list) == [1, 4242]


@pytest.mark.asyncio
async def test_reconcile_rewrites_an_interrupted_table_but_not_a_finished_one(
    tmp_path: Path,
) -> None:
    async with world(tmp_path, users=()) as (db, manager, _):
        await db.set_table_message(CHANNEL, 4242, active=True)  # interrupted by the restart
        await db.set_multiplayer_channel(30, "jackpot", 2)
        await db.set_table_message(30, 4343, active=False)  # finished normally
        bot = make_bot(db, manager)
        channels = {CHANNEL: make_channel(CHANNEL), 30: make_channel(30)}
        for ch in channels.values():
            ch.fetch_message = AsyncMock()  # play embeds still exist
        bot.get_channel = MagicMock(side_effect=lambda cid: channels.get(cid))
        await TablesCog(bot).reconcile()

        interrupted = channels[CHANNEL].get_partial_message.return_value
        interrupted.edit.assert_awaited_once()
        assert "restart" in interrupted.edit.await_args.kwargs["embed"].description.lower()
        assert interrupted.edit.await_args.kwargs["view"] is None
        finished = channels[30].get_partial_message.return_value
        finished.edit.assert_not_awaited()
        row = await db.get_multiplayer_channel(CHANNEL)
        assert row is not None and row.table_active is False


@pytest.mark.asyncio
async def test_reconcile_reposts_a_deleted_play_embed(tmp_path: Path) -> None:
    async with world(tmp_path, users=()) as (db, manager, _):
        bot = make_bot(db, manager)
        channel = make_channel(CHANNEL)
        response = MagicMock(status=404, reason="Not Found")
        channel.fetch_message = AsyncMock(side_effect=discord.NotFound(response, "gone"))
        bot.get_channel = MagicMock(return_value=channel)
        await TablesCog(bot).reconcile()
        channel.send.assert_awaited_once()
        row = await db.get_multiplayer_channel(CHANNEL)
        assert row is not None and row.play_message_id == 5000 + CHANNEL


@pytest.mark.asyncio
async def test_reconcile_survives_a_channel_it_cannot_find(tmp_path: Path) -> None:
    async with world(tmp_path, users=()) as (db, manager, _):
        bot = make_bot(db, manager)
        bot.get_channel = MagicMock(return_value=None)
        response = MagicMock(status=404, reason="Not Found")
        bot.fetch_channel = AsyncMock(side_effect=discord.NotFound(response, "gone"))
        await TablesCog(bot).reconcile()  # must not raise
        assert await db.get_multiplayer_channel(CHANNEL) is not None
```
Append to `tests/test_admin_cog.py` (add `from yokai.errors import TableBusyError` and `from discord import app_commands` to its imports):
```python
def make_text_channel(channel_id: int = 10) -> MagicMock:
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = channel_id
    return channel


@pytest.mark.asyncio
async def test_set_multiplayer_refuses_non_admins() -> None:
    bot = make_bot()
    tables = MagicMock()
    tables.set_channel = AsyncMock()
    bot.get_cog = MagicMock(return_value=tables)
    cog = AdminCog(bot)
    interaction = make_interaction(111)
    interaction.channel = make_text_channel()
    with patch("yokai.cogs.admin.send", new=AsyncMock()) as sent:
        await cog.set_multiplayer.callback(cog, interaction, game=app_commands.Choice(name="Duel", value="duel"))
    tables.set_channel.assert_not_awaited()
    assert sent.await_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_set_multiplayer_dedicates_the_current_channel() -> None:
    bot = make_bot()
    tables = MagicMock()
    tables.set_channel = AsyncMock()
    bot.get_cog = MagicMock(return_value=tables)
    cog = AdminCog(bot)
    interaction = make_interaction(555)
    channel = make_text_channel()
    interaction.channel = channel
    with patch("yokai.cogs.admin.send", new=AsyncMock()) as sent:
        await cog.set_multiplayer.callback(cog, interaction, game=app_commands.Choice(name="Duel", value="duel"))
    tables.set_channel.assert_awaited_once_with(channel, "duel")
    interaction.response.defer.assert_awaited_once()
    assert sent.await_args.kwargs["ephemeral"] is True
    assert "Duel" in sent.await_args.kwargs["embed"].description


@pytest.mark.asyncio
async def test_set_multiplayer_needs_a_text_channel() -> None:
    bot = make_bot()
    tables = MagicMock()
    tables.set_channel = AsyncMock()
    bot.get_cog = MagicMock(return_value=tables)
    cog = AdminCog(bot)
    interaction = make_interaction(555)
    interaction.channel = MagicMock(spec=discord.VoiceChannel)
    with patch("yokai.cogs.admin.send", new=AsyncMock()) as sent:
        await cog.set_multiplayer.callback(cog, interaction, game=app_commands.Choice(name="Duel", value="duel"))
    tables.set_channel.assert_not_awaited()
    assert sent.await_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_set_multiplayer_reports_a_domain_error_ephemerally() -> None:
    bot = make_bot()
    tables = MagicMock()
    tables.set_channel = AsyncMock(side_effect=TableBusyError())
    bot.get_cog = MagicMock(return_value=tables)
    cog = AdminCog(bot)
    interaction = make_interaction(555)
    interaction.channel = make_text_channel()
    with patch("yokai.cogs.admin.send", new=AsyncMock()) as sent:
        await cog.set_multiplayer.callback(cog, interaction, game=app_commands.Choice(name="Duel", value="duel"))
    assert sent.await_args.kwargs["ephemeral"] is True
    assert "already open" in sent.await_args.kwargs["embed"].description


@pytest.mark.asyncio
async def test_unset_multiplayer_reports_whether_anything_was_removed() -> None:
    bot = make_bot()
    tables = MagicMock()
    tables.unset_channel = AsyncMock(side_effect=[True, False])
    bot.get_cog = MagicMock(return_value=tables)
    cog = AdminCog(bot)
    interaction = make_interaction(555)
    interaction.channel_id = 10
    with patch("yokai.cogs.admin.send", new=AsyncMock()) as sent:
        await cog.unset_multiplayer.callback(cog, interaction)
        assert "no longer" in sent.await_args.kwargs["embed"].description.lower()
        await cog.unset_multiplayer.callback(cog, interaction)
        assert "not a table channel" in sent.await_args.kwargs["embed"].description.lower()
    assert sent.await_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_unset_multiplayer_refuses_non_admins() -> None:
    bot = make_bot()
    tables = MagicMock()
    tables.unset_channel = AsyncMock()
    bot.get_cog = MagicMock(return_value=tables)
    cog = AdminCog(bot)
    interaction = make_interaction(111)
    interaction.channel_id = 10
    with patch("yokai.cogs.admin.send", new=AsyncMock()):
        await cog.unset_multiplayer.callback(cog, interaction)
    tables.unset_channel.assert_not_awaited()
```
Update `tests/test_command_tree.py::test_admin_commands_live_under_the_admin_group` so the expected set is `{"economy", "diag", "set_multiplayer", "unset_multiplayer"}`. Add to `tests/test_bot_wiring.py`:
```python
@pytest.mark.asyncio
async def test_the_bot_owns_a_table_manager_wired_from_config() -> None:
    from yokai.gambling.tables import TableManager

    bot = make_bot()
    assert isinstance(bot.tables, TableManager)
    assert bot.tables.active_games is bot.active_games
    assert bot.tables.rake_percent == bot.config.gambling_duel_rake_percent
    assert bot.tables.join_seconds == bot.config.gambling_lobby_join_seconds
    assert bot.tables.pick_seconds == bot.config.gambling_turn_seconds


@pytest.mark.asyncio
async def test_setup_hook_loads_the_tables_extension() -> None:
    bot = make_bot()
    bot.db.initialize = AsyncMock()
    bot.db.refund_open_bets = AsyncMock(return_value=0)
    bot.audio_buffer.start = AsyncMock()
    bot.searcher.warm_up = AsyncMock()
    bot.load_extension = AsyncMock()
    bot.tree.sync = AsyncMock(return_value=[])
    with patch.object(bot.voice_earn_tracker, "start"):
        await bot.setup_hook()
    loaded = [call.args[0] for call in bot.load_extension.await_args_list]
    assert "yokai.cogs.tables" in loaded


@pytest.mark.asyncio
async def test_on_ready_reconciles_the_table_channels_once() -> None:
    bot = make_bot()
    cog = MagicMock()
    cog.reconcile = AsyncMock()
    bot.get_cog = MagicMock(return_value=cog)  # type: ignore[method-assign]
    with patch.object(bot.heartbeat, "start"):
        await bot.on_ready()
        await bot.on_ready()
    cog.reconcile.assert_awaited_once()
```
In `tests/test_one_emoji_rule.py` add to `CHECKED`: `"cogs/admin.py"`, `"cogs/tables.py"`, `"ui/table_common.py"`, `"ui/table_views.py"`, `"ui/duel_views.py"`.

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_tables_cog.py tests/test_admin_cog.py tests/test_bot_wiring.py tests/test_command_tree.py tests/test_one_emoji_rule.py -q`
Expected: collection ERROR for `yokai.cogs.tables`, attribute errors for the new admin commands and `bot.tables`.

- [ ] **Step 3: Implement `cogs/tables.py`**

Create `src/yokai/cogs/tables.py`:
```python
"""Table channels: the persistent Play embed, provisioning and restart healing."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import discord
from discord.ext import commands

from yokai.errors import TableBusyError, TablePermissionError
from yokai.gambling.models import MultiplayerChannel
from yokai.ui.embeds import EmbedFactory
from yokai.ui.table_common import NO_MENTIONS, avatar_url, delete_message_quietly
from yokai.ui.table_views import PlayView

if TYPE_CHECKING:
    from yokai.bot import YokaiBot

logger = logging.getLogger(__name__)


class TablesCog(commands.Cog, name="Tables"):
    """Owns the channels dedicated to multiplayer games."""

    def __init__(self, bot: YokaiBot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        # One persistent view answers every Play button, in every channel, across restarts.
        self.bot.add_view(PlayView(self.bot))

    def _play_embed(self, game: str) -> discord.Embed:
        spec = self.bot.tables.spec(game)
        return EmbedFactory.table_play(
            game_label=spec.label,
            rules=spec.rules,
            min_bet=self.bot.config.gambling_min_bet,
            max_bet=self.bot.config.gambling_max_bet,
            bot_avatar_url=avatar_url(self.bot),
        )

    async def _post_play_message(self, channel: Any, game: str) -> discord.Message:
        return await channel.send(
            embed=self._play_embed(game), view=PlayView(self.bot), allowed_mentions=NO_MENTIONS
        )

    async def _delete_messages_of(self, row: MultiplayerChannel) -> None:
        channel = self.bot.get_channel(row.channel_id)
        if channel is None:
            return
        for message_id in (row.play_message_id, row.table_message_id):
            if message_id:
                await delete_message_quietly(channel, message_id)

    # ---- provisioning --------------------------------------------------------------------

    async def set_channel(self, channel: discord.TextChannel, game: str) -> MultiplayerChannel:
        """Dedicate `channel` to `game` and post its Play embed."""
        self.bot.tables.spec(game)  # unknown games fail here
        permissions = channel.permissions_for(channel.guild.me)
        missing = [
            name
            for name, granted in (
                ("View Channel", permissions.view_channel),
                ("Send Messages", permissions.send_messages),
                ("Embed Links", permissions.embed_links),
            )
            if not granted
        ]
        if missing:
            raise TablePermissionError(missing, channel.name)
        for row in await self.bot.db.list_multiplayer_channels():
            if (row.game == game or row.channel_id == channel.id) and self.bot.tables.is_busy(
                row.channel_id
            ):
                raise TableBusyError()
        message = await self._post_play_message(channel, game)
        try:
            removed = await self.bot.db.set_multiplayer_channel(channel.id, game, message.id)
        except BaseException:
            await delete_message_quietly(channel, message.id)
            raise
        for old in removed:
            await self._delete_messages_of(old)
        return MultiplayerChannel(channel.id, game, message.id, None, False)

    async def unset_channel(self, channel_id: int) -> bool:
        """Stop dedicating a channel; returns False when it was not one."""
        if self.bot.tables.is_busy(channel_id):
            raise TableBusyError()
        row = await self.bot.db.clear_multiplayer_channel(channel_id)
        if row is None:
            return False
        await self._delete_messages_of(row)
        return True

    # ---- restart healing -----------------------------------------------------------------

    async def reconcile(self) -> None:
        """After a restart: close interrupted tables and restore missing Play embeds."""
        for row in await self.bot.db.list_multiplayer_channels():
            channel = self.bot.get_channel(row.channel_id)
            if channel is None:
                try:
                    channel = await self.bot.fetch_channel(row.channel_id)
                except discord.HTTPException as exc:
                    logger.warning("Table channel %d is unavailable: %s", row.channel_id, exc)
                    continue
            if not isinstance(channel, discord.TextChannel):
                logger.warning("Table channel %d is not a text channel.", row.channel_id)
                continue
            if row.table_active:
                await self._close_interrupted_table(channel, row)
            await self._ensure_play_message(channel, row)

    async def _close_interrupted_table(
        self, channel: discord.TextChannel, row: MultiplayerChannel
    ) -> None:
        """A table still marked active was cut off by the restart; its stakes were refunded."""
        if row.table_message_id:
            notice = EmbedFactory.table_notice(
                "Table closed",
                "The bot restarted while this table was open. Every stake was refunded.",
                bot_avatar_url=avatar_url(self.bot),
            )
            try:
                await channel.get_partial_message(row.table_message_id).edit(
                    embed=notice, view=None
                )
            except discord.HTTPException as exc:
                logger.warning("Could not close stale table in %d: %s", row.channel_id, exc)
        await self.bot.db.set_table_inactive(row.channel_id)

    async def _ensure_play_message(
        self, channel: discord.TextChannel, row: MultiplayerChannel
    ) -> None:
        if row.play_message_id:
            try:
                await channel.fetch_message(row.play_message_id)
                return
            except discord.NotFound:
                pass
            except discord.HTTPException as exc:
                logger.warning("Could not check the Play embed in %d: %s", row.channel_id, exc)
                return
        try:
            message = await self._post_play_message(channel, row.game)
        except discord.HTTPException as exc:
            logger.warning("Could not repost the Play embed in %d: %s", row.channel_id, exc)
            return
        await self.bot.db.set_play_message(row.channel_id, message.id)


async def setup(bot: YokaiBot) -> None:
    """Extension setup entrypoint."""
    await bot.add_cog(TablesCog(bot))
```

- [ ] **Step 4: Add the admin subcommands**

In `src/yokai/cogs/admin.py` extend imports with `from yokai.errors import InvalidGameChoiceError, InvalidPaymentError, YokaiError` and `from yokai.gambling.lobby import GAME_SPECS`, add above the class:
```python
GAME_CHOICES = [
    app_commands.Choice(name=spec.label, value=name) for name, spec in GAME_SPECS.items()
]
```
and these methods inside `AdminCog` (after `diag`):
```python
    @admin.command(
        name="set_multiplayer", description="Make this channel the table channel for a game."
    )
    @app_commands.describe(game="Which game this channel hosts")
    @app_commands.choices(game=GAME_CHOICES)
    async def set_multiplayer(
        self, interaction: discord.Interaction, game: app_commands.Choice[str]
    ) -> None:
        """Dedicate the current channel to a multiplayer game and post its Play embed."""
        if await self._deny_unless_admin(interaction, "Ask the bot owner to set up a table."):
            return
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await self._send_domain_error(
                interaction, InvalidGameChoiceError("Run this in a normal text channel.")
            )
            return
        await interaction.response.defer(ephemeral=True)
        tables = self.bot.get_cog("Tables")
        try:
            await tables.set_channel(channel, game.value)  # type: ignore[union-attr]
        except YokaiError as err:
            await self._send_domain_error(interaction, err)
            return
        embed = EmbedFactory.success(
            title="Table channel set",
            description=(
                f"This channel now hosts **{game.name}** tables. "
                "Members open one with the Play button."
            ),
            context="Admin",
            bot_avatar_url=self._avatar_url(),
        )
        await send(interaction, embed=embed, ephemeral=True)

    @admin.command(
        name="unset_multiplayer", description="Stop using this channel as a table channel."
    )
    async def unset_multiplayer(self, interaction: discord.Interaction) -> None:
        if await self._deny_unless_admin(interaction, "Ask the bot owner to change tables."):
            return
        await interaction.response.defer(ephemeral=True)
        tables = self.bot.get_cog("Tables")
        try:
            removed = await tables.unset_channel(interaction.channel_id)  # type: ignore[union-attr]
        except YokaiError as err:
            await self._send_domain_error(interaction, err)
            return
        if removed:
            embed = EmbedFactory.success(
                title="Table channel removed",
                description="This channel is no longer a table channel.",
                context="Admin",
                bot_avatar_url=self._avatar_url(),
            )
        else:
            embed = EmbedFactory.info(
                title="Nothing to remove",
                description="This is not a table channel.",
                context="Admin",
                bot_avatar_url=self._avatar_url(),
            )
        await send(interaction, embed=embed, ephemeral=True)
```

- [ ] **Step 5: Wire the bot**

In `src/yokai/bot.py`: add `from yokai.gambling.tables import TableManager`; in `__init__` right after `self.active_games = ActiveGames()` add:
```python
        self.tables = TableManager(
            self.db,
            self.active_games,
            welcome_bonus=config.economy_welcome_bonus,
            min_bet=config.gambling_min_bet,
            max_bet=config.gambling_max_bet,
            rake_percent=config.gambling_duel_rake_percent,
            join_seconds=config.gambling_lobby_join_seconds,
            pick_seconds=config.gambling_turn_seconds,
        )
        self._tables_reconciled = False
```
In `setup_hook` after loading `yokai.cogs.owner` add `await self.load_extension("yokai.cogs.tables")`. In `on_ready` after `self.heartbeat.start()` add:
```python
        if not self._tables_reconciled:
            self._tables_reconciled = True
            tables_cog = self.get_cog("Tables")
            if tables_cog is not None:
                try:
                    await tables_cog.reconcile()  # type: ignore[attr-defined]
                except (discord.HTTPException, aiosqlite.Error) as exc:
                    logger.error("Could not reconcile table channels: %s", exc)
```
and `import aiosqlite` at the top of `bot.py`.

- [ ] **Step 6: Run to verify they pass**

Run: `python -m pytest tests/test_tables_cog.py tests/test_admin_cog.py tests/test_bot_wiring.py tests/test_command_tree.py tests/test_one_emoji_rule.py -q`
Expected: all pass.

- [ ] **Step 7: Suite, lint, commit**

```powershell
python -m pytest -q; python -m ruff check .; python -m ruff format .; python -m ruff format --check .
git add src tests
git commit -m "feat(tables): add table channel provisioning, /admin set_multiplayer and bot wiring" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>" -m "Claude-Session: https://claude.ai/code/session_01BxUkTAJGP4uNByTtyvj8n9"
```

---

### Task 11: Documentation and phase record

**Files:**
- Modify: `README.md`, `TASKS.md`, `QA.md`

- [ ] **Step 1: README**

Under the command list in `README.md`, add (matching the file's line endings; the file uses CRLF, so edit with a tool that preserves them or detect `\r\n` first):
```
  - `/admin set_multiplayer game:<name>`: Admin-only. Makes the current channel the table channel for a multiplayer game and posts its persistent **Play** embed.
  - `/admin unset_multiplayer`: Admin-only. Stops using the current channel as a table channel.
```
and a short features bullet: "**Multiplayer tables:** dedicated channels with a Play button; the finished game stays on screen until the next table is requested. First game: Duel (pick a number 1 to 10, closest to a hidden number wins)."

- [ ] **Step 2: TASKS.md**

Under Phase 10, after the M1 block, add:
```markdown
### M2 — Tables, pot accounting and duel (plan: `docs/superpowers/plans/2026-10-07-multiplayer-m2-tables-and-duel.md`)

- [ ] M2.1 Table configuration and errors.
- [ ] M2.2 Migration v6, pot accounting on solo settlement.
- [ ] M2.3 Atomic `settle_lobby`.
- [ ] M2.4 Table channel storage.
- [ ] M2.5 Lobby state machine and duel engine.
- [ ] M2.6 `TableManager`.
- [ ] M2.7 Table and duel embeds.
- [ ] M2.8 Play, stake form, lobby and duel views.
- [ ] M2.9 `TablesCog`, `/admin set_multiplayer`, `/admin unset_multiplayer`, bot wiring.

**Acceptance criteria (M2)**
- `/admin set_multiplayer game:Duel` in a text channel posts a Play embed that keeps working
  after the bot restarts; `/admin unset_multiplayer` removes it.
- Pressing Play asks for a stake, opens a table, and deletes the previous table's message; the
  finished table's result stays on screen until the next table is requested.
- A table in play refuses another Play press; the host or an admin can cancel an unstarted
  table (everyone is refunded); a started table cannot be cancelled.
- A duel starts when the second player joins at the host's stake; picks are private; the
  closest pick to the hidden number wins the pot minus the 2% rake; a tie refunds both.
- Every settlement is one transaction; the lottery pot receives only the rake from a duel and
  the lost stakes from solo games; restarting mid-table refunds every stake.
- `pytest`, `ruff check` and `ruff format --check` are clean.
- *(manual, real Discord)* Set a channel, press Play, join from a second account, pick numbers
  from both, check the reveal and balances; let a lobby expire alone; cancel a lobby; kill the
  bot mid-duel and confirm stakes are back and the table message says it was closed; delete the
  Play embed and confirm it returns after a restart; confirm a blacklisted account is refused.

**STOP — owner review.**
```
Tick the M2 boxes once the work is verified.

- [ ] **Step 3: QA.md**

Add one row to the manual QA table for "Multiplayer tables (M2)" using the same column layout as the existing rows (copy the neighbouring row's shape): the checks from the manual acceptance bullet above.

- [ ] **Step 4: Final verification and commit**

```powershell
python -m pytest -q; python -m ruff check .; python -m ruff format --check .
git add README.md TASKS.md QA.md
git commit -m "docs(tables): document multiplayer tables and record phase M2" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>" -m "Claude-Session: https://claude.ai/code/session_01BxUkTAJGP4uNByTtyvj8n9"
```
Expected: suite and lint clean.

**STOP. Report what changed, list the unverified manual Discord checks, and wait for owner review before planning M3.**
