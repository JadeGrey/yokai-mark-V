# AGENTS.md — Yokai

Yokai is a private, single-server Discord bot (~10 users, runs on the owner's Windows PC). Its name and persona are a fan nod to Echo's Yokai drone from Rainbow Six Siege. Purpose: entertainment and quality of life. First module: music.

Read first: `SPEC.md` (what to build and why) and `TASKS.md` (ordered phases with acceptance criteria). This file is the standing rulebook.

## Locked decisions
Do not change these without asking the owner.
- Python 3.12+ and `discord.py>=2.7.1` with voice extras plus `davey`. Discord has required DAVE end-to-end encryption for voice since March 2026; a bot without DAVE cannot join calls.
- Playback: `yt-dlp` used in-process (Python API) plus FFmpeg. All audio comes from YouTube. No Lavalink. No Spotify streaming.
- Spotify links (track, album, playlist) are metadata only: official API first, scraper fallback, then matched to YouTube audio.
- Recommendations: YouTube Music radio via `ytmusicapi` only, behind a `Recommender` interface.
- Storage: SQLite via `aiosqlite`. Single guild; slash commands synced to that guild.
- Presence: `Watching for plant`. Idle when not in a voice channel, online while connected to one.

## Working agreement
1. Work one phase of `TASKS.md` at a time. Begin each phase with a short plan (files to touch, tests to add), then implement.
2. At the end of each phase, stop. Summarize what changed, list anything unverified, and wait for the owner's review.
3. Commit per task with a clear message. Never commit `.env`, tokens, databases, or cookie files.
4. discord.py, yt-dlp, ytmusicapi, the Spotify API, and the Spotify scraper change often. Check current docs or changelogs before using an API and never guess signatures or field names. If reality differs from `SPEC.md`, say so and propose a fix instead of silently diverging.
5. Ask before adding any dependency not listed in `SPEC.md` §11.
6. Never mark a task done without running the verification commands below.

## Commands (PowerShell, from repo root)
- Setup: `python -m venv .venv; .\.venv\Scripts\Activate.ps1; pip install -e ".[dev]"`
- Run: `python -m yokai`
- Tests: `python -m pytest -q` (tests that hit the network are marked `live` and skipped by default; run `-m live` only when asked)
- Lint and format check: `python -m ruff check .` and `python -m ruff format --check .`
- External tools that must be on PATH (checked at startup): `ffmpeg`, `deno`

## Code conventions
- `src/yokai/` layout, fully type-hinted, `from __future__ import annotations`, small modules, dataclasses for models.
- Never block the event loop. yt-dlp, ytmusicapi, and the Spotify scraper are synchronous: call them through `asyncio.to_thread` with a timeout and a concurrency limit.
- Use `logging` (rotating file plus console). No `print`. No bare `except`: catch specific errors and map them to the typed errors in `yokai/errors.py`.
- Every external integration sits behind an interface (`Resolver`, `SpotifyProvider`, `Recommender`) so it can be swapped or faked in tests.
- Configuration comes from environment variables loaded in `config.py`. Document every variable in `.env.example`.

## Discord rules
- Slash commands only (`app_commands`), guild-scoped. No message-content or other privileged intents.
- Defer within 3 seconds for anything that touches the network, then reply with followups.
- Every user-visible message goes through the universal embed system (`ui/embeds.py`). No raw `send_message("text")` or `channel.send("text")` in feature code. Error embeds are ephemeral.
- Default `allowed_mentions` to none. Mention users only inside embeds.
- Buttons and views must time out, disable themselves, and only act for users allowed to use them.

## Security rules
- Accept links only from `youtube.com`, `youtu.be`, `music.youtube.com`, `open.spotify.com`, and `spotify.link` (redirects must land on `open.spotify.com`). Anything else gets a friendly error. Never hand arbitrary URLs to yt-dlp.
- Free-text queries become `ytsearch` queries, never shell strings. Never use `shell=True`. Limit query length.
- Never log tokens, cookies, or the full environment. Owner-only commands check `OWNER_ID`.

## Persona (short version; full guide in `SPEC.md` §3)
Yokai is confident and a little cocky, never mean, never over the top. At most one short quip per message. Clarity comes first: error and troubleshooting text stays plain and useful, with any quip kept separate. No Ubisoft artwork, audio, or quotes anywhere in the repo; the owner sets the avatar in the Developer Portal. `/about` carries an "unaffiliated fan project" line.

## Data and legal guardrails
- Do not use Spotify-derived data (IDs, metadata, audio features) to train or feed any ML model. Analytics tables store YouTube-derived fields only.
- Do not add other audio platforms, downloaders, or cookie tooling beyond what `SPEC.md` describes.

## Definition of done (every task)
Code and tests written; `pytest` and `ruff` clean; acceptance criteria in `TASKS.md` met, or explicitly flagged as needing manual verification; docs and `.env.example` updated.
