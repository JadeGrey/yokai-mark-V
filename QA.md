# Yokai Mark V — Manual QA Checklist

This document details the 10 manual verification procedures to validate Yokai Mark V in Discord alongside automated unit tests.

---

## QA Execution Matrix

| # | Test Case | Target State / Expected Behavior | Verification Steps | Status |
|---|---|---|---|---|
| **1** | **Fresh Start & Idle Presence** | • Presence: `idle`<br>• Activity: `Watching for plant`<br>• No audio connections or active timers. | Start the bot (`.\run.ps1`). Check the bot's user profile in Discord member list. | `Pending Owner Run` |
| **2** | **VC Join & DAVE Audio** | • Bot joins voice channel.<br>• Discord DAVE E2EE voice handshake succeeds.<br>• Audio plays clearly without stutter.<br>• Presence updates to `online`. | 1. Join a voice channel.<br>2. Run `/play query: https://www.youtube.com/watch?v=dQw4w9WgXcQ`<br>3. Verify audio stream and bot presence is Online. | `Pending Owner Run` |
| **3** | **Input Resolution Matrix** | • Free-text search resolves top track.<br>• YouTube playlist queues up to cap with summary embed.<br>• Spotify track matches YouTube audio.<br>• Spotify album/playlist queues lazily as pending matches. | 1. `/play query: lofi hip hop`<br>2. `/play query: <youtube playlist url>`<br>3. `/play query: <spotify track url>`<br>4. `/play query: <spotify album or playlist url>` | `Pending Owner Run` |
| **4** | **Playback & Queue Controls** | • `/pause` halts audio; `/resume` continues at exact timestamp.<br>• `/skip` advances queue immediately.<br>• `/loop mode: track` loops current; `/loop mode: queue` cycles queue; `/loop mode: off` disables.<br>• `/shuffle` randomizes upcoming tracks.<br>• `/remove position: N` removes track.<br>• `/clear` clears queue.<br>• `/volume level: 50` updates volume. | Run each command in sequence while tracks are queued and active. Verify responses and queue state via `/queue`. | `Pending Owner Run` |
| **5** | **Recommendations (`/recommend`)** | • Returns 8 non-duplicate YouTube Music radio tracks in themed embed.<br>• "Queue them all" button enqueues all tracks.<br>• Button disables after press (single use).<br>• Concurrency lock prevents double-press. | 1. While a track is playing, run `/recommend`.<br>2. Click **Queue them all**.<br>3. Confirm tracks append to queue and button disables. | `Pending Owner Run` |
| **6** | **Empty Channel Auto-Disconnect** | • Bot detects when all human users leave the VC.<br>• Auto-disconnect timer triggers after 120s.<br>• Bot leaves VC; presence returns to `idle`. | 1. Join VC with bot playing.<br>2. Disconnect from VC, leaving bot alone.<br>3. Wait 120 seconds. Verify bot disconnects and presence becomes Idle. | `Pending Owner Run` |
| **7** | **Voice Kick & Move Handling** | • When kicked from VC, bot cleans up player state.<br>• Stop timers, clear queue, return presence to `idle`.<br>• When moved to another channel, player tracks move. | 1. Right-click bot in VC -> Disconnect (kick).<br>2. Verify bot logs disconnect and returns presence to Idle.<br>3. Confirm next `/play` works cleanly. | `Pending Owner Run` |
| **8** | **Network Disruption Recovery** | • If network drops briefly, FFmpeg reconnects (`-reconnect 1`).<br>• On permanent drop, player records event and skips or halts cleanly without crashing. | 1. During active playback, toggle internet connection off for 3 seconds, then back on.<br>2. Verify playback recovers or skips gracefully. | `Pending Owner Run` |
| **9** | **Utility & Diagnostics Suite** | • `/ping`: Displays Gateway, REST, Database, Voice, and Resolver latencies.<br>• `/usage [period]`: Shows top operators and commands with mentions.<br>• `/about`: Displays versions, uptime, OS, and Ubisoft disclaimer.<br>• `/diag`: Rejects non-owners with error embed; displays ephemeral telemetry + Update yt-dlp button for owner. | 1. Run `/ping`, `/usage period: 7d`, and `/about`.<br>2. Run `/diag` as the configured bot owner.<br>3. Have another user run `/diag` and verify refusal. | `Pending Owner Run` |
| **10** | **Process Supervision & Auto-Restart** | • Killing python process triggers `run.ps1` cooldown.<br>• Bot restarts after 5 seconds automatically. | 1. Launch Yokai using `.\run.ps1`.<br>2. In Task Manager or PowerShell, kill the `python.exe` process.<br>3. Verify `run.ps1` captures crash and restarts Yokai in 5 seconds. | `Pending Owner Run` |

---

## Automated Test Baseline

```powershell
python -m pytest -q
# Result: 76 passed in 1.70s

python -m ruff check .
# Result: All checks passed!

python -m ruff format --check .
# Result: 58 files already formatted
```
