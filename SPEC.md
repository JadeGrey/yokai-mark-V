# Yokai — Product & Technical Spec

Companion files: `AGENTS.md` (standing rules) and `TASKS.md` (ordered phases). If this spec and reality disagree (APIs change fast), flag it and propose a fix.

---

## 1. Overview

**Yokai** is a private Discord bot for one server (about 10 users at most) that runs on the owner's Windows PC. Its name and profile picture come from Echo's Yokai drone in Rainbow Six Siege. Purpose: entertainment and quality of life. The first and most important module is **music discovery and streaming**.

### v1 scope
1. Foundations: config, logging, SQLite storage, universal embed system, presence manager.
2. Music package: play from YouTube links, YouTube search queries, and Spotify track/album/playlist links; queue controls.
3. `/recommend`: recommendations from YouTube Music radio, with a button that queues them.
4. Utility commands: `/ping`, `/usage`, `/about`, plus an owner-only `/diag`.

### Non-goals for v1
Other entertainment/QoL modules (they come later, so keep the architecture extensible), multi-server support, web dashboard, Lavalink, non-YouTube audio sources, lyrics, audio filters, a custom ML recommender, autoplay when the queue ends, per-user Spotify/YouTube account linking.

---

## 2. Locked decisions

| Area | Decision | Why |
|---|---|---|
| Language / library | Python 3.12+, `discord.py>=2.7.1` | 2.7.0 added DAVE support; owner is fluent in Python |
| Voice encryption | `davey` installed | Discord requires DAVE E2EE for voice since March 2026 |
| Playback | `yt-dlp` in-process + FFmpeg, PCM with volume control | One process, simple on Windows, fastest to patch when YouTube changes |
| Audio source | YouTube only | Spotify does not allow bots to stream its audio |
| Spotify | Metadata only: official API first, scraper fallback, then match to YouTube | Official API can't read most public playlists in Dev Mode (see §7.4) |
| Recommendations | YouTube Music radio via `ytmusicapi` | No keys, results are already YouTube-native |
| Storage | SQLite (`aiosqlite`, WAL mode) | Tiny data, single process |
| Scope | Single guild, guild-scoped commands (instant sync) | Private bot |
| Hosting | Owner's Windows PC | Bot is offline when the PC is off or asleep |

Design rule: every external integration hides behind an interface so it can be replaced (Lavalink playback, a different recommender, a different Spotify provider) without touching feature code.

---

## 3. Identity and persona

**Identity.** Name: Yokai. The owner sets the display name and avatar in the Discord Developer Portal. Do not fetch, embed, or commit Ubisoft artwork, audio, or quotes.

**Bio (for `/about`):** *"Recon drone with opinions about your music taste. I find it, I play it, I quietly judge it."*

**Voice rules**
- Confident and a little cocky, never mean, never at a user's expense, never over the top.
- At most one short quip (about 8 words or fewer) per message. Many messages should have none.
- Clarity first: errors and troubleshooting text are plain and specific. Any quip goes in a separate line or the footer, never inside the explanation.
- At most one emoji per embed, from a small fixed set defined in `theme.py`.
- A light nod to the game is fine ("scanning", "recon", "on the wire"). No memes, no slang overload.

**Line bank** (original writing; store as data in `theme.py`, at least 3 variants per key, never repeat the same variant twice in a row):

| Key | Examples |
|---|---|
| `play_start` | "Locked in." / "On it." / "Here we go." |
| `queued` | "Queued. Obviously." / "Added to the lineup." / "Noted." |
| `skip` | "Skipped. Bold call." / "Gone. Next." |
| `search_wait` | "Scanning…" / "Looking around…" |
| `recs_intro` | "Recon complete. Try these." / "Scanned the area. These are worth your time." |
| `recs_queued` | "All of them. Good instinct." / "Sent to the queue." |
| `empty_queue` | "Nothing in the queue. Feed me something." |
| `not_in_vc` | "Join a voice channel first, then we'll talk." |
| `error_lead` | "That one slipped past me." / "Didn't go to plan." (followed by plain details) |
| `ping_good` | "Fast. As expected." |
| `idle_leave` | "Nothing to watch. Heading out." |

---

## 4. Presence

- Activity: `discord.Activity(type=ActivityType.watching, name="for plant")`, which displays as **Watching for plant**.
- Status: **idle** by default; **online** while the bot is connected to a voice channel; back to **idle** on disconnect.
- Pass the initial presence (idle + activity) to the `Bot` constructor so there is no flicker on startup.
- Implement a `PresenceManager` with a small state machine (`IDLE`, `ACTIVE`). It is the only code allowed to call `change_presence`. Updates are idempotent (only call Discord when the state actually changes), because gateway presence updates are rate limited (about 5 per 20 seconds).
- Drive it from the player's connect/disconnect events and from `on_voice_state_update` for the bot's own member (covers being kicked or moved).
- Unit-test the state machine with a fake client.

---

## 5. Universal embed system (`ui/embeds.py`, `ui/views.py`, `theme.py`)

Goal: every message Yokai sends looks like it came from the same bot, and feature code never builds embeds by hand.

**Requirements**
1. One `EmbedFactory` (or module of builder functions). Feature code calls semantic builders: `info`, `success`, `warning`, `error`, `now_playing`, `queued`, `queue_page`, `recommendations`, `ping`, `usage`, `about`, `diag`.
2. A shared base builder applies: semantic color, consistent title style, footer (`Yokai · <context>` with the bot avatar as icon), optional thumbnail, optional one-line quip from the line bank.
3. Colors, emoji, and footer text live in `theme.py`. Suggested default: one brand accent color for music/info, plus green/amber/red for success/warning/error. The owner can retune them in one place.
4. Enforce Discord limits centrally with `clamp()` helpers: title 256, description 4096, field name 256, field value 1024, footer 2048, 25 fields, 6000 characters total. Truncate with an ellipsis; never raise.
5. A single `send()` helper handles the interaction lifecycle (response vs followup vs edit), ephemeral flag (errors default to ephemeral), `allowed_mentions=none`, and attaching views.
6. `BaseView` for all buttons/selects: timeout, disable on timeout, `interaction_check` that restricts who may press, and safe error handling. `QueuePaginator` and `RecommendView` extend it.
7. Error embeds have a fixed shape: quip line (optional) + plain explanation + optional "what to try" hint.
8. Tests: every builder respects limits (including hostile inputs such as 10,000-character titles); every quip key has 3+ variants; a lint-style test greps feature modules for raw text sends.

---

## 6. Commands

All are slash commands, guild-scoped. Anything touching the network defers first.

| Command | Args | Behavior |
|---|---|---|
| `/play` | `query` (link or search text) | Caller must be in a voice channel. Joins if needed. Resolves per §7. Single track: "now playing" or "queued at position N". Album/playlist: queue up to `MAX_PLAYLIST_TRACKS`, start the first track as soon as it resolves, resolve the rest lazily, and post one summary embed (including "loaded N of M" when partial). |
| `/pause`, `/resume` | none | Standard. |
| `/skip` | none | Skips the current track. |
| `/stop` | none | Stops playback, clears the queue, leaves the channel (presence returns to idle). |
| `/queue` | `page` (optional) | Paginated embed with prev/next buttons (`QueuePaginator`). |
| `/nowplaying` | none | Title, artist, requester, elapsed/total with a text progress bar. Track position yourself (monotonic clock minus paused time). |
| `/remove` | `position` | Removes one queued track. |
| `/clear` | none | Clears upcoming tracks, keeps the current one. |
| `/shuffle` | none | Shuffles upcoming tracks. |
| `/loop` | `mode`: off / track / queue | Loop behavior. |
| `/volume` | `level` 0–100 | Default 50. |
| `/recommend` | `seed` (optional) | See §8. |
| `/ping` | none | See §9. |
| `/usage` | `period`: all / 30d / 7d | See §9. |
| `/about` | none | See §9. |
| `/diag` | none | Owner-only, ephemeral. See §9. |

**Voice-channel rules.** Music commands require the caller to be in a voice channel (except `/queue` and `/nowplaying`). If the bot is in a different channel that still has non-bot listeners, refuse politely. If the bot lacks Connect/Speak permission, say exactly which permission is missing. Everyone in the voice channel may use playback controls (small trusted group; no vote-skip).

---

## 7. Music package

### 7.1 Pipeline
`input → classify → resolve to Track(s) → queue → (just before playback) fetch fresh stream URL → FFmpeg → Discord voice`

Stream URLs expire, so **never store stream URLs in the queue**. Queue entries are lightweight `Track` records (video ID, title, artist, duration, thumbnail, requester, origin). Fetch the stream URL when the track becomes next (prefetch), and again on retry.

### 7.2 Input classification
| Input | Handling |
|---|---|
| YouTube video URL (`watch`, `youtu.be`, `shorts`, `music.youtube.com`) | Single track. Ignore an attached `list=` parameter unless the URL is a `/playlist?list=` link. |
| YouTube playlist URL | Flat extraction, capped at `MAX_PLAYLIST_TRACKS`. Auto-generated mixes (`RD…`) get a friendly "not supported" message. |
| Spotify track / album / playlist (URL, `spotify:` URI, `spotify.link` short link) | See §7.4. Artist, show, and episode links get a friendly "not supported" message. |
| Any other URL | Rejected with a friendly message (allowlist in `AGENTS.md`). |
| Plain text | Search: yt-dlp `ytsearch5:<query>`; take the first non-live result within `MAX_TRACK_SECONDS`. |

### 7.3 YouTube resolver (yt-dlp)
- Interface (sketch): `resolve_query(text) -> Track`, `resolve_url(url) -> Track | list[Track]`, `get_stream(track) -> StreamInfo(url, http_headers, expires_hint)`.
- Run yt-dlp via `asyncio.to_thread` with `asyncio.wait_for` (about 30s) and a semaphore (max 2 concurrent extractions).
- Options to start from: `format="bestaudio/best"`, `noplaylist=True` for single items, `extract_flat="in_playlist"` for playlists, `skip_download=True`, `quiet=True`, `socket_timeout=15`. Optional `cookiefile` from `YTDLP_COOKIES_FILE` (off by default).
- **JavaScript runtime:** current yt-dlp needs an external JS runtime (Deno) plus the `yt-dlp-ejs` component (included with `yt-dlp[default]`) for full YouTube support. Configure it through yt-dlp's params (`js_runtimes`, `remote_components` — verify names against the installed version and the EJS wiki) and verify Deno at startup.
- Pass yt-dlp's returned `http_headers` to FFmpeg when needed.
- **YouTube is the fragile part.** YouTube has been moving clients to SABR-only streaming, which removes direct audio URLs for some clients. Expect breakage every few weeks. Mitigations: keep yt-dlp current (see below), keep the resolver behind an interface, classify failures (§7.7), and surface health in `/diag`. A PO-token provider (e.g. `bgutil-ytdlp-pot-provider`) is a later fallback only if 403 or bot-check errors appear.
- **Update workflow:** log the yt-dlp version at startup and warn if its release date is more than 30 days old. `/diag` shows the version and has an **Update yt-dlp** button (owner-only) that runs `python -m pip install -U "yt-dlp[default]"` as a subprocess with an argument list (no shell), then tells the owner to restart the bot.

### 7.4 Spotify: hybrid, metadata only
Spotify never provides audio. The job is: URL → list of `(title, artists, duration)` → match each to a YouTube track.

Provider interface: `get_track(id)`, `get_album(id)`, `get_playlist(id)` returning `SpotifyTrackMeta` or `SpotifyCollection(name, tracks, total, partial)`.

Orchestrator:
1. Try the **official provider** (if credentials are configured and it's healthy).
2. On `Forbidden`, missing items, or an auth failure, fall back to the **scraper provider** (if enabled).
3. If both fail, show a friendly error suggesting a YouTube link or search text.
4. Log which provider served each request. Add a simple circuit breaker: after 3 consecutive failures, skip that provider for 10 minutes.

**Official provider notes (verify against the current Spotify docs):**
- Since the February–March 2026 Dev Mode changes: the app owner must have Spotify Premium, new apps are limited to 5 authorized users, search results are capped at 10, and several endpoints were removed.
- Playlist endpoints were renamed from `/playlists/{id}/tracks` to `/playlists/{id}/items`, and **playlist items are only returned for playlists the authorized user owns or collaborates on.** Most pasted public playlists will therefore hit the fallback.
- Recommendations, audio features, and related artists are gone. Do not use them, and do not call Spotify search.
- Spotify said it is moving away from client-credentials for metadata endpoints. Implement whichever auth flow currently works; if Spotify rejects the app, disable the official provider automatically and rely on the scraper.
- Use `httpx` (async), not a synchronous Spotify client.

**Scraper provider notes:**
- Use the `spotifyscraper` package (reads public web-player data using an anonymous token from Spotify's embed pages). Pin an exact version; wrap every call in `asyncio.to_thread`; treat all exceptions as provider failure.
- It relies on undocumented endpoints and may break without notice. Cache results for 24 hours. Cap tracks at `MAX_PLAYLIST_TRACKS`. Some embed-based paths return only the first 50–100 tracks, so support `partial=True` and show "loaded N of M".

### 7.5 Spotify → YouTube matching (`music/resolvers/matcher.py`, pure functions)
1. Query with `"<primary artist> <title>"`. Candidates come first from `ytmusicapi` song search (`filter="songs"`, limit 5), which gives clean audio tracks with duration; if nothing scores well, fall back to yt-dlp `ytsearch5:`.
2. Score each candidate: normalized title similarity (strip "(feat. …)", "- Remastered", punctuation; use `difflib`), artist overlap, and duration difference (≤3s excellent, ≤10s acceptable, >15s reject).
3. Penalize live, cover, remix, karaoke, instrumental, sped up, slowed, 8D, and reverb unless the Spotify title contains the same word.
4. Accept above a tunable threshold; otherwise mark the track "couldn't match" and skip it, listing skipped tracks in the summary embed.
5. Cache successful matches in `spotify_match_cache` (functional cache only; never used for analytics or ML).
6. Table-driven unit tests covering: exact match, remaster, feature credit, live-version trap, wrong-duration trap, non-Latin titles.

Spotify collection tracks queue as `PENDING_MATCH` and are matched lazily just before they are needed, so `/play` on a 100-track playlist starts within seconds.

### 7.6 Player and queue (`music/player.py`)
- One `GuildPlayer` per guild (only one exists in practice). State machine: `IDLE → LOADING → PLAYING ⇄ PAUSED → IDLE`.
- Queue with loop modes (off/track/queue), a history list (last 100 tracks), max size `MAX_QUEUE_SIZE`.
- Audio: `FFmpegPCMAudio` wrapped in `PCMVolumeTransformer`. FFmpeg before-options: `-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5`; options: `-vn`.
- The `after` callback runs in a non-async thread: hand control back with `loop.call_soon_threadsafe` or `asyncio.run_coroutine_threadsafe`.
- Prefetch the next stream URL while the current track plays.
- Auto-disconnect: `ALONE_DISCONNECT_SECONDS` when no humans remain in the channel; `IDLE_DISCONNECT_SECONDS` after the queue ends.
- Handle being kicked/moved: clean up state and return presence to idle.
- **Play events:** on every track end, write a `play_events` row with outcome `completed` (ended naturally or ≥90% listened), `skipped`, `stopped`, or `error`, plus `listened_s`. This is cheap now and gives a future custom recommender real data.

### 7.7 Typed errors and behavior
| Error | Cause | Behavior |
|---|---|---|
| `NotFoundError` | No results / deleted video | Plain error embed, continue queue |
| `LoginRequiredError` | Age-gated / private / members-only | Say why; skip; continue |
| `UnavailableError` | Region-blocked / removed | Skip; continue |
| `BotCheckError` | Bot check, 403, SABR/no formats | One retry after backoff; then skip; count toward health |
| `TimeoutError` | Extraction too slow | One retry; then skip |
| `UnsupportedError` | Bad host, unsupported Spotify type, live stream | Friendly message |

After 3 consecutive extraction failures, mark YouTube health as degraded, post one escalation embed ("YouTube is giving me trouble; owner can check `/diag`"), and record it. Keep a ring buffer of the last 20 sanitized errors for `/diag`.

---

## 8. Recommendations (`music/recommend/`)

**Interface:** `Recommender.recommend(seed: Track, count: int, exclude: set[str]) -> Recommendation(tracks, seed, strategy)`. v1 ships one implementation, `YTMusicRadioRecommender`. A hybrid recommender (local play/skip re-ranking) and a custom model are future work (§15).

**`/recommend [seed]` behavior**
1. Choose the seed, in order: the optional `seed` argument (query or link, resolved via §7); the currently playing track; the requesting user's most recent completed track from `play_events`; otherwise an error embed asking for a seed.
2. Call `ytmusicapi` `get_watch_playlist(videoId=<seed>, radio=True, limit=~25)` unauthenticated, via `asyncio.to_thread`. Verify field names against the installed version (`videoId`, `title`, `artists`, `length`, `videoType`, thumbnails).
3. Filter: drop the seed itself, tracks already queued or playing, the last 100 plays, duplicates by normalized title+artist (even with different video IDs), non-music video types where identifiable, and tracks over 10 minutes.
4. Take `RECOMMEND_COUNT` (default 8, valid 5–10).
5. Cache results per seed for 10 minutes.
6. Post one embed: quip, numbered list (`Title — Artist  \`3:41\``), footer naming the seed, and a single **button** (label from `theme.py`, e.g. "Queue them all").
7. Button behavior (`RecommendView`): restricted to users in the bot's voice channel or the requester; if the bot isn't connected, join the presser's channel; queue all listed tracks; edit the embed to a "queued" state and disable the button; single use; times out after 180s and disables itself. Guard with a lock so a double-click can't queue twice.
8. If ytmusicapi fails or returns too few tracks, show an error embed; never crash.

---

## 9. Utility commands

**`/ping`.** One embed with a status line colored by the worst measured latency (good <150ms, ok <400ms, bad ≥400ms):
- Gateway: `bot.latency` (handle not-ready / NaN).
- REST round-trip: time a cheap REST call (e.g. `application_info()`; confirm it isn't cached).
- Voice: `voice_client.latency` and `average_latency` when connected; otherwise "not connected".
- Resolver: rolling average of the last 10 track resolves; "no data yet" if none.
- Database: time `SELECT 1`.

**`/usage [period]`.** Ranking of the top 10 users by commands run (from `command_log`), with each user's count of tracks requested (from `play_events`), plus the top 5 commands overall. Period: all / 30d / 7d. Render users as mentions inside the embed (no members intent needed, no pings). Log every slash command through one central hook (`on_app_command_completion` plus errors), never inside individual commands.

**`/about`.** Name, bio, version (package metadata), uptime, host OS, stack with versions (Python, discord.py, yt-dlp, ytmusicapi, FFmpeg), owner mention, and the line: *"Unaffiliated fan project inspired by Echo's Yokai drone from Rainbow Six Siege. © Ubisoft."*

**`/diag` (owner-only, ephemeral).** Python/discord.py versions; `davey` importable and its version (DAVE status); FFmpeg and Deno paths and versions; yt-dlp version and age; YouTube health flag; Spotify provider status (official enabled/healthy, scraper enabled/healthy, circuit-breaker state); ytmusicapi version; database size; last 20 sanitized errors; the **Update yt-dlp** button (§7.3).

---

## 10. Data model (SQLite, WAL, migrations via `PRAGMA user_version`)

```sql
CREATE TABLE command_log (
  id INTEGER PRIMARY KEY,
  ts INTEGER NOT NULL,            -- unix seconds, UTC
  user_id INTEGER NOT NULL,
  command TEXT NOT NULL
);

CREATE TABLE play_events (
  id INTEGER PRIMARY KEY,
  ts INTEGER NOT NULL,
  user_id INTEGER NOT NULL,       -- requester
  video_id TEXT NOT NULL,         -- YouTube video ID
  title TEXT NOT NULL,            -- YouTube-derived
  artist TEXT,                    -- YouTube-derived
  duration_s INTEGER,
  origin TEXT NOT NULL,           -- link | search | import | recommendation
  outcome TEXT NOT NULL,          -- completed | skipped | stopped | error
  listened_s INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE spotify_match_cache (
  spotify_id TEXT PRIMARY KEY,
  video_id TEXT NOT NULL,
  confidence REAL NOT NULL,
  ts INTEGER NOT NULL
);
```

Privacy: store Discord user IDs, command names, and play events only. No message content. `spotify_match_cache` exists only to avoid re-matching and must never feed analytics or ML. Keep the database file out of version control.

---

## 11. Dependencies and configuration

**Runtime:** `discord.py[voice]>=2.7.1`, `davey`, `yt-dlp[default]`, `ytmusicapi`, `httpx`, `aiosqlite`, `python-dotenv`, `spotifyscraper` (exact pin). Use stdlib `difflib` for similarity.
**Dev:** `pytest`, `pytest-asyncio`, `ruff`.
**External tools on PATH:** `ffmpeg`, `deno`.

**Environment variables** (all documented in `.env.example`):

| Variable | Default | Purpose |
|---|---|---|
| `DISCORD_TOKEN` | required | Bot token |
| `GUILD_ID` | required | The one server |
| `OWNER_ID` | required | Owner-only commands |
| `LOG_LEVEL` | `INFO` | Logging |
| `DB_PATH` | `data/yokai.db` | SQLite file |
| `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` | empty | Enables the official provider |
| `SPOTIFY_SCRAPER_ENABLED` | `true` | Enables the scraper fallback |
| `YTDLP_COOKIES_FILE` | empty | Optional cookie file (off by default) |
| `FFMPEG_PATH` / `DENO_PATH` | empty | Override PATH lookup |
| `MAX_QUEUE_SIZE` | `200` | Queue cap |
| `MAX_PLAYLIST_TRACKS` | `100` | Playlist/album cap |
| `MAX_TRACK_SECONDS` | `3600` | Reject longer tracks |
| `IDLE_DISCONNECT_SECONDS` | `300` | Leave after the queue ends |
| `ALONE_DISCONNECT_SECONDS` | `120` | Leave when alone |
| `RECOMMEND_COUNT` | `8` | Tracks per `/recommend` |

Invite scopes: `bot`, `applications.commands`. Permissions: View Channels, Send Messages, Embed Links, Connect, Speak.

---

## 12. Windows environment

- Python 3.12+, FFmpeg, and Deno on PATH (for example via `winget`; verify current package IDs).
- A `run.ps1` that activates the venv and restarts the bot on crash with a short delay. Optionally start it at login with Task Scheduler.
- Logs in `logs/` (rotating). The bot is offline whenever the PC sleeps; the README should note it.
- Outbound connections only; no firewall or port forwarding required.

---

## 13. Testing strategy

- Default `pytest` run makes **no network calls**. Use fakes for `Resolver`, `SpotifyProvider`, and `Recommender`.
- Unit tests: URL classification and allowlist; matcher scoring (table-driven); queue and loop logic; player state machine with a fake voice client; presence state machine; embed limits and quip-bank rules; `/usage` aggregation queries; error mapping; circuit breaker; recommendation filtering.
- `live` tests (skipped by default): yt-dlp against a known public video, ytmusicapi radio, scraper against a known public playlist.
- Manual QA checklist lives in `TASKS.md` Phase 6.

---

## 14. Risks

| Risk | Mitigation |
|---|---|
| YouTube changes break extraction | Interface boundary, typed errors, health flag, update button, PO-token fallback only if needed |
| Spotify changes or restricts scraping | Circuit breaker, official-first order, graceful "paste a YouTube link" message |
| `ytmusicapi` is unofficial | Interface boundary; failures degrade to an error embed |
| DAVE / voice library changes | `davey` check at startup; `/diag` surfaces status |
| YouTube's terms restrict this kind of use | Private, small, non-monetized, not publicly listed |
| PC uptime | Documented; auto-restart script |

---

## 15. Future (out of scope for v1)

Hybrid recommender (external candidates re-ranked by local play/skip history), custom model trained on `play_events` (YouTube-derived fields only), autoplay when the queue ends, `/search` picker with a select menu, lyrics, a Lavalink engine behind the `Resolver`/player interfaces, a `/privacy` data-export/delete command, and further entertainment and QoL modules.

---

## 16. References (verify before relying on them)

- discord.py changelog (DAVE in 2.7.0): https://discordpy.readthedocs.io/en/stable/whats_new.html
- Discord voice connections and DAVE: https://docs.discord.com/developers/topics/voice-connections
- yt-dlp EJS / JavaScript runtime requirement: https://github.com/yt-dlp/yt-dlp/wiki/EJS
- Spotify February 2026 migration guide: https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide
- ytmusicapi docs: https://ytmusicapi.readthedocs.io
- SpotifyScraper: https://github.com/AliAkhtari78/SpotifyScraper
- Antigravity best practices: https://antigravity.google/docs/cli/best-practices/
