# Economy — Gambling: Design Spec

Date: 2026-09-30. Status: sections 1–4 approved in conversation; §8–§12 written without a separate conversational pass, pending written-spec review.
Companion files: `AGENTS.md`, `SPEC.md`, `TASKS.md`, `docs/superpowers/specs/2026-09-29-economy-core-design.md` (the wallet this builds on).

This is the second economy sub-project. **Saving** and **investing** (simulated market) are separate follow-up specs and are out of scope here. Building is split into four phases (G1–G4) with a stop and owner review after each, per `AGENTS.md` working agreement 2.

## 1. Goal and success criteria

Let members spend and win Renown in solo and multiplayer casino-style games, with no way to create or lose Renown outside the audited wallet.

- Solo games: slots, blackjack, roulette.
- Multiplayer: head-to-head duel, jackpot, crash, multiplayer blackjack table, Texas hold'em poker.
- Every stake is escrowed up front; every stake, win, push and refund writes an audited `renown_transactions` row; a loss (payout 0) is recorded in the `gambling_bets` row only.
- No path to a negative balance, a double payout, or a stake lost to a bot restart.
- All game logic lives in pure, seedable engines tested without Discord or the network.
- `pytest` and `ruff` clean. No new dependency. No new Discord intent.

## 2. Constraints and decisions (from the owner)

- Renown is a server-only fun currency, no real money.
- Architecture "A": shared `gambling/` package with an escrow layer, pure engines, and thin cogs.
- Games: slots, blackjack, roulette (solo); duel, jackpot, crash, blackjack table, poker (multiplayer).
- "Multiplayer poker" means Texas hold'em, multiplayer only. No bot opponent.
- Poker restart handling: refund original buy-ins (no per-hand table persistence).
- Whole-number bets only. Defaults: min bet 10, max bet 5,000.
- Duel and jackpot take a 2% rake (configurable, 0 disables). Poker has no rake.
- Out of scope: saving, investing, challenges, any real-money or external-platform tie-in.

## 3. Architecture

```
src/yokai/gambling/
  escrow.py        # place_bet / settle_bet wrappers + startup sweep
  models.py        # Bet, GameResult dataclasses, errors mapping
  lobby.py         # Lobby state machine shared by multiplayer games
  rng.py           # SystemRandom factory; tests inject seeded random.Random
  engines/
    slots.py  blackjack.py  roulette.py  duel.py  jackpot.py  crash.py  poker.py
src/yokai/cogs/gambling.py   # slash commands, thin: validate -> engine -> embed
src/yokai/ui/embeds.py       # new builders (see §9)
src/yokai/ui/views.py        # game views (BaseView subclasses)
```

Engines take a `random.Random`, return dataclasses, and import nothing from `discord`. Production passes `random.SystemRandom()` for every game; tests pass `random.Random(seed)`.

## 4. Escrow and wallet integration

Stakes leave the wallet when a game starts.

- `Database.place_bet(user_id, amount, game, lobby_id=None) -> bet_id`: under `_write_lock`, ensure wallet, conditional `UPDATE ... WHERE balance >= ?` (same guard as `pay()`), insert a `gambling_bets` row (`status='open'`) and a `renown_transactions` row (reason `bet:<game>`). Raises `InsufficientFundsError`.
- `Database.settle_bet(bet_id, payout)`: in one transaction, mark the bet `settled`, store `payout`, and credit the wallet if `payout > 0` (reason `win:<game>` or `push:<game>`). A loss (payout 0) writes no transaction row; the `gambling_bets` row records it. Idempotent: a bet that is not `open` raises and changes nothing, so a double-settle cannot double-pay.
- `Database.refund_bet(bet_id)`: credit the stake, mark `refunded` (reason `refund:<game>`). Same idempotency guard.
- Blackjack double-down and poker rebuys are additional bets sharing a `group_id`; settlement is per bet.
- Startup sweep: before the bot accepts commands, refund every `open` bet left by a previous process. In-flight state (lobbies, hands) is in memory only and is therefore cancelled. Poker players get their original buy-in back; chips won or lost in the interrupted session are not preserved (accepted, see §12).
- There is no house wallet. Wins mint Renown; the house edge and the rakes in §6 bound inflation.

### Schema (migration v3)

```sql
CREATE TABLE gambling_bets (
  id INTEGER PRIMARY KEY,
  ts INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  game TEXT NOT NULL,           -- slots|blackjack|roulette|duel|jackpot|crash|bjtable|poker
  stake INTEGER NOT NULL,
  status TEXT NOT NULL,         -- open|settled|refunded
  payout INTEGER NOT NULL DEFAULT 0,
  lobby_id TEXT,                -- NULL for solo games
  group_id TEXT                 -- ties double-downs/rebuys to one hand
);
CREATE INDEX idx_gambling_bets_user ON gambling_bets(user_id);
CREATE INDEX idx_gambling_bets_status ON gambling_bets(status);
```

`renown_transactions.reason` gains values `bet:*`, `win:*`, `push:*`, `refund:*`; no schema change there. Gambling data is Renown/Discord-derived only; nothing here touches Spotify- or YouTube-derived fields.

## 5. Solo games (phase G1)

### Common
- Commands validate amount (>= min, <= max, whole number), balance, and "no other active game" **before** `defer`, so rejections are ephemeral. One active solo game per user.
- Buttons use `BaseView` with `allowed_user_ids=[caller]`, time out, and disable themselves.

### Slots — `/slots bet`
- Three reels from a weighted symbol table (CHERRY 40, LEMON 30, BELL 15, BAR 10, SEVEN 5 per 100); paytable is a constant (three of a kind 5x–50x by symbol, exactly two cherries 1.4x, else 0). Reels render as text names (`[CHERRY] [BELL] [SEVEN]`), never emoji, because AGENTS.md allows one emoji per embed. Exact RTP 95.03%.
- Weights tuned for RTP ~95%; a test computes exact RTP from the table and asserts 94–96%.
- Result delivered by editing one message at most twice (spin, result). "Spin again" button repeats the same stake.

### Blackjack — `/blackjack bet`
- 6-deck shoe, fresh per hand; blackjack pays 3:2; dealer stands on all 17s; no insurance; no splits; double on any first two cards.
- Buttons: Hit, Stand, Double (Double is disabled if the extra stake cannot be placed).
- On the 3-minute view timeout the hand auto-stands and settles normally (the player is not forfeited).

### Roulette — `/roulette bet choice [pick]`
- European single-zero wheel (37 pockets), 2.7% edge on all bets.
- `choice`: red/black/odd/even/low/high (1:1), dozen/column (2:1), straight number (35:1). `pick` carries the number/dozen/column and is validated before defer. One bet per command.

## 6. Lobby framework and multiplayer (phases G2 and G3)

### Lobby (`lobby.py`)
- States: `OPEN -> RUNNING -> SETTLED | CANCELLED`. Host runs the command; a public message with Join / Leave / Start (host) buttons is edited in place.
- Join places the stake immediately; Leave refunds while `OPEN`. An unstarted lobby auto-cancels after the join window and refunds everyone.
- One lobby per channel; a user may be in only one active game of any kind. One `asyncio.Lock` per lobby serializes join/leave/start/settle.
- Views allow participants only (non-participants may press Join); all views time out and disable.

### Duel — `/duel user bet [game]`
- Challenger stake escrowed on challenge; target gets Accept/Decline (target only). Expires after the join window with a refund.
- Resolves immediately on accept: coinflip (default) or high-roll dice. Winner takes the pot minus `GAMBLING_DUEL_RAKE_PERCENT` (default 2), rounded down. Self-challenge and bot targets refused.

### Jackpot — `/jackpot bet`
- 2–10 players, 60 s join window (`GAMBLING_LOBBY_JOIN_SECONDS`), per-entry min/max. One stake-weighted draw; winner takes the pot minus `GAMBLING_JACKPOT_RAKE_PERCENT` (default 2). A lobby with one entrant cancels and refunds.
- `draw_winner(entries, rng)` is pure; tests check probabilities over many seeded draws.

### Crash — `/crash bet`
- Lobby, 30 s join window, then one message edited about once per second with the rising multiplier. Players press Cash out to lock in.
- Crash point drawn up front and never recomputed: `max(1.0, 0.97 / U)` with `U` uniform in (0, 1], capped at `GAMBLING_CRASH_MAX_MULTIPLIER` (default 100). 3% edge.
- Cash-out is validated against the server's multiplier at press time, not the displayed one; a press after the crash loses. The engine advances on a tick function taking the current time, so tests need no sleeping. Edit cadence must respect discord.py's current rate limits (verify in the plan; AGENTS.md rule 4).

### Blackjack table — `/blackjack-table bet`
- 2–5 players, 30 s join window, one shared dealer, same rules as solo except no double. Players act in join order; only the active player's buttons work; 45 s per turn (`GAMBLING_TURN_SECONDS`), auto-stand on timeout. Each player settles individually.

## 7. Poker (phase G4)

- `/poker buyin` opens a 2–6 player table lobby (60 s join window). Buy-in is escrowed and becomes the chip stack (min/max configurable).
- Hands repeat until one player holds all chips, everyone but the host leaves, the table idles out, or the hand cap (`GAMBLING_POKER_MAX_HANDS`, default 30) is reached; then remaining chips are credited back via `settle_bet`.
- Blinds fixed at 1% / 2% of the buy-in (rounded up), button rotates each hand, no escalation. No-limit, standard min-raise. No rake.
- Public embed edited in place (community cards, pot, stacks, turn). Hole cards via a **My cards** button that replies ephemerally. Cards render in poker notation (`As Kh 10d`, suit letters c/d/h/s), never suit glyphs, because the one-emoji test counts `♠♥♦♣` as emoji.
- Acting player's buttons: Fold, Check/Call, Raise (modal, validated against min-raise and stack), All-in. 45 s turn timer: check if free, else fold; two consecutive timeouts remove and cash out the player. A player who leaves the server mid-hand is folded and cashed out after the hand.
- Engine: self-written 5-of-7 evaluator (category + kicker tuple, wheel straight handled); pure betting state machine (blinds -> preflop -> flop -> turn -> river -> showdown); side pots built from contribution levels; ties split evenly with the odd chip to the first player left of the button. No new dependency.

## 8. Configuration

All in `config.py` and documented in `.env.example`.

| Variable | Default | Purpose |
|---|---|---|
| `GAMBLING_MIN_BET` | `10` | Minimum stake / poker min buy-in |
| `GAMBLING_MAX_BET` | `5000` | Maximum stake |
| `GAMBLING_DUEL_RAKE_PERCENT` | `2` | Rake on duel pots (0 disables) |
| `GAMBLING_JACKPOT_RAKE_PERCENT` | `2` | Rake on jackpot pots (0 disables) |
| `GAMBLING_LOBBY_JOIN_SECONDS` | `60` | Join window for lobbies (crash and blackjack table use half this) |
| `GAMBLING_TURN_SECONDS` | `45` | Per-turn timer for table games |
| `GAMBLING_CRASH_MAX_MULTIPLIER` | `100` | Crash point cap |
| `GAMBLING_POKER_MAX_HANDS` | `30` | Hand cap per poker table |

Per-game odds (paytables, weights, blackjack rules) are constants in the engines, not environment variables.

## 9. Commands, errors, UX

New commands (all slash, guild-scoped, logged through `command_log`): `/slots`, `/blackjack`, `/roulette`, `/duel`, `/jackpot`, `/crash`, `/blackjack-table`, `/poker`, and `/gamble-stats [user]` (net profit and games per game type, from `gambling_bets`).

- New typed errors in `errors.py`: `BetOutOfRangeError`, `AlreadyInGameError`, `LobbyClosedError`, `NotYourTurnError` (reusing `InsufficientFundsError`).
- New `EmbedFactory` builders per game state (slots result, blackjack hand, roulette result, lobby, crash tick, poker table, gamble stats) and new `theme.py` quip keys (win, loss, push, crash, bust, all-in), 3+ variants each, one quip and one emoji per embed at most. Error text stays plain.
- No raw `send_message` / `channel.send` in feature code; errors ephemeral; `allowed_mentions` none, mentions only inside embeds.
- Embeds: every gambling embed has at most one emoji and contains no suit glyphs.

## 10. Testing strategy

No network anywhere; nothing needs the `live` marker.

- **Escrow:** place/settle/refund atomicity, idempotency (double-settle and double-refund change nothing), no negative balance, concurrent bets cannot overspend, startup sweep refunds open bets and leaves settled ones alone.
- **Slots / roulette:** seeded determinism, exact payout tables, RTP and house-edge assertions.
- **Blackjack:** natural vs natural, bust, dealer bust, push, double, 3:2 payout rounding, shoe handling; table variant turn order and timeout auto-stand.
- **Duel / jackpot:** rake rounding, weighted-draw probabilities, single-entrant refund, expiry refund.
- **Crash:** distribution and cap, server-authoritative cash-out, post-crash press loses, tick-driven engine with a fake clock.
- **Poker:** evaluator tables (all categories, kickers, wheel, 7-card selection), side pots with 2–4 all-ins and ties, seeded full hand, a **conservation property test** (random action sequences: total chips out == total chips in), min-raise and timeout-fold.
- **Lobby:** join/leave/start races under the per-lobby lock, auto-cancel refunds, one-active-game rule.
- **Cogs/views:** validation-before-defer returns ephemeral errors; caller/participant checks reject outsiders.

## 11. Phasing

Each phase ends with a stop and owner review.

- **G1:** migration v3, escrow layer, rng helper, slots, blackjack, roulette, `/gamble-stats`.
- **G2:** lobby framework, duel, jackpot.
- **G3:** crash, blackjack table.
- **G4:** poker.

## 12. Risks

| Risk | Mitigation |
|---|---|
| Double settlement or lost stake under races or crashes | Escrow idempotency guards, `_write_lock`, startup refund sweep, concurrency tests |
| Restart mid-poker loses session winnings (only original buy-in returned) | Accepted for a small, trusted server; revisit with per-hand persistence if it bites |
| Crash message edits hit Discord rate limits | Tick at ~1/s, verify current limits in the plan, degrade tick rate if edits fail |
| Inflation from minted wins | House edges, duel/jackpot rake; revisit with sinks if the leaderboard runs away |
| Players farm the economy with multiple accounts or collusion at poker | Out of scope for ~10 trusted users; `gambling_bets` provides the audit trail |
| Poker complexity (side pots, min-raise) hides bugs | Pure engine, property-based conservation test, own phase with review |
| Players hold funds across a lobby and block each other | One-active-game rule, auto-cancel timeouts, refunds |

## 13. Out of scope

Saving and investing (own specs, simulated market for investing), challenges, per-hand poker persistence, blackjack splits and insurance, multi-bet roulette tables, poker AI opponents, any real-money feature, and any change to music playback.
