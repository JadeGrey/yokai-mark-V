# Guild RPG Phase 1 (Foundation) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (native, inline) to implement this plan task-by-task. The owner delegated plan approval and the execution method for this phase; native was chosen because every task builds on the previous task's interfaces. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the foundation of the Adventurers' Guild RPG: validated TOML content, the first database table, stat calculation, and two working commands (`/guild join` and `/card`).

**Architecture:** A new `yokai/rpg/` package holds pure models, a TOML content loader, stat math and a small `RpgStore` over the existing SQLite database. The store runs its writes through a new public `Database.transaction()` hook so later phases can move items and Renown atomically. A new cog exposes the commands, every message goes through `EmbedFactory`, and the cog is only loaded when `RPG_ENABLED` is true.

**Tech Stack:** Python 3.12+, discord.py app commands, aiosqlite, stdlib `tomllib`, pytest (asyncio auto mode), ruff. No new dependency.

**Spec:** `docs/superpowers/specs/2026-10-14-guild-rpg-design.md` (sections 4, 5, 6, 7, 12, 13 and phase 1 of section 16).

## Global Constraints

- Python 3.12+ (`target-version = "py312"`), `from __future__ import annotations` in every module, fully type-hinted, dataclasses for models.
- Ruff: line length 100, rules `E, F, I, W`. `python -m ruff check .` and `python -m ruff format --check .` must be clean. After writing code in a task, run `python -m ruff format <the files you touched>` before the lint step so formatting differences never block a commit.
- No new dependencies (AGENTS.md rule 5). Content is read with stdlib `tomllib`.
- Slash commands only, guild-scoped, no privileged intents. Defer within 3 seconds for anything slow; validate before deferring so errors are ephemeral.
- Every user-visible message goes through `ui/embeds.py` (`EmbedFactory` and `send`). No raw `send_message("text")` or `channel.send("text")` (enforced by `tests/test_code_standards.py`).
- At most one emoji per embed, taken from `theme.Emoji` (enforced by `tests/test_one_emoji_rule.py`, which must list every new feature module).
- Mentions only inside embeds; `allowed_mentions` stays none.
- Use `logging`, never `print`; no bare `except`.
- Content ids are permanent (spec section 6). Never log tokens or the environment.
- Persona: at most one short quip per message; error text stays plain, with the quip only in the footer (already how `EmbedFactory.error` works).
- Tests never hit the network. Tests run from the repo root (other tests use relative paths such as `Path("src/yokai")`).
- Commit per task with a clear message, ending with the line `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Never commit `.env`, databases or cookie files.

## Review Focus

The spec implies these inputs and conditions, which no single task's happy path exercises. Each has a test in the task that owns the code.

1. **Two simultaneous `/guild join` calls by one user** must leave exactly one character row and report exactly one creation (Task 4 store test).
2. **A character whose rank id is no longer in the content files** (content edited later) must not crash `/card` (Task 6 cog test).
3. **A malformed content file** (bad TOML, wrong types, unknown stat, duplicate ids, out-of-order ranks) must fail startup with a message that names the file and the problem, never a half-loaded game (Task 3 loader tests).
4. **A character stored with a level below 1 or above the cap** (a manual database edit or a later cap change) must still produce sane stats (Task 2 stats tests).
5. **`/card` on a bot account, or on a member with no character,** must answer with an ephemeral friendly error, without deferring and without a crash (Task 6 cog tests).
6. **`RPG_ENABLED=false`** must leave the cog unloaded while still applying the database migration (Task 1 config test; Task 6 wiring).

---

## File Structure

Create:
- `content/rpg/levels.toml`, `content/rpg/ranks.toml`, `content/rpg/tags.toml`: the first three content files.
- `src/yokai/rpg/__init__.py`: package docstring only.
- `src/yokai/rpg/models.py`: `Stats`, `Tag`, `Rank`, `LevelTable`, `Character`, stat name constants.
- `src/yokai/rpg/stats.py`: `stats_at_level`, `format_stats`.
- `src/yokai/rpg/content/__init__.py`: re-exports.
- `src/yokai/rpg/content/registry.py`: `Content` and its lookups.
- `src/yokai/rpg/content/loader.py`: `ContentError`, `load_content` and the validators.
- `src/yokai/rpg/store.py`: `RpgStore`.
- `src/yokai/rpg/runtime.py`: `RpgRuntime` (content plus store).
- `src/yokai/cogs/rpg.py`: `/guild join` and `/card`.
- `tests/test_rpg_stats.py`, `tests/test_rpg_content.py`, `tests/test_rpg_store.py`, `tests/test_rpg_embeds.py`, `tests/test_rpg_cog.py`.

Modify:
- `src/yokai/config.py`, `tests/test_config.py`, `.env.example`: `RPG_ENABLED`, `RPG_CONTENT_DIR`.
- `src/yokai/storage/db.py`: migration V9, `CURRENT_SCHEMA_VERSION = 9`, public `transaction()`.
- `src/yokai/errors.py`: `NoCharacterError`.
- `src/yokai/theme.py`: `Emoji.SWORDS`, quip key `guild_join`.
- `src/yokai/ui/embeds.py`: `EmbedFactory.guild_joined`, `EmbedFactory.player_card`.
- `scripts/preview_embeds.py`: two sample embeds.
- `src/yokai/bot.py`: runtime creation and conditional cog load.
- `tests/test_one_emoji_rule.py`: list `cogs/rpg.py`.
- `README.md`, `QA.md`, `docs/superpowers/specs/2026-10-14-guild-rpg-design.md`: docs.

## Pre-flight (before Task 1)

- [ ] **Step 1: Confirm a clean baseline**

Run (PowerShell, repo root, venv active):
```powershell
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
git status --short
```
Expected: tests pass, ruff clean, working tree clean. If anything fails, stop and report it before changing code.

- [ ] **Step 2: Create the checkpoint tag the owner uses to revert a phase**

```powershell
git tag -a checkpoint/rpg-p1-start -m "Before Guild RPG phase 1 (foundation)"
git tag --list "checkpoint/rpg*"
```
Expected: the tag is listed.

---

### Task 1: Configuration

**Files:**
- Modify: `src/yokai/config.py`
- Modify: `.env.example`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `Config.rpg_enabled: bool` (default `True`) and `Config.rpg_content_dir: Path` (default `Path("content/rpg")`), read from `RPG_ENABLED` and `RPG_CONTENT_DIR`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
RPG_BASE_ENV = {
    "DISCORD_TOKEN": "mock.token.123",
    "GUILD_ID": "123456789",
    "OWNER_ID": "999",
}


def test_rpg_settings_default_to_enabled_and_the_repo_content_folder() -> None:
    with patch.dict(os.environ, RPG_BASE_ENV, clear=True):
        config = load_config(env_path=Path("non_existent_env"))
    assert config.rpg_enabled is True
    assert config.rpg_content_dir == Path("content/rpg")


def test_rpg_settings_can_be_overridden() -> None:
    env = {**RPG_BASE_ENV, "RPG_ENABLED": "false", "RPG_CONTENT_DIR": "elsewhere/rpg"}
    with patch.dict(os.environ, env, clear=True):
        config = load_config(env_path=Path("non_existent_env"))
    assert config.rpg_enabled is False
    assert config.rpg_content_dir == Path("elsewhere/rpg")


def test_blank_rpg_content_dir_falls_back_to_the_default() -> None:
    env = {**RPG_BASE_ENV, "RPG_CONTENT_DIR": "   "}
    with patch.dict(os.environ, env, clear=True):
        config = load_config(env_path=Path("non_existent_env"))
    assert config.rpg_content_dir == Path("content/rpg")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_config.py -q`
Expected: the three new tests FAIL with `AttributeError: 'Config' object has no attribute 'rpg_enabled'`.

- [ ] **Step 3: Implement**

In `src/yokai/config.py`, add to the `Config` dataclass after the Challenges block:

```python
    # Guild RPG
    rpg_enabled: bool = True
    rpg_content_dir: Path = Path("content/rpg")
```

In `load_config`, after the `challenge_reward_scale = ...` statement and before `return Config(`:

```python
    rpg_enabled = _parse_bool(os.getenv("RPG_ENABLED"), default=True)
    rpg_content_dir = Path(os.getenv("RPG_CONTENT_DIR", "content/rpg").strip() or "content/rpg")
```

In the `return Config(` call, after `challenge_reward_scale=challenge_reward_scale,` add:

```python
        rpg_enabled=rpg_enabled,
        rpg_content_dir=rpg_content_dir,
```

Append to `.env.example` (after the Challenges block, keeping a blank line before it):

```
# Guild RPG (the adventurers' guild)
# Master switch. When false the /guild and /card commands are not registered.
RPG_ENABLED=true
# Folder holding the RPG content files (levels.toml, ranks.toml, tags.toml, ...), relative to
# where the bot is started.
RPG_CONTENT_DIR=content/rpg
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_config.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/yokai/config.py .env.example tests/test_config.py
git commit -m "feat(rpg): add RPG_ENABLED and RPG_CONTENT_DIR settings" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Models and stat calculation

**Files:**
- Create: `src/yokai/rpg/__init__.py`
- Create: `src/yokai/rpg/models.py`
- Create: `src/yokai/rpg/stats.py`
- Test: `tests/test_rpg_stats.py`

**Interfaces:**
- Produces (in `yokai.rpg.models`):
  - `STAT_NAMES: tuple[str, ...]` = `("hp", "attack", "magic", "defense", "resist", "speed", "crit")` and `STAT_LABELS: dict[str, str]` mapping each to its display label (`"HP"`, `"Attack"`, `"Magic"`, `"Defense"`, `"Resist"`, `"Speed"`, `"Crit"`).
  - `Stats` (frozen dataclass, seven `int` fields in `STAT_NAMES` order, default 0) with `__add__(other: Stats) -> Stats` and `scaled(factor: int) -> Stats`.
  - `Tag(id: str, name: str, role: str, leans: tuple[str, ...], description: str)`, `Rank(id: str, min_level: int)`, `LevelTable(cap: int, base: Stats, growth: Stats)`, `Character(user_id: int, level: int, xp: int, rank: str, battles_won: int, battles_lost: int, created_ts: int)`; all frozen dataclasses.
- Produces (in `yokai.rpg.stats`): `stats_at_level(table: LevelTable, level: int) -> Stats` and `format_stats(stats: Stats) -> list[tuple[str, str]]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_rpg_stats.py`:

```python
"""Tests for the RPG stat model and level math."""

from __future__ import annotations

import pytest

from yokai.rpg.models import STAT_LABELS, STAT_NAMES, LevelTable, Stats
from yokai.rpg.stats import format_stats, stats_at_level

TABLE = LevelTable(
    cap=60,
    base=Stats(hp=100, attack=10, magic=10, defense=5, resist=5, speed=10, crit=5),
    growth=Stats(hp=12, attack=2, magic=2, defense=1, resist=1, speed=1, crit=0),
)


def test_stat_names_and_labels_cover_the_same_seven_stats() -> None:
    assert STAT_NAMES == ("hp", "attack", "magic", "defense", "resist", "speed", "crit")
    assert set(STAT_LABELS) == set(STAT_NAMES)


def test_stats_add_and_scale_every_field() -> None:
    a = Stats(hp=1, attack=2, magic=3, defense=4, resist=5, speed=6, crit=7)
    b = Stats(hp=10, attack=20, magic=30, defense=40, resist=50, speed=60, crit=70)
    assert a + b == Stats(hp=11, attack=22, magic=33, defense=44, resist=55, speed=66, crit=77)
    assert a.scaled(3) == Stats(hp=3, attack=6, magic=9, defense=12, resist=15, speed=18, crit=21)


def test_level_one_is_the_base_stats() -> None:
    assert stats_at_level(TABLE, 1) == TABLE.base


def test_each_level_adds_the_growth_once() -> None:
    assert stats_at_level(TABLE, 5) == Stats(
        hp=148, attack=18, magic=18, defense=9, resist=9, speed=14, crit=5
    )


@pytest.mark.parametrize("bad_level", [0, -3])
def test_a_level_below_one_is_treated_as_level_one(bad_level: int) -> None:
    assert stats_at_level(TABLE, bad_level) == TABLE.base


def test_a_level_above_the_cap_is_clamped_to_the_cap() -> None:
    assert stats_at_level(TABLE, 9999) == stats_at_level(TABLE, 60)


def test_format_stats_returns_labelled_values_with_crit_as_a_percentage() -> None:
    pairs = format_stats(Stats(hp=1234, attack=10, magic=11, defense=5, resist=6, speed=7, crit=5))
    assert pairs == [
        ("HP", "1,234"),
        ("Attack", "10"),
        ("Magic", "11"),
        ("Defense", "5"),
        ("Resist", "6"),
        ("Speed", "7"),
        ("Crit", "5%"),
    ]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_rpg_stats.py -q`
Expected: collection error `ModuleNotFoundError: No module named 'yokai.rpg'`.

- [ ] **Step 3: Implement**

Create `src/yokai/rpg/__init__.py`:

```python
"""The Adventurers' Guild RPG: characters, gear, missions and combat."""
```

Create `src/yokai/rpg/models.py`:

```python
"""Data models for the Guild RPG."""

from __future__ import annotations

from dataclasses import dataclass

STAT_NAMES: tuple[str, ...] = ("hp", "attack", "magic", "defense", "resist", "speed", "crit")

STAT_LABELS: dict[str, str] = {
    "hp": "HP",
    "attack": "Attack",
    "magic": "Magic",
    "defense": "Defense",
    "resist": "Resist",
    "speed": "Speed",
    "crit": "Crit",
}


@dataclass(frozen=True, slots=True)
class Stats:
    """The seven combat stats. `crit` is a percentage (5 means 5%)."""

    hp: int = 0
    attack: int = 0
    magic: int = 0
    defense: int = 0
    resist: int = 0
    speed: int = 0
    crit: int = 0

    def __add__(self, other: Stats) -> Stats:
        return Stats(*(getattr(self, name) + getattr(other, name) for name in STAT_NAMES))

    def scaled(self, factor: int) -> Stats:
        """Every stat multiplied by a whole number (used for per-level growth)."""
        return Stats(*(getattr(self, name) * factor for name in STAT_NAMES))


@dataclass(frozen=True, slots=True)
class Tag:
    """A gear tag, such as Oni or Kitsune."""

    id: str
    name: str
    role: str
    leans: tuple[str, ...]
    description: str


@dataclass(frozen=True, slots=True)
class Rank:
    """A guild rank and the level at which promotion into it opens."""

    id: str
    min_level: int


@dataclass(frozen=True, slots=True)
class LevelTable:
    """The level cap and the base stat curve: base at level 1, plus growth per level above 1."""

    cap: int
    base: Stats
    growth: Stats


@dataclass(frozen=True, slots=True)
class Character:
    """A member's adventurer, as stored in the database."""

    user_id: int
    level: int
    xp: int
    rank: str
    battles_won: int
    battles_lost: int
    created_ts: int
```

Create `src/yokai/rpg/stats.py`:

```python
"""Pure stat math for the Guild RPG."""

from __future__ import annotations

from yokai.rpg.models import STAT_LABELS, STAT_NAMES, LevelTable, Stats


def stats_at_level(table: LevelTable, level: int) -> Stats:
    """Base stats for a level. Levels below 1 or above the cap are clamped, never an error."""
    clamped = max(1, min(level, table.cap))
    return table.base + table.growth.scaled(clamped - 1)


def format_stats(stats: Stats) -> list[tuple[str, str]]:
    """(label, display value) for each stat in display order; Crit is shown as a percentage."""
    pairs: list[tuple[str, str]] = []
    for name in STAT_NAMES:
        value = getattr(stats, name)
        pairs.append((STAT_LABELS[name], f"{value}%" if name == "crit" else f"{value:,}"))
    return pairs
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_rpg_stats.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/yokai/rpg tests/test_rpg_stats.py
git commit -m "feat(rpg): add stat models and level stat math" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Content loader, registry and the first content files

**Files:**
- Create: `src/yokai/rpg/content/__init__.py`
- Create: `src/yokai/rpg/content/registry.py`
- Create: `src/yokai/rpg/content/loader.py`
- Create: `content/rpg/levels.toml`, `content/rpg/ranks.toml`, `content/rpg/tags.toml`
- Test: `tests/test_rpg_content.py`

**Interfaces:**
- Consumes: `Stats`, `Tag`, `Rank`, `LevelTable`, `STAT_NAMES` from Task 2.
- Produces:
  - `yokai.rpg.content.loader.ContentError(Exception)` and `load_content(directory: Path | str) -> Content`.
  - `yokai.rpg.content.registry.Content` (frozen dataclass): fields `levels: LevelTable`, `ranks: tuple[Rank, ...]`, `tags: Mapping[str, Tag]` (read-only, in file order); methods `rank_index(rank_id: str) -> int` (raises `KeyError` for an unknown id) and `next_rank(rank_id: str) -> Optional[Rank]` (raises `KeyError` for an unknown id, returns `None` for the top rank).
  - `yokai.rpg.content` re-exports `Content`, `ContentError`, `load_content`.

- [ ] **Step 1: Write the content files**

Create `content/rpg/levels.toml`:

```toml
# Level cap and the base stat curve. All numbers are placeholders that the pacing simulator
# (phase 2) will tune. Stats at level N = [base] + [growth] * (N - 1). Crit is a percentage.
cap = 60

[base]
hp = 100
attack = 10
magic = 10
defense = 5
resist = 5
speed = 10
crit = 5

[growth]
hp = 12
attack = 2
magic = 2
defense = 1
resist = 1
speed = 1
crit = 0
```

Create `content/rpg/ranks.toml`:

```toml
# Guild ranks in ascending order. `min_level` is the level at which promotion into that rank
# opens (the first rank starts at level 1). Placeholders, tuned by the pacing simulator.
[[rank]]
id = "E"
min_level = 1

[[rank]]
id = "D"
min_level = 5

[[rank]]
id = "C"
min_level = 12

[[rank]]
id = "B"
min_level = 20

[[rank]]
id = "A"
min_level = 30

[[rank]]
id = "SS"
min_level = 45

[[rank]]
id = "SS+"
min_level = 60
```

Create `content/rpg/tags.toml`:

```toml
# Gear tags, themed on Japanese folklore. `leans` lists the stats a tag's gear favours (a
# tendency in the item stats, not a rule). Set bonuses and skills arrive with the gear phase.
[[tag]]
id = "oni"
name = "Oni"
role = "Bruiser"
leans = ["attack", "hp"]
description = "Ogres of folklore. They hit hard and shrug off hits."

[[tag]]
id = "kitsune"
name = "Kitsune"
role = "Trickster mage"
leans = ["magic", "speed"]
description = "Fox spirits of illusion and fire."

[[tag]]
id = "tengu"
name = "Tengu"
role = "Striker"
leans = ["crit", "speed", "attack"]
description = "Winged mountain swordsmen. Fast, precise and lethal."

[[tag]]
id = "kappa"
name = "Kappa"
role = "Healer"
leans = ["hp", "resist", "magic"]
description = "River spirits who mend what breaks."

[[tag]]
id = "yuki_onna"
name = "Yuki-onna"
role = "Controller"
leans = ["magic", "resist"]
description = "The snow woman. She slows and weakens everything near her."

[[tag]]
id = "tsukumogami"
name = "Tsukumogami"
role = "Tank"
leans = ["defense", "resist", "hp"]
description = "Old objects that woke up and refuse to break."

[[tag]]
id = "jorogumo"
name = "Jorogumo"
role = "Damage over time"
leans = ["speed", "crit", "attack"]
description = "Spider women. Patient poison and bleeding."

[[tag]]
id = "baku"
name = "Baku"
role = "Support"
leans = ["resist", "speed", "magic"]
description = "Dream eaters. They devour nightmares and cleanse what ails their allies."
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_rpg_content.py`:

```python
"""Tests for the RPG content loader and validators."""

from __future__ import annotations

from pathlib import Path

import pytest

from yokai.rpg.content import Content, ContentError, load_content
from yokai.rpg.models import STAT_NAMES, Stats

LEVELS = """\
cap = 60

[base]
hp = 100
attack = 10
magic = 10
defense = 5
resist = 5
speed = 10
crit = 5

[growth]
hp = 12
attack = 2
magic = 2
defense = 1
resist = 1
speed = 1
crit = 0
"""

RANKS = """\
[[rank]]
id = "E"
min_level = 1

[[rank]]
id = "D"
min_level = 5

[[rank]]
id = "C"
min_level = 12
"""

TAGS = """\
[[tag]]
id = "oni"
name = "Oni"
role = "Bruiser"
leans = ["attack", "hp"]
description = "Ogres of folklore."

[[tag]]
id = "kitsune"
name = "Kitsune"
role = "Trickster mage"
leans = ["magic", "speed"]
description = "Fox spirits."
"""


def write_dir(tmp_path: Path, levels: str = LEVELS, ranks: str = RANKS, tags: str = TAGS) -> Path:
    (tmp_path / "levels.toml").write_text(levels, encoding="utf-8")
    (tmp_path / "ranks.toml").write_text(ranks, encoding="utf-8")
    (tmp_path / "tags.toml").write_text(tags, encoding="utf-8")
    return tmp_path


def mutate(text: str, old: str, new: str) -> str:
    assert old in text, f"test bug: {old!r} not in the sample text"
    return text.replace(old, new, 1)


def test_the_shipped_content_files_load_and_match_the_spec() -> None:
    content = load_content(Path("content/rpg"))
    assert content.levels.cap == 60
    assert [rank.id for rank in content.ranks] == ["E", "D", "C", "B", "A", "SS", "SS+"]
    assert content.ranks[0].min_level == 1
    assert list(content.tags) == [
        "oni",
        "kitsune",
        "tengu",
        "kappa",
        "yuki_onna",
        "tsukumogami",
        "jorogumo",
        "baku",
    ]


def test_a_valid_folder_loads(tmp_path: Path) -> None:
    content = load_content(write_dir(tmp_path))
    assert isinstance(content, Content)
    assert content.levels.base == Stats(
        hp=100, attack=10, magic=10, defense=5, resist=5, speed=10, crit=5
    )
    assert content.tags["oni"].leans == ("attack", "hp")
    assert content.tags["kitsune"].name == "Kitsune"


def test_tags_are_read_only(tmp_path: Path) -> None:
    content = load_content(write_dir(tmp_path))
    with pytest.raises(TypeError):
        content.tags["new"] = content.tags["oni"]  # type: ignore[index]


def test_rank_lookups(tmp_path: Path) -> None:
    content = load_content(write_dir(tmp_path))
    assert content.rank_index("E") == 0
    assert content.rank_index("C") == 2
    next_rank = content.next_rank("E")
    assert next_rank is not None and next_rank.id == "D" and next_rank.min_level == 5
    assert content.next_rank("C") is None
    with pytest.raises(KeyError):
        content.rank_index("Z")
    with pytest.raises(KeyError):
        content.next_rank("Z")


def test_a_missing_file_names_the_file(tmp_path: Path) -> None:
    write_dir(tmp_path)
    (tmp_path / "ranks.toml").unlink()
    with pytest.raises(ContentError, match="Missing content file.*ranks.toml"):
        load_content(tmp_path)


def test_a_missing_folder_is_a_content_error(tmp_path: Path) -> None:
    with pytest.raises(ContentError, match="Missing content file"):
        load_content(tmp_path / "does-not-exist")


def test_invalid_toml_names_the_file(tmp_path: Path) -> None:
    write_dir(tmp_path, tags="[[tag]\nid = ")
    with pytest.raises(ContentError, match="tags.toml is not valid TOML"):
        load_content(tmp_path)


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("cap = 60", 'cap = "sixty"', "cap must be a whole number"),
        ("cap = 60", "cap = 0", "cap must be at least 1"),
        ("cap = 60", "cap = true", "cap must be a whole number"),
        ("hp = 100", "hp = 0", r"\[base\].hp must be at least 1"),
        ("hp = 12", "hp = -1", r"\[growth\].hp must be at least 0"),
        ("crit = 5\n", "", r"\[base\] is missing stat\(s\): crit"),
        ("crit = 5\n", "crit = 5\nluck = 1\n", r"\[base\] has unknown stat\(s\): luck"),
    ],
)
def test_bad_levels_are_rejected(tmp_path: Path, old: str, new: str, message: str) -> None:
    levels = mutate(LEVELS, old, new)
    with pytest.raises(ContentError, match=message):
        load_content(write_dir(tmp_path, levels=levels))


def test_levels_without_a_base_table_are_rejected(tmp_path: Path) -> None:
    levels = "cap = 60\n"
    with pytest.raises(ContentError, match=r"\[base\] must be a table"):
        load_content(write_dir(tmp_path, levels=levels))


@pytest.mark.parametrize(
    ("ranks", "message"),
    [
        ("", "needs at least one"),
        (mutate(RANKS, "min_level = 5", "min_level = 1"), "higher than the previous"),
        (mutate(RANKS, 'id = "D"', 'id = "E"'), "duplicate rank id"),
        (mutate(RANKS, "min_level = 1", "min_level = 2"), "first rank must start at level 1"),
        (mutate(RANKS, "min_level = 12", "min_level = 61"), "above the level cap"),
        (mutate(RANKS, 'id = "C"', 'id = ""'), "id must be non-empty text"),
    ],
)
def test_bad_ranks_are_rejected(tmp_path: Path, ranks: str, message: str) -> None:
    with pytest.raises(ContentError, match=message):
        load_content(write_dir(tmp_path, ranks=ranks))


@pytest.mark.parametrize(
    ("tags", "message"),
    [
        ("", "needs at least one"),
        (mutate(TAGS, 'id = "kitsune"', 'id = "oni"'), "duplicate tag id"),
        (mutate(TAGS, 'name = "Kitsune"', 'name = "ONI"'), "duplicate tag name"),
        (mutate(TAGS, '["attack", "hp"]', '["attack", "luck"]'), "unknown stat"),
        (mutate(TAGS, '["attack", "hp"]', "[]"), "1 to 3 stats"),
        (
            mutate(TAGS, '["attack", "hp"]', '["attack", "hp", "magic", "speed"]'),
            "1 to 3 stats",
        ),
        (mutate(TAGS, '["attack", "hp"]', '"attack"'), "must be a list"),
        (mutate(TAGS, 'role = "Bruiser"', 'role = ""'), "role must be non-empty text"),
    ],
)
def test_bad_tags_are_rejected(tmp_path: Path, tags: str, message: str) -> None:
    with pytest.raises(ContentError, match=message):
        load_content(write_dir(tmp_path, tags=tags))


def test_every_stat_name_is_accepted_as_a_lean(tmp_path: Path) -> None:
    entries = "\n".join(
        f'[[tag]]\nid = "t{i}"\nname = "T{i}"\nrole = "r"\nleans = ["{stat}"]\ndescription = "d"\n'
        for i, stat in enumerate(STAT_NAMES)
    )
    content = load_content(write_dir(tmp_path, tags=entries))
    assert len(content.tags) == len(STAT_NAMES)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m pytest tests/test_rpg_content.py -q`
Expected: collection error `ModuleNotFoundError: No module named 'yokai.rpg.content'`.

- [ ] **Step 4: Implement the registry**

Create `src/yokai/rpg/content/registry.py`:

```python
"""Read-only lookups over the loaded RPG content."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional

from yokai.rpg.models import LevelTable, Rank, Tag


@dataclass(frozen=True, slots=True)
class Content:
    """Everything the RPG reads from `content/rpg/`, validated and immutable."""

    levels: LevelTable
    ranks: tuple[Rank, ...]
    tags: Mapping[str, Tag]

    def rank_index(self, rank_id: str) -> int:
        """Position of a rank in the ladder (0 is the lowest). Raises KeyError if unknown."""
        for index, rank in enumerate(self.ranks):
            if rank.id == rank_id:
                return index
        raise KeyError(rank_id)

    def next_rank(self, rank_id: str) -> Optional[Rank]:
        """The rank above `rank_id`, or None at the top. Raises KeyError if unknown."""
        index = self.rank_index(rank_id)
        return self.ranks[index + 1] if index + 1 < len(self.ranks) else None
```

- [ ] **Step 5: Implement the loader**

Create `src/yokai/rpg/content/loader.py`:

```python
"""Load and validate the RPG content files (TOML); a bad file stops startup with a clear error."""

from __future__ import annotations

import tomllib
from pathlib import Path
from types import MappingProxyType
from typing import Any

from yokai.rpg.content.registry import Content
from yokai.rpg.models import STAT_NAMES, LevelTable, Rank, Stats, Tag

MAX_LEANS = 3


class ContentError(Exception):
    """Raised when a content file is missing or invalid."""


def _read(directory: Path, name: str) -> dict[str, Any]:
    path = directory / name
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError as err:
        raise ContentError(f"Missing content file: {path}") from err
    except tomllib.TOMLDecodeError as err:
        raise ContentError(f"{name} is not valid TOML: {err}") from err
    except OSError as err:
        raise ContentError(f"Could not read content file {path}: {err}") from err


def _int(value: object, where: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContentError(f"{where} must be a whole number")
    if value < minimum:
        raise ContentError(f"{where} must be at least {minimum}")
    return value


def _text(value: object, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContentError(f"{where} must be non-empty text")
    return value.strip()


def _stats(table: object, where: str, hp_minimum: int) -> Stats:
    if not isinstance(table, dict):
        raise ContentError(f"{where} must be a table")
    unknown = sorted(set(table) - set(STAT_NAMES))
    if unknown:
        raise ContentError(f"{where} has unknown stat(s): {', '.join(unknown)}")
    missing = [name for name in STAT_NAMES if name not in table]
    if missing:
        raise ContentError(f"{where} is missing stat(s): {', '.join(missing)}")
    values = {
        name: _int(table[name], f"{where}.{name}", hp_minimum if name == "hp" else 0)
        for name in STAT_NAMES
    }
    return Stats(**values)


def _parse_levels(raw: dict[str, Any]) -> LevelTable:
    cap = _int(raw.get("cap"), "levels.toml cap", 1)
    base = _stats(raw.get("base"), "levels.toml [base]", 1)
    growth = _stats(raw.get("growth"), "levels.toml [growth]", 0)
    return LevelTable(cap=cap, base=base, growth=growth)


def _entries(raw: dict[str, Any], key: str, file_name: str) -> list[dict[str, Any]]:
    entries = raw.get(key)
    if not isinstance(entries, list) or not entries:
        raise ContentError(f"{file_name} needs at least one [[{key}]] entry")
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise ContentError(f"{file_name} {key} #{index} must be a table")
    return entries


def _parse_ranks(raw: dict[str, Any], cap: int) -> tuple[Rank, ...]:
    ranks: list[Rank] = []
    seen: set[str] = set()
    for index, entry in enumerate(_entries(raw, "rank", "ranks.toml"), start=1):
        where = f"ranks.toml rank #{index}"
        rank_id = _text(entry.get("id"), f"{where} id")
        if rank_id in seen:
            raise ContentError(f"ranks.toml has a duplicate rank id: {rank_id}")
        seen.add(rank_id)
        min_level = _int(entry.get("min_level"), f"{where} min_level", 1)
        if ranks and min_level <= ranks[-1].min_level:
            raise ContentError(f"{where} min_level must be higher than the previous rank's")
        ranks.append(Rank(id=rank_id, min_level=min_level))
    if ranks[0].min_level != 1:
        raise ContentError("ranks.toml: the first rank must start at level 1")
    if ranks[-1].min_level > cap:
        raise ContentError("ranks.toml: the last rank's min_level is above the level cap")
    return tuple(ranks)


def _parse_leans(value: object, where: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ContentError(f"{where} leans must be a list of stat names")
    if not 1 <= len(value) <= MAX_LEANS:
        raise ContentError(f"{where} leans must name 1 to {MAX_LEANS} stats")
    for stat in value:
        if stat not in STAT_NAMES:
            raise ContentError(f"{where} leans has an unknown stat: {stat!r}")
    return tuple(value)


def _parse_tags(raw: dict[str, Any]) -> dict[str, Tag]:
    tags: dict[str, Tag] = {}
    names: set[str] = set()
    for index, entry in enumerate(_entries(raw, "tag", "tags.toml"), start=1):
        where = f"tags.toml tag #{index}"
        tag_id = _text(entry.get("id"), f"{where} id")
        if tag_id in tags:
            raise ContentError(f"tags.toml has a duplicate tag id: {tag_id}")
        name = _text(entry.get("name"), f"{where} name")
        if name.casefold() in names:
            raise ContentError(f"tags.toml has a duplicate tag name: {name}")
        names.add(name.casefold())
        tags[tag_id] = Tag(
            id=tag_id,
            name=name,
            role=_text(entry.get("role"), f"{where} role"),
            leans=_parse_leans(entry.get("leans"), where),
            description=_text(entry.get("description"), f"{where} description"),
        )
    return tags


def load_content(directory: Path | str) -> Content:
    """Read and validate every content file in `directory`. Raises ContentError on any problem."""
    root = Path(directory)
    levels = _parse_levels(_read(root, "levels.toml"))
    ranks = _parse_ranks(_read(root, "ranks.toml"), levels.cap)
    tags = _parse_tags(_read(root, "tags.toml"))
    return Content(levels=levels, ranks=ranks, tags=MappingProxyType(tags))
```

Create `src/yokai/rpg/content/__init__.py`:

```python
"""RPG content: validated, read-only game data loaded from TOML files."""

from __future__ import annotations

from yokai.rpg.content.loader import ContentError, load_content
from yokai.rpg.content.registry import Content

__all__ = ["Content", "ContentError", "load_content"]
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest tests/test_rpg_content.py -q`
Expected: all PASS. If a `match=` pattern fails, fix the loader's message (not the test) unless the test's expectation contradicts the spec.

- [ ] **Step 7: Lint and commit**

```powershell
python -m ruff check src tests
python -m ruff format --check src tests
git add content/rpg src/yokai/rpg tests/test_rpg_content.py
git commit -m "feat(rpg): add TOML content loader, registry and first content files" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Database migration, transaction hook, store and runtime

**Files:**
- Modify: `src/yokai/storage/db.py`
- Create: `src/yokai/rpg/store.py`
- Create: `src/yokai/rpg/runtime.py`
- Test: `tests/test_rpg_store.py`

**Interfaces:**
- Consumes: `Character` (Task 2), `Content`/`load_content` (Task 3), the existing `Database` and its private `_write_tx()`.
- Produces:
  - `Database.transaction() -> AsyncIterator[aiosqlite.Connection]`: public async context manager. Holds the in-process write lock and SQLite's write lock (`BEGIN IMMEDIATE`), yields the shared connection, commits when the block ends normally, rolls back if it raises.
  - Schema v9 with table `rpg_characters` (`user_id` primary key, `level`, `xp`, `rank`, `battles_won`, `battles_lost`, `created_ts`); `CURRENT_SCHEMA_VERSION == 9`.
  - `RpgStore(db: Database)` with `async get_character(user_id: int) -> Optional[Character]` and `async create_character(user_id: int, start_rank: str, now: Optional[int] = None) -> tuple[Character, bool]` (second value is `True` only for the call that created the row; safe under concurrency).
  - `RpgRuntime` (frozen dataclass) with fields `content: Content` and `store: RpgStore`, and `RpgRuntime.create(db: Database, content_dir: Path | str) -> RpgRuntime`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_rpg_store.py`:

```python
"""Tests for the RPG schema, the public transaction hook, the store and the runtime."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from yokai.rpg.runtime import RpgRuntime
from yokai.rpg.store import RpgStore
from yokai.storage.db import CURRENT_SCHEMA_VERSION, Database


async def fresh_db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "rpg_test.db")
    await db.initialize()
    return db


@pytest.mark.asyncio
async def test_schema_is_at_least_v9_and_has_the_characters_table(tmp_path: Path) -> None:
    db = await fresh_db(tmp_path)
    try:
        assert CURRENT_SCHEMA_VERSION >= 9
        async with db.conn.execute("PRAGMA user_version;") as cursor:
            row = await cursor.fetchone()
        assert row is not None and row[0] == CURRENT_SCHEMA_VERSION
        async with db.conn.execute("PRAGMA table_info(rpg_characters);") as cursor:
            columns = {r[1] for r in await cursor.fetchall()}
        assert columns == {
            "user_id",
            "level",
            "xp",
            "rank",
            "battles_won",
            "battles_lost",
            "created_ts",
        }
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_v8_database_upgrades_to_v9_without_losing_other_data(tmp_path: Path) -> None:
    db = await fresh_db(tmp_path)
    try:
        await db.credit_wallet(1, 50, "test", welcome_bonus=200)
        await db.conn.execute("DROP TABLE rpg_characters;")
        await db.conn.execute("PRAGMA user_version = 8;")
        await db.conn.commit()
    finally:
        await db.close()

    db = Database(tmp_path / "rpg_test.db")
    await db.initialize()
    try:
        wallet = await db.get_wallet(1)
        assert wallet is not None and wallet.balance == 250
        created = await RpgStore(db).create_character(1, "E", now=100)
        assert created[1] is True
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_transaction_commits_when_the_block_succeeds(tmp_path: Path) -> None:
    db = await fresh_db(tmp_path)
    try:
        async with db.transaction() as conn:
            await conn.execute(
                "INSERT INTO rpg_characters (user_id, rank, created_ts) VALUES (7, 'E', 1);"
            )
        assert await RpgStore(db).get_character(7) is not None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_transaction_rolls_back_when_the_block_raises(tmp_path: Path) -> None:
    db = await fresh_db(tmp_path)
    try:
        with pytest.raises(RuntimeError):
            async with db.transaction() as conn:
                await conn.execute(
                    "INSERT INTO rpg_characters (user_id, rank, created_ts) VALUES (7, 'E', 1);"
                )
                raise RuntimeError("boom")
        assert await RpgStore(db).get_character(7) is None
        # The write lock was released and no transaction was left open.
        await RpgStore(db).create_character(8, "E", now=1)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_get_character_returns_none_when_absent(tmp_path: Path) -> None:
    db = await fresh_db(tmp_path)
    try:
        assert await RpgStore(db).get_character(123) is None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_create_character_starts_at_level_one_in_the_given_rank(tmp_path: Path) -> None:
    db = await fresh_db(tmp_path)
    try:
        character, created = await RpgStore(db).create_character(5, "E", now=1234)
        assert created is True
        assert (character.user_id, character.level, character.xp) == (5, 1, 0)
        assert (character.rank, character.battles_won, character.battles_lost) == ("E", 0, 0)
        assert character.created_ts == 1234
        assert await RpgStore(db).get_character(5) == character
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_creating_twice_keeps_the_original_character(tmp_path: Path) -> None:
    db = await fresh_db(tmp_path)
    try:
        store = RpgStore(db)
        first, created_first = await store.create_character(5, "E", now=1000)
        second, created_second = await store.create_character(5, "D", now=2000)
        assert created_first is True and created_second is False
        assert second == first and second.rank == "E" and second.created_ts == 1000
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_simultaneous_joins_create_exactly_one_character(tmp_path: Path) -> None:
    """Review Focus 1: two /guild join calls at once must not make two rows or two 'created'."""
    db = await fresh_db(tmp_path)
    try:
        store = RpgStore(db)
        results = await asyncio.gather(*[store.create_character(9, "E", now=500) for _ in range(8)])
        assert sum(1 for _, created in results if created) == 1
        assert len({character for character, _ in results}) == 1
        async with db.conn.execute("SELECT COUNT(*) FROM rpg_characters;") as cursor:
            row = await cursor.fetchone()
        assert row is not None and row[0] == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_runtime_create_loads_shipped_content_and_the_store(tmp_path: Path) -> None:
    db = await fresh_db(tmp_path)
    try:
        runtime = RpgRuntime.create(db, Path("content/rpg"))
        assert runtime.content.ranks[0].id == "E"
        assert isinstance(runtime.store, RpgStore)
    finally:
        await db.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_rpg_store.py -q`
Expected: collection error `ModuleNotFoundError: No module named 'yokai.rpg.runtime'` (or `store`).

- [ ] **Step 3: Add the migration and the transaction hook to `src/yokai/storage/db.py`**

Change the constant:

```python
CURRENT_SCHEMA_VERSION = 9
```

Add directly after the `MIGRATION_V8` string:

```python
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
```

In `_migrate`, after the `if version < 8:` block, add:

```python
        if version < 9:
            logger.info("Applying SQLite migration v9...")
            await self.conn.executescript(MIGRATION_V9)
            await self.conn.execute("PRAGMA user_version = 9;")
            await self.conn.commit()
            logger.info("SQLite schema migrated to version 9.")
```

Directly after the `_write_tx` method (before `_ensure_wallet`), add the public hook:

```python
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
```

(`aiosqlite` and `AsyncIterator` are already imported at the top of `db.py`.)

- [ ] **Step 4: Implement the store and runtime**

Create `src/yokai/rpg/store.py`:

```python
"""Database access for the Guild RPG, on the shared SQLite connection."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Optional, Sequence

import aiosqlite

from yokai.rpg.models import Character

if TYPE_CHECKING:
    from yokai.storage.db import Database

_CHARACTER_COLUMNS = "user_id, level, xp, rank, battles_won, battles_lost, created_ts"


def _character_from_row(row: Sequence[Any]) -> Character:
    return Character(
        user_id=row[0],
        level=row[1],
        xp=row[2],
        rank=row[3],
        battles_won=row[4],
        battles_lost=row[5],
        created_ts=row[6],
    )


async def _fetch_character(conn: aiosqlite.Connection, user_id: int) -> Optional[Character]:
    query = f"SELECT {_CHARACTER_COLUMNS} FROM rpg_characters WHERE user_id = ?;"
    async with conn.execute(query, (user_id,)) as cursor:
        row = await cursor.fetchone()
    return _character_from_row(row) if row else None


class RpgStore:
    """Reads and writes RPG rows. Later phases add items, boards and fights here."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def get_character(self, user_id: int) -> Optional[Character]:
        """Read-only lookup; never creates a character."""
        return await _fetch_character(self._db.conn, user_id)

    async def create_character(
        self, user_id: int, start_rank: str, now: Optional[int] = None
    ) -> tuple[Character, bool]:
        """Create the character if absent. Returns (character, created).

        `created` is True only for the call that inserted the row, so simultaneous joins by one
        user produce exactly one character and exactly one True.
        """
        created_ts = int(time.time()) if now is None else now
        async with self._db.transaction() as conn:
            cursor = await conn.execute(
                "INSERT OR IGNORE INTO rpg_characters (user_id, rank, created_ts) "
                "VALUES (?, ?, ?);",
                (user_id, start_rank, created_ts),
            )
            created = cursor.rowcount == 1
            character = await _fetch_character(conn, user_id)
        assert character is not None
        return character, created
```

Create `src/yokai/rpg/runtime.py`:

```python
"""The RPG's loaded content and store, bundled so the bot and cogs share one object."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from yokai.rpg.content import Content, load_content
from yokai.rpg.store import RpgStore

if TYPE_CHECKING:
    from yokai.storage.db import Database


@dataclass(frozen=True, slots=True)
class RpgRuntime:
    """Everything the RPG cogs need. Created once at startup when RPG_ENABLED is true."""

    content: Content
    store: RpgStore

    @classmethod
    def create(cls, db: Database, content_dir: Path | str) -> RpgRuntime:
        """Load and validate the content files, then bind the store. Raises ContentError."""
        return cls(content=load_content(content_dir), store=RpgStore(db))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_rpg_store.py -q`
Expected: all PASS. Then run the whole suite to confirm the version bump broke nothing: `python -m pytest -q` (expected: all pass; tests compare against `CURRENT_SCHEMA_VERSION`, not a literal).

- [ ] **Step 6: Lint and commit**

```powershell
python -m ruff check src tests
python -m ruff format --check src tests
git add src/yokai/storage/db.py src/yokai/rpg tests/test_rpg_store.py
git commit -m "feat(rpg): add rpg_characters table, Database.transaction hook, store and runtime" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Error, theme, embeds and the preview samples

**Files:**
- Modify: `src/yokai/errors.py`, `src/yokai/theme.py`, `src/yokai/ui/embeds.py`, `scripts/preview_embeds.py`
- Regenerate: `docs/preview/embeds.html`
- Test: `tests/test_rpg_embeds.py`

**Interfaces:**
- Produces:
  - `yokai.errors.NoCharacterError(own: bool = True)`; title `"No adventurer yet"`; `.message` and `.user_hint` differ for `own=True` and `own=False`.
  - `Emoji.SWORDS` (`"⚔️"`) and the quip key `"guild_join"` (three variants).
  - `EmbedFactory.guild_joined(user_mention: str, rank: str, level: int, stats: Sequence[tuple[str, str]], bot_avatar_url: Optional[str] = None) -> discord.Embed`.
  - `EmbedFactory.player_card(user_mention: str, rank: str, level: int, level_cap: int, stats: Sequence[tuple[str, str]], wins: int, losses: int, joined_ts: int, next_rank: Optional[tuple[str, int]] = None, top_rank: bool = False, thumbnail_url: Optional[str] = None, bot_avatar_url: Optional[str] = None) -> discord.Embed`. A "Next promotion" field appears when `next_rank` is given (`(rank id, min level)`) or when `top_rank` is true; with neither it is omitted.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_rpg_embeds.py`:

```python
"""Tests for the Guild RPG embeds and the NoCharacterError."""

from __future__ import annotations

import discord

from tests.embed_helpers import emoji_count, total_embed_length
from yokai.errors import NoCharacterError, YokaiError
from yokai.theme import QUIP_VARIANTS, Emoji
from yokai.ui.embeds import EmbedFactory

STATS = [
    ("HP", "100"),
    ("Attack", "10"),
    ("Magic", "10"),
    ("Defense", "5"),
    ("Resist", "5"),
    ("Speed", "10"),
    ("Crit", "5%"),
]


def field_map(embed: discord.Embed) -> dict[str, str]:
    return {field.name: field.value for field in embed.fields}


def test_guild_join_quip_bank_has_at_least_three_variants() -> None:
    assert len(QUIP_VARIANTS["guild_join"]) >= 3
    assert Emoji.SWORDS


def test_guild_joined_embed_shape() -> None:
    embed = EmbedFactory.guild_joined("<@42>", "E", 1, STATS)
    assert "<@42>" in (embed.description or "")
    assert "rank E" in (embed.description or "")
    assert "/card" in (embed.description or "")
    fields = field_map(embed)
    for label, value in STATS:
        assert fields[label] == f"**{value}**"
    assert emoji_count(embed) == 1
    assert total_embed_length(embed) <= 6000


def test_player_card_shows_rank_level_record_and_stats() -> None:
    embed = EmbedFactory.player_card(
        "<@42>", "C", 14, 60, STATS, 31, 9, 1_700_000_000, next_rank=("B", 20)
    )
    fields = field_map(embed)
    assert fields["Rank"] == "**C**"
    assert fields["Level"] == "**14** / 60"
    assert "**31**W" in fields["Record"] and "**9**L" in fields["Record"]
    assert fields["HP"] == "**100**" and fields["Crit"] == "**5%**"
    assert fields["Next promotion"] == "Rank **B** at level **20**"
    assert "<t:1700000000:D>" in (embed.description or "")
    assert "<@42>" in (embed.description or "")
    assert emoji_count(embed) == 1
    assert total_embed_length(embed) <= 6000
    assert len(embed.fields) <= 25


def test_player_card_at_the_top_rank_says_so() -> None:
    embed = EmbedFactory.player_card("<@42>", "SS+", 60, 60, STATS, 0, 0, 1, top_rank=True)
    assert field_map(embed)["Next promotion"] == "Top rank reached."


def test_player_card_omits_the_promotion_field_when_the_rank_is_unknown() -> None:
    embed = EmbedFactory.player_card("<@42>", "ZZ", 3, 60, STATS, 0, 0, 1)
    assert "Next promotion" not in field_map(embed)


def test_player_card_escapes_markdown_in_the_rank() -> None:
    embed = EmbedFactory.player_card("<@42>", "A*B", 3, 60, STATS, 0, 0, 1)
    assert field_map(embed)["Rank"] == "**A\\*B**"


def test_player_card_thumbnail_is_optional() -> None:
    plain = EmbedFactory.player_card("<@42>", "E", 1, 60, STATS, 0, 0, 1)
    assert plain.thumbnail.url is None
    shown = EmbedFactory.player_card(
        "<@42>", "E", 1, 60, STATS, 0, 0, 1, thumbnail_url="https://img.example/a.png"
    )
    assert shown.thumbnail.url == "https://img.example/a.png"


def test_no_character_error_wording_differs_for_self_and_others() -> None:
    mine = NoCharacterError(own=True)
    theirs = NoCharacterError(own=False)
    assert isinstance(mine, YokaiError) and mine.title == "No adventurer yet"
    assert "You haven't joined" in mine.message and "/guild join" in (mine.user_hint or "")
    assert "That member" in theirs.message and "/guild join" in (theirs.user_hint or "")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_rpg_embeds.py -q`
Expected: FAIL (`ImportError: cannot import name 'NoCharacterError'`).

- [ ] **Step 3: Add the error**

Append to the end of `src/yokai/errors.py`:

```python
class NoCharacterError(YokaiError):
    """Raised when someone looks up a guild character that does not exist."""

    title = "No adventurer yet"

    def __init__(self, own: bool = True) -> None:
        if own:
            super().__init__(
                "You haven't joined the adventurers' guild yet.",
                user_hint="Run /guild join to create your character.",
            )
        else:
            super().__init__(
                "That member hasn't joined the adventurers' guild yet.",
                user_hint="They can run /guild join to create a character.",
            )
```

- [ ] **Step 4: Add the emoji and the quips**

In `src/yokai/theme.py`, add to `class Emoji` after `DICE`:

```python
    SWORDS: Final[str] = "⚔️"
```

In `QUIP_VARIANTS`, add after the `"game_bust"` entry:

```python
    "guild_join": (
        "Signed in. Don't make me regret it.",
        "Your name's on the roster now.",
        "Another recruit. Let's see what you've got.",
    ),
```

- [ ] **Step 5: Add the embeds**

In `src/yokai/ui/embeds.py`, add these two classmethods to `EmbedFactory` directly after `economy_admin` (before `spinning`):

```python
    @classmethod
    def guild_joined(
        cls,
        user_mention: str,
        rank: str,
        level: int,
        stats: Sequence[tuple[str, str]],  # (label, display value)
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        fields: list[FieldSpec] = [(label, f"**{value}**", True) for label, value in stats]
        return cls._create_base(
            title=f"{Emoji.SWORDS} Welcome to the guild",
            description=(
                f"{user_mention} signed the roster as a **rank {escape_md(rank)}** adventurer "
                f"at level **{level}**. Use /card to see your adventurer card."
            ),
            color=COLOR_SUCCESS,
            context="Guild",
            bot_avatar_url=bot_avatar_url,
            quip=quip_bank.get("guild_join"),
            fields=_grid(fields),
        )

    @classmethod
    def player_card(
        cls,
        user_mention: str,
        rank: str,
        level: int,
        level_cap: int,
        stats: Sequence[tuple[str, str]],  # (label, display value)
        wins: int,
        losses: int,
        joined_ts: int,
        next_rank: Optional[tuple[str, int]] = None,  # (rank id, level it opens at)
        top_rank: bool = False,
        thumbnail_url: Optional[str] = None,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        fields: list[FieldSpec] = [
            ("Rank", f"**{escape_md(rank)}**", True),
            ("Level", f"**{level}** / {level_cap}", True),
            ("Record", f"**{wins:,}**W{SEP}**{losses:,}**L", True),
        ]
        fields.extend((label, f"**{value}**", True) for label, value in stats)
        if next_rank is not None:
            next_id, next_level = next_rank
            promotion = f"Rank **{escape_md(next_id)}** at level **{next_level}**"
            fields.append(("Next promotion", promotion, False))
        elif top_rank:
            fields.append(("Next promotion", "Top rank reached.", False))
        return cls._create_base(
            title=f"{Emoji.SWORDS} Adventurer card",
            description=f"{user_mention}{SEP}joined <t:{joined_ts}:D>",
            color=COLOR_BRAND,
            context="Guild",
            bot_avatar_url=bot_avatar_url,
            thumbnail_url=thumbnail_url,
            fields=_grid(fields),
        )
```

- [ ] **Step 6: Add the preview samples**

In `scripts/preview_embeds.py`, inside `build_samples()` immediately before the line `samples: list[tuple[str, discord.Embed]] = [`, add:

```python
    guild_stats = [
        ("HP", "100"),
        ("Attack", "10"),
        ("Magic", "10"),
        ("Defense", "5"),
        ("Resist", "5"),
        ("Speed", "10"),
        ("Crit", "5%"),
    ]
```

and append these two entries to the end of the `samples` list, after the `"Lottery (rollover)"` entry:

```python
        (
            "Guild (joined)",
            EmbedFactory.guild_joined("<@42>", "E", 1, guild_stats, bot_avatar_url=av),
        ),
        (
            "Guild (player card)",
            EmbedFactory.player_card(
                "<@42>",
                "C",
                14,
                60,
                guild_stats,
                31,
                9,
                1_700_000_000,
                next_rank=("B", 20),
                bot_avatar_url=av,
            ),
        ),
```

- [ ] **Step 7: Run the tests to verify they pass, regenerate the preview, commit**

```powershell
python -m pytest tests/test_rpg_embeds.py tests/test_preview_embeds.py tests/test_theme.py tests/test_errors.py tests/test_embeds.py -q
python scripts/preview_embeds.py
python -m ruff check src tests scripts
python -m ruff format --check src tests scripts
git add src/yokai/errors.py src/yokai/theme.py src/yokai/ui/embeds.py scripts/preview_embeds.py docs/preview/embeds.html tests/test_rpg_embeds.py
git commit -m "feat(rpg): add guild join and player card embeds, NoCharacterError and persona quips" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```
Expected: all PASS. If a test in `test_errors.py` or `test_theme.py` enumerates all errors or quips and rejects the new ones, fix the new code to satisfy the rule (for example a title length limit), not the rule.

---

### Task 6: The cog, bot wiring and cog tests

**Files:**
- Create: `src/yokai/cogs/rpg.py`
- Modify: `src/yokai/bot.py`, `tests/test_one_emoji_rule.py`
- Test: `tests/test_rpg_cog.py`

**Interfaces:**
- Consumes: `RpgRuntime`, `RpgStore`, `Content` (Tasks 3 and 4), `stats_at_level`/`format_stats` (Task 2), `EmbedFactory.guild_joined`/`player_card`, `NoCharacterError` (Task 5), `Config.rpg_enabled`/`rpg_content_dir` (Task 1).
- Produces: `RpgCog` with the group `/guild` (command `join`) and the command `/card [member]`; `YokaiBot.rpg: Optional[RpgRuntime]`; an `async def setup(bot)` extension entry point in `yokai.cogs.rpg`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_rpg_cog.py`:

```python
"""Tests for the /guild join and /card commands."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest
from discord.ext import commands

from yokai.cogs.rpg import RpgCog
from yokai.rpg.runtime import RpgRuntime
from yokai.storage.db import Database


async def make_runtime(tmp_path: Path) -> tuple[Database, RpgRuntime]:
    db = Database(tmp_path / "rpg_cog.db")
    await db.initialize()
    return db, RpgRuntime.create(db, Path("content/rpg"))


def make_bot(runtime: RpgRuntime) -> MagicMock:
    bot = MagicMock()
    bot.user = None
    bot.rpg = runtime
    return bot


def make_interaction(user_id: int = 111) -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    user.bot = False
    interaction.user = user
    interaction.response.defer = AsyncMock()
    return interaction


def make_member(user_id: int, is_bot: bool = False) -> MagicMock:
    member = MagicMock(spec=discord.Member)
    member.id = user_id
    member.bot = is_bot
    return member


@pytest.mark.asyncio
async def test_join_creates_the_character_and_replies_publicly(tmp_path: Path) -> None:
    db, runtime = await make_runtime(tmp_path)
    try:
        cog = RpgCog(make_bot(runtime))
        interaction = make_interaction(111)
        with patch("yokai.cogs.rpg.send", new=AsyncMock()) as sent:
            await cog.join.callback(cog, interaction)

        interaction.response.defer.assert_awaited_once()
        embed = sent.await_args.kwargs["embed"]
        assert "<@111>" in (embed.description or "")
        assert "rank E" in (embed.description or "")
        assert sent.await_args.kwargs.get("ephemeral", False) is False
        character = await runtime.store.get_character(111)
        assert character is not None and character.rank == "E" and character.level == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_join_twice_replies_ephemerally_without_deferring(tmp_path: Path) -> None:
    db, runtime = await make_runtime(tmp_path)
    try:
        await runtime.store.create_character(111, "E", now=1)
        cog = RpgCog(make_bot(runtime))
        interaction = make_interaction(111)
        with patch("yokai.cogs.rpg.send", new=AsyncMock()) as sent:
            await cog.join.callback(cog, interaction)

        interaction.response.defer.assert_not_awaited()
        assert sent.await_args.kwargs["ephemeral"] is True
        assert "Already on the roster" in (sent.await_args.kwargs["embed"].title or "")
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_card_for_yourself_without_a_character_is_an_ephemeral_error(
    tmp_path: Path,
) -> None:
    db, runtime = await make_runtime(tmp_path)
    try:
        cog = RpgCog(make_bot(runtime))
        interaction = make_interaction(111)
        with patch("yokai.cogs.rpg.send", new=AsyncMock()) as sent:
            await cog.card.callback(cog, interaction, member=None)

        interaction.response.defer.assert_not_awaited()
        assert sent.await_args.kwargs["ephemeral"] is True
        embed = sent.await_args.kwargs["embed"]
        assert "No adventurer yet" in (embed.title or "")
        assert "You haven't joined" in (embed.description or "")
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_card_for_another_member_is_public_and_shows_their_stats(tmp_path: Path) -> None:
    db, runtime = await make_runtime(tmp_path)
    try:
        await runtime.store.create_character(222, "E", now=1_700_000_000)
        cog = RpgCog(make_bot(runtime))
        interaction = make_interaction(111)
        with patch("yokai.cogs.rpg.send", new=AsyncMock()) as sent:
            await cog.card.callback(cog, interaction, member=make_member(222))

        interaction.response.defer.assert_awaited_once()
        assert sent.await_args.kwargs.get("ephemeral", False) is False
        embed = sent.await_args.kwargs["embed"]
        names = {field.name: field.value for field in embed.fields}
        assert "<@222>" in (embed.description or "")
        assert names["Rank"] == "**E**" and names["Level"] == "**1** / 60"
        assert names["HP"] == "**100**"
        assert names["Next promotion"] == "Rank **D** at level **5**"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_card_for_a_member_without_a_character_uses_the_third_person(
    tmp_path: Path,
) -> None:
    db, runtime = await make_runtime(tmp_path)
    try:
        cog = RpgCog(make_bot(runtime))
        interaction = make_interaction(111)
        with patch("yokai.cogs.rpg.send", new=AsyncMock()) as sent:
            await cog.card.callback(cog, interaction, member=make_member(333))

        interaction.response.defer.assert_not_awaited()
        assert sent.await_args.kwargs["ephemeral"] is True
        assert "That member" in (sent.await_args.kwargs["embed"].description or "")
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_card_for_a_bot_account_is_an_ephemeral_error(tmp_path: Path) -> None:
    """Review Focus 5: a bot can never have a character, and asking must not crash."""
    db, runtime = await make_runtime(tmp_path)
    try:
        cog = RpgCog(make_bot(runtime))
        interaction = make_interaction(111)
        with patch("yokai.cogs.rpg.send", new=AsyncMock()) as sent:
            await cog.card.callback(cog, interaction, member=make_member(444, is_bot=True))

        interaction.response.defer.assert_not_awaited()
        assert sent.await_args.kwargs["ephemeral"] is True
        assert "No adventurer yet" in (sent.await_args.kwargs["embed"].title or "")
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_card_survives_a_rank_that_is_missing_from_the_content(tmp_path: Path) -> None:
    """Review Focus 2: content edited after a character was created must not crash /card."""
    db, runtime = await make_runtime(tmp_path)
    try:
        await runtime.store.create_character(555, "RETIRED-RANK", now=1)
        cog = RpgCog(make_bot(runtime))
        interaction = make_interaction(111)
        with patch("yokai.cogs.rpg.send", new=AsyncMock()) as sent:
            await cog.card.callback(cog, interaction, member=make_member(555))

        embed = sent.await_args.kwargs["embed"]
        names = {field.name: field.value for field in embed.fields}
        assert names["Rank"] == "**RETIRED-RANK**"
        assert "Next promotion" not in names
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_card_clamps_a_level_outside_the_content_range(tmp_path: Path) -> None:
    """Review Focus 4: a stored level above the cap still shows sane stats."""
    db, runtime = await make_runtime(tmp_path)
    try:
        await runtime.store.create_character(666, "E", now=1)
        async with db.transaction() as conn:
            await conn.execute("UPDATE rpg_characters SET level = 9999 WHERE user_id = 666;")
        cog = RpgCog(make_bot(runtime))
        interaction = make_interaction(111)
        with patch("yokai.cogs.rpg.send", new=AsyncMock()) as sent:
            await cog.card.callback(cog, interaction, member=make_member(666))

        names = {field.name: field.value for field in sent.await_args.kwargs["embed"].fields}
        # Level 60 is the cap: 100 + 12 * 59 = 808 HP.
        assert names["HP"] == "**808**"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_the_cog_registers_guild_join_and_card(tmp_path: Path) -> None:
    db, runtime = await make_runtime(tmp_path)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    try:
        bot.rpg = runtime  # type: ignore[attr-defined]
        await bot.add_cog(RpgCog(bot))  # type: ignore[arg-type]
        names = {command.qualified_name for command in bot.tree.walk_commands()}
        assert {"guild join", "card"} <= names
    finally:
        await bot.close()
        await db.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_rpg_cog.py -q`
Expected: collection error `ModuleNotFoundError: No module named 'yokai.cogs.rpg'`.

- [ ] **Step 3: Implement the cog**

Create `src/yokai/cogs/rpg.py`:

```python
"""Guild RPG slash commands: /guild join and /card (more arrive in later phases)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

from yokai.errors import NoCharacterError
from yokai.rpg.content import Content
from yokai.rpg.runtime import RpgRuntime
from yokai.rpg.stats import format_stats, stats_at_level
from yokai.ui.embeds import EmbedFactory, send

if TYPE_CHECKING:
    from yokai.bot import YokaiBot

logger = logging.getLogger(__name__)


def _avatar_of(member: discord.abc.User) -> Optional[str]:
    """The member's avatar URL, or None when it is not a plain string (tests, odd objects)."""
    url = getattr(getattr(member, "display_avatar", None), "url", None)
    return url if isinstance(url, str) else None


def _promotion(content: Content, rank_id: str) -> tuple[Optional[tuple[str, int]], bool]:
    """(next rank as (id, min level), reached the top). Both empty if the rank is unknown."""
    try:
        upcoming = content.next_rank(rank_id)
    except KeyError:
        logger.warning("A character has rank %r, which is not in the content files.", rank_id)
        return None, False
    if upcoming is None:
        return None, True
    return (upcoming.id, upcoming.min_level), False


class RpgCog(commands.Cog, name="Guild"):
    """The adventurers' guild: create a character and view player cards."""

    guild_group = app_commands.Group(name="guild", description="The adventurers' guild.")

    def __init__(self, bot: YokaiBot) -> None:
        self.bot = bot

    def _avatar_url(self) -> Optional[str]:
        return str(self.bot.user.display_avatar.url) if self.bot.user else None

    def _runtime(self) -> RpgRuntime:
        runtime = self.bot.rpg
        if runtime is None:
            raise RuntimeError("The Guild cog was loaded without an RPG runtime.")
        return runtime

    async def _send_domain_error(self, interaction: discord.Interaction, err: Exception) -> None:
        title = getattr(err, "title", "That didn't work")
        message = getattr(err, "message", str(err))
        hint = getattr(err, "user_hint", None)
        embed = EmbedFactory.error(
            message=message,
            title=title,
            user_hint=hint,
            context="Guild",
            bot_avatar_url=self._avatar_url(),
        )
        await send(interaction, embed=embed, ephemeral=True)

    @guild_group.command(name="join", description="Join the adventurers' guild.")
    async def join(self, interaction: discord.Interaction) -> None:
        """Joining twice is answered before deferring so it stays ephemeral."""
        runtime = self._runtime()
        user_id = interaction.user.id
        if await runtime.store.get_character(user_id) is not None:
            embed = EmbedFactory.info(
                title="Already on the roster",
                description="You've already joined the guild. Use /card to see your card.",
                context="Guild",
                bot_avatar_url=self._avatar_url(),
            )
            await send(interaction, embed=embed, ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        character, _ = await runtime.store.create_character(user_id, runtime.content.ranks[0].id)
        stats = stats_at_level(runtime.content.levels, character.level)
        embed = EmbedFactory.guild_joined(
            user_mention=f"<@{user_id}>",
            rank=character.rank,
            level=character.level,
            stats=format_stats(stats),
            bot_avatar_url=self._avatar_url(),
        )
        await send(interaction, embed=embed)

    @app_commands.command(name="card", description="Show an adventurer's player card.")
    @app_commands.describe(member="Whose card to show (defaults to you)")
    async def card(
        self, interaction: discord.Interaction, member: Optional[discord.Member] = None
    ) -> None:
        """Anyone can view anyone's card. Missing characters are answered before deferring."""
        runtime = self._runtime()
        target = member or interaction.user
        character = None if target.bot else await runtime.store.get_character(target.id)
        if character is None:
            await self._send_domain_error(
                interaction, NoCharacterError(own=target.id == interaction.user.id)
            )
            return

        await interaction.response.defer(thinking=True)
        content = runtime.content
        next_rank, top_rank = _promotion(content, character.rank)
        embed = EmbedFactory.player_card(
            user_mention=f"<@{target.id}>",
            rank=character.rank,
            level=character.level,
            level_cap=content.levels.cap,
            stats=format_stats(stats_at_level(content.levels, character.level)),
            wins=character.battles_won,
            losses=character.battles_lost,
            joined_ts=character.created_ts,
            next_rank=next_rank,
            top_rank=top_rank,
            thumbnail_url=_avatar_of(target),
            bot_avatar_url=self._avatar_url(),
        )
        await send(interaction, embed=embed)


async def setup(bot: YokaiBot) -> None:
    """Extension setup entrypoint."""
    await bot.add_cog(RpgCog(bot))
```

- [ ] **Step 4: Wire the cog into the bot**

In `src/yokai/bot.py`:

1. Add imports in the sorted `yokai.*` import block (after `from yokai.presence import ...`, before `from yokai.storage.db import Database`):

```python
from yokai.rpg.content import ContentError
from yokai.rpg.runtime import RpgRuntime
```

2. In `YokaiBot.__init__`, directly after `self.db = Database(config.db_path)` add:

```python
        self.rpg: Optional[RpgRuntime] = None
```

3. In `setup_hook`, directly after the `refund_open_bets` block (before `await self.audio_buffer.start()`), add:

```python
        if self.config.rpg_enabled:
            try:
                self.rpg = RpgRuntime.create(self.db, self.config.rpg_content_dir)
            except ContentError as err:
                logger.error("Guild RPG content is invalid, refusing to start: %s", err)
                raise
            logger.info("Guild RPG content loaded from %s", self.config.rpg_content_dir)
        else:
            logger.info("Guild RPG is disabled (RPG_ENABLED=false).")
```

4. After `await self.load_extension("yokai.cogs.challenges")` add:

```python
        if self.rpg is not None:
            await self.load_extension("yokai.cogs.rpg")
```

- [ ] **Step 5: Add the cog to the one-emoji rule**

In `tests/test_one_emoji_rule.py`, add `"cogs/rpg.py",` to the `CHECKED` list after `"cogs/lottery.py",`.

- [ ] **Step 6: Run the tests to verify they pass**

```powershell
python -m pytest tests/test_rpg_cog.py tests/test_one_emoji_rule.py tests/test_code_standards.py -q
```
Expected: all PASS. If `cog.join.callback(...)` raises because `cog.join` is not a command object, call `RpgCog.join.callback(cog, interaction)` instead (the class attribute is the `app_commands.Command`), and apply the same change to `card`.

- [ ] **Step 7: Lint and commit**

```powershell
python -m ruff check src tests
python -m ruff format --check src tests
git add src/yokai/cogs/rpg.py src/yokai/bot.py tests/test_rpg_cog.py tests/test_one_emoji_rule.py
git commit -m "feat(rpg): add /guild join and /card commands and wire the RPG runtime into the bot" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Docs, spec delta and final verification

**Files:**
- Modify: `README.md`, `QA.md`, `docs/superpowers/specs/2026-10-14-guild-rpg-design.md`

**Interfaces:**
- Consumes: everything above. Produces: documentation only.

- [ ] **Step 1: README section**

In `README.md`, insert this section directly before `## Updating yt-dlp`:

```markdown
## Guild RPG (in progress)

An Adventurers' Guild RPG is being built in phases. **Phase 1 (foundation)** adds the character and the player card; missions, gear, ranks, parties and trading arrive in later phases (see `docs/superpowers/specs/2026-10-14-guild-rpg-design.md`).

- `/guild join` creates your adventurer (rank E, level 1) and shows your starting stats.
- `/card [member]` shows anyone's adventurer card: rank, level, record, the seven stats (HP, Attack, Magic, Defense, Resist, Speed, Crit) and the level where the next promotion opens.

Game data (levels, ranks, gear tags) lives in TOML files in `content/rpg/` and is validated at startup; a bad file stops the bot with a message naming the file and the problem. The numbers in those files are placeholders that later phases tune. Settings in `.env`:

| Variable | Default | Meaning |
|---|---|---|
| `RPG_ENABLED` | `true` | Master switch. When false the `/guild` and `/card` commands are not registered. |
| `RPG_CONTENT_DIR` | `content/rpg` | Folder with the RPG content files, relative to where the bot is started. |
```

- [ ] **Step 2: QA checklist rows**

In `QA.md`, change the intro sentence "This document details the 33 manual verification procedures" to say 36, and add these rows after row 33:

```markdown
| **34** | **Guild Join** | • `/guild join` creates a character and shows a "Welcome to the guild" embed with rank E, level 1 and the seven stats (HP 100, Attack 10, Magic 10, Defense 5, Resist 5, Speed 10, Crit 5%).<br>• The reply is visible to the channel and has exactly one emoji.<br>• Running it again answers privately with "Already on the roster". | 1. Run `/guild join` with an account that has not joined.<br>2. Check the embed fields and the single emoji.<br>3. Run `/guild join` again and confirm only you see the reply. | `Pending Owner Run` |
| **35** | **Player Card** | • `/card` shows your own card; `/card member:@friend` shows a friend's (visible to the channel).<br>• Fields: Rank, Level (`1 / 60`), Record, seven stats, and "Next promotion: Rank D at level 5".<br>• A member who has not joined, and a bot account, produce a private "No adventurer yet" error. | 1. Run `/card`.<br>2. Run `/card` with another member who has joined, then one who has not, then a bot.<br>3. Check each reply and who can see it. | `Pending Owner Run` |
| **36** | **RPG Disabled and Bad Content** | • With `RPG_ENABLED=false` and a restart, `/guild` and `/card` are absent from the command list and the rest of the bot works.<br>• With `RPG_ENABLED=true` and a broken file in `content/rpg/` (for example delete a closing bracket in `tags.toml`), the bot refuses to start and the log names `tags.toml` and the problem. | 1. Set `RPG_ENABLED=false`, restart, type `/guild` in Discord.<br>2. Set it back to `true`, damage `content/rpg/tags.toml`, start the bot, read the log, then restore the file with `git checkout content/rpg/tags.toml`. | `Pending Owner Run` |
```

- [ ] **Step 3: Update the spec with what phase 1 changed**

In `docs/superpowers/specs/2026-10-14-guild-rpg-design.md`:

1. In section 13 (Configuration), add a row to the table:

```markdown
| `RPG_CONTENT_DIR` | `content/rpg` | Folder with the TOML content files, relative to where the bot starts. |
```

2. At the end of section 5 (Data model), add a paragraph:

```markdown
Phase 1 creates `rpg_characters` with only `user_id, level, xp, rank, battles_won, battles_lost,
created_ts`. The attempt, streak and cooldown columns shown above are added by the phase that first
uses them, with `_add_column_if_missing`. The public transaction hook is `Database.transaction()`.
```

3. At the end of section 12 (Commands), add: `The /admin diag RPG check arrives with phase 2, when fights first need thread permissions.`

- [ ] **Step 4: Full verification**

```powershell
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
python -c "from yokai.rpg.content import load_content; c = load_content('content/rpg'); print(len(c.tags), [r.id for r in c.ranks])"
git status --short
```
Expected: all tests pass, ruff clean, the one-liner prints `8 ['E', 'D', 'C', 'B', 'A', 'SS', 'SS+']`, and the working tree has only the doc changes from this task. Copy the real counts from the pytest and ruff output into the "Automated Test Baseline" block at the bottom of `QA.md` (the `N passed` line and the `N files already formatted` line); do not guess them.

- [ ] **Step 5: Smoke-test the migration and cog load against a throwaway database**

```powershell
python -c "import asyncio, tempfile, pathlib; from yokai.storage.db import Database, CURRENT_SCHEMA_VERSION; from yokai.rpg.runtime import RpgRuntime; exec('async def main():\n    d = pathlib.Path(tempfile.mkdtemp()) / \"smoke.db\"\n    db = Database(d)\n    await db.initialize()\n    rt = RpgRuntime.create(db, \"content/rpg\")\n    c, created = await rt.store.create_character(1, rt.content.ranks[0].id)\n    print(CURRENT_SCHEMA_VERSION, created, c.rank, c.level)\n    await db.close()\nasyncio.run(main())')"
```
Expected output: `9 True E 1`.

- [ ] **Step 6: Commit**

```powershell
git add README.md QA.md docs/superpowers/specs/2026-10-14-guild-rpg-design.md
git commit -m "docs(rpg): document phase 1, record QA rows and update the spec" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 7: Stop and report**

Per AGENTS.md, stop here. Summarize for the owner: what changed (files and commits), the verification output, anything unverified (the Discord-side behaviour in QA rows 34 to 36, which needs a real server), and the checkpoint tag `checkpoint/rpg-p1-start` to revert to. Do not start phase 2 until the owner says to.

---

## Deferred to later phases (so phase 1 stays small)

- Attempt, streak and promotion-cooldown columns, the daily board and shop tables, and the fight log (phase 2 onward).
- `/admin diag` RPG check and the `/admin rpg` owner tools (phase 2 and later).
- Item, equipment, tag bonus and skill content files and their validators (phase 3).
- The XP curve and the pacing simulator (phase 2).

