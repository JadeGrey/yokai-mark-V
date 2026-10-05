# Yokai Admin Panel — Design

Date: 2026-10-05 · Status: approved in conversation, awaiting written-spec review

## 1. Purpose and scope

A local, modular admin panel for the owner, running on the same Windows PC as the bot. It lets
the owner inspect and fix bot state without hand-editing SQLite, and it adds the moderation
primitives the bot lacks: a blacklist (users who cannot use the bot) and an adminlist (users who
can run admin commands).

**v1 modules:** Dashboard, Users (view/edit), Database, Access (blacklist + adminlist), Logs
(current + rotated), Audit (admin-action log).

**Non-goals (v1):** internet exposure, multi-user panel accounts, Discord OAuth, free-form row
editing, a read-only SQL console, analytics charts, a config viewer, live bot control (kick from
voice, etc.), Discord username lookup, `/blacklist` slash commands. These go on a "later" list.

**Assumptions:** single owner, panel never leaves 127.0.0.1, bot and panel share one SQLite file.

## 2. Locked decisions

- Form: local web UI. Stack: FastAPI + Jinja2 + HTMX (vendored static JS, no npm, no build step).
- Process model: separate process (`python -m yokai_admin`), shared SQLite (WAL). No bot<->panel
  network protocol. The bot polls the DB with a short cache.
- Panel DB layer: thin synchronous `sqlite3`, not the bot's `aiosqlite` `Database` class.
- Auth: localhost bind + random per-launch token exchanged for a session cookie.
- New dependencies (approved): `fastapi`, `uvicorn`, `jinja2`, `python-multipart`; dev-only
  `httpx` (for `TestClient`). Anything else needs owner approval (AGENTS.md rule 5).
- Per AGENTS.md, `SPEC.md` §11 must be updated with these dependencies when phase P-A2 lands.

## 3. Repository layout

```
src/yokai/moderation/          bot side: ModerationStore (cache), is_admin / is_blacklisted
src/yokai/heartbeat.py         bot side: 30 s heartbeat writer task
src/yokai_admin/
  __main__.py                  build app, pick port, open browser at token URL
  app.py                       FastAPI factory, auth/CSRF/Host middleware, module mounting
  core/
    db.py                      sync sqlite3 access (BEGIN IMMEDIATE, busy_timeout)
    auth.py                    token, session cookie, CSRF
    audit.py                   write_audit() helper
    module.py                  AdminModule dataclass + registry validation
    templating.py              Jinja env, base layout, nav
  modules/
    __init__.py                explicit MODULES = [...] list
    dashboard/ users/ database/ access/ logs/ audit/
  static/                      htmx.min.js, css
  templates/                   base.html
tests/admin/                   panel tests; tests/moderation/ for the bot side
```

The bot remains the single schema owner. The panel never runs migrations; at startup it checks
`PRAGMA user_version >= 4` and exits with a clear message otherwise.

## 4. Module contract

Each module package exports `MODULE = AdminModule(id, title, icon, router, nav_order, tile=None)`.
`modules/__init__.py` holds an explicit `MODULES` list (no auto-discovery: no import magic, and a
broken module cannot vanish silently). Adding a feature = one package + one line.

- The core builds the sidebar from `MODULES` and mounts each `router` at `/m/<id>`.
- `tile` is an optional callable returning a small dashboard card (`title`, `value`, optional
  `status`). This is how future bot features surface status on the home page.
- A module that raises at import or while rendering its tile is shown as "errored" in the nav and
  on the dashboard; the rest of the panel keeps working.
- Duplicate `id`s fail fast at startup.

## 5. Data model (migration v4, in `yokai/storage/db.py`)

```sql
CREATE TABLE IF NOT EXISTS blacklist (
  user_id INTEGER PRIMARY KEY, reason TEXT NOT NULL DEFAULT '',
  added_ts INTEGER NOT NULL, added_by TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS admins (
  user_id INTEGER PRIMARY KEY, added_ts INTEGER NOT NULL, added_by TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS admin_audit (
  id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, actor TEXT NOT NULL, module TEXT NOT NULL,
  action TEXT NOT NULL, target TEXT, detail_json TEXT, reason TEXT);
CREATE INDEX IF NOT EXISTS idx_admin_audit_ts ON admin_audit(ts);
CREATE TABLE IF NOT EXISTS bot_heartbeat (
  id INTEGER PRIMARY KEY CHECK (id = 1), ts INTEGER NOT NULL, pid INTEGER NOT NULL,
  version TEXT NOT NULL);
```

`CURRENT_SCHEMA_VERSION` becomes 4. The owner (`OWNER_ID`) is never stored in `admins`; code
treats the owner as implicitly admin and unblacklistable.

## 6. Bot-side enforcement

- `ModerationStore` loads both lists with a ~5 s TTL cache; on a read failure it serves the last
  good copy and logs a warning. Panel changes therefore take effect within seconds, no IPC.
- `YokaiTree(CommandTree).interaction_check` (installed in `bot.py`) denies blacklisted users with
  an ephemeral error embed from `ui/embeds.py`. Persistent/ephemeral views route through their
  `interaction_check` (`ui/views.py`, `ui/game_views.py`) with the same rule.
- Existing owner-only checks (`cogs/meta.py:188`, `cogs/economy.py:161`) change from
  `user.id == owner_id` to `store.is_admin(user.id)` (owner or adminlist member).
- The owner can never be blacklisted. Admins cannot edit the lists; list management is panel-only.
- `economy/voice_tracker.py` skips blacklisted users when granting voice earnings.
- Heartbeat: a background task upserts `bot_heartbeat` (id=1) every 30 s with `ts`, `pid`,
  `version`; it starts when the bot is ready and stops on close. The panel treats the bot as
  online if `now - ts <= 90 s`.
- New user-facing text follows the persona rules; the denial message is plain and useful.

## 7. Modules

**Dashboard.** Online/offline (heartbeat), DB size, schema version, wallet count, commands in the
last 24 h, last error lines from the current log, plus module tiles.

**Users.** Search by user ID. Profile: wallet, daily streak, last daily, recent transactions,
gambling results per game, top played tracks, command counts. Edits:
- *Balance adjustment:* signed delta + required reason; one `BEGIN IMMEDIATE` transaction updates
  the wallet (floored at 0, same as `Database.admin_adjust_wallet`), inserts a
  `renown_transactions` row with reason `admin`, and an `admin_audit` row carrying the reason.
- *Reset daily cooldown:* clears `last_daily_ts`, audited.
The panel shows IDs only; it never calls Discord.

**Database.** Per-table row counts and file size; paged read-only table browser (table names
validated against `sqlite_master`); `PRAGMA integrity_check`; VACUUM; backup via the SQLite online
backup API to `BACKUP_DIR/yokai-<timestamp>.db`; list/delete backups; restore. Restore takes a
safety backup first, requires typing `RESTORE`, and refuses while the bot appears online
(heartbeat fresh or DB locked), telling the owner to stop the bot. Every action is audited.

**Access.** Blacklist and adminlist add/remove with reason; adding the owner to the blacklist or
the admins table is rejected; Discord IDs validated as positive integers.

**Logs.** Lists current and rotated files from the log directory (listing only; filenames never
taken from user input). Paged view with level filter and text search, SSE live-tail of the current
file, download. Log lines are HTML-escaped when rendered.

**Audit.** Read-only, paged `admin_audit` view filterable by module and target.

## 8. Security

- Binds to `127.0.0.1` only. On launch, a random token is generated and the browser is opened at
  `http://127.0.0.1:<port>/?token=...`; the token is exchanged for an HttpOnly,
  SameSite=Strict session cookie and then dropped from the URL.
- Every state-changing request requires a CSRF token; `Host` and `Origin` are validated against
  localhost to defeat DNS rebinding.
- Never render or log tokens, cookies or environment values. All SQL parameterized.
- Destructive actions (restore, delete backup, balance edits) require explicit confirmation and
  are audited.

## 9. Configuration

New variables in `config.py` and `.env.example`: `ADMIN_PORT` (default 8765), `BACKUP_DIR`
(default `backups/`). The panel reads the same `.env` for `DB_PATH` and the log directory.

## 10. Testing

Temp-file SQLite; no network. Coverage: migration v4 and idempotence; `ModerationStore` cache,
fallback and owner rules; tree and view gates for blacklisted users; admin-check widening;
heartbeat write and staleness; ledger-consistent balance edit and concurrent-writer safety
(second connection under `BEGIN IMMEDIATE`); backup/restore round trip and online-bot refusal;
auth, CSRF and Host/Origin rejection; module registry (duplicate ids, failing module isolated);
log listing path-safety and escaping. `pytest` and `ruff check/format --check` must be clean.

## 11. Phasing (one phase at a time, stop for review after each)

- **P-A1 Bot-side moderation:** migration v4, `ModerationStore`, `YokaiTree` gate, view gates,
  admin widening, voice-tracker skip, heartbeat. Useful without the panel.
- **P-A2 Panel core + Dashboard:** app factory, auth/CSRF, module contract, layout, `.env.example`,
  `SPEC.md` §11 dependency update.
- **P-A3 Access + Audit modules.**
- **P-A4 Users module.**
- **P-A5 Database module.**
- **P-A6 Logs module.**

Each phase gets acceptance criteria in `TASKS.md` when the implementation plan is written.

## 12. Later (not in v1)

Command/music analytics charts, read-only config viewer, SQL console, Discord username cache,
live control channel, `/blacklist` slash commands.
