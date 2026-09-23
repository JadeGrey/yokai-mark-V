# Yokai (Mark V)

Yokai is a private, single-server Discord bot (~10 users) hosted locally on the owner's Windows PC. Its persona is a fan nod to Echo's Yokai drone from *Rainbow Six Siege*—confident, sharp, and focused on high-fidelity music streaming, smart queue management, and automated recommendations.

> *Unaffiliated fan project inspired by Echo's Yokai drone from Rainbow Six Siege. © Ubisoft.*

---

## Features

- **High-Fidelity Audio Streaming:** Powered by in-process `yt-dlp` and FFmpeg directly into Discord voice channels with DAVE end-to-end encryption (`davey`).
- **Universal Input Support:** Stream from YouTube links (`watch`, `youtu.be`, `shorts`, `music.youtube.com`), YouTube playlists, free-text search queries, and Spotify track, album, and playlist links.
- **Smart Spotify Matching:** Metadata-only matching using an official Spotify Web API client with automatic scraper fallback, matching tracks to clean YouTube native audio without streaming Spotify audio or polluting analytics.
- **Instant Prefetching & Streaming:** Prefetches direct stream URLs and metadata ahead of time for instantaneous track transitions.
- **Radio Recommendations (`/recommend`):** Intelligent YouTube Music radio discovery with multi-tier deduplication and a collaborative "Queue them all" interactive button.
- **Comprehensive Telemetry & Diagnostics:**
  - `/ping`: Real-time WebSocket Gateway, REST API, SQLite database, Voice, and audio Resolver rolling average latencies.
  - `/usage`: Usage leaderboard showing top operators and commands across customizable time windows (all-time, 30 days, 7 days).
  - `/about`: Detailed runtime stack versions, system uptime, and credits.
  - `/diag`: Owner-only ephemeral diagnostic dashboard with in-place **Update yt-dlp** button.
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

## Updating yt-dlp

YouTube frequently updates player algorithms and cipher signatures. Keeping `yt-dlp` up to date ensures uninterrupted streaming:

1. **In Discord (Owner Only):** Run `/diag` and click the **Update yt-dlp** button.
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
