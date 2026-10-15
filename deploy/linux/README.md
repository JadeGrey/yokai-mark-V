# Hosting Yokai on Linux (Raspberry Pi or a small PC)

This is the setup for running Yokai 24/7 on a Linux machine such as a Raspberry Pi 5 running
64-bit Raspberry Pi OS. Nothing here has been run on a real Pi yet; see
[What is still unverified](#what-is-still-unverified).

Files in this folder:

| File | What it does |
|---|---|
| `yokai.service` | Runs the bot and restarts it if it crashes (the Linux version of `run.ps1`). |
| `yokai-backup.service` / `.timer` | Backs up the database every night and keeps the newest 14. |

## 1. Hardware

- Raspberry Pi 4 or 5 with at least 4 GB RAM (2 GB is tight), the official power supply, a case
  with cooling, wired Ethernet, and a **USB SSD to boot from** (a microSD card wears out under
  SQLite writes). Any name-brand 128 to 256 GB USB 3 SSD is fast enough.
- In Raspberry Pi Imager choose **Raspberry Pi OS Lite (64-bit)**, set a hostname, a user and
  your SSH key in the settings dialog, and write it to the SSD.
- Optional: a small UPS that also powers your router.

## 2. Install the system packages

```bash
python3 --version        # needs 3.12 or newer
sudo apt update
sudo apt install -y ffmpeg git python3-venv
```

If `python3 --version` is older than 3.12 (Raspberry Pi OS "Bookworm" ships 3.11), use a newer
release of Raspberry Pi OS, or install Python 3.12+ with [uv](https://docs.astral.sh/uv/) and create
the virtual environment with it in step 4. Use a **64-bit** OS: the voice-encryption library
(`davey`) publishes 64-bit ARM Linux builds for Python 3.12 to 3.14.

Install `deno` (yt-dlp needs it). Either follow <https://docs.deno.com/runtime/getting_started/installation/>
or download the `deno-aarch64-unknown-linux-gnu.zip` release (`x86_64` on a PC) from
<https://github.com/denoland/deno/releases>, unzip it and move the `deno` file to
`/usr/local/bin/`. Check both tools:

```bash
ffmpeg -version | head -1
deno --version | head -1
```

## 3. Create the service user and fetch the code

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin yokai
sudo mkdir -p /opt/yokai && sudo chown yokai:yokai /opt/yokai
sudo -u yokai git clone https://github.com/JadeGrey/yokai-mark-V.git /opt/yokai
```

(If the repository is private, give the Pi a read-only deploy key or clone with a token.)

## 4. Install Yokai

```bash
cd /opt/yokai
sudo -u yokai python3 -m venv .venv
sudo -u yokai .venv/bin/pip install --upgrade pip
sudo -u yokai .venv/bin/pip install -e .
```

## 5. Configure

```bash
sudo -u yokai cp .env.example .env
sudo -u yokai nano .env          # DISCORD_TOKEN, GUILD_ID, OWNER_ID, ...
sudo chmod 600 /opt/yokai/.env
```

- Leave `FFMPEG_PATH` and `DENO_PATH` commented out: both are found on the PATH.
- Keep `DB_PATH`, `LOG_DIR`, `AUDIO_BUFFER_DIR` and `BACKUP_DIR` as relative paths (the service
  runs from `/opt/yokai`), or point them at a folder owned by `yokai`.
- Linux has its own timezone data, so named `RPG_TZ` values like `Europe/Oslo` work without
  installing `tzdata`.

## 6. Start it

```bash
sudo cp deploy/linux/yokai.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now yokai
journalctl -u yokai -f           # live log; Ctrl+C leaves it running
```

`systemctl restart yokai`, `stop` and `status` work as usual. Stopping sends Ctrl+C-style SIGINT so
the bot shuts down cleanly. The bot also keeps its own rotating logs in `/opt/yokai/logs/`.

## 7. Nightly backups

```bash
sudo cp deploy/linux/yokai-backup.service deploy/linux/yokai-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now yokai-backup.timer
sudo systemctl start yokai-backup.service     # make one now to test it
ls /opt/yokai/data/backups
```

Backups use SQLite's online backup and are integrity-checked, so they are safe while the bot is
running. **A backup on the same SSD does not survive a dead SSD.** Also copy them somewhere else:
see the comment at the top of `yokai-backup.service` to add `--copy-to` a USB drive or mounted
share, or sync `data/backups` to another machine with `rsync`.

## 8. The admin panel

The panel only listens on `127.0.0.1`. Reach it from your PC with an SSH tunnel:

```bash
ssh -L 8765:127.0.0.1:8765 you@your-pi        # leave this open
# then, in that SSH session:
cd /opt/yokai && sudo -u yokai .venv/bin/python -m yokai_admin
```

Open the single-use link it prints (it uses `127.0.0.1:8765`, which the tunnel forwards). Do not
expose the port to the internet.

## Moving from the Windows PC

Do this on a day with little activity. The bot must only ever run in one place at a time.

1. **On the PC:** stop the bot (Ctrl+C in the `run.ps1` window), then run
   `python scripts/backup_db.py` and note the file it makes in `data\backups`.
2. Copy that `yokai-YYYYMMDD-HHMMSS.db` to the Pi as `/opt/yokai/data/yokai.db`
   (create the `data` folder if needed, and `chown yokai:yokai` it). Do not copy `-wal` or `-shm`
   files.
3. Copy your `.env` to `/opt/yokai/.env`, then edit it: delete the Windows `FFMPEG_PATH` and
   `DENO_PATH` lines and any `C:\...` paths. If `YTDLP_COOKIES_FILE` is set, copy that file too,
   `chmod 600` it, and set the new path. (From a home connection you probably won't need it.)
4. Start the service (step 6). In Discord run `/admin diag` and check ffmpeg, deno, YouTube
   and the database all look healthy, then try `/play` with a few different videos.
5. Leave the PC's copy of `yokai.db` untouched for a week as a fallback. Do not run the PC bot
   again while the Pi is running.

Players keep their Renown, characters, gear and challenges, since everything lives in
`yokai.db`.

## Updating

```bash
cd /opt/yokai
sudo -u yokai git pull
sudo -u yokai .venv/bin/pip install -e .
sudo systemctl restart yokai
```

The **Update yt-dlp** button in `/admin diag` works too: it runs `pip` in the bot's own
virtual environment, which `yokai` owns.

## What is still unverified

- The service and timer files have not been loaded by a real systemd; run
  `systemd-analyze verify /etc/systemd/system/yokai.service` after copying them.
- The install and the full test suite pass on Linux in the GitHub Actions workflow
  (`.github/workflows/ci.yml`) on both x86-64 and 64-bit ARM (the Pi's architecture), with Python
  3.12 and 3.13, and `davey` loads there. Nothing has run on a real Raspberry Pi yet.
- Whether YouTube accepts requests from the Pi without cookies is only known by trying. Test with
  several `/play` requests on day one.
- Voice quality over your network and Discord's voice servers from the new location.
