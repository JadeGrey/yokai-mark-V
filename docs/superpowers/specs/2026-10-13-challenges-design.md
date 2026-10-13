# Daily and weekly challenges design

Date: 2026-10-13. Status: draft for owner review. Builds on the economy core
(`2026-09-29-economy-core-design.md`) and the gambling and table designs. Where they differ, this
document wins for challenges.

## 1. Goal

Give the ~10 people on the server a reason to use the music bot and the gamemodes. Each day and each
week the bot offers a small slate of challenges ("listen to 5 tracks in a call", "play 3 rounds").
Completing a challenge pays Renown into the existing wallet automatically.

Success criteria:
- Three daily and two to three weekly challenges are active at all times, drawn from a template pool.
- Progress is recorded from real music, game and voice activity without changing how those features behave.
- Each challenge pays exactly once per user per period, even under concurrent events or a crash.
- A bug in the challenge system can never break playback, a bet settlement or voice crediting.
- A full daily slate is worth about one `/daily`; a weekly slate about three to four.
- `pytest` and `ruff` clean; nothing hits the network.

## 2. Decisions (owner-approved 2026-10-13)

- **Reward:** Renown in the existing wallet (not cosmetic only). Tunable by `CHALLENGE_REWARD_SCALE`.
- **Slates:** a rotating random slate per period drawn from a template pool, one challenge per
  category (music, games, voice) for daily; owner can override the slate.
- **Music credit:** every human in the call with the bot when a track finishes, or is skipped after at
  least 50% listened, gets credit. Requires 2 or more humans in the channel (same rule as
  `VoiceEarnTracker`). Skipped-early tracks and solo listening do not count.
- **Game credit:** rounds played and rounds won, each with a minimum wager (`CHALLENGE_MIN_WAGER`,
  default 10). Net-profit goals are excluded.
- **Payout and UX:** auto-credit on completion, rate-limited batched notice embeds, and a
  `/challenges` command showing progress and time to reset.
- **Architecture:** event-driven. Existing code emits small typed events to a `ChallengeEngine`.
- **Reset boundary:** midnight in `CHALLENGE_TZ` (default `UTC`) for daily, Monday midnight for weekly.

## 3. Out of scope for v1

Streaks and badges, a challenge leaderboard, per-user opt-out, viewing another user's progress, and
Spotify-derived metrics. None require schema changes later beyond a new table.

## 4. Package layout

New package `src/yokai/challenges/`:

| Module | Responsibility |
|---|---|
| `models.py` | Dataclasses: `Template`, `Slate`, `ProgressRow`, events `TrackListened`, `RoundPlayed`, `VoiceMinutes`. |
| `templates.py` | The pool, as pure data. Id, period, category, metric, target, base reward, optional filters. |
| `periods.py` | Pure functions: current period key and boundaries for a timezone (`2026-10-13`, `2026-W42`). |
| `slate.py` | Pure function: `(period, period_key, pool) -> template ids`, seeded by the period key. |
| `engine.py` | `ChallengeEngine.record(event)`: match, advance, complete, pay. |
| `scheduler.py` | Background loop: period rollover, purge of old rows, notice flushing. |

New `cogs/challenges.py` (the `/challenges` command and view) and `ui` embed helpers in
`ui/embeds.py`. Every integration point is behind the engine; features depend on
`bot.challenges.record(event)` only.

## 5. Data model (migration v8)

```sql
CREATE TABLE challenge_slates (
  period TEXT NOT NULL,            -- 'daily' | 'weekly'
  period_key TEXT NOT NULL,        -- '2026-10-13' | '2026-W42'
  template_ids TEXT NOT NULL,      -- JSON list
  created_ts INTEGER NOT NULL,
  overridden INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (period, period_key)
);

CREATE TABLE challenge_progress (
  user_id INTEGER NOT NULL,
  period TEXT NOT NULL,
  period_key TEXT NOT NULL,
  template_id TEXT NOT NULL,
  progress INTEGER NOT NULL DEFAULT 0,
  seen TEXT,                       -- JSON list for distinct-count metrics, else NULL
  completed_ts INTEGER,
  rewarded INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (user_id, period, period_key, template_id)
);
```

The slate is stored when first created, so editing the pool mid-period never changes active
challenges. Rows older than 30 days are purged by the scheduler. Rewards are credited with
`credit_wallet(..., reason="challenge:<template_id>")`.

## 6. Templates and rewards

Reward budget: `/daily` pays a base of `ECONOMY_DAILY_BASE` (100) plus a streak multiplier; voice pays
about 60 per hour. A daily slate pays roughly 100 to 120 total across 3 challenges, a weekly slate
roughly 350. Rewards are `round(base_reward * CHALLENGE_REWARD_SCALE)`.

Starter pool (numbers are tunable in review):

| Period | Category | Templates |
|---|---|---|
| Daily | Music | listen to 3 / 5 / 8 tracks |
| Daily | Games | play 3 / 5 rounds (any game); win 1 round of one named game |
| Daily | Voice | 20 / 30 voice minutes |
| Weekly | Music | 25 tracks; 10 different artists |
| Weekly | Games | play 20 rounds; win 5 rounds; play 3 different games |
| Weekly | Voice | 180 voice minutes |

Metrics: `tracks_listened`, `rounds_played`, `rounds_won`, `voice_minutes`, and the distinct-count
metrics `distinct_artists`, `distinct_games` (progress is `len(seen)`). A template may filter by game.

## 7. Events and hooks

All hooks run after the host action has committed, and `ChallengeEngine.record` catches and logs its
own exceptions.

1. **Music.** Emit `TrackListened(user_ids, video_id, artist, ts)` from the track-end path in
   `music/player.py`, beside `_record_event`. Fire only for outcome `completed`, or `skipped` with
   `listened_s >= 50%` of `duration_s`. `user_ids` is the humans in the bot's voice channel at that
   moment, excluding bots, blacklisted users and the AFK channel; fewer than 2 humans emits nothing.
   Each user counts a given `video_id` once per period for `tracks_listened`.
2. **Games.** Emit `RoundPlayed(user_id, game, stake, payout)` from the settlement path every game
   uses (`Database.settle_bet`, covering slots, solo games, tables and poker). A win is
   `payout > stake`. Rounds with `stake < CHALLENGE_MIN_WAGER` are ignored. Implementation detail for
   the plan: whether `settle_bet` has the needed fields or needs a thin listener on `Database`.
3. **Voice.** `VoiceEarnTracker.tick` emits `VoiceMinutes(user_id, minutes)` for each human it credits,
   so voice challenges inherit its 2-human and AFK rules.

## 8. Payout and integrity

- Completion and payout happen inside one `_write_tx`: set `completed_ts` and `rewarded=1`, then
  credit the wallet. If the credit fails the transaction rolls back and the next event retries.
- The `rewarded` flag plus the primary key make payout exactly-once under concurrent `record` calls.
- Blacklisted users earn no progress (checked via `bot.moderation`).
- Users with no wallet get one created by the normal `credit_wallet` welcome-bonus path.

## 9. Scheduler and recovery

The scheduler computes the current period keys from `CHALLENGE_TZ` on every tick (every ~30 s). The
first tick after startup creates any missing slate, so a bot that was off at midnight recovers
without special handling. It also purges rows older than 30 days and flushes queued notices.

## 10. UI

- **`/challenges`** (guild-scoped, ephemeral, deferred): one embed through `ui/embeds.py` showing the
  selected period's challenges with a text progress bar, `current/target`, reward, a completion mark,
  and time until reset. A `View` with Daily and Weekly buttons edits the same message, times out after
  about 2 minutes, disables itself and acts only for the invoking user.
- **Completion notices:** the engine queues them; the scheduler merges them into one embed per channel
  every ~10 seconds, with mentions only inside the embed. Music completions go to the music text
  channel; game completions go to the channel of the game where available; voice completions get no
  notice and appear in `/challenges`. A failed send is logged and dropped; the reward is already paid.
  Persona rule: at most one short quip, clarity first.
- **Owner override:** `/admin challenges reroll|set|disable` (owner-only via `OWNER_ID`). Set or reroll
  marks the slate `overridden=1`; progress for retained templates is kept, for removed templates
  dropped. The plan will check whether the web admin panel (`yokai_admin`) is a better home.

## 11. Configuration (document all in `.env.example`)

| Variable | Default | Meaning |
|---|---|---|
| `CHALLENGE_ENABLED` | `true` | Master switch; when false, hooks are no-ops and `/challenges` says so. |
| `CHALLENGE_TZ` | `UTC` | IANA timezone for reset boundaries. |
| `CHALLENGE_MIN_WAGER` | `10` | Minimum stake for a round to count. |
| `CHALLENGE_REWARD_SCALE` | `1.0` | Multiplier on template base rewards. |

No new dependencies (`zoneinfo` is stdlib; on Windows this needs the `tzdata` package, which the plan
must verify is already installed or ask about, per the dependency rule).

## 12. Testing

- Pure unit tests: slate determinism, one challenge per category, no duplicates, period keys across
  timezone and DST edges, reward scaling.
- Engine tests against a temp SQLite database with fake events: progress, distinct-count dedupe,
  wager floor, blacklisted users, exactly-once payout under concurrent `record`, payout failure
  rolling back the completion mark.
- Hook tests with fakes for the player, the settle path and the voice tracker; an engine that raises
  must not break the host action.
- UI tests: embed contents, and the view disabling on timeout and ignoring other users.
- Migration test: v7 to v8 on an existing database.

## 13. Risks

- **Economy inflation.** Mitigated by the reward budget, the wager floor, once-per-period payout and
  `CHALLENGE_REWARD_SCALE`.
- **Hook coupling.** Mitigated by one-line emits behind a failure-isolated engine.
- **Windows timezone data.** `zoneinfo` needs `tzdata` on Windows; verify before relying on it.
- **Notice channel for games.** Not every game path has a channel handy; fall back to no notice.
