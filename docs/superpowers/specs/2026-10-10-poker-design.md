# Poker table (Texas hold'em) design

Date: 2026-10-10. Status: draft for owner review. Builds on the original poker section of
`2026-09-30-gambling-design.md` (§7) and the table framework of
`2026-10-07-multiplayer-tables-design.md`. Where they differ, this document wins.

## 1. Goal

A Texas hold'em cash game that runs as a table in a dedicated channel, like the duel, jackpot,
crash and blackjack tables. Players buy in with Renown, play repeated hands with chips, and are paid
out in Renown when they leave, bust, or the table ends. Entertainment only; no real money.

Success criteria:
- A 2 to 6 player table can be opened from the Play embed, joined, played and closed entirely with buttons.
- Renown is conserved: the sum paid out equals the sum bought in. No rake.
- A restart, an error or a stuck table never costs anyone their buy-in (original buy-ins are refunded).
- Hole cards are never visible to anyone but their owner.
- Engine is pure and fully tested without Discord or a database; `pytest` and `ruff` clean.

## 2. Decisions (owner-approved 2026-10-10)

- **Cash game with per-seat cash-out.** Buy-in is escrowed as chips. A player who leaves or busts is
  paid out immediately; the remaining seats are paid when the table ends.
- **Hole cards** via a **My cards** button (ephemeral reply). Public embed edited in place. Acting
  player gets Fold, Check/Call, Raise (modal), All-in.
- No rebuys, no joining after the first hand, no persistence of hands across restarts (v1). A restart
  refunds the original buy-ins; chips won or lost in the interrupted session are not preserved.
- **Equal buy-ins** (`equal_stakes=True`): guests buy in for the host's amount. This keeps blinds
  fair and simple. Min/max come from `GAMBLING_MIN_BET` / `GAMBLING_MAX_BET`.
- Blinds 1% / 2% of the buy-in, rounded up (minimum 1 / 2), fixed for the whole table. Button
  rotates each hand. No-limit, standard min-raise. No rake.
- Ends when: one player holds all chips, `GAMBLING_POKER_MAX_HANDS` hands (default 30) are played,
  everyone but one has left, or the table idles out (every remaining player timed out twice).
- Cards render in poker notation (`As Kh 10d`), never suit glyphs (the one-emoji test counts glyphs).

## 3. Architecture

```
gambling/engines/poker.py     pure: deck, evaluator, betting state machine, side pots
gambling/tables.py            + TableManager.cash_out(), + poker table settle
storage/db.py                 + Database.cash_out_lobby() (lobby-level conservation check),
                                capped poker refunds
ui/poker_views.py             public table view, My cards, Raise modal, runner (run_poker)
ui/embeds.py                  + poker table / result / hand-recap builders
gambling/lobby.py             + GameSpec "poker" (min 2, max 6, equal_stakes)
config.py / .env.example      + GAMBLING_POKER_MAX_HANDS
```

### 3.1 Engine (`engines/poker.py`)

Reuses `Card` and the suit/rank conventions from `engines/blackjack.py`; builds a single 52-card deck
from an injected `random.Random`.

- **Evaluator:** best 5 of 7 cards returned as an orderable tuple `(category, kickers...)`. Handles the
  wheel (A-2-3-4-5), straight flush, quads, full house, flush, straight, trips, two pair, pair, high
  card. Self-written; no new dependency.
- **`PokerTable(stacks: dict[int,int], order: list[int], button: int, small_blind, big_blind, deck)`**
  plays one hand: posts blinds (heads-up: button is small blind), deals hole cards, runs betting
  rounds (preflop, flop, turn, river), showdown. API: `current` (user to act), `legal_actions(user)`,
  `fold/check/call/raise_to/all_in(user, ...)`, `street`, `board`, `pot`, `to_call`, `min_raise_to`,
  `is_over`, `result()`.
- **Short all-ins** that do not make a full raise do not reopen betting.
- **Side pots** are built from contribution levels; each pot is awarded among the non-folded players
  eligible for it. Ties split evenly; the odd chip goes to the first tied player left of the button.
  Uncalled excess is returned.
- Everything is integers. A hand returns `HandResult(stacks_after, pots, winners, shown_hands)`.
- Property test: for any random legal action sequence, `sum(stacks_after) == sum(stacks_before)`.

### 3.2 Money (`TableManager`, `Database`)

Buy-in goes through the existing `open_table` / `join` (escrow, one-active-game claim). The table
runs in memory.

- `TableManager.cash_out(lobby, user_id, chips)`: under `lobby.lock`, pays one seat its chips and
  releases that user's one-game claim. Calls `Database.cash_out_lobby(lobby_id, {bet_id: chips})`.
- `Database.cash_out_lobby(lobby_id, payouts)` (one seat leaving, or the final settle): in one
  transaction, marks each bet settled with `payout = chips`, credits the wallet, writes the usual
  `win:` / `push:` / `payout:` transaction row, and **checks conservation at lobby level**:
  `Renown already paid or refunded in this lobby + this call <= total buy-ins of this lobby`. This
  replaces the per-batch `payout <= stake` rule of `settle_lobby` for poker, because a winner may
  legitimately leave with more than their own buy-in. Poker has no rake, so nothing goes to the
  lottery pot. A bet that is not open raises `BetNotOpenError` and changes nothing; a bet from
  another table or another game raises `ValueError`.
- When the table ends, `TableManager.settle_poker(lobby, payouts)` pays the seats still at the table
  (`payouts` must cover exactly those) through the same method and finishes the lobby (`SETTLED`).
  Invariant asserted in the manager: total paid out equals total buy-ins.
- `Lobby.paid_out` (user id -> Renown) records seats already paid. `Lobby.remaining_seats` is the
  rest. `_finish` and the refund path use `remaining_seats`, so a seat that left earlier is never
  refunded again and a new game that player started afterwards keeps its claim. `lobby.pot` stays the
  total of buy-ins.
- **Refund cap (found while planning P2):** refunding a poker bet at its full buy-in after another
  seat cashed out with winnings would pay out more than was bought in. So an open poker bet is
  refunded `min(stake, (all buy-ins - already paid) * stake // open buy-ins)`. With nothing paid
  yet this is the full buy-in. This applies on every refund path: `abort`, a failed settlement and
  the startup sweep.
- Abort/error path: `abort` refunds every **still-open** bet (capped as above). A seat that was
  already cashed out stays paid.

### 3.3 UI and runner (`ui/poker_views.py`)

Follows `bjtable_views.py`: one public message edited in place, a lock around state changes, an
`acted` event, a deadline per decision, and `finish_table` so any failure refunds and tells players.

- Buttons: **Fold**, **Check / Call N**, **Raise** (opens a modal; validated against min-raise and
  stack, whole numbers only), **All-in**, **My cards** (ephemeral hole cards, any seated player), and
  **Leave** (folds now, cashes out after the hand).
- Only the acting player can use action buttons; everyone else gets the standard not-your-turn error.
  `interaction_check` runs the moderation `screen` like the other tables.
- Turn timer `GAMBLING_TURN_SECONDS` (45 s). On timeout: check if free, otherwise fold. Two
  consecutive timeouts remove the player (folded, cashed out after the hand).
- Between hands: a short recap embed (winners, pots, shown hands), then the next hand. Busted players
  (0 chips) are cashed out for 0 and removed.
- Embeds go through `EmbedFactory`; errors ephemeral; `allowed_mentions` none; at most one emoji and
  one quip per embed; plain error text.
- Hole cards are only ever sent in ephemeral replies; they are never put in the public embed except
  at showdown for players still in the hand.

### 3.4 Restart and failure handling

No new persistence. The existing startup sweep refunds every open bet (poker bets capped to what is
left of the table, see §3.2); `TablesCog.reconcile` already
closes a table left marked active and reposts the Play embed. A player who leaves the server mid-hand
is folded and cashed out after the hand.

## 4. Configuration

| Variable | Default | Purpose |
|---|---|---|
| `GAMBLING_POKER_MAX_HANDS` | `30` | Hand cap per table (minimum 1) |

Documented in `.env.example`. Blind percentages are engine constants.

## 5. Errors

Reuse `NotYourTurnError`, `LobbyClosedError`, `InvalidGameChoiceError`, `InsufficientFundsError`. Add
`InvalidPokerActionError` (plain message, e.g. "The minimum raise is to 40.") if the existing errors
do not read well.

## 6. Testing

- **Evaluator:** every category, kicker ordering, wheel, best-5-of-7 selection, exact ties.
- **Hand engine:** blinds (including heads-up), action order, min-raise, short all-in not reopening,
  fold-to-win, all streets to showdown, seeded full hand, timeouts (check-or-fold).
- **Side pots:** 2 to 4 all-ins at different levels, ties, odd chip, uncalled excess.
- **Conservation property test:** random legal action sequences over many seeds; chips in == chips out.
- **Manager/DB:** `cash_out` atomicity, idempotency, lobby-level conservation (winner may exceed own
  buy-in, total may not exceed pot), abort after partial cash-outs refunds only open bets, claims
  released correctly.
- **Views:** only the acting player acts; modal validation; My cards is ephemeral and only for seated
  players; closed-table presses are rejected; runner failure refunds open bets.
- No network; nothing needs the `live` marker.

## 7. Phasing (each phase: plan, implement, stop for owner review)

- **P1 Engine:** evaluator, hand state machine, side pots, property tests.
- **P2 Money:** `GameSpec`, `cash_out` in `Database` and `TableManager`, abort semantics, config.
- **P3 Table UI:** views, modal, runner, embeds, cog wiring (poker becomes `available`).
- **P4 Docs and wrap-up:** README/SPEC/TASKS/`.env.example`, QA checklist, checkpoint tags.

## 8. Out of scope

Rebuys, late join, tournaments with rising blinds, rake, bots, hand persistence, hand-history
analytics, any real-money feature.

## 9. Open points to confirm at plan time

- Whether **Leave** should also be available between hands from the Play embed or only on the table.
- Exact embed layout (seat list vs. compact table) once a preview is built in `docs/preview/embeds.html`.
