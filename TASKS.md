# Yokai — Tasks

Read `AGENTS.md` (rules) and `SPEC.md` (requirements) first. Work **one phase at a time**. Each phase starts with a short plan and ends with a **STOP** for owner review. Tick boxes as you go. Task IDs (`P2.3`) are for reference in commits and reviews.

## Suggested kickoff prompt (owner pastes this into Antigravity)

> Read `AGENTS.md`, `SPEC.md`, and `TASKS.md` in full. Do not write code yet. Produce an implementation plan for Phase 0 and Phase 1 only: files to create, tests to write, external docs you need to check, and any assumptions or conflicts you found in the spec. Then wait for my approval before implementing.

For each later phase: *"Plan and implement Phase N of `TASKS.md`, following `AGENTS.md`. Stop at the phase checkpoint and summarize."*

---

## Phase 0 — Owner prerequisites (done by the owner, not the agent)

- [x] O1. Create the Discord application and bot user; copy the token. Enable **no** privileged intents.
- [x] O2. Set the bot's name to Yokai and upload the avatar in the Developer Portal.
- [x] O3. Enable Developer Mode in Discord; copy the server ID (`GUILD_ID`) and your user ID (`OWNER_ID`).
- [x] O4. Invite the bot with scopes `bot` + `applications.commands` and permissions: View Channels, Send Messages, Embed Links, Connect, Speak.
- [x] O5. Install Python 3.12+, FFmpeg, and Deno; confirm `python --version`, `ffmpeg -version`, and `deno --version` work in PowerShell.
- [ ] O6. (Optional, for the official Spotify provider) Create a Spotify developer app. The app owner needs Spotify Premium. Copy the client ID and secret. Skip this to run scraper-only.
- [x] O7. Create `.env` from `.env.example` once Phase 1 produces it.

---

## Phase 1 — Scaffolding and foundations

**Goal:** a bot that starts, logs in, shows the right presence, and has the shared building blocks every later feature uses.

- [x] P1.1 Project skeleton: `pyproject.toml` (runtime + dev extras per `SPEC.md` §11), `src/yokai/` layout, `.gitignore` (`.env`, `data/`, `logs/`, `.venv/`), `.env.example` with every variable, short `README.md`.
- [x] P1.2 `config.py`: load and validate environment variables; clear error messages for missing required values.
- [x] P1.3 Logging: rotating file in `logs/` plus console; token and cookie redaction.
- [x] P1.4 Startup health checks: `davey` importable, `ffmpeg` and `deno` found (versions logged), yt-dlp version and release age logged. Failures produce clear log lines; missing FFmpeg or `davey` blocks voice features but not startup.
- [x] P1.5 `bot.py` and `__main__.py`: `Bot` subclass with default intents (no privileged), `allowed_mentions=none`, initial presence idle + Watching "for plant", guild-scoped command sync in `setup_hook`, cog auto-loading, graceful shutdown.
- [x] P1.6 Typed errors in `errors.py` per `SPEC.md` §7.7.
- [x] P1.7 `theme.py` and the **universal embed system** (`ui/embeds.py`, `ui/views.py`): all builders in `SPEC.md` §5, `clamp()` helpers, `send()` helper, `BaseView`, quip bank with no-repeat selection.
- [x] P1.8 SQLite layer (`storage/`): connection management, WAL, `PRAGMA user_version` migrations, tables from `SPEC.md` §10, small repository functions.
- [x] P1.9 Central command logging hook writing to `command_log`.
- [x] P1.10 `PresenceManager` state machine (idempotent), unit-tested with a fake client.
- [x] P1.11 Temporary `/ping` stub or hello command to prove the embed system and hook work end-to-end (replaced in Phase 5).

**Acceptance criteria**
- `python -m yokai` logs in; the bot shows **idle** and **Watching for plant**. *(manual)*
- The stub command replies with a themed embed and a `command_log` row appears.
- Embed tests pass, including hostile-length inputs; quip bank test passes; presence state machine tests pass.
- `pytest` and `ruff check` and `ruff format --check` are clean.

**STOP — owner review.**

---

## Phase 2 — Music core (YouTube)

**Goal:** play music from YouTube links and search queries with a working queue.

- [x] P2.1 Models: `Track`, `StreamInfo`, queue with loop modes, history, size limits.
- [x] P2.2 Input classifier and URL allowlist (`SPEC.md` §7.2), fully unit-tested (watch/youtu.be/shorts/music/playlist/RD mix/Spotify/other hosts/garbage).
- [x] P2.3 `Resolver` interface and the yt-dlp implementation: threaded, timeout, semaphore, JS runtime configuration, optional cookie file, error mapping to typed errors.
- [x] P2.4 `GuildPlayer`: state machine, FFmpeg PCM + volume, reconnect options, prefetch next stream URL, `after` callback handled thread-safely, retry-once on stream failure with a fresh URL.
- [x] P2.5 Voice handling: join/move rules, permission checks with specific messages, auto-disconnect timers, kicked/moved cleanup, presence tied to connect/disconnect.
- [x] P2.6 Commands: `/play` (links, playlists with cap, search text), `/pause`, `/resume`, `/skip`, `/stop`, `/queue` (paginated), `/nowplaying`, `/remove`, `/clear`, `/shuffle`, `/loop`, `/volume`. All output via embeds.
- [x] P2.7 Play-event logging with outcomes `completed | skipped | stopped | error` and `listened_s`.
- [x] P2.8 Failure handling: consecutive-failure counter, YouTube health flag, one escalation embed, error ring buffer (used by `/diag` later).
- [x] P2.9 Tests with fakes: queue and loop logic, player state transitions, outcome classification, error handling paths, voice-rule checks.

**Acceptance criteria**
- `/play <youtube url>` and `/play <search text>` produce audio in a voice channel, including inside a DAVE end-to-end-encrypted call. *(manual)*
- A YouTube playlist link queues up to the cap and starts playing before the whole list is resolved. *(manual)*
- Presence flips to **online** on join and **idle** on `/stop`, auto-leave, and being kicked. *(manual)*
- Skipping, looping, shuffling, and removing behave correctly; `play_events` rows show correct outcomes.
- Forced resolver failures (simulated in tests) produce the right embeds and never crash the player.
- `pytest` and `ruff` clean.

**STOP — owner review.**

---

## Phase 3 — Spotify links

**Goal:** paste a Spotify track, album, or playlist link and have it play via YouTube.

- [x] P3.1 Spotify URL/URI parser (open.spotify.com with locale prefixes, `spotify:` URIs, `spotify.link` redirect that must land on `open.spotify.com`); friendly rejection of artist/show/episode links.
- [x] P3.2 `SpotifyProvider` interface and models (`SpotifyTrackMeta`, `SpotifyCollection` with `partial`).
- [x] P3.3 Official provider (`httpx`): check the current Spotify docs first; use `/playlists/{id}/items`; detect missing items or 403; auto-disable if the app is rejected.
- [x] P3.4 Scraper provider (`spotifyscraper`, exact pin, threaded, cache 24h, cap and `partial`).
- [x] P3.5 Orchestrator with official-first fallback, circuit breaker (3 failures → 10-minute skip), provider logging, friendly failure embed.
- [x] P3.6 `matcher.py`: query building, candidate retrieval (ytmusicapi songs first, yt-dlp fallback), scoring, thresholds, penalties (`SPEC.md` §7.5); table-driven tests including live-version and wrong-duration traps.
- [x] P3.7 `spotify_match_cache` read/write.
- [x] P3.8 Lazy matching: Spotify tracks enter the queue as `PENDING_MATCH`, are matched just before playback, and the summary embed lists tracks that couldn't be matched and shows "loaded N of M" when partial.
- [x] P3.9 Tests with fake providers: fallback order, circuit breaker, partial collections, unmatched tracks, cache hits.

**Acceptance criteria**
- A Spotify track link plays the correct song. *(manual)*
- A Spotify album link queues all tracks (up to the cap) and starts quickly. *(manual)*
- A public playlist you don't own resolves through the scraper fallback (or produces the friendly failure embed if Spotify blocks it). *(manual)*
- With the official provider disabled or misconfigured, everything still works via the scraper; with both disabled, the failure message suggests a YouTube link.
- No Spotify-derived fields are written to `play_events`. `pytest` and `ruff` clean.

**STOP — owner review.**

---

## Phase 4 — `/recommend`

**Goal:** YouTube Music radio recommendations with a queue-them button.

- [x] P4.1 `Recommender` interface and `Recommendation` model.
- [x] P4.2 `YTMusicRadioRecommender`: unauthenticated `get_watch_playlist(radio=True)`, threaded, verified field names, 10-minute per-seed cache.
- [x] P4.3 Filtering per `SPEC.md` §8 (seed, queued/playing, last 100 plays, normalized duplicates, non-music types, >10 min) and count trimming to `RECOMMEND_COUNT`.
- [x] P4.4 Seed selection: explicit arg → now playing → requester's last completed track → error embed.
- [x] P4.5 `recommendations` embed and `RecommendView` with a single-use **queue-all** button: permission rules, join-if-needed, lock against double-press, embed transitions to "queued", timeout disables the button.
- [x] P4.6 Tests: filtering, seed selection order, view behavior (permission, double-press, timeout) with fakes.

**Acceptance criteria**
- `/recommend` while a song plays returns 5–10 relevant, non-duplicate tracks in one embed. *(manual)*
- Pressing the button queues them all, once; the embed updates and the button disables. *(manual)*
- ytmusicapi failure or a thin result shows an error embed and nothing crashes.
- `pytest` and `ruff` clean.

**STOP — owner review.**

---

## Phase 5 — Utility and troubleshooting commands

- [x] P5.1 `/ping`: gateway, REST round-trip, voice, resolver (rolling average of last 10), database; status color from worst latency; NaN/not-ready safe.
- [x] P5.2 `/usage`: top 10 by commands, tracks requested per user, top 5 commands, period option; mentions inside the embed.
- [x] P5.3 `/about`: fields and disclaimer per `SPEC.md` §9.
- [x] P5.4 `/diag` (owner-only, ephemeral): versions, DAVE status, FFmpeg/Deno, yt-dlp age, YouTube health, Spotify provider status, ytmusicapi version, DB size, last 20 sanitized errors, **Update yt-dlp** button (argument-list subprocess, no shell, tells the owner to restart).
- [x] P5.5 Tests: usage aggregation queries (period boundaries, ties, empty data), ping formatting, owner check on `/diag`.


**Acceptance criteria**
- All four commands render correctly in themed embeds and stay within embed limits. *(manual)*
- `/usage` reflects real command and play history. `/diag` is refused for non-owners.
- `pytest` and `ruff` clean.

**STOP — owner review.**

---

## Phase 6 — Hardening and polish

- [x] P6.1 `run.ps1` (activate venv, restart on crash with delay); README covers setup, `.env`, running, updating yt-dlp, and the note that the bot is offline while the PC sleeps.
- [x] P6.2 Persona pass: read every user-facing string; trim quips that appear too often; confirm at most one quip and one emoji per embed; confirm errors are plain and useful.
- [x] P6.3 Rate-limit and concurrency review: presence updates, followups, extraction semaphore, double-invocation of `/play`.
- [x] P6.4 Log review: no secrets, useful context, sensible levels.
- [x] P6.5 Run the manual QA checklist below and record results in `QA.md` (short).


**Manual QA checklist (owner and agent together)**
1. Fresh start → idle + Watching for plant.
2. Join a VC → `/play` link → audio works in a DAVE-encrypted call → presence online.
3. `/play` text search; `/play` YouTube playlist; `/play` Spotify track, album, and playlist.
4. Skip, pause/resume, loop track/queue, shuffle, remove, clear, volume.
5. `/recommend` → button → tracks queue once.
6. Leave the VC empty → auto-disconnect → presence idle.
7. Kick the bot from VC → clean state, presence idle.
8. Disconnect the network briefly during playback → recovers or fails gracefully.
9. `/ping`, `/usage`, `/about`, `/diag` (as owner and as non-owner).
10. Kill the process mid-song → `run.ps1` restarts it cleanly.

**Acceptance criteria:** checklist passes, `pytest` and `ruff` clean, README lets a fresh Windows machine reach a working bot.

**STOP — owner review. v1 complete.**

---

## Phase 7 — Economy: Core wallet

**Goal:** A server-only Renown currency: a wallet per user, `/balance`, `/daily`, `/pay`,
`/leaderboard`, an owner-only `/economy-admin`, and passive earning from voice presence and
music-command usage. Spec: `docs/superpowers/specs/2026-09-29-economy-core-design.md`. Plan:
`docs/superpowers/plans/2026-09-29-economy-core-plan.md`.

- [x] E1. Economy configuration (`ECONOMY_*` variables in `config.py` and `.env.example`).
- [x] E2. Typed errors: `DailyOnCooldownError`, `InsufficientFundsError`, `InvalidPaymentError`.
- [x] E3. Wallet models and pure `/daily` math (`economy/models.py`, `economy/daily.py`).
- [x] E4. Command-reward rate limiter (`economy/ratelimit.py`).
- [x] E5. Wallet storage: migration v2 (`wallets`, `renown_transactions`), get/ensure/credit/debit/
      pay/claim_daily/admin_adjust/leaderboard on `Database`.
- [x] E6. Background voice-presence Renown tick (`economy/voice_tracker.py`).
- [x] E7. Theme additions: Renown coin emoji, `daily_claim`/`pay_sent` quips.
- [x] E8. Economy embed builders: `balance`, `daily_claim`, `pay_sent`, `leaderboard_page`,
      `economy_admin`.
- [x] E9. Public leaderboard paginator view (`ui/views.py`).
- [x] E10. Economy slash commands (`cogs/economy.py`): `/balance`, `/daily`, `/pay`,
      `/leaderboard`, `/economy-admin`.
- [x] E11. Wired into `bot.py`: voice tracker lifecycle, music-command reward via the existing
      `on_app_command_completion` hook.
- [x] E12. Embed preview page updated with every new embed shape.

**Acceptance criteria**
- A brand-new user's first `/balance` or `/daily` grants the welcome bonus exactly once.
- `/daily` pays out with the random and streak multipliers described in the spec, refuses a
  second claim within 24h (ephemerally), and resets the streak after a 48h gap. *(manual + unit)*
- `/pay` moves Renown between two users atomically, refuses self-pay, bot targets, and
  insufficient balance before deferring.
- Sitting in a voice channel with at least one other human earns Renown on each tick; a lone
  human earns nothing. *(manual, requires two real accounts in a voice channel)*
- Using music commands earns Renown up to 5 rewarded calls per minute; further calls in the
  same window earn nothing but still work normally. *(manual + unit)*
- `/leaderboard` paginates correctly and is usable by anyone, not just the caller.
- `/economy-admin` is refused for non-owners and floors a large deduction at 0.
- An existing pre-Phase-7 database (`data/yokai.db` at schema v1) upgrades to v2 on the next
  startup without losing any `command_log`/`play_events`/`spotify_match_cache` rows.
- `pytest` and `ruff check` and `ruff format --check` are clean.

**STOP — owner review.**

---

## Phase 8 — Economy: Gambling G1 (escrow + solo games)

**Goal:** The shared bet-escrow foundation and the three solo games. Spec:
`docs/superpowers/specs/2026-09-30-gambling-design.md` (§11, phase G1). Plan:
`docs/superpowers/plans/2026-10-01-gambling-g1-plan.md`. G2 (lobbies, duel, jackpot), G3 (crash,
blackjack table) and G4 (poker) get their own plans after this phase is reviewed.

- [x] G1.1 `GAMBLING_MIN_BET` / `GAMBLING_MAX_BET` configuration.
- [x] G1.2 Typed errors: `BetOutOfRangeError`, `AlreadyInGameError`, `BetNotOpenError`,
      `InvalidGameChoiceError`; `InsufficientFundsError` gains an `action` word.
- [x] G1.3 Migration v3 (`gambling_bets`) and `Database` escrow: `place_bet`, `settle_bet`,
      `refund_bet`, `refund_open_bets`, `get_gamble_stats`.
- [x] G1.4 `gambling/` package: `rng`, `escrow` (`validate_bet`, `escrowed_bet`), `sessions`.
- [x] G1.5 Pure engines: slots, roulette, blackjack.
- [x] G1.6 Theme emoji/quips and game embeds; preview page samples.
- [x] G1.7 `/slots`, `/roulette`, `/gamble-stats`; startup refund of open bets; cog loaded.
- [x] G1.8 Interactive `/blackjack` (hit, stand, double, timeout auto-stand).

**Acceptance criteria**
- Every stake is debited when a game starts; every win, push and refund credits through an audited
  `renown_transactions` row; a loss is recorded in `gambling_bets`.
- A bet outside the min/max, above the balance, or while another game is running is rejected
  ephemerally before any defer, and costs nothing.
- Restarting the bot mid-hand returns the stake of every open bet at startup.
- Slots RTP is 94–96% (exact enumeration test); every roulette bet type has a 36/37 return;
  blackjack follows the spec's rules (3:2, dealer stands on all 17s, double on two cards, no
  splits or insurance).
- A blackjack hand left idle auto-stands and settles when the view times out.
- No embed has more than one emoji; cards and reels use text notation.
- `pytest`, `ruff check` and `ruff format --check` are clean.
- *(manual, real Discord)* `/slots`, `/roulette` (each bet type), `/blackjack` (hit, stand,
  double, natural, bust) and `/gamble-stats` behave as described; a second game is refused while
  one is running; kill the process mid-hand and confirm the stake is back after restart.

**STOP — owner review.**

---

## Phase 9 — Admin panel

**Goal:** A local, modular web admin panel for the owner, plus the moderation primitives it
manages. Spec: `docs/superpowers/specs/2026-10-05-admin-panel-design.md`. Phase P-A1 plan:
`docs/superpowers/plans/2026-10-05-admin-panel-pa1-moderation.md`. P-A2 to P-A6 each get their
own plan after the previous phase is reviewed.

- [x] P-A1 Bot-side moderation: migration v4, `ModerationStore`, `YokaiTree` and view gates,
      admin widening, voice-earning skip, heartbeat.
- [x] P-A2 Panel core and Dashboard (FastAPI + Jinja2 + HTMX, auth/CSRF, module contract).
- [x] P-A3 Access (blacklist/adminlist) and Audit modules, plus a bot-side name cache (schema v5).
- [x] P-A4 Users module (profiles, ledger-safe balance edits). Must follow the spec's
      cross-process write rule (`BEGIN IMMEDIATE`, relative updates, re-read before ledger row).
- [x] P-A5 Database module (browse, integrity, VACUUM, backup/restore).
- [x] P-A6 Logs module (current and rotated, live tail).

**Acceptance criteria (P-A1)**
- Schema version is 4; a v3 database upgrades with its rows intact.
- A blacklisted user gets an ephemeral "Access blocked" embed on every slash command and view
  button; autocomplete is denied silently; the owner can never be blocked.
- `/diag` and `/economy-admin` (renamed `/admin diag` and `/admin economy` in Phase 10 M1) work for the owner and for adminlist members, and refuse others
  with an "Admins only" embed; a user on both lists is not an admin.
- A change to either table takes effect within about 5 seconds, with no restart.
- A moderation read failure never breaks commands (last good copy, else fail open).
- Blacklisted members earn no voice Renown; the other human in the channel still does.
- `bot_heartbeat` is refreshed every 30 s while connected and cleared on a clean shutdown.
- `pytest`, `ruff check` and `ruff format --check` are clean.
- *(manual, real Discord)* Insert a row into `blacklist` for a test account (columns: `user_id`,
  `reason`, `added_ts`, `added_by`; any SQLite tool or a short `sqlite3` script works), wait 5
  seconds, then run `/ping` and press a button as that account: both are refused. Delete the
  row and confirm access returns. Add an account to `admins` and confirm `/diag` works for it.
  Watch `bot_heartbeat.ts` advance every 30 s, and vanish after stopping the bot with Ctrl+C.

**Acceptance criteria (P-A6)**
- The Logs page lists every `*.log` and `*.log.N` file in `LOG_DIR` with size and modified time;
  names are only ever taken from that listing (no path, drive, stream or symlink can be opened).
- The viewer reads a file newest first in pages of 100 entries, filters by minimum level and by
  case-insensitive literal text, keeps tracebacks with their entry, understands both the bot's and
  the supervisor's line formats, and is bounded (8 MB read, 4,000 characters per entry).
- Log text is escaped; control, ANSI and bidirectional characters are removed; invalid UTF-8 and
  BOMs are handled.
- The live tail polls every 2 s (decision: polling, not SSE), shows only complete new lines newest
  first, survives rotation and truncation with a marker, can be paused and resumed, and answers
  with a message when the file or folder is missing or a poll parameter is tampered with.
- Download returns exactly the listed file as an attachment.
- Every route is read-only (GET), needs a session, and answers with a message, not a 500, on bad
  input.
- `pytest`, `ruff check` and `ruff format --check` are clean.
- *(checked in headless Edge while building)* list, viewer, level filter, search, a traceback,
  supervisor log, live tail receiving appended lines, pause and resume, rotation marker and the
  failed-request notice, in dark 1280, light 1280 and dark 390.

**Acceptance criteria (P-A5)**
- Overview shows file size, schema, bot state and every table with its row count; the browser pages
  any table read-only; names are validated against `sqlite_master`; cell values are escaped,
  control characters dropped and long values shortened.
- Integrity check reports `ok` or up to 20 problems and writes nothing.
- VACUUM asks first, reports before/after sizes, is audited, and gives a message when the database
  is busy.
- Backups use the online backup API, are verified, never overwrite, are listed newest first, and
  can be deleted after a confirm; backup names come only from the folder listing.
- Restore needs the typed word `RESTORE`, refuses while the heartbeat is fresh or the file is in
  use, verifies the chosen backup and its schema, takes a safety backup first, removes stale WAL
  files, replaces the file, and audits into the restored database; every refusal leaves the live
  file untouched.
- Write endpoints require session and CSRF; GETs change nothing; failures answer with a message.
- `pytest`, `ruff check` and `ruff format --check` are clean.
- *(checked in headless Edge while building)* overview, wide table browser, integrity, backup,
  restore form, refusal with a fresh heartbeat, successful restore, VACUUM, delete confirm,
  failed-request notice and audit page, in dark 1280, light 1280 and dark 390.
- *(manual, real bot)* With the bot running but disconnected from Discord, confirm a restore is
  refused because the file is in use.

**Acceptance criteria (P-A4)**
- Bot side: wallet operations (`credit_wallet`, `pay`, `claim_daily`, `admin_adjust_wallet`) run in
  a `BEGIN IMMEDIATE` transaction that rolls back and releases the write lock on any failure
  (a failed operation used to leave the lock held). Review found no interleaving race: the first
  statement of each operation already took the write lock. Restart the bot to pick this up.
- People lists everyone known (wallet or name), searches by name or ID (literal match, `%` and `_`
  are not wildcards) and pages; a profile shows wallet, daily status, ledger, gambling per game,
  top tracks and commands. Unknown or malformed IDs answer with a message, never an error.
- A balance edit needs a reason, shows a preview (with a clamp explanation), asks to confirm,
  refuses a stale preview, caps at 100,000, and records the applied amount; balance equals the sum
  of deltas afterwards.
- Resetting the daily cooldown asks first, keeps the streak, and is refused when pointless.
- Refused forms keep what was typed; a failed request shows a visible notice; hostile names,
  titles and reasons are escaped; GET never changes anything.
- The audit log labels both actions and names the person.
- `pytest`, `ruff check` and `ruff format --check` are clean.
- *(checked in headless Edge while building)* search, profile, clamped preview, confirm, stale
  preview, daily reset, refused form, failed-request notice and the audit page, in dark 1280, light
  1280 and dark 390.

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
- The browser opens and the link is printed only after the port is bound; a taken port exits with
  code 1 and a hint; Ctrl+C stops the panel quietly (exit 0).
- A page whose polling fails shows a "Lost contact" notice and dims the old reading; a page whose
  session ended reloads onto the locked screen.
- `pytest`, `ruff check` and `ruff format --check` are clean.
- *(checked in headless Edge while building)* dark theme, light theme, a 500 px layout, the offline
  state and the sonar ring mid-animation render as designed.
- *(manual, real browser with the real bot)* Open the panel: the status reads Online and a sonar
  ring plays about every 5 s; stop the bot with Ctrl+C and within a few seconds the Dashboard
  reads Offline with no ring; enable "reduce motion" and confirm the ring stops; reuse the launch
  link in another browser and confirm it is refused; start a second panel and confirm the port
  message.

**Acceptance criteria (P-A3)**
- Schema is v5; the bot remembers each person's display name (at most one write a day per person)
  and a failing name write never affects a command.
- Access: blacklist and admin list can be added to and removed from. A blacklist add needs a reason
  (3-200 characters); every removal asks for confirmation; the owner can never be added to either
  list however the ID is typed; a blacklisted person cannot be made an admin; blacklisting an admin
  keeps the admin row and flags both lists.
- Every change and its audit entry commit together or not at all; two simultaneous adds leave one
  row; a busy database gives a friendly message.
- Without OWNER_ID the panel still starts, the Access page shows a note and no forms, and the
  server refuses edits.
- The bot honours a panel change within a few seconds, without a restart.
- Audit shows entries newest first with names, reasons and details, filters by module and person,
  and pages by 25; both modules add a Dashboard readout.
- Hostile names, reasons and details are escaped; write endpoints need a session and the CSRF token.
- `pytest`, `ruff check` and `ruff format --check` are clean.
- *(driven in headless Edge while building)* the real add, owner-refusal, confirm-then-remove and
  audit flows work against a real database in dark, light and 390 px layouts.
- *(manual, real browser and real bot)* Add a test account to the blacklist from the panel; within
  about five seconds that account is refused on every command and button; remove it and access
  returns. Make an account an admin and confirm `/diag` works for it. Try to add your own ID to
  each list. Confirm the Audit page lists each step with names. Stop the bot, change a list,
  restart the bot and confirm it is honoured. Run the bot once on your real database to upgrade it
  to schema v5 before starting the panel. On a phone the Remove link of the Access table sits past
  the right edge until the table is scrolled sideways.

**STOP — owner review.**

---

## Later (do not start without owner approval)
Hybrid recommender using local play/skip data, custom model, autoplay, `/search` picker, lyrics, Lavalink engine, new entertainment and QoL modules, `/privacy` data commands.

---

## Phase 10 — Multiplayer tables and lottery

**Goal:** Dedicated-channel multiplayer tables, a house-funded lottery, and tidy `/admin` and `/owner`
command groups. Spec: `docs/superpowers/specs/2026-10-07-multiplayer-tables-design.md`. Phases M1 to
M4 each get their own plan and a stop for owner review.

### M1 — Command groups

Plan: `docs/superpowers/plans/2026-10-07-multiplayer-m1-command-groups.md`.

- [x] M1.1 `/admin` group with `economy` (was `/economy-admin`).
- [x] M1.2 `/admin diag` (was `/diag`).
- [x] M1.3 `/owner` group, registered only once it has a subcommand.
- [x] M1.4 Extensions loaded; `/diag` mentions updated; docs updated.

**Acceptance criteria (M1)**
- `/admin economy` and `/admin diag` work for the owner and for adminlist members and refuse others
  with the ephemeral "Admins only" embed; the old `/economy-admin` and `/diag` no longer exist.
- `/owner` is absent from the command tree until it has a subcommand.
- `pytest`, `ruff check` and `ruff format --check` are clean.
- *(manual, real Discord)* Restart the bot; `/admin` offers `economy` and `diag`; `/diag` and
  `/economy-admin` are gone; `/admin diag` shows the **Update yt-dlp** button; a non-admin is refused.

**STOP — owner review.**

### M2 — Tables, pot accounting and duel

Plan: `docs/superpowers/plans/2026-10-07-multiplayer-m2-tables-and-duel.md`.

- [x] M2.1 Table configuration and errors.
- [x] M2.2 Migration v6, pot accounting on solo settlement.
- [x] M2.3 Atomic `settle_lobby`.
- [x] M2.4 Table channel storage.
- [x] M2.5 Lobby state machine and duel engine.
- [x] M2.6 `TableManager`.
- [x] M2.7 Table and duel embeds.
- [x] M2.8 Play, stake form, lobby and duel views.
- [x] M2.9 `TablesCog`, `/admin set_multiplayer`, `/admin unset_multiplayer`, bot wiring.

**Acceptance criteria (M2)**
- `/admin set_multiplayer` offers every planned game (Duel, Jackpot, Crash, Blackjack table,
  Lottery); the Lottery is still refused with a plain "not open yet"
  message (the other games arrived in M3). `/admin set_multiplayer game:Duel` in a text channel posts a Play embed that keeps
  working after the bot restarts; `/admin unset_multiplayer` removes it.
- Pressing Play asks for a stake, opens a table, and deletes the previous table's message; the
  finished table's result stays on screen until the next table is requested.
- A table in play refuses another Play press; the host or an admin can cancel an unstarted
  table (everyone is refunded); a started table cannot be cancelled.
- However a table ends (finished, tied, cancelled, aborted, or cut off by a restart), a fresh
  Play embed is posted under its result and the old one is removed, so the button is always at
  the bottom of the channel.
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


---

## Phase 11 — Poker table

**Goal:** Texas hold'em as a cash-game table in a dedicated channel. Spec:
`docs/superpowers/specs/2026-10-10-poker-design.md`. Phases P1 to P4 each get their own plan and a
stop for owner review.

### P1 — Engine

Plan: `docs/superpowers/plans/2026-10-10-poker-p1-engine.md`.

- [x] P1.1 Error type, deck and hand evaluator.
- [x] P1.2 Side pots and the one-hand betting engine.
- [x] P1.3 Conservation property test.

**Acceptance criteria (P1)**
- The evaluator ranks all nine categories correctly, including the wheel and steel wheel, kickers
  and best-5-of-7.
- A hand plays blinds, action order (heads-up included), min-raise, short all-ins that do not
  reopen betting, check-or-fold timeouts, fold-wins and showdowns.
- Side pots are built from contribution levels; ties split with the odd chip to the first winner
  left of the button; uncalled chips are returned.
- 300 random legal hands never create or destroy chips.
- `pytest`, `ruff check` and `ruff format --check` are clean. Nothing here touches Discord, the
  database or the network.

**STOP — owner review.** P2 (money: `cash_out`) is planned after this review.

### P2 — Money

Plan: `docs/superpowers/plans/2026-10-10-poker-p2-money.md`.

- [x] P2.1 `GAMBLING_POKER_MAX_HANDS` setting and wiring.
- [x] P2.2 Poker game spec (listed, not yet playable) and lobby cash-out bookkeeping.
- [x] P2.3 `Database.cash_out_lobby` and capped poker refunds.
- [x] P2.4 `TableManager.cash_out` and `settle_poker`.

**Acceptance criteria (P2)**
- A poker seat can be paid its chips mid-game, including more than its buy-in, and the one-game
  claim is released; the table keeps running.
- The table-wide limit holds: Renown paid out never exceeds the sum of buy-ins, and a batch that
  would break it pays nobody.
- The final settle must pay exactly what was bought in; seats paid earlier are never paid or
  refunded again, and a claim they took since is left alone.
- After a cash-out, an abort, a failed settlement or a restart refunds the remaining seats only
  their share of what is left, so no Renown is created.
- Poker cash-outs add nothing to the lottery pot.
- Poker is listed in the `/admin set_multiplayer` dropdown but cannot be opened yet (P3).
- `pytest`, `ruff check` and `ruff format --check` are clean.

**STOP — owner review.** P3 (table UI) is planned after this review.

### P3 — Table UI

Plan: `docs/superpowers/plans/2026-10-10-poker-p3-table-ui.md`.

- [x] P3.1 The multi-hand table (`PokerTable`): blinds, button, busts, leavers, hand cap.
- [x] P3.2 Poker embeds (table, hand recap, final).
- [x] P3.3 Views and runner: action buttons, My cards, Raise form, Leave, timers; poker playable.
- [x] P3.4 Docs.

**Acceptance criteria (P3)**
- A poker table opens from the Play embed, fills with equal buy-ins, and plays hand after hand on one
  message edited in place; only the acting player's buttons work.
- Hole cards are only ever shown ephemerally to their owner, and publicly only at a showdown.
- A missed turn checks when free and folds otherwise; two in a row remove the player; Leave folds and
  pays out; a busted player is paid nothing and removed.
- The table closes at the hand cap or when fewer than two players are left, and pays everyone their
  chips: Renown in equals Renown out, nothing goes to the lottery pot.
- A failure at any point refunds the seats still at the table and tells the channel.
- Poker is playable (listed, openable, `/admin set_multiplayer game:Poker table` works).
- `pytest`, `ruff check` and `ruff format --check` are clean.
- *(manual, real Discord)* Dedicate a channel to Poker; open a table with 3 members; play several
  hands using Call, Raise (below the minimum, junk text, a valid raise), All-in and Fold; check My
  cards is private and a stranger is refused; let a turn time out, then leave with Leave; bust a
  player; restart the bot mid-hand and check balances and that a fresh Play embed returns; play to
  the hand cap (`GAMBLING_POKER_MAX_HANDS=3` is handy) and check the final embed and `/balance`.

Manual real-Discord checklist: run by the owner on 2026-10-12, passed.

**STOP — owner review.** Approved 2026-10-12.

### P4 — Docs and wrap-up

- [x] P4.1 README, `.env.example` and `TASKS.md` describe poker (done within P3.4).
- [x] P4.2 Manual real-Discord QA recorded as passed (see P3 acceptance).
- [x] P4.3 Final checkpoint tag `checkpoint/poker-p4`.

**Known minors (deferred, not blocking)**
- Timeout refresh edits the table message outside the view lock.
- Recap can label a whole later one-player pot "uncalled" when part was folded chips.
- Leave pressed as a turn times out shows "not your turn" although the leave is recorded.
- A failed reply edit leaves the public message stale until the next action.


---

## Phase 12 — Challenges

**Goal:** Daily and weekly challenges that pay Renown automatically and push people toward the music
bot and the gamemodes. Spec: `docs/superpowers/specs/2026-10-13-challenges-design.md`. Plan:
`docs/superpowers/plans/2026-10-13-challenges.md`.

- [x] 1 Configuration (`CHALLENGE_*`) and period math.
- [x] 2 Models, template pool and slate picker.
- [x] 3 Migration v8 and atomic challenge progress storage.
- [x] 4 The challenge engine.
- [x] 5 Settled bets reported to the engine.
- [x] 6 Listened tracks and voice time reported to the engine.
- [x] 7 Challenge embeds and the notice scheduler.
- [x] 8 The `/challenges` command and its Daily/Weekly view.
- [x] 9 `/admin challenges` list, reroll and set.
- [x] 10 Docs, spec sync and manual QA checklist.

**Acceptance criteria**
- Three daily and three weekly challenges are active at all times, one per category, and the stored
  slate survives a restart.
- Music, game and voice progress is recorded without changing how those features behave; an engine
  failure never breaks playback, a settlement or voice crediting.
- Each challenge pays exactly once per user per period, in the same transaction that marks it done.
- `/challenges` shows progress and the reset time; `/admin challenges list|reroll|set` works for the
  owner and the adminlist and refuses everyone else.
- `pytest`, `ruff check` and `ruff format --check` are clean.
- *(manual, real Discord)* The checklist in `QA.md` (rows 26 to 33).

**STOP — owner review.** Manual QA is pending the owner's run.

**Known minors (deferred, not blocking)**
- `periods.next_reset_ts` at a nonexistent DST midnight is untested, and only the literal `UTC` avoids needing `tzdata`.
- The named-zone DST test is skipped without `tzdata`, so that path is unverified.
- The engine's round-dedupe cache is marked before the DB write, so a failed write suppresses that round's retry.
- An override racing an in-flight event can pay a template that was just removed.
- `reroll` can repeat the same slate.
- The scheduler's `_last_purge=0.0` vs `time.monotonic()` delays the first purge on a freshly booted PC.
- Voice minutes use `max(1, interval // 60)` per tick, so non-default `ECONOMY_VOICE_INTERVAL_SECONDS` values are counted imprecisely.
- A near-certain win (for example cashing out of crash at ~1.01x) counts as a won round.
- Very short videos have no minimum duration for music challenges.
- `/admin challenges` gives no hint when challenges are disabled.
- No tests assert that `setup_hook` starts the scheduler or the `close()` call order.
- Some UI and admin tests could assert more (timeout edit, tab styling, confirmation embed content).
- Lottery tickets are not counted as rounds (by design).
