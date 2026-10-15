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

An Adventurers' Guild RPG is being built in phases (see `docs/superpowers/specs/2026-10-14-guild-rpg-design.md`). **Phases 1 to 6** give you a character, the daily mission board, turn-based fights, gear, rank promotion, parties, raids and trading.

- `/guild join` creates your adventurer (rank E, level 1) and shows your starting stats.
- `/guild board` shows today's missions by rank with their rewards, your attempts left and your streak. It is private to you.
- `/guild mission <mission>` starts a fight against a mission from the board (the field autocompletes). The bot opens a thread for it and posts one live message with **Attack**, **Magic** and **Defend** buttons; you pick an action each round (45 seconds by default), actions resolve fastest first, then the enemies act. Win to earn XP and Renown; lose and you get nothing back for that attempt.
- `/guild mission <mission> [party]`: pass `party: true` to open a lobby in the channel instead of going alone (raids always open one). Up to four members press **Join** (rank and attempts are checked on the spot), the leader presses **Start**, everyone's attempts are charged at that moment and the bot opens one thread for the group. Everyone earns their own XP, Renown and drops, and enemies get tougher with party size. The lobby closes after three minutes; nothing is charged unless the leader starts it.
- **Raids** appear on the board from rank C upward (one per rank per day, marked `Raid:`). They are three-stage boss missions for a party of two to four (three or four recommended), cost 3 or 4 attempts and drop better gear.
- `/trade <member>` opens a public trade with another adventurer: gear for gear, gear for Renown, or a gift (one side offers nothing). Each side presses **Add item** (a private picker of your unequipped gear), **Set Renown** or **Remove item**, then both press **Confirm**. Changing an offer clears both confirmations, nothing happens unless both confirm, and the swap is all-or-nothing. Up to 6 items per side, equipped gear can't be offered, nobody can trade during a guild fight, and a trade that would push someone past the 60-item inventory is refused. A trade closes after 5 minutes; either side can cancel. There is no trade fee.
- `/guild wiki [section]` opens the guild guide, private to you: a table of contents plus sections on getting started, every command, combat, ranks, gear and set bonuses, loot odds and the shop, every mission and raid, the bestiary, parties, trading and an FAQ. Use the menu to jump, or Prev/Next/Contents. Numbers and lists are generated from the live content files and settings, so it stays correct when the game is tuned.
- `/guild rank` shows your rank and what the next one needs. When you have reached the level (D at 5, C at 12, B at 20, A at 30, SS at 45, SS+ at 60) it offers a **Challenge** button: a solo trial fight that costs 2 attempts, pays Renown (no XP, no gear drops) and promotes you if you win. A loss starts a cooldown (`RPG_PROMOTION_COOLDOWN_HOURS`, 6 by default) before you may try again. Missions of your new rank open as soon as you pass.
- `/guild leaderboard [sort] [page]` shows the guild standings, public, sortable by rank, level or battles won with a dropdown and Prev/Next buttons.
- `/card [member]` shows anyone's adventurer card: rank, level, record, the seven stats (HP, Attack, Magic, Defense, Resist, Speed, Crit) including your gear, what you wear in each slot, your set bonuses, and the level where the next promotion opens.
- `/gear inventory`, `/gear equip`, `/gear unequip`, `/gear repair`, `/gear sell`, `/gear shop` and `/gear buy` manage your equipment (all private to you; item fields autocomplete).

How it works:
- **Daily attempts.** Every mission costs attempts (1 for ranks E to C, 2 above). You get `RPG_BASE_ATTEMPTS` a day, plus a bonus for a streak of consecutive active days (+1 per 3 days, up to +3). Miss a day and the streak drops by 3 days, not to zero. Unused attempts do not carry over. If the bot restarts mid-fight, the attempts are refunded.
- **Ranks.** Missions are ranked E, D, C, B, A, SS and SS+. You can enter missions at your rank or below; lower ones pay less. Promotion fights arrive in a later phase, so every character is rank E for now.
- **The board** is the same for everyone, drawn fresh each guild day (midnight in `RPG_TZ`), three regular missions per rank.
- **Gear.** There are five slots (weapon, head, body, boots, accessory) and four rarities (common, uncommon, rare, epic). Every piece belongs to one of eight folklore **tags** (Oni, Kitsune, Tengu, Kappa, Yuki-onna, Tsukumogami, Jorogumo, Baku), carries rolled stats (about 10% around its base) and usually a small drawback, and has a level requirement. Wearing 2 matching pieces gives a set bonus; wearing 4 gives a bigger one and a **skill** you can use in fights (an epic weapon can add another). Mission wins drop gear (bosses drop more and better), and the guild shop sells three common or uncommon items per gear tier each day, up to `RPG_SHOP_DAILY_CAP` purchases per member per day.
- **Durability.** Every equipped item loses durability after a fight (3 points for a win, 8 for a loss, out of 100 or more). You get a warning when an item crosses 50%, 25% and 0%, and again before starting a mission if something is at 25% or below. At 0% an item counts for nothing (no stats, no set piece, no skill) until you repair it with `/gear repair`; a full repair costs about a third of the item's price, pro rata. You can't change gear during a fight, and an inventory holds 60 items (extra drops are sold automatically; `/gear sell` pays 30% of the price).
- **Away players.** If you do not act in time you defend automatically; after two missed rounds in a row the fight stops waiting for you, and you can rejoin by acting.

Game data (levels, ranks, tags, enemies, skills, missions, balance) lives in TOML files in `content/rpg/` and is validated at startup; a bad file stops the bot with a message naming the file and the problem. The numbers are calibrated with `python scripts/rpg_sim.py`, which plays the real fight engine as a daily player and prints how long each level and rank takes; `tests/test_rpg_pacing.py` fails if a change breaks the targets (rank A in about a month, the level cap in about three). Settings in `.env`:

| Variable | Default | Meaning |
|---|---|---|
| `RPG_ENABLED` | `true` | Master switch. When false the `/guild` and `/card` commands are not registered. |
| `RPG_CONTENT_DIR` | `content/rpg` | Folder with the RPG content files, relative to where the bot is started. |
| `RPG_TZ` | `UTC` | Timezone for the guild day (board, attempts and streak reset at its midnight). Named zones need the `tzdata` package on Windows. |
| `RPG_BASE_ATTEMPTS` | `8` | Mission attempts per member per day, before streak bonuses. |
| `RPG_TURN_SECONDS` | `45` | Seconds each player has to choose an action every round (minimum 10). |
| `RPG_SHOP_DAILY_CAP` | `3` | Items one member can buy from the guild shop per guild day. |

The owner and admins also get `/admin rpg grant`, `level`, `rank` and `reset` (recorded in the admin audit log). The 800 gear items are generated: change `src/yokai/rpg/itemgen.py` or `content/rpg/tags.toml` and run `python scripts/gen_rpg_items.py` (a test fails if `items.toml` is out of date).

Fights run in threads, so the bot needs permission to create public threads and send messages in them; `/admin diag` shows a "Guild RPG" line that reports whether it has them.

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
