# Multiplayer Phase M4: The Lottery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A house-funded weekly lottery in its own channel. Members buy numbered tickets with a button, a scheduled draw picks one winning number, holders split the pot, nobody holding it rolls the pot over, and winners are announced in the channel.

**Architecture:** A pure engine (`engines/lottery.py`) parses and plans ticket choices and draws numbers. Four `Database` methods do every money movement as one transaction each (`buy_lottery_tickets`, `run_lottery_draw`, plus state and announcement bookkeeping). A persistent **Buy tickets** view and a stake-style modal collect purchases. A `LotteryCog` owns the channel panel, posts announcements and runs a 60-second scheduler task. `/admin set_multiplayer game:lottery` dedicates the channel through the existing `TablesCog`, which delegates the panel to the lottery cog.

**Tech Stack:** Python 3.12, discord.py 2.7.1, aiosqlite, pytest (`asyncio_mode = "auto"`), ruff (line length 100).

**Spec:** `docs/superpowers/specs/2026-10-07-multiplayer-tables-design.md` (§8 lottery, §9 config, §10 errors, §11 testing). M2 and M3 are built; the pot (`lottery_state.pot`, `lottery_pot_entries`) already fills from losses and rakes.

## Global Constraints

- Python 3.12+, `discord.py>=2.7.1`; slash commands guild-scoped; no privileged intents (`AGENTS.md`).
- Every user-visible message goes through `ui/embeds.py`; at most one emoji per embed, only from `theme.Emoji`; errors plain and ephemeral; `allowed_mentions` none, members mentioned only inside embeds.
- Every view and modal blocks blacklisted users (`moderation.gate.screen`); the persistent Buy view has `timeout=None` and a fixed `custom_id`; no view method or attribute may shadow a discord.py internal (`tests/test_view_names.py` scans `yokai.ui` automatically).
- Never block the event loop; no bare `except`; `logging` not `print`; `from __future__ import annotations`; full type hints.
- Numbers run from 1 to `LOTTERY_NUMBER_RANGE` (default 20, clamped 2 to 100). A ticket costs `LOTTERY_TICKET_PRICE` (default 100, minimum 1). A member holds at most `LOTTERY_MAX_TICKETS_PER_USER` (default 10, minimum 1) tickets per draw and never the same number twice. One draw per `LOTTERY_DRAW_INTERVAL_HOURS` (default 168, minimum 1).
- A ticket purchase is a direct debit, never a `place_bet` (the startup sweep would refund a week-old open bet). Ledger reasons: `ticket:lottery` for purchases, `win:lottery` for prizes. Purchases after the draw time are refused.
- The pot is the prize: winners split it (rounded down, the remainder stays in the pot); no holder of the winning number means the pot rolls over unchanged. The lottery can never owe more than the pot holds.
- Winner announcements are new messages in the channel and are never deleted.
- No new dependency. Schema version 7 (migration v7) is the only schema change.
- Work directly on `main`; checkpoint tag first; commit per task; finish with `pytest -q`, `ruff check .`, `ruff format --check .` clean, then stop for owner review.

## Review Focus

- A purchase is atomic: debit, ticket rows, pot credit and ledger row commit together or not at all; two simultaneous purchases can never push a member over the ticket cap; sales close the moment the draw time passes (Tasks 4, 6).
- The draw is atomic and idempotent: winners credited, pot reduced, draw recorded, draw number and next draw time advanced in one transaction; running it twice pays once; tickets of earlier draws never count; the remainder stays in the pot; no winners rolls the pot over (Task 5).
- Offline at draw time: the draw happens on the next check, the schedule jumps to the first future time, an unannounced draw is announced later and not twice in the normal case (Tasks 5, 8).
- Hostile ticket input: empty, huge, negative, zero, non-numeric, markdown or mentions in the text, a quick-pick count far above the cap (rejected before any sampling), numbers already held (Tasks 2, 7).
- The lottery can never be opened as a table (`open_table` refuses it); the Buy button works after a restart and refuses blacklisted users (Tasks 7, 8).
- Moving or unsetting the lottery channel removes its panel but keeps the pot, tickets and schedule (Task 8).

---

## File Structure

| File | Responsibility |
|---|---|
| `src/yokai/config.py`, `.env.example` (modify) | lottery settings |
| `src/yokai/errors.py` (modify) | `LotteryClosedError`, `TicketLimitError` |
| `src/yokai/gambling/engines/lottery.py` (create) | parse, quick picks, ticket planning, draw, split, odds |
| `src/yokai/gambling/models.py` (modify) | `LotteryState`, `LotteryDraw`, `LotteryPurchase` |
| `src/yokai/storage/db.py` (modify) | migration v7, lottery state, purchase and draw transactions |
| `src/yokai/ui/embeds.py` (modify) | lottery panel, receipt, announcement |
| `src/yokai/ui/lottery_views.py` (create) | `BuyView`, `TicketsModal`, `handle_buy` |
| `src/yokai/cogs/lottery.py` (create) | panel, scheduler, announcements |
| `src/yokai/cogs/tables.py`, `src/yokai/gambling/lobby.py`, `src/yokai/gambling/tables.py`, `src/yokai/bot.py` (modify) | dedicate the lottery channel, refuse it as a table, load the cog |
| `scripts/preview_embeds.py`, `docs/preview/embeds.html` (modify) | samples |
| tests | `test_lottery_engine.py`, `test_lottery_schedule.py`, `test_lottery_tickets.py`, `test_lottery_draw.py`, `test_lottery_embeds.py`, `test_lottery_views.py`, `test_lottery_cog.py`, plus edits to config, errors, pot, lobby, manager, cog and wiring tests |

---

### Task 0: Checkpoint tag

- [ ] **Step 1: Tag a clean main**

```powershell
git status --short; git branch --show-current
git tag -a checkpoint/pre-multiplayer-m4 -m "Before multiplayer M4 (lottery)"
git tag | Select-String multiplayer
```
Expected: no modified files, branch `main`, the m1 to m4 tags listed.

---

### Task 1: Configuration and errors

**Files:**
- Modify: `src/yokai/config.py`, `src/yokai/errors.py`, `.env.example`
- Test: `tests/test_gambling_config.py`, `tests/test_gambling_errors.py`

**Interfaces:**
- Produces: `Config.lottery_ticket_price`, `lottery_number_range`, `lottery_max_tickets_per_user`, `lottery_draw_interval_hours` (ints); `LotteryClosedError(message=..., user_hint=...)`; `TicketLimitError(held: int, maximum: int)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gambling_config.py`:
```python
def test_lottery_defaults() -> None:
    cfg = _load()
    assert cfg.lottery_ticket_price == 100
    assert cfg.lottery_number_range == 20
    assert cfg.lottery_max_tickets_per_user == 10
    assert cfg.lottery_draw_interval_hours == 168


def test_lottery_overrides() -> None:
    cfg = _load(
        {
            "LOTTERY_TICKET_PRICE": "250",
            "LOTTERY_NUMBER_RANGE": "30",
            "LOTTERY_MAX_TICKETS_PER_USER": "5",
            "LOTTERY_DRAW_INTERVAL_HOURS": "24",
        }
    )
    assert (
        cfg.lottery_ticket_price,
        cfg.lottery_number_range,
        cfg.lottery_max_tickets_per_user,
        cfg.lottery_draw_interval_hours,
    ) == (250, 30, 5, 24)


def test_lottery_values_are_clamped_to_sensible_bounds() -> None:
    cfg = _load(
        {
            "LOTTERY_TICKET_PRICE": "0",
            "LOTTERY_NUMBER_RANGE": "1",
            "LOTTERY_MAX_TICKETS_PER_USER": "0",
            "LOTTERY_DRAW_INTERVAL_HOURS": "0",
        }
    )
    assert (
        cfg.lottery_ticket_price,
        cfg.lottery_number_range,
        cfg.lottery_max_tickets_per_user,
        cfg.lottery_draw_interval_hours,
    ) == (1, 2, 1, 1)
    assert _load({"LOTTERY_NUMBER_RANGE": "5000"}).lottery_number_range == 100
```
Append to `tests/test_gambling_errors.py` (merge `LotteryClosedError`, `TicketLimitError` into the existing `yokai.errors` import):
```python
def test_lottery_closed_error_has_a_default_and_accepts_a_custom_message() -> None:
    default = LotteryClosedError()
    assert isinstance(default, YokaiError) and "closed" in default.message.lower()
    custom = LotteryClosedError("Tickets are not on sale yet.", user_hint=None)
    assert custom.message == "Tickets are not on sale yet." and custom.user_hint is None


def test_ticket_limit_error_names_the_cap_and_what_the_member_holds() -> None:
    err = TicketLimitError(held=8, maximum=10)
    assert isinstance(err, YokaiError) and "10" in err.message and "8" in err.message
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_gambling_config.py tests/test_gambling_errors.py -q`
Expected: failures: unknown `Config` attributes and an `ImportError` for the new errors.

- [ ] **Step 3: Implement**

`src/yokai/config.py`: add to the `Config` dataclass after `gambling_crash_max_multiplier`:
```python
    # Lottery
    lottery_ticket_price: int = 100
    lottery_number_range: int = 20
    lottery_max_tickets_per_user: int = 10
    lottery_draw_interval_hours: int = 168
```
In `load_config`, after the `gambling_crash_max_multiplier` parsing:
```python
    lottery_ticket_price = max(
        1, _parse_int("LOTTERY_TICKET_PRICE", os.getenv("LOTTERY_TICKET_PRICE"), 100)
    )
    lottery_number_range = min(
        100, max(2, _parse_int("LOTTERY_NUMBER_RANGE", os.getenv("LOTTERY_NUMBER_RANGE"), 20))
    )
    lottery_max_tickets_per_user = max(
        1,
        _parse_int(
            "LOTTERY_MAX_TICKETS_PER_USER", os.getenv("LOTTERY_MAX_TICKETS_PER_USER"), 10
        ),
    )
    lottery_draw_interval_hours = max(
        1,
        _parse_int(
            "LOTTERY_DRAW_INTERVAL_HOURS", os.getenv("LOTTERY_DRAW_INTERVAL_HOURS"), 168
        ),
    )
```
and pass `lottery_ticket_price=lottery_ticket_price, lottery_number_range=lottery_number_range, lottery_max_tickets_per_user=lottery_max_tickets_per_user, lottery_draw_interval_hours=lottery_draw_interval_hours,` in the `Config(...)` call after `gambling_crash_max_multiplier=gambling_crash_max_multiplier,`.

`src/yokai/errors.py` append:
```python
class LotteryClosedError(YokaiError):
    """Raised when tickets are bought while sales are closed."""

    title = "Tickets closed"

    def __init__(
        self,
        message: str = "The draw is happening right now, so ticket sales are closed.",
        user_hint: Optional[str] = "Tickets for the next draw go on sale in a moment.",
    ) -> None:
        super().__init__(message, user_hint=user_hint)


class TicketLimitError(YokaiError):
    """Raised when a purchase would take a member past the per-draw ticket cap."""

    title = "Ticket limit"

    def __init__(self, held: int, maximum: int) -> None:
        super().__init__(
            f"You can hold at most **{maximum}** tickets per draw, and you already have "
            f"**{held}**.",
            user_hint="Wait for the next draw to buy more.",
        )
        self.held = held
        self.maximum = maximum
```
`.env.example` append:
```
# Lottery
# Price of one ticket (whole Renown)
LOTTERY_TICKET_PRICE=100
# Tickets carry a number from 1 to this (2 to 100). A bigger range means fewer wins.
LOTTERY_NUMBER_RANGE=20
# Most tickets one member can hold per draw
LOTTERY_MAX_TICKETS_PER_USER=10
# Hours between draws (168 is weekly)
LOTTERY_DRAW_INTERVAL_HOURS=168
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_gambling_config.py tests/test_gambling_errors.py -q`
Expected: all pass.

- [ ] **Step 5: Suite, lint, commit**

```powershell
python -m pytest -q; python -m ruff check .; python -m ruff format .; python -m ruff format --check .
git add src/yokai/config.py src/yokai/errors.py .env.example tests/test_gambling_config.py tests/test_gambling_errors.py
git commit -m "feat(lottery): add lottery settings and errors" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>" -m "Claude-Session: https://claude.ai/code/session_01BxUkTAJGP4uNByTtyvj8n9"
```

---

### Task 2: Lottery engine

**Files:**
- Create: `src/yokai/gambling/engines/lottery.py`
- Create: `tests/test_lottery_engine.py`

**Interfaces:**
- Produces: `parse_numbers(text, number_range) -> list[int]`; `parse_count(text) -> int`; `quick_picks(count, taken, number_range, rng) -> list[int]`; `plan_tickets(held, numbers_text, quick_text, number_range, max_per_user, rng) -> list[int]` (the full list of new numbers: hand-picked first, then quick picks); `draw_number(number_range, rng) -> int`; `split_pot(pot, winner_count) -> tuple[int, int]` returning `(each, remainder)`; `hit_chance(tickets_sold, number_range) -> float`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_lottery_engine.py`:
```python
"""The pure lottery rules: parsing choices, quick picks, planning a purchase, the draw."""

from __future__ import annotations

import random

import pytest

from yokai.errors import InvalidGameChoiceError, TicketLimitError
from yokai.gambling.engines.lottery import (
    draw_number,
    hit_chance,
    parse_count,
    parse_numbers,
    plan_tickets,
    quick_picks,
    split_pot,
)


def test_numbers_may_be_separated_by_commas_or_spaces_and_repeats_are_dropped() -> None:
    assert parse_numbers("3, 7  19,7", 20) == [3, 7, 19]


def test_blank_text_means_no_numbers() -> None:
    assert parse_numbers("", 20) == []
    assert parse_numbers("  , ,", 20) == []


@pytest.mark.parametrize("text", ["0", "21", "-3", "abc", "3.5", "1;2", "7x", "٣"])
def test_anything_that_is_not_a_number_in_range_is_refused(text: str) -> None:
    with pytest.raises(InvalidGameChoiceError):
        parse_numbers(text, 20)


def test_a_refusal_never_echoes_markdown_or_mentions_unescaped() -> None:
    with pytest.raises(InvalidGameChoiceError) as caught:
        parse_numbers("@everyone **bold**", 20)
    message = caught.value.message
    assert "@everyone" not in message.replace("\\@everyone", "")
    assert "**bold**" not in message.replace("\\*\\*bold\\*\\*", "")


def test_a_huge_number_is_refused_not_computed() -> None:
    with pytest.raises(InvalidGameChoiceError):
        parse_numbers("9" * 5000, 20)


def test_counts_are_whole_numbers_and_blank_is_zero() -> None:
    assert parse_count("") == 0 and parse_count("  ") == 0
    assert parse_count(" 4 ") == 4
    for bad in ("-1", "2.5", "x", "٣"):
        with pytest.raises(InvalidGameChoiceError):
            parse_count(bad)


def test_quick_picks_are_distinct_free_and_sorted() -> None:
    picks = quick_picks(5, taken={1, 2, 3}, number_range=20, rng=random.Random(4))
    assert picks == sorted(picks) and len(set(picks)) == 5
    assert not set(picks) & {1, 2, 3} and all(1 <= p <= 20 for p in picks)


def test_quick_picks_are_deterministic_for_a_seed() -> None:
    assert quick_picks(4, set(), 20, random.Random(9)) == quick_picks(4, set(), 20, random.Random(9))


def test_quick_picks_cannot_exceed_the_free_numbers() -> None:
    with pytest.raises(InvalidGameChoiceError):
        quick_picks(3, taken=set(range(1, 19)), number_range=20, rng=random.Random(1))


def test_the_draw_stays_in_range_and_can_reach_every_number() -> None:
    rng = random.Random(11)
    drawn = {draw_number(20, rng) for _ in range(2000)}
    assert drawn == set(range(1, 21))


def test_the_pot_splits_evenly_and_the_remainder_stays() -> None:
    assert split_pot(1000, 3) == (333, 1)
    assert split_pot(1000, 1) == (1000, 0)
    assert split_pot(0, 2) == (0, 0)


def test_with_no_winners_the_whole_pot_is_left() -> None:
    assert split_pot(1000, 0) == (0, 1000)


def test_the_hit_chance_matches_the_published_table() -> None:
    assert round(hit_chance(10, 20), 2) == 0.40
    assert round(hit_chance(20, 20), 2) == 0.64
    assert round(hit_chance(5, 30), 2) == 0.16
    assert hit_chance(0, 20) == 0.0


def test_a_purchase_of_hand_picked_numbers_only() -> None:
    assert plan_tickets([], "3, 7", "", 20, 10, random.Random(1)) == [3, 7]


def test_a_purchase_of_quick_picks_only_avoids_what_is_held() -> None:
    picks = plan_tickets([1, 2, 3], "", "4", 20, 10, random.Random(1))
    assert len(picks) == 4 and not set(picks) & {1, 2, 3}


def test_a_purchase_can_mix_both_without_overlap() -> None:
    picks = plan_tickets([], "5, 6", "3", 20, 10, random.Random(2))
    assert picks[:2] == [5, 6] and len(picks) == 5 and len(set(picks)) == 5


def test_an_empty_purchase_is_refused() -> None:
    with pytest.raises(InvalidGameChoiceError):
        plan_tickets([], "", "", 20, 10, random.Random(1))
    with pytest.raises(InvalidGameChoiceError):
        plan_tickets([], " ", "0", 20, 10, random.Random(1))


def test_numbers_already_held_are_refused() -> None:
    with pytest.raises(InvalidGameChoiceError, match="already hold 7"):
        plan_tickets([7], "7, 8", "", 20, 10, random.Random(1))


def test_going_past_the_cap_is_refused_before_anything_is_picked() -> None:
    with pytest.raises(TicketLimitError):
        plan_tickets([1] * 0 + list(range(1, 9)), "9, 10, 11", "", 20, 10, random.Random(1))
    with pytest.raises(TicketLimitError):
        plan_tickets([], "", "11", 20, 10, random.Random(1))


def test_a_huge_quick_pick_count_is_refused_without_sampling() -> None:
    with pytest.raises(TicketLimitError):
        plan_tickets([], "", "99999999", 20, 10, random.Random(1))
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_lottery_engine.py -q`
Expected: collection ERROR `No module named 'yokai.gambling.engines.lottery'`.

- [ ] **Step 3: Implement**

Create `src/yokai/gambling/engines/lottery.py`:
```python
"""Lottery rules: pick numbers, one drawn number wins, holders split the pot. Pure functions."""

from __future__ import annotations

import random
from typing import Collection

from yokai.errors import InvalidGameChoiceError, TicketLimitError
from yokai.textutil import escape_md

MAX_TOKEN_LENGTH = 6  # a number up to 100 never needs more; longer tokens are refused unread


def _clip(token: str) -> str:
    """A token safe to show back to the member: shortened and with markdown escaped."""
    return escape_md(token[:20])


def parse_numbers(text: str, number_range: int) -> list[int]:
    """Numbers typed by hand, separated by commas or spaces. Repeats are dropped."""
    numbers: list[int] = []
    for token in text.replace(",", " ").split():
        if len(token) > MAX_TOKEN_LENGTH or not (token.isascii() and token.isdigit()):
            raise InvalidGameChoiceError(f"'{_clip(token)}' is not a ticket number.")
        number = int(token)
        if not 1 <= number <= number_range:
            raise InvalidGameChoiceError(f"Ticket numbers run from 1 to {number_range}.")
        if number not in numbers:
            numbers.append(number)
    return numbers


def parse_count(text: str) -> int:
    """The quick-pick count; blank means none."""
    cleaned = text.strip()
    if not cleaned:
        return 0
    if len(cleaned) > MAX_TOKEN_LENGTH or not (cleaned.isascii() and cleaned.isdigit()):
        raise InvalidGameChoiceError("Quick picks must be a whole number, like 3.")
    return int(cleaned)


def quick_picks(
    count: int, taken: Collection[int], number_range: int, rng: random.Random
) -> list[int]:
    """`count` random numbers that are not in `taken`, distinct and sorted."""
    free = [n for n in range(1, number_range + 1) if n not in taken]
    if count > len(free):
        raise InvalidGameChoiceError("There aren't that many free numbers left.")
    return sorted(rng.sample(free, count))


def plan_tickets(
    held: Collection[int],
    numbers_text: str,
    quick_text: str,
    number_range: int,
    max_per_user: int,
    rng: random.Random,
) -> list[int]:
    """The new ticket numbers for one purchase: hand-picked first, then quick picks."""
    chosen = parse_numbers(numbers_text, number_range)
    quick = parse_count(quick_text)
    if not chosen and quick == 0:
        raise InvalidGameChoiceError("Enter some numbers, a quick-pick count, or both.")
    overlap = sorted(set(chosen) & set(held))
    if overlap:
        raise InvalidGameChoiceError(f"You already hold {', '.join(str(n) for n in overlap)}.")
    if len(held) + len(chosen) + quick > max_per_user:
        raise TicketLimitError(len(held), max_per_user)
    picks = quick_picks(quick, set(held) | set(chosen), number_range, rng) if quick else []
    return chosen + picks


def draw_number(number_range: int, rng: random.Random) -> int:
    """The winning number."""
    return rng.randint(1, number_range)


def split_pot(pot: int, winner_count: int) -> tuple[int, int]:
    """Split a pot between holders of the winning number as (each, remainder left in the pot)."""
    if winner_count <= 0:
        return 0, pot
    return divmod(pot, winner_count)


def hit_chance(tickets_sold: int, number_range: int) -> float:
    """Chance that at least one of `tickets_sold` distinct tickets holds the winning number."""
    return 1.0 - (1.0 - 1.0 / number_range) ** tickets_sold
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_lottery_engine.py -q`
Expected: all pass. If `test_a_refusal_never_echoes_markdown...` fails, read `yokai/textutil.escape_md` and assert against what it really escapes (the property to keep: user text is escaped and clipped before it is shown).

- [ ] **Step 5: Lint and commit**

```powershell
python -m ruff check .; python -m ruff format .; python -m ruff format --check .
git add src/yokai/gambling/engines/lottery.py tests/test_lottery_engine.py
git commit -m "feat(lottery): add the lottery engine" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>" -m "Claude-Session: https://claude.ai/code/session_01BxUkTAJGP4uNByTtyvj8n9"
```

---

### Task 3: Migration v7, models and the lottery state

**Files:**
- Modify: `src/yokai/storage/db.py` (`CURRENT_SCHEMA_VERSION`, `MIGRATION_V7`, `_migrate`, new methods), `src/yokai/gambling/models.py`
- Modify: `tests/test_gambling_pot.py` (the schema-version assertion)
- Create: `tests/test_lottery_schedule.py`

**Interfaces:**
- Produces: dataclasses `LotteryState(pot, draw_no, next_draw_ts: Optional[int])`, `LotteryDraw(draw_no, ts, winning_number, pot, winners: tuple[int, ...], prize_each, announced: bool)`, `LotteryPurchase(draw_no, numbers: tuple[int, ...], cost, new_balance, pot)`; `Database.get_lottery_state() -> LotteryState`; `Database.ensure_lottery_schedule(now: int, interval_s: int) -> int`; `Database.lottery_tickets_sold() -> int` (current draw); `Database.user_lottery_numbers(user_id) -> list[int]` (current draw, sorted).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_lottery_schedule.py`:
```python
"""Migration v7 and the lottery's state and schedule."""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from yokai.storage.db import (
    CURRENT_SCHEMA_VERSION,
    MIGRATION_V1,
    MIGRATION_V2,
    MIGRATION_V3,
    MIGRATION_V4,
    MIGRATION_V5,
    MIGRATION_V6,
    Database,
)

WEEK = 7 * 24 * 3600


async def _db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "lottery.db")
    await db.initialize()
    return db


async def _tables(db: Database) -> set[str]:
    async with db.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table';") as cur:
        return {row[0] for row in await cur.fetchall()}


@pytest.mark.asyncio
async def test_migration_v7_creates_the_lottery_tables_and_state_columns(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        assert CURRENT_SCHEMA_VERSION == 7
        assert {"lottery_tickets", "lottery_draws"} <= await _tables(db)
        state = await db.get_lottery_state()
        assert (state.pot, state.draw_no, state.next_draw_ts) == (0, 1, None)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_v6_database_upgrades_keeping_its_pot_and_rows(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    raw = await aiosqlite.connect(path)
    for script in (MIGRATION_V1, MIGRATION_V2, MIGRATION_V3, MIGRATION_V4, MIGRATION_V5):
        await raw.executescript(script)
    await raw.executescript(MIGRATION_V6)
    await raw.execute("PRAGMA user_version = 6;")
    await raw.execute("UPDATE lottery_state SET pot = 777 WHERE id = 1;")
    await raw.execute(
        "INSERT INTO wallets (user_id, balance, daily_streak, created_ts) VALUES (7, 321, 0, 1);"
    )
    await raw.commit()
    await raw.close()

    db = Database(path)
    await db.initialize()
    try:
        state = await db.get_lottery_state()
        assert (state.pot, state.draw_no, state.next_draw_ts) == (777, 1, None)
        wallet = await db.get_wallet(7)
        assert wallet is not None and wallet.balance == 321
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_the_schedule_starts_once_and_is_then_left_alone(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        assert await db.ensure_lottery_schedule(1_000, WEEK) == 1_000 + WEEK
        assert await db.ensure_lottery_schedule(5_000, WEEK) == 1_000 + WEEK  # not restarted
        state = await db.get_lottery_state()
        assert state.next_draw_ts == 1_000 + WEEK
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_fresh_lottery_has_no_tickets(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        assert await db.lottery_tickets_sold() == 0
        assert await db.user_lottery_numbers(1) == []
    finally:
        await db.close()
```
In `tests/test_gambling_pot.py` change `assert CURRENT_SCHEMA_VERSION == 6` to `assert CURRENT_SCHEMA_VERSION >= 6`.

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_lottery_schedule.py -q`
Expected: collection ERROR `cannot import name 'MIGRATION_V6'`... no: V6 exists; the failures are `CURRENT_SCHEMA_VERSION == 7` and the missing `get_lottery_state`.

- [ ] **Step 3: Implement**

Append to `src/yokai/gambling/models.py`:
```python
@dataclass(frozen=True)
class LotteryState:
    pot: int
    draw_no: int  # the draw tickets are currently being sold for
    next_draw_ts: Optional[int]  # None until the draw clock has started


@dataclass(frozen=True)
class LotteryDraw:
    draw_no: int
    ts: int
    winning_number: int
    pot: int  # the pot the draw was made against
    winners: tuple[int, ...]  # user ids; empty when the pot rolled over
    prize_each: int
    announced: bool


@dataclass(frozen=True)
class LotteryPurchase:
    draw_no: int
    numbers: tuple[int, ...]
    cost: int
    new_balance: int
    pot: int
```
In `src/yokai/storage/db.py`: set `CURRENT_SCHEMA_VERSION = 7`; extend the models import to `from yokai.gambling.models import LotteryDraw, LotteryPurchase, LotteryState, MultiplayerChannel`; add after `MIGRATION_V6`:
```python
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

ALTER TABLE lottery_state ADD COLUMN draw_no INTEGER NOT NULL DEFAULT 1;
ALTER TABLE lottery_state ADD COLUMN next_draw_ts INTEGER;
"""
```
In `_migrate` after the `version < 6` block:
```python
        if version < 7:
            logger.info("Applying SQLite migration v7...")
            await self.conn.executescript(MIGRATION_V7)
            await self.conn.execute("PRAGMA user_version = 7;")
            await self.conn.commit()
            logger.info("SQLite schema migrated to version 7.")
```
Add these methods after `get_pot`:
```python
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
```
(`LotteryDraw` and `LotteryPurchase` are imported now and used by Tasks 4 and 5; if ruff flags them as unused until then, import them in those tasks instead.)

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_lottery_schedule.py tests/test_gambling_pot.py -q`
Expected: all pass. Then `python -m pytest -q` and fix any other test that hard-codes schema version 6 by following `CURRENT_SCHEMA_VERSION`.

- [ ] **Step 5: Lint and commit**

```powershell
python -m ruff check .; python -m ruff format .; python -m ruff format --check .
git add src tests
git commit -m "feat(lottery): add migration v7, lottery models and the draw schedule" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>" -m "Claude-Session: https://claude.ai/code/session_01BxUkTAJGP4uNByTtyvj8n9"
```

---

### Task 4: Buying tickets

**Files:**
- Modify: `src/yokai/storage/db.py` (new method after `user_lottery_numbers`)
- Create: `tests/test_lottery_tickets.py`

**Interfaces:**
- Produces: `Database.buy_lottery_tickets(user_id, numbers, price, max_per_user, welcome_bonus, now=None) -> LotteryPurchase`. Raises `LotteryClosedError` (no schedule yet, or `now >= next_draw_ts`), `TicketLimitError`, `InvalidGameChoiceError` (a number already held), `InsufficientFundsError` (action `"buy tickets"`), `ValueError` (empty list). All or nothing.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_lottery_tickets.py`:
```python
"""A ticket purchase is one all-or-nothing transaction."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from yokai.errors import (
    InsufficientFundsError,
    InvalidGameChoiceError,
    LotteryClosedError,
    TicketLimitError,
)
from yokai.storage.db import Database

NOW = 1_000_000
WEEK = 7 * 24 * 3600


async def _db(tmp_path: Path, balance: int = 1000) -> Database:
    db = Database(tmp_path / "tickets.db")
    await db.initialize()
    await db.ensure_wallet(1, balance)
    await db.ensure_wallet(2, balance)
    await db.ensure_lottery_schedule(NOW, WEEK)
    return db


async def _buy(db: Database, user_id: int, numbers: list[int], *, now: int = NOW + 10, **kw):  # type: ignore[no-untyped-def]
    return await db.buy_lottery_tickets(
        user_id,
        numbers,
        price=kw.get("price", 100),
        max_per_user=kw.get("max_per_user", 10),
        welcome_bonus=0,
        now=now,
    )


async def _balance(db: Database, user_id: int) -> int:
    wallet = await db.get_wallet(user_id)
    assert wallet is not None
    return wallet.balance


@pytest.mark.asyncio
async def test_a_purchase_debits_stores_the_tickets_and_feeds_the_pot(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        purchase = await _buy(db, 1, [3, 7, 19])
        assert (purchase.draw_no, purchase.numbers, purchase.cost) == (1, (3, 7, 19), 300)
        assert purchase.new_balance == 700 and purchase.pot == 300
        assert await _balance(db, 1) == 700
        assert await db.user_lottery_numbers(1) == [3, 7, 19]
        assert await db.lottery_tickets_sold() == 3
        assert await db.get_pot() == 300
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_the_ledger_and_pot_entries_record_the_purchase(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        await _buy(db, 1, [3, 7])
        async with db.conn.execute(
            "SELECT delta, reason, balance_after FROM renown_transactions "
            "WHERE reason = 'ticket:lottery';"
        ) as cur:
            assert await cur.fetchall() == [(-200, "ticket:lottery", 800)]
        async with db.conn.execute(
            "SELECT source, amount FROM lottery_pot_entries WHERE source = 'ticket';"
        ) as cur:
            assert await cur.fetchall() == [("ticket", 200)]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_not_enough_renown_changes_nothing(tmp_path: Path) -> None:
    db = await _db(tmp_path, balance=150)
    try:
        with pytest.raises(InsufficientFundsError):
            await _buy(db, 1, [3, 7])
        assert await _balance(db, 1) == 150 and await db.get_pot() == 0
        assert await db.user_lottery_numbers(1) == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_the_per_draw_cap_counts_what_is_already_held(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        await _buy(db, 1, [1, 2, 3, 4, 5, 6, 7, 8])
        with pytest.raises(TicketLimitError):
            await _buy(db, 1, [9, 10, 11])
        await _buy(db, 1, [9, 10])  # exactly the cap
        assert len(await db.user_lottery_numbers(1)) == 10
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_the_same_number_cannot_be_bought_twice_but_two_members_may_share_one(
    tmp_path: Path,
) -> None:
    db = await _db(tmp_path)
    try:
        await _buy(db, 1, [7])
        with pytest.raises(InvalidGameChoiceError, match="7"):
            await _buy(db, 1, [7, 8])
        assert await db.user_lottery_numbers(1) == [7]  # the 8 was not bought either
        await _buy(db, 2, [7])
        assert await db.lottery_tickets_sold() == 2
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_sales_close_at_the_draw_time_and_before_a_schedule_exists(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        await _buy(db, 1, [1], now=NOW + WEEK - 1)  # the last second
        with pytest.raises(LotteryClosedError):
            await _buy(db, 1, [2], now=NOW + WEEK)
        assert await _balance(db, 1) == 900
    finally:
        await db.close()
    fresh = Database(tmp_path / "fresh.db")
    await fresh.initialize()
    try:
        await fresh.ensure_wallet(1, 1000)
        with pytest.raises(LotteryClosedError):
            await _buy(fresh, 1, [1])
    finally:
        await fresh.close()


@pytest.mark.asyncio
async def test_a_failure_part_way_rolls_everything_back(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        with patch.object(db, "_credit_pot", new=AsyncMock(side_effect=RuntimeError("boom"))):
            with pytest.raises(RuntimeError):
                await _buy(db, 1, [3, 7])
        assert await _balance(db, 1) == 1000 and await db.get_pot() == 0
        assert await db.user_lottery_numbers(1) == []
        await _buy(db, 1, [3, 7])  # the lock was released
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_two_simultaneous_purchases_cannot_pass_the_cap(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        results = await asyncio.gather(
            _buy(db, 1, [1, 2, 3, 4, 5, 6]),
            _buy(db, 1, [11, 12, 13, 14, 15, 16]),
            return_exceptions=True,
        )
        assert sum(isinstance(r, TicketLimitError) for r in results) == 1
        assert len(await db.user_lottery_numbers(1)) == 6
        assert await _balance(db, 1) == 400
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_member_without_a_wallet_gets_the_welcome_bonus_first(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        purchase = await db.buy_lottery_tickets(
            9, [4], price=100, max_per_user=10, welcome_bonus=200, now=NOW + 5
        )
        assert purchase.new_balance == 100
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_buying_nothing_is_a_programming_error(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        with pytest.raises(ValueError):
            await _buy(db, 1, [])
    finally:
        await db.close()
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_lottery_tickets.py -q`
Expected: failures: `Database` has no `buy_lottery_tickets`.

- [ ] **Step 3: Implement**

In `src/yokai/storage/db.py` extend the errors import with `InvalidGameChoiceError, LotteryClosedError, TicketLimitError` and add after `user_lottery_numbers`:
```python
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
```
Add `Sequence` to the `typing` import in `db.py`. Note: the `InsufficientFundsError` message reads "...which isn't enough to buy tickets **300**."; if that wording reads badly, pass `action="spend"` instead and keep the test (it only asserts the exception type).

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_lottery_tickets.py -q`
Expected: all pass.

- [ ] **Step 5: Suite, lint, commit**

```powershell
python -m pytest -q; python -m ruff check .; python -m ruff format .; python -m ruff format --check .
git add src/yokai/storage/db.py tests/test_lottery_tickets.py
git commit -m "feat(lottery): buy tickets in one transaction" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>" -m "Claude-Session: https://claude.ai/code/session_01BxUkTAJGP4uNByTtyvj8n9"
```

---

### Task 5: The draw

**Files:**
- Modify: `src/yokai/storage/db.py` (new methods after `buy_lottery_tickets`)
- Create: `tests/test_lottery_draw.py`

**Interfaces:**
- Consumes: `engines.lottery.split_pot` (Task 2), `LotteryDraw` (Task 3).
- Produces: `Database.run_lottery_draw(winning_number: int, interval_s: int, now: Optional[int] = None) -> Optional[LotteryDraw]` (None when the draw is not due or the schedule has not started); `Database.get_unannounced_draws() -> list[LotteryDraw]`; `Database.mark_draw_announced(draw_no) -> None`; `Database.get_last_draw() -> Optional[LotteryDraw]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_lottery_draw.py`:
```python
"""The weekly draw is one atomic, idempotent transaction."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from yokai.storage.db import Database

NOW = 1_000_000
WEEK = 7 * 24 * 3600
DUE = NOW + WEEK


async def _db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "draw.db")
    await db.initialize()
    for user_id in (1, 2, 3):
        await db.ensure_wallet(user_id, 1000)
    await db.ensure_lottery_schedule(NOW, WEEK)
    return db


async def _buy(db: Database, user_id: int, numbers: list[int]) -> None:
    await db.buy_lottery_tickets(
        user_id, numbers, price=100, max_per_user=10, welcome_bonus=0, now=NOW + 5
    )


async def _balance(db: Database, user_id: int) -> int:
    wallet = await db.get_wallet(user_id)
    assert wallet is not None
    return wallet.balance


@pytest.mark.asyncio
async def test_one_winner_takes_the_pot_and_the_next_draw_starts_empty(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        await _buy(db, 1, [7, 8])
        await _buy(db, 2, [9])
        draw = await db.run_lottery_draw(7, WEEK, now=DUE)
        assert draw is not None
        assert (draw.draw_no, draw.winning_number, draw.pot) == (1, 7, 300)
        assert (draw.winners, draw.prize_each, draw.announced) == ((1,), 300, False)
        assert await _balance(db, 1) == 800 + 300
        assert await db.get_pot() == 0
        state = await db.get_lottery_state()
        assert (state.draw_no, state.next_draw_ts) == (2, DUE + WEEK)
        assert await db.lottery_tickets_sold() == 0  # the new draw has no tickets
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_holders_of_the_winning_number_split_it_and_the_remainder_stays(
    tmp_path: Path,
) -> None:
    db = await _db(tmp_path)
    try:
        await _buy(db, 1, [7])
        await _buy(db, 2, [7])
        await _buy(db, 3, [7, 8])  # three tickets on 7 -> pot 400 is split three ways
        draw = await db.run_lottery_draw(7, WEEK, now=DUE)
        assert draw is not None
        assert draw.winners == (1, 2, 3) and draw.prize_each == 133
        assert await db.get_pot() == 1  # 400 - 3 * 133
        assert await _balance(db, 1) == 900 + 133
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_no_holder_rolls_the_pot_over_unchanged(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        await _buy(db, 1, [7])
        draw = await db.run_lottery_draw(12, WEEK, now=DUE)
        assert draw is not None and draw.winners == () and draw.prize_each == 0
        assert await db.get_pot() == 100
        assert await _balance(db, 1) == 900
        async with db.conn.execute("SELECT COUNT(*) FROM renown_transactions WHERE reason = 'win:lottery';") as cur:
            assert (await cur.fetchone())[0] == 0  # type: ignore[index]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_rolled_over_pot_is_won_by_a_later_draw_with_new_tickets_only(
    tmp_path: Path,
) -> None:
    db = await _db(tmp_path)
    try:
        await _buy(db, 1, [7])
        await db.run_lottery_draw(12, WEEK, now=DUE)  # nobody wins; pot 100 rolls over
        await db.buy_lottery_tickets(
            2, [5], price=100, max_per_user=10, welcome_bonus=0, now=DUE + 10
        )
        # user 1's old ticket on 7 must not count in draw 2
        draw = await db.run_lottery_draw(7, WEEK, now=DUE + WEEK)
        assert draw is not None and draw.draw_no == 2 and draw.winners == ()
        assert await db.get_pot() == 200
        draw3 = await db.run_lottery_draw(5, WEEK, now=DUE + 2 * WEEK)
        assert draw3 is not None and draw3.winners == (2,) and draw3.prize_each == 200
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_draw_that_is_not_due_does_nothing(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        await _buy(db, 1, [7])
        assert await db.run_lottery_draw(7, WEEK, now=DUE - 1) is None
        state = await db.get_lottery_state()
        assert (state.draw_no, state.next_draw_ts, state.pot) == (1, DUE, 100)
        assert await _balance(db, 1) == 900
        await _buy(db, 2, [3])  # the lock was released
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_drawing_twice_pays_once(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        await _buy(db, 1, [7])
        first = await db.run_lottery_draw(7, WEEK, now=DUE)
        second = await db.run_lottery_draw(7, WEEK, now=DUE)
        assert first is not None and second is None
        assert await _balance(db, 1) == 900 + 100
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_an_offline_bot_draws_once_and_the_schedule_jumps_to_the_future(
    tmp_path: Path,
) -> None:
    db = await _db(tmp_path)
    try:
        await _buy(db, 1, [7])
        late = DUE + 3 * WEEK + 500  # offline for three and a bit weeks
        draw = await db.run_lottery_draw(7, WEEK, now=late)
        assert draw is not None
        state = await db.get_lottery_state()
        assert state.next_draw_ts == DUE + 4 * WEEK and state.next_draw_ts > late
        assert await db.run_lottery_draw(7, WEEK, now=late) is None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_the_ledger_records_each_prize(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        await _buy(db, 1, [7])
        await _buy(db, 2, [7])
        await db.run_lottery_draw(7, WEEK, now=DUE)
        async with db.conn.execute(
            "SELECT user_id, delta, reason FROM renown_transactions "
            "WHERE reason = 'win:lottery' ORDER BY user_id;"
        ) as cur:
            assert await cur.fetchall() == [(1, 100, "win:lottery"), (2, 100, "win:lottery")]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_failure_part_way_leaves_the_draw_undone(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        await _buy(db, 1, [7])
        real = db._insert_transaction
        calls = {"n": 0}

        async def failing(*args, **kwargs):  # type: ignore[no-untyped-def]
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")
            return await real(*args, **kwargs)

        with patch.object(db, "_insert_transaction", new=failing):
            with pytest.raises(RuntimeError):
                await db.run_lottery_draw(7, WEEK, now=DUE)
        state = await db.get_lottery_state()
        assert (state.draw_no, state.pot) == (1, 100)
        assert await _balance(db, 1) == 900
        assert await db.get_last_draw() is None
        draw = await db.run_lottery_draw(7, WEEK, now=DUE)  # and it can still be drawn
        assert draw is not None and draw.winners == (1,)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_announcements_are_tracked_until_marked(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        await _buy(db, 1, [7])
        await db.run_lottery_draw(7, WEEK, now=DUE)
        pending = await db.get_unannounced_draws()
        assert [d.draw_no for d in pending] == [1]
        await db.mark_draw_announced(1)
        assert await db.get_unannounced_draws() == []
        last = await db.get_last_draw()
        assert last is not None and last.announced is True and last.winners == (1,)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_draw_before_the_clock_has_started_does_nothing(tmp_path: Path) -> None:
    db = Database(tmp_path / "noclock.db")
    await db.initialize()
    try:
        assert await db.run_lottery_draw(7, WEEK, now=DUE) is None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_an_empty_draw_is_still_recorded_and_advances(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        draw = await db.run_lottery_draw(3, WEEK, now=DUE)
        assert draw is not None and draw.winners == () and draw.pot == 0
        assert (await db.get_lottery_state()).draw_no == 2
    finally:
        await db.close()
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_lottery_draw.py -q`
Expected: failures: `Database` has no `run_lottery_draw`.

- [ ] **Step 3: Implement**

In `src/yokai/storage/db.py` add `import json` and `from yokai.gambling.engines.lottery import split_pot` to the imports and these methods after `buy_lottery_tickets`:
```python
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
            f"SELECT {self._DRAW_COLUMNS} FROM lottery_draws WHERE announced = 0 "
            "ORDER BY draw_no;"
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
```
The `_DRAW_COLUMNS` class attribute must sit above its first use inside the class body; move it above `_draw_from_row` if ruff or the interpreter complains.

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_lottery_draw.py -q`
Expected: all pass. If `test_a_failure_part_way_leaves_the_draw_undone` fails because `_write_tx` did not roll back the explicit `BEGIN IMMEDIATE`, check that the patched `_insert_transaction` raised inside the `async with self._write_tx()` block (it does) and that no `commit()` ran before it.

- [ ] **Step 5: Suite, lint, commit**

```powershell
python -m pytest -q; python -m ruff check .; python -m ruff format .; python -m ruff format --check .
git add src/yokai/storage/db.py tests/test_lottery_draw.py
git commit -m "feat(lottery): run the weekly draw atomically" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>" -m "Claude-Session: https://claude.ai/code/session_01BxUkTAJGP4uNByTtyvj8n9"
```

---

### Task 6: Embeds

**Files:**
- Modify: `src/yokai/ui/embeds.py` (append classmethods inside `EmbedFactory` after `bjtable_hand`), `scripts/preview_embeds.py`, `docs/preview/embeds.html`
- Create: `tests/test_lottery_embeds.py`

**Interfaces:**
- Produces on `EmbedFactory`:
  - `lottery_panel(pot, tickets_sold, number_range, price, max_tickets, next_draw_ts: Optional[int], last_draw: Optional[tuple[int, Sequence[int], int]], bot_avatar_url=None)` where `last_draw` is `(winning_number, winners, prize_each)`.
  - `lottery_receipt(numbers: Sequence[int], cost, pot, balance, next_draw_ts: Optional[int], bot_avatar_url=None)`.
  - `lottery_announcement(draw_no, winning_number, pot, winners: Sequence[int], prize_each, next_pot, next_draw_ts: Optional[int], bot_avatar_url=None)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_lottery_embeds.py`:
```python
"""The lottery embeds obey the layout rules and carry the facts people need."""

from __future__ import annotations

import pytest

from tests.embed_helpers import all_embed_text, emoji_count, total_embed_length
from yokai.theme import COLOR_BRAND, COLOR_SUCCESS
from yokai.ui.embeds import EmbedFactory

NEXT = 1_900_000_000


def _embeds() -> list[tuple[str, object]]:
    return [
        ("panel", EmbedFactory.lottery_panel(1500, 12, 20, 100, 10, NEXT, None)),
        ("panel-last", EmbedFactory.lottery_panel(0, 0, 20, 100, 10, NEXT, (7, [1, 2], 750))),
        ("panel-stopped", EmbedFactory.lottery_panel(0, 0, 20, 100, 10, None, None)),
        ("receipt", EmbedFactory.lottery_receipt([3, 7, 19], 300, 1800, 700, NEXT)),
        ("win", EmbedFactory.lottery_announcement(4, 7, 1500, [1, 2], 750, 0, NEXT)),
        ("rollover", EmbedFactory.lottery_announcement(5, 12, 1500, [], 0, 1500, NEXT)),
        (
            "crowd",
            EmbedFactory.lottery_announcement(6, 3, 999_999, list(range(1, 41)), 24_999, 39, NEXT),
        ),
    ]


@pytest.mark.parametrize("label,embed", _embeds())
def test_every_lottery_embed_obeys_the_layout_rules(label: str, embed) -> None:  # type: ignore[no-untyped-def]
    assert emoji_count(embed) <= 1, label
    assert total_embed_length(embed) <= 6000, label
    assert len(embed.fields) <= 25, label


def test_the_panel_shows_pot_tickets_price_range_and_a_live_countdown() -> None:
    text = all_embed_text(EmbedFactory.lottery_panel(1500, 12, 20, 100, 10, NEXT, None))
    assert "1,500" in text and "12" in text and "100" in text
    assert "1 to 20" in text and "10" in text and f"<t:{NEXT}:R>" in text
    assert "Buy tickets" in text


def test_the_panel_names_the_last_draw() -> None:
    text = all_embed_text(EmbedFactory.lottery_panel(0, 0, 20, 100, 10, NEXT, (7, [1, 2], 750)))
    assert "7" in text and "<@1>" in text and "<@2>" in text and "750" in text


def test_a_panel_before_the_clock_starts_has_no_countdown() -> None:
    text = all_embed_text(EmbedFactory.lottery_panel(0, 0, 20, 100, 10, None, None))
    assert "<t:" not in text


def test_the_receipt_lists_the_numbers_cost_and_balance() -> None:
    text = all_embed_text(EmbedFactory.lottery_receipt([3, 7, 19], 300, 1800, 700, NEXT))
    assert "3, 7, 19" in text and "300" in text and "700" in text and "1,800" in text


def test_a_win_announcement_names_the_number_the_winners_and_the_prize() -> None:
    embed = EmbedFactory.lottery_announcement(4, 7, 1500, [1, 2], 750, 0, NEXT)
    text = all_embed_text(embed)
    assert "**7**" in text and "<@1>" in text and "<@2>" in text and "750" in text
    assert embed.color is not None and embed.color.value == COLOR_SUCCESS


def test_a_rollover_announcement_says_nobody_won_and_shows_the_new_pot() -> None:
    embed = EmbedFactory.lottery_announcement(5, 12, 1500, [], 0, 1500, NEXT)
    text = all_embed_text(embed).lower()
    assert "nobody" in text and "rolls over" in text and "1,500" in text
    assert embed.color is not None and embed.color.value == COLOR_BRAND


def test_a_huge_crowd_of_winners_is_summarised_not_listed_in_full() -> None:
    embed = EmbedFactory.lottery_announcement(6, 3, 999_999, list(range(1, 41)), 24_999, 39, NEXT)
    assert total_embed_length(embed) <= 6000
    assert "40" in all_embed_text(embed)
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_lottery_embeds.py -q`
Expected: collection error: `EmbedFactory` has no attribute `lottery_panel`.

- [ ] **Step 3: Implement**

Append inside `EmbedFactory` in `src/yokai/ui/embeds.py` (after `bjtable_hand`; real `"\n"` newlines):
```python
    @classmethod
    def lottery_panel(
        cls,
        pot: int,
        tickets_sold: int,
        number_range: int,
        price: int,
        max_tickets: int,
        next_draw_ts: Optional[int],
        last_draw: Optional[tuple[int, Sequence[int], int]],  # (winning number, winners, prize each)
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        """The always-on lottery embed: the pot, the odds ingredients and the next draw."""
        lines = [
            f"Pick numbers from **1 to {number_range}** (up to {max_tickets} tickets each) at "
            f"**{price:,}** Renown a ticket. One number is drawn; everyone holding it splits "
            "the pot. If nobody holds it, the pot rolls over and grows.",
            "",
            "Press **Buy tickets** to pick your numbers or take quick picks.",
        ]
        if last_draw is not None:
            number, winners, prize_each = last_draw
            if winners:
                names = " ".join(f"<@{user_id}>" for user_id in winners[:10])
                lines.append(f"\n**Last draw:** {number} — {names} won **{prize_each:,}** each.")
            else:
                lines.append(f"\n**Last draw:** {number} — nobody held it.")
        fields: list[FieldSpec] = [
            ("Pot", f"**{pot:,}**", True),
            ("Tickets sold", f"**{tickets_sold:,}**", True),
            ("Next draw", f"<t:{next_draw_ts}:R>" if next_draw_ts else "Not started", True),
        ]
        return cls._create_base(
            title=f"{Emoji.COIN} Lottery",
            description="\n".join(lines),
            color=COLOR_BRAND,
            context="Gambling",
            bot_avatar_url=bot_avatar_url,
            fields=_grid(fields),
        )

    @classmethod
    def lottery_receipt(
        cls,
        numbers: Sequence[int],
        cost: int,
        pot: int,
        balance: int,
        next_draw_ts: Optional[int],
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        """The private confirmation after buying tickets."""
        fields: list[FieldSpec] = [
            ("Cost", f"**{cost:,}**", True),
            ("Pot", f"**{pot:,}**", True),
            ("Balance", f"**{balance:,}**", True),
        ]
        when = f" Next draw <t:{next_draw_ts}:R>." if next_draw_ts else ""
        return cls._create_base(
            title=f"{Emoji.COIN} Tickets bought",
            description=f"Your numbers: **{', '.join(str(n) for n in numbers)}**.{when}",
            color=COLOR_SUCCESS,
            context="Gambling",
            bot_avatar_url=bot_avatar_url,
            fields=_grid(fields),
        )

    @classmethod
    def lottery_announcement(
        cls,
        draw_no: int,
        winning_number: int,
        pot: int,
        winners: Sequence[int],
        prize_each: int,
        next_pot: int,
        next_draw_ts: Optional[int],
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        """The public result of a draw."""
        when = f"\nNext draw <t:{next_draw_ts}:R>." if next_draw_ts else ""
        if winners:
            shown = " ".join(f"<@{user_id}>" for user_id in winners[:20])
            more = f" and {len(winners) - 20} more" if len(winners) > 20 else ""
            body = (
                f"The winning number is **{winning_number}**.\n"
                f"**{len(winners)}** held it: {shown}{more}\n"
                f"Each wins **{prize_each:,}** Renown.{when}"
            )
            color, quip_key = COLOR_SUCCESS, "game_win"
        else:
            body = (
                f"The winning number is **{winning_number}**.\n"
                f"Nobody held it, so the pot rolls over. It now stands at **{next_pot:,}**.{when}"
            )
            color, quip_key = COLOR_BRAND, "game_loss"
        fields: list[FieldSpec] = [("Pot", f"**{pot:,}**", True)]
        return cls._create_base(
            title=f"{Emoji.COIN} Lottery draw #{draw_no}",
            description=body,
            color=color,
            context="Gambling",
            bot_avatar_url=bot_avatar_url,
            quip=quip_bank.get(quip_key),
            fields=_grid(fields),
        )
```
Add before the closing `    ]` of `samples` in `scripts/preview_embeds.py`, then run `python scripts/preview_embeds.py`:
```python
        (
            "Lottery (panel)",
            EmbedFactory.lottery_panel(
                1500, 12, 20, 100, 10, 1_900_000_000, (7, [42, 43], 750), bot_avatar_url=av
            ),
        ),
        (
            "Lottery (tickets bought)",
            EmbedFactory.lottery_receipt(
                [3, 7, 19], 300, 1800, 700, 1_900_000_000, bot_avatar_url=av
            ),
        ),
        (
            "Lottery (draw won)",
            EmbedFactory.lottery_announcement(
                4, 7, 1500, [42, 43], 750, 0, 1_900_000_000, bot_avatar_url=av
            ),
        ),
        (
            "Lottery (rollover)",
            EmbedFactory.lottery_announcement(
                5, 12, 1500, [], 0, 1500, 1_900_000_000, bot_avatar_url=av
            ),
        ),
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_lottery_embeds.py tests/test_preview_embeds.py -q`
Expected: all pass. Note the `crowd` case: the announcement lists at most 20 mentions and says "and 20 more", and the text must contain "40" (the holder count).

- [ ] **Step 5: Suite, lint, commit**

```powershell
python -m pytest -q; python -m ruff check .; python -m ruff format .; python -m ruff format --check .
git add src/yokai/ui/embeds.py scripts/preview_embeds.py docs/preview/embeds.html tests/test_lottery_embeds.py
git commit -m "feat(lottery): add the lottery panel, receipt and announcement embeds" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>" -m "Claude-Session: https://claude.ai/code/session_01BxUkTAJGP4uNByTtyvj8n9"
```

---

### Task 7: The Buy tickets view and form

**Files:**
- Create: `src/yokai/ui/lottery_views.py`
- Create: `tests/test_lottery_views.py`

**Interfaces:**
- Consumes: `plan_tickets` (Task 2), `Database.buy_lottery_tickets/user_lottery_numbers/get_lottery_state` (Tasks 3, 4), `EmbedFactory.lottery_receipt` (Task 6), `ui.table_common` helpers, `bot.config.lottery_*`, `bot.get_cog("Lottery").update_panel()` (Task 8; the view calls it through `getattr` and tolerates a missing cog).
- Produces: `BUY_CUSTOM_ID = "yokai:lottery:buy"`; `BuyView(bot)` (persistent); `handle_buy(bot, interaction)`; `TicketsModal(bot)` with `numbers` and `quick` text inputs and `on_submit`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_lottery_views.py`:
```python
"""The Buy tickets button and form, against a real database and mocked Discord."""

from __future__ import annotations

import random
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from tests.moderation_helpers import fake_moderation
from yokai.storage.db import Database
from yokai.ui.lottery_views import BUY_CUSTOM_ID, BuyView, TicketsModal, handle_buy

CHANNEL = 10
WEEK = 7 * 24 * 3600


async def _world(tmp_path: Path, balance: int = 1000):  # type: ignore[no-untyped-def]
    db = Database(tmp_path / "views.db")
    await db.initialize()
    await db.ensure_wallet(1, balance)
    await db.set_multiplayer_channel(CHANNEL, "lottery", 1)
    await db.ensure_lottery_schedule(1, 10**12)  # a draw far in the future: sales are open
    return db


def make_bot(db: Database) -> MagicMock:
    bot = MagicMock()
    bot.user = None
    bot.db = db
    bot.config.economy_welcome_bonus = 0
    bot.config.lottery_ticket_price = 100
    bot.config.lottery_number_range = 20
    bot.config.lottery_max_tickets_per_user = 10
    bot.moderation = fake_moderation(owner_id=999)
    panel = MagicMock()
    panel.update_panel = AsyncMock()
    bot.get_cog = MagicMock(return_value=panel)
    bot.rng = random.Random(3)
    return bot


def make_interaction(user_id: int = 1, channel_id: int = CHANNEL) -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock(spec=discord.Member)
    interaction.user.id = user_id
    interaction.channel_id = channel_id
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.response.send_modal = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


async def _submit(bot, numbers: str, quick: str):  # type: ignore[no-untyped-def]
    modal = TicketsModal(bot)
    modal.numbers._value = numbers  # type: ignore[attr-defined]
    modal.quick._value = quick  # type: ignore[attr-defined]
    interaction = make_interaction()
    with (
        patch("yokai.ui.lottery_views.send", new=AsyncMock()) as sent,
        patch("yokai.ui.table_common.send", new=AsyncMock()) as failed,
        patch("yokai.ui.lottery_views.new_rng", return_value=bot.rng),
    ):
        await modal.on_submit(interaction)
    return interaction, sent, failed


def test_the_buy_view_is_persistent_with_a_fixed_custom_id() -> None:
    view = BuyView(MagicMock())
    assert view.timeout is None and view.is_persistent()
    assert [item.custom_id for item in view.children] == [BUY_CUSTOM_ID]  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_pressing_buy_in_the_lottery_channel_opens_the_form(tmp_path: Path) -> None:
    db = await _world(tmp_path)
    try:
        interaction = make_interaction()
        await handle_buy(make_bot(db), interaction)
        modal = interaction.response.send_modal.await_args.args[0]
        assert isinstance(modal, TicketsModal)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_pressing_buy_elsewhere_is_refused_ephemerally(tmp_path: Path) -> None:
    db = await _world(tmp_path)
    try:
        interaction = make_interaction(channel_id=12345)
        with patch("yokai.ui.table_common.send", new=AsyncMock()) as sent:
            await handle_buy(make_bot(db), interaction)
        interaction.response.send_modal.assert_not_awaited()
        assert sent.await_args.kwargs["ephemeral"] is True
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_pressing_buy_in_a_table_channel_is_refused(tmp_path: Path) -> None:
    db = await _world(tmp_path)
    try:
        await db.set_multiplayer_channel(30, "duel", 2)
        interaction = make_interaction(channel_id=30)
        with patch("yokai.ui.table_common.send", new=AsyncMock()):
            await handle_buy(make_bot(db), interaction)
        interaction.response.send_modal.assert_not_awaited()
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_pressing_buy_with_too_little_renown_is_refused(tmp_path: Path) -> None:
    db = await _world(tmp_path, balance=50)
    try:
        interaction = make_interaction()
        with patch("yokai.ui.table_common.send", new=AsyncMock()) as sent:
            await handle_buy(make_bot(db), interaction)
        interaction.response.send_modal.assert_not_awaited()
        assert "isn't enough" in sent.await_args.kwargs["embed"].description
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_pressing_buy_after_the_draw_time_is_refused(tmp_path: Path) -> None:
    db = await _world(tmp_path)
    try:
        await db.conn.execute("UPDATE lottery_state SET next_draw_ts = 2 WHERE id = 1;")
        await db.conn.commit()
        interaction = make_interaction()
        with patch("yokai.ui.table_common.send", new=AsyncMock()) as sent:
            await handle_buy(make_bot(db), interaction)
        interaction.response.send_modal.assert_not_awaited()
        assert "closed" in sent.await_args.kwargs["embed"].description.lower()
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_hand_picked_numbers_are_bought_and_receipted_privately(tmp_path: Path) -> None:
    db = await _world(tmp_path)
    try:
        bot = make_bot(db)
        interaction, sent, failed = await _submit(bot, "3, 7, 19", "")
        interaction.response.defer.assert_awaited_once()
        assert await db.user_lottery_numbers(1) == [3, 7, 19]
        assert sent.await_args.kwargs["ephemeral"] is True
        assert "3, 7, 19" in sent.await_args.kwargs["embed"].description
        failed.assert_not_awaited()
        bot.get_cog.return_value.update_panel.assert_awaited_once()
        wallet = await db.get_wallet(1)
        assert wallet is not None and wallet.balance == 700
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_quick_picks_fill_in_without_clashing_with_chosen_numbers(tmp_path: Path) -> None:
    db = await _world(tmp_path)
    try:
        bot = make_bot(db)
        await _submit(bot, "5, 6", "3")
        held = await db.user_lottery_numbers(1)
        assert len(held) == 5 and {5, 6} <= set(held)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_hostile_input_is_an_ephemeral_error_and_costs_nothing(tmp_path: Path) -> None:
    db = await _world(tmp_path)
    try:
        bot = make_bot(db)
        for numbers, quick in (
            ("abc", ""),
            ("0, 21", ""),
            ("-5", ""),
            ("@everyone **x**", ""),
            ("9" * 4000, ""),
            ("", "99999999"),
            ("", "-3"),
            ("", ""),
            ("1,2,3,4,5,6,7,8,9,10,11", ""),
        ):
            _, sent, failed = await _submit(bot, numbers, quick)
            assert failed.await_args.kwargs["ephemeral"] is True, (numbers, quick)
            sent.assert_not_awaited()
        assert await db.user_lottery_numbers(1) == []
        wallet = await db.get_wallet(1)
        assert wallet is not None and wallet.balance == 1000
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_database_failure_is_answered_not_left_hanging(tmp_path: Path) -> None:
    import sqlite3

    db = await _world(tmp_path)
    try:
        bot = make_bot(db)
        broken = AsyncMock(side_effect=sqlite3.OperationalError("disk I/O error"))
        with patch.object(db, "buy_lottery_tickets", new=broken):
            _, sent, failed = await _submit(bot, "3", "")
        assert failed.await_args.kwargs["ephemeral"] is True
        sent.assert_not_awaited()
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_panel_that_cannot_be_updated_does_not_undo_the_purchase(tmp_path: Path) -> None:
    db = await _world(tmp_path)
    try:
        bot = make_bot(db)
        bot.get_cog.return_value.update_panel = AsyncMock(
            side_effect=discord.HTTPException(MagicMock(status=500, reason="x"), "boom")
        )
        interaction, sent, failed = await _submit(bot, "3", "")
        assert await db.user_lottery_numbers(1) == [3]
        sent.assert_awaited_once()
        failed.assert_not_awaited()
    finally:
        await db.close()
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_lottery_views.py -q`
Expected: collection ERROR `No module named 'yokai.ui.lottery_views'`.

- [ ] **Step 3: Implement**

Create `src/yokai/ui/lottery_views.py`:
```python
"""The lottery channel's Buy tickets button and form."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

import aiosqlite
import discord

from yokai.errors import (
    ChannelNotConfiguredError,
    InsufficientFundsError,
    LotteryClosedError,
    YokaiError,
)
from yokai.gambling.engines.lottery import plan_tickets
from yokai.gambling.rng import new_rng
from yokai.moderation.gate import screen
from yokai.ui.embeds import EmbedFactory, send
from yokai.ui.table_common import avatar_url, reply_error

if TYPE_CHECKING:
    from yokai.bot import YokaiBot

logger = logging.getLogger(__name__)

BUY_CUSTOM_ID = "yokai:lottery:buy"


class TicketsModal(discord.ui.Modal):
    """Hand-picked numbers and/or a quick-pick count."""

    numbers = discord.ui.TextInput(
        label="Numbers (e.g. 3, 7, 19)",
        placeholder="Leave blank for quick picks only",
        required=False,
        max_length=100,
    )
    quick = discord.ui.TextInput(
        label="Quick picks (how many)",
        placeholder="Random free numbers, e.g. 3",
        required=False,
        max_length=3,
    )

    def __init__(self, bot: YokaiBot) -> None:
        super().__init__(title="Buy lottery tickets", timeout=300)
        self.bot = bot

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return not await screen(interaction)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        # thinking=True makes this a private reply of its own; a plain defer on a form opened
        # from a button silently updates the button's message and ignores `ephemeral`.
        await interaction.response.defer(ephemeral=True, thinking=True)
        config = self.bot.config
        user_id = interaction.user.id
        try:
            held = await self.bot.db.user_lottery_numbers(user_id)
            numbers = plan_tickets(
                held,
                self.numbers.value,
                self.quick.value,
                config.lottery_number_range,
                config.lottery_max_tickets_per_user,
                new_rng(),
            )
            purchase = await self.bot.db.buy_lottery_tickets(
                user_id,
                numbers,
                price=config.lottery_ticket_price,
                max_per_user=config.lottery_max_tickets_per_user,
                welcome_bonus=config.economy_welcome_bonus,
            )
            state = await self.bot.db.get_lottery_state()
        except YokaiError as err:
            await reply_error(self.bot, interaction, err)
            return
        except aiosqlite.Error as exc:
            logger.error("Buying lottery tickets failed: %s", exc, exc_info=exc)
            await reply_error(
                self.bot,
                interaction,
                YokaiError(
                    "Something went wrong on my end, and nothing was charged.",
                    user_hint="Try again in a moment.",
                ),
            )
            return
        embed = EmbedFactory.lottery_receipt(
            purchase.numbers,
            purchase.cost,
            purchase.pot,
            purchase.new_balance,
            state.next_draw_ts,
            bot_avatar_url=avatar_url(self.bot),
        )
        await send(interaction, embed=embed, ephemeral=True)
        panel = self.bot.get_cog("Lottery")
        if panel is not None:
            try:
                await panel.update_panel()  # type: ignore[attr-defined]
            except (discord.HTTPException, aiosqlite.Error) as exc:
                logger.warning("Could not update the lottery panel: %s", exc)


async def handle_buy(bot: YokaiBot, interaction: discord.Interaction) -> None:
    """Buy tickets was pressed: check the channel, the sales window and the balance first."""
    channel_id = interaction.channel_id
    try:
        row = await bot.db.get_multiplayer_channel(channel_id) if channel_id else None
        if row is None or row.game != "lottery":
            raise ChannelNotConfiguredError()
        state = await bot.db.get_lottery_state()
        if state.next_draw_ts is None or int(time.time()) >= state.next_draw_ts:
            raise LotteryClosedError()
        price = bot.config.lottery_ticket_price
        wallet = await bot.db.ensure_wallet(interaction.user.id, bot.config.economy_welcome_bonus)
        if wallet.balance < price:
            raise InsufficientFundsError(
                balance=wallet.balance, amount=price, action="buy a ticket for"
            )
    except YokaiError as err:
        await reply_error(bot, interaction, err)
        return
    await interaction.response.send_modal(TicketsModal(bot))


class BuyView(discord.ui.View):
    """The always-on Buy tickets button under the lottery panel."""

    def __init__(self, bot: YokaiBot) -> None:
        super().__init__(timeout=None)
        self.bot = bot

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return not await screen(interaction)

    @discord.ui.button(
        label="Buy tickets", style=discord.ButtonStyle.success, custom_id=BUY_CUSTOM_ID
    )
    async def buy_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await handle_buy(self.bot, interaction)
```
Also add, to `tests/test_lottery_views.py`, a regression test shaped like `test_the_stake_form_asks_discord_for_a_private_thinking_reply` in `tests/test_table_views.py` (a real `discord.InteractionResponse` over a capturing adapter) asserting that `TicketsModal.on_submit` defers with type 5 and `{"flags": 64}`.

Note for the implementer: the test helper patches `yokai.ui.lottery_views.new_rng` so quick picks are deterministic; `plan_tickets` is called with `new_rng()` inside `on_submit` for exactly that reason. The `numbers`/`quick` attribute names are checked by `tests/test_view_names.py`; `Modal` has no members called `numbers` or `quick`.

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_lottery_views.py tests/test_view_names.py -q`
Expected: all pass. A hostile-input case that unexpectedly buys tickets, or raises something other than a `YokaiError`, is a real bug in `plan_tickets` or the modal: fix the code, not the test.

- [ ] **Step 5: Suite, lint, commit**

```powershell
python -m pytest -q; python -m ruff check .; python -m ruff format .; python -m ruff format --check .
git add src/yokai/ui/lottery_views.py tests/test_lottery_views.py
git commit -m "feat(lottery): add the Buy tickets button and form" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>" -m "Claude-Session: https://claude.ai/code/session_01BxUkTAJGP4uNByTtyvj8n9"
```

---

### Task 8: The lottery cog, channel dedication and wiring

**Files:**
- Create: `src/yokai/cogs/lottery.py`
- Modify: `src/yokai/gambling/lobby.py` (`GameSpec.is_table`, lottery spec), `src/yokai/gambling/tables.py` (`open_table` guard), `src/yokai/cogs/tables.py` (delegate the lottery panel, generic hint), `src/yokai/bot.py` (load the cog), `tests/test_one_emoji_rule.py`
- Create: `tests/test_lottery_cog.py`
- Modify: `tests/test_lobby.py`, `tests/test_table_manager.py`, `tests/test_tables_cog.py`, `tests/test_bot_wiring.py`

**Interfaces:**
- Consumes: Tasks 1 to 7.
- Produces: `LotteryCog(bot)` with `cog_load()` (registers `BuyView`, starts the scheduler task), `cog_unload()`, `async panel_embed() -> discord.Embed`, `async post_panel(channel) -> discord.Message` (also starts the draw clock), `async update_panel() -> None`, `async tick(now: Optional[int] = None) -> None`, `async announce(draw) -> None`; module constant `CHECK_SECONDS = 60`; `GameSpec.is_table: bool = True` (False for the lottery, which is also `available=True`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_lottery_cog.py`:
```python
"""LotteryCog: the panel, the scheduler and the announcements."""

from __future__ import annotations

import random
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from yokai.cogs.lottery import LotteryCog
from yokai.storage.db import Database
from yokai.ui.lottery_views import BuyView

CHANNEL = 10
WEEK = 7 * 24 * 3600
NOW = 1_000_000


async def _db(tmp_path: Path, *, with_channel: bool = True) -> Database:
    db = Database(tmp_path / "cog.db")
    await db.initialize()
    for user_id in (1, 2):
        await db.ensure_wallet(user_id, 1000)
    if with_channel:
        await db.set_multiplayer_channel(CHANNEL, "lottery", 1)
    return db


def make_bot(db: Database) -> MagicMock:
    bot = MagicMock()
    bot.user = None
    bot.db = db
    bot.config.economy_welcome_bonus = 0
    bot.config.lottery_ticket_price = 100
    bot.config.lottery_number_range = 20
    bot.config.lottery_max_tickets_per_user = 10
    bot.config.lottery_draw_interval_hours = 168
    bot.add_view = MagicMock()
    return bot


def make_channel() -> MagicMock:
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = CHANNEL
    sent = MagicMock(spec=discord.Message)
    sent.id = 5000
    channel.send = AsyncMock(return_value=sent)
    partial = MagicMock()
    partial.edit = AsyncMock()
    channel.get_partial_message = MagicMock(return_value=partial)
    return channel


@pytest.mark.asyncio
async def test_loading_the_cog_registers_the_buy_view_and_starts_the_scheduler(
    tmp_path: Path,
) -> None:
    db = await _db(tmp_path)
    try:
        bot = make_bot(db)
        cog = LotteryCog(bot)
        await cog.cog_load()
        try:
            assert isinstance(bot.add_view.call_args.args[0], BuyView)
            assert cog._task is not None and not cog._task.done()
        finally:
            await cog.cog_unload()
        assert cog._task is None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_posting_the_panel_sends_the_embed_and_starts_the_clock(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        cog = LotteryCog(make_bot(db))
        channel = make_channel()
        message = await cog.post_panel(channel)
        assert message.id == 5000
        kwargs = channel.send.await_args.kwargs
        assert isinstance(kwargs["view"], BuyView) and "Lottery" in kwargs["embed"].title
        assert kwargs["allowed_mentions"].everyone is False
        assert (await db.get_lottery_state()).next_draw_ts is not None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_the_panel_reflects_the_pot_and_tickets(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        await db.ensure_lottery_schedule(NOW, WEEK)
        await db.buy_lottery_tickets(
            1, [3, 7], price=100, max_per_user=10, welcome_bonus=0, now=NOW + 5
        )
        embed = await LotteryCog(make_bot(db)).panel_embed()
        text = " ".join(f.value for f in embed.fields)
        assert "200" in text and "2" in text
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_updating_the_panel_edits_the_stored_message(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        bot = make_bot(db)
        channel = make_channel()
        bot.get_channel = MagicMock(return_value=channel)
        await LotteryCog(bot).update_panel()
        channel.get_partial_message.assert_called_once_with(1)
        channel.get_partial_message.return_value.edit.assert_awaited_once()
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_updating_with_no_lottery_channel_is_a_quiet_no_op(tmp_path: Path) -> None:
    db = await _db(tmp_path, with_channel=False)
    try:
        bot = make_bot(db)
        bot.get_channel = MagicMock(return_value=None)
        await LotteryCog(bot).update_panel()  # must not raise
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_tick_with_no_lottery_channel_draws_nothing(tmp_path: Path) -> None:
    db = await _db(tmp_path, with_channel=False)
    try:
        await db.ensure_lottery_schedule(NOW, WEEK)
        cog = LotteryCog(make_bot(db))
        await cog.tick(now=NOW + WEEK + 5)
        assert (await db.get_lottery_state()).draw_no == 1
        assert await db.get_last_draw() is None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_tick_starts_the_clock_when_the_lottery_has_a_channel_but_no_schedule(
    tmp_path: Path,
) -> None:
    db = await _db(tmp_path)
    try:
        bot = make_bot(db)
        bot.get_channel = MagicMock(return_value=make_channel())
        await LotteryCog(bot).tick(now=NOW)
        assert (await db.get_lottery_state()).next_draw_ts == NOW + WEEK
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_due_tick_draws_pays_and_announces_in_the_channel(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        await db.ensure_lottery_schedule(NOW, WEEK)
        await db.buy_lottery_tickets(
            1, [7], price=100, max_per_user=10, welcome_bonus=0, now=NOW + 5
        )
        bot = make_bot(db)
        channel = make_channel()
        bot.get_channel = MagicMock(return_value=channel)
        cog = LotteryCog(bot)
        with patch("yokai.cogs.lottery.draw_number", return_value=7):
            await cog.tick(now=NOW + WEEK)
        wallet = await db.get_wallet(1)
        assert wallet is not None and wallet.balance == 900 + 100
        announcement = channel.send.await_args.kwargs["embed"]
        assert "<@1>" in announcement.description and "draw #1" in announcement.title
        assert channel.send.await_args.kwargs["allowed_mentions"].everyone is False
        assert await db.get_unannounced_draws() == []
        assert (await db.get_lottery_state()).draw_no == 2
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_tick_before_the_draw_time_does_nothing(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        await db.ensure_lottery_schedule(NOW, WEEK)
        bot = make_bot(db)
        channel = make_channel()
        bot.get_channel = MagicMock(return_value=channel)
        await LotteryCog(bot).tick(now=NOW + WEEK - 1)
        channel.send.assert_not_awaited()
        assert (await db.get_lottery_state()).draw_no == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_failed_announcement_is_retried_on_the_next_tick_without_a_second_draw(
    tmp_path: Path,
) -> None:
    db = await _db(tmp_path)
    try:
        await db.ensure_lottery_schedule(NOW, WEEK)
        await db.buy_lottery_tickets(
            1, [7], price=100, max_per_user=10, welcome_bonus=0, now=NOW + 5
        )
        bot = make_bot(db)
        channel = make_channel()
        response = MagicMock(status=500, reason="Server Error")
        channel.send = AsyncMock(
            side_effect=[discord.HTTPException(response, "boom"), MagicMock(id=6000)]
        )
        bot.get_channel = MagicMock(return_value=channel)
        cog = LotteryCog(bot)
        with patch("yokai.cogs.lottery.draw_number", return_value=7):
            await cog.tick(now=NOW + WEEK)  # the draw is made, the announcement fails
            assert [d.draw_no for d in await db.get_unannounced_draws()] == [1]
            await cog.tick(now=NOW + WEEK + 60)  # retried; no second draw
        assert await db.get_unannounced_draws() == []
        assert channel.send.await_count == 2
        assert (await db.get_lottery_state()).draw_no == 2
        wallet = await db.get_wallet(1)
        assert wallet is not None and wallet.balance == 1000  # paid exactly once
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_bot_that_was_offline_draws_once_on_the_first_tick(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        await db.ensure_lottery_schedule(NOW, WEEK)
        bot = make_bot(db)
        bot.get_channel = MagicMock(return_value=make_channel())
        cog = LotteryCog(bot)
        await cog.tick(now=NOW + 3 * WEEK + 5)
        await cog.tick(now=NOW + 3 * WEEK + 65)
        assert (await db.get_lottery_state()).draw_no == 2  # one draw, not three
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_the_scheduler_survives_a_failing_tick(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        cog = LotteryCog(make_bot(db))
        calls = {"n": 0}

        async def flaky(now=None):  # type: ignore[no-untyped-def]
            calls["n"] += 1
            if calls["n"] == 1:
                raise discord.HTTPException(MagicMock(status=500, reason="x"), "boom")

        with patch.object(cog, "tick", new=flaky), patch("yokai.cogs.lottery.CHECK_SECONDS", 0.01):
            await cog.cog_load()
            import asyncio

            await asyncio.sleep(0.15)
            await cog.cog_unload()
        assert calls["n"] >= 2  # it kept going after the first failure
    finally:
        await db.close()
```
Update existing tests: in `tests/test_lobby.py` change the availability test to expect all five games available and `GAME_SPECS["lottery"].is_table is False` (all others `True`); in `tests/test_table_manager.py` change `test_a_game_that_is_not_built_yet_cannot_open_a_table_and_costs_nothing` to assert that opening a `"lottery"` table raises `InvalidGameChoiceError` matching `"isn't a table game"` with nothing charged (keep the name or rename it to `test_the_lottery_cannot_be_opened_as_a_table`); in `tests/test_tables_cog.py` replace `test_a_game_that_is_not_built_yet_cannot_be_dedicated` with:
```python
@pytest.mark.asyncio
async def test_the_lottery_channel_gets_the_lottery_panel_not_a_play_embed(tmp_path: Path) -> None:
    async with world(tmp_path, users=()) as (db, manager, _):
        bot = make_bot(db, manager)
        channel = make_channel(20)
        panel_message = MagicMock(spec=discord.Message)
        panel_message.id = 7777
        lottery = MagicMock()
        lottery.post_panel = AsyncMock(return_value=panel_message)
        bot.get_cog = MagicMock(return_value=lottery)
        row = await TablesCog(bot).set_channel(channel, "lottery")
        lottery.post_panel.assert_awaited_once_with(channel)
        channel.send.assert_not_awaited()  # no Play embed
        assert row.game == "lottery" and row.play_message_id == 7777
        assert (await db.get_multiplayer_channel(20)) is not None
```
and a test that an unknown or unavailable game still raises (use `patch.dict` to mark one spec unavailable). In `tests/test_bot_wiring.py` add:
```python
@pytest.mark.asyncio
async def test_setup_hook_loads_the_lottery_extension() -> None:
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
    assert "yokai.cogs.lottery" in loaded
```
Add `"cogs/lottery.py"` and `"ui/lottery_views.py"` to `CHECKED` in `tests/test_one_emoji_rule.py`.

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_lottery_cog.py tests/test_lobby.py tests/test_table_manager.py tests/test_tables_cog.py tests/test_bot_wiring.py tests/test_one_emoji_rule.py -q`
Expected: collection ERROR for `yokai.cogs.lottery`; failures in the updated tests.

- [ ] **Step 3: Implement the cog**

Create `src/yokai/cogs/lottery.py`:
```python
"""The lottery channel: the always-on panel, the weekly draw and its announcement."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import TYPE_CHECKING, Optional

import aiosqlite
import discord
from discord.ext import commands

from yokai.gambling.engines.lottery import draw_number
from yokai.gambling.models import LotteryDraw, MultiplayerChannel
from yokai.gambling.rng import new_rng
from yokai.ui.embeds import EmbedFactory
from yokai.ui.lottery_views import BuyView
from yokai.ui.table_common import NO_MENTIONS, avatar_url

if TYPE_CHECKING:
    from yokai.bot import YokaiBot

logger = logging.getLogger(__name__)

CHECK_SECONDS = 60


class LotteryCog(commands.Cog, name="Lottery"):
    """Owns the lottery channel's panel and runs the weekly draw."""

    def __init__(self, bot: YokaiBot) -> None:
        self.bot = bot
        self._task: Optional[asyncio.Task[None]] = None

    async def cog_load(self) -> None:
        self.bot.add_view(BuyView(self.bot))
        self._task = asyncio.create_task(self._loop())

    async def cog_unload(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    # ---- the panel -----------------------------------------------------------------------

    async def _lottery_row(self) -> Optional[MultiplayerChannel]:
        for row in await self.bot.db.list_multiplayer_channels():
            if row.game == "lottery":
                return row
        return None

    async def panel_embed(self) -> discord.Embed:
        config = self.bot.config
        state = await self.bot.db.get_lottery_state()
        sold = await self.bot.db.lottery_tickets_sold()
        last = await self.bot.db.get_last_draw()
        return EmbedFactory.lottery_panel(
            pot=state.pot,
            tickets_sold=sold,
            number_range=config.lottery_number_range,
            price=config.lottery_ticket_price,
            max_tickets=config.lottery_max_tickets_per_user,
            next_draw_ts=state.next_draw_ts,
            last_draw=(last.winning_number, last.winners, last.prize_each) if last else None,
            bot_avatar_url=avatar_url(self.bot),
        )

    async def post_panel(self, channel: discord.TextChannel) -> discord.Message:
        """Post the panel and make sure the draw clock is running."""
        interval_s = self.bot.config.lottery_draw_interval_hours * 3600
        await self.bot.db.ensure_lottery_schedule(int(time.time()), interval_s)
        return await channel.send(
            embed=await self.panel_embed(), view=BuyView(self.bot), allowed_mentions=NO_MENTIONS
        )

    async def update_panel(self) -> None:
        """Refresh the panel in place; a missing channel or message is not an error."""
        row = await self._lottery_row()
        if row is None or row.play_message_id is None:
            return
        channel = self.bot.get_channel(row.channel_id)
        if channel is None:
            return
        await channel.get_partial_message(row.play_message_id).edit(  # type: ignore[union-attr]
            embed=await self.panel_embed()
        )

    # ---- the draw ------------------------------------------------------------------------

    async def announce(self, draw: LotteryDraw) -> None:
        """Announce a draw in the channel (a new message, never deleted) and mark it done."""
        row = await self._lottery_row()
        if row is None:
            return
        channel = self.bot.get_channel(row.channel_id)
        if channel is None:
            return
        state = await self.bot.db.get_lottery_state()
        embed = EmbedFactory.lottery_announcement(
            draw_no=draw.draw_no,
            winning_number=draw.winning_number,
            pot=draw.pot,
            winners=draw.winners,
            prize_each=draw.prize_each,
            next_pot=state.pot,
            next_draw_ts=state.next_draw_ts,
            bot_avatar_url=avatar_url(self.bot),
        )
        await channel.send(embed=embed, allowed_mentions=NO_MENTIONS)  # type: ignore[union-attr]
        await self.bot.db.mark_draw_announced(draw.draw_no)
        try:
            await self.update_panel()
        except discord.HTTPException as exc:
            logger.warning("Could not update the lottery panel after the draw: %s", exc)

    async def tick(self, now: Optional[int] = None) -> None:
        """One scheduler pass: start the clock, retry announcements, draw if due."""
        row = await self._lottery_row()
        if row is None:
            return
        ts = int(time.time()) if now is None else now
        interval_s = self.bot.config.lottery_draw_interval_hours * 3600
        await self.bot.db.ensure_lottery_schedule(ts, interval_s)
        for pending in await self.bot.db.get_unannounced_draws():
            await self.announce(pending)
        draw = await self.bot.db.run_lottery_draw(
            draw_number(self.bot.config.lottery_number_range, new_rng()), interval_s, ts
        )
        if draw is not None:
            await self.announce(draw)

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except (discord.HTTPException, aiosqlite.Error) as exc:
                logger.error("The lottery check failed: %s", exc, exc_info=exc)
            await asyncio.sleep(CHECK_SECONDS)


async def setup(bot: YokaiBot) -> None:
    """Extension setup entrypoint."""
    await bot.add_cog(LotteryCog(bot))
```

- [ ] **Step 4: Implement the wiring**

`src/yokai/gambling/lobby.py`: add `is_table: bool = True  # False for games with no table, like the lottery` to `GameSpec` (after `available`), replace the comment above the lottery entry with `# Not a table: it has its own channel panel and a weekly draw.`, and give the lottery entry `is_table=False` and remove its `available=False,` line.

`src/yokai/gambling/tables.py` `open_table`: right after `spec = self.spec(game)` add
```python
        if not spec.is_table:
            raise InvalidGameChoiceError(f"{spec.label} isn't a table game.")
```
and keep the existing `available` check after it.

`src/yokai/cogs/tables.py`: in `_post_play_message` add at the top of the method body
```python
        if game == "lottery":
            lottery = self.bot.get_cog("Lottery")
            if lottery is None:
                raise InvalidGameChoiceError("The lottery is not loaded.")
            return await lottery.post_panel(channel)  # type: ignore[attr-defined]
```
and change the unavailable-game hint in `set_channel` to `"That game is not available yet."`.

`src/yokai/bot.py`: after `await self.load_extension("yokai.cogs.tables")` add `await self.load_extension("yokai.cogs.lottery")`.

- [ ] **Step 5: Run to verify they pass**

Run: `python -m pytest -q`
Expected: all pass. Typical fixes: `test_loading_the_cog_hooks_table_endings...` is untouched; `make_bot` in `tests/test_tables_cog.py` needs `bot.get_cog` set by the new test only.

- [ ] **Step 6: Lint and commit**

```powershell
python -m ruff check .; python -m ruff format .; python -m ruff format --check .
git add src tests
git commit -m "feat(lottery): add the lottery cog, scheduler and channel dedication" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>" -m "Claude-Session: https://claude.ai/code/session_01BxUkTAJGP4uNByTtyvj8n9"
```

---

### Task 9: Documentation and phase record

**Files:**
- Modify: `README.md`, `TASKS.md`

- [ ] **Step 1: README**

Edit `README.md` (CRLF; edit bytes-safely): change the `/admin set_multiplayer` bullet so every game, including the Lottery, is playable; and extend the "Multiplayer tables" bullet or add a "**Lottery**" bullet: "a weekly draw in its own channel: buy numbered tickets with a button (hand-picked or quick picks), one number is drawn, holders split the pot, and an unclaimed pot rolls over. Everything the house wins in any game feeds the pot."

- [ ] **Step 2: TASKS.md**

Replace the line `(M4 lottery: planned after M3 is reviewed.)` with:
```markdown
### M4 — The lottery

Plan: `docs/superpowers/plans/2026-10-08-multiplayer-m4-lottery.md`.

- [x] M4.1 Lottery settings and errors.
- [x] M4.2 Lottery engine.
- [x] M4.3 Migration v7, models, schedule.
- [x] M4.4 Buying tickets (one transaction).
- [x] M4.5 The weekly draw (one transaction).
- [x] M4.6 Lottery embeds.
- [x] M4.7 Buy tickets button and form.
- [x] M4.8 Lottery cog, scheduler, channel dedication.

**Acceptance criteria (M4)**
- `/admin set_multiplayer game:Lottery` posts a persistent panel (pot, tickets sold, next draw)
  with a **Buy tickets** button that keeps working after a restart; the draw clock starts then.
- The form takes hand-picked numbers and/or a quick-pick count; numbers run 1 to 20, a member
  holds at most 10 per draw and never the same number twice; a purchase debits, stores the
  tickets and feeds the pot in one transaction; sales close at the draw time.
- Once a week a number is drawn: holders split the pot (remainder stays), nobody holding it
  rolls the pot over; the result is announced in the channel and never deleted; the panel
  updates.
- A bot that was offline at draw time draws once on the next check; an unannounced draw is
  announced later and nobody is paid twice.
- The lottery cannot be opened as a table.
- `pytest`, `ruff check` and `ruff format --check` are clean.
- *(manual, real Discord)* Dedicate a channel; buy tickets with numbers, with quick picks, and
  with both; try the cap, a repeat number, junk input and too little Renown; lower
  `LOTTERY_DRAW_INTERVAL_HOURS` or edit `next_draw_ts` in the database to force a draw; check the
  announcement, the payout in `/balance`, the rollover when nobody holds the number, and that
  restarting the bot keeps the Buy button working.

**STOP — owner review.** This completes Phase 10.
```
Also tick the Phase 10 summary if one exists.

- [ ] **Step 3: Final verification and commit**

```powershell
python -m pytest -q; python -m ruff check .; python -m ruff format --check .
git add README.md TASKS.md
git commit -m "docs(lottery): document the lottery and record phase M4" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>" -m "Claude-Session: https://claude.ai/code/session_01BxUkTAJGP4uNByTtyvj8n9"
```

**STOP. Report what changed, list the unverified manual Discord checks, and wait for owner review.**
