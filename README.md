# Yokai (Mark V)

Yokai is a private, single-server Discord bot (~10 users) hosted locally on the owner's Windows PC. Its persona is a fan nod to Echo's Yokai drone from *Rainbow Six Siege*—confident, sharp, and focused on high-fidelity music streaming, smart queue management, and automated recommendations.

> *Unaffiliated fan project inspired by Echo's Yokai drone from Rainbow Six Siege. © Ubisoft.*

---

## Features

- **High-Fidelity Audio Streaming:** Powered by in-process `yt-dlp` and FFmpeg directly into Discord voice channels with DAVE end-to-end encryption (`davey`).
- **Universal Input Support:** Stream from YouTube links (`watch`, `youtu.be`, `shorts`, `music.youtube.com`), YouTube playlists, free-text search queries, and Spotify track, album, and playlist links.
- **Smart Spotify Matching:** Metadata-only matching using an official Spotify Web API client with automatic scraper fallback, matching tracks to clean YouTube native audio without streaming Spotify audio or polluting analytics.
- **Instant Prefetching & Streaming:** Prefetches direct stream URLs and metadata ahead of time for instantaneous track transitions.
- **Fast Search & Self-Healing Playback:** Fast search through YouTube Music, a local pre-buffer for gapless track changes, and self-healing playback (stuck loads time out, cut-off tracks resume).
- **Radio Recommendations (`/recommend`):** Intelligent YouTube Music radio discovery with multi-tier deduplication and a collaborative "Queue them all" interactive button.
- **Comprehensive Telemetry & Diagnostics:**
  - `/ping`: Real-time WebSocket Gateway, REST API, SQLite database, Voice, and audio Resolver rolling average latencies.
  - `/usage`: Usage leaderboard showing top operators and commands across customizable time windows (all-time, 30 days, 7 days).
  - `/about`: Detailed runtime stack versions, system uptime, and credits.
  - `/admin diag`: Admin-only ephemeral diagnostic dashboard with in-place **Update yt-dlp** button.
  - `/admin economy`: Admin-only Renown grant or deduction (was `/economy-admin`).
  - `/admin set_multiplayer game:<name>`: Admin-only. Makes the current channel the table channel for a multiplayer game and posts its persistent **Play** embed. Every game is listed and playable: Duel, Jackpot, Crash, Blackjack table, Poker table and the Lottery (whose channel gets a ticket panel instead of a Play embed).
  - `/admin unset_multiplayer`: Admin-only. Stops using the current channel as a table channel.
- **Multiplayer tables:** dedicated channels with an always-on Play button. A finished game stays on screen until the next table is requested. Games: Jackpot (a stake-weighted draw takes the pot), Crash (cash out before the multiplier crashes), Blackjack table (shared dealer, up to five players), Poker table (Texas hold'em for two to six; everyone buys in for the host's stake, play hands until the hand limit, and chips are paid back out in Renown when you leave or bust) and Duel (pick a number 1 to 10; the pick closest to a hidden number wins the pot, a tie refunds both). Everything the house keeps feeds a lottery pot.
- **Lottery:** a weekly draw in its own channel. Buy numbered tickets with a button (hand-picked numbers, quick picks, or both), one number from 1 to 20 is drawn, everyone holding it splits the pot, and an unclaimed pot rolls over and grows. Everything the house wins in any game feeds the pot.
- **Daily and weekly challenges:** three daily and three weekly challenges (one each for music, games and voice) pay Renown automatically. See [Challenges](#challenges).
- **Operational Hardening:** Auto-disconnects when idle or left alone in voice channels, graceful process supervision (`run.ps1`), and zero privileged intents.

---

## Windows Prerequisites

Ensure the following dependencies are installed and available on your system `PATH`:

1. **Python 3.12+** (tested with Python 3.14 on Windows)
2. **FFmpeg** (required for PCM audio transcoding):
   ```powershell
   winget install Gyan.FFmpeg
   ```
3. **Deno** (required by yt-dlp for JavaScript extraction and YouTube EJS):
   ```powershell
   winget install DenoLand.Deno
   ```
4. **Git**:
   ```powershell
   winget install Git.Git
   ```

*After installing external tools via winget, restart your PowerShell terminal to reload your system `PATH`.*

---

## Setup & Installation

1. **Clone the repository:**
   ```powershell
   git clone https://github.com/JadeGrey/yokai-mark-V.git
   Set-Location yokai-mark-V
   ```

2. **Create and activate a virtual environment:**
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

3. **Install dependencies:**
   ```powershell
   pip install -e ".[dev]"
   ```

---

## Configuration (`.env`)

Create your `.env` file from the provided example:
```powershell
Copy-Item .env.example .env
```

Edit `.env` with your preferred text editor:

```ini
# ==============================================================================
# REQUIRED CREDENTIALS
# ==============================================================================
DISCORD_TOKEN=your_bot_token_here
GUILD_ID=1193767905049981069
OWNER_ID=737486185466691585

# ==============================================================================
# BOT TUNING & LOGGING
# ==============================================================================
LOG_LEVEL=INFO
DB_PATH=data/yokai.db

# ==============================================================================
# SPOTIFY PROVIDERS (HYBRID METADATA RESOLUTION)
# ==============================================================================
# Optional: Official Spotify Web API credentials (recommended)
SPOTIFY_CLIENT_ID=
SPOTIFY_CLIENT_SECRET=
# Enables embed-based scraper fallback when official credentials are absent or rate-limited
SPOTIFY_SCRAPER_ENABLED=true

# ==============================================================================
# PLAYBACK & QUEUE LIMITS
# ==============================================================================
MAX_QUEUE_SIZE=200
MAX_PLAYLIST_TRACKS=100
MAX_TRACK_SECONDS=3600
IDLE_DISCONNECT_SECONDS=300
ALONE_DISCONNECT_SECONDS=120
RECOMMEND_COUNT=8
AUDIO_BUFFER_MB=500
AUDIO_BUFFER_DIR=data/cache

# ==============================================================================
# OPTIONAL PATH OVERRIDES & COOKIES
# ==============================================================================
# YTDLP_COOKIES_FILE=cookies.txt
# FFMPEG_PATH=C:\Program Files\ffmpeg\bin\ffmpeg.exe
# DENO_PATH=C:\Users\username\.deno\bin\deno.exe
```

### Discord Bot Portal Permissions
In the Discord Developer Portal under OAuth2 URL Generator:
- **Scopes:** `bot`, `applications.commands`
- **Bot Permissions:**
  - View Channels
  - Send Messages
  - Embed Links
  - Attach Files
  - Connect
  - Speak
  - Use Voice Activity

---

## Running Yokai

### Recommended: Continuous Supervisor Runner
Run Yokai using the PowerShell supervisor script, which automatically activates the virtual environment and restarts the bot if an unexpected crash occurs:
```powershell
.\run.ps1
```

### Direct Run
Alternatively, launch Yokai directly from an activated virtual environment:
```powershell
python -m yokai
```

> ⚠️ **Important Hosting Note:**
> Yokai runs locally on your PC. It will be **offline** whenever your computer is asleep, hibernating, or powered off. To ensure 24/7 availability, adjust your Windows Power & Sleep settings to prevent the PC from sleeping while plugged in.

---

## Admin panel

A local web panel for the owner (bot status, people and their wallets with ledger-safe balance edits, the blacklist and admin list, an audit log, a database browser with backups, restore and VACUUM today; and a log viewer with a live tail). It runs as its own process, listens on `127.0.0.1` only, and works even while the bot is off.

```powershell
.\run-admin.ps1        # activates the venv and starts the panel
python -m yokai_admin   # the same thing, by hand
```

It opens your browser through a **single-use link** (also printed in the console). If you close the tab, restart the panel to get a new link. Settings in `.env`: `ADMIN_PORT` (default `8765`), `DB_PATH`, `LOG_DIR`, `BACKUP_DIR` (default: a `backups` folder beside the database). The panel never needs the Discord token. Set `OWNER_ID` in `.env` to enable editing the blacklist and admin list (the owner can never be added to either); without it the pages are read-only. Run the bot once after updating so it can upgrade the database.

## Challenges

Every day and every week Yokai picks a small slate of challenges (three daily, three weekly: one music, one games, one voice) from a template pool, for example "Listen to 5 tracks in a call" or "Play 20 gambling rounds". Finishing one pays Renown straight into your wallet; a full daily slate is worth roughly one `/daily`, a weekly slate about three to four. Run `/challenges` to see your progress and the time until the next reset (the Daily and Weekly buttons switch tabs; they work for you only and expire after about two minutes).

Rules worth knowing:
- **Music** counts for everyone in the voice channel when a track finishes, or is skipped after at least half of it has played. It needs two or more humans in the call (the AFK channel never counts), unmatched Spotify placeholder tracks never count, and each distinct track counts once per person per period, so looping one song does not farm progress.
- **Games** count when a round settles, only if the stake is at least `CHALLENGE_MIN_WAGER` (default 10). A win means the payout beat the stake. Poker counts as one round when you cash out. Lottery tickets do not count as rounds. Game completions have no channel notice; check `/challenges`.
- **Voice** minutes accumulate on each voice-earn tick while you are in a call with another human.
- **Rewards pay automatically**, once per challenge per period, and are added to your wallet (`/balance` shows the new total; the ledger entry appears in the admin panel as "Challenge (template id)"). Music completions post one batched notice in the music text channel.

Admins (the owner and the adminlist) get `/admin challenges period:<daily|weekly> action:<list|reroll|set>`; `set` takes comma-separated template ids (use `list` to see them). Settings in `.env`:

| Variable | Default | Meaning |
|---|---|---|
| `CHALLENGE_ENABLED` | `true` | Master switch. When false nothing advances and `/challenges` says they are off. |
| `CHALLENGE_TZ` | `UTC` | Timezone for resets (midnight daily, Monday midnight weekly). `UTC` needs nothing extra; named zones such as `America/New_York` need the `tzdata` package on Windows, which is not installed by default (`pip install tzdata` to enable). |
| `CHALLENGE_MIN_WAGER` | `10` | Minimum stake for a game round to count. |
| `CHALLENGE_REWARD_SCALE` | `1.0` | Multiplier on every challenge reward (0.1 to 10). |

## Guild RPG (in progress)

An Adventurers' Guild RPG is being built in phases. **Phase 1 (foundation)** adds the character and the player card; missions, gear, ranks, parties and trading arrive in later phases (see `docs/superpowers/specs/2026-10-14-guild-rpg-design.md`).

- `/guild join` creates your adventurer (rank E, level 1) and shows your starting stats.
- `/card [member]` shows anyone's adventurer card: rank, level, record, the seven stats (HP, Attack, Magic, Defense, Resist, Speed, Crit) and the level where the next promotion opens.

Game data (levels, ranks, gear tags) lives in TOML files in `content/rpg/` and is validated at startup; a bad file stops the bot with a message naming the file and the problem. The numbers in those files are placeholders that later phases tune. Settings in `.env`:

| Variable | Default | Meaning |
|---|---|---|
| `RPG_ENABLED` | `true` | Master switch. When false the `/guild` and `/card` commands are not registered. |
| `RPG_CONTENT_DIR` | `content/rpg` | Folder with the RPG content files, relative to where the bot is started. |

## Updating yt-dlp

YouTube frequently updates player algorithms and cipher signatures. Keeping `yt-dlp` up to date ensures uninterrupted streaming:

1. **In Discord (Admins only):** Run `/admin diag` and click the **Update yt-dlp** button.
2. **Via PowerShell CLI:**
   ```powershell
   .\.venv\Scripts\Activate.ps1
   pip install -U "yt-dlp[default]"
   ```
*After updating yt-dlp, restart Yokai to load the new package into memory.*

---

## Testing & Code Quality

Run automated test suites and linters:

```powershell
# Run unit and integration tests (default excludes network calls)
python -m pytest -q

# Run live network tests (optional, hits YouTube & Spotify)
python -m pytest -q -m live

# Run Ruff linter and formatting checks
python -m ruff check .
python -m ruff format --check .
```
