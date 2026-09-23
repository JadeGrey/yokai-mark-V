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

- [ ] P5.1 `/ping`: gateway, REST round-trip, voice, resolver (rolling average of last 10), database; status color from worst latency; NaN/not-ready safe.
- [ ] P5.2 `/usage`: top 10 by commands, tracks requested per user, top 5 commands, period option; mentions inside the embed.
- [ ] P5.3 `/about`: fields and disclaimer per `SPEC.md` §9.
- [ ] P5.4 `/diag` (owner-only, ephemeral): versions, DAVE status, FFmpeg/Deno, yt-dlp age, YouTube health, Spotify provider status, ytmusicapi version, DB size, last 20 sanitized errors, **Update yt-dlp** button (argument-list subprocess, no shell, tells the owner to restart).
- [ ] P5.5 Tests: usage aggregation queries (period boundaries, ties, empty data), ping formatting, owner check on `/diag`.

**Acceptance criteria**
- All four commands render correctly in themed embeds and stay within embed limits. *(manual)*
- `/usage` reflects real command and play history. `/diag` is refused for non-owners.
- `pytest` and `ruff` clean.

**STOP — owner review.**

---

## Phase 6 — Hardening and polish

- [ ] P6.1 `run.ps1` (activate venv, restart on crash with delay); README covers setup, `.env`, running, updating yt-dlp, and the note that the bot is offline while the PC sleeps.
- [ ] P6.2 Persona pass: read every user-facing string; trim quips that appear too often; confirm at most one quip and one emoji per embed; confirm errors are plain and useful.
- [ ] P6.3 Rate-limit and concurrency review: presence updates, followups, extraction semaphore, double-invocation of `/play`.
- [ ] P6.4 Log review: no secrets, useful context, sensible levels.
- [ ] P6.5 Run the manual QA checklist below and record results in `QA.md` (short).

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

## Later (do not start without owner approval)
Hybrid recommender using local play/skip data, custom model, autoplay, `/search` picker, lyrics, Lavalink engine, new entertainment and QoL modules, `/privacy` data commands.
