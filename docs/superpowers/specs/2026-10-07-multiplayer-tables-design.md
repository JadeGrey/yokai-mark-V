# Multiplayer Tables and Lottery: Design Spec

Date: 2026-10-07. Status: sections approved in conversation; pending written-spec review.
Companion files: `AGENTS.md`, `SPEC.md`, `TASKS.md`, `docs/superpowers/specs/2026-09-30-gambling-design.md` (G1 shipped; its §6 multiplayer design is superseded by this document), `docs/superpowers/specs/2026-09-29-economy-core-design.md`.

This replaces the G2/G3 plan in the gambling spec. Multiplayer games no longer have their own slash commands; they run as **tables** in dedicated channels. Poker (G4) is out of scope here and will reuse the table framework. Building is split into four phases, each with its own plan, a stop and an owner review (`AGENTS.md` working agreement 2).

## 1. Goal and success criteria

Give members multiplayer casino games played in dedicated channels, plus a weekly lottery fed by the house's winnings, with no way to create or lose Renown outside the audited wallet.

- Admins dedicate a channel to a game with `/admin set_multiplayer game:<name>`. The channel shows a persistent embed with a **Play** button that works 24/7, across restarts.
- Pressing Play opens a table. The finished table stays on screen until the next table is requested, and only then is it cleared.
- Games: duel, jackpot, crash, blackjack table (poker later).
- Every stake is escrowed; every settlement is atomic across the whole table (§5). A bot crash can never pay a pot twice.
- Everything the house keeps from any game feeds a lottery pot. Members buy numbered tickets; the pot rolls over until someone matches the weekly draw.
- All command groups are tidy: admin commands live under `/admin`, owner-only commands under `/owner`.
- All game logic lives in pure, seedable engines. `pytest` and `ruff` clean. No new dependency, no new Discord intent.

## 2. Decisions (from the owner)

- One table at a time per channel, shown as messages in the channel (no threads). The bot deletes only its own messages.
- Pressing Play while a table is `OPEN` or `RUNNING` is refused. The host or an admin can cancel an unstarted (`OPEN`) table, which refunds everyone. A running table cannot be replaced.
- Each player chooses their own stake within min/max. The exception is duel, where the joiner must match the host's stake.
- A table auto-starts when its join window ends, or at once when it is full; the host may press Start early once the minimum is met. A table that ends its window below the minimum cancels and refunds.
- Existing admin commands move into `/admin` (breaking rename). `/owner` is created empty for now.
- Lottery: pick-a-number, one weekly draw, the pot rolls over, ties split the pot. Range 1 to 20.
- Duel is a pick-a-number game (1 to 10, closer to a hidden target wins, a tie refunds both).
- Out of scope: poker, threads, other duel variants, tiered lottery prizes, admin-panel support for the lottery, any real-money feature.

## 3. Phasing

| Phase | Contents |
|---|---|
| **M1** | `/admin` and `/owner` groups; move `/economy-admin` and `/diag` |
| **M2** | Migration v6, channel storage, `set_multiplayer`, persistent Play embed, lobby framework, atomic `settle_lobby`, lottery pot accounting, duel |
| **M3** | Jackpot, crash, blackjack table |
| **M4** | Migration v7, lottery tickets, weekly draw, lottery channel |

## 4. Command groups and channel storage (M1, M2)

`cogs/admin.py` holds the `/admin` group. Subcommands:

- `economy` (moved from `/economy-admin`)
- `diag` (moved from `/diag`)
- `set_multiplayer game:<choice>` (choices: duel, jackpot, crash, bjtable; `lottery` joins in M4)
- `unset_multiplayer`

The `/owner` group class exists but is registered only once it has a subcommand (Discord rejects empty groups). The yt-dlp update stays inside `/admin diag`.

Every `/admin` subcommand runs the same `moderation.is_admin` check as today, with the same plain ephemeral "Admins only" error. Owner commands will check `config.owner_id`. The old `/economy-admin` and `/diag` are removed; tests, `README.md`, `QA.md`, `SPEC.md` and `TASKS.md` are updated.

### Migration v6

```sql
CREATE TABLE multiplayer_channels (
  channel_id INTEGER PRIMARY KEY,
  game TEXT NOT NULL UNIQUE,      -- duel|jackpot|crash|bjtable|lottery
  play_message_id INTEGER,        -- the persistent Play embed
  table_message_id INTEGER,       -- current or last table message (NULL for lottery)
  table_active INTEGER NOT NULL DEFAULT 0,  -- 1 while a table is open or running
  updated_ts INTEGER NOT NULL
);
CREATE TABLE lottery_state (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  pot INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO lottery_state (id, pot) VALUES (1, 0);
CREATE TABLE lottery_pot_entries (
  id INTEGER PRIMARY KEY,
  ts INTEGER NOT NULL,
  source TEXT NOT NULL,           -- e.g. slots, duel, jackpot, ticket
  amount INTEGER NOT NULL
);
```

`game` is UNIQUE, so one channel per game. Running `set_multiplayer` again for a game moves it (the old Play embed is deleted and a new one posted); running it in a channel already dedicated to another game re-points that channel. Before saving, the command verifies the bot can view, send and embed in the channel and replies with a plain, specific error if not.

## 5. Tables and lobbies (M2)

### Persistent Play embed

One `PlayView(timeout=None)` with a fixed `custom_id` (`yokai:table:play`) is registered in `setup_hook`. Its handler finds the game from `interaction.channel_id`. On startup the bot reconciles every configured channel: a missing Play embed is reposted and `play_message_id` updated; a table message whose channel is still marked `table_active` is edited to say the table was closed by the restart (its stakes are already refunded by the startup sweep); a finished table is never rewritten, so its result stays on screen.

### Pressing Play

1. Checks, in order: blacklist screen, channel configured, no `OPEN` or `RUNNING` table, caller not in another game, balance.
2. A stake modal opens, validated against min/max.
3. On submit, under a per-channel lock (two simultaneous presses cannot both win): delete the previous table message, escrow the host's stake, post the lobby message, save `table_message_id`.
4. The lobby has Join, Leave, Start (host, once the minimum is met) and Cancel (host or an admin, only while `OPEN`). Non-participants may press Join only.
5. When the game ends, the final embed stays until the next successful Play removes it.

### Lobby framework

- States: `OPEN -> RUNNING -> SETTLED | CANCELLED`. One `asyncio.Lock` per lobby serialises join, leave, start, cancel and settle.
- State is in memory only. A restart cancels every lobby; the existing startup sweep refunds the open bets.
- A joining player claims the one-active-game slot (`ActiveGames`) and releases it when the lobby ends.
- Views are `BaseView` subclasses; the persistent Play view also runs the blacklist screen.

### Atomic settlement

`Database.settle_lobby(payouts: dict[int, int]) -> dict[int, int]` settles every bet of a lobby in one transaction under `_write_lock`: each bet marked `settled` with its payout, wallets credited, the pot credited (§6), all or nothing. If any bet is not `open`, the whole call raises and changes nothing. Cancelling a lobby refunds each open bet individually, which is safe because a refund only returns a stake. This closes the gap in per-bet settlement, where a crash between settling a winner and the losers would let the startup sweep refund losing stakes after the pot was paid.

### Duel (M2)

Two seats, a pick-a-number game. The host's Play sets the stake; one **Join** click (no modal) fills the second seat at the same stake and moves the table to `RUNNING` at once.

- The server draws a hidden target from 1 to 10 (`SystemRandom`) when the seats fill.
- Each player picks a number from 1 to 10 through an ephemeral prompt (a **Pick your number** button on the table message opens it), so the opponent never sees the pick. A player who has not picked within `GAMBLING_TURN_SECONDS` (45) gets a random number.
- When both picks are in, the table reveals the target and both picks together. The pick closer to the target wins and takes the pot minus `GAMBLING_DUEL_RAKE_PERCENT` (2%, rounded down), settled with `settle_lobby`.
- **Tie** (equal distance, including both players picking the same number): both stakes are refunded in full, no rake and no pot credit.
- The engine is pure: `resolve(pick_a, pick_b, target)` returns the winner or a tie. Central picks are favoured against a uniform target, so the game has a small skill element; it is symmetric, so neither seat has an edge.

## 6. Lottery pot accounting (M2)

Every settlement credits the pot in the same transaction, so a crash cannot pay a win without crediting the pot.

- **House-banked games** (slots, roulette, blackjack, crash, blackjack table): per bet, `house_take = max(0, stake - payout)`. Wins still mint Renown as today.
- **Pooled games** (duel, jackpot, later poker): only the **rake** is credited (`sum(stakes) - sum(payouts)` for the lobby), because losing stakes already flow to the winner. Crediting the full losing stake as well would count it twice.
- Refunds credit nothing. Ticket purchases (M4) credit the full price.
- Each credit writes a `lottery_pot_entries` row (audit trail); `lottery_state.pot` is the running total. History before this change is not back-filled; the pot starts at 0.

## 7. Other table games (M3)

### Jackpot
2 to 10 players, each with their own stake, 60 s window (`GAMBLING_LOBBY_JOIN_SECONDS`), starts at once when full. One stake-weighted draw using a pure `draw_winner(entries, rng)`. The winner takes the pot minus `GAMBLING_JACKPOT_RAKE_PERCENT` (default 2), rounded down. A single entrant at window end cancels and refunds.

### Crash
1 to 10 players (solo against the house is allowed), 30 s window. The crash point is drawn once and never recomputed: `max(1.0, 0.97 / U)` with `U` uniform in (0, 1], capped at `GAMBLING_CRASH_MAX_MULTIPLIER` (default 100). One message is edited about once per second. **Cash out** locks in the server's multiplier at the moment of the press, not the displayed one, and replies ephemerally; a press after the crash loses. The engine advances on a tick function taking the current time, so tests use a fake clock and never sleep. Everyone is settled in one `settle_lobby` call when the round ends. Current discord.py and Discord edit rate limits are verified in the M3 plan (`AGENTS.md` rule 4); the loop slows its tick when an edit fails.

### Blackjack table
2 to 5 players, 30 s window, one shared dealer. Rules match solo blackjack (6-deck shoe, 3:2, dealer stands on all 17s, no insurance, no splits) except no double. Players act in join order; only the active player's Hit and Stand work (others get an ephemeral "not your turn"). 45 s per turn (`GAMBLING_TURN_SECONDS`), auto-stand on timeout. One message shows every hand and is edited after each action. All hands settle together in one `settle_lobby` call at the end.

## 8. Lottery (M4)

### Mechanics
- Numbers 1 to `LOTTERY_NUMBER_RANGE` (default 20). A ticket carries one number and costs `LOTTERY_TICKET_PRICE` (default 100).
- A member cannot hold the same number twice in a draw and may hold at most `LOTTERY_MAX_TICKETS_PER_USER` (default 10) tickets per draw.
- One draw per `LOTTERY_DRAW_INTERVAL_HOURS` (default 168). One winning number is drawn with `SystemRandom`. Everyone holding it splits the pot (rounded down; the remainder stays in the pot). If nobody holds it, the pot rolls over.
- Odds: with T distinct tickets sold the pot is claimed with probability `1 - (1 - 1/N)^T`. At N=20 that is about 40% for 10 tickets and 64% for 20 tickets. Raising `LOTTERY_NUMBER_RANGE` lowers the hit rate. The prize is always the pot, so the lottery cannot owe more than it holds.

### Channel
`/admin set_multiplayer game:lottery` posts a persistent embed (pot, tickets sold, next draw time) with a **Buy tickets** button. The button opens a modal with optional "Numbers (e.g. 3, 7, 19)" and "Quick picks (how many)"; numbers not chosen by hand are filled by random quick picks up to the cap. The embed is edited in place. Winner announcements are new messages in the same channel and are never deleted.

### Purchase and draw
A ticket purchase is a direct debit, not a bet: wallet down, pot up and `lottery_tickets` rows inserted in one transaction, all or nothing, reason `ticket:lottery`. (It cannot use `place_bet`, because the startup sweep would refund a week-old open bet.) Purchases after the draw time are refused.

The draw is one transaction under `_write_lock`: record the winning number in `lottery_draws`, credit each winner (reason `win:lottery`), subtract the prize from the pot. A scheduler task checks every 60 s whether `next_draw_ts` has passed; if the bot was offline at draw time it draws on startup, then moves `next_draw_ts` forward by whole intervals past now. The announcement is sent after the commit; `lottery_draws.announced` marks it, and an unannounced draw is retried.

### Migration v7
```sql
CREATE TABLE lottery_tickets (
  draw_no INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  number INTEGER NOT NULL,
  ts INTEGER NOT NULL,
  PRIMARY KEY (draw_no, user_id, number)
);
CREATE TABLE lottery_draws (
  draw_no INTEGER PRIMARY KEY,
  ts INTEGER NOT NULL,
  winning_number INTEGER NOT NULL,
  pot INTEGER NOT NULL,
  winners TEXT NOT NULL,         -- JSON list of user ids
  prize_each INTEGER NOT NULL,
  announced INTEGER NOT NULL DEFAULT 0
);
ALTER TABLE lottery_state ADD COLUMN draw_no INTEGER NOT NULL DEFAULT 1;
ALTER TABLE lottery_state ADD COLUMN next_draw_ts INTEGER;
```

## 9. Configuration

All in `config.py`, documented in `.env.example`.

| Variable | Default | Purpose |
|---|---|---|
| `GAMBLING_DUEL_RAKE_PERCENT` | `2` | Rake on duel pots (0 disables) |
| `GAMBLING_JACKPOT_RAKE_PERCENT` | `2` | Rake on jackpot pots (0 disables) |
| `GAMBLING_LOBBY_JOIN_SECONDS` | `60` | Join window (crash and blackjack table use half) |
| `GAMBLING_TURN_SECONDS` | `45` | Per-turn timer for table games |
| `GAMBLING_CRASH_MAX_MULTIPLIER` | `100` | Crash point cap |
| `LOTTERY_TICKET_PRICE` | `100` | Price of one ticket |
| `LOTTERY_NUMBER_RANGE` | `20` | Numbers run from 1 to this |
| `LOTTERY_MAX_TICKETS_PER_USER` | `10` | Per member per draw |
| `LOTTERY_DRAW_INTERVAL_HOURS` | `168` | Time between draws |

`GAMBLING_MIN_BET` and `GAMBLING_MAX_BET` already exist and apply to table stakes. Per-game odds are constants in the engines.

## 10. Errors and UX

- New typed errors in `errors.py`: `TableBusyError`, `ChannelNotConfiguredError`, `LobbyClosedError`, `NotYourTurnError`, `LotteryClosedError`, `TicketLimitError`. Error text is plain and ephemeral.
- New `EmbedFactory` builders: Play embed, lobby, each game's running and result states, closed-by-restart notice, lottery panel, draw announcement. Each embed has at most one emoji and one quip; cards use text notation. New quip keys in `theme.py` (3+ variants each).
- No raw `send_message` or `channel.send` in feature code (the lottery announcement and the lobby messages go through the embed system). `allowed_mentions` none; winners are mentioned only inside embeds.

## 11. Testing

No network anywhere; nothing needs the `live` marker.

- **Settlement:** `settle_lobby` is all-or-nothing (a failure partway leaves every bet open and every wallet unchanged); no sequence of settle, refund and sweep pays one pot twice; concurrent settles cannot double-pay.
- **Pot accounting:** house-banked take, pooled-game rake only, refunds credit nothing, pot credit and settlement commit together, entries sum to the running pot.
- **Engines (seeded):** duel winner by distance, every tie case (same pick, equidistant picks) refunds both with no rake, rake rounding, hidden picks never shown before the reveal, timeout assigns a random pick, a non-participant cannot pick; jackpot weighted-draw probabilities and single-entrant refund; crash distribution, cap, server-authoritative cash-out, post-crash press loses (fake clock); blackjack table turn order, timeout auto-stand, per-player payout.
- **Lobby:** join, leave, start and cancel races under the lock; auto-cancel refunds; one-active-game rule; host/admin-only cancel; no cancel once `RUNNING`.
- **Play flow:** busy-table refusal, two simultaneous presses, previous-table clearing, startup reconcile of Play embed and stale tables, persistent view `custom_id` registered.
- **Commands:** `/admin` group structure and `is_admin` gating; `set_multiplayer` permission check, move and re-point; old commands gone.
- **Lottery:** ticket purchase atomicity and per-user limits, no duplicate numbers, split and remainder, rollover, draw idempotency, late-draw catch-up, unannounced-draw retry, exact hit-probability formula.

## 12. Risks

| Risk | Mitigation |
|---|---|
| Double settlement or a stake lost under races or crashes | `settle_lobby` atomicity, `_write_lock`, startup refund sweep, concurrency tests |
| Crash message edits hit Discord rate limits | ~1 tick/s, limits verified in the M3 plan, tick slows if edits fail |
| Pot double counting in pooled games | Explicit house-banked vs pooled rule (§6), tested per game |
| Inflation from minted wins | House edges and rakes; the pot returns house take to members rather than minting new Renown |
| Lottery drawn while the bot is offline | Late draw on startup, stored `next_draw_ts` |
| Bot lacks channel permissions | Checked when `set_multiplayer` runs and again at startup reconcile |
| A lone member wins the pot repeatedly | Accepted for a ~10-member server; per-user ticket cap and N are tunable |
