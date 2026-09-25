# Yokai Overhaul: Design Spec

Date: 2026-09-24. Status: approved in conversation, pending written-spec review.
Companion files: `AGENTS.md`, `SPEC.md`, `TASKS.md`. Where this spec changes a locked decision, the owner approved it in the brainstorming session and the affected docs are updated in the same phase.

## 1. Goal and success criteria

Make Yokai play music fast and reliably, look consistently polished, and add a few features the owner will use.

Success looks like:
- Cold `/play` starts audio in about 1.5s or less at p50, measured by the live harness. p95 is reported, and any miss is stated plainly.
- No audible gap between queued tracks.
- `/skip` and `/stop` always work, including after a failed or stuck track.
- All embeds share one refined look while obeying the persona rules.
- Autoplay, `/search` and the new player controls work.
- `pytest` (currently 76 tests) and `ruff` stay clean throughout. Every bug fixed gets a test.

## 2. Constraints and decisions

From the owner:
- Work on `main`. A git tag `checkpoint/pre-overhaul` (at `b422747`) already exists. Each phase gets its own checkpoint tag before it starts. Commits use the Co-Authored-By line from the session attribution.
- Keep the cyan brand color and the persona rules: at most one emoji per embed, at most one short quip, everything through `EmbedFactory`, error embeds ephemeral.
- Restyle embeds only. No interactive player panel.
- Owner is open to re-architecting the music path. Adding a dependency or leaving a locked decision (in-process yt-dlp, no Lavalink) still needs the owner's approval each time. None is planned.
- Spotify stays metadata-only. Lyrics and audio filters are out of scope.
- Work is phased. After each phase: summarize, list anything unverified, wait for the owner's review (per `AGENTS.md`).

Observed problems (from the owner): slow `/play` starts, gaps between tracks, songs that stop mid-play with `/skip` then doing nothing.

Baseline observed in the repo: 76 tests pass, ruff clean, DAVE, FFmpeg and Deno detected, yt-dlp 2026.8.19, no playback errors in `logs/yokai.log`. The log also shows `/stats` still registered in Discord after removal from the code.

## 3. Music core (speed and reliability)

### 3.1 Fast resolution
- New `music/search.py`. Free-text search uses `ytmusicapi` first (about 0.3-0.5s), with yt-dlp `ytsearch` as fallback. 10-minute query cache. No new dependency.
- Reworked `music/resolvers/ytdlp.py`:
  - Warm yt-dlp instances, one per concurrency slot, reused across calls.
  - `/play` uses a single extraction pass: no search followed by a second full extraction.
  - Stream-URL expiry is read from the URL's `expire=` value, not a fixed 30 minutes.
  - Duplicate requests for one video share a single in-flight extraction, so prefetch and play never extract twice.
  - Player client and format options are chosen from harness measurements, not guessed.

### 3.2 Gapless transitions
- New `music/buffer.py`. While track N plays, the next 1-2 tracks download to `data/cache/`. FFmpeg reads the local file.
- Track N starts from the remote URL immediately and never waits for its own buffer.
- Cap about 500 MB with LRU eviction. The folder is wiped on startup and on `/stop`, and is gitignored.
- A missing or failed buffer falls back to the remote URL and never blocks playback.

### 3.3 Player rebuild (`music/player.py`)
- One playback loop per guild. Skip, stop, track-ended and enqueue arrive as events. `_play_lock` and the recursive `_after` -> `_on_track_end` -> `play_next` chain are removed.
- Every await has a timeout. A watchdog advances when a track stays LOADING over 20s, or when the state is PLAYING but the voice client has gone silent.
- Skip and stop cancel any in-flight load and cannot deadlock.
- Also fixed: skip advancing twice, `/stop` recording a false "completed" event, loop-track and loop-queue behavior (skip still advances in loop-track), and a pending Spotify match blocking the queue.

### 3.4 Measurement
- Structured per-stage timing logs: search, extract, buffer, FFmpeg start, track-to-track gap.
- `scripts/bench_music.py` live harness prints p50 and p95, run before and after each change.

Risk: YouTube extraction speed and the best client options change over time. They come from measurements and can be re-tuned.

## 4. Embed restyle (`theme.py`, `ui/embeds.py`, `ui/views.py`)

- Titles state the thing ("Queued", "Couldn't load that track", "Playback paused"). No generic "Error" or "Success". The single emoji sits in the title.
- The second emoji in error embeds is removed. "What to try" becomes a plain "Try" field.
- Now playing: title linked to the YouTube page, artist line, inline fields for Duration, Requested by and Source, a smoother progress bar with elapsed and total time, thumbnail.
- Queued: same field grid plus a time-until-it-plays estimate.
- Queue pages: a compact numbered list (`1. Title - Artist - 3:41`) under a header with total runtime, loop state and the current track. Removes the 25-field cap.
- Recommendations, playlist and Spotify imports share the list style. Import summaries show loaded counts and put skipped or unmatched tracks in one field.
- Error embeds have a fixed shape: plain explanation, "Try" field, quip on its own line.
- `/ping`, `/usage`, `/about`, `/diag`: aligned field grids, one consistent footer, color (not extra emoji) for status.
- Progress-bar glyphs, separators and spacing live in `theme.py` beside the colors.
- Tests: existing limit tests stay. New tests assert at most one emoji per builder and check hostile inputs.
- Before any rewrite, a preview script dumps each redesigned embed and the owner approves the look.

## 5. New features

Each is its own task with tests, and uses the same voice checks, `EmbedFactory` and command logging as existing commands.

- `/autoplay on|off`: default off, per server, not persisted. When the queue ends with it on, take YouTube Music radio for the last track and queue about 5, reusing the `Recommender` interface and the `/recommend` filters. Refill when low. Stops on `/stop`, on alone or idle timers, or after 3 failed refills, with one explanatory embed.
- `/search <query>`: up to 10 results in a select menu (title, artist, duration). Caller-only, 60s timeout, disables itself. Picking a result queues it, or plays it if idle.
- Player controls: `/seek` (`1:30`, `+30`, `-15`; restarts FFmpeg with `-ss` and corrects progress timing), `/previous` (from history), `/playnext <query>`, `/jump <position>`, `/join`, `/leave` (disconnects without clearing the queue).
- Command sync: clear stale registered commands such as `/stats`.

## 6. Phases

Each phase starts with a checkpoint tag and ends with a stop for owner review.

1. Baseline and harness: `scripts/bench_music.py`, timing logs, baseline numbers, a test that reproduces the "skip does nothing" bug.
2. Music core (section 3). Owner runs a short voice QA. Harness numbers are compared to baseline.
3. Embed restyle (section 4): preview script, look approval, rewrite.
4. Features (section 5).
5. Cleanup sweep: bug and polish pass across `/ping`, `/usage`, `/about`, `/diag`, presence, health, storage and config.

## 7. Docs, config, testing

- Update `SPEC.md`, `AGENTS.md`, `TASKS.md`, `QA.md` and `README.md` where decisions change (search path, buffer, autoplay, new commands).
- Document every new variable in `.env.example` (for example the buffer size cap). Add `data/cache/` to `.gitignore`.
- Default `pytest` stays network-free, with fakes for the searcher, resolver and buffer.
- Player stress tests cover skip and stop races, a stuck-load timeout and loop modes.
- Live checks go through the harness and are marked `live`.
- `ruff check` and `ruff format --check` clean, and the full suite run before a phase is called done.
