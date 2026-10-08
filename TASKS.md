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
- [ ] P-A5 Database module (browse, integrity, VACUUM, backup/restore).
- [ ] P-A6 Logs module (current and rotated, live tail).

**Acceptance criteria (P-A1)**
- Schema version is 4; a v3 database upgrades with its rows intact.
- A blacklisted user gets an ephemeral "Access blocked" embed on every slash command and view
  button; autocomplete is denied silently; the owner can never be blocked.
- `/diag` and `/economy-admin` work for the owner and for adminlist members, and refuse others
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
