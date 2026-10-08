# Admin Panel P-A6: Logs Module Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (native) or superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking. Tests first, then code, one commit per task.

**Goal:** A Logs page where the owner can see every log file the bot and its supervisor wrote, read any of them newest-first with a level filter and text search, watch the current log update live, and download a file.

**Architecture:** Panel-only, read-only phase: no POST routes, no writes, no bot changes, no database access. A `logs` module with three small services: `files.py` (the only way a request can name a file is through a listing of `LOG_DIR`), `entries.py` (parse a file into entries, filter, page), and `tail.py` (initial tail and incremental reads for live updates). The live tail is HTMX polling every 2 s that fetches only the bytes added since the last poll (decided with the owner over the spec's SSE; see Deltas).

**Tech Stack:** Python 3.12+, FastAPI/Starlette, Jinja2, HTMX 2.0.11 (vendored; `hx-swap-oob="afterbegin"`), stdlib only. No new dependencies, no new JavaScript.

**Spec:** `docs/superpowers/specs/2026-10-05-admin-panel-design.md` (§7 Logs, §8 security, §10 testing, §11 phase P-A6). P-A1 to P-A5 are merged.

## Owner decisions this plan implements

1. **Live tail by HTMX polling every 2 s**, not SSE (spec delta below).
2. **Files:** every log file in `LOG_DIR`: `yokai.log`, its rotated copies (`yokai.log.1` to `.5`) and `supervisor.log`.
3. **Viewer:** newest first, 100 entries per page, minimum-level filter, case-insensitive text search, Newer/Older paging; a traceback stays attached to its entry.

## Deltas from the spec (flag to owner at review)

- **Polling instead of SSE.** A stream holds a server thread per viewer, delays Ctrl+C shutdown, and cannot be tested without hanging the test client. Polling is the Dashboard's pattern, survives log rotation (the poller notices a replaced or shortened file and restarts from the top with a "rotated" marker) and has a Pause/Resume button. Cost: up to 2 s of delay and a poll request every 2 s while the live page is open.
- **Live tail shows the current `yokai.log` only**, newest on top, with no level filter or search (use the viewer for that). A page left open grows in memory until reloaded; this is noted on the page.
- **Download is raw.** The bot's own `RedactingFilter` has already removed secrets from `yokai.log`; the panel does not re-scan files. Nothing outside `LOG_DIR` can be read.

## Global Constraints

- AGENTS.md rules: no new dependency, no bare `except`, `logging` not `print`, ruff line length 100, strict CSP (no inline script/style), `from __future__ import annotations`, type hints.
- **File names never come from request text.** A request names a file; the code looks the name up in a fresh listing of `LOG_DIR` (regex-matched, regular, non-symlink files) and uses the path from the listing. Same rule as backups.
- **Bounded reads.** A viewer request reads at most `MAX_READ_BYTES` (8 MB) from the end of a file; a poll reads at most 256 KB; one entry is shortened to 4,000 characters; the search text is at most 100 characters.
- **Escaping.** Log text is always rendered through Jinja autoescape, never `|safe`; control and bidirectional characters are dropped (tab and newline kept); decoding uses `utf-8-sig` with `errors="replace"`.
- Handlers are synchronous (FastAPI runs them in a threadpool); no route writes anything.

## Review Focus

1. **Path safety:** names such as `..\yokai.log`, `../x.log`, `C:\x.log`, `yokai.log:stream`, `CON`, `yokai.log.`, `yokai.log.99999999`, NULs, empty, 5,000 characters, URL-encoded forms, a directory named `x.log`, and a symlink named `evil.log` are all refused by view, live and download; only listed files are ever opened.
2. **Hostile content:** log lines containing `<script>`, `<img onerror>`, ANSI escapes, NULs, bidi overrides, invalid UTF-8, a BOM, a 5 MB single line and CRLF are escaped, cleaned or truncated; no page exceeds a sane size.
3. **Parsing correctness:** both log formats (`2026-09-24 19:48:58 [ERROR] name: msg` and the supervisor's `[2026-09-30 23:38:19] [INFO] msg`) group correctly; tracebacks stay with their entry; lines before the first entry become one level-less entry; unknown levels (`SUCCESS`) rank as INFO; the level filter is a minimum.
4. **Live tail:** only complete lines are emitted; a partial last line is held until finished; rotation (new file, shorter file) restarts cleanly with a marker; no duplicate or lost lines across polls; a vanished file or missing folder answers with a message, not a 500; a tampered `pos`/`ino` (negative, huge, non-numeric, beyond the file) is handled.
5. **Read-only:** no route changes state; every route works with GET only; missing session is refused; POST to any route is refused (405/403).
6. **Failure modes:** missing or unreadable `LOG_DIR`, a file deleted between listing and open, permission errors, a locked file, an enormous number of files (listing capped at 200), page numbers 0, negative, 10^6, and 5,000 digits.

## File structure

| File | Responsibility |
|---|---|
| `src/yokai_admin/modules/logs/files.py` (create) | `LogFile`, `list_log_files`, `find_log_file`, name rules |
| `src/yokai_admin/modules/logs/entries.py` (create) | `LogEntry`, `parse_entries`, `filter_entries`, `read_page`, level ranks |
| `src/yokai_admin/modules/logs/tail.py` (create) | `initial_tail`, `read_new`, rotation detection |
| `src/yokai_admin/modules/logs/router.py`, `__init__.py`, `templates/logs/*` (create) | routes, templates, tile, `MODULE` (nav order 7) |
| `src/yokai_admin/modules/__init__.py` (modify) | register the module |
| `src/yokai_admin/static/panel.css` (modify) | `.log-entry`, level colors, `.tail` |
| `tests/test_admin_logs_files.py`, `test_admin_logs_entries.py`, `test_admin_logs_tail.py`, `test_admin_logs.py` (create), `test_admin_static.py` (extend) | tests |

---

### Task 1: Log file listing and safe lookup

**Files:** `modules/logs/__init__.py` (docstring), `modules/logs/files.py`, `tests/test_admin_logs_files.py`

```python
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}\\.log(?:\\.\\d{1,3})?$")
MAX_FILES = 200

@dataclass(frozen=True, slots=True)
class LogFile: name: str; path: Path; size: int; modified_ts: int; current: bool   # current = no numeric suffix

def list_log_files(log_dir: Path) -> list[LogFile]   # missing dir -> []; regular non-symlink files only; current files first (by name), then rotated ascending
def find_log_file(log_dir: Path, name: str) -> LogFile   # InputRefused unless listed
```

- [ ] **Step 1: Failing tests:** listing order (`supervisor.log`, `yokai.log`, `yokai.log.1`, `yokai.log.2`, ... `.10` after `.9`), ignored entries (`notes.txt`, `yokai.log.bak`, `yokai.log~`, a directory `x.log`, a symlink where the platform allows it, `yokai.log:stream`-like names that exist), a missing folder is `[]`, a folder that is a file raises `PanelDbError`-style message via a typed `PanelLogError`? (reuse `PanelDbError` text "log folder could not be read"), cap at `MAX_FILES`; `find_log_file` refuses the whole hostile name list from Review Focus 1 and returns the right file for each listed name.
- [ ] **Step 2: Implement; Step 3:** run, lint, commit `feat(admin): add the log file listing with safe lookup`.

### Task 2: Entries, filtering and paging

**Files:** `modules/logs/entries.py`, `tests/test_admin_logs_entries.py`

```python
PAGE_ENTRIES = 100; MAX_READ_BYTES = 8 * 1024 * 1024; MAX_ENTRY_CHARS = 4000; MAX_QUERY = 100
LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")   # filter choices (minimum level)

@dataclass(frozen=True, slots=True)
class LogEntry: text: str; level: Optional[str]; when: Optional[str]   # level upper-case as written
@dataclass(frozen=True, slots=True)
class LogPage: entries: list[LogEntry]; total: int; page: int; truncated_file: bool; level: str; query: str

def clean_text(text: str) -> str
def parse_entries(text: str) -> list[LogEntry]
def read_page(path: Path, *, level: str, query: str, page: int) -> LogPage   # newest first
```

- [ ] **Step 1: Failing tests:** both formats; traceback attached; level-less leading lines; `SUCCESS`/unknown rank as INFO; minimum level (`ERROR` shows ERROR and CRITICAL only; `ALL`/empty shows everything including level-less); search is case-insensitive, literal (`%`, `.*`, `[` have no special meaning), matches inside tracebacks; page/total math and newest-first order; page beyond the end is empty; level not in `LEVELS` falls back to all; query longer than 100 is cut; `clean_text` drops NUL/ANSI/bidi, keeps tab/newline, normalizes CRLF; BOM stripped; invalid UTF-8 replaced; a file bigger than `MAX_READ_BYTES` reports `truncated_file=True` and starts at a whole entry; a 5 MB single line is cut to 4,000 characters; empty file and a vanished file (`FileNotFoundError` -> `PanelDbError`-style message).
- [ ] **Step 2: Implement; Step 3:** run, lint, commit `feat(admin): add log parsing, filtering and paging`.

### Task 3: Live tail reads

**Files:** `modules/logs/tail.py`, `tests/test_admin_logs_tail.py`

```python
INITIAL_LINES = 100; MAX_POLL_BYTES = 256 * 1024

@dataclass(frozen=True, slots=True)
class TailChunk: lines: list[str]; pos: int; ino: int; rotated: bool

def initial_tail(path: Path) -> TailChunk                       # last INITIAL_LINES complete lines, pos at end of the last complete line
def read_new(path: Path, pos: int, ino: int) -> TailChunk       # complete lines added since pos
```

- [ ] **Step 1: Failing tests:** initial tail of a short, long and empty file; `read_new` returns only appended complete lines and advances `pos`; a partial last line is held back and delivered once finished (no duplicates, nothing lost over many appends); more than `MAX_POLL_BYTES` appended is delivered over several polls; rotation by rename+new file (different `ino`) and by truncation (size < pos) returns `rotated=True` and restarts at 0 (lines of the new file included); `pos` beyond the file, negative or huge is treated as rotation/restart; a vanished file raises the message error; CRLF; lines cleaned and truncated like entries.
- [ ] **Step 2: Implement; Step 3:** run, lint, commit `feat(admin): add incremental log reads for the live tail`.

### Task 4: Logs module UI

**Files:** `modules/logs/router.py`, `__init__.py`, templates `page.html`, `view.html`, `live.html`, `_poller.html`, `_lines.html`; `modules/__init__.py` (nav order 7); `panel.css`; tests `test_admin_logs.py`, `test_admin_static.py`.

Routes (all GET, under `/m/logs`):

| Route | Purpose |
|---|---|
| `""` | file list (name, size, modified, View / Download), Live tail link, folder shown |
| `/view?file=&level=&q=&page=` | newest-first viewer with level select, search, pager |
| `/live` | page with the last 100 lines (newest on top) and the running poller |
| `/live/poll?pos=&ino=` | new lines as an `hx-swap-oob="afterbegin"` block plus the next poller (outerHTML) |
| `/live/pause?pos=&ino=` | the paused poller (Resume button), no new read |
| `/download?file=` | raw file, `text/plain; charset=utf-8`, `Content-Disposition: attachment`, `nosniff` |

- [ ] **Step 1: Failing tests (real app):** list shows files with sizes and links; viewer shows entries newest first, level filter, search, pager, tracebacks together; hostile lines escaped (`<script>`, `<img onerror>`); unknown/hostile file names answer with a message (200) on view/live and refuse download without leaking other files; download returns exactly the file bytes with attachment headers; live page shows the tail; poll appends only new lines (write to the file between polls), handles rotation with a marker, partial line held; pause/resume; tampered `pos`/`ino`; missing log dir and empty dir messages; no POST route exists (405) and a session is required (401); dashboard tile; module registered, nav shows "Logs".
- [ ] **Step 2: Implement** router (`_listed(request, name)` helper maps failures to messages; query values validated: level in `LEVELS` else all, `q` cleaned and cut, `page` via `parse_page`, `pos`/`ino` ASCII digits <= 20 chars), templates (autoescape only), CSS (`.log-entry` mono with wrapped `pre` text, level accent colors from existing tokens, `.tail` scroll box max-height 60vh).
- [ ] **Step 3:** full suite, lint, commit `feat(admin): add the Logs module`.

### Task 5: Docs, real-browser check, verification

- [ ] Docs: spec §7 Logs text (polling, files, limits), README paragraph, `TASKS.md` P-A6 ticked with acceptance criteria.
- [ ] Real browser (headless Edge + CDP, throwaway scripts outside the repo) in dark 1280, light 1280, dark 390: list, viewer with filter and search, a traceback, live tail receiving appended lines (append from the driver), pause/resume, rotation marker, hostile line, download link, failed-request notice. Fix problems in a separate `fix(admin):` commit with a regression test.
- [ ] Full `python -m pytest -q`, `python -m ruff check .`, `python -m ruff format --check .`; docs commit.
- [ ] Whole-branch fresh review on the most capable model with the Review Focus list; one fix pass, test-first; stop for owner review. This is the last planned panel phase.

**Acceptance criteria (P-A6)**
- The Logs page lists every `*.log` and `*.log.N` file in `LOG_DIR` with size and modified time; names are only ever taken from that listing.
- The viewer reads a file newest-first in pages of 100 entries, filters by minimum level and case-insensitive literal text, keeps tracebacks with their entry, and is bounded (8 MB read, 4,000 characters per entry).
- Log text is escaped; control and bidirectional characters are removed; invalid UTF-8 and BOMs are handled.
- The live tail polls every 2 s, shows only complete new lines newest-first, survives rotation and truncation with a marker, can be paused, and answers with a message when the file or folder is missing.
- Download returns exactly the listed file as an attachment.
- Every route is read-only (GET), needs a session, and answers with a message instead of a 500 on bad input.
- `pytest`, `ruff check` and `ruff format --check` clean; real-browser check recorded.

## Self-review

- Spec coverage: list current and rotated (T1), paged view with level filter and search (T2, T4), live tail (T3, T4; polling delta), download (T4), escaping (T2, T4), listing-only filenames (T1).
- No placeholders; each task names files, tests and a commit message.
- Types: `LogFile` from T1 used in T2 to T4; `TailChunk` from T3 in T4.
- Open risks: polling adds a request every 2 s while the live page is open; the tail page grows until reloaded; `st_ino` as the rotation identity relies on NTFS file indexes being stable (they are for rename-based rotation, which `RotatingFileHandler` uses).
