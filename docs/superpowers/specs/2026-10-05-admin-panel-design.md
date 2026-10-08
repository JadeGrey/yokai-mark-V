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
voice, etc.), `/blacklist` slash commands. These go on a "later" list.

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
  __main__.py                  `python -m yokai_admin`: load settings, check schema, serve
  launch.py                    build_panel (app + single-use launch URL), run (uvicorn on loopback)
  app.py                       FastAPI factory, auth/CSRF/Host middleware, module mounting
  core/
    db.py                      sync sqlite3 access (BEGIN IMMEDIATE, busy_timeout)
    auth.py                    token, session cookie, CSRF
    audit.py                   write_audit() helper
    module.py                  AdminModule dataclass + registry validation
    templating.py              Jinja env, base layout, nav
  modules/
    __init__.py                explicit MODULE_PATHS tuple of "package.module:NAME" strings
    dashboard/ users/ database/ access/ logs/ audit/
  static/                      htmx.min.js, css
  templates/                   base.html
tests/admin/                   panel tests; tests/moderation/ for the bot side
```

The bot remains the single schema owner. The panel never runs migrations; at startup it checks
`PRAGMA user_version >= 4` and exits with a clear message otherwise.

## 4. Module contract

Each module package exports
`MODULE = AdminModule(id, title, router, nav_order, templates_dir=None, tile=None)` (no icon: the
rail is text-only). `modules/__init__.py` holds an explicit `MODULE_PATHS` tuple of
`"package.module:NAME"` strings (no auto-discovery: no import magic, and a broken module cannot
vanish silently). The loader imports each path inside an isolation boundary. Adding a feature =
one package + one line.

- The core builds the sidebar from the loaded modules and mounts each `router` at `/m/<id>`;
  each module's own `templates_dir` is added to the template search path.
- `tile` is an optional callable returning a small dashboard card (`title`, `value`, optional
  `status`). This is how future bot features surface status on the home page.
- A module that fails to import is shown as "failed" in the nav and on the dashboard; one whose
  tile raises while rendering shows an error row on the dashboard only (a tile read is transient,
  the module itself still works). The rest of the panel keeps working either way.
- Duplicate `id`s fail fast at startup.

## 5. Data model (migrations v4 and v5, in `yokai/storage/db.py`)

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
CREATE TABLE IF NOT EXISTS user_names (
  user_id INTEGER PRIMARY KEY, display_name TEXT NOT NULL, updated_ts INTEGER NOT NULL);
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

**Users (shipped as the People module).** List and search by name or user ID. Profile: wallet, daily streak, last daily, recent transactions,
gambling results per game, top played tracks, command counts. Edits:
- *Balance adjustment:* signed delta (capped at 100,000) + required reason, with a preview and a
  confirm step. One `BEGIN IMMEDIATE` transaction applies a relative update (floored at 0),
  inserts a `renown_transactions` row for the amount actually applied, and writes an
  `admin_audit` row. A confirm whose preview is stale (balance changed) is refused.
- *Reset daily cooldown:* back-dates `last_daily_ts` to 24 h ago so the streak survives
  (NULL would reset it); refused if there is no wallet, no prior claim, or it is already
  claimable. Audited.
The panel shows IDs only; it never calls Discord.

*Cross-process write rule (from the P-A1 review, binding on P-A4/P-A5):* the bot's `_write_lock`
only serializes coroutines inside the bot process, and several bot paths (`pay`, `claim_daily`)
compute `balance_after` from a balance they read earlier. Panel writes therefore use
`BEGIN IMMEDIATE`, relative updates (`balance = balance + ?`), and re-read the balance inside the
same transaction before writing the ledger row. The P-A4 plan must also review whether the bot's
read-then-write paths need the same treatment.

**Database.** Per-table row counts and file size; paged read-only table browser (table names
validated against `sqlite_master`); `PRAGMA integrity_check`; VACUUM; backup via the SQLite online
backup API to `BACKUP_DIR/yokai-<timestamp>.db` (default: a `backups` folder beside the database;
each backup is verified and never overwrites another); list/delete backups, named only through a
listing of the folder (never from request text); restore. Restore requires typing `RESTORE`,
refuses while the bot appears online (fresh heartbeat) or the file is in use (a held write lock, or
the operating system refusing to replace an open file, which is the only guard for a bot that is
running but disconnected from Discord), checks the chosen backup (integrity, schema not older than
the panel supports nor newer than the live file), takes a `-pre-restore` safety backup, removes
stale `-wal`/`-shm` files, replaces the file and writes its audit row (and clears the stale
heartbeat) in the restored database. Backup, delete, VACUUM and restore are audited; read-only
actions (browsing, integrity check) are not.

**Access.** Blacklist and adminlist add/remove with reason; adding the owner to the blacklist or
the admins table is rejected. IDs must be plain ASCII digits (non-ASCII digits that `int()` would
accept are refused) between 1 and 2^63 - 1. A blacklist add needs a reason of 3 to 200 characters;
an admin add takes an optional note, kept in the audit log only (the `admins` table has no note
column). Every removal asks for confirmation. A blacklisted person cannot be made an admin;
blacklisting an admin keeps the admin row (the blacklist already wins in the bot) and the page
flags both lists. Without `OWNER_ID` the panel still starts, shows no forms, and refuses edits.
Every change and its audit entry share one `BEGIN IMMEDIATE` transaction. People are shown by
display name: the bot remembers each user's latest name in `user_names` (written at most once a
day per person; control and bidirectional-override characters are stripped), and the panel only
reads it.

**Logs.** Lists every `*.log` and `*.log.N` file in the log directory (the bot's `yokai.log`, its
rotated copies, and the supervisor's `supervisor.log`); a request names a file only through that
listing, never as a path. Viewer: newest first, 100 entries per page, minimum-level filter and
case-insensitive literal text search, tracebacks kept with their entry, reads bounded (last 8 MB of
a file, 4,000 characters per entry). Live tail of the current `yokai.log`: HTMX polling every 2 s
(chosen over SSE: no held server thread, clean Ctrl+C shutdown, testable, survives rotation) that
reads only the bytes added since the last poll, holds back a half-written line, restarts with a
marker after rotation or truncation, and can be paused. Download returns the listed file as an
attachment. All routes are GET and read-only. Log text is HTML-escaped when rendered; control and
bidirectional characters are removed.

**Audit.** Read-only, paged `admin_audit` view filterable by module and target.

## 8. Security

- Binds to `127.0.0.1` only. On launch, a random token is generated and the browser is opened at
  `http://127.0.0.1:<port>/auth?token=...`; the token works exactly once and is exchanged for an
  HttpOnly, SameSite=Strict session cookie (12 hours), then the browser is redirected to `/`. The
  launch URL is printed to the console only, never to a log file, and only after the port is
  bound, so a taken port can never send the browser to a link whose server did not start.
- A live page whose session ended is reloaded into the locked screen (`HX-Refresh`), and a page
  whose polling fails is marked stale, so an old "Online" never looks current.
- Every response carries a strict Content-Security-Policy (`default-src 'none'`, scripts, styles
  and fonts from `'self'` only) and `Cache-Control: no-store` for pages; all assets are vendored.
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

Command/music analytics charts, read-only config viewer, SQL console,
live control channel, `/blacklist` slash commands.
