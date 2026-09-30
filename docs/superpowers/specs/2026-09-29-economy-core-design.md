# Economy — Core Wallet: Design Spec

Date: 2026-09-29. Status: approved in conversation, pending written-spec review.
Companion files: `AGENTS.md`, `SPEC.md`, `TASKS.md`. This is the first of three planned economy sub-projects (Core wallet → gambling minigames → investing/savings); the other two are out of scope here and get their own brainstorm and spec once Core ships. A daily/weekly/monthly "challenges" system was raised during brainstorming and deliberately deferred to its own future sub-project — see §8.

## 1. Goal and success criteria

Give the server a lightweight, fun, server-only currency ("Renown") that rewards using the bot (voice presence, playing music) without requiring any new external integration, privileged intent, or dependency. It has no purpose yet beyond itself — gambling and investing (later sub-projects) will spend it.

Success looks like:
- Every user has exactly one wallet, created lazily on first touch, with a one-time welcome bonus.
- `/balance`, `/daily`, `/pay`, `/leaderboard` work correctly and share the existing embed system and persona rules.
- Voice presence and music-command usage passively earn Renown without needing any new Discord intent.
- An owner-only `/economy-admin` can correct balances, fully audited.
- No possible path to a negative balance.
- `pytest` and `ruff` stay clean; every new code path has a test that doesn't touch the network (there is nothing to touch — this sub-project has no external integration).

## 2. Constraints and decisions (from the owner)

- Activity-driven, server-only fun currency, no interaction with the music module's actual playback (no pay-to-skip, no priority queue) — it just *earns* from music usage.
- Currency name: **Renown**, a nod to Rainbow Six Siege's free in-game currency (a name, not artwork/audio/quotes — consistent with `AGENTS.md`'s persona guardrails).
- Passive voice earning requires ≥2 non-bot humans in the channel, guarding against solo AFK farming.
- Daily claim: base amount × random 1.0–1.5× × a streak multiplier that grows to 3.0× by day 7 and holds there.
- `/pay` has no minimum or cooldown beyond having sufficient balance.
- New users get a one-time welcome bonus (~2× the daily base), not a zero start.
- An owner-only admin-adjust command is in scope for Core.
- Voice tracking uses a simple interval poll loop (not event-driven accrual) — stateless across restarts, easy to test, matches the codebase's preference for simple, boring mechanisms over precise-but-fragile ones.
- Music-command earning is a flat reward per eligible command, rate-limited, with **no** daily/weekly/monthly milestone in Core — that whole "challenges" idea is real but is its own sub-project (§8), not a Core detail.

## 3. Commands

All slash, guild-scoped, all output through `ui/embeds.py`/`theme.py` (no raw text sends), all logged through the existing `command_log` hook.

| Command | Args | Behavior |
|---|---|---|
| `/balance` | `user` (optional) | Shows the caller's balance, or another user's. Read-only; anyone can check anyone. Creates the wallet (with welcome bonus) on first touch if it doesn't exist. |
| `/daily` | none | Claims the daily reward. Ephemeral error with exact time remaining if claimed within the last 24h. |
| `/pay` | `user`, `amount` | Transfers Renown. Validated before defer (amount > 0 integer, not self, not a bot, sufficient balance) so failures stay ephemeral per `AGENTS.md`'s defer-visibility rule. |
| `/leaderboard` | `page` (optional) | Paginated, ranked by current balance, 10 per page, reusing the `QueuePaginator`/`BaseView` pattern. |
| `/economy-admin` | `user`, `amount` | Owner-only (checks `OWNER_ID`), ephemeral. Adds Renown (positive) or deducts (negative, floored at 0). Refused for non-owners exactly like `/diag`. |

## 4. Data model

New tables in the existing SQLite DB (WAL, migration via `PRAGMA user_version` bump, same as `SPEC.md` §10's existing tables):

```sql
CREATE TABLE wallets (
  user_id INTEGER PRIMARY KEY,
  balance INTEGER NOT NULL DEFAULT 0,
  last_daily_ts INTEGER,        -- unix seconds, NULL if never claimed
  daily_streak INTEGER NOT NULL DEFAULT 0,
  created_ts INTEGER NOT NULL
);

CREATE TABLE renown_transactions (
  id INTEGER PRIMARY KEY,
  ts INTEGER NOT NULL,
  user_id INTEGER NOT NULL,       -- account affected
  delta INTEGER NOT NULL,         -- positive or negative
  reason TEXT NOT NULL,           -- welcome | daily | voice | command | pay_sent | pay_received | admin
  counterparty_id INTEGER,        -- other user for pay_sent/pay_received; NULL otherwise
  balance_after INTEGER NOT NULL
);
```

Every balance change writes exactly one `renown_transactions` row, in the same transaction as the balance update. This is the audit trail `/economy-admin` needs and mirrors how `play_events` already gives the bot a debuggable history. No Spotify-derived or message-content data is ever involved, so this doesn't touch `AGENTS.md`'s data/legal guardrails.

## 5. Earning mechanics

### 5.1 Welcome bonus
Flat `ECONOMY_WELCOME_BONUS` (default 200 = 2× daily base), granted exactly once, automatically, the first time any code path touches a user's wallet and finds none (`/balance`, `/daily`, `/pay`, a voice tick, or a music-command reward). Recorded as one `welcome` transaction before the triggering action's own transaction.

### 5.2 Daily claim (`/daily`)
- `ECONOMY_DAILY_BASE` (default 100).
- Roll a random multiplier, uniform 1.0–1.5×.
- Streak multiplier: 1.0× on day 1, growing linearly to 3.0× at day 7, held at 3.0× for day 8 onward. Streak continues (+1 day) if the previous claim was 24–48h ago; resets to day 1 if ≥48h have passed since the last claim.
- Payout = `round(BASE * random(1.0, 1.5) * streak_mult)`.
- Reject (ephemeral) if <24h since the last claim, stating the exact time remaining.

### 5.3 Voice passive earn
- A background task (`discord.ext.tasks.loop`) ticks every `ECONOMY_VOICE_INTERVAL_SECONDS` (default 300 = 5 min).
- Each tick: for every voice channel in the guild, count non-bot members. If ≥2, credit `ECONOMY_VOICE_REWARD` (default 5) to each of them.
- No new intents needed — voice state is already visible via the existing (non-privileged) voice intent used by the music module.
- Stateless by design: a bot restart just resumes ticking; no in-progress accrual is ever lost or double-counted.

### 5.4 Music-command reward
- Hooks into the existing central `on_app_command_completion` handler that already writes `command_log` — no changes to individual command handlers.
- Applies to all music-module commands: `/play`, `/pause`, `/resume`, `/skip`, `/stop`, `/queue`, `/nowplaying`, `/remove`, `/clear`, `/shuffle`, `/loop`, `/volume`, `/recommend`.
- Flat `ECONOMY_COMMAND_REWARD` (default 5, ≈5% of daily base) per eligible call.
- Rate limit: at most `ECONOMY_COMMAND_RATE_LIMIT` (default 5) rewarded calls per rolling 60-second window per user, tracked in an in-memory sliding window (per-process, not persisted — acceptable for a single-process bot with ~10 trusted users; a restart simply clears the window). Calls beyond the limit still run normally; they just earn nothing.

## 6. Errors and UX

- Typed errors follow the existing `errors.py` pattern (e.g. a `DailyOnCooldownError`, `InsufficientFundsError`, `InvalidPaymentError`).
- `/pay` validates amount/target/balance before deferring, so rejections are ephemeral.
- `/economy-admin` by a non-owner is refused ephemerally, same as `/diag`.
- New embed builders in `ui/embeds.py`: `balance`, `daily_claim`, `pay_sent`, `leaderboard_page`, `economy_admin`. Leaderboard reuses the numbered-list + pagination look already established by `/queue` and `/recommend`.
- New quip-bank keys in `theme.py` (`daily_claim`, `pay_sent`, `insufficient_funds`, `daily_cooldown`, etc.), 3+ variants each, respecting the existing one-quip/one-emoji-per-embed rule. No changes to the persona voice rules themselves.
- Renown displays as a plain integer plus the word "Renown" — no currency symbol, no decimal places.

## 7. Configuration

New variables, all documented in `.env.example` alongside the existing ones:

| Variable | Default | Purpose |
|---|---|---|
| `ECONOMY_DAILY_BASE` | `100` | Base `/daily` payout before multipliers |
| `ECONOMY_WELCOME_BONUS` | `200` | One-time new-wallet bonus |
| `ECONOMY_VOICE_INTERVAL_SECONDS` | `300` | Voice-earn tick interval |
| `ECONOMY_VOICE_REWARD` | `5` | Renown per tick per qualifying human |
| `ECONOMY_COMMAND_REWARD` | `5` | Renown per eligible music command |
| `ECONOMY_COMMAND_RATE_LIMIT` | `5` | Max rewarded music commands per 60s per user |

No new runtime dependency, no new external service, no new Discord intent. `AGENTS.md` §"Ask before adding any dependency" doesn't apply here — Core needs nothing beyond what's already installed.

## 8. Explicitly out of scope for Core (deferred to future sub-projects)

- **Gambling minigames** (slots, blackjack, coinflip, dice, etc.) — next sub-project after Core, its own brainstorm and spec.
- **Investing and high-yield-savings-style accounts** — sub-project after gambling, its own brainstorm and spec.
- **Daily/weekly/monthly challenges with "smart management"** — raised mid-brainstorm as a way to reward reaching a music-command-usage threshold; scoped out because it is a system in its own right (challenge definitions, rotation, per-user progress tracking, reward payout, likely its own admin surface), not a one-line addition to the wallet. Core ships with only the flat per-command reward described in §5.4. This gets its own design pass once Core (and likely gambling/investing) exist to draw on.
- Any pay-to-skip / priority-queue interaction with the music player — the owner was explicit that music integration is one-directional (usage earns Renown; Renown doesn't affect playback).

## 9. Testing strategy

No network calls anywhere in this sub-project, so nothing needs a `live` marker. Unit tests:
- Wallet repository: create-on-first-touch grants welcome bonus exactly once; credit/debit always keep `balance_after` correct; debit is floored at 0 and refuses to go negative.
- Daily streak math, table-driven: on-time claim increments streak; claim within 24h is rejected; claim after ≥48h resets streak to 1; multiplier caps at 3.0× from day 7 onward.
- Voice tick logic with a fake guild/channel roster: 2+ non-bot humans credits everyone present; 1 human credits no one; bots are never credited or counted.
- Command-reward rate limiter: 5 calls in a window are rewarded, the 6th is not, and the window correctly expires after 60s.
- `/pay` validation: rejects self-pay, bot targets, non-positive/non-integer amounts, and insufficient balance; success path debits sender and credits recipient atomically with two transaction rows.
- `/leaderboard` ordering and pagination, including ties.
- `/economy-admin` owner check (refused for non-owners) and floor-at-zero on large deductions.

## 10. Risks

| Risk | Mitigation |
|---|---|
| Voice-tick task drifts or double-fires across a reconnect | Use `discord.ext.tasks.loop`'s built-in single-instance guarantee; test the tick function directly with fakes rather than relying on real timing |
| In-memory rate-limit window resets on restart | Accepted: restarts are infrequent and the server is small/trusted; not worth persisting for this scale |
| Renown becomes meaningless with nothing to spend it on until gambling/investing ship | Acceptable interim state; `/leaderboard` and `/pay` (gifting/bragging rights) already give it a purpose per the owner's "activity-driven, server-only fun currency" goal |
