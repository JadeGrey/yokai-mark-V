# Admin Panel P-A2: Panel Core and Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A runnable local admin panel (`python -m yokai_admin`) with a secure shell, a module contract that future features plug into, and one module, the Dashboard, that shows whether the bot is online and a few vital signs.

**Architecture:** A FastAPI app bound to 127.0.0.1 serves server-rendered Jinja2 pages with HTMX for polling. A pure-ASGI security middleware gates every route (Host check, one-time token to session cookie, Origin and CSRF checks, strict CSP), so a module cannot forget auth. Modules are packages that export an `AdminModule`; the registry loads them from `"package.module:NAME"` strings so one that fails to import is isolated and shown as errored. The panel reads the bot's SQLite file through a read-only `sqlite3` connection and never runs migrations.

**Tech Stack:** Python 3.12+, FastAPI 0.142 / Starlette 1.7, uvicorn 0.54, Jinja2 3.1, HTMX 2.x (vendored), Archivo variable font (vendored, OFL), stdlib `sqlite3`, pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-10-05-admin-panel-design.md` (§2 locked decisions, §3 layout, §4 module contract, §7 Dashboard, §8 security, §9 configuration, §11 phase P-A2). P-A1 is already merged: schema v4, `bot_heartbeat`, `ModerationStore`.

## Deltas from the spec (owner approves these by approving this plan)

1. `AdminModule` has **no `icon` field** (the rail is text-only on purpose) and **gains `templates_dir`** so each module owns its templates.
2. The registry is a tuple of `"package.module:NAME"` **strings**, not imported objects, so an import failure becomes an "errored" nav entry instead of crashing startup. Duplicate ids still fail fast.
3. **New vendored assets:** `htmx.min.js` (already approved as part of the stack) and the **Archivo variable font** (SIL OFL 1.1, 90 KB) so the panel makes no outbound requests at run time. If you decline the font, delete `static/fonts/` and the `@font-face` block; the CSS already falls back to system fonts.
4. `python-multipart` is **not** added now; nothing in P-A2 parses a form. It arrives with P-A3 (first form).
5. The Dashboard polls every 5 s with HTMX; each poll plays one sonar ping (no endless animation).
6. Starlette 1.7's `TestClient` prints a deprecation warning suggesting `httpx2`. It is only a warning; the project already depends on `httpx`, so no new dependency is added.

## Global Constraints

- Python 3.12+; every module starts with `from __future__ import annotations`; fully type-hinted; dataclasses for models.
- Use `logging`; no `print`; no bare `except` (catch specific errors; the one deliberate `except Exception` is the module-import isolation boundary and is commented).
- The panel binds **127.0.0.1 only** (`HOST` is a constant, not configurable), never needs `DISCORD_TOKEN`, and makes **no outbound network calls** at run time.
- Never log tokens, cookies or the environment. The single-use launch URL is printed to the **console only** (never a file handler).
- SQL is parameterized; the panel opens the database read-only (`mode=ro`) in this phase.
- Panel copy is plain, sentence case, no emoji, errors say what happened and what to do.
- Never commit `.env`, tokens, databases or cookie files. Commit per task.
- New dependencies approved by the spec: `fastapi`, `uvicorn`, `jinja2`. Anything else needs owner approval.
- Tests: tests that touch SQLite through `Database` use a closing fixture (see P-A1 ruling) so a failure cannot hang pytest. The default run makes no network calls (the one real-server smoke test binds loopback only).
- Verification (PowerShell, repo root): `python -m pytest -q`, `python -m ruff check .`, `python -m ruff format --check .`.
- Git commits end with: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`

## Verified facts this plan relies on

Checked in a throwaway venv on Python 3.14 (nothing installed in the repo):
- `pip install fastapi uvicorn jinja2 httpx` works on 3.14.0 (pydantic-core 2.46.5 has wheels).
- Starlette 1.7.0: `Jinja2Templates.TemplateResponse(request, name, context)` (request first); `Jinja2Templates(directory=[dir_a, dir_b])` accepts several directories and autoescapes `.html`; `TrustedHostMiddleware(app, allowed_hosts, www_redirect)` strips the port before matching and answers 400 for other hosts; `Response.set_cookie(..., httponly, samesite="strict")` works; `FastAPI(lifespan=...)` startup/shutdown run inside `with TestClient(...)`; `docs_url=None, redoc_url=None, openapi_url=None` gives 404.
- jsDelivr serves `htmx.org@2` (52 KB) and `@fontsource-variable/archivo` v5.3.0 (`archivo-latin-wdth-normal.woff2`, 90 KB, license OFL-1.1).
- Existing code facts: `yokai.config.Config` is a frozen dataclass whose tests use `patch.dict(os.environ, ..., clear=True)` plus `env_path=Path("non_existent_env")`; `yokai/__main__.py` hard-codes `log_dir="logs"`; the bot's log lines look like `2026-10-05 12:00:00 [ERROR] name: message`; `Database.write_heartbeat(pid, version, now)` exists; `bot_heartbeat` has one row (`id = 1`).

## Design brief (frontend-design, two passes)

**Subject / audience / job.** The owner's operator console for Yokai, a bot whose persona is Echo's recon drone: it hovers, listens, and pings. One person, on their own PC, opening the panel mostly to answer one question ("is it alive?") and then to fix something. The panel should feel like the console of a quiet, competent instrument, not a SaaS dashboard.

**Pass 1: token plan.**
- *Color* (light, with an automatic dark variant): Hull `#EEF1F3` (ground, the drone's shell), Panel `#F8FAFB` (rail), Ink `#13212C`, Slate `#4F606D` (secondary text), Line `#BCC6CE`, Sonar `#08697D` (the one accent: pings, links, focus), plus status colors Flare `#B5361F`, Lichen `#25704A`, Amber `#8A5200`. Dark: Hull `#0F181F`, Panel `#15202A`, Ink `#E6EDF2`, Slate `#97A8B5`, Line `#3A4A57`, Sonar `#43C1DA`, Flare `#F27A63`, Lichen `#62C48F`, Amber `#E8AB45`. Every text pair was measured: all text colors are at least 5.2:1 on Hull and Panel in both themes.
- *Type:* one family, **Archivo variable**, used for its **width axis**: heading and the status word in condensed (62-75% width, 700-800 weight), body at normal width 400/600. Data (log lines) in the system mono stack. Scale: 13 / 15 / 18 / 24 px, and the status word at `clamp(2.75rem, 7vw, 4.5rem)`. Tabular numerals everywhere.
- *Layout:* left-aligned throughout. A 13.5 rem rail (wordmark and module list) beside a main column capped at 62 rem. No cards: content is one hero sentence, then ledger rows (definition lists) and a log excerpt.

```
+-----------+------------------------------------------------+
| Yokai     |  (o)  Online                                   |
| Admin     |       Last heartbeat 12 seconds ago.           |
| panel     |                                                |
|           |  Database size            2.41 MB              |
| Dashboard |  Schema version           4                    |
|  (active) |  Wallets                  9                    |
| Users     |  Commands in last 24 h    143                  |
| ...       |                                                |
|           |  Recent errors                                 |
|           |  | 2026-10-05 12:00:00 [ERROR] yokai.bot: ...  |
+-----------+------------------------------------------------+
```
- *Principles:* spend the boldness once, on the status. A big condensed word and a sonar mark whose ring plays once per poll, so the animation is real telemetry, not decoration. Everything else is quiet.

**Pass 2: review against the brief, and what changed.**
1. First instinct was a near-black ground with a neon cyan accent (the "dark + one bright accent" default). Changed to a light, cool-grey ground with a deep teal accent; dark is only an automatic variant.
2. First instinct was a grid of stat cards with big numbers. Changed to one hero sentence plus ledger rows, no cards, no gradients.
3. First instinct was small all-caps labels, mono data labels and an icon rail. Removed: labels are sentence case in the body face; the rail is text only.
4. First instinct was an endless pulsing dot. Changed to one ring per poll (every 5 s), and none when offline or when the user prefers reduced motion.
5. Status is never color alone: ok, warn and error markers differ in shape (circle, diamond, square) and carry hidden text.

## Review Focus

Failure modes the spec implies but happy-path tests would miss, most likely first. Each has a test in the task named.

1. A request with a non-loopback `Host` header (DNS rebinding) is refused even with a valid session cookie (Task 4).
2. A new module that never thinks about auth: its routes are locked by default, and its HTMX/POST routes need the CSRF header (Tasks 4 and 6).
3. Hostile text in a log line, in a module path, or in a query parameter is HTML-escaped on screen (Tasks 6 and 8).
4. The database is missing, too old (schema below v4), unreadable, or the file is not SQLite: the Dashboard still renders and says why, never a 500 (Tasks 2 and 8).
5. The launch token: used twice, wrong, or reused after a session exists; the port already taken at startup; the token URL never reaches a file log (Tasks 4 and 9).
6. Heartbeat boundaries: exactly 90 s old counts as online, 91 s as offline, a timestamp in the future does not produce a negative age (Task 7).

---

## File structure

| File | Responsibility |
|---|---|
| `pyproject.toml` (modify) | Dependencies and package data |
| `src/yokai/config.py`, `src/yokai/__main__.py` (modify) | `LOG_DIR` setting shared with the panel |
| `.env.example`, `SPEC.md`, `README.md`, `TASKS.md` (modify) | Docs |
| `src/yokai_admin/__init__.py`, `errors.py` | Package marker; typed errors |
| `src/yokai_admin/core/settings.py` | `PanelSettings`, `load_settings` |
| `src/yokai_admin/core/db.py` | Read-only SQLite access and schema check |
| `src/yokai_admin/core/module.py` | `AdminModule`, `Tile`, `load_modules`, `render_tiles` |
| `src/yokai_admin/core/auth.py` | `AuthState`: one-time token, sessions, CSRF |
| `src/yokai_admin/core/security.py` | `SecurityMiddleware`, `/auth` route, headers |
| `src/yokai_admin/core/templating.py` | Jinja setup, `render`, `render_fragment`, nav |
| `src/yokai_admin/app.py` | `create_app` factory |
| `src/yokai_admin/templates/base.html`, `no_modules.html` | Shell templates |
| `src/yokai_admin/static/...` | `panel.css`, vendored `htmx.min.js`, font, `VENDORED.md` |
| `src/yokai_admin/modules/__init__.py` | `MODULE_PATHS` registry |
| `src/yokai_admin/modules/dashboard/` | `service.py`, `router.py`, `__init__.py`, templates |
| `src/yokai_admin/launch.py`, `__main__.py` | `build_panel`, `run`, `main` |
| `tests/admin_helpers.py`, `tests/admin_templates/demo/page.html` | Shared sample modules, `seed_database`, `panel_client` |
| `tests/test_admin_*.py` | New tests |

---

### Task 1: Settings, config, dependencies and docs for configuration

**Files:**
- Modify: `pyproject.toml`, `src/yokai/config.py`, `src/yokai/__main__.py`, `.env.example`, `SPEC.md` (§11), `tests/test_config.py`
- Create: `src/yokai_admin/__init__.py`, `src/yokai_admin/errors.py`, `src/yokai_admin/core/__init__.py`, `src/yokai_admin/core/settings.py`
- Test: `tests/test_admin_settings.py`, `tests/test_config.py`

**Interfaces:**
- Produces: `yokai.config.Config.log_dir: Path` (env `LOG_DIR`, default `logs`); `yokai_admin.errors.{PanelError, PanelConfigError, PanelDbError, ModuleConfigError}`; `yokai_admin.core.settings.{HOST: str = "127.0.0.1", DEFAULT_PORT: int = 8765, PanelSettings(db_path: Path, log_dir: Path, port: int = 8765), load_settings(env_path: Path | str | None = None) -> PanelSettings}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
def test_log_dir_defaults_and_overrides() -> None:
    base = {"DISCORD_TOKEN": "t.o.k", "GUILD_ID": "1", "OWNER_ID": "2"}
    with patch.dict(os.environ, base, clear=True):
        assert load_config(env_path=Path("non_existent_env")).log_dir == Path("logs")

    with patch.dict(os.environ, {**base, "LOG_DIR": "elsewhere/logs"}, clear=True):
        assert load_config(env_path=Path("non_existent_env")).log_dir == Path("elsewhere/logs")
```

Create `tests/test_admin_settings.py`:

```python
"""Tests for the admin panel's own settings loader."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from yokai_admin.core.settings import HOST, PanelSettings, load_settings
from yokai_admin.errors import PanelConfigError

NO_ENV_FILE = Path("non_existent_env")


def test_defaults_need_no_discord_secrets() -> None:
    with patch.dict(os.environ, {}, clear=True):
        settings = load_settings(env_path=NO_ENV_FILE)
    assert settings == PanelSettings(db_path=Path("data/yokai.db"), log_dir=Path("logs"), port=8765)


def test_overrides_are_read_from_the_environment() -> None:
    env = {"DB_PATH": "custom/bot.db", "LOG_DIR": "custom/logs", "ADMIN_PORT": "9000"}
    with patch.dict(os.environ, env, clear=True):
        settings = load_settings(env_path=NO_ENV_FILE)
    assert settings.db_path == Path("custom/bot.db")
    assert settings.log_dir == Path("custom/logs")
    assert settings.port == 9000


@pytest.mark.parametrize("value", ["abc", "80", "1023", "70000", "0", "-1"])
def test_a_bad_port_is_rejected_with_a_clear_message(value: str) -> None:
    with patch.dict(os.environ, {"ADMIN_PORT": value}, clear=True):
        with pytest.raises(PanelConfigError, match="ADMIN_PORT"):
            load_settings(env_path=NO_ENV_FILE)


def test_the_host_is_loopback_and_cannot_be_configured() -> None:
    assert HOST == "127.0.0.1"
    with patch.dict(os.environ, {"ADMIN_HOST": "0.0.0.0"}, clear=True):
        settings = load_settings(env_path=NO_ENV_FILE)
    assert not hasattr(settings, "host")
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_admin_settings.py tests/test_config.py -q`
Expected: FAIL (`ModuleNotFoundError: yokai_admin`; `Config` has no `log_dir`).

- [ ] **Step 3: Implement**

`pyproject.toml`: add `"fastapi",`, `"uvicorn",`, `"jinja2",` to `dependencies` (after `"spotifyscraper==3.9.2",`), and append:

```toml
[tool.setuptools.package-data]
yokai_admin = ["templates/**/*", "static/**/*", "modules/*/templates/**/*"]
```

Then install: `python -m pip install -e ".[dev]"`. Expected: installs fastapi, uvicorn, jinja2 (and their dependencies) without error.

`src/yokai/config.py`: under `# Logging & Storage` add `log_dir: Path = Path("logs")` after `db_path`; in `load_config` after the `db_path = ...` line add:

```python
    log_dir = Path(os.getenv("LOG_DIR", "logs").strip() or "logs")
```

and pass `log_dir=log_dir,` in the `Config(...)` call after `db_path=db_path,`.

`src/yokai/__main__.py`: change `log_dir="logs",` to `log_dir=config.log_dir,`.

`.env.example`: append

```
# Admin panel (python -m yokai_admin): a local web UI, always bound to 127.0.0.1
ADMIN_PORT=8765
# Folder the bot writes its rotating logs to; the panel reads them from here
LOG_DIR=logs
```

`SPEC.md` §11: change the Runtime line to end with ``..., `spotifyscraper` (exact pin); admin panel (`yokai_admin`): `fastapi`, `uvicorn`, `jinja2` (`python-multipart` joins in P-A3). Use stdlib `difflib` for similarity.`` and add two rows to the environment table after `DB_PATH`:

```
| `LOG_DIR` | `logs` | Folder of the rotating bot logs (the panel reads it) |
| `ADMIN_PORT` | `8765` | Local port of the admin panel (127.0.0.1 only) |
```

Create `src/yokai_admin/__init__.py`:

```python
"""Yokai admin panel: a local, owner-only web UI for the bot."""

from __future__ import annotations

__version__ = "0.1.0"
```

Create `src/yokai_admin/errors.py`:

```python
"""Typed errors for the admin panel."""

from __future__ import annotations


class PanelError(Exception):
    """Base class for admin panel errors."""


class PanelConfigError(PanelError):
    """Raised when panel configuration is invalid."""


class PanelDbError(PanelError):
    """Raised when the bot's database cannot be used by the panel."""


class ModuleConfigError(PanelError):
    """Raised when the module registry is invalid (for example a duplicate id)."""
```

Create `src/yokai_admin/core/__init__.py` (a docstring only: `"""Panel core: settings, database access, security and the module contract."""`) and `src/yokai_admin/core/settings.py`:

```python
"""Panel settings: read from the same .env as the bot, but never needs Discord secrets."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from yokai_admin.errors import PanelConfigError

HOST = "127.0.0.1"  # loopback only; deliberately not configurable
DEFAULT_PORT = 8765


@dataclass(frozen=True, slots=True)
class PanelSettings:
    """Where the panel finds the bot's data and which local port it listens on."""

    db_path: Path
    log_dir: Path
    port: int = DEFAULT_PORT


def load_settings(env_path: Path | str | None = None) -> PanelSettings:
    """Load panel settings from the environment, optionally reading an .env file first."""
    if env_path is not None:
        load_dotenv(dotenv_path=env_path)
    else:
        load_dotenv()

    db_path = Path(os.getenv("DB_PATH", "data/yokai.db").strip() or "data/yokai.db")
    log_dir = Path(os.getenv("LOG_DIR", "logs").strip() or "logs")

    raw_port = os.getenv("ADMIN_PORT", str(DEFAULT_PORT)).strip() or str(DEFAULT_PORT)
    try:
        port = int(raw_port)
    except ValueError as err:
        raise PanelConfigError(f"ADMIN_PORT must be a whole number, got {raw_port!r}.") from err
    if not 1024 <= port <= 65535:
        raise PanelConfigError(f"ADMIN_PORT must be between 1024 and 65535, got {port}.")

    return PanelSettings(db_path=db_path, log_dir=log_dir, port=port)
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_admin_settings.py tests/test_config.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add pyproject.toml src/yokai/config.py src/yokai/__main__.py .env.example SPEC.md src/yokai_admin tests/test_admin_settings.py tests/test_config.py
git commit -m "feat(admin): add panel settings, LOG_DIR config and web dependencies" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Read-only database access

**Files:**
- Create: `src/yokai_admin/core/db.py`
- Test: `tests/test_admin_db.py`

**Interfaces:**
- Consumes: `PanelDbError` (Task 1); the bot's `Database` (tests only).
- Produces: `MIN_SCHEMA_VERSION = 4`; `open_readonly(db_path: Path) -> sqlite3.Connection` (row factory `sqlite3.Row`, `mode=ro`); `readonly(db_path) -> ContextManager[sqlite3.Connection]` (always closes); `schema_version(conn) -> int`; `require_schema(conn, minimum: int = MIN_SCHEMA_VERSION) -> int`. All failures raise `PanelDbError` with a message that says what to do.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_admin_db.py`:

```python
"""Tests for the panel's read-only access to the bot's SQLite file."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import AsyncIterator

import pytest

from yokai.storage.db import Database
from yokai_admin.core.db import MIN_SCHEMA_VERSION, readonly, require_schema
from yokai_admin.errors import PanelDbError


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    """An open bot database that is always closed, even when the test fails."""
    database = Database(tmp_path / "t.db")
    await database.initialize()
    try:
        yield database
    finally:
        await database.close()


def test_a_missing_file_says_to_start_the_bot(tmp_path: Path) -> None:
    with pytest.raises(PanelDbError, match="not found"):
        with readonly(tmp_path / "nope.db"):
            pass


async def test_reads_a_live_database_the_bot_still_has_open(db: Database, tmp_path: Path) -> None:
    await db.credit_wallet(1, 10, reason="daily", welcome_bonus=200)
    with readonly(tmp_path / "t.db") as conn:
        assert conn.execute("SELECT balance FROM wallets WHERE user_id = 1;").fetchone()[0] == 210


async def test_reads_a_database_the_bot_closed_cleanly(db: Database, tmp_path: Path) -> None:
    await db.credit_wallet(1, 10, reason="daily", welcome_bonus=200)
    await db.close()
    with readonly(tmp_path / "t.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM wallets;").fetchone()[0] == 1


async def test_the_connection_cannot_write(db: Database, tmp_path: Path) -> None:
    with readonly(tmp_path / "t.db") as conn:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO blacklist (user_id, added_ts, added_by) VALUES (1, 1, 'x');")


async def test_require_schema_accepts_the_current_version(db: Database, tmp_path: Path) -> None:
    with readonly(tmp_path / "t.db") as conn:
        assert require_schema(conn) == MIN_SCHEMA_VERSION


def test_an_old_schema_is_refused_with_a_hint(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    raw.execute("PRAGMA user_version = 3;")
    raw.execute("CREATE TABLE t (x INTEGER);")
    raw.commit()
    raw.close()
    with readonly(path) as conn:
        with pytest.raises(PanelDbError, match=r"v3.*v4"):
            require_schema(conn)


def test_a_file_that_is_not_sqlite_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "junk.db"
    path.write_bytes(b"this is not a database" * 50)
    with readonly(path) as conn:
        with pytest.raises(PanelDbError, match="not a SQLite database"):
            require_schema(conn)
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_admin_db.py -q`
Expected: FAIL (`ModuleNotFoundError: yokai_admin.core.db`).

- [ ] **Step 3: Implement**

Create `src/yokai_admin/core/db.py`:

```python
"""Read-only access to the bot's SQLite file. The bot owns the schema; the panel never migrates."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from yokai_admin.errors import PanelDbError

MIN_SCHEMA_VERSION = 4


def open_readonly(db_path: Path) -> sqlite3.Connection:
    """Open the bot's database read-only (rows come back as `sqlite3.Row`)."""
    if not db_path.exists():
        raise PanelDbError(
            f"Database not found at {db_path}. Start the bot once so it can create it, "
            "or set DB_PATH in .env."
        )
    uri = db_path.resolve().as_uri() + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    except sqlite3.Error as exc:
        raise PanelDbError(f"Could not open the database read-only: {exc}") from exc
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def readonly(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Open the database read-only for the duration of a `with` block."""
    conn = open_readonly(db_path)
    try:
        yield conn
    finally:
        conn.close()


def schema_version(conn: sqlite3.Connection) -> int:
    """Return `PRAGMA user_version`, mapping a non-SQLite file to a clear error."""
    try:
        return int(conn.execute("PRAGMA user_version;").fetchone()[0])
    except sqlite3.DatabaseError as exc:
        raise PanelDbError(f"That file is not a SQLite database ({exc}).") from exc


def require_schema(conn: sqlite3.Connection, minimum: int = MIN_SCHEMA_VERSION) -> int:
    """Return the schema version, or raise if the bot's database is too old for the panel."""
    version = schema_version(conn)
    if version < minimum:
        raise PanelDbError(
            f"The database schema is v{version}; the panel needs v{minimum} or newer. "
            "Start the bot once to upgrade it."
        )
    return version
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_admin_db.py -q`
Expected: PASS. If `test_the_connection_cannot_write` fails because SQLite raised a different `sqlite3.Error` subclass, widen the `pytest.raises` to `sqlite3.Error` and keep the intent (a write must fail).

- [ ] **Step 5: Commit**

```powershell
git add src/yokai_admin/core/db.py tests/test_admin_db.py
git commit -m "feat(admin): add read-only database access with schema check" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Module contract and loader

**Files:**
- Create: `src/yokai_admin/core/module.py`, `tests/admin_helpers.py`
- Test: `tests/test_admin_modules.py`

**Interfaces:**
- Consumes: `ModuleConfigError` and `PanelSettings` (Task 1).
- Produces:
  - `TileStatus = Literal["neutral", "ok", "warn", "error"]`.
  - `Tile(title: str, value: str, status: TileStatus = "neutral", detail: str = "")` (frozen dataclass).
  - `TileContext(settings: PanelSettings, now: int)`.
  - `AdminModule(id: str, title: str, router: APIRouter, nav_order: int = 100, templates_dir: Optional[Path] = None, tile: Optional[Callable[[TileContext], Tile]] = None)`; `id` must match `^[a-z][a-z0-9-]{0,31}$` else `ModuleConfigError`.
  - `ModuleSlot(path: str, module: Optional[AdminModule], error: Optional[str] = None)`.
  - `load_modules(paths: Sequence[str]) -> list[ModuleSlot]`: sorted loaded modules by `(nav_order, id)`, errored slots last; duplicate ids raise `ModuleConfigError`.
  - `TileResult(module_id: str, module_title: str, tile: Optional[Tile], error: Optional[str])`; `render_tiles(slots, ctx) -> list[TileResult]` (modules without a tile are skipped; a raising tile becomes `error="Could not read this tile."`).
  - Test helpers in `tests/admin_helpers.py`: `ALPHA` (id `alpha`, order 20, ok tile "3 tracks"), `BETA` (id `beta`, order 10), `ALPHA_AGAIN` (duplicate id), `BROKEN_TILE` (id `broken-tile`, raising tile), `NOT_A_MODULE`.

- [ ] **Step 1: Write the failing tests**

Create `tests/admin_helpers.py` (Tasks 6, 8 and 9 extend it):

```python
"""Shared fixtures for the admin panel tests: sample modules, a seeded database, a client."""

from __future__ import annotations

from fastapi import APIRouter

from yokai_admin.core.module import AdminModule, Tile, TileContext


def _router(label: str) -> APIRouter:
    router = APIRouter()

    @router.get("")
    def index() -> dict[str, str]:
        return {"module": label}

    return router


def ok_tile(ctx: TileContext) -> Tile:
    return Tile(title="Queue", value="3 tracks", status="ok")


def failing_tile(ctx: TileContext) -> Tile:
    raise RuntimeError("tile exploded")


ALPHA = AdminModule(id="alpha", title="Alpha", router=_router("alpha"), nav_order=20, tile=ok_tile)
BETA = AdminModule(id="beta", title="Beta", router=_router("beta"), nav_order=10)
ALPHA_AGAIN = AdminModule(id="alpha", title="Alpha again", router=_router("alpha-again"))
BROKEN_TILE = AdminModule(
    id="broken-tile", title="Broken tile", router=_router("broken"), tile=failing_tile
)
NOT_A_MODULE = "just a string, not an AdminModule"
```

Create `tests/test_admin_modules.py`:

```python
"""Tests for the module contract: loading, ordering, isolation and tiles."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import APIRouter

from yokai_admin.core.module import AdminModule, TileContext, load_modules, render_tiles
from yokai_admin.core.settings import PanelSettings
from yokai_admin.errors import ModuleConfigError

H = "tests.admin_helpers"
CTX = TileContext(settings=PanelSettings(db_path=Path("x.db"), log_dir=Path("logs")), now=1000)


def test_loaded_modules_are_sorted_by_nav_order() -> None:
    slots = load_modules([f"{H}:ALPHA", f"{H}:BETA"])
    assert [slot.module.id for slot in slots if slot.module] == ["beta", "alpha"]


def test_duplicate_ids_fail_fast() -> None:
    with pytest.raises(ModuleConfigError, match="alpha"):
        load_modules([f"{H}:ALPHA", f"{H}:ALPHA_AGAIN"])


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (f"{H}:MISSING", "AttributeError"),
        ("tests.no_such_module:X", "ModuleNotFoundError"),
        (f"{H}:NOT_A_MODULE", "TypeError"),
        ("no-colon-here", "ModuleConfigError"),
    ],
)
def test_a_broken_module_becomes_an_errored_slot_and_the_rest_still_load(
    path: str, expected: str
) -> None:
    slots = load_modules([path, f"{H}:BETA"])
    broken = [slot for slot in slots if slot.module is None]
    assert len(broken) == 1 and expected in (broken[0].error or "")
    assert broken[0].path == path
    assert [slot.module.id for slot in slots if slot.module] == ["beta"]
    assert slots[-1] is broken[0]  # errored slots sort last


@pytest.mark.parametrize("bad_id", ["", "Alpha", "a b", "a/b", "1abc", "x" * 40])
def test_module_ids_are_validated(bad_id: str) -> None:
    with pytest.raises(ModuleConfigError):
        AdminModule(id=bad_id, title="T", router=APIRouter())


def test_a_failing_tile_is_isolated_and_modules_without_tiles_are_skipped() -> None:
    slots = load_modules([f"{H}:ALPHA", f"{H}:BROKEN_TILE", f"{H}:BETA"])
    results = {result.module_id: result for result in render_tiles(slots, CTX)}
    assert set(results) == {"alpha", "broken-tile"}  # beta has no tile
    assert results["alpha"].tile is not None and results["alpha"].tile.value == "3 tracks"
    assert results["broken-tile"].tile is None
    assert results["broken-tile"].error == "Could not read this tile."
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_admin_modules.py -q`
Expected: FAIL (`ModuleNotFoundError: yokai_admin.core.module`).

- [ ] **Step 3: Implement**

Create `src/yokai_admin/core/module.py`:

```python
"""The contract every admin module implements, and the loader that isolates broken ones."""

from __future__ import annotations

import importlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, Optional, Sequence

from fastapi import APIRouter

from yokai_admin.core.settings import PanelSettings
from yokai_admin.errors import ModuleConfigError

logger = logging.getLogger(__name__)

TileStatus = Literal["neutral", "ok", "warn", "error"]
_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_TILE_ERROR = "Could not read this tile."


@dataclass(frozen=True, slots=True)
class Tile:
    """A small status readout a module contributes to the Dashboard."""

    title: str
    value: str
    status: TileStatus = "neutral"
    detail: str = ""


@dataclass(frozen=True, slots=True)
class TileContext:
    """What a tile callable may use: panel settings and the current Unix time."""

    settings: PanelSettings
    now: int


@dataclass(frozen=True, slots=True)
class AdminModule:
    """One feature of the panel: a router mounted at /m/<id>, optional templates and tile."""

    id: str
    title: str
    router: APIRouter
    nav_order: int = 100
    templates_dir: Optional[Path] = None
    tile: Optional[Callable[[TileContext], Tile]] = None

    def __post_init__(self) -> None:
        if not _ID_RE.match(self.id):
            raise ModuleConfigError(
                f"Invalid module id {self.id!r}: use 1-32 lowercase letters, digits or hyphens, "
                "starting with a letter."
            )


@dataclass(frozen=True, slots=True)
class ModuleSlot:
    """A configured module path and what came of loading it."""

    path: str
    module: Optional[AdminModule]
    error: Optional[str] = None


@dataclass(frozen=True, slots=True)
class TileResult:
    """One Dashboard row: a tile, or the reason it could not be read."""

    module_id: str
    module_title: str
    tile: Optional[Tile]
    error: Optional[str]


def _import_module_object(path: str) -> AdminModule:
    module_name, separator, attribute = path.partition(":")
    if not separator or not module_name or not attribute:
        raise ModuleConfigError(f"Module path {path!r} must look like 'package.module:NAME'.")
    candidate = getattr(importlib.import_module(module_name), attribute)
    if not isinstance(candidate, AdminModule):
        raise TypeError(f"{path} is a {type(candidate).__name__}, not an AdminModule.")
    return candidate


def load_modules(paths: Sequence[str]) -> list[ModuleSlot]:
    """Import every configured module; a failure becomes an errored slot, not a crash.

    Duplicate module ids are a configuration mistake and raise `ModuleConfigError`.
    """
    slots: list[ModuleSlot] = []
    seen: dict[str, str] = {}
    for path in paths:
        try:
            module = _import_module_object(path)
        # Isolation boundary: importing a module runs arbitrary code, and one broken module
        # must never stop the panel or hide the others.
        except Exception as exc:
            logger.error("Admin module %s failed to load: %s", path, exc)
            slots.append(ModuleSlot(path=path, module=None, error=f"{type(exc).__name__}: {exc}"))
            continue
        if module.id in seen:
            raise ModuleConfigError(
                f"Duplicate module id {module.id!r} (from {seen[module.id]} and {path})."
            )
        seen[module.id] = path
        slots.append(ModuleSlot(path=path, module=module))

    def sort_key(slot: ModuleSlot) -> tuple[int, int, str]:
        if slot.module is None:
            return (1, 0, slot.path)
        return (0, slot.module.nav_order, slot.module.id)

    return sorted(slots, key=sort_key)


def render_tiles(slots: Sequence[ModuleSlot], ctx: TileContext) -> list[TileResult]:
    """Call every module's tile callable; one that raises yields an error row instead."""
    results: list[TileResult] = []
    for slot in slots:
        module = slot.module
        if module is None or module.tile is None:
            continue
        try:
            results.append(TileResult(module.id, module.title, module.tile(ctx), None))
        # Same isolation boundary: a tile is module code and must not break the Dashboard.
        except Exception as exc:
            logger.error("Tile for module %s failed: %s", module.id, exc)
            results.append(TileResult(module.id, module.title, None, _TILE_ERROR))
    return results
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_admin_modules.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/yokai_admin/core/module.py tests/admin_helpers.py tests/test_admin_modules.py
git commit -m "feat(admin): add the module contract with isolated loading and tiles" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Security core (token, session, CSRF, Host and Origin, headers)

**Files:**
- Create: `src/yokai_admin/core/auth.py`, `src/yokai_admin/core/security.py`
- Test: `tests/test_admin_security.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `auth.py`: `SESSION_COOKIE = "yokai_admin_session"`, `CSRF_HEADER = "x-csrf-token"`, `SESSION_TTL_S = 43200`, `Session(id, csrf, expires_at)`, `AuthState(token: Optional[str] = None, clock: Callable[[], float] = time.time, ttl_s: int = SESSION_TTL_S)` with `.token` (property), `.exchange(candidate: str) -> Optional[Session]` (single use, constant-time, thread-safe) and `.lookup(session_id: Optional[str]) -> Optional[Session]` (None when unknown or expired).
  - `security.py`: `CSP` (string), `locked_page(message: str = ...) -> str`, `SecurityMiddleware(app, auth, port)` (pure ASGI), `install_security(app: FastAPI, auth: AuthState, port: int) -> None`, which adds `GET /auth`, the middleware, and `TrustedHostMiddleware` for `127.0.0.1` and `localhost` (outermost). Authenticated requests expose the session as `request.state.session`.
  - Behavior: every path except `/auth` and `GET/HEAD /static/...` needs a valid session cookie (else 401 locked page); unsafe methods (anything but GET/HEAD/OPTIONS) need a matching `Origin` (when present) and the `X-CSRF-Token` header equal to the session's token; websocket scopes are refused; every response carries the security headers; non-static responses add `Cache-Control: no-store`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_admin_security.py`:

```python
"""Tests for the panel's access control: Host, one-time token, session, Origin and CSRF."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from starlette.staticfiles import StaticFiles
from starlette.testclient import TestClient

from yokai_admin.core.auth import CSRF_HEADER, SESSION_COOKIE, SESSION_TTL_S, AuthState
from yokai_admin.core.security import install_security

PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"
TOKEN = "t0ken-for-tests"


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def make_app(tmp_path: Path, clock: Clock | None = None) -> tuple[FastAPI, AuthState]:
    auth = AuthState(token=TOKEN, clock=clock or Clock())
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    (tmp_path / "x.css").write_text("body{}")
    app.mount("/static", StaticFiles(directory=tmp_path), name="static")

    @app.get("/")
    def home(request: Request) -> dict[str, str]:
        return {"csrf": request.state.session.csrf}

    @app.api_route("/mutate", methods=["POST", "PUT", "PATCH", "DELETE"])
    def mutate() -> dict[str, bool]:
        return {"ok": True}

    install_security(app, auth, PORT)
    return app, auth


def logged_in(app: FastAPI) -> tuple[TestClient, str]:
    client = TestClient(app, base_url=BASE)
    response = client.get(f"/auth?token={TOKEN}", follow_redirects=False)
    assert response.status_code == 303
    return client, client.get("/").json()["csrf"]


def test_the_token_is_random_per_instance_and_long() -> None:
    first, second = AuthState(), AuthState()
    assert first.token != second.token and len(first.token) >= 32


def test_unauthenticated_requests_get_the_locked_page(tmp_path: Path) -> None:
    app, _ = make_app(tmp_path)
    response = TestClient(app, base_url=BASE).get("/")
    assert response.status_code == 401
    assert "link" in response.text and "set-cookie" not in response.headers


def test_static_files_are_served_without_a_session(tmp_path: Path) -> None:
    app, _ = make_app(tmp_path)
    assert TestClient(app, base_url=BASE).get("/static/x.css").status_code == 200


def test_a_wrong_token_is_refused_and_sets_no_cookie(tmp_path: Path) -> None:
    app, _ = make_app(tmp_path)
    response = TestClient(app, base_url=BASE).get("/auth?token=wrong", follow_redirects=False)
    assert response.status_code == 401 and "set-cookie" not in response.headers


def test_a_good_token_sets_an_httponly_strict_cookie(tmp_path: Path) -> None:
    app, _ = make_app(tmp_path)
    response = TestClient(app, base_url=BASE).get(f"/auth?token={TOKEN}", follow_redirects=False)
    cookie = response.headers["set-cookie"].lower()
    assert response.status_code == 303 and response.headers["location"] == "/"
    assert SESSION_COOKIE in cookie and "httponly" in cookie and "samesite=strict" in cookie


def test_the_token_works_only_once(tmp_path: Path) -> None:
    """Review Focus 5: a used link is dead, even from another browser."""
    app, _ = make_app(tmp_path)
    logged_in(app)
    again = TestClient(app, base_url=BASE).get(f"/auth?token={TOKEN}", follow_redirects=False)
    assert again.status_code == 401


def test_an_unknown_cookie_is_refused(tmp_path: Path) -> None:
    app, _ = make_app(tmp_path)
    client = TestClient(app, base_url=BASE, cookies={SESSION_COOKIE: "forged"})
    assert client.get("/").status_code == 401


def test_a_session_expires(tmp_path: Path) -> None:
    clock = Clock()
    app, _ = make_app(tmp_path, clock)
    client, _ = logged_in(app)
    clock.now += SESSION_TTL_S + 1
    assert client.get("/").status_code == 401


def test_a_non_loopback_host_is_refused_even_with_a_valid_session(tmp_path: Path) -> None:
    """Review Focus 1: DNS rebinding sends a loopback connection with a foreign Host header."""
    app, _ = make_app(tmp_path)
    client, _ = logged_in(app)
    assert client.get("/", headers={"host": "evil.example:8765"}).status_code == 400
    assert client.get("/", headers={"host": "localhost:8765"}).status_code == 200


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_unsafe_methods_need_the_csrf_header(tmp_path: Path, method: str) -> None:
    """Review Focus 2: every state-changing request is CSRF-protected by default."""
    app, _ = make_app(tmp_path)
    client, csrf = logged_in(app)
    assert getattr(client, method)("/mutate").status_code == 403
    assert getattr(client, method)("/mutate", headers={CSRF_HEADER: "wrong"}).status_code == 403
    assert getattr(client, method)("/mutate", headers={CSRF_HEADER: csrf}).status_code == 200


def test_a_foreign_origin_is_refused_even_with_the_right_csrf_token(tmp_path: Path) -> None:
    app, _ = make_app(tmp_path)
    client, csrf = logged_in(app)
    headers = {CSRF_HEADER: csrf, "origin": "http://evil.example"}
    assert client.post("/mutate", headers=headers).status_code == 403
    good = {CSRF_HEADER: csrf, "origin": BASE}
    assert client.post("/mutate", headers=good).status_code == 200


def test_unsafe_requests_without_a_session_are_refused(tmp_path: Path) -> None:
    app, _ = make_app(tmp_path)
    assert TestClient(app, base_url=BASE).post("/mutate").status_code == 401


def test_api_docs_are_not_exposed(tmp_path: Path) -> None:
    app, _ = make_app(tmp_path)
    client, _ = logged_in(app)
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_security_headers_are_on_every_response_and_pages_are_not_cached(tmp_path: Path) -> None:
    app, _ = make_app(tmp_path)
    client, _ = logged_in(app)
    page = client.get("/")
    csp = page.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "script-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert page.headers["x-content-type-options"] == "nosniff"
    assert page.headers["referrer-policy"] == "no-referrer"
    assert page.headers["cache-control"] == "no-store"
    assert "cache-control" not in client.get("/static/x.css").headers
    assert "content-security-policy" in client.get("/static/x.css").headers
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_admin_security.py -q`
Expected: FAIL (`ModuleNotFoundError: yokai_admin.core.auth`).

- [ ] **Step 3: Implement**

Create `src/yokai_admin/core/auth.py`:

```python
"""One-time launch token, in-memory sessions and per-session CSRF tokens."""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

SESSION_COOKIE = "yokai_admin_session"
CSRF_HEADER = "x-csrf-token"
SESSION_TTL_S = 12 * 3600


@dataclass(slots=True)
class Session:
    """A logged-in browser: its cookie id, its CSRF token and when it ends."""

    id: str
    csrf: str
    expires_at: float


class AuthState:
    """Holds the launch token (valid for exactly one exchange) and the live sessions."""

    def __init__(
        self,
        token: Optional[str] = None,
        clock: Callable[[], float] = time.time,
        ttl_s: int = SESSION_TTL_S,
    ) -> None:
        self._token = token or secrets.token_urlsafe(32)
        self._clock = clock
        self._ttl_s = ttl_s
        self._token_used = False
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    @property
    def token(self) -> str:
        return self._token

    def exchange(self, candidate: str) -> Optional[Session]:
        """Trade the launch token for a session. Works once; wrong or reused tokens get None."""
        with self._lock:
            if self._token_used:
                return None
            if not secrets.compare_digest(candidate.encode(), self._token.encode()):
                return None
            self._token_used = True
            session = Session(
                id=secrets.token_urlsafe(32),
                csrf=secrets.token_urlsafe(32),
                expires_at=self._clock() + self._ttl_s,
            )
            self._sessions[session.id] = session
            return session

    def lookup(self, session_id: Optional[str]) -> Optional[Session]:
        """Return the live session for a cookie value, dropping it if it has expired."""
        if not session_id:
            return None
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            if self._clock() >= session.expires_at:
                del self._sessions[session_id]
                return None
            return session
```

Create `src/yokai_admin/core/security.py`:

```python
"""Loopback-only access control: Host and Origin checks, session gate, CSRF and headers."""

from __future__ import annotations

import html
import secrets

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.datastructures import MutableHeaders
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from yokai_admin.core.auth import CSRF_HEADER, SESSION_COOKIE, SESSION_TTL_S, AuthState

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; font-src 'self'; "
    "img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'self'; "
    "frame-ancestors 'none'"
)
_STATIC_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Resource-Policy": "same-origin",
}
_LOCKED_DEFAULT = (
    "Open the panel with the link printed in the console where you started it. "
    "The link works once; restart the panel to get a new one."
)


def locked_page(message: str = _LOCKED_DEFAULT) -> str:
    """A self-contained page for requests without a session (no module templates involved)."""
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<link rel="stylesheet" href="/static/panel.css"><link rel="icon" href="data:,">'
        "<title>Panel locked - Yokai admin</title></head>"
        f'<body><main class="locked"><h1>Panel locked</h1><p>{html.escape(message)}</p>'
        "</main></body></html>"
    )


def _text_response(status: int, message: str) -> Response:
    return HTMLResponse(locked_page(message), status_code=status)


class SecurityMiddleware:
    """Pure ASGI (not BaseHTTPMiddleware) so streaming responses such as a log tail still work."""

    def __init__(self, app: ASGIApp, auth: AuthState, port: int) -> None:
        self.app = app
        self.auth = auth
        self.allowed_origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":  # the panel uses no websockets
            await send({"type": "websocket.close", "code": 1008})
            return

        path: str = scope["path"]
        method: str = scope["method"]
        send = self._with_headers(send, path)
        request = Request(scope)

        if method not in SAFE_METHODS:
            origin = request.headers.get("origin")
            if origin is not None and origin not in self.allowed_origins:
                await _text_response(403, "Cross-origin request blocked.")(scope, receive, send)
                return

        if path == "/auth" or (path.startswith("/static/") and method in SAFE_METHODS):
            await self.app(scope, receive, send)
            return

        session = self.auth.lookup(request.cookies.get(SESSION_COOKIE))
        if session is None:
            await _text_response(401, _LOCKED_DEFAULT)(scope, receive, send)
            return

        if method not in SAFE_METHODS:
            supplied = request.headers.get(CSRF_HEADER, "")
            if not secrets.compare_digest(supplied.encode(), session.csrf.encode()):
                message = "The security token is missing or wrong. Reload the page and try again."
                await _text_response(403, message)(scope, receive, send)
                return

        scope.setdefault("state", {})["session"] = session
        await self.app(scope, receive, send)

    @staticmethod
    def _with_headers(send: Send, path: str) -> Send:
        async def wrapped(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in _STATIC_HEADERS.items():
                    headers[name] = value
                if not path.startswith("/static/"):
                    headers["Cache-Control"] = "no-store"
            await send(message)

        return wrapped


def install_security(app: FastAPI, auth: AuthState, port: int) -> None:
    """Add the /auth route and the middleware stack (Host check outermost, then the gate)."""

    @app.get("/auth", include_in_schema=False)
    def exchange_token(token: str = "") -> Response:
        session = auth.exchange(token)
        if session is None:
            return _text_response(
                401, "That link is no longer valid. Restart the panel to get a new one."
            )
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            SESSION_COOKIE,
            session.id,
            max_age=SESSION_TTL_S,
            httponly=True,
            samesite="strict",
            path="/",
        )
        return response

    app.add_middleware(SecurityMiddleware, auth=auth, port=port)
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"], www_redirect=False
    )
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_admin_security.py -q`
Expected: PASS. If `test_the_token_is_random...` or any assertion about `set-cookie` casing fails, compare lowercased headers (as the tests do) rather than loosening the security behavior.

- [ ] **Step 5: Commit**

```powershell
git add src/yokai_admin/core/auth.py src/yokai_admin/core/security.py tests/test_admin_security.py
git commit -m "feat(admin): add loopback security core (token, session, CSRF, headers)" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Static assets and the design system

**Files:**
- Create: `src/yokai_admin/static/panel.css`, `src/yokai_admin/static/vendor/htmx.min.js`, `src/yokai_admin/static/fonts/Archivo-variable.woff2`, `src/yokai_admin/static/fonts/OFL.txt`, `src/yokai_admin/static/VENDORED.md`
- Test: `tests/test_admin_static.py`

**Interfaces:**
- Produces: static files served at `/static/...`; CSS classes used by Tasks 6 and 8: `.shell`, `.rail`, `.wordmark`, `.nav` (`a[aria-current="page"]`, `.is-broken`), `.main`, `.status`, `.status-mark`, `.ping` (`data-state="online|offline"`, child `.ping-ring` x2), `.status-word`, `.status-note`, `.section`, `.readouts` (dl), `.mark` (`data-status="ok|warn|error"`), `.detail`, `.notice`, `.log`, `.empty`, `.locked`, `.sr-only`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_admin_static.py`:

```python
"""Tests that the vendored assets are intact and the stylesheet stays offline and accessible."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

STATIC = Path("src/yokai_admin/static")
CSS = (STATIC / "panel.css").read_text(encoding="utf-8")


def test_vendored_files_match_their_recorded_hashes() -> None:
    notes = (STATIC / "VENDORED.md").read_text(encoding="utf-8")
    entries = re.findall(r"^- (\S+) sha256:([0-9a-f]{64})$", notes, flags=re.MULTILINE)
    assert {name for name, _ in entries} >= {"vendor/htmx.min.js", "fonts/Archivo-variable.woff2"}
    for name, digest in entries:
        assert hashlib.sha256((STATIC / name).read_bytes()).hexdigest() == digest, name


def test_the_font_license_ships_with_the_font() -> None:
    assert "SIL Open Font License" in (STATIC / "fonts" / "OFL.txt").read_text(encoding="utf-8")


def test_htmx_is_the_minified_2x_build() -> None:
    text = (STATIC / "vendor" / "htmx.min.js").read_text(encoding="utf-8")
    assert "htmx" in text and len(text) > 40_000 and 'version:"2.' in text


def test_the_stylesheet_references_nothing_external() -> None:
    assert "http://" not in CSS and "https://" not in CSS and "@import" not in CSS


def test_the_stylesheet_defines_the_palette_and_both_themes() -> None:
    for token in ("--hull", "--panel", "--ink", "--slate", "--line", "--sonar"):
        assert f"{token}:" in CSS
    for token in ("--flare", "--lichen", "--amber"):
        assert f"{token}:" in CSS
    assert "prefers-color-scheme: dark" in CSS


def test_motion_respects_reduced_motion_and_focus_is_visible() -> None:
    assert "prefers-reduced-motion: reduce" in CSS
    assert ":focus-visible" in CSS


def test_status_markers_differ_by_shape_not_only_color() -> None:
    assert '.mark[data-status="warn"]' in CSS and '.mark[data-status="error"]' in CSS
    assert "border-radius: 0" in CSS
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_admin_static.py -q`
Expected: FAIL (`FileNotFoundError` for the static files).

- [ ] **Step 3: Implement**

Fetch and pin the vendored files (run from the repo root in PowerShell; `curl.exe` ships with Windows):

```powershell
$static = "src/yokai_admin/static"
New-Item -ItemType Directory -Force "$static/vendor", "$static/fonts" | Out-Null
$htmxVersion = (curl.exe -fsSL https://cdn.jsdelivr.net/npm/htmx.org@2/package.json | ConvertFrom-Json).version
curl.exe -fsSL "https://cdn.jsdelivr.net/npm/htmx.org@$htmxVersion/dist/htmx.min.js" -o "$static/vendor/htmx.min.js"
$fontVersion = (curl.exe -fsSL https://cdn.jsdelivr.net/npm/@fontsource-variable/archivo/package.json | ConvertFrom-Json).version
curl.exe -fsSL "https://cdn.jsdelivr.net/npm/@fontsource-variable/archivo@$fontVersion/files/archivo-latin-wdth-normal.woff2" -o "$static/fonts/Archivo-variable.woff2"
curl.exe -fsSL "https://cdn.jsdelivr.net/npm/@fontsource-variable/archivo@$fontVersion/LICENSE" -o "$static/fonts/OFL.txt"
"htmx $htmxVersion; archivo $fontVersion"
```

Expected: three files exist, htmx about 52 KB, font about 90 KB, and the last line prints the pinned versions. If the `LICENSE` URL answers 404, take the SIL OFL 1.1 text (with Archivo's copyright line) from the font's upstream repository instead and note the source in `VENDORED.md`; the test only requires the phrase "SIL Open Font License".

Write `VENDORED.md` (the hashes are computed, not typed):

```powershell
$static = "src/yokai_admin/static"
$h1 = (Get-FileHash "$static/vendor/htmx.min.js" -Algorithm SHA256).Hash.ToLower()
$h2 = (Get-FileHash "$static/fonts/Archivo-variable.woff2" -Algorithm SHA256).Hash.ToLower()
@"
# Vendored files

Fetched once and committed, so the panel makes no outbound requests at run time. Regenerate by
re-downloading and updating the hashes; ``tests/test_admin_static.py`` verifies them.

- vendor/htmx.min.js sha256:$h1
  htmx.org $htmxVersion (BSD Zero Clause), https://cdn.jsdelivr.net/npm/htmx.org@$htmxVersion/dist/htmx.min.js
- fonts/Archivo-variable.woff2 sha256:$h2
  Archivo variable, width and weight axes (SIL Open Font License 1.1), @fontsource-variable/archivo $fontVersion, see fonts/OFL.txt
"@ | Set-Content -Encoding utf8 "$static/VENDORED.md"
```

Create `src/yokai_admin/static/panel.css`:

```css
/* Yokai admin panel. One accent (sonar), one family (Archivo, used for its width axis). */

@font-face {
  font-family: "Archivo";
  src: url("fonts/Archivo-variable.woff2") format("woff2");
  font-weight: 100 900;
  font-stretch: 62% 125%;
  font-display: swap;
}

:root {
  color-scheme: light dark;
  --hull: #eef1f3;
  --panel: #f8fafb;
  --ink: #13212c;
  --slate: #4f606d;
  --line: #bcc6ce;
  --sonar: #08697d;
  --flare: #b5361f;
  --lichen: #25704a;
  --amber: #8a5200;
  --font: "Archivo", system-ui, "Segoe UI", sans-serif;
  --mono: ui-monospace, "Cascadia Mono", Consolas, monospace;
  --rail: 13.5rem;
}

@media (prefers-color-scheme: dark) {
  :root {
    --hull: #0f181f;
    --panel: #15202a;
    --ink: #e6edf2;
    --slate: #97a8b5;
    --line: #3a4a57;
    --sonar: #43c1da;
    --flare: #f27a63;
    --lichen: #62c48f;
    --amber: #e8ab45;
  }
}

*,
*::before,
*::after {
  box-sizing: border-box;
}

html {
  background: var(--hull);
  color: var(--ink);
  font-family: var(--font);
  font-size: 100%;
  line-height: 1.55;
  -webkit-text-size-adjust: 100%;
}

body {
  margin: 0;
  min-height: 100vh;
  font-size: 0.9375rem;
  font-variant-numeric: tabular-nums;
}

h1,
h2,
h3 {
  margin: 0;
  font-stretch: 75%;
  font-weight: 700;
  letter-spacing: -0.005em;
  line-height: 1.15;
}

h2 {
  font-size: 1.5rem;
}

h3 {
  font-size: 1.125rem;
}

a {
  color: var(--sonar);
  text-underline-offset: 0.18em;
}

:focus-visible {
  outline: 2px solid var(--sonar);
  outline-offset: 3px;
  border-radius: 2px;
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  margin: -1px;
  padding: 0;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
  border: 0;
}

/* Shell: rail and main column */

.shell {
  display: grid;
  grid-template-columns: var(--rail) minmax(0, 1fr);
  min-height: 100vh;
}

.rail {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
  padding: 1.5rem 1rem;
  background: var(--panel);
  border-right: 1px solid var(--line);
}

.wordmark {
  margin: 0 0.5rem;
  font-stretch: 62%;
  font-weight: 800;
  font-size: 1.75rem;
  line-height: 1;
  letter-spacing: 0.01em;
}

.wordmark small {
  display: block;
  margin-top: 0.35rem;
  font-stretch: 100%;
  font-weight: 400;
  font-size: 0.8125rem;
  letter-spacing: 0;
  color: var(--slate);
}

.nav {
  display: grid;
  gap: 0.125rem;
  margin: 0;
  padding: 0;
  list-style: none;
}

.nav a,
.nav .is-broken {
  display: block;
  padding: 0.5rem;
  border-radius: 0.25rem;
  color: var(--ink);
  text-decoration: none;
}

.nav a:hover {
  background: color-mix(in srgb, var(--sonar) 10%, transparent);
}

.nav a[aria-current="page"] {
  background: color-mix(in srgb, var(--sonar) 14%, transparent);
  box-shadow: inset 3px 0 0 var(--sonar);
  font-weight: 600;
}

.nav .is-broken {
  color: var(--flare);
}

.nav .is-broken small {
  display: block;
  color: var(--slate);
  overflow-wrap: anywhere;
}

.main {
  max-width: 62rem;
  padding: 2.25rem 2.5rem 4rem;
}

/* Status: the one bold element */

.status {
  display: flex;
  align-items: center;
  gap: 1.5rem;
  padding: 0.5rem 0 2rem;
}

.status-mark {
  display: grid;
  flex: none;
  place-items: center;
  width: 4.5rem;
  height: 4.5rem;
}

.status-word {
  font-stretch: 62%;
  font-weight: 800;
  font-size: clamp(2.75rem, 7vw, 4.5rem);
  line-height: 0.95;
}

.status-note {
  margin: 0.5rem 0 0;
  color: var(--slate);
}

.ping {
  position: relative;
  width: 1.5rem;
  height: 1.5rem;
}

.ping::before {
  content: "";
  position: absolute;
  inset: 0;
  border-radius: 50%;
  background: var(--sonar);
}

.ping[data-state="offline"]::before {
  background: transparent;
  border: 2px solid var(--slate);
}

.ping-ring {
  position: absolute;
  inset: 0;
  border: 2px solid var(--sonar);
  border-radius: 50%;
  opacity: 0;
}

/* One ring per poll: the panel just asked the bot's database "are you there?" */
.ping[data-state="online"] .ping-ring {
  animation: ping 2.4s cubic-bezier(0.15, 0.6, 0.3, 1) 1 forwards;
}

.ping[data-state="online"] .ping-ring + .ping-ring {
  animation-delay: 0.5s;
}

@keyframes ping {
  from {
    transform: scale(1);
    opacity: 0.9;
  }
  to {
    transform: scale(3);
    opacity: 0;
  }
}

/* Readouts: key/value ledger rows */

.section {
  margin-top: 2.5rem;
}

.section > h3 {
  margin-bottom: 0.75rem;
}

.readouts {
  display: grid;
  grid-template-columns: minmax(10rem, 14rem) 1fr;
  margin: 0;
  border-top: 1px solid var(--line);
}

.readouts dt,
.readouts dd {
  margin: 0;
  padding: 0.7rem 0;
  border-bottom: 1px solid var(--line);
}

.readouts dt {
  color: var(--slate);
}

.readouts dd {
  font-weight: 600;
}

.detail {
  margin-left: 0.5rem;
  font-weight: 400;
  color: var(--slate);
}

.mark {
  display: inline-block;
  width: 0.6rem;
  height: 0.6rem;
  margin-right: 0.55rem;
  border-radius: 50%;
  background: var(--slate);
}

.mark[data-status="ok"] {
  background: var(--lichen);
}

.mark[data-status="warn"] {
  background: var(--amber);
  border-radius: 0;
  transform: rotate(45deg) scale(0.9);
}

.mark[data-status="error"] {
  background: var(--flare);
  border-radius: 0;
}

.notice {
  margin: 0 0 1.5rem;
  padding: 0.5rem 1rem;
  border-left: 3px solid var(--flare);
}

.notice ul {
  margin: 0.25rem 0 0;
  padding-left: 1.1rem;
}

.notice code {
  font-family: var(--mono);
  font-size: 0.8125rem;
}

.log {
  margin: 0;
  padding: 1rem;
  overflow-x: auto;
  font: 0.8125rem/1.5 var(--mono);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 0.375rem;
}

.empty {
  margin: 0;
  color: var(--slate);
}

.locked {
  max-width: 34rem;
  margin: 18vh auto 0;
  padding: 0 1.5rem;
}

.locked h1 {
  margin-bottom: 0.75rem;
  font-stretch: 62%;
  font-weight: 800;
  font-size: 3rem;
}

@media (max-width: 48rem) {
  .shell {
    grid-template-columns: 1fr;
  }

  .rail {
    flex-direction: row;
    align-items: center;
    gap: 1rem;
    padding: 0.75rem 1rem;
    overflow-x: auto;
    border-right: 0;
    border-bottom: 1px solid var(--line);
  }

  .nav {
    display: flex;
    gap: 0.25rem;
  }

  .nav a[aria-current="page"] {
    box-shadow: inset 0 -3px 0 var(--sonar);
  }

  .main {
    padding: 1.5rem 1rem 3rem;
  }

  .status {
    gap: 1rem;
  }

  .readouts {
    grid-template-columns: 1fr;
  }

  .readouts dt {
    padding-bottom: 0;
    border-bottom: 0;
  }

  .readouts dd {
    padding-top: 0.1rem;
  }
}

@media (prefers-reduced-motion: reduce) {
  .ping-ring {
    animation: none !important;
  }
}
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_admin_static.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/yokai_admin/static tests/test_admin_static.py
git commit -m "feat(admin): add the stylesheet and vendored htmx and Archivo font" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 6: App factory, templating and shell

**Files:**
- Create: `src/yokai_admin/core/templating.py`, `src/yokai_admin/app.py`, `src/yokai_admin/templates/base.html`, `src/yokai_admin/templates/no_modules.html`, `tests/admin_templates/demo/page.html`
- Modify: `tests/admin_helpers.py` (add `DEMO`, `panel_client`)
- Test: `tests/test_admin_app.py`

**Interfaces:**
- Consumes: `load_modules`, `ModuleSlot`, `AdminModule` (Task 3); `AuthState`, `SESSION_COOKIE` (Task 4); `install_security` (Task 4); `PanelSettings` (Task 1); static files (Task 5).
- Produces:
  - `templating.py`: `CORE_TEMPLATES: Path`, `STATIC_DIR: Path`, `NavItem(title, href, active, error, path)`, `build_templates(slots) -> Jinja2Templates`, `render(request, name, *, active: str = "", **context) -> Response` (full page with nav and `csrf_token`), `render_fragment(request, name, **context) -> Response` (no shell).
  - `app.py`: `create_app(settings: PanelSettings, module_paths: Sequence[str], auth: AuthState, *, on_startup: Optional[Callable[[], None]] = None) -> FastAPI`. State: `app.state.settings`, `app.state.slots`, `app.state.templates`. `/` redirects (303) to the first loaded module, or renders `no_modules.html` when none loaded. Module routers mount at `/m/<id>`.
  - Test helpers: `DEMO` (id `demo`, order 5, renders `demo/page.html` with the `m` query parameter as `message`), `panel_client(tmp_path, module_paths, *, db_path=None, token="t0ken", login=True, on_startup=None)` context manager yielding `(client, csrf)`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/admin_helpers.py` (imports at the top of the file, then the new definitions):

```python
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, Optional, Sequence

from fastapi import Request
from fastapi.responses import Response
from starlette.testclient import TestClient

from yokai_admin.app import create_app
from yokai_admin.core.auth import SESSION_COOKIE, AuthState
from yokai_admin.core.settings import PanelSettings
from yokai_admin.core.templating import render

_demo_router = APIRouter()


@_demo_router.get("")
def _demo_page(request: Request) -> Response:
    return render(
        request, "demo/page.html", active="demo", message=request.query_params.get("m", "")
    )


DEMO = AdminModule(
    id="demo",
    title="Demo",
    router=_demo_router,
    nav_order=5,
    templates_dir=Path(__file__).parent / "admin_templates",
)


@contextmanager
def panel_client(
    tmp_path: Path,
    module_paths: Sequence[str],
    *,
    db_path: Optional[Path] = None,
    token: str = "t0ken",
    login: bool = True,
    on_startup: Optional[Callable[[], None]] = None,
) -> Iterator[tuple[TestClient, str]]:
    """Run the real app in a TestClient; yields (client, csrf_token) after the token exchange."""
    settings = PanelSettings(db_path=db_path or tmp_path / "yokai.db", log_dir=tmp_path / "logs")
    auth = AuthState(token=token)
    app = create_app(settings, module_paths, auth, on_startup=on_startup)
    with TestClient(app, base_url=f"http://127.0.0.1:{settings.port}") as client:
        csrf = ""
        if login:
            response = client.get(f"/auth?token={token}", follow_redirects=False)
            assert response.status_code == 303
            session = auth.lookup(client.cookies.get(SESSION_COOKIE))
            assert session is not None
            csrf = session.csrf
        yield client, csrf
```

Create `tests/admin_templates/demo/page.html`:

```html
{% extends "base.html" %}
{% block content %}<h2>Demo page</h2><p id="message">{{ message }}</p>{% endblock %}
```

Create `tests/test_admin_app.py`:

```python
"""Tests for the app factory: routing, shell, nav, module isolation and headers."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.admin_helpers import panel_client
from yokai_admin.errors import ModuleConfigError

H = "tests.admin_helpers"


def test_the_root_redirects_to_the_first_module(tmp_path: Path) -> None:
    with panel_client(tmp_path, [f"{H}:ALPHA", f"{H}:BETA"]) as (client, _):
        response = client.get("/", follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"] == "/m/beta"


def test_module_routers_mount_under_m_id(tmp_path: Path) -> None:
    with panel_client(tmp_path, [f"{H}:ALPHA"]) as (client, _):
        assert client.get("/m/alpha").json() == {"module": "alpha"}


def test_a_new_modules_routes_are_locked_without_a_session(tmp_path: Path) -> None:
    """Review Focus 2: a module cannot forget auth; the gate covers every route."""
    with panel_client(tmp_path, [f"{H}:ALPHA"], login=False) as (client, _):
        assert client.get("/m/alpha").status_code == 401
        assert client.post("/m/alpha").status_code == 401


def test_the_shell_lists_modules_in_order_and_marks_the_active_one(tmp_path: Path) -> None:
    with panel_client(tmp_path, [f"{H}:ALPHA", f"{H}:DEMO"]) as (client, csrf):
        page = client.get("/m/demo").text
        assert page.index("Demo") < page.index("Alpha")  # nav_order 5 before 20
        assert re.search(r'<a href="/m/demo" aria-current="page">Demo</a>', page)
        assert '<a href="/m/alpha">Alpha</a>' in page
        assert f'"X-CSRF-Token": "{csrf}"' in page  # HTMX sends it on every request
        assert "htmx.min.js" in page and "panel.css" in page


def test_hostile_text_in_a_page_and_in_a_module_path_is_escaped(tmp_path: Path) -> None:
    """Review Focus 3: nothing user- or config-supplied is rendered as HTML."""
    evil_path = f"{H}:<img src=x onerror=alert(1)>"
    with panel_client(tmp_path, [f"{H}:DEMO", evil_path]) as (client, _):
        page = client.get("/m/demo", params={"m": "<script>alert(1)</script>"}).text
        assert "<script>alert(1)</script>" not in page
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
        assert "<img src=x" not in page and "&lt;img src=x" in page


def test_a_failed_module_shows_in_the_nav_while_the_others_work(tmp_path: Path) -> None:
    with panel_client(tmp_path, [f"{H}:DEMO", f"{H}:MISSING"]) as (client, _):
        page = client.get("/m/demo").text
        assert "Module failed to load" in page and f"{H}:MISSING" in page
        assert client.get("/m/demo").status_code == 200


def test_duplicate_module_ids_stop_startup(tmp_path: Path) -> None:
    with pytest.raises(ModuleConfigError):
        with panel_client(tmp_path, [f"{H}:ALPHA", f"{H}:ALPHA_AGAIN"]):
            pass


def test_with_no_loadable_module_the_root_explains_what_to_do(tmp_path: Path) -> None:
    with panel_client(tmp_path, ["tests.no_such_module:X"]) as (client, _):
        response = client.get("/")
        assert response.status_code == 200 and "No modules loaded" in response.text
        assert "tests.no_such_module:X" in response.text


def test_api_docs_are_disabled(tmp_path: Path) -> None:
    with panel_client(tmp_path, [f"{H}:ALPHA"]) as (client, _):
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404


def test_static_assets_are_served(tmp_path: Path) -> None:
    with panel_client(tmp_path, [f"{H}:ALPHA"], login=False) as (client, _):
        css = client.get("/static/panel.css")
        assert css.status_code == 200 and "text/css" in css.headers["content-type"]


def test_the_startup_hook_runs_once(tmp_path: Path) -> None:
    calls: list[int] = []
    with panel_client(tmp_path, [f"{H}:ALPHA"], on_startup=lambda: calls.append(1)):
        pass
    assert calls == [1]
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_admin_app.py -q`
Expected: FAIL (`ModuleNotFoundError: yokai_admin.app`).

- [ ] **Step 3: Implement**

Create `src/yokai_admin/core/templating.py`:

```python
"""Jinja setup shared by the shell and every module, plus the page-rendering helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Sequence

from fastapi import Request
from fastapi.responses import Response
from starlette.templating import Jinja2Templates

from yokai_admin.core.module import ModuleSlot

PACKAGE_DIR = Path(__file__).resolve().parent.parent
CORE_TEMPLATES = PACKAGE_DIR / "templates"
STATIC_DIR = PACKAGE_DIR / "static"


@dataclass(frozen=True, slots=True)
class NavItem:
    """One rail entry: a working module link, or a module that failed to load."""

    title: str
    href: str
    active: bool
    error: Optional[str]
    path: str


def build_templates(slots: Sequence[ModuleSlot]) -> Jinja2Templates:
    """Core templates plus each loaded module's own template directory (autoescape is on)."""
    directories: list[Path] = [CORE_TEMPLATES]
    for slot in slots:
        if slot.module is not None and slot.module.templates_dir is not None:
            directories.append(slot.module.templates_dir)
    return Jinja2Templates(directory=directories)


def _nav(slots: Sequence[ModuleSlot], active: str) -> list[NavItem]:
    items: list[NavItem] = []
    for slot in slots:
        if slot.module is None:
            items.append(NavItem("Module failed to load", "", False, slot.error, slot.path))
        else:
            module = slot.module
            items.append(NavItem(module.title, f"/m/{module.id}", module.id == active, None, slot.path))
    return items


def render(request: Request, name: str, *, active: str = "", **context: Any) -> Response:
    """Render a full page inside the shell. `active` is the module id to highlight."""
    state = request.app.state
    context = {
        "nav": _nav(state.slots, active),
        "csrf_token": request.state.session.csrf,
        **context,
    }
    return state.templates.TemplateResponse(request, name, context)


def render_fragment(request: Request, name: str, **context: Any) -> Response:
    """Render a template without the shell (for HTMX partials)."""
    return request.app.state.templates.TemplateResponse(request, name, context)
```

Create `src/yokai_admin/templates/base.html`:

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light dark">
  <meta name="htmx-config" content='{"includeIndicatorStyles": false, "allowEval": false, "allowScriptTags": false}'>
  <link rel="stylesheet" href="/static/panel.css">
  <link rel="icon" href="data:,">
  <script src="/static/vendor/htmx.min.js" defer></script>
  <title>{% block title %}Yokai admin{% endblock %}</title>
</head>
<body hx-headers='{"X-CSRF-Token": "{{ csrf_token }}"}'>
<div class="shell">
  <aside class="rail">
    <p class="wordmark">Yokai<small>Admin panel</small></p>
    <nav aria-label="Modules">
      <ul class="nav">
        {% for item in nav %}
          {% if item.error %}
            <li class="is-broken" title="{{ item.error }}">Module failed to load<small>{{ item.path }}</small></li>
          {% else %}
            <li><a href="{{ item.href }}"{% if item.active %} aria-current="page"{% endif %}>{{ item.title }}</a></li>
          {% endif %}
        {% endfor %}
      </ul>
    </nav>
  </aside>
  <main class="main">{% block content %}{% endblock %}</main>
</div>
</body>
</html>
```

Create `src/yokai_admin/templates/no_modules.html`:

```html
{% extends "base.html" %}
{% block title %}No modules loaded - Yokai admin{% endblock %}
{% block content %}
<h1 class="status-word">No modules loaded</h1>
<p class="status-note">None of the configured modules could start. Read the reasons in the rail and in the console, fix them, then restart the panel.</p>
{% endblock %}
```

Create `src/yokai_admin/app.py`:

```python
"""Application factory: wires modules, templates, static files and security together."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator, Callable, Optional, Sequence

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from starlette.staticfiles import StaticFiles

from yokai_admin.core.auth import AuthState
from yokai_admin.core.module import load_modules
from yokai_admin.core.security import install_security
from yokai_admin.core.settings import PanelSettings
from yokai_admin.core.templating import STATIC_DIR, build_templates, render


def create_app(
    settings: PanelSettings,
    module_paths: Sequence[str],
    auth: AuthState,
    *,
    on_startup: Optional[Callable[[], None]] = None,
) -> FastAPI:
    """Build the panel. Duplicate module ids raise `ModuleConfigError`; other module failures
    are shown in the nav and never stop the app."""
    slots = load_modules(module_paths)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if on_startup is not None:
            on_startup()
        yield

    app = FastAPI(title="Yokai admin", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.slots = slots
    app.state.templates = build_templates(slots)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    for slot in slots:
        if slot.module is not None:
            app.include_router(slot.module.router, prefix=f"/m/{slot.module.id}")

    first = next((slot.module for slot in slots if slot.module is not None), None)

    @app.get("/", include_in_schema=False)
    def home(request: Request) -> Response:
        if first is None:
            return render(request, "no_modules.html")
        return RedirectResponse(f"/m/{first.id}", status_code=303)

    install_security(app, auth, settings.port)
    return app
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_admin_app.py tests/test_admin_modules.py tests/test_admin_security.py -q`
Expected: PASS. If the first-line length check trips ruff on `FastAPI(...)` or `_nav`, let `python -m ruff format` wrap them.

- [ ] **Step 5: Commit**

```powershell
git add src/yokai_admin/app.py src/yokai_admin/core/templating.py src/yokai_admin/templates tests/admin_helpers.py tests/admin_templates tests/test_admin_app.py
git commit -m "feat(admin): add the app factory, templating and shell" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Dashboard service (pure functions)

**Files:**
- Create: `src/yokai_admin/modules/__init__.py` (docstring only for now), `src/yokai_admin/modules/dashboard/__init__.py` (docstring only for now), `src/yokai_admin/modules/dashboard/service.py`
- Modify: `tests/admin_helpers.py` (add `seed_database`)
- Test: `tests/test_admin_dashboard_service.py`

**Interfaces:**
- Consumes: `readonly`, `schema_version` (Task 2); the bot's `Database` (tests).
- Produces (in `service.py`):
  - `ONLINE_WINDOW_S = 90`.
  - `BotStatus(state: Literal["online", "offline"], last_beat_ts: Optional[int], age_s: Optional[int], pid: Optional[int], version: Optional[str])`; `bot_status(conn, now: int) -> BotStatus` (age clamped at 0; `<= 90` is online; no row is offline with all fields None).
  - `Overview(db_size_bytes: int, schema_version: int, wallets: int, commands_24h: int)`; `overview(conn, db_path: Path, now: int) -> Overview`.
  - `recent_errors(log_dir: Path, limit: int = 8) -> list[str]` (reads only the last 256 KB of `yokai.log`; only lines whose level is `ERROR` or `CRITICAL` at the formatter position; each line cut to 400 characters; missing file gives `[]`).
  - `describe_age(seconds: int) -> str`, `format_bytes(n: int) -> str`, `status_note(status: Optional[BotStatus], db_error: Optional[str]) -> str`.
  - Test helper `seed_database(path, *, heartbeat_ts=None, wallets=0, commands=())` (creates a v4 database through the bot's `Database`, then closes it).

- [ ] **Step 1: Write the failing tests**

Add to `tests/admin_helpers.py` (imports at the top, definition at the bottom):

```python
import asyncio

from yokai.storage.db import Database


def seed_database(
    path: Path,
    *,
    heartbeat_ts: Optional[int] = None,
    wallets: int = 0,
    commands: Sequence[int] = (),
) -> None:
    """Create a real v4 database with the given heartbeat, wallets and command timestamps."""

    async def run() -> None:
        db = Database(path)
        await db.initialize()
        try:
            if heartbeat_ts is not None:
                await db.write_heartbeat(pid=4242, version="0.1.0", now=heartbeat_ts)
            for user_id in range(1, wallets + 1):
                await db.credit_wallet(user_id, 10, reason="daily", welcome_bonus=200)
            for ts in commands:
                await db.conn.execute(
                    "INSERT INTO command_log (ts, user_id, command) VALUES (?, 1, 'play');", (ts,)
                )
            await db.conn.commit()
        finally:
            await db.close()

    asyncio.run(run())
```

Create `tests/test_admin_dashboard_service.py`:

```python
"""Tests for the Dashboard's data functions, against real databases and log files."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.admin_helpers import seed_database
from yokai_admin.core.db import readonly
from yokai_admin.modules.dashboard.service import (
    ONLINE_WINDOW_S,
    BotStatus,
    bot_status,
    describe_age,
    format_bytes,
    overview,
    recent_errors,
    status_note,
)

NOW = 1_000_000


def test_a_fresh_heartbeat_is_online(tmp_path: Path) -> None:
    seed_database(tmp_path / "b.db", heartbeat_ts=NOW - 30)
    with readonly(tmp_path / "b.db") as conn:
        status = bot_status(conn, NOW)
    assert status == BotStatus("online", NOW - 30, 30, 4242, "0.1.0")


@pytest.mark.parametrize(("age", "state"), [(ONLINE_WINDOW_S, "online"), (ONLINE_WINDOW_S + 1, "offline")])
def test_the_online_window_boundary_is_ninety_seconds(tmp_path: Path, age: int, state: str) -> None:
    """Review Focus 6: exactly 90 s is still online, 91 s is not."""
    seed_database(tmp_path / "b.db", heartbeat_ts=NOW - age)
    with readonly(tmp_path / "b.db") as conn:
        assert bot_status(conn, NOW).state == state


def test_a_heartbeat_from_the_future_does_not_give_a_negative_age(tmp_path: Path) -> None:
    seed_database(tmp_path / "b.db", heartbeat_ts=NOW + 500)
    with readonly(tmp_path / "b.db") as conn:
        status = bot_status(conn, NOW)
    assert status.age_s == 0 and status.state == "online"


def test_no_heartbeat_row_means_offline_with_no_details(tmp_path: Path) -> None:
    seed_database(tmp_path / "b.db")
    with readonly(tmp_path / "b.db") as conn:
        assert bot_status(conn, NOW) == BotStatus("offline", None, None, None, None)


def test_overview_counts_wallets_and_recent_commands(tmp_path: Path) -> None:
    seed_database(
        tmp_path / "b.db",
        wallets=3,
        commands=[NOW - 10, NOW - 86_399, NOW - 86_401],  # two inside 24 h, one outside
    )
    with readonly(tmp_path / "b.db") as conn:
        data = overview(conn, tmp_path / "b.db", NOW)
    assert (data.wallets, data.commands_24h, data.schema_version) == (3, 2, 4)
    assert data.db_size_bytes > 0


def test_recent_errors_keeps_only_error_and_critical_levels_in_order(tmp_path: Path) -> None:
    (tmp_path / "yokai.log").write_text(
        "2026-10-05 10:00:00 [INFO] yokai: started\n"
        "2026-10-05 10:00:01 [ERROR] yokai.bot: first failure\n"
        "2026-10-05 10:00:02 [WARNING] yokai: careful\n"
        "2026-10-05 10:00:03 [CRITICAL] yokai: second failure\n",
        encoding="utf-8",
    )
    assert recent_errors(tmp_path) == [
        "2026-10-05 10:00:01 [ERROR] yokai.bot: first failure",
        "2026-10-05 10:00:03 [CRITICAL] yokai: second failure",
    ]


def test_an_info_line_that_merely_mentions_error_is_not_an_error(tmp_path: Path) -> None:
    (tmp_path / "yokai.log").write_text(
        "2026-10-05 10:00:00 [INFO] yokai: saw [ERROR] in a user's track title\n",
        encoding="utf-8",
    )
    assert recent_errors(tmp_path) == []


def test_recent_errors_respects_the_limit_and_truncates_long_lines(tmp_path: Path) -> None:
    lines = [f"2026-10-05 10:00:{i:02d} [ERROR] yokai: failure {i}" for i in range(12)]
    lines.append("2026-10-05 10:01:00 [ERROR] yokai: " + "x" * 1000)
    (tmp_path / "yokai.log").write_text("\n".join(lines), encoding="utf-8")
    found = recent_errors(tmp_path, limit=3)
    assert len(found) == 3 and found[0].endswith("failure 11")
    assert len(found[-1]) == 400


def test_a_missing_log_is_an_empty_list(tmp_path: Path) -> None:
    assert recent_errors(tmp_path / "no-such-dir") == []


def test_a_huge_log_is_read_from_the_tail_and_bad_bytes_do_not_crash(tmp_path: Path) -> None:
    filler = "2026-10-05 10:00:00 [INFO] yokai: filler line\n" * 20_000  # about 900 KB
    final = b"2026-10-05 11:00:00 [ERROR] yokai: caf\xe9 broke\n"
    (tmp_path / "yokai.log").write_bytes(filler.encode() + final)
    found = recent_errors(tmp_path)
    assert len(found) == 1 and found[0].startswith("2026-10-05 11:00:00 [ERROR]")


@pytest.mark.parametrize(
    ("seconds", "text"),
    [(0, "0 seconds"), (1, "1 second"), (59, "59 seconds"), (60, "1 minute"),
     (300, "5 minutes"), (7200, "2 hours"), (3 * 86_400, "3 days")],
)
def test_describe_age(seconds: int, text: str) -> None:
    assert describe_age(seconds) == text


@pytest.mark.parametrize(
    ("size", "text"), [(512, "512 B"), (2048, "2.0 KB"), (5 * 1024 * 1024, "5.00 MB")]
)
def test_format_bytes(size: int, text: str) -> None:
    assert format_bytes(size) == text


def test_status_note_covers_each_state() -> None:
    online = BotStatus("online", NOW - 12, 12, 1, "0.1.0")
    offline = BotStatus("offline", NOW - 300, 300, 1, "0.1.0")
    never = BotStatus("offline", None, None, None, None)
    assert status_note(online, None) == "Last heartbeat 12 seconds ago."
    assert status_note(offline, None) == "Last heartbeat 5 minutes ago."
    assert "No heartbeat on record" in status_note(never, None)
    assert status_note(None, "Database not found.") == "Database not found."
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_admin_dashboard_service.py -q`
Expected: FAIL (`ModuleNotFoundError: yokai_admin.modules`).

- [ ] **Step 3: Implement**

Create `src/yokai_admin/modules/__init__.py` containing only `"""Admin panel modules. Each package exports one `MODULE`."""` and `src/yokai_admin/modules/dashboard/__init__.py` containing only `"""Dashboard module: bot status and vital signs."""` (Task 8 fills both in).

Create `src/yokai_admin/modules/dashboard/service.py`:

```python
"""Dashboard data: bot liveness from the heartbeat, database vitals, recent log errors."""

from __future__ import annotations

import logging
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from yokai_admin.core.db import schema_version

logger = logging.getLogger(__name__)

ONLINE_WINDOW_S = 90
MAX_TAIL_BYTES = 256 * 1024
MAX_LINE_CHARS = 400
# The bot formats lines as "%(asctime)s [%(levelname)s] ..."; match the level in that position
# only, so an INFO line that merely contains the text "[ERROR]" is not counted.
_ERROR_LINE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \[(ERROR|CRITICAL)\]")


@dataclass(frozen=True, slots=True)
class BotStatus:
    """Whether the bot is alive according to its heartbeat row."""

    state: Literal["online", "offline"]
    last_beat_ts: Optional[int]
    age_s: Optional[int]
    pid: Optional[int]
    version: Optional[str]


@dataclass(frozen=True, slots=True)
class Overview:
    """Database vitals shown as readouts."""

    db_size_bytes: int
    schema_version: int
    wallets: int
    commands_24h: int


def bot_status(conn: sqlite3.Connection, now: int) -> BotStatus:
    """Read the heartbeat row. Online means a beat no older than `ONLINE_WINDOW_S` seconds."""
    row = conn.execute("SELECT ts, pid, version FROM bot_heartbeat WHERE id = 1;").fetchone()
    if row is None:
        return BotStatus("offline", None, None, None, None)
    age = max(0, now - int(row["ts"]))
    state: Literal["online", "offline"] = "online" if age <= ONLINE_WINDOW_S else "offline"
    return BotStatus(state, int(row["ts"]), age, int(row["pid"]), str(row["version"]))


def _scalar(conn: sqlite3.Connection, sql: str, params: tuple[object, ...] = ()) -> int:
    return int(conn.execute(sql, params).fetchone()[0])


def overview(conn: sqlite3.Connection, db_path: Path, now: int) -> Overview:
    """Collect the database vitals."""
    return Overview(
        db_size_bytes=db_path.stat().st_size,
        schema_version=schema_version(conn),
        wallets=_scalar(conn, "SELECT COUNT(*) FROM wallets;"),
        commands_24h=_scalar(
            conn, "SELECT COUNT(*) FROM command_log WHERE ts >= ?;", (now - 86_400,)
        ),
    )


def recent_errors(log_dir: Path, limit: int = 8) -> list[str]:
    """Return the last `limit` ERROR/CRITICAL lines of the current log (reads only its tail)."""
    log_file = log_dir / "yokai.log"
    try:
        with log_file.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - MAX_TAIL_BYTES))
            data = handle.read()
    except FileNotFoundError:
        return []
    except OSError as exc:
        logger.warning("Could not read the log file %s: %s", log_file, exc)
        return []

    lines = data.decode("utf-8", errors="replace").splitlines()
    if size > MAX_TAIL_BYTES and lines:
        lines = lines[1:]  # the first line of a tail read is usually cut in half
    errors = [line for line in lines if _ERROR_LINE.match(line)]
    return [line[:MAX_LINE_CHARS] for line in errors[-limit:]]


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


def format_bytes(size: int) -> str:
    """Human-readable file size."""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.2f} MB"


def status_note(status: Optional[BotStatus], db_error: Optional[str]) -> str:
    """The sentence under the status word."""
    if db_error:
        return db_error
    if status is None or status.age_s is None:
        return "No heartbeat on record. The bot has not started, or it shut down cleanly."
    return f"Last heartbeat {describe_age(status.age_s)} ago."
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_admin_dashboard_service.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/yokai_admin/modules tests/admin_helpers.py tests/test_admin_dashboard_service.py
git commit -m "feat(admin): add Dashboard data functions" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Dashboard module (routes, templates, tiles)

**Files:**
- Create: `src/yokai_admin/modules/dashboard/router.py`, `src/yokai_admin/modules/dashboard/templates/dashboard/page.html`, `src/yokai_admin/modules/dashboard/templates/dashboard/_status.html`
- Modify: `src/yokai_admin/modules/dashboard/__init__.py` (export `MODULE`), `src/yokai_admin/modules/__init__.py` (add `MODULE_PATHS`)
- Test: `tests/test_admin_dashboard.py`

**Interfaces:**
- Consumes: `service` functions (Task 7); `render`, `render_fragment` (Task 6); `render_tiles`, `TileContext` (Task 3); `readonly`, `require_schema` (Task 2); `PanelDbError`.
- Produces: `MODULE = AdminModule(id="dashboard", title="Dashboard", nav_order=0, ...)`; `MODULE_PATHS = ("yokai_admin.modules.dashboard:MODULE",)`; `GET /m/dashboard` (full page) and `GET /m/dashboard/status` (fragment `<section id="status" hx-get="/m/dashboard/status" hx-trigger="every 5s" hx-swap="outerHTML">`). Module tiles from other modules appear under "Modules"; a failing tile shows its error row. The page never returns 500 for a missing, old, locked or corrupt database; it shows the reason where the note goes and the status word reads "No data".

- [ ] **Step 1: Write the failing tests**

Create `tests/test_admin_dashboard.py`:

```python
"""Tests for the Dashboard module through the real app."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from tests.admin_helpers import panel_client, seed_database

DASH = "yokai_admin.modules.dashboard:MODULE"
H = "tests.admin_helpers"


def test_an_online_bot_shows_the_status_vitals_and_a_ping(tmp_path: Path) -> None:
    db = tmp_path / "yokai.db"
    seed_database(db, heartbeat_ts=int(time.time()) - 5, wallets=2)
    with panel_client(tmp_path, [DASH], db_path=db) as (client, _):
        page = client.get("/m/dashboard").text
    assert ">Online<" in page and 'data-state="online"' in page
    assert "Last heartbeat" in page and "4242" in page and "0.1.0" in page
    assert "Schema version" in page and "Wallets" in page and "Commands in the last 24 hours" in page
    assert '<a href="/m/dashboard" aria-current="page">Dashboard</a>' in page


def test_a_stale_heartbeat_reads_offline(tmp_path: Path) -> None:
    db = tmp_path / "yokai.db"
    seed_database(db, heartbeat_ts=int(time.time()) - 300)
    with panel_client(tmp_path, [DASH], db_path=db) as (client, _):
        page = client.get("/m/dashboard").text
    assert ">Offline<" in page and 'data-state="offline"' in page and "5 minutes ago" in page


def test_no_heartbeat_row_says_so(tmp_path: Path) -> None:
    db = tmp_path / "yokai.db"
    seed_database(db)
    with panel_client(tmp_path, [DASH], db_path=db) as (client, _):
        assert "No heartbeat on record" in client.get("/m/dashboard").text


def test_a_missing_database_still_renders_with_the_reason(tmp_path: Path) -> None:
    """Review Focus 4: no 500, a clear message."""
    with panel_client(tmp_path, [DASH], db_path=tmp_path / "absent.db") as (client, _):
        response = client.get("/m/dashboard")
    assert response.status_code == 200
    assert ">No data<" in response.text and "not found" in response.text


def test_an_old_schema_renders_with_the_upgrade_hint(tmp_path: Path) -> None:
    old = tmp_path / "old.db"
    raw = sqlite3.connect(old)
    raw.execute("PRAGMA user_version = 3;")
    raw.execute("CREATE TABLE t (x INTEGER);")
    raw.commit()
    raw.close()
    with panel_client(tmp_path, [DASH], db_path=old) as (client, _):
        response = client.get("/m/dashboard")
    assert response.status_code == 200 and "needs v4" in response.text


def test_a_database_that_is_not_sqlite_renders_without_a_server_error(tmp_path: Path) -> None:
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"not a database" * 100)
    with panel_client(tmp_path, [DASH], db_path=junk) as (client, _):
        response = client.get("/m/dashboard")
    assert response.status_code == 200 and "not a SQLite database" in response.text


def test_the_status_fragment_has_no_shell_and_keeps_polling(tmp_path: Path) -> None:
    db = tmp_path / "yokai.db"
    seed_database(db, heartbeat_ts=int(time.time()))
    with panel_client(tmp_path, [DASH], db_path=db) as (client, csrf):
        fragment = client.get("/m/dashboard/status", headers={"HX-Request": "true"}).text
        full = client.get("/m/dashboard").text
    assert "<html" not in fragment and "<aside" not in fragment
    assert 'hx-get="/m/dashboard/status"' in fragment and 'hx-trigger="every 5s"' in fragment
    assert 'hx-swap="outerHTML"' in fragment
    assert 'id="status"' in full and csrf in full


def test_the_fragment_is_locked_without_a_session(tmp_path: Path) -> None:
    with panel_client(tmp_path, [DASH], login=False) as (client, _):
        assert client.get("/m/dashboard/status").status_code == 401


def test_other_modules_tiles_appear_and_a_failing_tile_is_isolated(tmp_path: Path) -> None:
    db = tmp_path / "yokai.db"
    seed_database(db, heartbeat_ts=int(time.time()))
    with panel_client(tmp_path, [DASH, f"{H}:ALPHA", f"{H}:BROKEN_TILE"], db_path=db) as (c, _):
        response = c.get("/m/dashboard")
    assert response.status_code == 200
    assert "Queue" in response.text and "3 tracks" in response.text
    assert "Could not read this tile." in response.text


def test_log_lines_are_escaped_and_an_empty_log_says_so(tmp_path: Path) -> None:
    """Review Focus 3: a hostile log line is text, never markup."""
    db = tmp_path / "yokai.db"
    seed_database(db)
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "yokai.log").write_text(
        "2026-10-05 10:00:00 [ERROR] yokai: <script>alert(1)</script>\n", encoding="utf-8"
    )
    with panel_client(tmp_path, [DASH], db_path=db) as (client, _):
        page = client.get("/m/dashboard").text
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page

    (logs / "yokai.log").write_text("2026-10-05 10:00:00 [INFO] yokai: all quiet\n", encoding="utf-8")
    with panel_client(tmp_path, [DASH], db_path=db) as (client, _):
        assert "No errors in the current log." in client.get("/m/dashboard").text


def test_modules_that_failed_to_load_are_listed_on_the_dashboard(tmp_path: Path) -> None:
    db = tmp_path / "yokai.db"
    seed_database(db)
    with panel_client(tmp_path, [DASH, f"{H}:MISSING"], db_path=db) as (client, _):
        page = client.get("/m/dashboard").text
    assert "1 module did not load" in page and f"{H}:MISSING" in page
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_admin_dashboard.py -q`
Expected: FAIL (`ModuleNotFoundError` or an import error for `yokai_admin.modules.dashboard:MODULE`, shown as errored slots; assertions fail).

- [ ] **Step 3: Implement**

Create `src/yokai_admin/modules/dashboard/router.py`:

```python
"""Dashboard routes: the full page and the HTMX status fragment."""

from __future__ import annotations

import logging
import sqlite3
import time
from typing import Any, Optional

from fastapi import APIRouter, Request
from fastapi.responses import Response

from yokai_admin.core.db import readonly, require_schema
from yokai_admin.core.module import TileContext, render_tiles
from yokai_admin.core.templating import render, render_fragment
from yokai_admin.errors import PanelDbError
from yokai_admin.modules.dashboard.service import (
    BotStatus,
    Overview,
    bot_status,
    format_bytes,
    overview,
    recent_errors,
    status_note,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _snapshot(request: Request) -> dict[str, Any]:
    settings = request.app.state.settings
    now = int(time.time())
    status: Optional[BotStatus] = None
    stats: Optional[Overview] = None
    db_error: Optional[str] = None
    try:
        with readonly(settings.db_path) as conn:
            require_schema(conn)
            status = bot_status(conn, now)
            stats = overview(conn, settings.db_path, now)
    except PanelDbError as exc:
        db_error = str(exc)
    except sqlite3.Error as exc:
        logger.warning("Dashboard could not read the database: %s", exc)
        db_error = f"The database could not be read right now ({exc}). It will retry shortly."

    slots = request.app.state.slots
    return {
        "status": status,
        "stats": stats,
        "db_size": format_bytes(stats.db_size_bytes) if stats else "",
        "note": status_note(status, db_error),
        "errors": recent_errors(settings.log_dir),
        "tiles": render_tiles(slots, TileContext(settings=settings, now=now)),
    }


@router.get("")
def page(request: Request) -> Response:
    load_errors = [slot for slot in request.app.state.slots if slot.error]
    return render(
        request,
        "dashboard/page.html",
        active="dashboard",
        load_errors=load_errors,
        **_snapshot(request),
    )


@router.get("/status")
def status_fragment(request: Request) -> Response:
    return render_fragment(request, "dashboard/_status.html", **_snapshot(request))
```

Note: the "needs v4" assertion in the tests comes from `PanelDbError`'s message ("the panel needs v4 or newer"), so `status_note` returns it unchanged.

Create `src/yokai_admin/modules/dashboard/templates/dashboard/page.html`:

```html
{% extends "base.html" %}
{% block title %}Dashboard - Yokai admin{% endblock %}
{% block content %}
  {% if load_errors %}
  <div class="notice" role="status">
    <strong>{{ load_errors|length }} module{{ "s" if load_errors|length != 1 }} did not load.</strong>
    <ul>
      {% for slot in load_errors %}<li><code>{{ slot.path }}</code>: {{ slot.error }}</li>{% endfor %}
    </ul>
  </div>
  {% endif %}
  {% include "dashboard/_status.html" %}
{% endblock %}
```

Create `src/yokai_admin/modules/dashboard/templates/dashboard/_status.html`:

```html
<section id="status" hx-get="/m/dashboard/status" hx-trigger="every 5s" hx-swap="outerHTML">
  <div class="status">
    <div class="status-mark">
      <div class="ping" data-state="{{ 'online' if status and status.state == 'online' else 'offline' }}" aria-hidden="true">
        <span class="ping-ring"></span><span class="ping-ring"></span>
      </div>
    </div>
    <div>
      <h1 class="status-word">{% if not status %}No data{% elif status.state == "online" %}Online{% else %}Offline{% endif %}</h1>
      <p class="status-note">{{ note }}</p>
    </div>
  </div>

  {% if stats %}
  <dl class="readouts">
    <dt>Database size</dt><dd>{{ db_size }}</dd>
    <dt>Schema version</dt><dd>{{ stats.schema_version }}</dd>
    <dt>Wallets</dt><dd>{{ stats.wallets }}</dd>
    <dt>Commands in the last 24 hours</dt><dd>{{ stats.commands_24h }}</dd>
    {% if status and status.version %}<dt>Bot version</dt><dd>{{ status.version }}</dd>{% endif %}
    {% if status and status.pid %}<dt>Process ID</dt><dd>{{ status.pid }}</dd>{% endif %}
  </dl>
  {% endif %}

  {% if tiles %}
  <div class="section">
    <h3>Modules</h3>
    <dl class="readouts">
      {% for result in tiles %}
        {% if result.tile %}
          <dt>{{ result.tile.title }}</dt>
          <dd>
            <span class="mark" data-status="{{ result.tile.status }}"></span>
            <span class="sr-only">{{ result.tile.status }}: </span>{{ result.tile.value }}
            {% if result.tile.detail %}<span class="detail">{{ result.tile.detail }}</span>{% endif %}
          </dd>
        {% else %}
          <dt>{{ result.module_title }}</dt>
          <dd><span class="mark" data-status="error"></span><span class="sr-only">error: </span>{{ result.error }}</dd>
        {% endif %}
      {% endfor %}
    </dl>
  </div>
  {% endif %}

  <div class="section">
    <h3>Recent errors</h3>
    {% if errors %}
      <pre class="log">{{ errors|join("\n") }}</pre>
    {% else %}
      <p class="empty">No errors in the current log.</p>
    {% endif %}
  </div>
</section>
```

Replace `src/yokai_admin/modules/dashboard/__init__.py`:

```python
"""Dashboard module: bot status and vital signs. Home page of the panel."""

from __future__ import annotations

from pathlib import Path

from yokai_admin.core.module import AdminModule
from yokai_admin.modules.dashboard.router import router

MODULE = AdminModule(
    id="dashboard",
    title="Dashboard",
    router=router,
    nav_order=0,
    templates_dir=Path(__file__).parent / "templates",
)
```

Replace `src/yokai_admin/modules/__init__.py`:

```python
"""Admin panel modules.

To add a module: create a package here that exports `MODULE = AdminModule(...)`, then add its
"package.module:NAME" path below. A module that fails to import is shown as failed in the nav and
never stops the panel.
"""

from __future__ import annotations

MODULE_PATHS: tuple[str, ...] = ("yokai_admin.modules.dashboard:MODULE",)
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_admin_dashboard.py tests/test_admin_dashboard_service.py tests/test_admin_app.py -q`
Expected: PASS. If a `>Online<` style assertion fails only because of whitespace in the rendered heading, keep the template's single-line `<h1>` as written (the tests depend on it).

- [ ] **Step 5: Commit**

```powershell
git add src/yokai_admin/modules tests/test_admin_dashboard.py
git commit -m "feat(admin): add the Dashboard module" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Launcher

**Files:**
- Create: `src/yokai_admin/launch.py`, `src/yokai_admin/__main__.py`
- Test: `tests/test_admin_launch.py`

**Interfaces:**
- Consumes: `create_app` (Task 6); `AuthState` (Task 4); `MODULE_PATHS` (Task 8); `load_settings`, `PanelSettings`, `HOST` (Task 1); `readonly`, `require_schema` (Task 2); `PanelConfigError`, `PanelDbError`.
- Produces: `launch.auth_url(port: int, token: str) -> str`; `launch.build_panel(settings, *, opener: Callable[[str], object] = webbrowser.open) -> tuple[FastAPI, str]` (returns the app and the single-use launch URL; the opener is called once, at server startup); `launch.preflight(settings) -> None` (raises `PanelDbError`); `launch.run(settings, *, open_browser: bool = True) -> int` (0 after a normal stop, 1 when the port is unavailable); `__main__.main() -> int` (1 on bad config, missing or too-old database; logs to the console only).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_admin_launch.py`:

```python
"""Tests for starting the panel: launch URL, preflight, console-only logging, a real server."""

from __future__ import annotations

import logging
import socket
import sqlite3
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from starlette.testclient import TestClient

from tests.admin_helpers import seed_database
from yokai_admin.__main__ import main
from yokai_admin.core.settings import HOST, PanelSettings
from yokai_admin.launch import auth_url, build_panel, preflight, run


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind((HOST, 0))
        return int(sock.getsockname()[1])


def test_the_launch_url_carries_the_port_and_token() -> None:
    assert auth_url(8765, "abc") == "http://127.0.0.1:8765/auth?token=abc"


def test_building_the_panel_opens_the_browser_once_at_startup(tmp_path: Path) -> None:
    opened: list[str] = []
    settings = PanelSettings(db_path=tmp_path / "x.db", log_dir=tmp_path, port=8765)
    app, url = build_panel(settings, opener=opened.append)
    assert opened == []  # nothing happens until the server starts
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        assert opened == [url]
        followed = client.get(url.replace("http://127.0.0.1:8765", ""), follow_redirects=False)
        assert followed.status_code == 303  # the launch URL really logs in


def test_the_token_url_is_never_written_to_a_file_handler(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Review Focus 5: the single-use URL goes to the console logger only."""
    settings = PanelSettings(db_path=tmp_path / "x.db", log_dir=tmp_path, port=8765)
    app, url = build_panel(settings, opener=lambda _url: None)
    with caplog.at_level(logging.INFO, logger="yokai_admin"):
        with TestClient(app, base_url="http://127.0.0.1:8765"):
            pass
    assert url in caplog.text
    assert not [
        h for h in logging.getLogger().handlers if isinstance(h, logging.FileHandler)
    ]


def test_preflight_names_the_problem_for_a_missing_or_old_database(tmp_path: Path) -> None:
    from yokai_admin.errors import PanelDbError

    with pytest.raises(PanelDbError, match="not found"):
        preflight(PanelSettings(db_path=tmp_path / "none.db", log_dir=tmp_path))

    old = tmp_path / "old.db"
    raw = sqlite3.connect(old)
    raw.execute("PRAGMA user_version = 3;")
    raw.execute("CREATE TABLE t (x INTEGER);")
    raw.commit()
    raw.close()
    with pytest.raises(PanelDbError, match="v3"):
        preflight(PanelSettings(db_path=old, log_dir=tmp_path))


def test_main_exits_1_with_a_message_when_the_database_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("DB_PATH", str(tmp_path / "missing.db"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path))
    with caplog.at_level(logging.ERROR):
        assert main() == 1
    assert "not found" in caplog.text


def test_main_exits_1_on_a_bad_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("ADMIN_PORT", "80")
    with caplog.at_level(logging.ERROR):
        assert main() == 1
    assert "ADMIN_PORT" in caplog.text


def test_run_returns_1_with_a_hint_when_the_port_is_taken(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Review Focus 5: a busy port is a friendly exit, not a traceback or a hang."""
    seed_database(tmp_path / "yokai.db")
    with socket.socket() as blocker:
        blocker.bind((HOST, 0))
        blocker.listen()
        port = int(blocker.getsockname()[1])
        settings = PanelSettings(db_path=tmp_path / "yokai.db", log_dir=tmp_path, port=port)
        with caplog.at_level(logging.ERROR):
            assert run(settings, open_browser=False) == 1
    assert "ADMIN_PORT" in caplog.text


def test_a_real_server_logs_in_with_the_launch_url_and_serves_the_dashboard(
    tmp_path: Path,
) -> None:
    db = tmp_path / "yokai.db"
    seed_database(db, heartbeat_ts=int(time.time()))
    port = _free_port()
    settings = PanelSettings(db_path=db, log_dir=tmp_path / "logs", port=port)
    app, url = build_panel(settings, opener=lambda _url: None)
    server = uvicorn.Server(
        uvicorn.Config(app, host=HOST, port=port, log_config=None, log_level="warning", access_log=False)
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(200):
            if server.started:
                break
            time.sleep(0.05)
        assert server.started
        with httpx.Client(follow_redirects=True) as client:
            page = client.get(url)
            assert page.status_code == 200 and ">Online<" in page.text
            assert page.headers["content-security-policy"].startswith("default-src 'none'")
        assert httpx.get(url).status_code == 401  # the link is single-use
        assert httpx.get(f"http://{HOST}:{port}/").status_code == 401  # no cookie, no panel
    finally:
        server.should_exit = True
        thread.join(timeout=10)
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_admin_launch.py -q`
Expected: FAIL (`ModuleNotFoundError: yokai_admin.launch`).

- [ ] **Step 3: Implement**

Create `src/yokai_admin/launch.py`:

```python
"""Starting the panel: build the app and the single-use launch URL, then serve on loopback."""

from __future__ import annotations

import logging
import webbrowser
from typing import Callable

import uvicorn
from fastapi import FastAPI

from yokai_admin.app import create_app
from yokai_admin.core.auth import AuthState
from yokai_admin.core.db import readonly, require_schema
from yokai_admin.core.settings import HOST, PanelSettings
from yokai_admin.modules import MODULE_PATHS

logger = logging.getLogger("yokai_admin")


def auth_url(port: int, token: str) -> str:
    """The link that logs the first browser in. The token works exactly once."""
    return f"http://{HOST}:{port}/auth?token={token}"


def preflight(settings: PanelSettings) -> None:
    """Fail early, with a clear reason, when the bot's database is missing or too old."""
    with readonly(settings.db_path) as conn:
        require_schema(conn)


def build_panel(
    settings: PanelSettings, *, opener: Callable[[str], object] = webbrowser.open
) -> tuple[FastAPI, str]:
    """Create the app and its launch URL; `opener` is called once when the server starts."""
    auth = AuthState()
    url = auth_url(settings.port, auth.token)

    def announce() -> None:
        # Console only: the root logger of `python -m yokai_admin` has no file handler.
        logger.info("Admin panel ready. This link works once: %s", url)
        opener(url)

    return create_app(settings, MODULE_PATHS, auth, on_startup=announce), url


def run(settings: PanelSettings, *, open_browser: bool = True) -> int:
    """Serve the panel until stopped. Returns 1 when the port cannot be used."""
    app, _ = build_panel(
        settings, opener=webbrowser.open if open_browser else (lambda _url: None)
    )
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=HOST,
            port=settings.port,
            log_config=None,  # keep our own logging setup
            log_level="warning",
            access_log=False,  # request lines would include the launch token
        )
    )
    try:
        server.run()
    # uvicorn logs a bind failure and then calls sys.exit(1); an OSError can also escape.
    except (OSError, SystemExit) as exc:
        logger.error(
            "Could not listen on %s:%d (%s). Set ADMIN_PORT to a free port in .env.",
            HOST,
            settings.port,
            exc,
        )
        return 1
    if not server.started:
        logger.error(
            "The panel did not start on %s:%d. Set ADMIN_PORT to a free port in .env.",
            HOST,
            settings.port,
        )
        return 1
    return 0
```

Create `src/yokai_admin/__main__.py`:

```python
"""Run the admin panel: `python -m yokai_admin`."""

from __future__ import annotations

import logging
import sys

from yokai_admin.core.settings import load_settings
from yokai_admin.errors import PanelConfigError, PanelDbError
from yokai_admin.launch import preflight, run

logger = logging.getLogger("yokai_admin")


def main() -> int:
    """Load settings, check the database, then serve the panel on 127.0.0.1."""
    # Console only, on purpose: the single-use launch link is printed here and must not be
    # written to a log file.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    try:
        settings = load_settings()
        preflight(settings)
    except (PanelConfigError, PanelDbError) as exc:
        logger.error("%s", exc)
        return 1
    return run(settings)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_admin_launch.py -q`
Expected: PASS. If `test_run_returns_1...` hangs, uvicorn is not exiting on a taken port on this platform: confirm with `server.started` after `run` returns and adjust the `except` clause to the exception the failing bind actually raises, keeping the "friendly exit 1" behavior; do not skip the test. If the real-server test is flaky on startup timing, raise the wait loop, never remove the assertions.

- [ ] **Step 5: Commit**

```powershell
git add src/yokai_admin/launch.py src/yokai_admin/__main__.py tests/test_admin_launch.py
git commit -m "feat(admin): add the launcher with single-use launch link" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Docs and full verification

**Files:**
- Modify: `README.md` (new section after "Running Yokai", before "Updating yt-dlp"), `TASKS.md` (Phase 9), `docs/superpowers/specs/2026-10-05-admin-panel-design.md` (§4 contract deltas)

- [ ] **Step 1: Update the spec's module contract (§4) to match what was built**

In §4 replace the contract sentence with: ``Each module package exports `MODULE = AdminModule(id, title, router, nav_order, templates_dir=None, tile=None)` (no icon: the rail is text-only). `modules/__init__.py` holds an explicit `MODULE_PATHS` tuple of `"package.module:NAME"` strings; the loader imports each inside an isolation boundary, so a module that fails to import is shown as failed in the nav and never stops the panel. Duplicate ids fail fast at startup.`` Also in §3 add `launch.py` to the `yokai_admin` file list and in §8 add: the launch URL is printed to the console only and works once.

- [ ] **Step 2: Add the README section**

Insert before `## Updating yt-dlp`:

````markdown
## Admin panel

A local web panel for the owner (bot status today; users, database tools, access lists and logs arrive in later phases). It runs as its own process, listens on `127.0.0.1` only, and works even while the bot is off.

```powershell
python -m yokai_admin
```

It opens your browser through a **single-use link** (also printed in the console). If you close the tab, restart the panel to get a new link. Settings in `.env`: `ADMIN_PORT` (default `8765`), `DB_PATH`, `LOG_DIR`. The panel never needs the Discord token, and it only reads the database in this version.
````

- [ ] **Step 3: Add the Phase 9 entry for P-A2 in `TASKS.md`**

Change `- [ ] P-A2 ...` to `- [x] P-A2 ...` and add under the Phase 9 acceptance criteria:

```markdown
**Acceptance criteria (P-A2)**
- `python -m yokai_admin` serves on `127.0.0.1:ADMIN_PORT`, opens the browser through a single-use
  link, and refuses everything else (no cookie: locked page; used or wrong token: refused;
  foreign `Host` or `Origin`: refused; state-changing requests without the CSRF header: refused).
- Every response carries a strict CSP; the panel makes no outbound requests (htmx and the font
  are vendored with recorded hashes).
- A module exports `MODULE` and is listed in `MODULE_PATHS`; one that fails to import appears as
  failed in the nav and on the Dashboard without stopping the panel; duplicate ids stop startup.
- The Dashboard shows online/offline from the heartbeat (90 s window), database size, schema,
  wallets, commands in 24 h, module tiles and recent log errors, refreshes every 5 s, and still
  renders with a reason when the database is missing, too old or unreadable.
- Hostile text (log lines, module paths, query parameters) is escaped on screen.
- `pytest`, `ruff check` and `ruff format --check` are clean.
- *(manual, real browser)* With the bot running, open the panel: the status reads Online and a sonar
  ring plays about every 5 s; stop the bot with Ctrl+C and within a few seconds the Dashboard
  reads Offline with no ring; use a phone-width window and confirm the rail becomes a top bar;
  switch the OS to dark mode and confirm the dark palette; enable "reduce motion" and confirm the
  ring stops; reuse the launch link in another browser and confirm it is refused; start a second
  panel and confirm the port message.
```

- [ ] **Step 4: Run the full verification**

```powershell
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
```

Expected: all tests pass (the P-A1 baseline of 568 plus the new panel tests), ruff clean. If `ruff format --check` lists files, run `python -m ruff format <files>`, then re-run all three. Report any failure by name; do not mark P-A2 done on a red run.

- [ ] **Step 5: Commit and stop for owner review**

```powershell
git add README.md TASKS.md docs/superpowers/specs/2026-10-05-admin-panel-design.md
git commit -m "docs(admin): record admin panel phase P-A2" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

Then summarize what changed and list what is unverified (the manual real-browser checks above), per `AGENTS.md`, and wait for review. Do not start P-A3.

---

## Self-review

**Spec coverage (§2, §3, §4, §7 Dashboard, §8, §9, §11 P-A2):** FastAPI + Jinja2 + HTMX stack and vendored assets: Tasks 1, 5. Separate process, sync `sqlite3`, read-only: Task 2. Layout (`core/`, `modules/dashboard`, `static`, `templates`): file structure. Module contract, isolation, tiles, duplicate ids: Task 3. Token to cookie, CSRF, Host/Origin, headers, no websockets, no docs: Task 4. Shell, nav, failed-module display, startup hook: Task 6. Dashboard: online/offline (90 s), DB size, schema version, wallet count, commands in 24 h, recent log errors, tiles, 5 s refresh: Tasks 7 and 8. `ADMIN_PORT` and `LOG_DIR` in `.env.example`, `SPEC.md` §11: Task 1 (`BACKUP_DIR` belongs to P-A5). Launcher with browser open and console-only link: Task 9. The spec's phase list says P-A2 also updates `SPEC.md` §11 and `.env.example`: Task 1.

**Placeholders:** none. Every code step shows its code; the one command-driven step (fetching vendored files) shows the commands and the expected sizes.

**Type consistency:** `PanelSettings(db_path, log_dir, port)`, `AdminModule(id, title, router, nav_order, templates_dir, tile)`, `ModuleSlot(path, module, error)`, `Tile`/`TileContext`/`TileResult`, `AuthState(token, clock, ttl_s)`, `install_security(app, auth, port)`, `create_app(settings, module_paths, auth, *, on_startup)`, `render(request, name, *, active, **ctx)`, `BotStatus`/`Overview`, `build_panel(settings, *, opener)`, `run(settings, *, open_browser)` are spelled the same wherever they appear.

**Review Focus coverage:** 1 to Task 4 (`test_a_non_loopback_host...`); 2 to Tasks 4 and 6; 3 to Tasks 6 and 8; 4 to Tasks 2 and 8; 5 to Tasks 4 and 9; 6 to Task 7.

**Known risks for the implementer:** (a) `python -m pip install -e ".[dev]"` must run in Task 1 before any import of `fastapi`; (b) the vendored-asset URLs were checked while planning (htmx 2.0.11 contains `version:"2.0.11"`; the Archivo `LICENSE` URL returns the OFL text), but if either has moved, Task 5 describes the fallback; (c) uvicorn's behavior on a taken port is asserted by a test rather than assumed, so adjust the `except` clause to what the platform actually raises; (d) Starlette's `TestClient` emits a deprecation warning about `httpx2`, which is expected and not an error.
