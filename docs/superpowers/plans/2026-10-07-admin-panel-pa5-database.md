# Admin Panel P-A5: Database Module Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (native) or superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking. Tests first, then code, one commit per task.

**Goal:** A Database page where the owner can see what is in the bot's database, browse any table read-only, check integrity, compact the file, take and manage backups, and restore one safely, with every change in the audit log.

**Architecture:** Panel-only phase (no bot changes). A `database` module with a read side (`service.py`: table list with counts, paged table browser) and a maintenance side (`maintenance.py`: integrity check, VACUUM, backup, list/delete backups, guarded restore). Backups use SQLite's online backup API, so they are consistent while the bot runs. Restore is the only operation that replaces the live file, so it is built defensively (see "Restore design"). HTMX swaps one results region; refusals come back as HTTP 200 fragments, as in Access and People.

**Tech Stack:** Python 3.12+, FastAPI/Starlette, Jinja2, HTMX 2.0.11 (vendored), stdlib `sqlite3`/`shutil`/`os`, pytest, ruff. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-05-admin-panel-design.md` (§7 Database, §8 security, §10 testing, §11 phase P-A5). P-A1 to P-A4 are merged (schema v5).

## Owner decisions this plan implements

1. **Scope:** everything the spec lists, including restore.
2. **Browser:** every table and column, values HTML-escaped and truncated in the cell. No tables hidden.
3. **Restore guard:** refuse if the heartbeat is fresh **or** the database is locked/in use (details below).
4. **Backups:** new `BACKUP_DIR` setting, default `backups/` beside the database file. Nothing is deleted automatically; the owner deletes from the page.

## Deltas from the spec (flag to owner at review)

- **Audit scope.** The spec says "every action is audited". Integrity check and browsing are read-only and write nothing, so they are **not** audited; backup, delete backup, VACUUM and restore are. (Auditing reads would only add noise.)
- **Restore guard.** The heartbeat is only refreshed while the bot is connected to Discord, so a bot that is running but disconnected looks offline. The guard therefore also relies on the operating system: on Windows an open SQLite file cannot be replaced, so the replace step fails cleanly and nothing changes. Both guards are tested.
- **Audit history after restore.** The restored file carries the audit log of the moment it was backed up. The restore's own audit row is written into the restored database; the pre-restore safety backup keeps the previous history.

## Global Constraints

- AGENTS.md rules: no new dependency, no bare `except`, `logging` not `print`, every external failure mapped to `PanelDbError`/`InputRefused`, ruff line length 100, CSP-strict templates (no inline script/style), `from __future__ import annotations`, type hints.
- Backup filenames are **never** built from request text. A request names a backup; the code looks that name up in a fresh listing of `BACKUP_DIR` (regex-matched names only) and uses the path from the listing.
- Table names from the request are only used if they are in the set read from `sqlite_master`; identifiers are quoted with `"` doubled; values are always parameters.
- Maintenance actions (backup, vacuum, delete, restore) share one process-wide non-blocking lock; a second one answers "another maintenance action is running".
- Writes to the bot's database use the existing `write_transaction`; VACUUM (cannot run in a transaction) and the restore replace are the only exceptions and are written out explicitly.

## Restore design (the risky part)

Order of steps in `restore_backup(settings, name, typed, now)`; any failure before step 7 leaves the live database unchanged:

1. Validate `typed == "RESTORE"` (exact, case-sensitive) and the maintenance lock.
2. Look up `name` in the backup listing; refuse if absent.
3. Refuse if the heartbeat is fresh (`ONLINE_WINDOW_S`) or cannot be read.
4. Check the chosen backup: opens read-only, `PRAGMA integrity_check` is `ok`, `user_version` is at least `MIN_SCHEMA_VERSION` and **not newer than the live database's** (a newer file could break an older bot).
5. Take the safety backup of the live database (`yokai-<ts>-pre-restore.db`, backup API, verified).
6. Copy the chosen backup to `<db>.restoring` beside the live file with the backup API (a clean file with no WAL), verify it.
7. Checkpoint the live WAL (`wal_checkpoint(TRUNCATE)`), close, delete leftover `-wal`/`-shm` files (a stale WAL applied to a different database would corrupt it), then `os.replace(<db>.restoring, <db>)`. A `PermissionError`/`OSError` here means the file is in use: delete `.restoring`, report "close the bot first", and the safety backup is already on disk.
8. Write the restore audit row into the restored database (`write_transaction`). If that fails, say so plainly; the restore itself succeeded.

## Review Focus

Failure modes happy-path tests would miss, most likely first. Each has a named test.

1. **Restore safety:** wrong typed word, unknown backup name, path-like names (`..\x`, `C:\x`, `yokai-1.db/../x`), fresh heartbeat, locked/in-use file, corrupt backup, backup from a newer schema, safety backup failing, and an interrupted copy all leave the live database byte-identical; a good restore ends with the backup's rows, no stale `-wal`/`-shm`, a safety backup on disk and one audit row.
2. **Backups are consistent and verified:** taken while another connection writes; a backup that fails its integrity check is removed and reported; names never collide or overwrite (a second backup in the same second gets a suffix).
3. **Path/identifier injection:** table names not in `sqlite_master` (`wallets; DROP TABLE x`, `sqlite_master` variants, quotes, empty, huge) answer with a message; backup names are matched by regex and by listing only.
4. **Hostile data:** cell values (`<script>`, bytes, huge text, NULL), table names and backup file names are escaped/truncated.
5. **VACUUM and integrity:** VACUUM refused when the file is missing/too old; reports size before and after; a held write lock gives a "busy" message, not a 500. Integrity problems are listed (capped) and not hidden.
6. **Unknown or broken input:** missing database, old schema, missing backup dir (created on first backup, listed as empty otherwise), non-numeric/huge `page`; write endpoints locked without session and CSRF; GET never changes anything; the generic request-failed notice still fires.
7. **Atomicity of audit:** a backup/delete/vacuum audit row exists exactly when the action happened.

## File structure

| File | Responsibility |
|---|---|
| `src/yokai_admin/core/formatting.py` (modify) | `format_when` survives out-of-range timestamps; add `format_bytes` |
| `src/yokai_admin/core/templating.py` (modify) | register `bytes` filter |
| `src/yokai_admin/core/settings.py` (modify) | `backup_dir`; `BACKUP_DIR` env |
| `.env.example`, `README.md` (modify) | document `BACKUP_DIR` |
| `src/yokai_admin/modules/database/service.py` (create) | `list_tables`, `read_table`, cell formatting, `integrity_check` |
| `src/yokai_admin/modules/database/backups.py` (create) | listing, `create_backup`, `delete_backup`, name rules |
| `src/yokai_admin/modules/database/maintenance.py` (create) | lock, `vacuum`, `restore_backup` |
| `src/yokai_admin/modules/database/router.py`, `__init__.py`, `templates/database/*` (create) | routes, templates, tile, `MODULE` |
| `src/yokai_admin/modules/audit/service.py`, `modules/__init__.py` (modify) | labels, register module (nav order 6) |
| `src/yokai_admin/static/panel.css` (modify) | table browser, backup list styles |
| `tests/test_admin_formatting.py` (extend), `test_admin_database_service.py`, `test_admin_database_backups.py`, `test_admin_database_maintenance.py`, `test_admin_database.py` (create), `test_admin_audit.py`, `test_admin_static.py` (extend) | tests |
| `tests/admin_helpers.py` (modify) | `seed_database` can enable WAL; `settings_for` builds settings with a backup dir |

---

### Task 1: Settings, formatting hardening, helpers

**Files:** `core/formatting.py`, `core/templating.py`, `core/settings.py`, `.env.example`, `README.md`, `tests/admin_helpers.py`, `tests/test_admin_formatting.py`, `tests/test_admin_settings.py`

- [ ] **Step 1: Failing tests.**
  - `format_when` returns an en dash (never raises) for `10**18`, `-10**18`, and a bool-like junk value; unchanged for normal values.
  - `format_bytes(0) == "0 B"`, `1536 -> "1.5 KB"`, `5 * 1024**2 -> "5.0 MB"`, `None -> "–"`, negative -> `"0 B"`.
  - `load_settings` with no `BACKUP_DIR` gives `db_path.parent / "backups"`; with `BACKUP_DIR=x` gives `Path("x")`; empty string falls back to the default.
  - `PanelSettings(db_path=..., log_dir=...)` (existing call shape used across tests) still works, `backup_dir` defaults to `None` and a property `effective_backup_dir` returns the beside-the-database default.
- [ ] **Step 2: Implement.** `format_when`: wrap `datetime.fromtimestamp` in `try/except (OverflowError, OSError, ValueError)` returning the en dash. `format_bytes` with 1024 steps up to GB. `PanelSettings.backup_dir: Optional[Path] = None` and `effective_backup_dir` property (`backup_dir or db_path.parent / "backups"`). Document `BACKUP_DIR` in `.env.example` and the README panel paragraph.
- [ ] **Step 3: Run** `python -m pytest tests/test_admin_formatting.py tests/test_admin_settings.py -q`, then lint, commit `feat(admin): add BACKUP_DIR, byte formatting, harden timestamps`.

### Task 2: Read side (tables, browser, integrity)

**Files:** `modules/database/service.py` (+ package `__init__.py` docstring), `tests/test_admin_database_service.py`

Key interfaces:

```python
PAGE_ROWS = 50
MAX_CELL_CHARS = 120

@dataclass(frozen=True, slots=True)
class TableInfo: name: str; rows: int
@dataclass(frozen=True, slots=True)
class TablePage: name: str; columns: list[str]; rows: list[list[str]]; total: int

def list_tables(conn) -> list[TableInfo]          # from sqlite_master, no sqlite_% tables, sorted
def read_table(conn, name: str, page: int) -> TablePage   # raises InputRefused for unknown names
def format_cell(value: object) -> str             # None -> "NULL", bytes -> "<N bytes>", long text truncated with an ellipsis
def integrity_check(conn, limit: int = 20) -> list[str]   # ["ok"] or up to `limit` problems
```

- [ ] **Step 1: Failing tests** (real seeded database): table list and counts match; `sqlite_%` tables hidden; `read_table` pages 50 rows with correct totals and column names; page beyond the end returns empty rows; **unknown names refused**: `wallets; DROP TABLE wallets`, `"wallets"`, `wallets--`, `sqlite_master`, `sqlite_sequence`, `WALLETS ` (case/space variants), empty string, 5,000 characters; a table whose name contains a double quote (created in the test) is browsable and the quoting holds; `format_cell` for `None`, `b"\x00\x01"`, 500-character text, `<script>`, an int and a float; `integrity_check` returns `["ok"]` on a good database and reports problems on a deliberately corrupted copy (flip bytes in a page of a copy), capped at `limit`.
- [ ] **Step 2: Implement.** `read_table` validates `name` against `{t.name for t in list_tables(conn)}` (exact match), builds `SELECT * FROM "<name with " doubled>" LIMIT ? OFFSET ?`, columns from `cursor.description`. Counts use the quoted name too.
- [ ] **Step 3: Run, lint, commit** `feat(admin): add the Database read side with a validated table browser`.

### Task 3: Backups

**Files:** `modules/database/backups.py`, `tests/test_admin_database_backups.py`

```python
NAME_RE = re.compile(r"^yokai-\d{8}-\d{6}(?:-pre-restore)?(?:-\d+)?\.db$")

@dataclass(frozen=True, slots=True)
class BackupFile: name: str; path: Path; size: int; modified_ts: int; safety: bool

def list_backups(backup_dir: Path) -> list[BackupFile]   # newest first; missing dir -> []; ignores non-matching names, directories, symlinks
def find_backup(backup_dir: Path, name: str) -> BackupFile  # lookup by listing only; InputRefused otherwise
def create_backup(db_path: Path, backup_dir: Path, *, now: int, label: str = "") -> BackupFile
def delete_backup(db_path: Path, backup_dir: Path, name: str, *, now=None) -> BackupFile
```

- [ ] **Step 1: Failing tests.**
  - `create_backup` produces `yokai-YYYYMMDD-HHMMSS.db` from `now`, which opens read-only, passes `integrity_check`, has the same row counts, and `user_version` matches; creates `backup_dir` if missing; a second backup in the same second becomes `...-1.db` and **never overwrites**; no `.tmp` file left behind; consistent while a second connection keeps committing inserts (rows are a prefix of the live rows, integrity ok).
  - A backup that fails verification (monkeypatch `integrity_check` to report a problem) is deleted and a `PanelDbError` says so.
  - `create_backup` refuses a missing/old-schema live database with a clear message; a backup dir that is a file gives a clear message.
  - Audit: `create_backup` and `delete_backup` write one `admin_audit` row each (module `database`, actions `backup.create` / `backup.delete`, target = file name, detail has size) inside a `write_transaction`; if the audit write fails after the file was made, the message says the backup exists but the audit entry was not saved.
  - `find_backup`/`delete_backup` refuse: unknown names, `..\\x.db`, `../x.db`, `C:\\x.db`, absolute paths, `yokai-20200101-000000.db/../../x`, names with NULs, empty, 5,000 characters, a name that matches a directory; a non-matching file in the folder (`notes.txt`, `yokai.db`) is neither listed nor deletable; deleting removes exactly that file.
  - `list_backups` marks `-pre-restore` files as `safety=True` and sorts newest first.
- [ ] **Step 2: Implement.** Backup into `<final>.tmp` via `src.backup(dst)` from a read-only source connection, verify with `integrity_check` on the temp file, `os.replace` into the final name chosen with an exclusive existence check (loop suffix). Delete uses `find_backup` then `Path.unlink`.
- [ ] **Step 3: Run, lint, commit** `feat(admin): add verified online backups and a safe backup listing`.

### Task 4: VACUUM and restore

**Files:** `modules/database/maintenance.py`, `tests/test_admin_database_maintenance.py`

```python
class Busy(PanelDbError): ...
def maintenance_lock() -> ContextManager[None]       # non-blocking; raises PanelDbError("Another maintenance action is running...")
@dataclass(frozen=True, slots=True)
class VacuumResult: before: int; after: int
def vacuum(db_path: Path, *, now=None) -> VacuumResult
@dataclass(frozen=True, slots=True)
class RestoreResult: restored: str; safety: BackupFile; audit_saved: bool
def restore_backup(settings: PanelSettings, name: str, typed: str, *, now: int) -> RestoreResult
```

- [ ] **Step 1: Failing tests** for `vacuum`: sizes reported (delete many rows first so `after < before`); audit row written after success with both sizes; missing/old database refused; a held `BEGIN IMMEDIATE` on another connection yields a "busy" `PanelDbError` and no audit row; the lock refuses a second concurrent maintenance action.
- [ ] **Step 2: Failing tests** for `restore_backup` (every case asserts the live file's bytes unchanged unless stated):
  1. typed `restore`, `Restore`, `RESTORE `, empty: refused.
  2. unknown / path-like names: refused.
  3. fresh heartbeat in the live database: refused with "stop the bot first"; a stale heartbeat (older than the window) and a missing row are allowed.
  4. live file held open by another connection with a pending write: refused (PermissionError path on Windows; on other platforms the BEGIN IMMEDIATE probe) and **no `.restoring` left**.
  5. corrupt backup (bytes flipped) refused; backup `user_version` newer than live refused; backup older than `MIN_SCHEMA_VERSION` refused; a file that is not SQLite refused.
  6. safety backup failure (monkeypatch `create_backup` to raise) refuses before anything is replaced.
  7. interrupted copy (monkeypatch the verify step on `.restoring` to raise) leaves the live file identical and removes `.restoring`.
  8. **Good restore:** live db has extra rows after the backup was taken and a stale `-wal` present; afterwards the live db equals the backup's content (row counts and a sample), integrity ok, no `-wal`/`-shm` leftovers from the old file, `<db>.restoring` gone, a `-pre-restore` safety backup exists that contains the extra rows, and exactly one `database/restore` audit row exists in the restored file naming both files.
  9. Audit write failure after a successful replace returns `audit_saved=False` (does not raise).
  10. Restoring while a backup file listing includes the `-pre-restore` file works (safety backups are restorable).
- [ ] **Step 3: Implement** per "Restore design". `vacuum`: open read-write autocommit, check schema, record size, `VACUUM;`, then audit via `write_transaction`.
- [ ] **Step 4: Mutation checks** (record in the ledger): removing the typed-word check, the heartbeat check, the schema-newer check, the WAL deletion, the safety backup, or the `.restoring` cleanup each fails at least one test.
- [ ] **Step 5: Run, lint, commit** `feat(admin): add VACUUM and a guarded restore`.

### Task 5: Module UI

**Files:** `modules/database/router.py`, `__init__.py`, templates `page.html`, `_tables.html`, `_table.html`, `_integrity.html`, `_backups.html`, `_confirm.html`; `audit/service.py` labels (`backup.create` "Created a backup", `backup.delete` "Deleted a backup", `vacuum` "Compacted the database", `restore` "Restored a backup"); `modules/__init__.py` (nav order 6, after People); `panel.css`; tests `test_admin_database.py`, `test_admin_audit.py`, `test_admin_static.py`.

Routes (all under `/m/database`):

| Route | Purpose |
|---|---|
| `GET ""` | overview: file size, schema, tables with counts (links), backups list, action buttons |
| `GET /table/{name}?page=` | paged read-only browser (unknown name: message, 200) |
| `POST /integrity` | runs the check, returns a results fragment |
| `GET /vacuum/confirm`, `POST /vacuum` | confirm then compact |
| `POST /backup` | create a backup, returns the refreshed backups region |
| `GET /backups/{name}/delete/confirm`, `POST /backups/{name}/delete` | confirm, delete |
| `GET /backups/{name}/restore/confirm`, `POST /backups/{name}/restore` (field `confirm`) | typed-RESTORE form, restore |

- [ ] **Step 1: Failing tests** (real app, as in `test_admin_users.py`): overview lists tables and counts and sizes; browser pages, escapes hostile cells and table names, refuses injection names with a message; integrity shows ok and a failure (corrupted copy); backup button creates a file and shows it; delete asks first and removes exactly that file; vacuum asks first and shows before/after; restore form requires typing `RESTORE` and shows the stop-the-bot refusal with a fresh heartbeat; every POST needs session and CSRF (401/403) and GETs change nothing (file listing and audit count unchanged); busy database answers with a message (200); missing database answers "not found"; audit page shows the four labels; Dashboard tile reads "<size>, last backup <age>" and "no backups yet"; hostile backup file names (create `yokai-20200101-000000.db` only; others ignored).
- [ ] **Step 2: Implement** router (module-level helper `_act` like People's, mapping `InputRefused`/`PanelDbError`/`sqlite3.Error`/`OSError` to messages; only parsed values reach templates and URLs), templates with region ids (`#db-results`, `#backups`) kept inside each fragment, CSS for `.table-scroll` (horizontal scroll inside the wrapper, no page scroll), `.mono`, `.backup-row`.
- [ ] **Step 3: Run the module tests plus the full suite, lint, commit** `feat(admin): add the Database module`.

### Task 6: Docs, real-browser check, verification

- [ ] Docs: spec §7 Database text (audit scope, restore guard, `BACKUP_DIR`), README paragraph, `TASKS.md` P-A5 ticked with acceptance criteria below.
- [ ] Real browser (headless Edge + CDP, throwaway scripts outside the repo) in dark 1280, light 1280, dark 390: overview, table browser (wide table), integrity, backup, delete confirm, vacuum, restore refusal with a fresh heartbeat, restore success on a throwaway database, request-failed notice. Fix problems in a separate `fix(admin):` commit with a regression test.
- [ ] Full `python -m pytest -q`, `python -m ruff check .`, `python -m ruff format --check .`. Docs commit.
- [ ] Whole-branch fresh review on the most capable model with the Review Focus list; one fix pass, test-first; stop for owner review.

**Acceptance criteria (P-A5)**
- Overview shows file size, schema, every table with its row count; the browser pages any table read-only; names are validated against `sqlite_master`; hostile cell values are escaped and long ones truncated.
- Integrity check reports `ok` or up to 20 problems.
- VACUUM asks first, reports before/after sizes, and is audited; a busy database gives a message.
- Backups use the online backup API, are verified, never overwrite, are listed newest first, and can be deleted after a confirm; names come only from the listing.
- Restore needs typing `RESTORE`, refuses while the bot looks online or the file is in use, verifies the chosen backup and its schema, takes a safety backup first, removes stale WAL files, replaces the file, and audits into the restored database; any refusal leaves the live file untouched.
- Write endpoints require session and CSRF; GETs change nothing; failures answer with a message, never a 500.
- `pytest`, `ruff check`, `ruff format --check` clean; real-browser check recorded.

## Self-review

- Spec coverage: table counts and size (T2/T5), browser with `sqlite_master` validation (T2), integrity (T2), VACUUM (T4), backup API to `BACKUP_DIR/yokai-<timestamp>.db` (T3), list/delete (T3), restore with safety backup, typed `RESTORE`, heartbeat/lock refusal (T4), audit (T3-T4), `format_when` hardening carried from P-A3 (T1).
- No placeholders; every task names files, tests and commit message.
- Types: `BackupFile` shared by T3-T5; `PanelSettings.effective_backup_dir` used by T3-T5.
- Open risk: the Windows in-use detection is what protects against a running-but-disconnected bot; tested with a held connection, but not against the real bot process (manual check listed in Task 6).
