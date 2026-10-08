# Multiplayer Phase M3: Jackpot, Crash and Blackjack Table Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Jackpot, Crash and the Blackjack table playable in their dedicated channels, on the lobby, escrow, atomic-settlement and pot-accounting framework M2 built.

**Architecture:** Three pure engines (`engines/jackpot.py`, `engines/crash.py`, `engines/bjtable.py`) hold every rule and are tested with seeded randomness and a fake clock. `TableManager` gains `resolve_jackpot`; crash and the blackjack table settle through the existing `TableManager.settle`. Three thin Discord runners (`ui/jackpot_views.py`, `ui/crash_views.py`, `ui/bjtable_views.py`) plug into `GAME_RUNNERS` and share one failure guard (`table_common.finish_table`) that refunds everyone and tells the players if anything breaks.

**Tech Stack:** Python 3.12, discord.py 2.7.1, aiosqlite, pytest (`asyncio_mode = "auto"`), ruff (line length 100).

**Spec:** `docs/superpowers/specs/2026-10-07-multiplayer-tables-design.md` (§7 other table games, §6 pot accounting, §9 config, §10 errors, §11 testing). M2 plan: `docs/superpowers/plans/2026-10-07-multiplayer-m2-tables-and-duel.md`. The lottery is M4.

## Global Constraints

- Python 3.12+, `discord.py>=2.7.1`; slash commands guild-scoped; no privileged intents (`AGENTS.md`).
- Every user-visible message goes through `ui/embeds.py`; at most one emoji per embed, only from `theme.Emoji`; cards in text notation (`As Kh 10d`), never suit glyphs; errors plain and ephemeral.
- `allowed_mentions` none; members mentioned only inside embeds.
- Every view and modal blocks blacklisted users (`moderation.gate.screen`).
- Never block the event loop; no bare `except`; `logging` not `print`; `from __future__ import annotations`; full type hints.
- Stakes whole Renown within `GAMBLING_MIN_BET` to `GAMBLING_MAX_BET`. Jackpot rake `GAMBLING_JACKPOT_RAKE_PERCENT` default 2, rounded down. Crash cap `GAMBLING_CRASH_MAX_MULTIPLIER` default 100. Turn timer `GAMBLING_TURN_SECONDS` default 45. Join windows: jackpot 60 s (`GAMBLING_LOBBY_JOIN_SECONDS`), crash and blackjack table half of that (`GameSpec.window_factor` 0.5).
- Seats: jackpot 2 to 10, crash 1 to 10, blackjack table 2 to 5.
- Crash point: `max(1.0, 0.97 / U)` with `U` uniform in (0, 1], capped, drawn once up front and never recomputed. A cash-out is judged by the **server** clock at the moment it is handled; a press at or after the crash time loses.
- Blackjack table rules equal solo blackjack (6-deck shoe, 3:2 naturals, dealer stands on all 17s, no insurance, no splits) except no double. Players act in join order; 45 s per decision, auto-stand on timeout.
- Discord documents no per-route edit limit and says clients must read rate-limit headers (https://docs.discord.com/developers/topics/rate-limits); discord.py's HTTP layer does. Crash therefore ticks about once a second, never queues an edit behind a slow one, and widens its interval (up to 3 s) when edits are slow.
- No new dependency; no schema change in this phase.
- Work directly on `main`; checkpoint tag first; commit per task; finish with `pytest -q`, `ruff check .`, `ruff format --check .` clean, then stop for owner review.

## Review Focus

- Crash is server-authoritative: a press after the crash time loses even if the displayed multiplier was still climbing; a second cash-out is refused; the payout is the locked multiplier (hundredths) times the stake, rounded down; a player who never cashed out loses the stake (Tasks 3, 7).
- Crash with one player works; with everyone cashed out the round ends early; a crash point of 1.00 crashes before anyone can cash out (Tasks 3, 7).
- Jackpot weights by stake, a single entrant is cancelled and refunded by the framework, only the rake reaches the pot, and the winner is always one of the entrants (Task 2).
- Blackjack table: only the player whose turn it is can act; a timeout and a button press at the same moment never act twice; the dealer plays once and not at all when every player busted or has a natural; a dealer natural ends the round at the deal (Tasks 4, 8).
- Every runner failure (render, settlement, a missing runner) refunds all stakes, releases the claims and tells the players the table was cancelled (Tasks 6, 7, 8, 9).
- A slow Discord edit never delays or changes a crash outcome (Task 7).

---

## File Structure

| File | Responsibility |
|---|---|
| `src/yokai/config.py`, `.env.example` (modify) | jackpot rake, crash cap |
| `src/yokai/errors.py` (modify) | `NotYourTurnError` |
| `src/yokai/gambling/engines/jackpot.py` (create) | stake-weighted draw and payouts |
| `src/yokai/gambling/engines/crash.py` (create) | multiplier curve, crash point, `CrashRound`, tick interval |
| `src/yokai/gambling/engines/blackjack.py` (modify) | shared `compare_hands` |
| `src/yokai/gambling/engines/bjtable.py` (create) | `BlackjackTable` |
| `src/yokai/gambling/tables.py` (modify) | new settings, `resolve_jackpot`, `JackpotResult` |
| `src/yokai/gambling/lobby.py` (modify) | flip `available` for the three games |
| `src/yokai/ui/embeds.py` (modify) | jackpot, crash and blackjack table embeds |
| `src/yokai/ui/table_common.py` (modify) | `tell_cancelled`, `finish_table` |
| `src/yokai/ui/duel_views.py` (modify) | use the shared `tell_cancelled` |
| `src/yokai/ui/jackpot_views.py`, `crash_views.py`, `bjtable_views.py` (create) | runners |
| `src/yokai/ui/table_views.py` (modify) | register runners; guard a missing runner |
| `src/yokai/bot.py` (modify) | pass new settings |
| `scripts/preview_embeds.py`, `docs/preview/embeds.html` (modify) | samples |
| tests | `test_jackpot_engine.py`, `test_crash_engine.py`, `test_bjtable_engine.py`, `test_m3_embeds.py`, `test_jackpot_views.py`, `test_crash_views.py`, `test_bjtable_views.py`, plus edits to config, errors, lobby, manager, cog and wiring tests and `tests/table_helpers.py` |

---

### Task 0: Checkpoint tag

- [ ] **Step 1: Tag a clean main**

```powershell
git status --short; git branch --show-current
git tag -a checkpoint/pre-multiplayer-m3 -m "Before multiplayer M3 (jackpot, crash, blackjack table)"
git tag | Select-String multiplayer
```
Expected: no modified files, branch `main`, the m1/m2/m3 tags listed.

---

### Task 1: Configuration and `NotYourTurnError`

**Files:**
- Modify: `src/yokai/config.py`, `src/yokai/errors.py`, `.env.example`
- Test: `tests/test_gambling_config.py`, `tests/test_gambling_errors.py`

**Interfaces:**
- Produces: `Config.gambling_jackpot_rake_percent: int` (default 2, clamped 0 to 50), `Config.gambling_crash_max_multiplier: int` (default 100, floor 2); `NotYourTurnError()`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gambling_config.py`:
```python
def test_jackpot_and_crash_defaults() -> None:
    cfg = _load()
    assert cfg.gambling_jackpot_rake_percent == 2
    assert cfg.gambling_crash_max_multiplier == 100


def test_jackpot_and_crash_overrides() -> None:
    cfg = _load({"GAMBLING_JACKPOT_RAKE_PERCENT": "4", "GAMBLING_CRASH_MAX_MULTIPLIER": "50"})
    assert (cfg.gambling_jackpot_rake_percent, cfg.gambling_crash_max_multiplier) == (4, 50)


def test_jackpot_rake_is_clamped_between_zero_and_fifty() -> None:
    assert _load({"GAMBLING_JACKPOT_RAKE_PERCENT": "-1"}).gambling_jackpot_rake_percent == 0
    assert _load({"GAMBLING_JACKPOT_RAKE_PERCENT": "80"}).gambling_jackpot_rake_percent == 50


def test_crash_cap_has_a_floor_of_two() -> None:
    assert _load({"GAMBLING_CRASH_MAX_MULTIPLIER": "1"}).gambling_crash_max_multiplier == 2
    assert _load({"GAMBLING_CRASH_MAX_MULTIPLIER": "0"}).gambling_crash_max_multiplier == 2
```
Append to `tests/test_gambling_errors.py` (merge `NotYourTurnError` into its existing `yokai.errors` import):
```python
def test_not_your_turn_is_a_plain_domain_error() -> None:
    err = NotYourTurnError()
    assert isinstance(err, YokaiError) and err.title == "Not your turn"
    assert "turn" in err.message.lower() and err.user_hint
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_gambling_config.py tests/test_gambling_errors.py -q`
Expected: failures: unknown `Config` attributes and an `ImportError` for `NotYourTurnError`.

- [ ] **Step 3: Implement**

`src/yokai/config.py`: add to the `Config` dataclass after `gambling_turn_seconds`:
```python
    gambling_jackpot_rake_percent: int = 2
    gambling_crash_max_multiplier: int = 100
```
In `load_config`, after the `gambling_turn_seconds` parsing:
```python
    gambling_jackpot_rake_percent = min(
        50,
        max(
            0,
            _parse_int(
                "GAMBLING_JACKPOT_RAKE_PERCENT", os.getenv("GAMBLING_JACKPOT_RAKE_PERCENT"), 2
            ),
        ),
    )
    gambling_crash_max_multiplier = max(
        2,
        _parse_int(
            "GAMBLING_CRASH_MAX_MULTIPLIER", os.getenv("GAMBLING_CRASH_MAX_MULTIPLIER"), 100
        ),
    )
```
and pass `gambling_jackpot_rake_percent=gambling_jackpot_rake_percent, gambling_crash_max_multiplier=gambling_crash_max_multiplier,` in the `Config(...)` call after `gambling_turn_seconds=gambling_turn_seconds,`.

`src/yokai/errors.py` append:
```python
class NotYourTurnError(YokaiError):
    """Raised when a player acts at a turn-based table out of turn."""

    title = "Not your turn"

    def __init__(self) -> None:
        super().__init__(
            "It isn't your turn yet.",
            user_hint="Wait until the table is waiting on you.",
        )
```
`.env.example`: after the `GAMBLING_TURN_SECONDS=45` line append:
```
# Percent of a jackpot pot kept by the house (0 disables); it feeds the lottery pot
GAMBLING_JACKPOT_RAKE_PERCENT=2
# Highest multiplier a crash round can reach
GAMBLING_CRASH_MAX_MULTIPLIER=100
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_gambling_config.py tests/test_gambling_errors.py -q`
Expected: all pass.

- [ ] **Step 5: Suite, lint, commit**

```powershell
python -m pytest -q; python -m ruff check .; python -m ruff format .; python -m ruff format --check .
git add src/yokai/config.py src/yokai/errors.py .env.example tests/test_gambling_config.py tests/test_gambling_errors.py
git commit -m "feat(tables): add jackpot and crash settings and NotYourTurnError" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>" -m "Claude-Session: https://claude.ai/code/session_01BxUkTAJGP4uNByTtyvj8n9"
```

---

### Task 2: Jackpot engine and `resolve_jackpot`

**Files:**
- Create: `src/yokai/gambling/engines/jackpot.py`
- Modify: `src/yokai/gambling/tables.py` (constructor, `JackpotResult`, `resolve_jackpot`), `src/yokai/bot.py` (pass settings), `tests/table_helpers.py`
- Create: `tests/test_jackpot_engine.py`
- Modify: `tests/test_table_manager.py` (append)

**Interfaces:**
- Produces:
  - `jackpot.draw_winner(entries: Sequence[tuple[int, int]], rng) -> int`, `jackpot.win_chances(entries) -> dict[int, float]`, `jackpot.payouts(entries, winner_id, rake_percent) -> tuple[dict[int, int], int, int]` returning `(payouts, prize, rake)`.
  - `TableManager(..., jackpot_rake_percent: int = 2, crash_max_multiplier: int = 100)` (new keyword arguments after `pick_seconds`); attributes of the same names.
  - `JackpotResult(winner_id: int, prize: int, rake: int, chances: dict[int, float], balances: dict[int, int])`; `TableManager.resolve_jackpot(lobby) -> JackpotResult`.
  - Test helper `ALL_PLAYABLE: dict[str, GameSpec]` (every game marked available) and `world(..., jackpot_rake_percent=2)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_jackpot_engine.py`:
```python
"""The pure jackpot rules: a stake-weighted draw, the winner takes the pot minus the rake."""

from __future__ import annotations

import random

import pytest

from yokai.gambling.engines.jackpot import draw_winner, payouts, win_chances


def test_a_single_entrant_always_wins() -> None:
    assert draw_winner([(7, 100)], random.Random(1)) == 7


def test_the_draw_is_deterministic_for_a_seed() -> None:
    entries = [(1, 100), (2, 100), (3, 100)]
    assert draw_winner(entries, random.Random(5)) == draw_winner(entries, random.Random(5))


def test_a_stake_three_times_larger_wins_about_three_times_as_often() -> None:
    rng = random.Random(12345)
    entries = [(1, 100), (2, 300)]
    wins = sum(draw_winner(entries, rng) == 2 for _ in range(20_000))
    assert 0.72 < wins / 20_000 < 0.78


def test_every_entrant_can_win() -> None:
    rng = random.Random(9)
    entries = [(1, 50), (2, 50), (3, 50), (4, 50)]
    assert {draw_winner(entries, rng) for _ in range(500)} == {1, 2, 3, 4}


def test_the_roll_boundaries_pick_the_first_and_last_entrant() -> None:
    class Roll(random.Random):
        def __init__(self, value: int) -> None:
            super().__init__()
            self.value = value

        def randrange(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            return self.value

    entries = [(1, 100), (2, 200), (3, 300)]
    assert draw_winner(entries, Roll(0)) == 1
    assert draw_winner(entries, Roll(99)) == 1
    assert draw_winner(entries, Roll(100)) == 2
    assert draw_winner(entries, Roll(599)) == 3


@pytest.mark.parametrize("entries", [[], [(1, 0)], [(1, -5), (2, 10)]])
def test_an_empty_or_non_positive_draw_is_refused(entries: list[tuple[int, int]]) -> None:
    with pytest.raises(ValueError):
        draw_winner(entries, random.Random(1))


def test_win_chances_are_stake_shares() -> None:
    chances = win_chances([(1, 100), (2, 300)])
    assert chances == {1: 0.25, 2: 0.75}


def test_payouts_give_the_winner_the_pot_minus_the_rake() -> None:
    result, prize, rake = payouts([(1, 100), (2, 300)], winner_id=2, rake_percent=2)
    assert (prize, rake) == (392, 8)
    assert result == {1: 0, 2: 392}


def test_the_rake_rounds_down_and_never_exceeds_the_pot() -> None:
    result, prize, rake = payouts([(1, 10), (2, 15)], winner_id=1, rake_percent=2)
    assert (prize, rake) == (25, 0)
    assert sum(result.values()) + rake == 25


def test_a_zero_rake_pays_the_whole_pot() -> None:
    result, prize, rake = payouts([(1, 100), (2, 100)], winner_id=1, rake_percent=0)
    assert (prize, rake) == (200, 0) and result[1] == 200


def test_the_winner_must_be_one_of_the_entrants() -> None:
    with pytest.raises(ValueError):
        payouts([(1, 100), (2, 100)], winner_id=3, rake_percent=2)
```
In `tests/table_helpers.py` add near the top (after the imports) and extend `world`:
```python
import dataclasses

from yokai.gambling.lobby import GAME_SPECS

# Every game marked playable, for tests that exercise games whose phase has shipped.
ALL_PLAYABLE: dict[str, GameSpec] = {
    name: dataclasses.replace(spec, available=True) for name, spec in GAME_SPECS.items()
}
```
(merge the `dataclasses` and `GAME_SPECS` imports into the file's existing import block), add the parameter `jackpot_rake_percent: int = 2` to `world(...)` and pass `jackpot_rake_percent=jackpot_rake_percent,` to `TableManager(...)`.

Append to `tests/test_table_manager.py` (add `import random` and `from tests.table_helpers import ALL_PLAYABLE` to its imports):
```python
# ---- the jackpot ------------------------------------------------------------------------


class FixedRoll(random.Random):
    """A random source whose randrange always returns the same value."""

    def __init__(self, value: int) -> None:
        super().__init__()
        self.value = value

    def randrange(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return self.value


async def _running_jackpot(manager):  # type: ignore[no-untyped-def]
    lobby = await manager.open_table(CHANNEL, "jackpot", 1, 100, announce_ok)
    await manager.join(lobby, 2, 200)
    await manager.join(lobby, 3, 300)
    assert await manager.begin(lobby) is BeginOutcome.STARTED
    return lobby


@pytest.mark.asyncio
async def test_a_jackpot_pays_the_drawn_player_the_pot_minus_the_rake(tmp_path: Path) -> None:
    async with world(tmp_path, specs=ALL_PLAYABLE) as (db, manager, active):
        manager.rng_factory = lambda: FixedRoll(599)  # the last entrant, user 3
        lobby = await _running_jackpot(manager)
        result = await manager.resolve_jackpot(lobby)
        assert result.winner_id == 3 and (result.prize, result.rake) == (588, 12)
        assert result.chances == {1: 100 / 600, 2: 200 / 600, 3: 300 / 600}
        assert (await balance_of(db, 1), await balance_of(db, 2)) == (900, 800)
        assert await balance_of(db, 3) == 1000 - 300 + 588
        assert await db.get_pot() == 12 and lobby.state is LobbyState.SETTLED
        assert not active.is_active(1) and await db.refund_open_bets() == 0


@pytest.mark.asyncio
async def test_a_jackpot_with_one_entrant_is_cancelled_and_refunded(tmp_path: Path) -> None:
    async with world(tmp_path, specs=ALL_PLAYABLE) as (db, manager, active):
        lobby = await manager.open_table(CHANNEL, "jackpot", 1, 100, announce_ok)
        assert await manager.begin(lobby) is BeginOutcome.CANCELLED
        assert await balance_of(db, 1) == 1000 and not active.is_active(1)


@pytest.mark.asyncio
async def test_each_jackpot_guest_picks_their_own_stake(tmp_path: Path) -> None:
    async with world(tmp_path, specs=ALL_PLAYABLE) as (db, manager, _):
        lobby = await manager.open_table(CHANNEL, "jackpot", 1, 100, announce_ok)
        seat = await manager.join(lobby, 2, 450)
        assert seat.stake == 450 and lobby.pot == 550
        with pytest.raises(BetOutOfRangeError):
            await manager.join(lobby, 3, 9999)


@pytest.mark.asyncio
async def test_a_zero_jackpot_rake_pays_the_whole_pot(tmp_path: Path) -> None:
    async with world(tmp_path, specs=ALL_PLAYABLE, jackpot_rake_percent=0) as (db, manager, _):
        manager.rng_factory = lambda: FixedRoll(0)
        lobby = await _running_jackpot(manager)
        result = await manager.resolve_jackpot(lobby)
        assert (result.winner_id, result.prize, result.rake) == (1, 600, 0)
        assert await db.get_pot() == 0
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_jackpot_engine.py tests/test_table_manager.py -q`
Expected: collection ERROR for `yokai.gambling.engines.jackpot`; the manager tests fail on `resolve_jackpot` and the unknown `jackpot_rake_percent` argument.

- [ ] **Step 3: Implement the engine**

Create `src/yokai/gambling/engines/jackpot.py`:
```python
"""Jackpot rules: everyone antes into one pot and one stake-weighted draw takes it."""

from __future__ import annotations

import random
from typing import Sequence

from yokai.gambling.engines.duel import prize_after_rake


def _total(entries: Sequence[tuple[int, int]]) -> int:
    if not entries:
        raise ValueError("a jackpot needs at least one entry")
    if any(stake <= 0 for _, stake in entries):
        raise ValueError("every stake must be positive")
    return sum(stake for _, stake in entries)


def draw_winner(entries: Sequence[tuple[int, int]], rng: random.Random) -> int:
    """Pick a winner with probability proportional to stake; entries are (user_id, stake)."""
    total = _total(entries)
    roll = rng.randrange(total)
    running = 0
    for user_id, stake in entries:
        running += stake
        if roll < running:
            return user_id
    raise AssertionError("unreachable: the roll is below the total")


def win_chances(entries: Sequence[tuple[int, int]]) -> dict[int, float]:
    """Each entrant's chance of winning, as a share between 0 and 1."""
    total = _total(entries)
    return {user_id: stake / total for user_id, stake in entries}


def payouts(
    entries: Sequence[tuple[int, int]], winner_id: int, rake_percent: int
) -> tuple[dict[int, int], int, int]:
    """Split the pot: the winner gets it minus the rake. Returns (payouts, prize, rake)."""
    if winner_id not in {user_id for user_id, _ in entries}:
        raise ValueError("the winner must be one of the entrants")
    prize, rake = prize_after_rake(_total(entries), rake_percent)
    return {user_id: (prize if user_id == winner_id else 0) for user_id, _ in entries}, prize, rake
```

- [ ] **Step 4: Implement the manager changes**

In `src/yokai/gambling/tables.py`: add `from yokai.gambling.engines import jackpot` (next to the duel import), add after `DuelResult`:
```python
@dataclass(frozen=True)
class JackpotResult:
    winner_id: int
    prize: int
    rake: int
    chances: dict[int, float]  # user id -> share of the pot, 0 to 1
    balances: dict[int, int]  # user id -> balance after the draw
```
add the two keyword arguments after `pick_seconds: int,` in `TableManager.__init__`:
```python
        jackpot_rake_percent: int = 2,
        crash_max_multiplier: int = 100,
```
and store them (`self.jackpot_rake_percent = jackpot_rake_percent`, `self.crash_max_multiplier = crash_max_multiplier`) next to `self.rake_percent`. Add this method after `resolve_duel`:
```python
    async def resolve_jackpot(self, lobby: Lobby) -> JackpotResult:
        """Draw the jackpot winner by stake and pay out in one transaction."""
        entries = [(user_id, seat.stake) for user_id, seat in lobby.seats.items()]
        winner_id = jackpot.draw_winner(entries, self.rng_factory())
        payouts, prize, rake = jackpot.payouts(entries, winner_id, self.jackpot_rake_percent)
        balances = await self.settle(lobby, payouts)
        return JackpotResult(
            winner_id=winner_id,
            prize=prize,
            rake=rake,
            chances=jackpot.win_chances(entries),
            balances=balances,
        )
```
In `src/yokai/bot.py`, in the `TableManager(...)` call add `jackpot_rake_percent=config.gambling_jackpot_rake_percent, crash_max_multiplier=config.gambling_crash_max_multiplier,` after `pick_seconds=config.gambling_turn_seconds,`.

- [ ] **Step 5: Run to verify they pass**

Run: `python -m pytest tests/test_jackpot_engine.py tests/test_table_manager.py -q`
Expected: all pass.

- [ ] **Step 6: Suite, lint, commit**

```powershell
python -m pytest -q; python -m ruff check .; python -m ruff format .; python -m ruff format --check .
git add src tests
git commit -m "feat(tables): add the jackpot engine and resolve_jackpot" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>" -m "Claude-Session: https://claude.ai/code/session_01BxUkTAJGP4uNByTtyvj8n9"
```

---

### Task 3: Crash engine

**Files:**
- Create: `src/yokai/gambling/engines/crash.py`
- Create: `tests/test_crash_engine.py`

**Interfaces:**
- Produces: constants `GROWTH_PER_SECOND = 0.07`, `HOUSE_FACTOR = 0.97`, `MIN_TICK_S = 1.0`, `MAX_TICK_S = 3.0`; `multiplier_at(elapsed_s) -> float`; `time_to_reach(multiplier) -> float`; `draw_crash_point(rng, cap) -> int` (hundredths: 100 means 1.00x); `next_tick_interval(last_edit_s) -> float`; `CrashRound(user_ids, crash_point)` with `crash_time_s`, `has_crashed(elapsed)`, `multiplier_hundredths_at(elapsed) -> int`, `cash_out(user_id, elapsed) -> int` (hundredths), `cashed_out: dict[int, int]`, `all_cashed_out`, `is_over(elapsed)`, `payouts(stakes: Mapping[int, int]) -> dict[int, int]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_crash_engine.py`:
```python
"""The pure crash rules: a drawn crash point, a rising multiplier, server-judged cash-outs."""

from __future__ import annotations

import math
import random

import pytest

from yokai.errors import InvalidGameChoiceError, LobbyClosedError, NotAtTableError
from yokai.gambling.engines.crash import (
    GROWTH_PER_SECOND,
    MAX_TICK_S,
    MIN_TICK_S,
    CrashRound,
    draw_crash_point,
    multiplier_at,
    next_tick_interval,
    time_to_reach,
)


class FixedRandom(random.Random):
    """A random source whose random() always returns the same value."""

    def __init__(self, value: float) -> None:
        super().__init__()
        self.value = value

    def random(self) -> float:
        return self.value


def test_the_multiplier_starts_at_one_and_only_rises() -> None:
    assert multiplier_at(0) == 1.0 and multiplier_at(-5) == 1.0
    assert multiplier_at(1) < multiplier_at(2) < multiplier_at(30)
    assert multiplier_at(10) == pytest.approx(math.exp(GROWTH_PER_SECOND * 10))


def test_time_to_reach_inverts_the_multiplier() -> None:
    assert time_to_reach(multiplier_at(12.5)) == pytest.approx(12.5)
    assert time_to_reach(1.0) == 0.0


def test_the_crash_point_follows_the_house_edge_formula() -> None:
    assert draw_crash_point(FixedRandom(0.0), cap=100) == 100  # u = 1 -> 0.97 -> floor of 1.00
    assert draw_crash_point(FixedRandom(0.5), cap=100) == 194  # u = 0.5 -> 1.94
    assert draw_crash_point(FixedRandom(0.99), cap=100) == 9700  # u = 0.01 -> 97.00


def test_the_crash_point_is_capped() -> None:
    assert draw_crash_point(FixedRandom(0.9999999), cap=100) == 10_000
    assert draw_crash_point(FixedRandom(0.9999999), cap=5) == 500


def test_about_three_percent_of_rounds_crash_at_one_and_the_tail_matches() -> None:
    rng = random.Random(2024)
    points = [draw_crash_point(rng, cap=100) for _ in range(30_000)]
    assert min(points) == 100
    assert 0.02 < sum(p == 100 for p in points) / 30_000 < 0.04
    assert 0.46 < sum(p >= 200 for p in points) / 30_000 < 0.51  # about 0.97 / 2


def test_the_tick_interval_widens_when_edits_are_slow_and_never_leaves_its_bounds() -> None:
    assert next_tick_interval(0.05) == MIN_TICK_S
    assert next_tick_interval(0.8) == pytest.approx(1.6)
    assert next_tick_interval(30.0) == MAX_TICK_S


def test_a_cash_out_locks_the_multiplier_at_the_moment_of_the_press() -> None:
    crash = CrashRound([1, 2], crash_point=500)  # crashes at 5.00x, about 23 s in
    assert crash.cash_out(1, elapsed=5.0) == math.floor(multiplier_at(5.0) * 100)
    assert crash.cashed_out == {1: 141}


def test_a_press_at_or_after_the_crash_time_loses() -> None:
    crash = CrashRound([1], crash_point=250)
    assert not crash.has_crashed(crash.crash_time_s - 0.001)
    with pytest.raises(LobbyClosedError):
        crash.cash_out(1, elapsed=crash.crash_time_s)
    with pytest.raises(LobbyClosedError):
        crash.cash_out(1, elapsed=crash.crash_time_s + 5)
    assert crash.cashed_out == {}


def test_a_crash_point_of_one_crashes_before_anyone_can_cash_out() -> None:
    crash = CrashRound([1, 2], crash_point=100)
    assert crash.crash_time_s == 0.0 and crash.has_crashed(0.0) and crash.is_over(0.0)
    with pytest.raises(LobbyClosedError):
        crash.cash_out(1, elapsed=0.0)


def test_a_cash_out_never_exceeds_the_crash_point() -> None:
    crash = CrashRound([1], crash_point=250)
    just_before = crash.crash_time_s - 1e-9
    assert crash.cash_out(1, elapsed=just_before) <= 250


def test_cashing_out_twice_is_refused() -> None:
    crash = CrashRound([1], crash_point=500)
    crash.cash_out(1, elapsed=3.0)
    with pytest.raises(InvalidGameChoiceError, match="already"):
        crash.cash_out(1, elapsed=4.0)


def test_only_players_can_cash_out() -> None:
    crash = CrashRound([1], crash_point=500)
    with pytest.raises(NotAtTableError):
        crash.cash_out(9, elapsed=1.0)


def test_payouts_use_the_locked_multiplier_and_round_down() -> None:
    crash = CrashRound([1, 2, 3], crash_point=1000)
    crash.cash_out(1, elapsed=5.0)  # 1.41x
    crash.cash_out(2, elapsed=10.0)  # 2.01x
    result = crash.payouts({1: 100, 2: 55, 3: 500})
    assert result == {1: 141, 2: 55 * 201 // 100, 3: 0}


def test_the_round_ends_early_when_everyone_has_cashed_out() -> None:
    crash = CrashRound([1, 2], crash_point=1000)
    assert not crash.is_over(1.0)
    crash.cash_out(1, elapsed=1.0)
    assert not crash.all_cashed_out and not crash.is_over(2.0)
    crash.cash_out(2, elapsed=2.0)
    assert crash.all_cashed_out and crash.is_over(2.0)


def test_the_round_ends_at_the_crash() -> None:
    crash = CrashRound([1], crash_point=300)
    assert not crash.is_over(crash.crash_time_s - 0.01)
    assert crash.is_over(crash.crash_time_s)


def test_the_displayed_multiplier_never_passes_the_crash_point() -> None:
    crash = CrashRound([1], crash_point=250)
    assert crash.multiplier_hundredths_at(0.0) == 100
    assert crash.multiplier_hundredths_at(1_000.0) == 250


def test_a_round_needs_at_least_one_player() -> None:
    with pytest.raises(ValueError):
        CrashRound([], crash_point=200)
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_crash_engine.py -q`
Expected: collection ERROR `No module named 'yokai.gambling.engines.crash'`.

- [ ] **Step 3: Implement**

Create `src/yokai/gambling/engines/crash.py`:
```python
"""Crash rules: a multiplier rises until a crash point drawn up front; cash out before it.

Multipliers are whole hundredths (250 means 2.50x) so payouts are exact integers.
"""

from __future__ import annotations

import math
import random
from typing import Mapping, Sequence

from yokai.errors import InvalidGameChoiceError, LobbyClosedError, NotAtTableError

GROWTH_PER_SECOND = 0.07  # the multiplier is exp(0.07 * seconds): 2x after ~10 s, 100x after ~66 s
HOUSE_FACTOR = 0.97  # a 3% edge: P(crash point >= m) = 0.97 / m
MIN_TICK_S = 1.0
MAX_TICK_S = 3.0


def multiplier_at(elapsed_s: float) -> float:
    """The multiplier `elapsed_s` seconds into a round."""
    return math.exp(GROWTH_PER_SECOND * max(0.0, elapsed_s))


def time_to_reach(multiplier: float) -> float:
    """Seconds into a round at which the multiplier reaches `multiplier` (at least 1.0)."""
    return math.log(max(1.0, multiplier)) / GROWTH_PER_SECOND


def draw_crash_point(rng: random.Random, cap: int) -> int:
    """The crash point in hundredths, drawn once: max(1.00, 0.97 / U) with U in (0, 1]."""
    u = 1.0 - rng.random()
    raw = HOUSE_FACTOR / u
    return min(cap * 100, max(100, math.floor(raw * 100)))


def next_tick_interval(last_edit_s: float) -> float:
    """Seconds to wait before the next message edit: slower when the last edit was slow."""
    return min(MAX_TICK_S, max(MIN_TICK_S, 2.0 * last_edit_s))


class CrashRound:
    """One round: who is in, who cashed out at what multiplier, and when it crashes."""

    def __init__(self, user_ids: Sequence[int], crash_point: int) -> None:
        if not user_ids:
            raise ValueError("a crash round needs at least one player")
        self.user_ids = list(user_ids)
        self.crash_point = crash_point
        self.crash_time_s = time_to_reach(crash_point / 100)
        self.cashed_out: dict[int, int] = {}

    def has_crashed(self, elapsed_s: float) -> bool:
        return elapsed_s >= self.crash_time_s

    @property
    def all_cashed_out(self) -> bool:
        return len(self.cashed_out) == len(self.user_ids)

    def is_over(self, elapsed_s: float) -> bool:
        return self.has_crashed(elapsed_s) or self.all_cashed_out

    def multiplier_hundredths_at(self, elapsed_s: float) -> int:
        """The multiplier to show, never above the crash point."""
        return min(self.crash_point, math.floor(multiplier_at(elapsed_s) * 100))

    def cash_out(self, user_id: int, elapsed_s: float) -> int:
        """Lock in the multiplier (hundredths) at the server's `elapsed_s`, or raise."""
        if user_id not in self.user_ids:
            raise NotAtTableError()
        if user_id in self.cashed_out:
            raise InvalidGameChoiceError("You already cashed out.")
        if self.has_crashed(elapsed_s):
            raise LobbyClosedError("It already crashed.", user_hint=None)
        locked = self.multiplier_hundredths_at(elapsed_s)
        self.cashed_out[user_id] = locked
        return locked

    def payouts(self, stakes: Mapping[int, int]) -> dict[int, int]:
        """Total returned per player: stake times the locked multiplier, or 0 if never cashed out."""
        return {
            user_id: stakes[user_id] * self.cashed_out[user_id] // 100
            if user_id in self.cashed_out
            else 0
            for user_id in self.user_ids
        }
```
Note: `test_the_crash_point_follows_the_house_edge_formula` expects `FixedRandom(0.0)` to give 100 (0.97 floors below 1.00 so the minimum applies) and `FixedRandom(0.5)` to give `floor(0.97 / 0.5 * 100) = 194`.

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_crash_engine.py -q`
Expected: all pass. If the distribution test is flaky at its bounds, the seed `2024` is fixed, so it is deterministic: adjust the bounds only if the true value (0.97 / 2 = 0.485) is inside them.

- [ ] **Step 5: Lint and commit**

```powershell
python -m ruff check .; python -m ruff format .; python -m ruff format --check .
git add src/yokai/gambling/engines/crash.py tests/test_crash_engine.py
git commit -m "feat(tables): add the crash engine" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>" -m "Claude-Session: https://claude.ai/code/session_01BxUkTAJGP4uNByTtyvj8n9"
```

---

### Task 4: Blackjack table engine

**Files:**
- Modify: `src/yokai/gambling/engines/blackjack.py` (extract `compare_hands`)
- Create: `src/yokai/gambling/engines/bjtable.py`
- Create: `tests/test_bjtable_engine.py`

**Interfaces:**
- Produces: `blackjack.compare_hands(player: Sequence[Card], dealer: Sequence[Card]) -> Outcome`; `BlackjackTable(shoe, user_ids)` with `order`, `hands: dict[int, list[Card]]`, `dealer: list[Card]`, `busted: set[int]`, `current -> Optional[int]`, `players_finished`, `hit(user_id)`, `stand(user_id)`, `play_dealer()`, `outcomes() -> dict[int, Outcome]`, `payouts(stakes) -> dict[int, int]`, `status_of(user_id) -> str` (one of `"blackjack"`, `"bust"`, `"21"`, `"stand"`, `"playing"`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_bjtable_engine.py`:
```python
"""The shared-dealer blackjack table: turn order, naturals, dealer play and payouts."""

from __future__ import annotations

import pytest

from tests.card_helpers import cards
from yokai.errors import LobbyClosedError, NotYourTurnError
from yokai.gambling.engines.blackjack import Outcome, compare_hands
from yokai.gambling.engines.bjtable import BlackjackTable


def _table(shoe: str, users: list[int]) -> BlackjackTable:
    return BlackjackTable(cards(shoe), users)


def test_the_deal_goes_player_by_player_then_the_dealer_twice() -> None:
    table = _table("2c 3c 4c 5c 6c 7c", [1, 2])
    assert [str(c) for c in table.hands[1]] == ["2c", "5c"]
    assert [str(c) for c in table.hands[2]] == ["3c", "6c"]
    assert [str(c) for c in table.dealer] == ["4c", "7c"]


def test_players_act_in_join_order_and_only_when_it_is_their_turn() -> None:
    table = _table("10h 9d 7h 8c 9s 10d", [1, 2])  # 18, 18 against a dealer 17
    assert table.current == 1
    with pytest.raises(NotYourTurnError):
        table.stand(2)
    with pytest.raises(NotYourTurnError):
        table.hit(2)
    table.stand(1)
    assert table.current == 2 and not table.players_finished
    table.stand(2)
    assert table.current is None and table.players_finished


def test_a_hit_that_busts_moves_on_to_the_next_player() -> None:
    table = _table("10h 9d 7h 6c 9s 10d 10c", [1, 2])  # player 1 has 16 and hits a ten
    table.hit(1)
    assert table.status_of(1) == "bust" and 1 in table.busted
    assert table.current == 2


def test_reaching_twenty_one_stands_automatically() -> None:
    table = _table("10h 9d 7h 5c 9s 10d 6c", [1, 2])  # 15 + a six = 21
    table.hit(1)
    assert table.status_of(1) == "21" and table.current == 2


def test_a_natural_skips_that_players_turn() -> None:
    table = _table("Ah 9d 7h Kc 9s 10d", [1, 2])  # player 1 has a natural
    assert table.status_of(1) == "blackjack" and table.current == 2


def test_a_dealer_natural_ends_the_round_at_the_deal() -> None:
    table = _table("10h 9d As 8c 9s Kd", [1, 2])  # dealer Ace + King
    assert table.current is None and table.players_finished
    table.play_dealer()
    assert len(table.dealer) == 2  # nothing to draw
    assert table.outcomes() == {1: Outcome.LOSE, 2: Outcome.LOSE}


def test_a_player_natural_pushes_against_a_dealer_natural() -> None:
    table = _table("Ah 9d As Kc 9s Kd", [1, 2])
    assert table.outcomes() == {1: Outcome.PUSH, 2: Outcome.LOSE}


def test_the_dealer_draws_to_seventeen_when_a_player_is_still_live() -> None:
    table = _table("10h 9d 5h 8c 9s 6d 7c", [1, 2])  # dealer 5 + 6 = 11, draws a seven
    table.stand(1)
    table.stand(2)
    table.play_dealer()
    assert [str(c) for c in table.dealer] == ["5h", "6d", "7c"]
    assert table.outcomes() == {1: Outcome.PUSH, 2: Outcome.PUSH}  # 18 against 18


def test_the_dealer_does_not_draw_when_every_player_busted() -> None:
    table = _table("10h 10d 5h 6c 6s 6d 10c 10s", [1, 2])  # both players hit to a bust
    table.hit(1)
    table.hit(2)
    table.play_dealer()
    assert len(table.dealer) == 2  # 5 + 6 = 11 stays untouched
    assert table.outcomes() == {1: Outcome.LOSE, 2: Outcome.LOSE}


def test_the_dealer_does_not_draw_when_every_player_has_a_natural() -> None:
    table = _table("Ah Kd 5h Kc Ad 6d", [1, 2])
    assert table.players_finished
    table.play_dealer()
    assert len(table.dealer) == 2
    assert table.outcomes() == {1: Outcome.BLACKJACK, 2: Outcome.BLACKJACK}


def test_a_dealer_bust_pays_every_standing_player() -> None:
    table = _table("10h 9d 6h 8c 9s 10d 10c", [1, 2])  # dealer 16 draws a ten and busts
    table.stand(1)
    table.stand(2)
    table.play_dealer()
    assert table.outcomes() == {1: Outcome.WIN, 2: Outcome.WIN}


def test_payouts_follow_the_solo_rules_including_three_to_two() -> None:
    table = _table("Ah 10d 9h Kc 8s 8d", [1, 2])  # natural for player 1, 18 for player 2
    table.stand(2)
    table.play_dealer()
    # dealer 9 + 8 = 17 stands: player 1 natural, player 2 wins 18 against 17
    assert table.payouts({1: 5, 2: 100}) == {1: 5 + 7, 2: 200}


def test_the_dealer_plays_once() -> None:
    table = _table("10h 9d 5h 8c 9s 6d 7c 2c", [1, 2])
    table.stand(1)
    table.stand(2)
    table.play_dealer()
    table.play_dealer()
    assert len(table.dealer) == 3


def test_the_dealer_cannot_play_before_the_players_are_done() -> None:
    table = _table("10h 9d 7h 8c 9s 10d", [1, 2])
    with pytest.raises(ValueError):
        table.play_dealer()


def test_no_one_can_act_once_the_round_is_over() -> None:
    table = _table("10h 9d As 8c 9s Kd", [1, 2])  # dealer natural
    with pytest.raises(LobbyClosedError):
        table.hit(1)


def test_compare_hands_matches_the_solo_game() -> None:
    assert compare_hands(cards("10h 6c 10d"), cards("9d 8s")) is Outcome.LOSE  # player bust
    assert compare_hands(cards("Ah Kd"), cards("9d 8s")) is Outcome.BLACKJACK
    assert compare_hands(cards("Ah Kd"), cards("As Kc")) is Outcome.PUSH
    assert compare_hands(cards("10h 9c"), cards("As Kc")) is Outcome.LOSE
    assert compare_hands(cards("10h 9c"), cards("10d 6s 10c")) is Outcome.WIN
    assert compare_hands(cards("10h 8c"), cards("9d 9s")) is Outcome.PUSH
```
Verify the stacked shoes against the dealing order P1 c1, P2 c1, D c1, P1 c2, P2 c2, D c2, then draws, when writing each case; fix a card string rather than the expectation if one is mis-stacked.

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_bjtable_engine.py tests/test_blackjack_engine.py -q`
Expected: collection ERROR for `compare_hands` / `bjtable`.

- [ ] **Step 3: Extract `compare_hands`**

In `src/yokai/gambling/engines/blackjack.py` add this module-level function before `class BlackjackGame`:
```python
def compare_hands(player: Sequence[Card], dealer: Sequence[Card]) -> Outcome:
    """The result of a finished hand against the dealer's finished hand."""
    player_total = hand_value(player)[0]
    dealer_total = hand_value(dealer)[0]
    if player_total > 21:
        return Outcome.LOSE
    player_natural = is_blackjack(player)
    dealer_natural = is_blackjack(dealer)
    if player_natural and dealer_natural:
        return Outcome.PUSH
    if player_natural:
        return Outcome.BLACKJACK
    if dealer_natural:
        return Outcome.LOSE
    if dealer_total > 21 or player_total > dealer_total:
        return Outcome.WIN
    if player_total == dealer_total:
        return Outcome.PUSH
    return Outcome.LOSE
```
and replace the body of `BlackjackGame._compare` with:
```python
    def _compare(self) -> None:
        self.outcome = compare_hands(self.player, self.dealer)
        self.finished = True
```

- [ ] **Step 4: Implement the table**

Create `src/yokai/gambling/engines/bjtable.py`:
```python
"""Blackjack with one shared dealer: players act in join order, each hand settles on its own."""

from __future__ import annotations

from typing import Mapping, Optional, Sequence

from yokai.errors import LobbyClosedError, NotYourTurnError
from yokai.gambling.engines.blackjack import (
    Card,
    Outcome,
    compare_hands,
    hand_value,
    is_blackjack,
    payout_for,
)


class BlackjackTable:
    """Deals P1, P2, ..., Dealer, then P1, P2, ..., Dealer from `shoe`; no double, no splits."""

    def __init__(self, shoe: Sequence[Card], user_ids: Sequence[int]) -> None:
        self._shoe = list(shoe)
        self._next = 0
        self.order = list(user_ids)
        self.hands: dict[int, list[Card]] = {user_id: [] for user_id in self.order}
        self.dealer: list[Card] = []
        self.busted: set[int] = set()
        self._done: set[int] = set()
        for _ in range(2):
            for user_id in self.order:
                self.hands[user_id].append(self._draw())
            self.dealer.append(self._draw())
        if is_blackjack(self.dealer):
            self._done = set(self.order)  # the dealer peeks: nobody gets to act
        else:
            self._done = {u for u in self.order if is_blackjack(self.hands[u])}

    def _draw(self) -> Card:
        card = self._shoe[self._next]
        self._next += 1
        return card

    @property
    def current(self) -> Optional[int]:
        """The player whose turn it is, or None once every player is done."""
        for user_id in self.order:
            if user_id not in self._done:
                return user_id
        return None

    @property
    def players_finished(self) -> bool:
        return self.current is None

    def _require_turn(self, user_id: int) -> None:
        current = self.current
        if current is None:
            raise LobbyClosedError("The players are done; the dealer is playing.", user_hint=None)
        if current != user_id:
            raise NotYourTurnError()

    def hit(self, user_id: int) -> None:
        self._require_turn(user_id)
        self.hands[user_id].append(self._draw())
        total = hand_value(self.hands[user_id])[0]
        if total > 21:
            self.busted.add(user_id)
            self._done.add(user_id)
        elif total == 21:
            self._done.add(user_id)

    def stand(self, user_id: int) -> None:
        self._require_turn(user_id)
        self._done.add(user_id)

    def status_of(self, user_id: int) -> str:
        """'bust', 'blackjack', '21', 'stand' for a finished hand, 'playing' otherwise."""
        if user_id in self.busted:
            return "bust"
        if is_blackjack(self.hands[user_id]):
            return "blackjack"
        if user_id not in self._done:
            return "playing"
        return "21" if hand_value(self.hands[user_id])[0] == 21 else "stand"

    def play_dealer(self) -> None:
        """The dealer draws to 17 unless every player busted or has a natural. Safe to repeat."""
        if not self.players_finished:
            raise ValueError("the players are not done yet")
        live = [
            user_id
            for user_id in self.order
            if user_id not in self.busted and not is_blackjack(self.hands[user_id])
        ]
        if not live or is_blackjack(self.dealer):
            return
        while hand_value(self.dealer)[0] < 17:
            self.dealer.append(self._draw())

    def outcomes(self) -> dict[int, Outcome]:
        return {
            user_id: compare_hands(self.hands[user_id], self.dealer) for user_id in self.order
        }

    def payouts(self, stakes: Mapping[int, int]) -> dict[int, int]:
        return {
            user_id: payout_for(outcome, stakes[user_id])
            for user_id, outcome in self.outcomes().items()
        }
```

- [ ] **Step 5: Run to verify they pass**

Run: `python -m pytest tests/test_bjtable_engine.py tests/test_blackjack_engine.py tests/test_blackjack_view.py -q`
Expected: all pass (the solo tests prove the `compare_hands` extraction changed nothing).

- [ ] **Step 6: Suite, lint, commit**

```powershell
python -m pytest -q; python -m ruff check .; python -m ruff format .; python -m ruff format --check .
git add src/yokai/gambling/engines tests/test_bjtable_engine.py
git commit -m "feat(tables): add the shared-dealer blackjack table engine" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>" -m "Claude-Session: https://claude.ai/code/session_01BxUkTAJGP4uNByTtyvj8n9"
```

---

### Task 5: Embeds

**Files:**
- Modify: `src/yokai/ui/embeds.py` (append classmethods inside `EmbedFactory`, after `duel_result`)
- Modify: `scripts/preview_embeds.py`, `docs/preview/embeds.html`
- Create: `tests/test_m3_embeds.py`

**Interfaces:**
- Produces on `EmbedFactory`:
  - `jackpot_drawing(entries: Sequence[tuple[int, int]], pot: int, rake_percent: int, bot_avatar_url=None)`
  - `jackpot_result(entries, winner_id: int, prize: int, rake: int, bot_avatar_url=None)`
  - `crash_flying(rows: Sequence[tuple[int, int, Optional[int]]], multiplier: int, bot_avatar_url=None)`, rows are `(user_id, stake, cashed_out_hundredths_or_None)`, `multiplier` in hundredths
  - `crash_result(rows, crash_point: int, payouts: Mapping[int, int], bot_avatar_url=None)`
  - `bjtable_hand(seats: Sequence[tuple[int, int, Sequence[str], int, str]], dealer_cards: Sequence[str], dealer_total: Optional[int], deadline_ts: Optional[int], finished: bool, bot_avatar_url=None)`, each seat is `(user_id, stake, cards, total, status_text)`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_m3_embeds.py`:
```python
"""The jackpot, crash and blackjack table embeds obey the layout rules."""

from __future__ import annotations

import pytest

from tests.embed_helpers import all_embed_text, emoji_count, total_embed_length
from yokai.theme import COLOR_BRAND, COLOR_SUCCESS
from yokai.ui.embeds import EmbedFactory

TEN = [(uid, 100 * uid) for uid in range(1, 11)]
CRASH_ROWS = [(uid, 100, 231 if uid % 2 else None) for uid in range(1, 11)]
SEATS = [
    (1, 100, ["10h", "6c"], 16, "to act"),
    (2, 50, ["As", "Kd"], 21, "blackjack"),
    (3, 25, ["9d", "9s", "10c"], 28, "bust"),
    (4, 10, ["10d", "8c"], 18, "stand"),
    (5, 10, ["2d", "3c"], 5, "waiting"),
]


def _embeds() -> list[tuple[str, object]]:
    return [
        ("jackpot-drawing", EmbedFactory.jackpot_drawing(TEN, 5500, 2)),
        ("jackpot-result", EmbedFactory.jackpot_result(TEN, 4, 5390, 110)),
        ("crash-flying", EmbedFactory.crash_flying(CRASH_ROWS, 231)),
        ("crash-result", EmbedFactory.crash_result(CRASH_ROWS, 412, {1: 231, 3: 231})),
        ("bj-playing", EmbedFactory.bjtable_hand(SEATS, ["9h", "??"], None, 1_700_000_045, False)),
        ("bj-final", EmbedFactory.bjtable_hand(SEATS, ["9h", "10c"], 19, None, True)),
    ]


@pytest.mark.parametrize("label,embed", _embeds())
def test_every_embed_obeys_the_layout_rules(label: str, embed) -> None:  # type: ignore[no-untyped-def]
    assert emoji_count(embed) <= 1, label
    assert total_embed_length(embed) <= 6000, label
    assert len(embed.fields) <= 25, label
    text = all_embed_text(embed)
    assert "♠" not in text and "♥" not in text and "♦" not in text and "♣" not in text


def test_the_jackpot_shows_each_players_chance_of_winning() -> None:
    text = all_embed_text(EmbedFactory.jackpot_drawing([(1, 100), (2, 300)], 400, 2))
    assert "<@1>" in text and "25%" in text and "<@2>" in text and "75%" in text
    assert "400" in text and "2%" in text


def test_the_jackpot_result_names_the_winner_and_the_prize() -> None:
    embed = EmbedFactory.jackpot_result([(1, 100), (2, 300)], 2, 392, 8)
    text = all_embed_text(embed)
    assert "<@2> wins 392" in text and "8" in text
    assert embed.color is not None and embed.color.value == COLOR_SUCCESS


def test_a_jackpot_with_no_rake_does_not_mention_one() -> None:
    text = all_embed_text(EmbedFactory.jackpot_result([(1, 100), (2, 100)], 1, 200, 0)).lower()
    assert "rake" not in text


def test_the_flying_crash_shows_the_multiplier_and_who_is_still_in() -> None:
    text = all_embed_text(EmbedFactory.crash_flying([(1, 100, None), (2, 50, 231)], 187))
    assert "1.87x" in text and "<@1>" in text and "still in" in text
    assert "<@2>" in text and "2.31x" in text


def test_the_crash_result_shows_the_crash_point_and_each_net() -> None:
    embed = EmbedFactory.crash_result([(1, 100, 231), (2, 100, None)], 412, {1: 231, 2: 0})
    text = all_embed_text(embed)
    assert "4.12x" in text and "+131" in text and "-100" in text
    assert embed.color is not None


def test_a_crash_where_nobody_cashed_out_reads_as_a_loss() -> None:
    embed = EmbedFactory.crash_result([(1, 100, None)], 100, {1: 0})
    assert "1.00x" in all_embed_text(embed) and "-100" in all_embed_text(embed)


def test_the_blackjack_table_lists_every_hand_and_hides_the_dealers_hole_card() -> None:
    embed = EmbedFactory.bjtable_hand(SEATS, ["9h", "??"], None, 1_700_000_045, False)
    text = all_embed_text(embed)
    assert text.count("<@") == 5 and "10h 6c" in text and "to act" in text
    assert "9h ??" in text and "<t:1700000045:R>" in text
    assert embed.color is not None and embed.color.value == COLOR_BRAND


def test_the_final_blackjack_table_shows_the_dealers_total_and_no_countdown() -> None:
    text = all_embed_text(EmbedFactory.bjtable_hand(SEATS, ["9h", "10c"], 19, None, True))
    assert "9h 10c" in text and "(19)" in text and "<t:" not in text
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_m3_embeds.py -q`
Expected: failures: `EmbedFactory` has no attribute `jackpot_drawing`.

- [ ] **Step 3: Implement**

Append inside `EmbedFactory` in `src/yokai/ui/embeds.py` (after `duel_result`, before the module-level `_render_progress_bar`; write real `"\n"` newlines):
```python
    @classmethod
    def jackpot_drawing(
        cls,
        entries: Sequence[tuple[int, int]],  # (user_id, stake)
        pot: int,
        rake_percent: int,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        """The jackpot while the winner is being drawn: everyone's stake and chance."""
        lines = [
            f"<@{user_id}> — **{stake:,}** ({stake * 100 / pot:.0f}%)" for user_id, stake in entries
        ]
        rake_note = f" The house keeps {rake_percent}%." if rake_percent else ""
        fields: list[FieldSpec] = [
            ("Pot", f"**{pot:,}**", True),
            ("Players", f"**{len(entries)}**", True),
        ]
        return cls._create_base(
            title=f"{Emoji.SLOTS} Jackpot",
            description="\n".join(lines) + f"\n\nDrawing a winner, weighted by stake.{rake_note}",
            color=COLOR_BRAND,
            context="Gambling",
            bot_avatar_url=bot_avatar_url,
            fields=_grid(fields),
        )

    @classmethod
    def jackpot_result(
        cls,
        entries: Sequence[tuple[int, int]],
        winner_id: int,
        prize: int,
        rake: int,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        """A drawn jackpot: the winner, the prize, and every entrant's chance."""
        pot = sum(stake for _, stake in entries)
        lines = [
            f"<@{user_id}> — **{stake:,}** ({stake * 100 / pot:.0f}%)" for user_id, stake in entries
        ]
        fields: list[FieldSpec] = [("Prize", f"**{prize:,}**", True)]
        if rake:
            fields.append(("Rake", f"**{rake:,}**", True))
        return cls._create_base(
            title=f"{Emoji.SLOTS} Jackpot",
            description=f"**<@{winner_id}> wins {prize:,} Renown.**\n\n" + "\n".join(lines),
            color=COLOR_SUCCESS,
            context="Gambling",
            bot_avatar_url=bot_avatar_url,
            quip=quip_bank.get("game_win"),
            fields=_grid(fields),
        )

    @classmethod
    def crash_flying(
        cls,
        rows: Sequence[tuple[int, int, Optional[int]]],  # (user_id, stake, cashed out at)
        multiplier: int,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        """A crash round in flight. Multipliers are whole hundredths (231 is 2.31x)."""
        lines = [
            f"<@{user_id}> — **{stake:,}** — "
            + ("still in" if cashed is None else f"out at **{cashed / 100:.2f}x**")
            for user_id, stake, cashed in rows
        ]
        return cls._create_base(
            title=f"{Emoji.DRONE} Crash",
            description=f"`{multiplier / 100:.2f}x`\n\n" + "\n".join(lines),
            color=COLOR_BRAND,
            context="Gambling",
            bot_avatar_url=bot_avatar_url,
        )

    @classmethod
    def crash_result(
        cls,
        rows: Sequence[tuple[int, int, Optional[int]]],
        crash_point: int,
        payouts: Mapping[int, int],
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        """A finished crash round: where it crashed and each player's net."""
        lines = []
        for user_id, stake, cashed in rows:
            net = _signed(payouts.get(user_id, 0) - stake)
            if cashed is None:
                lines.append(f"<@{user_id}> — crashed — **{net}**")
            else:
                lines.append(f"<@{user_id}> — out at **{cashed / 100:.2f}x** — **{net}**")
        anyone_won = any(cashed is not None for _, _, cashed in rows)
        return cls._create_base(
            title=f"{Emoji.DRONE} Crash",
            description=f"It crashed at **{crash_point / 100:.2f}x**.\n\n" + "\n".join(lines),
            color=COLOR_SUCCESS if anyone_won else COLOR_WARNING,
            context="Gambling",
            bot_avatar_url=bot_avatar_url,
            quip=quip_bank.get("game_win" if anyone_won else "game_loss"),
        )

    @classmethod
    def bjtable_hand(
        cls,
        seats: Sequence[tuple[int, int, Sequence[str], int, str]],
        dealer_cards: Sequence[str],
        dealer_total: Optional[int],
        deadline_ts: Optional[int],
        finished: bool,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        """The shared-dealer table: every hand with a status, and the dealer's cards."""
        lines = [
            f"<@{user_id}> (**{stake:,}**) `{' '.join(hand)}` ({total}) — {status}"
            for user_id, stake, hand, total, status in seats
        ]
        dealer_suffix = f" ({dealer_total})" if dealer_total is not None else ""
        lines.append(f"**Dealer** `{' '.join(dealer_cards)}`{dealer_suffix}")
        fields: list[FieldSpec] = []
        if deadline_ts is not None and not finished:
            fields.append(("Turn ends", f"<t:{deadline_ts}:R>", True))
        return cls._create_base(
            title=f"{Emoji.DICE} Blackjack table",
            description="\n".join(lines),
            color=COLOR_BRAND,
            context="Gambling",
            bot_avatar_url=bot_avatar_url,
            fields=_grid(fields),
        )
```
Add this block to `scripts/preview_embeds.py` just before the closing `    ]` of the `samples` list, then regenerate: `python scripts/preview_embeds.py`.
```python
        (
            "Jackpot (drawing)",
            EmbedFactory.jackpot_drawing(
                [(42, 100), (43, 300), (44, 200)], 600, 2, bot_avatar_url=av
            ),
        ),
        (
            "Jackpot (result)",
            EmbedFactory.jackpot_result(
                [(42, 100), (43, 300), (44, 200)], 43, 588, 12, bot_avatar_url=av
            ),
        ),
        (
            "Crash (in flight)",
            EmbedFactory.crash_flying(
                [(42, 100, None), (43, 50, 231), (44, 200, None)], 187, bot_avatar_url=av
            ),
        ),
        (
            "Crash (result)",
            EmbedFactory.crash_result(
                [(42, 100, None), (43, 50, 231)], 412, {42: 0, 43: 115}, bot_avatar_url=av
            ),
        ),
        (
            "Blackjack table (in play)",
            EmbedFactory.bjtable_hand(
                [
                    (42, 100, ["10h", "6c"], 16, "to act"),
                    (43, 50, ["As", "Kd"], 21, "blackjack"),
                ],
                ["9h", "??"],
                None,
                1_900_000_045,
                False,
                bot_avatar_url=av,
            ),
        ),
        (
            "Blackjack table (final)",
            EmbedFactory.bjtable_hand(
                [
                    (42, 100, ["10h", "6c", "10d"], 26, "bust: -100"),
                    (43, 50, ["As", "Kd"], 21, "blackjack: +75"),
                ],
                ["9h", "10c"],
                19,
                None,
                True,
                bot_avatar_url=av,
            ),
        ),
```
Import `COLOR_WARNING` is already available in `embeds.py`; ensure `Mapping` is imported (it is, from M2).

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_m3_embeds.py tests/test_preview_embeds.py -q`
Expected: all pass.

- [ ] **Step 5: Suite, lint, commit**

```powershell
python -m pytest -q; python -m ruff check .; python -m ruff format .; python -m ruff format --check .
git add src/yokai/ui/embeds.py scripts/preview_embeds.py docs/preview/embeds.html tests/test_m3_embeds.py
git commit -m "feat(tables): add the jackpot, crash and blackjack table embeds" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>" -m "Claude-Session: https://claude.ai/code/session_01BxUkTAJGP4uNByTtyvj8n9"
```

---

### Task 6: Shared failure guard and the jackpot runner

**Files:**
- Modify: `src/yokai/ui/table_common.py` (add `tell_cancelled`, `finish_table`), `src/yokai/ui/duel_views.py` (use `tell_cancelled`)
- Create: `src/yokai/ui/jackpot_views.py`
- Create: `tests/test_jackpot_views.py`

**Interfaces:**
- Consumes: `TableManager.resolve_jackpot`, `EmbedFactory.jackpot_drawing/jackpot_result` (Tasks 2, 5), `GAME_RUNNERS` shape from M2.
- Produces: `table_common.tell_cancelled(bot, message) -> None`; `table_common.finish_table(bot, lobby, message, play) -> None` where `play: Callable[[], Awaitable[discord.Embed]]` runs the game, settles the table and returns the final embed (on any failure the table is aborted, players told, and the error re-raised); `jackpot_views.JACKPOT_REVEAL_DELAY_S = 1.5`; `jackpot_views.run_jackpot(bot, lobby, message) -> None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_jackpot_views.py`:
```python
"""The jackpot runner: draw, reveal, settle, and refund on any failure."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from tests.moderation_helpers import fake_moderation
from tests.table_helpers import ALL_PLAYABLE, CHANNEL, announce_ok, balance_of, world
from tests.test_table_manager import FixedRoll
from yokai.gambling.lobby import LobbyState
from yokai.ui.jackpot_views import run_jackpot


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


async def _running(manager):  # type: ignore[no-untyped-def]
    lobby = await manager.open_table(CHANNEL, "jackpot", 1, 100, announce_ok)
    await manager.join(lobby, 2, 300)
    await manager.begin(lobby)
    return lobby


@pytest.mark.asyncio
async def test_the_runner_shows_the_draw_then_the_result_and_pays_the_winner(
    tmp_path: Path,
) -> None:
    async with world(tmp_path, specs=ALL_PLAYABLE) as (db, manager, active):
        manager.rng_factory = lambda: FixedRoll(399)  # the last entrant, user 2
        lobby = await _running(manager)
        message = make_message()
        with patch("yokai.ui.jackpot_views.JACKPOT_REVEAL_DELAY_S", 0):
            await run_jackpot(make_bot(db, manager), lobby, message)
        assert message.edit.await_count == 2
        drawing = message.edit.await_args_list[0].kwargs["embed"]
        assert "Drawing" in drawing.description
        final = message.edit.await_args.kwargs
        assert "<@2> wins 392" in final["embed"].description and final["view"] is None
        assert await balance_of(db, 2) == 1000 - 300 + 392
        assert await db.get_pot() == 8 and lobby.state is LobbyState.SETTLED
        assert not active.is_active(1) and not active.is_active(2)


@pytest.mark.asyncio
async def test_a_failure_while_drawing_refunds_everyone_and_tells_the_players(
    tmp_path: Path,
) -> None:
    async with world(tmp_path, specs=ALL_PLAYABLE) as (db, manager, active):
        lobby = await _running(manager)
        message = make_message()
        failing = AsyncMock(side_effect=RuntimeError("disk"))
        with (
            patch("yokai.ui.jackpot_views.JACKPOT_REVEAL_DELAY_S", 0),
            patch.object(manager, "resolve_jackpot", new=failing),
        ):
            with pytest.raises(RuntimeError):
                await run_jackpot(make_bot(db, manager), lobby, message)
        assert lobby.state is LobbyState.CANCELLED
        assert (await balance_of(db, 1), await balance_of(db, 2)) == (1000, 1000)
        assert not active.is_active(1)
        assert "cancelled" in message.edit.await_args.kwargs["embed"].title.lower()


@pytest.mark.asyncio
async def test_a_message_that_cannot_be_edited_still_refunds(tmp_path: Path) -> None:
    async with world(tmp_path, specs=ALL_PLAYABLE) as (db, manager, _):
        lobby = await _running(manager)
        message = make_message()
        message.edit = AsyncMock(side_effect=RuntimeError("deleted"))
        with patch("yokai.ui.jackpot_views.JACKPOT_REVEAL_DELAY_S", 0):
            with pytest.raises(RuntimeError):
                await run_jackpot(make_bot(db, manager), lobby, message)
        assert (await balance_of(db, 1), await balance_of(db, 2)) == (1000, 1000)
        assert lobby.state is LobbyState.CANCELLED
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_jackpot_views.py -q`
Expected: collection ERROR `No module named 'yokai.ui.jackpot_views'`.

- [ ] **Step 3: Implement the shared guard**

In `src/yokai/ui/table_common.py` add imports `from typing import TYPE_CHECKING, Any, Awaitable, Callable, Optional` and `from yokai.gambling.lobby import Lobby` (inside `TYPE_CHECKING` if ruff reports a cycle), then append:
```python
async def tell_cancelled(bot: YokaiBot, message: discord.Message) -> None:
    """Best effort: replace a table's message so nobody waits on dead buttons."""
    notice = EmbedFactory.table_notice(
        "Table cancelled",
        "Something went wrong. Every stake was refunded.",
        bot_avatar_url=avatar_url(bot),
    )
    try:
        await message.edit(embed=notice, view=None)
    except discord.HTTPException as exc:
        logger.warning("Could not tell the players the table was cancelled: %s", exc)


async def finish_table(
    bot: YokaiBot,
    lobby: Lobby,
    message: discord.Message,
    play: Callable[[], Awaitable[discord.Embed]],
) -> None:
    """Run a game: `play` settles the table and returns the final embed.

    If anything fails, every stake is refunded, the players are told, and the error is re-raised.
    """
    try:
        final = await play()
    except BaseException as exc:
        await bot.tables.abort(lobby)
        if isinstance(exc, Exception):
            await tell_cancelled(bot, message)
        raise
    try:
        await message.edit(embed=final, view=None)
    except discord.HTTPException as exc:
        logger.warning("Could not show the result (the stakes are already settled): %s", exc)
```
In `src/yokai/ui/duel_views.py`: delete the local `_tell_cancelled` function, import `tell_cancelled` from `yokai.ui.table_common` (add to the existing import list) and replace the call `await _tell_cancelled(bot, message)` with `await tell_cancelled(bot, message)`.

- [ ] **Step 4: Implement the runner**

Create `src/yokai/ui/jackpot_views.py`:
```python
"""The jackpot: show the draw, pick a winner by stake, pay out."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import discord

from yokai.gambling.lobby import Lobby
from yokai.ui.embeds import EmbedFactory
from yokai.ui.table_common import avatar_url, finish_table

if TYPE_CHECKING:
    from yokai.bot import YokaiBot

# A short pause between "drawing" and the result; two edits keep us far from any rate limit.
JACKPOT_REVEAL_DELAY_S = 1.5


async def run_jackpot(bot: YokaiBot, lobby: Lobby, message: discord.Message) -> None:
    """Run a started jackpot. Any failure refunds every stake and tells the players."""
    tables = bot.tables
    entries = [(user_id, seat.stake) for user_id, seat in lobby.seats.items()]

    async def play() -> discord.Embed:
        await message.edit(
            embed=EmbedFactory.jackpot_drawing(
                entries, lobby.pot, tables.jackpot_rake_percent, bot_avatar_url=avatar_url(bot)
            ),
            view=None,
        )
        await asyncio.sleep(JACKPOT_REVEAL_DELAY_S)
        result = await tables.resolve_jackpot(lobby)
        return EmbedFactory.jackpot_result(
            entries, result.winner_id, result.prize, result.rake, bot_avatar_url=avatar_url(bot)
        )

    await finish_table(bot, lobby, message, play)
```

- [ ] **Step 5: Run to verify they pass**

Run: `python -m pytest tests/test_jackpot_views.py tests/test_duel_views.py -q`
Expected: all pass (the duel tests prove the `tell_cancelled` move changed nothing).

- [ ] **Step 6: Suite, lint, commit**

```powershell
python -m pytest -q; python -m ruff check .; python -m ruff format .; python -m ruff format --check .
git add src/yokai/ui tests/test_jackpot_views.py
git commit -m "feat(tables): add the jackpot runner and a shared table failure guard" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>" -m "Claude-Session: https://claude.ai/code/session_01BxUkTAJGP4uNByTtyvj8n9"
```

---

### Task 7: Crash runner

**Files:**
- Create: `src/yokai/ui/crash_views.py`
- Create: `tests/test_crash_views.py`

**Interfaces:**
- Consumes: `CrashRound`, `draw_crash_point`, `next_tick_interval`, `MIN_TICK_S` (Task 3), `EmbedFactory.crash_flying/crash_result` (Task 5), `finish_table` (Task 6), `TableManager.settle`, `tables.clock`, `tables.crash_max_multiplier`.
- Produces: `CrashView(bot, lobby, crash, started_at)` with a **Cash out** button, an `asyncio.Event` named `wake`, `rows() -> list[tuple[int, int, Optional[int]]]`; module function `_pause(view, seconds)` (waits up to `seconds` or until `view.wake` is set; tests replace it with a fake that advances a fake clock); `run_crash(bot, lobby, message) -> None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_crash_views.py`:
```python
"""The crash runner against a fake clock: cash-outs are judged by the server's time."""

from __future__ import annotations

from pathlib import Path
from typing import Awaitable, Callable, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from tests.moderation_helpers import fake_moderation
from tests.table_helpers import ALL_PLAYABLE, CHANNEL, announce_ok, balance_of, world
from yokai.gambling.lobby import LobbyState
from yokai.ui.crash_views import CrashView, run_crash


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


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
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


Script = Callable[[CrashView, float], Awaitable[None]]


def fake_pause(clock: FakeClock, script: Optional[Script] = None):  # type: ignore[no-untyped-def]
    """A replacement for the runner's wait: advance the clock, then let the test act."""

    async def pause(view: CrashView, seconds: float) -> None:
        clock.now += seconds
        if script is not None:
            await script(view, clock.now - view.started_at)

    return pause


async def _running(manager, stakes=(100, 100)):  # type: ignore[no-untyped-def]
    lobby = await manager.open_table(CHANNEL, "crash", 1, stakes[0], announce_ok)
    for user_id, stake in enumerate(stakes[1:], start=2):
        await manager.join(lobby, user_id, stake)
    await manager.begin(lobby)
    return lobby


async def _press(view: CrashView, user_id: int) -> MagicMock:
    interaction = make_interaction(user_id)
    with patch("yokai.ui.table_common.send", new=AsyncMock()):
        with patch("yokai.ui.crash_views.send", new=AsyncMock()) as sent:
            await view.cash_out_button.callback(interaction)
            interaction.sent = sent  # type: ignore[attr-defined]
    return interaction


@pytest.mark.asyncio
async def test_a_player_who_cashes_out_in_time_is_paid_and_the_other_loses(tmp_path: Path) -> None:
    async with world(tmp_path, specs=ALL_PLAYABLE) as (db, manager, active):
        clock = FakeClock()
        manager.clock = clock
        lobby = await _running(manager)
        message = make_message()
        pressed = {"done": False}

        async def script(view: CrashView, elapsed: float) -> None:
            if elapsed >= 5.0 and not pressed["done"]:
                pressed["done"] = True
                await _press(view, 1)

        with (
            patch("yokai.ui.crash_views.draw_crash_point", return_value=1000),
            patch("yokai.ui.crash_views._pause", new=fake_pause(clock, script)),
        ):
            await run_crash(make_bot(db, manager), lobby, message)

        # 5 s -> floor(exp(0.35) * 100) = 141 hundredths: 100 -> 141
        assert await balance_of(db, 1) == 1000 - 100 + 141
        assert await balance_of(db, 2) == 900
        assert await db.get_pot() == 100  # the lost stake feeds the lottery pot
        assert lobby.state is LobbyState.SETTLED
        assert not active.is_active(1) and not active.is_active(2)
        final = message.edit.await_args.kwargs
        assert "10.00x" in final["embed"].description and final["view"] is None


@pytest.mark.asyncio
async def test_a_press_after_the_crash_time_loses_even_if_the_message_still_shows_a_lower_number(
    tmp_path: Path,
) -> None:
    async with world(tmp_path, specs=ALL_PLAYABLE) as (db, manager, _):
        clock = FakeClock()
        manager.clock = clock
        lobby = await _running(manager, (100,))
        message = make_message()
        late: dict[str, MagicMock] = {}

        async def script(view: CrashView, elapsed: float) -> None:
            # the crash is at 2.00x, about 9.9 s in; press at 12 s: the runner has not woken yet
            if elapsed >= 9.0 and "press" not in late:
                clock.now = view.started_at + 12.0
                late["press"] = await _press(view, 1)

        with (
            patch("yokai.ui.crash_views.draw_crash_point", return_value=200),
            patch("yokai.ui.crash_views._pause", new=fake_pause(clock, script)),
        ):
            await run_crash(make_bot(db, manager), lobby, message)

        assert await balance_of(db, 1) == 900  # lost the stake
        assert late["press"].response.send_message.await_count + 0 >= 0  # a reply was attempted


@pytest.mark.asyncio
async def test_everyone_cashing_out_ends_the_round_before_the_crash(tmp_path: Path) -> None:
    async with world(tmp_path, specs=ALL_PLAYABLE) as (db, manager, _):
        clock = FakeClock()
        manager.clock = clock
        lobby = await _running(manager)
        message = make_message()
        calls = {"n": 0}

        async def script(view: CrashView, elapsed: float) -> None:
            calls["n"] += 1
            if calls["n"] == 1:
                await _press(view, 1)
                await _press(view, 2)
                view.wake.set()

        with (
            patch("yokai.ui.crash_views.draw_crash_point", return_value=10_000),
            patch("yokai.ui.crash_views._pause", new=fake_pause(clock, script)),
        ):
            await run_crash(make_bot(db, manager), lobby, message)

        assert calls["n"] == 1  # it did not wait for the 100x crash
        assert await balance_of(db, 1) > 1000 - 100 and await balance_of(db, 2) > 1000 - 100
        assert await db.get_pot() == 0


@pytest.mark.asyncio
async def test_a_crash_point_of_one_loses_everyone_immediately(tmp_path: Path) -> None:
    async with world(tmp_path, specs=ALL_PLAYABLE) as (db, manager, _):
        clock = FakeClock()
        manager.clock = clock
        lobby = await _running(manager)
        message = make_message()
        with (
            patch("yokai.ui.crash_views.draw_crash_point", return_value=100),
            patch("yokai.ui.crash_views._pause", new=fake_pause(clock)),
        ):
            await run_crash(make_bot(db, manager), lobby, message)
        assert (await balance_of(db, 1), await balance_of(db, 2)) == (900, 900)
        assert await db.get_pot() == 200
        assert "1.00x" in message.edit.await_args.kwargs["embed"].description


@pytest.mark.asyncio
async def test_a_solo_round_runs(tmp_path: Path) -> None:
    async with world(tmp_path, specs=ALL_PLAYABLE) as (db, manager, active):
        clock = FakeClock()
        manager.clock = clock
        lobby = await _running(manager, (200,))
        assert len(lobby.seats) == 1
        message = make_message()
        with (
            patch("yokai.ui.crash_views.draw_crash_point", return_value=150),
            patch("yokai.ui.crash_views._pause", new=fake_pause(clock)),
        ):
            await run_crash(make_bot(db, manager), lobby, message)
        assert lobby.state is LobbyState.SETTLED and await balance_of(db, 1) == 800
        assert not active.is_active(1)


@pytest.mark.asyncio
async def test_a_stranger_cannot_cash_out(tmp_path: Path) -> None:
    async with world(tmp_path, specs=ALL_PLAYABLE) as (db, manager, _):
        clock = FakeClock()
        manager.clock = clock
        lobby = await _running(manager, (100,))
        from yokai.gambling.engines.crash import CrashRound

        view = CrashView(make_bot(db, manager), lobby, CrashRound([1], 500), clock())
        interaction = make_interaction(3)
        with patch("yokai.ui.table_common.send", new=AsyncMock()) as sent:
            await view.cash_out_button.callback(interaction)
        assert sent.await_args.kwargs["ephemeral"] is True
        assert "not seated" in sent.await_args.kwargs["embed"].description
        assert view.crash.cashed_out == {}


@pytest.mark.asyncio
async def test_a_failure_while_settling_refunds_everyone_and_tells_the_players(
    tmp_path: Path,
) -> None:
    async with world(tmp_path, specs=ALL_PLAYABLE) as (db, manager, active):
        clock = FakeClock()
        manager.clock = clock
        lobby = await _running(manager)
        message = make_message()
        failing = AsyncMock(side_effect=RuntimeError("disk"))
        with (
            patch("yokai.ui.crash_views.draw_crash_point", return_value=100),
            patch("yokai.ui.crash_views._pause", new=fake_pause(clock)),
            patch.object(db, "settle_lobby", new=failing),
        ):
            with pytest.raises(RuntimeError):
                await run_crash(make_bot(db, manager), lobby, message)
        assert lobby.state is LobbyState.CANCELLED
        assert (await balance_of(db, 1), await balance_of(db, 2)) == (1000, 1000)
        assert not active.is_active(1)
        assert "cancelled" in message.edit.await_args.kwargs["embed"].title.lower()


@pytest.mark.asyncio
async def test_a_slow_edit_never_delays_the_crash(tmp_path: Path) -> None:
    """Edits run in the background: the round still ends on the server clock."""
    async with world(tmp_path, specs=ALL_PLAYABLE) as (db, manager, _):
        clock = FakeClock()
        manager.clock = clock
        lobby = await _running(manager, (100,))
        message = make_message()
        import asyncio

        gate = asyncio.Event()

        async def slow_edit(**kwargs):  # type: ignore[no-untyped-def]
            if not gate.is_set() and message.edit.await_count > 1:
                await gate.wait()

        message.edit = AsyncMock(side_effect=slow_edit)

        async def script(view: CrashView, elapsed: float) -> None:
            if elapsed > 30:
                gate.set()

        with (
            patch("yokai.ui.crash_views.draw_crash_point", return_value=300),
            patch("yokai.ui.crash_views._pause", new=fake_pause(clock, script)),
        ):
            await asyncio.wait_for(run_crash(make_bot(db, manager), lobby, message), timeout=5)
        assert lobby.state is LobbyState.SETTLED
```
In `test_a_press_after_the_crash_time_loses...` replace the final weak assertion with a precise one once the reply mechanics are clear: assert that `late["press"].sent.await_args` (captured from the patched `yokai.ui.table_common.send`) is an ephemeral "already crashed" error. If capturing is awkward, assert the balance and `view.crash.cashed_out == {}` instead.

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_crash_views.py -q`
Expected: collection ERROR `No module named 'yokai.ui.crash_views'`.

- [ ] **Step 3: Implement**

Create `src/yokai/ui/crash_views.py`:
```python
"""Crash: a multiplier climbs until it crashes; players cash out before it does."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import TYPE_CHECKING, Optional

import discord

from yokai.errors import YokaiError
from yokai.gambling.engines.crash import (
    MIN_TICK_S,
    CrashRound,
    draw_crash_point,
    next_tick_interval,
)
from yokai.gambling.lobby import Lobby
from yokai.moderation.gate import screen
from yokai.ui.embeds import EmbedFactory, send
from yokai.ui.table_common import avatar_url, finish_table, reply_error

if TYPE_CHECKING:
    from yokai.bot import YokaiBot

logger = logging.getLogger(__name__)


class CrashView(discord.ui.View):
    """The table message while a round flies: one Cash out button for every seated player."""

    def __init__(self, bot: YokaiBot, lobby: Lobby, crash: CrashRound, started_at: float) -> None:
        super().__init__(timeout=None)
        self.bot = bot
        self.lobby = lobby
        self.crash = crash
        self.started_at = started_at
        self.wake = asyncio.Event()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return not await screen(interaction)

    def rows(self) -> list[tuple[int, int, Optional[int]]]:
        return [
            (user_id, seat.stake, self.crash.cashed_out.get(user_id))
            for user_id, seat in self.lobby.seats.items()
        ]

    @discord.ui.button(label="Cash out", style=discord.ButtonStyle.success)
    async def cash_out_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        # The server's clock at the moment this press is handled decides everything; the
        # number on the message is only a picture that may be a moment behind.
        elapsed = self.bot.tables.clock() - self.started_at
        user_id = interaction.user.id
        try:
            locked = self.crash.cash_out(user_id, elapsed)
        except YokaiError as err:
            await reply_error(self.bot, interaction, err)
            return
        payout = self.lobby.seats[user_id].stake * locked // 100
        embed = EmbedFactory.info(
            title="Cashed out",
            description=(
                f"You locked in **{locked / 100:.2f}x** and will be paid **{payout:,}** Renown "
                "when the round ends."
            ),
            context="Gambling",
            bot_avatar_url=avatar_url(self.bot),
        )
        await send(interaction, embed=embed, ephemeral=True)
        if self.crash.all_cashed_out:
            self.wake.set()


async def _pause(view: CrashView, seconds: float) -> None:
    """Wait up to `seconds`, or until everyone has cashed out. Tests replace this."""
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(view.wake.wait(), timeout=seconds)


async def run_crash(bot: YokaiBot, lobby: Lobby, message: discord.Message) -> None:
    """Run a started crash round. Any failure refunds every stake and tells the players."""
    tables = bot.tables
    crash = CrashRound(
        list(lobby.seats), draw_crash_point(tables.rng_factory(), tables.crash_max_multiplier)
    )
    started_at = tables.clock()
    view = CrashView(bot, lobby, crash, started_at)

    async def play() -> discord.Embed:
        interval = MIN_TICK_S
        pending: Optional[asyncio.Task[None]] = None

        async def refresh() -> None:
            nonlocal interval
            began = time.monotonic()
            elapsed = tables.clock() - started_at
            try:
                await message.edit(
                    embed=EmbedFactory.crash_flying(
                        view.rows(),
                        crash.multiplier_hundredths_at(elapsed),
                        bot_avatar_url=avatar_url(bot),
                    ),
                    view=view,
                )
            except discord.HTTPException as exc:
                logger.warning("Could not update the crash message: %s", exc)
            interval = next_tick_interval(time.monotonic() - began)

        await message.edit(
            embed=EmbedFactory.crash_flying(view.rows(), 100, bot_avatar_url=avatar_url(bot)),
            view=view,
        )
        while True:
            elapsed = tables.clock() - started_at
            if crash.is_over(elapsed):
                break
            if pending is None or pending.done():
                pending = asyncio.create_task(refresh())
            until_crash = max(0.0, crash.crash_time_s - elapsed)
            await _pause(view, min(interval, until_crash))
        view.stop()
        if pending is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await pending
        stakes = {user_id: seat.stake for user_id, seat in lobby.seats.items()}
        payouts = crash.payouts(stakes)
        await tables.settle(lobby, payouts)
        return EmbedFactory.crash_result(
            view.rows(), crash.crash_point, payouts, bot_avatar_url=avatar_url(bot)
        )

    await finish_table(bot, lobby, message, play)
```
Notes for the implementer: the first test's expectation `"10.00x"` is the crash point drawn as `1000`; and `test_a_slow_edit_never_delays_the_crash` relies on the refresh task being awaited only after the loop ends: if the test hangs, the gate in the test is never released because the loop finished first, so release it in the test's `script` (as written) or after the loop with a small `asyncio.wait_for(pending, timeout=...)` guard around the final await.

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_crash_views.py -q`
Expected: all pass. Debug note: `fake_pause` advances `FakeClock` by `seconds`, so every loop iteration moves time forward; a round with crash point 1000 (10.00x) ends after about 33 s of fake time.

- [ ] **Step 5: Suite, lint, commit**

```powershell
python -m pytest -q; python -m ruff check .; python -m ruff format .; python -m ruff format --check .
git add src/yokai/ui/crash_views.py tests/test_crash_views.py
git commit -m "feat(tables): add the crash runner" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>" -m "Claude-Session: https://claude.ai/code/session_01BxUkTAJGP4uNByTtyvj8n9"
```

---

### Task 8: Blackjack table runner

**Files:**
- Create: `src/yokai/ui/bjtable_views.py`
- Create: `tests/test_bjtable_views.py`

**Interfaces:**
- Consumes: `BlackjackTable`, `build_shoe`, `Outcome`, `hand_value` (Task 4 and solo engine), `EmbedFactory.bjtable_hand` (Task 5), `finish_table` (Task 6), `NotYourTurnError`.
- Produces: `BlackjackTableView(bot, lobby, table)` with **Hit** and **Stand** buttons, `acted: asyncio.Event`, `lock: asyncio.Lock`, `deadline_ts`, `reset_deadline()`, `build_embed(finished=False, results=None)`; module function `_await_action(view, seconds) -> bool` (True when a player acted, False on timeout; tests replace it); `run_bjtable(bot, lobby, message) -> None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_bjtable_views.py`:
```python
"""The blackjack table runner: turn order, timeouts, dealer play and settlement."""

from __future__ import annotations

from pathlib import Path
from typing import Awaitable, Callable, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from tests.card_helpers import cards
from tests.moderation_helpers import fake_moderation
from tests.table_helpers import ALL_PLAYABLE, CHANNEL, announce_ok, balance_of, world
from yokai.gambling.lobby import LobbyState
from yokai.ui.bjtable_views import BlackjackTableView, run_bjtable


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
    interaction.response.edit_message = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


async def _running(manager, stakes=(100, 100)):  # type: ignore[no-untyped-def]
    lobby = await manager.open_table(CHANNEL, "bjtable", 1, stakes[0], announce_ok)
    for user_id, stake in enumerate(stakes[1:], start=2):
        await manager.join(lobby, user_id, stake)
    await manager.begin(lobby)
    return lobby


Action = Callable[[BlackjackTableView, int], Awaitable[bool]]


def scripted_wait(actions: list[Optional[Action]]):  # type: ignore[no-untyped-def]
    """Replacement for _await_action: each call runs the next scripted move (None = timeout)."""
    queue = list(actions)

    async def wait(view: BlackjackTableView, seconds: float) -> bool:
        step = queue.pop(0)
        if step is None:
            return False
        who = view.table.current
        assert who is not None
        return await step(view, who)

    return wait


def press(button_name: str) -> Action:
    async def action(view: BlackjackTableView, who: int) -> bool:
        button = getattr(view, button_name)
        await button.callback(make_interaction(who))
        return True

    return action


@pytest.mark.asyncio
async def test_stand_then_timeout_settles_every_hand_against_the_dealer(tmp_path: Path) -> None:
    async with world(tmp_path, specs=ALL_PLAYABLE) as (db, manager, active):
        lobby = await _running(manager)
        message = make_message()
        shoe = cards("10h 9d 7h 8c 9s 10d")  # 18, 18 against a dealer 17
        with (
            patch("yokai.ui.bjtable_views.build_shoe", return_value=shoe),
            patch(
                "yokai.ui.bjtable_views._await_action",
                new=scripted_wait([press("stand_button"), None]),  # player 2 times out
            ),
        ):
            await run_bjtable(make_bot(db, manager), lobby, message)
        assert (await balance_of(db, 1), await balance_of(db, 2)) == (1100, 1100)
        assert lobby.state is LobbyState.SETTLED and await db.get_pot() == 0
        assert not active.is_active(1) and not active.is_active(2)
        final = message.edit.await_args.kwargs
        assert "(17)" in final["embed"].description and final["view"] is None


@pytest.mark.asyncio
async def test_a_bust_loses_the_stake_into_the_pot_and_the_other_player_still_plays(
    tmp_path: Path,
) -> None:
    async with world(tmp_path, specs=ALL_PLAYABLE) as (db, manager, _):
        lobby = await _running(manager)
        message = make_message()
        shoe = cards("10h 9d 7h 6c 9s 10d 10c")  # player 1: 16 then a ten = bust
        with (
            patch("yokai.ui.bjtable_views.build_shoe", return_value=shoe),
            patch(
                "yokai.ui.bjtable_views._await_action",
                new=scripted_wait([press("hit_button"), press("stand_button")]),
            ),
        ):
            await run_bjtable(make_bot(db, manager), lobby, message)
        assert await balance_of(db, 1) == 900
        assert await balance_of(db, 2) == 1100  # 18 beats the dealer's 17
        assert await db.get_pot() == 100


@pytest.mark.asyncio
async def test_a_dealer_natural_settles_at_once_with_no_turns(tmp_path: Path) -> None:
    async with world(tmp_path, specs=ALL_PLAYABLE) as (db, manager, _):
        lobby = await _running(manager)
        message = make_message()
        shoe = cards("10h 9d As 8c 9s Kd")
        wait = AsyncMock(side_effect=AssertionError("nobody should be waited on"))
        with (
            patch("yokai.ui.bjtable_views.build_shoe", return_value=shoe),
            patch("yokai.ui.bjtable_views._await_action", new=wait),
        ):
            await run_bjtable(make_bot(db, manager), lobby, message)
        assert (await balance_of(db, 1), await balance_of(db, 2)) == (900, 900)
        wait.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_natural_is_paid_three_to_two(tmp_path: Path) -> None:
    async with world(tmp_path, specs=ALL_PLAYABLE) as (db, manager, _):
        lobby = await _running(manager, (100, 100))
        message = make_message()
        shoe = cards("Ah 10d 9h Kc 8s 8d")  # player 1 natural, player 2 has 18, dealer 17
        with (
            patch("yokai.ui.bjtable_views.build_shoe", return_value=shoe),
            patch(
                "yokai.ui.bjtable_views._await_action",
                new=scripted_wait([press("stand_button")]),
            ),
        ):
            await run_bjtable(make_bot(db, manager), lobby, message)
        assert await balance_of(db, 1) == 1000 - 100 + 250
        assert await balance_of(db, 2) == 1100


@pytest.mark.asyncio
async def test_only_the_player_whose_turn_it_is_can_act(tmp_path: Path) -> None:
    async with world(tmp_path, specs=ALL_PLAYABLE) as (db, manager, _):
        lobby = await _running(manager)
        from yokai.gambling.engines.bjtable import BlackjackTable

        table = BlackjackTable(cards("10h 9d 7h 8c 9s 10d"), [1, 2])
        view = BlackjackTableView(make_bot(db, manager), lobby, table)
        view.message = make_message()
        interaction = make_interaction(2)  # player 1 is up
        with patch("yokai.ui.table_common.send", new=AsyncMock()) as sent:
            await view.hit_button.callback(interaction)
        assert sent.await_args.kwargs["ephemeral"] is True
        assert "turn" in sent.await_args.kwargs["embed"].description.lower()
        assert len(table.hands[2]) == 2 and not view.acted.is_set()
        interaction.response.edit_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_stranger_cannot_act(tmp_path: Path) -> None:
    async with world(tmp_path, specs=ALL_PLAYABLE) as (db, manager, _):
        lobby = await _running(manager)
        from yokai.gambling.engines.bjtable import BlackjackTable

        table = BlackjackTable(cards("10h 9d 7h 8c 9s 10d"), [1, 2])
        view = BlackjackTableView(make_bot(db, manager), lobby, table)
        with patch("yokai.ui.table_common.send", new=AsyncMock()) as sent:
            await view.stand_button.callback(make_interaction(3))
        assert sent.await_args.kwargs["ephemeral"] is True
        assert table.current == 1


@pytest.mark.asyncio
async def test_a_press_that_races_the_timeout_is_not_applied_twice(tmp_path: Path) -> None:
    """The timeout only stands the player it was waiting on, and only if they are still up."""
    async with world(tmp_path, specs=ALL_PLAYABLE) as (db, manager, _):
        lobby = await _running(manager)
        message = make_message()
        shoe = cards("10h 9d 7h 8c 9s 10d")

        async def stand_then_report_timeout(view: BlackjackTableView, seconds: float) -> bool:
            # player 1 stands just before the runner decides it timed out
            await view.stand_button.callback(make_interaction(1))
            return False

        calls = {"n": 0}

        async def wait(view: BlackjackTableView, seconds: float) -> bool:
            calls["n"] += 1
            if calls["n"] == 1:
                return await stand_then_report_timeout(view, seconds)
            return False  # player 2 times out

        with (
            patch("yokai.ui.bjtable_views.build_shoe", return_value=shoe),
            patch("yokai.ui.bjtable_views._await_action", new=wait),
        ):
            await run_bjtable(make_bot(db, manager), lobby, message)
        # both hands were stood exactly once: 18 against 17 wins for both
        assert (await balance_of(db, 1), await balance_of(db, 2)) == (1100, 1100)


@pytest.mark.asyncio
async def test_a_failure_while_playing_refunds_everyone_and_tells_the_players(
    tmp_path: Path,
) -> None:
    async with world(tmp_path, specs=ALL_PLAYABLE) as (db, manager, active):
        lobby = await _running(manager)
        message = make_message()
        shoe = cards("10h 9d 7h 8c 9s 10d")
        failing = AsyncMock(side_effect=RuntimeError("disk"))
        with (
            patch("yokai.ui.bjtable_views.build_shoe", return_value=shoe),
            patch(
                "yokai.ui.bjtable_views._await_action",
                new=scripted_wait([press("stand_button"), press("stand_button")]),
            ),
            patch.object(db, "settle_lobby", new=failing),
        ):
            with pytest.raises(RuntimeError):
                await run_bjtable(make_bot(db, manager), lobby, message)
        assert lobby.state is LobbyState.CANCELLED
        assert (await balance_of(db, 1), await balance_of(db, 2)) == (1000, 1000)
        assert not active.is_active(1)
        assert "cancelled" in message.edit.await_args.kwargs["embed"].title.lower()
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_bjtable_views.py -q`
Expected: collection ERROR `No module named 'yokai.ui.bjtable_views'`.

- [ ] **Step 3: Implement**

Create `src/yokai/ui/bjtable_views.py`:
```python
"""Blackjack table: one shared dealer, players act in join order with a timer per decision."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Mapping, Optional

import discord

from yokai.errors import YokaiError
from yokai.gambling.engines.blackjack import Outcome, build_shoe, hand_value
from yokai.gambling.engines.bjtable import BlackjackTable
from yokai.gambling.lobby import Lobby
from yokai.moderation.gate import screen
from yokai.ui.embeds import EmbedFactory
from yokai.ui.table_common import avatar_url, finish_table, reply_error

if TYPE_CHECKING:
    from yokai.bot import YokaiBot

logger = logging.getLogger(__name__)

_STATUS_TEXT = {
    "blackjack": "blackjack",
    "bust": "bust",
    "21": "21",
    "stand": "stands",
}


class BlackjackTableView(discord.ui.View):
    """Hit / Stand for whoever is up; everyone else is told it is not their turn."""

    def __init__(self, bot: YokaiBot, lobby: Lobby, table: BlackjackTable) -> None:
        super().__init__(timeout=None)
        self.bot = bot
        self.lobby = lobby
        self.table = table
        self.message: Optional[discord.Message] = None
        self.acted = asyncio.Event()
        self.lock = asyncio.Lock()
        self.deadline_ts = 0
        self.reset_deadline()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return not await screen(interaction)

    def reset_deadline(self) -> None:
        self.deadline_ts = int(self.bot.tables.clock() + self.bot.tables.pick_seconds)

    def build_embed(self, results: Optional[Mapping[int, str]] = None) -> discord.Embed:
        """The table so far; with `results` (per-player text) it is the final embed."""
        table = self.table
        finished = results is not None
        seats = []
        for user_id in table.order:
            seat = self.lobby.seats[user_id]
            if results is not None:
                status = results[user_id]
            elif user_id == table.current:
                status = "to act"
            else:
                state = table.status_of(user_id)
                status = _STATUS_TEXT.get(state, "waiting")
            hand = table.hands[user_id]
            seats.append((user_id, seat.stake, [str(c) for c in hand], hand_value(hand)[0], status))
        dealer_total = hand_value(table.dealer)[0] if finished else None
        dealer_cards = (
            [str(c) for c in table.dealer]
            if finished
            else [str(table.dealer[0]), "??"]
        )
        return EmbedFactory.bjtable_hand(
            seats,
            dealer_cards,
            dealer_total,
            None if finished else self.deadline_ts,
            finished,
            bot_avatar_url=avatar_url(self.bot),
        )

    async def _act(self, interaction: discord.Interaction, move: str) -> None:
        async with self.lock:
            try:
                getattr(self.table, move)(interaction.user.id)
            except YokaiError as err:
                await reply_error(self.bot, interaction, err)
                return
            self.reset_deadline()
            self.acted.set()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Hit", style=discord.ButtonStyle.primary)
    async def hit_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._act(interaction, "hit")

    @discord.ui.button(label="Stand", style=discord.ButtonStyle.secondary)
    async def stand_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._act(interaction, "stand")


async def _await_action(view: BlackjackTableView, seconds: float) -> bool:
    """Wait for a player to act; False if `seconds` pass first. Tests replace this."""
    try:
        await asyncio.wait_for(view.acted.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        return False
    return True


def _result_text(outcome: Outcome, stake: int, payout: int) -> str:
    net = payout - stake
    label = {
        Outcome.BLACKJACK: "blackjack",
        Outcome.WIN: "win",
        Outcome.PUSH: "push",
        Outcome.LOSE: "lose",
    }[outcome]
    sign = f"+{net:,}" if net > 0 else f"{net:,}"
    return f"{label}: {sign}"


async def run_bjtable(bot: YokaiBot, lobby: Lobby, message: discord.Message) -> None:
    """Run a started table. Any failure refunds every stake and tells the players."""
    tables = bot.tables
    table = BlackjackTable(build_shoe(tables.rng_factory()), list(lobby.seats))
    view = BlackjackTableView(bot, lobby, table)
    view.message = message

    async def play() -> discord.Embed:
        await message.edit(embed=view.build_embed(), view=view)
        while True:
            who = table.current
            if who is None:
                break
            view.acted.clear()
            if await _await_action(view, tables.pick_seconds):
                continue  # someone acted; the button handler already updated the message
            async with view.lock:
                if table.current == who:  # still their turn: they ran out of time
                    table.stand(who)
                    view.reset_deadline()
                    with contextlib.suppress(discord.HTTPException):
                        await message.edit(embed=view.build_embed(), view=view)
        view.stop()
        table.play_dealer()
        stakes = {user_id: seat.stake for user_id, seat in lobby.seats.items()}
        payouts = table.payouts(stakes)
        await tables.settle(lobby, payouts)
        results = {
            user_id: _result_text(outcome, stakes[user_id], payouts[user_id])
            for user_id, outcome in table.outcomes().items()
        }
        return view.build_embed(results)

    await finish_table(bot, lobby, message, play)
```
Note: the timeout path edits the message itself because no interaction is available; the button path answers the interaction by editing the message (so no second edit is needed). `view.build_embed` for a finished table shows the dealer's full hand and each player's result text.

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_bjtable_views.py -q`
Expected: all pass. If a stacked-shoe expectation is off, check it against the deal order (P1, P2, Dealer, P1, P2, Dealer, then draws) and fix the card string.

- [ ] **Step 5: Suite, lint, commit**

```powershell
python -m pytest -q; python -m ruff check .; python -m ruff format .; python -m ruff format --check .
git add src/yokai/ui/bjtable_views.py tests/test_bjtable_views.py
git commit -m "feat(tables): add the blackjack table runner" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>" -m "Claude-Session: https://claude.ai/code/session_01BxUkTAJGP4uNByTtyvj8n9"
```

---

### Task 9: Switch the games on, guard a missing runner, document

**Files:**
- Modify: `src/yokai/gambling/lobby.py` (flip `available`), `src/yokai/ui/table_views.py` (register runners, guard)
- Modify: `tests/test_lobby.py`, `tests/test_table_manager.py`, `tests/test_tables_cog.py`, `tests/test_table_views.py`, `tests/test_bot_wiring.py`
- Modify: `README.md`, `TASKS.md`

**Interfaces:**
- Consumes: `run_jackpot`, `run_crash`, `run_bjtable` (Tasks 6 to 8).
- Produces: `GAME_RUNNERS` containing `duel`, `jackpot`, `crash`, `bjtable`; `LobbyView._after_begin` aborts, refunds and tells the players when an available game has no runner.

- [ ] **Step 1: Write the failing tests**

In `tests/test_lobby.py` replace `test_every_planned_game_is_listed_but_only_the_duel_is_playable_yet` with:
```python
def test_every_planned_game_is_listed_and_only_the_lottery_is_not_playable_yet() -> None:
    assert list(GAME_SPECS) == ["duel", "jackpot", "crash", "bjtable", "lottery"]
    playable = [name for name, spec in GAME_SPECS.items() if spec.available]
    assert playable == ["duel", "jackpot", "crash", "bjtable"]
    assert all(spec.label and spec.rules for spec in GAME_SPECS.values())
    assert GAME_SPECS["crash"].window_factor == 0.5 and GAME_SPECS["bjtable"].window_factor == 0.5
    assert (GAME_SPECS["jackpot"].min_players, GAME_SPECS["jackpot"].max_players) == (2, 10)
    assert (GAME_SPECS["crash"].min_players, GAME_SPECS["crash"].max_players) == (1, 10)
    assert (GAME_SPECS["bjtable"].min_players, GAME_SPECS["bjtable"].max_players) == (2, 5)
```
In `tests/test_table_manager.py` change `test_a_game_that_is_not_built_yet_cannot_open_a_table_and_costs_nothing` to loop over only `("lottery",)`. In `tests/test_tables_cog.py` change `test_a_game_that_is_not_built_yet_cannot_be_dedicated` to loop over only `("lottery",)`. Add to `tests/test_table_views.py`:
```python
def test_every_playable_game_has_a_runner() -> None:
    from yokai.gambling.lobby import GAME_SPECS

    missing = [name for name, spec in GAME_SPECS.items() if spec.available and name not in GAME_RUNNERS]
    assert missing == []


@pytest.mark.asyncio
async def test_a_missing_runner_cancels_the_table_and_refunds_instead_of_stranding_stakes(
    tmp_path: Path,
) -> None:
    async with world(tmp_path) as (db, manager, active):
        lobby = await manager.open_table(CHANNEL, "duel", 1, 100, announce_ok)
        await manager.join(lobby, 2, None)
        view = make_view(make_bot(db, manager), lobby)
        with patch.dict(GAME_RUNNERS, {}, clear=True):
            await view._after_begin(await manager.begin(lobby))
        assert lobby.state is LobbyState.CANCELLED
        assert (await balance_of(db, 1), await balance_of(db, 2)) == (1000, 1000)
        assert not active.is_active(1)
        assert "cancelled" in view.message.edit.await_args.kwargs["embed"].title.lower()
```
Add to `tests/test_bot_wiring.py`:
```python
@pytest.mark.asyncio
async def test_the_table_manager_gets_the_jackpot_and_crash_settings() -> None:
    bot = make_bot()
    assert bot.tables.jackpot_rake_percent == bot.config.gambling_jackpot_rake_percent
    assert bot.tables.crash_max_multiplier == bot.config.gambling_crash_max_multiplier
```
Add a full-flow check to `tests/test_table_views.py` that joining a jackpot fills and starts through the real registry:
```python
@pytest.mark.asyncio
async def test_jackpot_crash_and_blackjack_tables_start_through_the_registry(
    tmp_path: Path,
) -> None:
    from tests.table_helpers import ALL_PLAYABLE

    for game in ("jackpot", "crash", "bjtable"):
        async with world(tmp_path / game if False else tmp_path, specs=ALL_PLAYABLE) as (
            db,
            manager,
            _,
        ):
            lobby = await manager.open_table(CHANNEL, game, 1, 100, announce_ok)
            await manager.join(lobby, 2, 100)
            view = make_view(make_bot(db, manager), lobby)
            runner = AsyncMock()
            with patch.dict(GAME_RUNNERS, {game: runner}):
                await view.start_button.callback(make_interaction(1))
            runner.assert_awaited_once()
            assert lobby.state is LobbyState.RUNNING
        (tmp_path / "tables.db").unlink(missing_ok=True)
```
(If the repeated `world(...)` in one test is awkward on Windows because the database file is reused, split it into three parametrized tests instead; each gets its own `tmp_path`.)

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_lobby.py tests/test_table_views.py tests/test_bot_wiring.py -q`
Expected: failures: the three games are still unavailable, `GAME_RUNNERS` has only the duel, and a missing runner raises `KeyError`.

- [ ] **Step 3: Implement**

`src/yokai/gambling/lobby.py`: delete the comment `# Listed in /admin set_multiplayer already, but refused until their phase (M3, M4) ships.`, and for `jackpot`, `crash` and `bjtable` remove the line `available=False,` (keep it on `lottery`, and add above the lottery entry: `# Listed already; playable once the lottery (phase M4) ships.`).

`src/yokai/ui/table_views.py`: add imports `from yokai.ui.bjtable_views import run_bjtable`, `from yokai.ui.crash_views import run_crash`, `from yokai.ui.jackpot_views import run_jackpot` and `from yokai.ui.table_common import tell_cancelled` (merge into the existing `table_common` import), set
```python
GAME_RUNNERS: dict[str, GameRunner] = {
    "duel": run_duel,
    "jackpot": run_jackpot,
    "crash": run_crash,
    "bjtable": run_bjtable,
}
```
and replace the last two lines of `LobbyView._after_begin`:
```python
        assert self.message is not None
        await GAME_RUNNERS[self.lobby.spec.name](self.bot, self.lobby, self.message)
```
with:
```python
        assert self.message is not None
        runner = GAME_RUNNERS.get(self.lobby.spec.name)
        if runner is None:
            # A playable game without a runner is a bug; never leave the stakes escrowed.
            logger.error("No runner is registered for %s", self.lobby.spec.name)
            await self.bot.tables.abort(self.lobby)
            await tell_cancelled(self.bot, self.message)
            return
        await runner(self.bot, self.lobby, self.message)
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Docs**

`README.md` (line endings are CRLF; edit bytes-safely): change the multiplayer sentence so it reads that Duel, Jackpot, Crash and the Blackjack table are playable and the Lottery is coming; update the `/admin set_multiplayer` bullet to "Every planned game is listed; all but the Lottery are playable."

`TASKS.md`: under Phase 10 after the M2 block add:
```markdown
### M3 — Jackpot, crash and blackjack table

Plan: `docs/superpowers/plans/2026-10-08-multiplayer-m3-jackpot-crash-blackjack-table.md`.

- [x] M3.1 Jackpot and crash settings, `NotYourTurnError`.
- [x] M3.2 Jackpot engine and `resolve_jackpot`.
- [x] M3.3 Crash engine.
- [x] M3.4 Blackjack table engine.
- [x] M3.5 Jackpot, crash and blackjack table embeds.
- [x] M3.6 Shared failure guard and the jackpot runner.
- [x] M3.7 Crash runner.
- [x] M3.8 Blackjack table runner.
- [x] M3.9 Games switched on; missing-runner guard.

**Acceptance criteria (M3)**
- `/admin set_multiplayer` accepts Jackpot, Crash and Blackjack table; the Lottery is still refused.
- Jackpot: 2 to 10 players each pick their own stake; the winner is drawn by stake and takes the
  pot minus the 2% rake; one entrant is refunded.
- Crash: 1 to 10 players; one message ticks about once a second; **Cash out** locks in the
  server's multiplier at the moment of the press; a press after the crash loses; cashed-out
  players are paid stake times multiplier (rounded down); a lost stake feeds the lottery pot.
- Blackjack table: 2 to 5 players act in join order; only the player up can Hit or Stand; a
  decision times out after 45 s into a stand; the dealer plays once; naturals pay 3:2; each hand
  settles on its own in one transaction.
- Any failure in any game refunds every stake and tells the players the table was cancelled.
- `pytest`, `ruff check` and `ruff format --check` are clean.
- *(manual, real Discord)* Play each game with two or three accounts: a jackpot with different
  stakes, a crash round where one account cashes out and one does not (and one where a press
  lands just after the crash), a blackjack table with a hit, a stand and a timeout, and a table
  with a dealer natural; confirm balances and `/gamble-stats`, watch the crash message for edit
  errors in `logs`, and kill the bot mid-round to confirm the stakes come back.

**STOP — owner review.**

(M4 lottery: planned after M3 is reviewed.)
```
and delete the earlier line `(M3 jackpot, crash and blackjack table, M4 lottery: planned after M2 is reviewed.)`.

- [ ] **Step 6: Final verification and commit**

```powershell
python -m pytest -q; python -m ruff check .; python -m ruff format .; python -m ruff format --check .
git add src tests README.md TASKS.md
git commit -m "feat(tables): switch on jackpot, crash and the blackjack table" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>" -m "Claude-Session: https://claude.ai/code/session_01BxUkTAJGP4uNByTtyvj8n9"
```
Expected: suite and lint clean.

**STOP. Report what changed, list the unverified manual Discord checks, and wait for owner review before planning M4.**
