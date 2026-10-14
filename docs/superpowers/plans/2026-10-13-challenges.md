# Daily and Weekly Challenges Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Daily and weekly challenges ("listen to 5 tracks in a call", "play 3 rounds") that pay Renown automatically and push people toward the music bot and the gamemodes.

**Architecture:** Existing music, game and voice code emit small typed events to a failure-isolated `ChallengeEngine` (`bot.challenges.emit(event)`). The engine matches events against the stored daily and weekly slates, advances per-user progress in SQLite, and pays the reward in the same transaction that marks completion. A small scheduler rolls periods over and flushes batched completion notices. `/challenges` shows progress; `/admin challenges` lets an admin list, reroll or set a slate.

**Tech Stack:** Python 3.12+, discord.py app commands, aiosqlite (migration v8), pytest + pytest-asyncio, ruff. `zoneinfo` for `CHALLENGE_TZ` (named zones need `tzdata` on Windows; see Open decision).

**Spec:** `docs/superpowers/specs/2026-10-13-challenges-design.md`

## Open decision (owner answers at plan review)

`ZoneInfo("UTC")` and every named zone fail on this machine: `tzdata` is not installed (Windows ships no IANA database). AGENTS.md rule 5 requires asking before adding a dependency.
- **Plan as written:** `"UTC"` is handled with `datetime.timezone.utc`, so the default needs no dependency. Task 1 adds `tzdata` to `pyproject.toml` so `CHALLENGE_TZ=America/New_York` works.
- **If the owner declines `tzdata`:** skip Task 1 Step 9 and the `tzdata` line in Step 10. Named zones then fail at startup with a clear `ConfigError`; the named-zone tests skip themselves.

## Spec refinements made by this plan (Task 10 syncs the spec)

- `challenge_progress` gains `updated_ts INTEGER NOT NULL` (needed for purging).
- Weekly slate is exactly 3 challenges (one per category), inside the spec's "2 or 3".
- `tracks_listened` is a distinct-count metric (distinct `video_id` per user per period), so replaying a looped track cannot farm progress.
- `/admin challenges` is `list | reroll | set` (no `disable`; `CHALLENGE_ENABLED` is the off switch) and uses the existing admin check (owner or adminlist), like the other `/admin` commands.
- Game completions have no notice channel in v1 (no channel is available where bets settle); they appear in `/challenges`. Music completions notify in the music text channel.
- A poker seat counts as one round when its bet closes (cash-out), with the buy-in as the stake.

## Global Constraints

- Python 3.12+, fully type-hinted, `from __future__ import annotations`, small modules, dataclasses for models.
- Never block the event loop; no `print` (use `logging`); no bare `except`; line length 100.
- Every user-visible message goes through `ui/embeds.py` (`EmbedFactory`); error embeds are ephemeral; no raw `send_message("text")` / `channel.send("text")` (`tests/test_code_standards.py` enforces it).
- Default `allowed_mentions` is none; mention users only inside embeds.
- Buttons and views time out, disable themselves and act only for the invoking user (`BaseView`).
- Defer within 3 seconds for anything that touches the network or database; validate input before deferring so validation errors stay ephemeral.
- Slash commands only, guild-scoped, no privileged intents.
- Configuration comes from environment variables loaded in `config.py`, and every variable is documented in `.env.example`.
- No new dependency except as settled in "Open decision"; no Spotify-derived data in progress tracking.
- Ledger reason for a payout is exactly `challenge:<template_id>`.
- Reset boundaries: midnight in `CHALLENGE_TZ` (default `UTC`) for daily, Monday midnight for weekly. Period keys: `2026-10-13` (daily), `2026-W42` (weekly, ISO week).
- Music credit: a track counts when `completed`, or `skipped` after at least 50% listened, only with 2 or more humans in the call (AFK channel excluded), once per user per `video_id` per period.
- Game credit: rounds played and won, only when stake is at least `CHALLENGE_MIN_WAGER` (default 10); a win is `payout > stake`.
- Commit per task, never commit `.env`, databases or cookie files. Every commit message ends with the trailer `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- Verification before marking any task done: `python -m pytest -q`, `python -m ruff check .`, `python -m ruff format --check .` (run from the repo root in PowerShell, venv active).

## Review Focus

Failure modes the spec implies but a happy-path test would not catch, most likely first. Each has a test in the task named.

1. **Bot restarts mid-period.** The stored slate must survive; a new engine on the same database must return the same challenges, not a re-roll (Task 4).
2. **Repeat farming.** A looped track (`LoopMode.TRACK`) or the same artist replayed must not advance distinct-count progress twice (Task 4).
3. **Blackjack double-down.** Two bets sharing one `group_id` are one round, and one win (Task 4).
4. **Garbage input to `/admin challenges set`.** Empty string, 50 ids, unknown or wrong-period ids, duplicates and unicode produce a friendly ephemeral error and change nothing (Tasks 4 and 9).
5. **Users who should earn nothing or whose wallet does not exist yet.** Blacklisted users earn no progress; a brand-new user who completes a challenge gets the welcome bonus plus the reward exactly once (Tasks 3 and 4).

## File Structure

| File | Responsibility |
|---|---|
| `src/yokai/challenges/__init__.py` | Empty package marker (no imports, so `config.py` can import `periods` without cycles). |
| `src/yokai/challenges/periods.py` | Pure: `load_tz`, `period_key`, `next_reset_ts`. |
| `src/yokai/challenges/models.py` | Dataclasses `Template`, `Advance`, `ProgressView`, `Completion`; events `TrackListened`, `RoundPlayed`, `VoiceMinutes`; pure `counts_as_listened`. |
| `src/yokai/challenges/templates.py` | The pool as pure data, `TEMPLATES_BY_ID`. |
| `src/yokai/challenges/slate.py` | Pure `pick_slate`. |
| `src/yokai/challenges/engine.py` | `ChallengeEngine`: slates, effects, progress, payout, notices queue, override. |
| `src/yokai/challenges/scheduler.py` | `ChallengeScheduler`: rollover, purge, batched notice flushing. |
| `src/yokai/cogs/challenges.py` | `/challenges` command. |
| `src/yokai/ui/challenge_views.py` | `ChallengesView` (Daily / Weekly tabs). |
| `src/yokai/storage/db.py` | Migration v8, challenge methods, bet-settlement listener. |
| `src/yokai/economy/models.py` | `BetSettlement` dataclass. |
| `src/yokai/config.py` | Four `CHALLENGE_*` settings. |
| `src/yokai/ui/embeds.py` | `challenge_board`, `challenge_notice`. |
| `src/yokai/music/player.py`, `src/yokai/economy/voice_tracker.py`, `src/yokai/bot.py` | One-line emits and wiring. |
| `tests/challenge_helpers.py` | Shared test pool, clock and engine factory. |

---

### Task 1: Configuration and period math

**Files:**
- Create: `src/yokai/challenges/__init__.py`, `src/yokai/challenges/periods.py`
- Modify: `src/yokai/config.py`, `.env.example`, `pyproject.toml` (see Open decision)
- Test: `tests/test_challenge_periods.py`, `tests/test_challenge_config.py`

**Interfaces:**
- Produces: `periods.DAILY = "daily"`, `periods.WEEKLY = "weekly"`, `periods.PERIODS = ("daily", "weekly")`; `periods.load_tz(name: str) -> tzinfo` (raises `ValueError`); `periods.period_key(period: str, now_ts: int, tz: tzinfo) -> str`; `periods.next_reset_ts(period: str, now_ts: int, tz: tzinfo) -> int`; `Config.challenge_enabled: bool`, `challenge_tz: str`, `challenge_min_wager: int`, `challenge_reward_scale: float`.

- [ ] **Step 1: Pre-flight: checkpoint tag**

Run: `git status --short` (expect clean) then `git tag -a checkpoint/challenges-start -m "Before challenges"`.

- [ ] **Step 2: Write the failing period tests**

Create `tests/test_challenge_periods.py`:

```python
"""Period keys and reset times for daily and weekly challenges."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from yokai.challenges import periods

UTC = timezone.utc


def ts(*args: int) -> int:
    return int(datetime(*args, tzinfo=UTC).timestamp())


def test_load_tz_utc_needs_no_tzdata() -> None:
    assert periods.load_tz("UTC") is UTC
    assert periods.load_tz(" utc ") is UTC


def test_load_tz_rejects_unknown_zone_with_a_helpful_message() -> None:
    with pytest.raises(ValueError, match="CHALLENGE_TZ"):
        periods.load_tz("Not/AZone")


def test_daily_key_changes_exactly_at_midnight() -> None:
    assert periods.period_key("daily", ts(2026, 10, 13, 23, 59, 59), UTC) == "2026-10-13"
    assert periods.period_key("daily", ts(2026, 10, 14, 0, 0, 0), UTC) == "2026-10-14"


def test_weekly_key_is_the_iso_week_and_rolls_on_monday() -> None:
    assert periods.period_key("weekly", ts(2026, 10, 13, 12), UTC) == "2026-W42"
    assert periods.period_key("weekly", ts(2026, 10, 18, 23, 59, 59), UTC) == "2026-W42"
    assert periods.period_key("weekly", ts(2026, 10, 19, 0, 0, 0), UTC) == "2026-W43"


def test_weekly_key_across_the_iso_year_boundary() -> None:
    assert periods.period_key("weekly", ts(2027, 1, 1, 12), UTC) == "2026-W53"
    assert periods.period_key("weekly", ts(2027, 1, 4, 12), UTC) == "2027-W01"


def test_unknown_period_is_rejected() -> None:
    with pytest.raises(ValueError):
        periods.period_key("monthly", 0, UTC)
    with pytest.raises(ValueError):
        periods.next_reset_ts("monthly", 0, UTC)


def test_next_daily_reset_is_the_coming_midnight() -> None:
    assert periods.next_reset_ts("daily", ts(2026, 10, 13, 12), UTC) == ts(2026, 10, 14)


def test_next_weekly_reset_is_the_coming_monday_even_on_a_monday() -> None:
    assert periods.next_reset_ts("weekly", ts(2026, 10, 13, 12), UTC) == ts(2026, 10, 19)
    assert periods.next_reset_ts("weekly", ts(2026, 10, 19, 0, 0, 0), UTC) == ts(2026, 10, 26)


def test_named_zone_reset_follows_local_midnight_across_dst() -> None:
    pytest.importorskip("tzdata")
    new_york = periods.load_tz("America/New_York")
    now = int(datetime(2026, 3, 8, 0, 30, tzinfo=new_york).timestamp())  # day DST starts
    reset = periods.next_reset_ts("daily", now, new_york)
    assert datetime.fromtimestamp(reset, new_york) == datetime(2026, 3, 9, 0, 0, tzinfo=new_york)
    assert periods.period_key("daily", now, new_york) == "2026-03-08"
```

- [ ] **Step 3: Run to verify it fails**

Run: `python -m pytest tests/test_challenge_periods.py -q`
Expected: FAIL (`ModuleNotFoundError: yokai.challenges`).

- [ ] **Step 4: Implement `periods.py`**

Create empty `src/yokai/challenges/__init__.py` (a docstring only: `"""Daily and weekly challenges."""`). Create `src/yokai/challenges/periods.py`:

```python
"""Pure period math for challenges: which day or ISO week it is, and when it resets."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo
from datetime import time as dtime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DAILY = "daily"
WEEKLY = "weekly"
PERIODS = (DAILY, WEEKLY)


def load_tz(name: str) -> tzinfo:
    """Resolve CHALLENGE_TZ. UTC never needs the tz database; named zones do on Windows."""
    cleaned = name.strip()
    if cleaned.upper() == "UTC":
        return timezone.utc
    try:
        return ZoneInfo(cleaned)
    except (ZoneInfoNotFoundError, ValueError, OSError) as err:
        raise ValueError(
            f"CHALLENGE_TZ {name!r} is not a known time zone. On Windows, install the "
            "'tzdata' package for named zones, or set CHALLENGE_TZ=UTC."
        ) from err


def period_key(period: str, now_ts: int, tz: tzinfo) -> str:
    """'2026-10-13' for daily, '2026-W42' (ISO week) for weekly, in the given zone."""
    local = datetime.fromtimestamp(now_ts, tz)
    if period == DAILY:
        return local.date().isoformat()
    if period == WEEKLY:
        iso = local.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    raise ValueError(f"Unknown challenge period: {period!r}")


def next_reset_ts(period: str, now_ts: int, tz: tzinfo) -> int:
    """Unix time of the next local midnight (daily) or Monday midnight (weekly)."""
    local = datetime.fromtimestamp(now_ts, tz)
    if period == DAILY:
        days = 1
    elif period == WEEKLY:
        days = 7 - local.weekday()
    else:
        raise ValueError(f"Unknown challenge period: {period!r}")
    target = datetime.combine(local.date() + timedelta(days=days), dtime.min, tzinfo=tz)
    return int(target.timestamp())
```

- [ ] **Step 5: Run to verify it passes**

Run: `python -m pytest tests/test_challenge_periods.py -q`
Expected: PASS (the named-zone test passes or skips depending on `tzdata`).

- [ ] **Step 6: Write the failing config tests**

Read `tests/test_gambling_config.py` first and reuse its helper for loading a `Config` from environment variables. Create `tests/test_challenge_config.py` with the same helper style and these tests:

```python
"""CHALLENGE_* settings: defaults, clamping and bad values."""

from __future__ import annotations

from pathlib import Path

import pytest

from yokai.config import ConfigError, load_config


def _load(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **env: str):
    for key in (
        "CHALLENGE_ENABLED",
        "CHALLENGE_TZ",
        "CHALLENGE_MIN_WAGER",
        "CHALLENGE_REWARD_SCALE",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DISCORD_TOKEN", "x")
    monkeypatch.setenv("GUILD_ID", "1")
    monkeypatch.setenv("OWNER_ID", "2")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return load_config(env_path=tmp_path / "missing.env")


def test_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = _load(monkeypatch, tmp_path)
    assert config.challenge_enabled is True
    assert config.challenge_tz == "UTC"
    assert config.challenge_min_wager == 10
    assert config.challenge_reward_scale == 1.0


def test_values_are_read(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = _load(
        monkeypatch,
        tmp_path,
        CHALLENGE_ENABLED="false",
        CHALLENGE_MIN_WAGER="25",
        CHALLENGE_REWARD_SCALE="1.5",
    )
    assert config.challenge_enabled is False
    assert config.challenge_min_wager == 25
    assert config.challenge_reward_scale == 1.5


def test_scale_and_wager_are_clamped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = _load(
        monkeypatch, tmp_path, CHALLENGE_REWARD_SCALE="999", CHALLENGE_MIN_WAGER="-5"
    )
    assert config.challenge_reward_scale == 10.0
    assert config.challenge_min_wager == 1
    low = _load(monkeypatch, tmp_path, CHALLENGE_REWARD_SCALE="0")
    assert low.challenge_reward_scale == 0.1


@pytest.mark.parametrize("bad", ["abc", "nan", "inf"])
def test_bad_scale_is_a_config_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, bad: str
) -> None:
    with pytest.raises(ConfigError, match="CHALLENGE_REWARD_SCALE"):
        _load(monkeypatch, tmp_path, CHALLENGE_REWARD_SCALE=bad)


def test_unknown_timezone_is_a_config_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with pytest.raises(ConfigError, match="CHALLENGE_TZ"):
        _load(monkeypatch, tmp_path, CHALLENGE_TZ="Not/AZone")
```

- [ ] **Step 7: Run to verify it fails**

Run: `python -m pytest tests/test_challenge_config.py -q`
Expected: FAIL (`AttributeError: 'Config' object has no attribute 'challenge_enabled'`).

- [ ] **Step 8: Implement the settings**

In `src/yokai/config.py`:

1. Add `import math` at the top and `from yokai.challenges.periods import load_tz`.
2. After the last field of the `Config` dataclass (the lottery fields), add:

```python
    # Challenges
    challenge_enabled: bool = True
    challenge_tz: str = "UTC"
    challenge_min_wager: int = 10
    challenge_reward_scale: float = 1.0
```

3. Below `_parse_int`, add:

```python
def _parse_float(name: str, value: str | None, default: float) -> float:
    if value is None or not value.strip():
        return default
    try:
        parsed = float(value.strip())
    except ValueError as err:
        raise ConfigError(f"Invalid number for {name}: {value!r}") from err
    if not math.isfinite(parsed):
        raise ConfigError(f"Invalid number for {name}: {value!r}")
    return parsed
```

4. In `load_config`, before `return Config(`, add:

```python
    challenge_enabled = _parse_bool(os.getenv("CHALLENGE_ENABLED"), default=True)
    challenge_tz = os.getenv("CHALLENGE_TZ", "UTC").strip() or "UTC"
    try:
        load_tz(challenge_tz)
    except ValueError as err:
        raise ConfigError(str(err)) from err
    challenge_min_wager = max(
        1, _parse_int("CHALLENGE_MIN_WAGER", os.getenv("CHALLENGE_MIN_WAGER"), 10)
    )
    challenge_reward_scale = min(
        10.0,
        max(0.1, _parse_float("CHALLENGE_REWARD_SCALE", os.getenv("CHALLENGE_REWARD_SCALE"), 1.0)),
    )
```

5. Pass the four values into the `Config(...)` call (`challenge_enabled=challenge_enabled`, and so on).

- [ ] **Step 9: Document the variables (and add `tzdata` if approved)**

Append to `.env.example` (match the file's existing comment style):

```
# Challenges (daily and weekly)
CHALLENGE_ENABLED=true
# IANA zone for daily/weekly resets, e.g. America/New_York. UTC needs nothing extra;
# named zones need the 'tzdata' package on Windows (installed with the project).
CHALLENGE_TZ=UTC
# A game round only counts toward challenges when the stake is at least this much Renown.
CHALLENGE_MIN_WAGER=10
# Multiplier on every challenge reward (0.1 to 10).
CHALLENGE_REWARD_SCALE=1.0
```

If the owner approved `tzdata`: add `"tzdata",` to `dependencies` in `pyproject.toml` and run `pip install -e ".[dev]"`.

- [ ] **Step 10: Verify and commit**

Run: `python -m pytest tests/test_challenge_periods.py tests/test_challenge_config.py -q` (expect PASS), then `python -m ruff format src tests` and `python -m ruff check .`.

```powershell
git add src/yokai/challenges src/yokai/config.py .env.example pyproject.toml tests/test_challenge_periods.py tests/test_challenge_config.py
git commit -m "feat(challenges): add period math and CHALLENGE_* settings" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Models, template pool, slate picker

**Files:**
- Create: `src/yokai/challenges/models.py`, `src/yokai/challenges/templates.py`, `src/yokai/challenges/slate.py`
- Test: `tests/test_challenge_templates.py`

**Interfaces:**
- Consumes: `periods.DAILY`, `periods.WEEKLY`.
- Produces:
  - `Template(id, period, category, metric, target, reward, title, game=None)` frozen dataclass. `category` in `CATEGORIES = ("music", "games", "voice")`. `metric` in `COUNTER_METRICS = {"rounds_played", "rounds_won", "voice_minutes"}` or `DISTINCT_METRICS = {"tracks", "artists", "games"}`.
  - `Advance(progress: int, completed_now: bool)`.
  - `ProgressView(template: Template, progress: int, completed: bool, reward: int)`.
  - `Completion(user_id: int, template_id: str, title: str, reward: int, channel_id: Optional[int])`.
  - Events: `TrackListened(user_ids: tuple[int, ...], video_id: str, artist: Optional[str], channel_id: Optional[int] = None)`, `RoundPlayed(user_id: int, game: str, stake: int, payout: int, round_key: str)`, `VoiceMinutes(user_id: int, minutes: int)`; `Event = Union[...]`.
  - `counts_as_listened(outcome: str, listened_s: int, duration_s: int) -> bool`.
  - `POOL: tuple[Template, ...]`, `TEMPLATES_BY_ID: dict[str, Template]`.
  - `pick_slate(period: str, period_key: str, pool: Sequence[Template], salt: str = "") -> list[str]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_challenge_templates.py`:

```python
"""The template pool, the slate picker and the listened rule."""

from __future__ import annotations

import itertools

from yokai.challenges.models import (
    CATEGORIES,
    COUNTER_METRICS,
    DISTINCT_METRICS,
    counts_as_listened,
)
from yokai.challenges.slate import pick_slate
from yokai.challenges.templates import POOL, TEMPLATES_BY_ID


def test_pool_ids_are_unique_and_indexed() -> None:
    assert len({t.id for t in POOL}) == len(POOL)
    assert set(TEMPLATES_BY_ID) == {t.id for t in POOL}


def test_every_period_and_category_has_a_template() -> None:
    for period, category in itertools.product(("daily", "weekly"), CATEGORIES):
        assert any(t.period == period and t.category == category for t in POOL)


def test_template_fields_are_sane() -> None:
    for t in POOL:
        assert t.target > 0 and t.reward > 0 and t.title
        assert t.metric in COUNTER_METRICS | DISTINCT_METRICS
        if t.game is not None:
            assert t.metric in {"rounds_played", "rounds_won"}


def test_every_possible_daily_slate_is_worth_about_one_daily() -> None:
    by_category = [
        [t.reward for t in POOL if t.period == "daily" and t.category == c] for c in CATEGORIES
    ]
    for combo in itertools.product(*by_category):
        assert 80 <= sum(combo) <= 140


def test_every_possible_weekly_slate_is_worth_three_or_four_dailies() -> None:
    by_category = [
        [t.reward for t in POOL if t.period == "weekly" and t.category == c] for c in CATEGORIES
    ]
    for combo in itertools.product(*by_category):
        assert 300 <= sum(combo) <= 400


def test_slate_is_deterministic_one_per_category_and_valid() -> None:
    first = pick_slate("daily", "2026-10-13", POOL)
    assert first == pick_slate("daily", "2026-10-13", POOL)
    assert len(first) == len(CATEGORIES)
    assert len(set(first)) == len(first)
    assert [TEMPLATES_BY_ID[i].category for i in first] == list(CATEGORIES)
    assert all(TEMPLATES_BY_ID[i].period == "daily" for i in first)


def test_slates_vary_across_days_and_with_a_salt() -> None:
    days = {tuple(pick_slate("daily", f"2026-10-{d:02d}", POOL)) for d in range(1, 29)}
    assert len(days) > 1
    salted = {tuple(pick_slate("daily", "2026-10-13", POOL, salt=str(n))) for n in range(30)}
    assert len(salted) > 1


def test_slate_skips_categories_with_no_template() -> None:
    only_music = tuple(t for t in POOL if t.category == "music")
    assert [TEMPLATES_BY_ID[i].category for i in pick_slate("daily", "k", only_music)] == [
        "music"
    ]
    assert pick_slate("weekly", "k", ()) == []


def test_counts_as_listened() -> None:
    assert counts_as_listened("completed", 5, 200)
    assert counts_as_listened("skipped", 100, 200)
    assert not counts_as_listened("skipped", 99, 200)
    assert not counts_as_listened("skipped", 50, 0)  # unknown duration cannot prove 50%
    assert not counts_as_listened("stopped", 190, 200)
    assert not counts_as_listened("error", 190, 200)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_challenge_templates.py -q`
Expected: FAIL (`ModuleNotFoundError: yokai.challenges.models`).

- [ ] **Step 3: Implement `models.py`**

```python
"""Challenge data types: templates, activity events and results."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Optional, Union

CATEGORIES: Final = ("music", "games", "voice")
COUNTER_METRICS: Final = frozenset({"rounds_played", "rounds_won", "voice_minutes"})
DISTINCT_METRICS: Final = frozenset({"tracks", "artists", "games"})


@dataclass(frozen=True, slots=True)
class Template:
    """One kind of challenge in the pool."""

    id: str
    period: str  # "daily" | "weekly"
    category: str  # one of CATEGORIES
    metric: str  # one of COUNTER_METRICS | DISTINCT_METRICS
    target: int
    reward: int  # base Renown, before CHALLENGE_REWARD_SCALE
    title: str
    game: Optional[str] = None  # only count this game (rounds_played / rounds_won)


@dataclass(frozen=True, slots=True)
class Advance:
    """Result of advancing one progress row."""

    progress: int
    completed_now: bool


@dataclass(frozen=True, slots=True)
class ProgressView:
    """One challenge as shown to a user."""

    template: Template
    progress: int
    completed: bool
    reward: int  # after scaling


@dataclass(frozen=True, slots=True)
class Completion:
    """A challenge a user just finished, queued for a notice."""

    user_id: int
    template_id: str
    title: str
    reward: int
    channel_id: Optional[int]


@dataclass(frozen=True, slots=True)
class TrackListened:
    """A track finished (or was skipped past halfway) with company in the call."""

    user_ids: tuple[int, ...]
    video_id: str
    artist: Optional[str]
    channel_id: Optional[int] = None


@dataclass(frozen=True, slots=True)
class RoundPlayed:
    """A bet settled (not refunded). `round_key` groups bets of one round (blackjack double)."""

    user_id: int
    game: str
    stake: int
    payout: int
    round_key: str


@dataclass(frozen=True, slots=True)
class VoiceMinutes:
    """Time spent in a voice channel with at least one other human."""

    user_id: int
    minutes: int


Event = Union[TrackListened, RoundPlayed, VoiceMinutes]


def counts_as_listened(outcome: str, listened_s: int, duration_s: int) -> bool:
    """A track counts when it finished, or was skipped after at least half of it."""
    if outcome == "completed":
        return True
    if outcome == "skipped":
        return duration_s > 0 and listened_s * 2 >= duration_s
    return False
```

- [ ] **Step 4: Implement `templates.py`**

```python
"""The pool of challenges. Edit here to add or retune challenges; rewards are base values."""

from __future__ import annotations

from yokai.challenges.models import Template
from yokai.challenges.periods import DAILY, WEEKLY

POOL: tuple[Template, ...] = (
    # Daily: music
    Template("d_music_3", DAILY, "music", "tracks", 3, 30, "Listen to 3 tracks in a call"),
    Template("d_music_5", DAILY, "music", "tracks", 5, 40, "Listen to 5 tracks in a call"),
    Template("d_music_8", DAILY, "music", "tracks", 8, 55, "Listen to 8 tracks in a call"),
    # Daily: games
    Template("d_games_3", DAILY, "games", "rounds_played", 3, 30, "Play 3 gambling rounds"),
    Template("d_games_5", DAILY, "games", "rounds_played", 5, 40, "Play 5 gambling rounds"),
    Template(
        "d_win_slots", DAILY, "games", "rounds_won", 1, 35, "Win a round of slots", game="slots"
    ),
    Template(
        "d_win_blackjack",
        DAILY,
        "games",
        "rounds_won",
        1,
        35,
        "Win a hand of blackjack",
        game="blackjack",
    ),
    # Daily: voice
    Template(
        "d_voice_20", DAILY, "voice", "voice_minutes", 20, 30, "Spend 20 minutes in voice with company"
    ),
    Template(
        "d_voice_30", DAILY, "voice", "voice_minutes", 30, 40, "Spend 30 minutes in voice with company"
    ),
    # Weekly: music
    Template("w_music_25", WEEKLY, "music", "tracks", 25, 120, "Listen to 25 tracks in a call"),
    Template(
        "w_music_artists_10", WEEKLY, "music", "artists", 10, 120, "Listen to 10 different artists"
    ),
    # Weekly: games
    Template("w_games_20", WEEKLY, "games", "rounds_played", 20, 110, "Play 20 gambling rounds"),
    Template("w_games_win_5", WEEKLY, "games", "rounds_won", 5, 130, "Win 5 gambling rounds"),
    Template("w_games_kinds_3", WEEKLY, "games", "games", 3, 110, "Play 3 different games"),
    # Weekly: voice
    Template(
        "w_voice_180",
        WEEKLY,
        "voice",
        "voice_minutes",
        180,
        120,
        "Spend 3 hours in voice with company",
    ),
)

TEMPLATES_BY_ID: dict[str, Template] = {t.id: t for t in POOL}
```

Before moving on, run `grep -n "escrowed_bet\|place_bet" src/yokai/cogs/gambling.py src/yokai/ui/game_views.py` and confirm the game names `slots` and `blackjack` are exactly what bets are placed with (they are: `"slots"` in `cogs/gambling.py`, `"blackjack"` in `ui/game_views.py`).

- [ ] **Step 5: Implement `slate.py`**

```python
"""Pure slate selection: one template per category, seeded so it is repeatable."""

from __future__ import annotations

import random
from typing import Sequence

from yokai.challenges.models import CATEGORIES, Template


def pick_slate(
    period: str, period_key: str, pool: Sequence[Template], salt: str = ""
) -> list[str]:
    """Choose one template id per category for a period. Same inputs, same slate."""
    rng = random.Random(f"{period}:{period_key}:{salt}")
    chosen: list[str] = []
    for category in CATEGORIES:
        options = sorted(
            (t for t in pool if t.period == period and t.category == category),
            key=lambda t: t.id,
        )
        if options:
            chosen.append(rng.choice(options).id)
    return chosen
```

- [ ] **Step 6: Run to verify it passes, then commit**

Run: `python -m pytest tests/test_challenge_templates.py -q` (expect PASS), `python -m ruff format src tests`, `python -m ruff check .`.

```powershell
git add src/yokai/challenges tests/test_challenge_templates.py
git commit -m "feat(challenges): add models, template pool and slate picker" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Storage (migration v8 and challenge queries)

**Files:**
- Modify: `src/yokai/storage/db.py` (constants near `MIGRATION_V7` ~line 203 and `CURRENT_SCHEMA_VERSION` line 37; `_migrate` ~line 326; new methods after `claim_daily`/wallet methods, grouped under a `# --- Challenges ---` comment), `src/yokai_admin/modules/users/service.py` (`_GAME_LABELS`)
- Test: `tests/test_challenge_storage.py`, `tests/test_challenge_admin_label.py`

**Interfaces:**
- Consumes: `Advance` from Task 2; `Database._write_tx`, `_ensure_wallet`, `_insert_transaction`, `get_wallet` (existing).
- Produces on `Database`:
  - `async get_challenge_slate(period: str, period_key: str) -> Optional[list[str]]`
  - `async create_challenge_slate(period: str, period_key: str, template_ids: Sequence[str]) -> list[str]` (first writer wins; returns what is stored; empty list allowed)
  - `async replace_challenge_slate(period: str, period_key: str, template_ids: Sequence[str]) -> list[str]` (non-empty; marks `overridden=1`; deletes incomplete progress of removed templates; keeps completed rows)
  - `async advance_challenge(user_id: int, period: str, period_key: str, template_id: str, *, target: int, reward: int, welcome_bonus: int, delta: int = 0, item: Optional[str] = None) -> Advance`
  - `async get_challenge_progress(user_id: int, period: str, period_key: str) -> dict[str, tuple[int, bool]]` (template id to progress and completed)
  - `async purge_challenges(older_than_ts: int) -> int`

- [ ] **Step 1: Write the failing storage tests**

Create `tests/test_challenge_storage.py`:

```python
"""Challenge storage: migration, slates, atomic progress and exactly-once payout."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from yokai.storage.db import CURRENT_SCHEMA_VERSION, Database

WELCOME = 200


@pytest.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "t.db")
    await database.initialize()
    yield database
    await database.close()


async def _challenge_rows(db: Database, user_id: int) -> int:
    async with db.conn.execute(
        "SELECT COUNT(*) FROM renown_transactions WHERE user_id = ? AND reason LIKE 'challenge:%';",
        (user_id,),
    ) as cursor:
        row = await cursor.fetchone()
    return row[0]


async def test_schema_is_at_least_v8_with_the_challenge_tables(db: Database) -> None:
    assert CURRENT_SCHEMA_VERSION >= 8
    async with db.conn.execute(
        "SELECT name FROM sqlite_master WHERE name LIKE 'challenge_%';"
    ) as cursor:
        names = {row[0] for row in await cursor.fetchall()}
    assert {"challenge_slates", "challenge_progress"} <= names


async def test_a_v7_database_upgrades_and_keeps_wallets(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    first = Database(path)
    await first.initialize()
    await first.credit_wallet(1, 50, "admin", WELCOME)
    await first.conn.execute("DROP TABLE challenge_slates;")
    await first.conn.execute("DROP TABLE challenge_progress;")
    await first.conn.execute("PRAGMA user_version = 7;")
    await first.conn.commit()
    await first.close()

    upgraded = Database(path)
    await upgraded.initialize()
    wallet = await upgraded.get_wallet(1)
    assert wallet is not None and wallet.balance == WELCOME + 50
    assert await upgraded.get_challenge_slate("daily", "2026-10-13") is None
    await upgraded.close()


async def test_first_slate_wins_and_empty_slates_are_allowed(db: Database) -> None:
    assert await db.get_challenge_slate("daily", "k") is None
    assert await db.create_challenge_slate("daily", "k", ["a", "b"]) == ["a", "b"]
    assert await db.create_challenge_slate("daily", "k", ["x"]) == ["a", "b"]
    assert await db.create_challenge_slate("weekly", "k", []) == []
    assert await db.get_challenge_slate("weekly", "k") == []


async def test_counter_progress_caps_and_completes_once(db: Database) -> None:
    first = await db.advance_challenge(
        7, "daily", "k", "t", target=3, reward=40, welcome_bonus=WELCOME, delta=2
    )
    assert (first.progress, first.completed_now) == (2, False)
    done = await db.advance_challenge(
        7, "daily", "k", "t", target=3, reward=40, welcome_bonus=WELCOME, delta=5
    )
    assert (done.progress, done.completed_now) == (3, True)
    again = await db.advance_challenge(
        7, "daily", "k", "t", target=3, reward=40, welcome_bonus=WELCOME, delta=1
    )
    assert (again.progress, again.completed_now) == (3, False)
    wallet = await db.get_wallet(7)
    assert wallet is not None and wallet.balance == WELCOME + 40  # welcome bonus + one reward
    assert await _challenge_rows(db, 7) == 1


async def test_distinct_items_count_once(db: Database) -> None:
    for item in ("a", "a", "b", "a"):
        result = await db.advance_challenge(
            1, "weekly", "k", "t", target=3, reward=10, welcome_bonus=WELCOME, item=item
        )
    assert result.progress == 2
    result = await db.advance_challenge(
        1, "weekly", "k", "t", target=3, reward=10, welcome_bonus=WELCOME, item="c"
    )
    assert result.completed_now


async def test_payout_is_exactly_once_under_concurrency(db: Database) -> None:
    results = await asyncio.gather(
        *(
            db.advance_challenge(
                9, "daily", "k", "t", target=5, reward=25, welcome_bonus=WELCOME, delta=1
            )
            for _ in range(20)
        )
    )
    assert sum(r.completed_now for r in results) == 1
    wallet = await db.get_wallet(9)
    assert wallet is not None and wallet.balance == WELCOME + 25
    assert await _challenge_rows(db, 9) == 1


async def test_progress_is_per_user_and_per_period_key(db: Database) -> None:
    await db.advance_challenge(
        1, "daily", "k1", "t", target=9, reward=5, welcome_bonus=WELCOME, delta=2
    )
    await db.advance_challenge(
        2, "daily", "k1", "t", target=9, reward=5, welcome_bonus=WELCOME, delta=1
    )
    await db.advance_challenge(
        1, "daily", "k2", "t", target=9, reward=5, welcome_bonus=WELCOME, delta=4
    )
    assert await db.get_challenge_progress(1, "daily", "k1") == {"t": (2, False)}
    assert await db.get_challenge_progress(2, "daily", "k1") == {"t": (1, False)}
    assert await db.get_challenge_progress(1, "daily", "k2") == {"t": (4, False)}


async def test_replace_keeps_retained_and_completed_drops_incomplete_removed(
    db: Database,
) -> None:
    await db.create_challenge_slate("daily", "k", ["keep", "gone_open", "gone_done"])
    kw = {"target": 2, "reward": 5, "welcome_bonus": WELCOME}
    await db.advance_challenge(1, "daily", "k", "keep", delta=1, **kw)
    await db.advance_challenge(1, "daily", "k", "gone_open", delta=1, **kw)
    await db.advance_challenge(1, "daily", "k", "gone_done", delta=2, **kw)

    assert await db.replace_challenge_slate("daily", "k", ["keep", "new"]) == ["keep", "new"]

    progress = await db.get_challenge_progress(1, "daily", "k")
    assert progress == {"keep": (1, False), "gone_done": (2, True)}
    # Re-adding the paid template cannot be earned a second time.
    redo = await db.advance_challenge(1, "daily", "k", "gone_done", delta=1, **kw)
    assert not redo.completed_now
    with pytest.raises(ValueError):
        await db.replace_challenge_slate("daily", "k", [])


async def test_purge_removes_old_rows_only(db: Database) -> None:
    await db.create_challenge_slate("daily", "old", ["t"])
    await db.advance_challenge(
        1, "daily", "old", "t", target=9, reward=5, welcome_bonus=WELCOME, delta=1
    )
    await db.conn.execute("UPDATE challenge_slates SET created_ts = 100;")
    await db.conn.execute("UPDATE challenge_progress SET updated_ts = 100;")
    await db.conn.commit()
    await db.create_challenge_slate("daily", "new", ["t"])
    await db.advance_challenge(
        1, "daily", "new", "t", target=9, reward=5, welcome_bonus=WELCOME, delta=1
    )

    assert await db.purge_challenges(older_than_ts=1000) == 2
    assert await db.get_challenge_slate("daily", "old") is None
    assert await db.get_challenge_slate("daily", "new") == ["t"]
    assert await db.get_challenge_progress(1, "daily", "old") == {}
    assert await db.get_challenge_progress(1, "daily", "new") == {"t": (1, False)}
```

Create `tests/test_challenge_admin_label.py`:

```python
"""The admin panel shows challenge payouts in plain words."""

from __future__ import annotations

from yokai_admin.modules.users.service import reason_label


def test_challenge_reason_is_labelled() -> None:
    assert reason_label("challenge:d_music_3") == "Challenge (d_music_3)"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_challenge_storage.py tests/test_challenge_admin_label.py -q`
Expected: FAIL (`CURRENT_SCHEMA_VERSION` is 7; `AttributeError ... get_challenge_slate`).

- [ ] **Step 3: Add the migration**

In `src/yokai/storage/db.py`:

1. Set `CURRENT_SCHEMA_VERSION = 8`.
2. After `MIGRATION_V7` (and its trailing comment), add:

```python
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
```

3. In `_migrate`, after the `if version < 7:` block, add:

```python
        if version < 8:
            logger.info("Applying SQLite migration v8...")
            await self.conn.executescript(MIGRATION_V8)
            await self.conn.execute("PRAGMA user_version = 8;")
            await self.conn.commit()
            logger.info("SQLite schema migrated to version 8.")
```

4. Add `import json` and `from yokai.challenges.models import Advance` at the top (keep ruff's import order), and `Sequence` to the typing imports if it is not already imported.

- [ ] **Step 4: Add the challenge methods**

Add this section to `Database` (place it after the wallet methods, before the gambling section; keep it contiguous):

```python
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
                "(period, period_key, template_ids, created_ts, overridden) VALUES (?, ?, ?, ?, 0);",
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
                if item not in seen:
                    seen.append(item[:100])
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
```

Before finishing, confirm in `db.py` that `time`, `json`, and `Sequence` are imported and that `challenges.models` importing nothing from `storage` (it does not) avoids an import cycle.

- [ ] **Step 5: Label payouts in the admin panel**

In `src/yokai_admin/modules/users/service.py`, add `"challenge": "Challenge",` to `_GAME_LABELS` (it already renders `prefix:detail` as `Label (detail)`).

- [ ] **Step 6: Run to verify it passes, then commit**

Run: `python -m pytest tests/test_challenge_storage.py tests/test_challenge_admin_label.py tests/test_economy_storage.py -q` (expect PASS; the economy file guards the older migrations), `python -m ruff format src tests`, `python -m ruff check .`.

```powershell
git add src/yokai/storage/db.py src/yokai_admin/modules/users/service.py tests/test_challenge_storage.py tests/test_challenge_admin_label.py
git commit -m "feat(challenges): add migration v8 and atomic challenge progress storage" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The challenge engine

**Files:**
- Create: `src/yokai/challenges/engine.py`, `tests/challenge_helpers.py`
- Test: `tests/test_challenge_engine.py`

**Interfaces:**
- Consumes: everything from Tasks 1 to 3; `ModerationStore.is_blacklisted(user_id) -> bool`.
- Produces `ChallengeEngine`:
  - `__init__(self, db: Database, moderation: ModerationStore, *, enabled: bool = True, tz: tzinfo = timezone.utc, min_wager: int = 10, reward_scale: float = 1.0, welcome_bonus: int = 200, clock: Callable[[], float] = time.time, pool: Sequence[Template] = POOL)`
  - `async active_slate(period: str) -> tuple[str, list[Template]]` (period key and templates; creates and caches the slate)
  - `reward_for(template: Template) -> int`
  - `async record(event: Event) -> None`
  - `emit(event: Event) -> None` (sync, fire and forget, never raises)
  - `drain_completions() -> list[Completion]`
  - `async view(user_id: int, period: str) -> list[ProgressView]`
  - `next_reset(period: str) -> int`
  - `async reroll(period: str) -> list[Template]`
  - `async set_slate(period: str, template_ids: Sequence[str]) -> list[Template]` (raises `ValueError` with a user-readable message)
  - `async purge_old() -> int`
  - `async close() -> None` (waits for in-flight emitted tasks)
  - Attribute `enabled: bool`.

- [ ] **Step 1: Write the test helpers**

Create `tests/challenge_helpers.py`:

```python
"""Shared pieces for challenge tests: a tiny pool, a movable clock and an engine factory."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from tests.moderation_helpers import fake_moderation
from yokai.challenges.engine import ChallengeEngine
from yokai.challenges.models import Template
from yokai.storage.db import Database

NOW = int(datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc).timestamp())  # Tuesday, W42
DAY = 86_400


class FakeClock:
    def __init__(self, now: int = NOW) -> None:
        self.now = now

    def __call__(self) -> float:
        return float(self.now)


def tpl(
    id: str,
    metric: str,
    target: int,
    reward: int,
    *,
    period: str = "daily",
    category: str = "music",
    game: Optional[str] = None,
) -> Template:
    return Template(id, period, category, metric, target, reward, f"title {id}", game)


def make_engine(
    db: Database,
    *pool: Template,
    blacklisted: tuple[int, ...] = (),
    clock: Optional[FakeClock] = None,
    **kwargs,
) -> ChallengeEngine:
    kwargs.setdefault("welcome_bonus", 200)
    return ChallengeEngine(
        db,
        fake_moderation(owner_id=0, blacklisted=blacklisted),
        clock=clock or FakeClock(),
        pool=pool,
        **kwargs,
    )
```

- [ ] **Step 2: Write the failing engine tests**

Create `tests/test_challenge_engine.py`:

```python
"""ChallengeEngine: matching events to slates, anti-farm rules, payout and override."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from tests.challenge_helpers import DAY, FakeClock, make_engine, tpl
from yokai.challenges.models import RoundPlayed, TrackListened, VoiceMinutes
from yokai.storage.db import Database


@pytest.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "t.db")
    await database.initialize()
    yield database
    await database.close()


async def balance(db: Database, user_id: int) -> int:
    wallet = await db.get_wallet(user_id)
    return wallet.balance if wallet else 0


def listened(*users: int, video: str = "v1", artist: str | None = "Band", channel: int = 5):
    return TrackListened(user_ids=tuple(users), video_id=video, artist=artist, channel_id=channel)


def round_(user: int, game: str = "slots", stake: int = 50, payout: int = 0, key: str = "r1"):
    return RoundPlayed(user_id=user, game=game, stake=stake, payout=payout, round_key=key)


async def test_every_listener_advances_and_completion_pays_and_queues(db: Database) -> None:
    engine = make_engine(db, tpl("d_tracks", "tracks", 2, 40))
    await engine.record(listened(1, 2, video="a"))
    await engine.record(listened(1, 2, video="b"))

    assert await balance(db, 1) == 200 + 40 and await balance(db, 2) == 200 + 40
    done = engine.drain_completions()
    assert {(c.user_id, c.template_id, c.reward, c.channel_id) for c in done} == {
        (1, "d_tracks", 40, 5),
        (2, "d_tracks", 40, 5),
    }
    assert engine.drain_completions() == []


async def test_a_looped_track_does_not_farm_progress(db: Database) -> None:
    engine = make_engine(db, tpl("d_tracks", "tracks", 2, 40))
    for _ in range(10):
        await engine.record(listened(1, video="same"))
    _, templates = await engine.active_slate("daily")
    rows = await engine.view(1, "daily")
    assert rows[0].progress == 1 and not rows[0].completed
    assert await balance(db, 1) == 0 and templates


async def test_artists_are_distinct_case_insensitive_and_missing_artist_is_ignored(
    db: Database,
) -> None:
    engine = make_engine(db, tpl("w_artists", "artists", 2, 50, period="weekly"))
    await engine.record(listened(1, video="a", artist="Daft Punk"))
    await engine.record(listened(1, video="b", artist=" daft punk "))
    await engine.record(listened(1, video="c", artist=None))
    assert (await engine.view(1, "weekly"))[0].progress == 1
    await engine.record(listened(1, video="d", artist="Justice"))
    assert (await engine.view(1, "weekly"))[0].completed


async def test_rounds_below_the_wager_floor_are_ignored(db: Database) -> None:
    engine = make_engine(db, tpl("d_rounds", "rounds_played", 2, 30, category="games"), min_wager=10)
    await engine.record(round_(1, stake=9, key="a"))
    await engine.record(round_(1, stake=10, key="b"))
    assert (await engine.view(1, "daily"))[0].progress == 1


async def test_a_blackjack_double_is_one_round_and_one_win(db: Database) -> None:
    engine = make_engine(
        db,
        tpl("d_rounds", "rounds_played", 5, 30, category="games"),
        tpl("d_win", "rounds_won", 5, 30, category="voice"),  # second slate slot, any category
    )
    for _ in range(2):  # the original bet and the doubled bet settle with the same key
        await engine.record(round_(1, game="blackjack", stake=50, payout=100, key="g1"))
    rows = {r.template.id: r for r in await engine.view(1, "daily")}
    assert rows["d_rounds"].progress == 1 and rows["d_win"].progress == 1


async def test_a_push_is_not_a_win_and_the_game_filter_applies(db: Database) -> None:
    engine = make_engine(
        db, tpl("d_win_slots", "rounds_won", 1, 35, category="games", game="slots")
    )
    await engine.record(round_(1, game="slots", stake=50, payout=50, key="push"))
    await engine.record(round_(1, game="blackjack", stake=50, payout=100, key="bj"))
    assert (await engine.view(1, "daily"))[0].progress == 0
    await engine.record(round_(1, game="slots", stake=50, payout=100, key="win"))
    assert (await engine.view(1, "daily"))[0].completed


async def test_distinct_games_metric(db: Database) -> None:
    engine = make_engine(db, tpl("w_kinds", "games", 2, 60, period="weekly", category="games"))
    await engine.record(round_(1, game="slots", key="a"))
    await engine.record(round_(1, game="slots", key="b"))
    assert (await engine.view(1, "weekly"))[0].progress == 1
    await engine.record(round_(1, game="roulette", key="c"))
    assert (await engine.view(1, "weekly"))[0].completed


async def test_voice_minutes_accumulate(db: Database) -> None:
    engine = make_engine(db, tpl("d_voice", "voice_minutes", 10, 30, category="voice"))
    await engine.record(VoiceMinutes(1, 5))
    await engine.record(VoiceMinutes(1, 5))
    assert (await engine.view(1, "daily"))[0].completed


async def test_blacklisted_users_earn_nothing_and_a_disabled_engine_ignores_everything(
    db: Database,
) -> None:
    engine = make_engine(db, tpl("d_voice", "voice_minutes", 5, 30), blacklisted=(1,))
    await engine.record(VoiceMinutes(1, 5))
    assert await balance(db, 1) == 0
    off = make_engine(db, tpl("d_voice", "voice_minutes", 5, 30), enabled=False)
    await off.record(VoiceMinutes(2, 5))
    off.emit(VoiceMinutes(2, 5))
    await off.close()
    assert await balance(db, 2) == 0


async def test_a_new_user_gets_the_welcome_bonus_plus_exactly_one_reward(db: Database) -> None:
    engine = make_engine(db, tpl("d_voice", "voice_minutes", 5, 30))
    await asyncio.gather(*(engine.record(VoiceMinutes(7, 1)) for _ in range(12)))
    assert await balance(db, 7) == 200 + 30
    assert len(engine.drain_completions()) == 1


async def test_reward_scale_applies_and_never_drops_below_one(db: Database) -> None:
    big = make_engine(db, tpl("d_voice", "voice_minutes", 1, 30), reward_scale=2.0)
    await big.record(VoiceMinutes(1, 1))
    assert await balance(db, 1) == 200 + 60
    tiny = make_engine(db, tpl("d_voice", "voice_minutes", 1, 3), reward_scale=0.1)
    assert tiny.reward_for(tpl("x", "voice_minutes", 1, 3)) == 1


async def test_the_slate_survives_a_restart(db: Database) -> None:
    pool = (
        tpl("d_a", "tracks", 5, 10),
        tpl("d_b", "tracks", 6, 10),
        tpl("d_c", "tracks", 7, 10),
    )
    first = make_engine(db, *pool)
    _, picked = await first.active_slate("daily")
    # A new engine whose pool lists the options in another order must see the stored slate.
    second = make_engine(db, *reversed(pool))
    _, again = await second.active_slate("daily")
    assert [t.id for t in again] == [t.id for t in picked]


async def test_a_new_day_gets_a_fresh_slate_and_progress(db: Database) -> None:
    clock = FakeClock()
    engine = make_engine(db, tpl("d_voice", "voice_minutes", 10, 30), clock=clock)
    await engine.record(VoiceMinutes(1, 6))
    clock.now += DAY
    assert (await engine.view(1, "daily"))[0].progress == 0
    clock.now -= DAY
    assert (await engine.view(1, "daily"))[0].progress == 6


async def test_unknown_template_ids_in_a_stored_slate_are_dropped(db: Database) -> None:
    clock = FakeClock()
    engine = make_engine(db, tpl("d_voice", "voice_minutes", 10, 30), clock=clock)
    key, _ = await engine.active_slate("daily")
    await db.replace_challenge_slate("daily", key, ["d_voice", "removed_from_pool"])
    fresh = make_engine(db, tpl("d_voice", "voice_minutes", 10, 30), clock=clock)
    _, templates = await fresh.active_slate("daily")
    assert [t.id for t in templates] == ["d_voice"]


async def test_emit_never_raises_even_when_the_database_fails(db: Database) -> None:
    engine = make_engine(db, tpl("d_voice", "voice_minutes", 5, 30))
    db.advance_challenge = AsyncMock(side_effect=RuntimeError("disk on fire"))  # type: ignore[method-assign]
    engine.emit(VoiceMinutes(1, 5))
    await engine.close()  # swallowed and logged; nothing escapes


async def test_set_slate_validates_everything_and_changes_nothing_on_error(db: Database) -> None:
    engine = make_engine(
        db,
        tpl("d_a", "tracks", 5, 10),
        tpl("d_b", "voice_minutes", 5, 10, category="voice"),
        tpl("w_a", "tracks", 5, 10, period="weekly"),
    )
    _, before = await engine.active_slate("daily")
    bad_inputs = [
        [],
        ["nope"],
        ["d_a", "d_a"],
        ["w_a"],  # wrong period
        ["d_a"] * 50,
        ["d_а"],  # Cyrillic 'а'
        ["d_a", "d_b", "d_a", "d_b", "d_a", "d_b", "d_a"],
    ]
    for ids in bad_inputs:
        with pytest.raises(ValueError):
            await engine.set_slate("daily", ids)
    _, after = await engine.active_slate("daily")
    assert [t.id for t in after] == [t.id for t in before]

    chosen = await engine.set_slate("daily", ["d_b"])
    assert [t.id for t in chosen] == ["d_b"]
    assert [t.id for t in (await engine.active_slate("daily"))[1]] == ["d_b"]


async def test_reroll_keeps_retained_progress_and_picks_from_the_pool(db: Database) -> None:
    pool = (tpl("d_a", "voice_minutes", 50, 10), tpl("d_b", "voice_minutes", 50, 10))
    engine = make_engine(db, *pool)
    await engine.record(VoiceMinutes(1, 5))
    rerolled = await engine.reroll("daily")
    assert len(rerolled) == 1 and rerolled[0].id in {"d_a", "d_b"}
    assert (await engine.active_slate("daily"))[1] == rerolled


async def test_next_reset_matches_the_period(db: Database) -> None:
    engine = make_engine(db, tpl("d_a", "tracks", 5, 10))
    assert engine.next_reset("daily") - 0 > 0
    assert engine.next_reset("weekly") > engine.next_reset("daily")
```

- [ ] **Step 3: Run to verify it fails**

Run: `python -m pytest tests/test_challenge_engine.py -q`
Expected: FAIL (`ModuleNotFoundError: yokai.challenges.engine`).

- [ ] **Step 4: Implement `engine.py`**

```python
"""The challenge engine: turns activity events into progress and pays rewards."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import timezone, tzinfo
from typing import TYPE_CHECKING, Callable, Optional, Sequence

import aiosqlite

from yokai.challenges import periods
from yokai.challenges.models import (
    Completion,
    Event,
    ProgressView,
    RoundPlayed,
    Template,
    TrackListened,
    VoiceMinutes,
)
from yokai.challenges.slate import pick_slate
from yokai.challenges.templates import POOL

if TYPE_CHECKING:
    from yokai.moderation.store import ModerationStore
    from yokai.storage.db import Database

logger = logging.getLogger(__name__)

MAX_SET_TEMPLATES = 6
PURGE_AFTER_S = 30 * 86_400
_ROUND_CACHE_SIZE = 4096


@dataclass(frozen=True, slots=True)
class _Effect:
    """One metric nudge: add `delta`, or add `item` to the distinct set."""

    metric: str
    game: Optional[str]
    delta: int
    item: Optional[str]


class ChallengeEngine:
    """Matches events to the active slates, advances progress and queues completions."""

    def __init__(
        self,
        db: Database,
        moderation: ModerationStore,
        *,
        enabled: bool = True,
        tz: tzinfo = timezone.utc,
        min_wager: int = 10,
        reward_scale: float = 1.0,
        welcome_bonus: int = 200,
        clock: Callable[[], float] = time.time,
        pool: Sequence[Template] = POOL,
    ) -> None:
        self.db = db
        self.moderation = moderation
        self.enabled = enabled
        self.tz = tz
        self.min_wager = min_wager
        self.reward_scale = reward_scale
        self.welcome_bonus = welcome_bonus
        self._clock = clock
        self._pool = tuple(pool)
        self._by_id = {t.id: t for t in self._pool}
        self._slates: dict[str, tuple[str, list[Template]]] = {}
        self._slate_lock = asyncio.Lock()
        self._pending: list[Completion] = []
        self._tasks: set[asyncio.Task[None]] = set()
        self._counted_rounds: OrderedDict[tuple[int, str, str], None] = OrderedDict()

    # ------------------------------------------------------------------ slates

    def _now(self) -> int:
        return int(self._clock())

    def reward_for(self, template: Template) -> int:
        """Renown paid for a template after CHALLENGE_REWARD_SCALE (never below 1)."""
        return max(1, round(template.reward * self.reward_scale))

    def next_reset(self, period: str) -> int:
        return periods.next_reset_ts(period, self._now(), self.tz)

    async def active_slate(self, period: str) -> tuple[str, list[Template]]:
        """The current period key and its templates; creates and stores the slate if missing."""
        key = periods.period_key(period, self._now(), self.tz)
        cached = self._slates.get(period)
        if cached is not None and cached[0] == key:
            return cached
        async with self._slate_lock:
            cached = self._slates.get(period)
            if cached is not None and cached[0] == key:
                return cached
            stored = await self.db.get_challenge_slate(period, key)
            if stored is None:
                stored = await self.db.create_challenge_slate(
                    period, key, pick_slate(period, key, self._pool)
                )
            templates = self._resolve(stored)
            self._slates[period] = (key, templates)
            return key, templates

    def _resolve(self, ids: Sequence[str]) -> list[Template]:
        templates: list[Template] = []
        for template_id in ids:
            template = self._by_id.get(template_id)
            if template is None:
                logger.warning("Stored challenge %r is no longer in the pool; ignoring it", template_id)
                continue
            templates.append(template)
        return templates

    async def reroll(self, period: str) -> list[Template]:
        """Replace the current slate with a fresh random one (marks it overridden)."""
        key = periods.period_key(period, self._now(), self.tz)
        ids = pick_slate(period, key, self._pool, salt=str(self._now()))
        if not ids:
            raise ValueError(f"There are no {period} challenges in the pool.")
        return await self._store_override(period, key, ids)

    async def set_slate(self, period: str, template_ids: Sequence[str]) -> list[Template]:
        """Replace the current slate with chosen templates; raises ValueError with a reason."""
        ids = list(template_ids)
        if not 1 <= len(ids) <= MAX_SET_TEMPLATES:
            raise ValueError(f"Pick between 1 and {MAX_SET_TEMPLATES} templates.")
        if len(set(ids)) != len(ids):
            raise ValueError("Each template can only be listed once.")
        unknown = [i for i in ids if i not in self._by_id]
        if unknown:
            shown = ", ".join(i[:40] for i in unknown[:5])
            raise ValueError(f"Unknown template id(s): {shown}")
        wrong = [i for i in ids if self._by_id[i].period != period]
        if wrong:
            raise ValueError(f"Not {period} templates: {', '.join(wrong)}")
        key = periods.period_key(period, self._now(), self.tz)
        return await self._store_override(period, key, ids)

    async def _store_override(self, period: str, key: str, ids: list[str]) -> list[Template]:
        async with self._slate_lock:
            stored = await self.db.replace_challenge_slate(period, key, ids)
            templates = self._resolve(stored)
            self._slates[period] = (key, templates)
        return templates

    # ------------------------------------------------------------------ events

    def emit(self, event: Event) -> None:
        """Record an event in the background. Never raises, so hosts need no try/except."""
        if not self.enabled:
            return
        try:
            task = asyncio.get_running_loop().create_task(self._record_safely(event))
        except RuntimeError:
            logger.debug("No running loop; dropping %s", type(event).__name__)
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _record_safely(self, event: Event) -> None:
        try:
            await self.record(event)
        except Exception:
            logger.exception("Challenge engine failed on %s", type(event).__name__)

    async def close(self) -> None:
        """Let in-flight emitted events finish (used at shutdown and in tests)."""
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def record(self, event: Event) -> None:
        """Apply one event to every affected user's active challenges."""
        if not self.enabled:
            return
        if isinstance(event, TrackListened):
            for user_id in dict.fromkeys(event.user_ids):
                await self._apply(user_id, self._track_effects(event), event.channel_id)
        elif isinstance(event, RoundPlayed):
            await self._apply(event.user_id, self._round_effects(event), None)
        elif isinstance(event, VoiceMinutes):
            if event.minutes > 0:
                effect = _Effect("voice_minutes", None, event.minutes, None)
                await self._apply(event.user_id, [effect], None)

    @staticmethod
    def _track_effects(event: TrackListened) -> list[_Effect]:
        effects = [_Effect("tracks", None, 0, event.video_id)]
        artist = (event.artist or "").strip().casefold()
        if artist:
            effects.append(_Effect("artists", None, 0, artist[:100]))
        return effects

    def _first_time(self, user_id: int, round_key: str, kind: str) -> bool:
        key = (user_id, round_key, kind)
        if key in self._counted_rounds:
            return False
        self._counted_rounds[key] = None
        while len(self._counted_rounds) > _ROUND_CACHE_SIZE:
            self._counted_rounds.popitem(last=False)
        return True

    def _round_effects(self, event: RoundPlayed) -> list[_Effect]:
        if event.stake < self.min_wager:
            return []
        effects = [_Effect("games", event.game, 0, event.game)]
        if self._first_time(event.user_id, event.round_key, "played"):
            effects.append(_Effect("rounds_played", event.game, 1, None))
        if event.payout > event.stake and self._first_time(event.user_id, event.round_key, "won"):
            effects.append(_Effect("rounds_won", event.game, 1, None))
        return effects

    async def _apply(
        self, user_id: int, effects: Sequence[_Effect], channel_id: Optional[int]
    ) -> None:
        if not effects or await self.moderation.is_blacklisted(user_id):
            return
        for period in periods.PERIODS:
            key, templates = await self.active_slate(period)
            for template in templates:
                for effect in effects:
                    if effect.metric != template.metric:
                        continue
                    if template.game is not None and template.game != effect.game:
                        continue
                    await self._advance(user_id, period, key, template, effect, channel_id)

    async def _advance(
        self,
        user_id: int,
        period: str,
        key: str,
        template: Template,
        effect: _Effect,
        channel_id: Optional[int],
    ) -> None:
        reward = self.reward_for(template)
        try:
            result = await self.db.advance_challenge(
                user_id,
                period,
                key,
                template.id,
                target=template.target,
                reward=reward,
                welcome_bonus=self.welcome_bonus,
                delta=effect.delta,
                item=effect.item,
            )
        except (aiosqlite.Error, ValueError) as exc:
            logger.error("Could not advance challenge %s for user %d: %s", template.id, user_id, exc)
            return
        if result.completed_now:
            self._pending.append(
                Completion(user_id, template.id, template.title, reward, channel_id)
            )

    # ------------------------------------------------------------------ reading

    def drain_completions(self) -> list[Completion]:
        """Hand over (and clear) the completions waiting for a notice."""
        pending, self._pending = self._pending, []
        return pending

    async def view(self, user_id: int, period: str) -> list[ProgressView]:
        """This user's progress on the active slate of a period."""
        key, templates = await self.active_slate(period)
        rows = await self.db.get_challenge_progress(user_id, period, key)
        views: list[ProgressView] = []
        for template in templates:
            progress, completed = rows.get(template.id, (0, False))
            views.append(ProgressView(template, progress, completed, self.reward_for(template)))
        return views

    async def purge_old(self) -> int:
        """Delete challenge rows older than 30 days."""
        return await self.db.purge_challenges(self._now() - PURGE_AFTER_S)
```

Notes for the implementer: `test_the_slate_survives_a_restart` relies on the pool order not affecting `pick_slate` (it sorts by id) and on the stored slate being read before any new pick; `test_a_blackjack_double_is_one_round_and_one_win` uses two different categories only so both templates fit one-per-category slates. If a test's wording of the daily slate disagrees, fix the test pool, not the engine.

- [ ] **Step 5: Run to verify it passes, then commit**

Run: `python -m pytest tests/test_challenge_engine.py -q` (expect PASS), then `python -m pytest -q`, `python -m ruff format src tests`, `python -m ruff check .`.

```powershell
git add src/yokai/challenges/engine.py tests/challenge_helpers.py tests/test_challenge_engine.py
git commit -m "feat(challenges): add the challenge engine" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Game hook (bet-settlement listener) and bot wiring

**Files:**
- Modify: `src/yokai/economy/models.py`, `src/yokai/storage/db.py` (`__init__`, `_finish_bet`, `settle_lobby`, `cash_out_lobby`), `src/yokai/bot.py`
- Test: `tests/test_challenge_game_hook.py`, `tests/test_challenge_bot_wiring.py`

**Interfaces:**
- Consumes: `ChallengeEngine`, `RoundPlayed`, `load_tz`.
- Produces: `BetSettlement(bet_id: int, user_id: int, game: str, stake: int, payout: int, group_id: Optional[str])` in `yokai/economy/models.py`; `Database.bet_listener: Optional[Callable[[BetSettlement], None]]`; `Database._notify_settled(settlements: Sequence[BetSettlement]) -> None`; `YokaiBot.challenges: ChallengeEngine`; `YokaiBot._on_bet_settled(settlement: BetSettlement) -> None`.

- [ ] **Step 1: Write the failing hook tests**

Create `tests/test_challenge_game_hook.py`:

```python
"""Settled bets reach the listener; refunds and listener errors do not matter to the host."""

from __future__ import annotations

from pathlib import Path

import pytest

from yokai.economy.models import BetSettlement
from yokai.storage.db import Database

WELCOME = 200


@pytest.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "t.db")
    await database.initialize()
    yield database
    await database.close()


async def test_settle_bet_notifies_with_the_payout_and_group(db: Database) -> None:
    seen: list[BetSettlement] = []
    db.bet_listener = seen.append
    bet_id = await db.place_bet(1, 50, "blackjack", WELCOME, group_id="g1")
    await db.settle_bet(bet_id, 100)
    assert seen == [BetSettlement(bet_id, 1, "blackjack", 50, 100, "g1")]


async def test_refunds_are_not_notified(db: Database) -> None:
    seen: list[BetSettlement] = []
    db.bet_listener = seen.append
    bet_id = await db.place_bet(1, 50, "slots", WELCOME)
    await db.refund_bet(bet_id)
    assert seen == []


async def test_settle_lobby_notifies_every_seat_including_losers(db: Database) -> None:
    seen: list[BetSettlement] = []
    db.bet_listener = seen.append
    a = await db.place_bet(1, 50, "duel", WELCOME)
    b = await db.place_bet(2, 50, "duel", WELCOME)
    await db.settle_lobby({a: 95, b: 0})
    assert {(s.user_id, s.stake, s.payout) for s in seen} == {(1, 50, 95), (2, 50, 0)}
    assert all(s.game == "duel" for s in seen)


async def test_poker_cash_out_notifies_with_the_buy_in_as_stake(db: Database) -> None:
    seen: list[BetSettlement] = []
    db.bet_listener = seen.append
    a = await db.place_bet(1, 100, "poker", WELCOME, lobby_id="L1")
    b = await db.place_bet(2, 100, "poker", WELCOME, lobby_id="L1")
    await db.cash_out_lobby("L1", {a: 150, b: 50})
    assert {(s.user_id, s.stake, s.payout, s.game) for s in seen} == {
        (1, 100, 150, "poker"),
        (2, 100, 50, "poker"),
    }


async def test_a_failing_listener_never_breaks_settlement(db: Database) -> None:
    def explode(_: BetSettlement) -> None:
        raise RuntimeError("listener bug")

    db.bet_listener = explode
    bet_id = await db.place_bet(1, 50, "slots", WELCOME)
    balance = await db.settle_bet(bet_id, 120)
    wallet = await db.get_wallet(1)
    assert wallet is not None and wallet.balance == balance == WELCOME - 50 + 120
```

Create `tests/test_challenge_bot_wiring.py`:

```python
"""The bot owns a challenge engine and forwards settled bets to it as RoundPlayed events."""

from __future__ import annotations

from unittest.mock import MagicMock

from tests.test_bot_wiring import make_bot
from yokai.challenges.engine import ChallengeEngine
from yokai.challenges.models import RoundPlayed
from yokai.economy.models import BetSettlement


async def test_the_bot_has_an_engine_configured_from_settings() -> None:
    bot = make_bot(challenge_min_wager=25, challenge_reward_scale=2.0, challenge_enabled=True)
    assert isinstance(bot.challenges, ChallengeEngine)
    assert bot.challenges.min_wager == 25 and bot.challenges.reward_scale == 2.0
    assert bot.db.bet_listener == bot._on_bet_settled


async def test_a_settled_bet_becomes_a_round_event_keyed_by_group_or_bet() -> None:
    bot = make_bot()
    bot.challenges.emit = MagicMock()  # type: ignore[method-assign]
    bot._on_bet_settled(BetSettlement(9, 1, "slots", 50, 0, None))
    bot._on_bet_settled(BetSettlement(10, 1, "blackjack", 50, 100, "g7"))
    first, second = (call.args[0] for call in bot.challenges.emit.call_args_list)
    assert first == RoundPlayed(1, "slots", 50, 0, "bet9")
    assert second == RoundPlayed(1, "blackjack", 50, 100, "g7")
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_challenge_game_hook.py tests/test_challenge_bot_wiring.py -q`
Expected: FAIL (`BetSettlement` missing).

- [ ] **Step 3: Add `BetSettlement`**

Append to `src/yokai/economy/models.py` (match the file's dataclass style):

```python
@dataclass(frozen=True, slots=True)
class BetSettlement:
    """A bet that was settled (not refunded): who staked what and what came back."""

    bet_id: int
    user_id: int
    game: str
    stake: int
    payout: int
    group_id: Optional[str]
```

(Add `Optional` to the file's typing imports if missing.)

- [ ] **Step 4: Add the listener to `Database`**

In `src/yokai/storage/db.py`:

1. Import `BetSettlement` alongside the existing `from yokai.economy.models import ...` line, and `Callable` from `typing`.
2. In `Database.__init__`, add: `self.bet_listener: Optional[Callable[[BetSettlement], None]] = None`.
3. Add the method next to `settle_bet`:

```python
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
```

4. `_finish_bet` (line ~1081): extend its `SELECT user_id, game, stake, lobby_id FROM gambling_bets` to also select `group_id` and unpack it (`user_id, game, stake, lobby_id, group_id = row`). After the transaction block finishes (after the `commit()` and outside the `try`, immediately before `return wallet.balance`), add:

```python
        if not refund:
            self._notify_settled(
                [BetSettlement(bet_id, user_id, game, stake, credit, group_id)]
            )
```

Read the whole function first; `credit` equals the payout when `refund` is false, and the `user_id`, `game`, `stake` names must still be in scope at that point.

5. `settle_lobby`: after the `async with self._write_tx():` block ends (after its `commit()`), before `return balances`, add:

```python
        self._notify_settled(
            [
                BetSettlement(bet_id, rows[bet_id][0], game, rows[bet_id][2], payout, None)
                for bet_id, payout in payouts.items()
            ]
        )
```

6. `cash_out_lobby`: same position, using its `rows` shape `(user_id, stake)`:

```python
        self._notify_settled(
            [
                BetSettlement(bet_id, rows[bet_id][0], "poker", rows[bet_id][1], payout, None)
                for bet_id, payout in payouts.items()
            ]
        )
```

- [ ] **Step 5: Wire the engine into the bot**

In `src/yokai/bot.py`:

1. Imports: `from yokai.challenges.engine import ChallengeEngine`, `from yokai.challenges.models import RoundPlayed`, `from yokai.challenges.periods import load_tz`, `from yokai.economy.models import BetSettlement`.
2. In `YokaiBot.__init__`, after `self.moderation = ...` and before the voice tracker is created:

```python
        self.challenges = ChallengeEngine(
            self.db,
            self.moderation,
            enabled=config.challenge_enabled,
            tz=load_tz(config.challenge_tz),
            min_wager=config.challenge_min_wager,
            reward_scale=config.challenge_reward_scale,
            welcome_bonus=config.economy_welcome_bonus,
        )
        self.db.bet_listener = self._on_bet_settled
```

3. Add the method on `YokaiBot`:

```python
    def _on_bet_settled(self, settlement: BetSettlement) -> None:
        """Forward a settled bet to the challenge engine as one finished round."""
        self.challenges.emit(
            RoundPlayed(
                user_id=settlement.user_id,
                game=settlement.game,
                stake=settlement.stake,
                payout=settlement.payout,
                round_key=settlement.group_id or f"bet{settlement.bet_id}",
            )
        )
```

- [ ] **Step 6: Run to verify it passes, then commit**

Run: `python -m pytest tests/test_challenge_game_hook.py tests/test_challenge_bot_wiring.py -q`, then the full suite (`python -m pytest -q`) to catch regressions in the gambling and table tests, then `python -m ruff format src tests`, `python -m ruff check .`.

```powershell
git add src/yokai/economy/models.py src/yokai/storage/db.py src/yokai/bot.py tests/test_challenge_game_hook.py tests/test_challenge_bot_wiring.py
git commit -m "feat(challenges): report settled bets to the challenge engine" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Music and voice hooks

**Files:**
- Modify: `src/yokai/music/player.py` (`_retire_current` ~line 449, new `_emit_challenge`), `src/yokai/economy/voice_tracker.py` (`tick`)
- Test: `tests/test_challenge_music_hook.py`, `tests/test_challenge_voice_hook.py`

**Interfaces:**
- Consumes: `bot.challenges.emit` (Task 5), `counts_as_listened`, `TrackListened`, `VoiceMinutes`.
- Produces: `GuildPlayer._emit_challenge(track: Track, outcome: str, listened_s: int) -> None` (sync, never raises); voice ticks emit `VoiceMinutes(user_id, max(1, interval_s // 60))` per credited human.

- [ ] **Step 1: Write the failing music hook tests**

Create `tests/test_challenge_music_hook.py`:

```python
"""The player tells the challenge engine who heard a finished track."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from tests.test_player_reliability import make_player, make_track
from yokai.challenges.models import TrackListened
from yokai.music.models import Track


def _member(user_id: int, bot: bool = False) -> MagicMock:
    member = MagicMock()
    member.id = user_id
    member.bot = bot
    return member


def _connect(player, members: list[MagicMock], *, afk: bool = False, connected: bool = True):
    channel = MagicMock()
    channel.id = 77
    channel.members = members
    channel.guild.afk_channel = SimpleNamespace(id=77) if afk else None
    player.voice_client = SimpleNamespace(is_connected=lambda: connected, channel=channel)
    player.text_channel = SimpleNamespace(id=555)


async def test_a_completed_track_credits_every_human_with_company() -> None:
    player, _, bot = make_player()
    _connect(player, [_member(1), _member(2), _member(99, bot=True)])
    track = make_track("vid1")
    track.artist = "Band"

    player._emit_challenge(track, "completed", 180)

    bot.challenges.emit.assert_called_once_with(
        TrackListened(user_ids=(1, 2), video_id="vid1", artist="Band", channel_id=555)
    )


async def test_skip_counts_only_past_half() -> None:
    player, _, bot = make_player()
    _connect(player, [_member(1), _member(2)])
    player._emit_challenge(make_track("a", duration_s=200), "skipped", 99)
    bot.challenges.emit.assert_not_called()
    player._emit_challenge(make_track("b", duration_s=200), "skipped", 100)
    assert bot.challenges.emit.call_count == 1


async def test_nothing_is_emitted_alone_stopped_afk_disconnected_or_unmatched() -> None:
    player, _, bot = make_player()
    track = make_track("vid1")

    _connect(player, [_member(1), _member(99, bot=True)])  # one human plus the bot
    player._emit_challenge(track, "completed", 180)

    _connect(player, [_member(1), _member(2)])
    player._emit_challenge(track, "stopped", 180)
    player._emit_challenge(track, "error", 180)

    _connect(player, [_member(1), _member(2)], afk=True)
    player._emit_challenge(track, "completed", 180)

    _connect(player, [_member(1), _member(2)], connected=False)
    player._emit_challenge(track, "completed", 180)

    _connect(player, [_member(1), _member(2)])
    player._emit_challenge(Track(video_id="sp:abc", title="x"), "completed", 180)
    player._emit_challenge(
        Track(video_id="pending", title="x", is_pending_match=True), "completed", 180
    )

    player.voice_client = None
    player._emit_challenge(track, "completed", 180)

    bot.challenges.emit.assert_not_called()


async def test_an_engine_error_never_reaches_playback() -> None:
    player, _, bot = make_player()
    _connect(player, [_member(1), _member(2)])
    bot.challenges.emit.side_effect = RuntimeError("engine bug")
    player._emit_challenge(make_track("vid1"), "completed", 180)  # must not raise


async def test_retiring_a_track_emits_once() -> None:
    player, _, bot = make_player()
    _connect(player, [_member(1), _member(2)])
    player.current_track = make_track("vid1")
    player._retire_current("completed", listened=180)
    bot.challenges.emit.assert_called_once()
```

- [ ] **Step 2: Write the failing voice hook tests**

Create `tests/test_challenge_voice_hook.py`:

```python
"""Voice ticks report time spent with company to the challenge engine."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from tests.moderation_helpers import fake_moderation
from yokai.challenges.models import VoiceMinutes
from yokai.economy.voice_tracker import VoiceEarnTracker


def _member(user_id: int, bot: bool = False) -> MagicMock:
    member = MagicMock()
    member.id = user_id
    member.bot = bot
    return member


def _bot(channels, blacklisted=()) -> MagicMock:
    bot = MagicMock()
    bot.config.guild_id = 1
    bot.config.economy_welcome_bonus = 200
    guild = MagicMock()
    guild.voice_channels = channels
    guild.afk_channel = None
    bot.get_guild.return_value = guild
    bot.db.credit_wallet = AsyncMock()
    bot.moderation = fake_moderation(owner_id=0, blacklisted=blacklisted)
    return bot


def _channel(members) -> MagicMock:
    channel = MagicMock()
    channel.id = 10
    channel.members = members
    return channel


def _emitted(bot: MagicMock) -> set[VoiceMinutes]:
    return {call.args[0] for call in bot.challenges.emit.call_args_list}


async def test_every_credited_human_gets_minutes_for_the_tick() -> None:
    bot = _bot([_channel([_member(1), _member(2), _member(99, bot=True)])])
    await VoiceEarnTracker(bot, interval_s=300, reward=5).tick()
    assert _emitted(bot) == {VoiceMinutes(1, 5), VoiceMinutes(2, 5)}


async def test_a_lone_human_and_blacklisted_members_get_nothing() -> None:
    alone = _bot([_channel([_member(1), _member(99, bot=True)])])
    await VoiceEarnTracker(alone, interval_s=300, reward=5).tick()
    assert _emitted(alone) == set()

    bot = _bot([_channel([_member(1), _member(2)])], blacklisted=(2,))
    await VoiceEarnTracker(bot, interval_s=300, reward=5).tick()
    assert _emitted(bot) == {VoiceMinutes(1, 5)}


async def test_minutes_still_count_when_voice_pay_is_off_or_the_credit_fails() -> None:
    off = _bot([_channel([_member(1), _member(2)])])
    await VoiceEarnTracker(off, interval_s=300, reward=0).tick()
    off.db.credit_wallet.assert_not_awaited()
    assert _emitted(off) == {VoiceMinutes(1, 5), VoiceMinutes(2, 5)}

    failing = _bot([_channel([_member(1), _member(2)])])
    failing.db.credit_wallet.side_effect = RuntimeError("db down")
    await VoiceEarnTracker(failing, interval_s=300, reward=5).tick()
    assert _emitted(failing) == {VoiceMinutes(1, 5), VoiceMinutes(2, 5)}


async def test_a_short_interval_still_counts_at_least_one_minute() -> None:
    bot = _bot([_channel([_member(1), _member(2)])])
    await VoiceEarnTracker(bot, interval_s=30, reward=5).tick()
    assert _emitted(bot) == {VoiceMinutes(1, 1), VoiceMinutes(2, 1)}
```

- [ ] **Step 3: Run to verify they fail**

Run: `python -m pytest tests/test_challenge_music_hook.py tests/test_challenge_voice_hook.py -q`
Expected: FAIL (`_emit_challenge` missing; no emits).

- [ ] **Step 4: Implement the music hook**

In `src/yokai/music/player.py`: add imports `from yokai.challenges.models import TrackListened, counts_as_listened`. In `_retire_current`, directly after the `self._spawn(self._record_event(...))` line add `self._emit_challenge(track, outcome, listened)`. Add the method next to `_record_event`:

```python
    def _emit_challenge(self, track: Track, outcome: str, listened_s: int) -> None:
        """Tell the challenge engine which humans heard this track. Never raises."""
        try:
            if track.is_pending_match or track.video_id.startswith("sp:"):
                return
            if not counts_as_listened(outcome, listened_s, track.duration_s):
                return
            vc = self.voice_client
            if vc is None or not vc.is_connected():
                return
            channel = vc.channel
            afk = getattr(getattr(channel, "guild", None), "afk_channel", None)
            if afk is not None and afk.id == channel.id:
                return
            humans = tuple(member.id for member in channel.members if not member.bot)
            if len(humans) < 2:
                return
            self.bot.challenges.emit(
                TrackListened(
                    user_ids=humans,
                    video_id=track.video_id,
                    artist=track.artist,
                    channel_id=getattr(self.text_channel, "id", None),
                )
            )
        except Exception:
            logger.exception("Could not report track %s to challenges", track.video_id)
```

- [ ] **Step 5: Implement the voice hook**

In `src/yokai/economy/voice_tracker.py`, import `VoiceMinutes` from `yokai.challenges.models`. In `tick`: remove the early `if self.reward <= 0: return`; wrap the existing credit `try/except` in `if self.reward > 0:`; and after it (still inside the `for member in humans:` loop, after the blacklist `continue`) add:

```python
                self.bot.challenges.emit(
                    VoiceMinutes(member.id, max(1, self.interval_s // 60))
                )
```

Update the docstring to say minutes are reported even when voice pay is off. Existing tests in `tests/test_economy_voice_tracker.py` (including any "reward zero" test) must still pass.

- [ ] **Step 6: Run to verify it passes, then commit**

Run: `python -m pytest tests/test_challenge_music_hook.py tests/test_challenge_voice_hook.py tests/test_economy_voice_tracker.py tests/test_player_reliability.py -q`, then `python -m pytest -q`, `python -m ruff format src tests`, `python -m ruff check .`.

```powershell
git add src/yokai/music/player.py src/yokai/economy/voice_tracker.py tests/test_challenge_music_hook.py tests/test_challenge_voice_hook.py
git commit -m "feat(challenges): report listened tracks and voice time to the engine" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Embeds, notice scheduler and lifecycle wiring

**Files:**
- Modify: `src/yokai/ui/embeds.py` (add two `EmbedFactory` methods; extend the `yokai.theme` import with `PROGRESS_EMPTY`, `PROGRESS_FILLED` if not already imported), `src/yokai/bot.py`
- Create: `src/yokai/challenges/scheduler.py`
- Test: `tests/test_challenge_embeds.py`, `tests/test_challenge_scheduler.py`

**Interfaces:**
- Consumes: `ChallengeEngine.active_slate`, `drain_completions`, `purge_old`, `close`; `Completion`.
- Produces:
  - `EmbedFactory.challenge_board(*, period: str, rows: Sequence[tuple[str, int, int, int, bool]], resets_ts: int, bot_avatar_url: Optional[str] = None) -> discord.Embed` where each row is `(title, progress, target, reward, completed)`.
  - `EmbedFactory.challenge_notice(*, lines: Sequence[str], extra: int = 0, bot_avatar_url: Optional[str] = None) -> discord.Embed`.
  - `ChallengeScheduler(bot, *, interval_s: float = 10.0, max_lines: int = 10)` with `start()`, `async stop()`, `async tick()`, `async flush_notices()`; `bot.challenge_scheduler`.

- [ ] **Step 1: Write the failing embed tests**

Create `tests/test_challenge_embeds.py` (read `tests/test_lottery_embeds.py` and `tests/embed_helpers.py` first and reuse their limit-checking helper if it fits):

```python
"""Challenge embeds: content, completion marks and Discord size limits."""

from __future__ import annotations

from yokai.ui.embeds import EmbedFactory


def _rows():
    return [
        ("Listen to 5 tracks in a call", 3, 5, 40, False),
        ("Play 3 gambling rounds", 3, 3, 30, True),
        ("Spend 20 minutes in voice with company", 0, 20, 30, False),
    ]


def test_board_shows_titles_progress_rewards_and_the_reset_timer() -> None:
    embed = EmbedFactory.challenge_board(period="daily", rows=_rows(), resets_ts=1_800_000_000)
    text = embed.description or ""
    assert "Daily" in (embed.title or "")
    for title in ("Listen to 5 tracks in a call", "Play 3 gambling rounds"):
        assert title in text
    assert "3/5" in text and "0/20" in text and "+40" in text
    assert "<t:1800000000:R>" in text


def test_board_marks_finished_challenges_in_words() -> None:
    text = EmbedFactory.challenge_board(
        period="weekly", rows=_rows(), resets_ts=1_800_000_000
    ).description or ""
    done_line = next(line for line in text.splitlines() if "3/3" in line)
    assert "done" in done_line.lower()
    open_line = next(line for line in text.splitlines() if "3/5" in line)
    assert "done" not in open_line.lower()


def test_board_with_no_rows_says_so() -> None:
    embed = EmbedFactory.challenge_board(period="daily", rows=[], resets_ts=1_800_000_000)
    assert "no challenges" in (embed.description or "").lower()


def test_board_stays_inside_discords_limits() -> None:
    rows = [("x" * 400, 1, 2, 3, False)] * 6
    embed = EmbedFactory.challenge_board(period="daily", rows=rows, resets_ts=1_800_000_000)
    assert len(embed) <= 6000 and len(embed.description or "") <= 4096


def test_notice_lists_lines_and_the_overflow_count() -> None:
    embed = EmbedFactory.challenge_notice(lines=["<@1> finished **A** · +30 Renown"], extra=3)
    assert "<@1> finished **A**" in (embed.description or "")
    assert "3 more" in (embed.description or "")
    plain = EmbedFactory.challenge_notice(lines=["a"])
    assert "more" not in (plain.description or "")
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_challenge_embeds.py -q`
Expected: FAIL (`AttributeError ... challenge_board`).

- [ ] **Step 3: Implement the embeds**

Add to `EmbedFactory` in `src/yokai/ui/embeds.py` (near the economy builders; import `Emoji`, `PROGRESS_EMPTY`, `PROGRESS_FILLED`, `COLOR_BRAND`, `COLOR_SUCCESS` from `yokai.theme` if not already imported; at most one emoji per embed, so the only emoji is the title's):

```python
    @classmethod
    def challenge_board(
        cls,
        *,
        period: str,
        rows: Sequence[tuple[str, int, int, int, bool]],
        resets_ts: int,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        """A user's daily or weekly challenges: title, progress bar, count and reward."""
        lines = [f"Resets <t:{resets_ts}:R>", ""]
        if not rows:
            lines.append("There are no challenges right now.")
        for title, progress, target, reward, completed in rows:
            filled = 10 if completed else min(10, (progress * 10) // max(1, target))
            bar = PROGRESS_FILLED * filled + PROGRESS_EMPTY * (10 - filled)
            status = " · done" if completed else ""
            lines.append(f"**{escape_md(clamp(title, 120))}**")
            lines.append(f"`{bar}` {progress}/{target}{status} · +{reward:,} Renown")
        return cls._create_base(
            title=f"{Emoji.COIN} {period.capitalize()} challenges",
            description="\n".join(lines),
            color=COLOR_BRAND,
            context="Challenges",
            bot_avatar_url=bot_avatar_url,
        )

    @classmethod
    def challenge_notice(
        cls,
        *,
        lines: Sequence[str],
        extra: int = 0,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        """A short, batched 'challenge complete' announcement."""
        body = list(lines)
        if extra > 0:
            body.append(f"...and {extra} more.")
        return cls._create_base(
            title="Challenge complete" if len(lines) + extra == 1 else "Challenges complete",
            description="\n".join(body),
            color=COLOR_SUCCESS,
            context="Challenges",
            bot_avatar_url=bot_avatar_url,
        )
```

- [ ] **Step 4: Write the failing scheduler tests**

Create `tests/test_challenge_scheduler.py`:

```python
"""The scheduler batches completion notices per channel and survives failures."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import discord

from yokai.challenges.models import Completion
from yokai.challenges.scheduler import ChallengeScheduler


def _completion(user: int, channel: int | None, title: str = "Listen to 3 tracks") -> Completion:
    return Completion(user, "d_music_3", title, 30, channel)


def _setup(completions: list[Completion], channels: dict[int, object]):
    bot = MagicMock()
    bot.user = None
    bot.get_channel.side_effect = channels.get
    engine = MagicMock()
    engine.drain_completions.return_value = completions
    engine.active_slate = AsyncMock()
    engine.purge_old = AsyncMock(return_value=0)
    bot.challenges = engine
    return bot, engine


def _text_channel() -> MagicMock:
    channel = MagicMock(spec=discord.TextChannel)
    channel.send = AsyncMock()
    return channel


async def test_completions_in_one_channel_become_one_embed() -> None:
    channel = _text_channel()
    bot, _ = _setup([_completion(1, 5), _completion(2, 5)], {5: channel})
    await ChallengeScheduler(bot).flush_notices()
    channel.send.assert_awaited_once()
    embed = channel.send.await_args.kwargs["embed"]
    assert "<@1>" in embed.description and "<@2>" in embed.description
    assert channel.send.await_args.kwargs["allowed_mentions"].users is False


async def test_channels_are_separate_and_unknown_or_missing_channels_are_dropped() -> None:
    a, b = _text_channel(), _text_channel()
    bot, _ = _setup(
        [_completion(1, 5), _completion(2, 6), _completion(3, None), _completion(4, 99)],
        {5: a, 6: b},
    )
    await ChallengeScheduler(bot).flush_notices()
    a.send.assert_awaited_once()
    b.send.assert_awaited_once()


async def test_a_flood_is_collapsed_to_max_lines_plus_a_count() -> None:
    channel = _text_channel()
    bot, _ = _setup([_completion(i, 5) for i in range(25)], {5: channel})
    await ChallengeScheduler(bot, max_lines=10).flush_notices()
    description = channel.send.await_args.kwargs["embed"].description
    assert description.count("finished") == 10
    assert "15 more" in description


async def test_a_failed_send_is_swallowed() -> None:
    channel = _text_channel()
    channel.send.side_effect = discord.HTTPException(MagicMock(status=403), "forbidden")
    bot, _ = _setup([_completion(1, 5)], {5: channel})
    await ChallengeScheduler(bot).flush_notices()  # must not raise


async def test_tick_creates_both_slates_and_a_broken_tick_does_not_kill_the_loop() -> None:
    bot, engine = _setup([], {})
    scheduler = ChallengeScheduler(bot)
    await scheduler.tick()
    assert [c.args[0] for c in engine.active_slate.await_args_list] == ["daily", "weekly"]

    engine.active_slate.side_effect = RuntimeError("db down")
    await scheduler._safe_tick()  # logs and returns
```

- [ ] **Step 5: Implement the scheduler**

Create `src/yokai/challenges/scheduler.py`:

```python
"""Background upkeep for challenges: period rollover, purging and batched notices."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Optional

import discord

from yokai.challenges import periods
from yokai.textutil import escape_md
from yokai.ui.embeds import EmbedFactory

if TYPE_CHECKING:
    from yokai.bot import YokaiBot

logger = logging.getLogger(__name__)

_PURGE_EVERY_S = 3600.0


class ChallengeScheduler:
    """Every `interval_s`: make sure slates exist, purge old rows hourly, flush notices."""

    def __init__(self, bot: YokaiBot, *, interval_s: float = 10.0, max_lines: int = 10) -> None:
        self.bot = bot
        self.interval_s = interval_s
        self.max_lines = max_lines
        self._task: Optional[asyncio.Task[None]] = None
        self._last_purge = 0.0

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
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
            await self._safe_tick()

    async def _safe_tick(self) -> None:
        try:
            await self.tick()
        except Exception:
            logger.exception("Challenge scheduler tick failed")

    async def tick(self) -> None:
        """One pass: slates (creates them after a rollover), purge, notices."""
        engine = self.bot.challenges
        if not engine.enabled:
            return
        for period in periods.PERIODS:
            await engine.active_slate(period)
        now = time.monotonic()
        if now - self._last_purge >= _PURGE_EVERY_S:
            self._last_purge = now
            await engine.purge_old()
        await self.flush_notices()

    async def flush_notices(self) -> None:
        """Send one embed per channel for everything completed since the last flush."""
        completions = self.bot.challenges.drain_completions()
        by_channel: dict[int, list] = {}
        for completion in completions:
            if completion.channel_id is not None:
                by_channel.setdefault(completion.channel_id, []).append(completion)
        avatar = str(self.bot.user.display_avatar.url) if self.bot.user else None
        for channel_id, items in by_channel.items():
            channel = self.bot.get_channel(channel_id)
            if not isinstance(channel, discord.abc.Messageable):
                continue
            lines = [
                f"<@{c.user_id}> finished **{escape_md(c.title)}** · +{c.reward:,} Renown"
                for c in items[: self.max_lines]
            ]
            embed = EmbedFactory.challenge_notice(
                lines=lines, extra=max(0, len(items) - self.max_lines), bot_avatar_url=avatar
            )
            try:
                await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException as exc:
                logger.debug("Could not send challenge notice to %d: %s", channel_id, exc)
```

- [ ] **Step 6: Wire the lifecycle into the bot**

In `src/yokai/bot.py`: import `ChallengeScheduler`; in `__init__` after the engine, `self.challenge_scheduler = ChallengeScheduler(self)`; in `setup_hook` after `self.voice_earn_tracker.start()` add `self.challenge_scheduler.start()`; in `close()`, before `await self.heartbeat.stop()` add:

```python
        await self.challenge_scheduler.stop()
        await self.challenges.close()
```

Add a test to `tests/test_challenge_bot_wiring.py` asserting `isinstance(bot.challenge_scheduler, ChallengeScheduler)`.

- [ ] **Step 7: Run to verify it passes, then commit**

Run: `python -m pytest tests/test_challenge_embeds.py tests/test_challenge_scheduler.py tests/test_challenge_bot_wiring.py tests/test_code_standards.py tests/test_embeds.py -q`, then `python -m pytest -q`, `python -m ruff format src tests`, `python -m ruff check .`.

```powershell
git add src/yokai/ui/embeds.py src/yokai/challenges/scheduler.py src/yokai/bot.py tests/test_challenge_embeds.py tests/test_challenge_scheduler.py tests/test_challenge_bot_wiring.py
git commit -m "feat(challenges): add challenge embeds and the notice scheduler" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 8: The `/challenges` command

**Files:**
- Create: `src/yokai/cogs/challenges.py`, `src/yokai/ui/challenge_views.py`
- Modify: `src/yokai/bot.py` (load the extension)
- Test: `tests/test_challenge_cog.py`

**Interfaces:**
- Consumes: `bot.challenges.view`, `bot.challenges.next_reset`, `EmbedFactory.challenge_board`, `BaseView`, `send`.
- Produces: `ChallengesView(bot, user_id, bot_avatar_url=None, timeout=120.0)` with `period: str`, `async build_embed() -> discord.Embed`; `ChallengesCog` with a top-level `/challenges` command; `async setup(bot)`.

- [ ] **Step 1: Read the patterns first**

Read `src/yokai/ui/views.py` (all of `BaseView`, including `on_timeout`), `tests/test_economy_views.py` and `tests/test_economy_cog.py` for how interactions are faked and how `send` is patched (`patch("yokai.cogs.<name>.send")`), then write the tests in the same style.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_challenge_cog.py`:

```python
"""/challenges: opens ephemerally, switches tabs, refuses strangers, respects the off switch."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import discord

from tests.test_bot_wiring import make_bot
from yokai.challenges.models import ProgressView
from yokai.cogs import challenges as challenges_module
from yokai.cogs.challenges import ChallengesCog
from yokai.ui.challenge_views import ChallengesView
from tests.challenge_helpers import tpl


def _interaction(user_id: int = 1) -> MagicMock:
    interaction = MagicMock()
    interaction.user.id = user_id
    interaction.response.defer = AsyncMock()
    interaction.response.edit_message = AsyncMock()
    interaction.response.is_done.return_value = False
    return interaction


def _bot_with_rows():
    bot = make_bot()
    row = ProgressView(tpl("d_a", "tracks", 5, 40), progress=2, completed=False, reward=40)
    bot.challenges.view = AsyncMock(return_value=[row])
    bot.challenges.next_reset = MagicMock(return_value=1_800_000_000)
    return bot


async def test_the_command_is_registered_with_a_short_description() -> None:
    bot = make_bot()
    await challenges_module.setup(bot)
    command = bot.tree.get_command("challenges")
    assert command is not None and 0 < len(command.description) <= 100


async def test_the_command_defers_ephemerally_then_replies_with_the_board() -> None:
    bot = _bot_with_rows()
    cog = ChallengesCog(bot)
    interaction = _interaction()
    with patch("yokai.cogs.challenges.send", new=AsyncMock()) as send:
        await cog.challenges.callback(cog, interaction)
    interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
    kwargs = send.await_args.kwargs
    assert kwargs["ephemeral"] is True
    assert isinstance(kwargs["view"], ChallengesView)
    assert "Daily" in kwargs["embed"].title and "2/5" in kwargs["embed"].description


async def test_when_challenges_are_off_it_answers_without_deferring() -> None:
    bot = _bot_with_rows()
    bot.challenges.enabled = False
    cog = ChallengesCog(bot)
    interaction = _interaction()
    with patch("yokai.cogs.challenges.send", new=AsyncMock()) as send:
        await cog.challenges.callback(cog, interaction)
    interaction.response.defer.assert_not_awaited()
    assert send.await_args.kwargs["ephemeral"] is True
    assert "off" in send.await_args.kwargs["embed"].title.lower()


async def test_the_tabs_switch_the_period_and_edit_the_message() -> None:
    bot = _bot_with_rows()
    view = ChallengesView(bot, user_id=1)
    interaction = _interaction()
    await view.weekly_button.callback(interaction)
    assert view.period == "weekly"
    bot.challenges.view.assert_awaited_with(1, "weekly")
    edited = interaction.response.edit_message.await_args.kwargs
    assert "Weekly" in edited["embed"].title and edited["view"] is view


async def test_only_the_owner_of_the_view_may_use_it() -> None:
    view = ChallengesView(_bot_with_rows(), user_id=1)
    assert view.allowed_user_ids == {1}
    assert view.timeout is not None and view.timeout <= 180


async def test_timeout_disables_the_buttons() -> None:
    view = ChallengesView(_bot_with_rows(), user_id=1)
    view.message = MagicMock(spec=discord.Message)
    view.message.edit = AsyncMock()
    await view.on_timeout()
    assert all(item.disabled for item in view.children)
```

- [ ] **Step 3: Run to verify it fails**

Run: `python -m pytest tests/test_challenge_cog.py -q`
Expected: FAIL (`ModuleNotFoundError: yokai.cogs.challenges`).

- [ ] **Step 4: Implement the view**

Create `src/yokai/ui/challenge_views.py`:

```python
"""The /challenges tab view: Daily and Weekly for the user who opened it."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import discord

from yokai.ui.embeds import EmbedFactory
from yokai.ui.views import BaseView

if TYPE_CHECKING:
    from yokai.bot import YokaiBot


class ChallengesView(BaseView):
    """Two tabs over the same message. Times out and only answers to its owner."""

    def __init__(
        self,
        bot: YokaiBot,
        user_id: int,
        bot_avatar_url: Optional[str] = None,
        timeout: float = 120.0,
    ) -> None:
        super().__init__(allowed_user_ids=[user_id], timeout=timeout)
        self.bot = bot
        self.user_id = user_id
        self.bot_avatar_url = bot_avatar_url
        self.period = "daily"
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        daily = self.period == "daily"
        self.daily_button.style = discord.ButtonStyle.primary if daily else discord.ButtonStyle.secondary
        self.weekly_button.style = (
            discord.ButtonStyle.secondary if daily else discord.ButtonStyle.primary
        )

    async def build_embed(self) -> discord.Embed:
        engine = self.bot.challenges
        views = await engine.view(self.user_id, self.period)
        rows = [
            (v.template.title, v.progress, v.template.target, v.reward, v.completed)
            for v in views
        ]
        return EmbedFactory.challenge_board(
            period=self.period,
            rows=rows,
            resets_ts=engine.next_reset(self.period),
            bot_avatar_url=self.bot_avatar_url,
        )

    async def _show(self, interaction: discord.Interaction, period: str) -> None:
        self.period = period
        self._sync_buttons()
        embed = await self.build_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Daily", style=discord.ButtonStyle.primary)
    async def daily_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._show(interaction, "daily")

    @discord.ui.button(label="Weekly", style=discord.ButtonStyle.secondary)
    async def weekly_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._show(interaction, "weekly")
```

- [ ] **Step 5: Implement the cog**

Create `src/yokai/cogs/challenges.py`:

```python
"""The /challenges command: your daily and weekly challenge progress."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

from yokai.ui.challenge_views import ChallengesView
from yokai.ui.embeds import EmbedFactory, send

if TYPE_CHECKING:
    from yokai.bot import YokaiBot


class ChallengesCog(commands.Cog, name="Challenges"):
    """Daily and weekly challenges."""

    def __init__(self, bot: YokaiBot) -> None:
        self.bot = bot

    def _avatar_url(self) -> Optional[str]:
        return str(self.bot.user.display_avatar.url) if self.bot.user else None

    @app_commands.command(
        name="challenges", description="See your daily and weekly challenges and progress."
    )
    async def challenges(self, interaction: discord.Interaction) -> None:
        if not self.bot.challenges.enabled:
            embed = EmbedFactory.info(
                title="Challenges are off",
                description="The owner has switched challenges off for now.",
                context="Challenges",
                bot_avatar_url=self._avatar_url(),
            )
            await send(interaction, embed=embed, ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        view = ChallengesView(self.bot, interaction.user.id, bot_avatar_url=self._avatar_url())
        embed = await view.build_embed()
        view.message = await send(interaction, embed=embed, view=view, ephemeral=True)


async def setup(bot: YokaiBot) -> None:
    """Extension setup entrypoint."""
    await bot.add_cog(ChallengesCog(bot))
```

In `bot.py` `setup_hook`, add `await self.load_extension("yokai.cogs.challenges")` after the lottery line.

- [ ] **Step 6: Run to verify it passes, then commit**

Run: `python -m pytest tests/test_challenge_cog.py tests/test_command_tree.py -q`, then `python -m pytest -q`, `python -m ruff format src tests`, `python -m ruff check .`.

```powershell
git add src/yokai/cogs/challenges.py src/yokai/ui/challenge_views.py src/yokai/bot.py tests/test_challenge_cog.py
git commit -m "feat(challenges): add the /challenges command" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 9: `/admin challenges` (list, reroll, set)

**Files:**
- Modify: `src/yokai/cogs/admin.py`, `tests/test_command_tree.py` (the exact admin command-name set)
- Test: `tests/test_challenge_admin_cog.py`

**Interfaces:**
- Consumes: `bot.challenges.reroll`, `set_slate`, `active_slate`; `AdminCog._deny_unless_admin`, `_send_domain_error`.
- Produces: `/admin challenges period:<daily|weekly> action:<list|reroll|set> [templates:<ids>]`.

- [ ] **Step 1: Read the existing pattern**

Read another command in `src/yokai/cogs/admin.py` (for example `economy`) and `tests/test_admin_cog.py`, and reuse how they fake interactions and patch `send`.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_challenge_admin_cog.py` in the style of `tests/test_admin_cog.py`:

```python
"""/admin challenges: admins only; list, reroll and set; bad input changes nothing."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from discord import app_commands

from tests.challenge_helpers import tpl
from tests.moderation_helpers import fake_moderation
from tests.test_bot_wiring import make_bot
from yokai.cogs.admin import AdminCog


def _setup(user_id: int = 456):
    bot = make_bot()
    bot.moderation = fake_moderation(owner_id=456)
    daily = [tpl("d_a", "tracks", 5, 10), tpl("d_b", "voice_minutes", 5, 10, category="voice")]
    bot.challenges.active_slate = AsyncMock(return_value=("2026-10-13", daily))
    bot.challenges.reroll = AsyncMock(return_value=daily[:1])
    bot.challenges.set_slate = AsyncMock(return_value=daily[1:])
    interaction = MagicMock()
    interaction.user.id = user_id
    interaction.response.defer = AsyncMock()
    interaction.response.is_done.return_value = False
    return bot, AdminCog(bot), interaction


def _choice(value: str) -> app_commands.Choice[str]:
    return app_commands.Choice(name=value, value=value)


async def _run(cog, interaction, action: str, templates=None, period: str = "daily"):
    with patch("yokai.cogs.admin.send", new=AsyncMock()) as send:
        await cog.challenges.callback(cog, interaction, _choice(period), _choice(action), templates)
    return send


async def test_non_admins_are_refused_ephemerally_and_nothing_changes() -> None:
    bot, cog, interaction = _setup(user_id=999)
    send = await _run(cog, interaction, "reroll")
    assert send.await_args.kwargs["ephemeral"] is True
    bot.challenges.reroll.assert_not_awaited()
    interaction.response.defer.assert_not_awaited()


async def test_list_shows_the_active_slate_ids() -> None:
    _, cog, interaction = _setup()
    send = await _run(cog, interaction, "list")
    text = send.await_args.kwargs["embed"].description
    assert "d_a" in text and "d_b" in text


async def test_reroll_calls_the_engine_and_confirms() -> None:
    bot, cog, interaction = _setup()
    send = await _run(cog, interaction, "reroll", period="weekly")
    bot.challenges.reroll.assert_awaited_once_with("weekly")
    assert send.await_args.kwargs["ephemeral"] is True


async def test_set_parses_a_comma_separated_list() -> None:
    bot, cog, interaction = _setup()
    await _run(cog, interaction, "set", templates=" d_b , ")
    bot.challenges.set_slate.assert_awaited_once_with("daily", ["d_b"])


async def test_set_without_templates_is_refused_before_deferring() -> None:
    bot, cog, interaction = _setup()
    send = await _run(cog, interaction, "set", templates="  ,, ")
    interaction.response.defer.assert_not_awaited()
    bot.challenges.set_slate.assert_not_awaited()
    assert send.await_args.kwargs["ephemeral"] is True


async def test_garbage_ids_surface_the_engines_message_as_an_ephemeral_error() -> None:
    bot, cog, interaction = _setup()
    bot.challenges.set_slate.side_effect = ValueError("Unknown template id(s): nope")
    send = await _run(cog, interaction, "set", templates="nope")
    embed = send.await_args.kwargs["embed"]
    assert "Unknown template" in embed.description
    assert send.await_args.kwargs["ephemeral"] is True


async def test_an_oversized_set_is_cut_off_before_it_reaches_the_engine() -> None:
    bot, cog, interaction = _setup()
    await _run(cog, interaction, "set", templates=",".join(f"x{i}" for i in range(500)))
    ids = bot.challenges.set_slate.await_args.args[1]
    assert len(ids) <= 12
```

Also update `tests/test_command_tree.py::test_admin_commands_live_under_the_admin_group` so the expected set includes `"challenges"`.

- [ ] **Step 3: Run to verify it fails**

Run: `python -m pytest tests/test_challenge_admin_cog.py tests/test_command_tree.py -q`
Expected: FAIL (`AttributeError ... challenges`).

- [ ] **Step 4: Implement the command**

Add to `AdminCog` in `src/yokai/cogs/admin.py` (after the existing `/admin` commands; `Optional` is already imported):

```python
    @admin.command(name="challenges", description="List, reroll or set the current challenges.")
    @app_commands.describe(
        period="Which slate",
        action="list shows the active ids, reroll draws a new slate, set chooses the ids",
        templates="For 'set': template ids separated by commas (see 'list')",
    )
    @app_commands.choices(
        period=[
            app_commands.Choice(name="daily", value="daily"),
            app_commands.Choice(name="weekly", value="weekly"),
        ],
        action=[
            app_commands.Choice(name="list", value="list"),
            app_commands.Choice(name="reroll", value="reroll"),
            app_commands.Choice(name="set", value="set"),
        ],
    )
    async def challenges(
        self,
        interaction: discord.Interaction,
        period: app_commands.Choice[str],
        action: app_commands.Choice[str],
        templates: Optional[str] = None,
    ) -> None:
        """Admin tools for the challenge slates; changes are visible to everyone."""
        if await self._deny_unless_admin(interaction, "Ask the owner to add you to the adminlist."):
            return
        engine = self.bot.challenges
        ids: list[str] = []
        if action.value == "set":
            ids = [part.strip()[:40] for part in (templates or "").split(",") if part.strip()][:12]
            if not ids:
                await self._send_domain_error(
                    interaction, ValueError("Give at least one template id, separated by commas.")
                )
                return
        await interaction.response.defer(ephemeral=True)
        try:
            if action.value == "list":
                _, current = await engine.active_slate(period.value)
                title = f"Active {period.value} challenges"
            elif action.value == "reroll":
                current = await engine.reroll(period.value)
                title = f"Rerolled the {period.value} challenges"
            else:
                current = await engine.set_slate(period.value, ids)
                title = f"Set the {period.value} challenges"
        except ValueError as err:
            await self._send_domain_error(interaction, err)
            return
        lines = [f"`{t.id}` · {t.title} ({t.target}, +{t.reward} base)" for t in current]
        embed = EmbedFactory.success(
            title=title,
            description="\n".join(lines) or "No challenges.",
            context="Admin",
            bot_avatar_url=self._avatar_url(),
        )
        await send(interaction, embed=embed, ephemeral=True)
```

Check `EmbedFactory.success`'s real signature before relying on these keyword names, and adjust (for example use `EmbedFactory.info` if `success` takes different arguments). The `list` action must not be styled as a change.

- [ ] **Step 5: Run to verify it passes, then commit**

Run: `python -m pytest tests/test_challenge_admin_cog.py tests/test_command_tree.py tests/test_admin_cog.py -q`, then `python -m pytest -q`, `python -m ruff format src tests`, `python -m ruff check .`.

```powershell
git add src/yokai/cogs/admin.py tests/test_challenge_admin_cog.py tests/test_command_tree.py
git commit -m "feat(challenges): add /admin challenges list, reroll and set" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Docs, spec sync, manual QA and wrap-up

**Files:**
- Modify: `README.md`, `TASKS.md`, `QA.md`, `docs/superpowers/specs/2026-10-13-challenges-design.md`, `SPEC.md` (only §11 if `tzdata` was approved)

- [ ] **Step 1: README**

Read `README.md` and add a short "Challenges" section in the style of the existing economy and poker sections: what daily and weekly challenges are, the four rules players need to know (music needs 2+ humans in the call and counts a track once; game rounds need at least `CHALLENGE_MIN_WAGER`; rewards pay automatically; `/challenges` shows progress and the reset time), the `/admin challenges` tools, and the four `CHALLENGE_*` variables.

- [ ] **Step 2: TASKS.md**

Append "Phase 12 — Challenges" in the same format as Phase 11: goal, spec and plan paths, one checklist line per task above (1 to 10), all ticked as they are done, plus a "Known minors" list for anything found during implementation.

- [ ] **Step 3: QA.md manual checklist**

Read `QA.md` and add a "Challenges" section for the owner to run in real Discord (these are the acceptance checks automated tests cannot give):
1. `/challenges` shows 3 daily and 3 weekly challenges with a reset countdown; Daily/Weekly buttons switch tabs; the buttons stop working after about 2 minutes; another member cannot use your buttons.
2. With two accounts in a voice channel with the bot, play a few short tracks to the end. Both accounts' progress rises; a skipped-early track does not count; replaying the same track does not count twice; with only one human in the call nothing counts.
3. Complete a music challenge. The wallet rises by the reward (`/balance`), the transaction shows as "Challenge (...)" in the admin panel, and one notice embed appears in the music text channel.
4. Play game rounds at stakes below and above `CHALLENGE_MIN_WAGER`; only the larger ones count. Win a blackjack hand with a double-down: one round, one win.
5. Stay in voice with a friend for 20 minutes; voice progress rises by 5 per tick.
6. Restart the bot mid-day: the same challenges and progress are shown.
7. `/admin challenges list|reroll|set` work for an admin and are refused for a normal member; `set` with garbage ids shows an ephemeral error.
8. Set `CHALLENGE_ENABLED=false` and restart: `/challenges` says they are off, and nothing advances.

- [ ] **Step 4: Sync the spec to what was built**

Edit `docs/superpowers/specs/2026-10-13-challenges-design.md` so it matches the "Spec refinements" list at the top of this plan (the `updated_ts` column, three weekly challenges, distinct-track counting, `/admin challenges list|reroll|set` with the adminlist check, no game-completion notices in v1, poker counted at cash-out). Change its status line to "implemented, pending owner QA".

- [ ] **Step 5: SPEC.md (only if `tzdata` was approved)**

Add `tzdata` to the runtime dependency list in `SPEC.md` §11.

- [ ] **Step 6: Full verification**

Run, from the repo root with the venv active:
- `python -m pytest -q` (all pass; `live` tests stay skipped)
- `python -m ruff check .`
- `python -m ruff format --check .`

Expected: all clean. If anything fails, fix it before continuing.

- [ ] **Step 7: Commit, tag and stop**

```powershell
git add README.md TASKS.md QA.md SPEC.md docs/superpowers/specs/2026-10-13-challenges-design.md
git commit -m "docs(challenges): document challenges and record the manual QA checklist" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
git tag -a checkpoint/challenges-done -m "Challenges implemented, pending owner QA"
```

Then stop. Summarize what changed, list what is unverified (everything in the QA checklist, since it needs real Discord voice), and wait for the owner's review.

---

## Self-review notes

- **Spec coverage:** §4 package layout (Tasks 1 to 4, 7), §5 data model (Task 3), §6 templates and rewards (Task 2, with budget tests), §7 hooks (Tasks 5 and 6), §8 payout and integrity (Tasks 3 and 4), §9 scheduler and recovery (Tasks 4 and 7; the first tick after startup calls `active_slate`), §10 UI (Tasks 7, 8, 9), §11 config (Task 1), §12 testing (every task), §13 risks (tzdata in Task 1, notice channel in the refinements list).
- **Type consistency:** `Advance`, `ProgressView`, `Completion`, `Template` are defined in Task 2 and used unchanged in Tasks 3, 4, 7, 8. `advance_challenge` keyword names match between Task 3 (definition) and Task 4 (call). `active_slate` returns `(key, templates)` everywhere it is used (Tasks 4, 7, 9). `BetSettlement` fields match between Task 5's definition, tests and `bot._on_bet_settled`.
- **Known soft spots the implementer must check against the real code:** the exact `_finish_bet` variable names (Task 5 Step 4), `EmbedFactory.success` keyword names (Task 9 Step 4), and the interaction fakes in the existing cog tests (Tasks 8 and 9). Each of those steps says to read the neighbouring code first.
