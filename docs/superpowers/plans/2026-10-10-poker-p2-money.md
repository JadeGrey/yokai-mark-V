# Poker P2: money Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Post-review amendment (2026-10-10):** the final review found that the refund formula below, with
> its `min(stake, ...)` cap, destroyed the buy-ins of busted seats on an abort or restart. The code
> now refunds `(all buy-ins - already paid) * stake // open buy-ins` with no cap; see the spec's
> "Refund share" bullet. Where this plan says "capped", read "shared out".

**Goal:** Let a poker seat be paid its chips mid-game and let the table settle what is left, without ever creating or losing Renown, and add the poker game spec and hand-cap setting.

**Architecture:** Poker reuses the existing escrow: each player's buy-in is an ordinary open bet in the table's lobby. A new `Database.cash_out_lobby` pays one or more of those bets with a table-wide limit (everything paid or refunded plus this call may not exceed the sum of all buy-ins) and no rake. `TableManager.cash_out` / `settle_poker` wrap it with the lobby lock and one-game claims. Refunds of poker bets are capped to what is left of the table, so an abort or restart after a cash-out cannot pay out more than was bought in.

**Tech Stack:** Python 3.12+, aiosqlite, pytest, ruff. No new dependency.

**Spec:** `docs/superpowers/specs/2026-10-10-poker-design.md` (§2 decisions, §3.2 money, §4 configuration; Task 5 updates §3.2 and §3.4 to match what is built). P1 (engine) is done; P3 (table UI) and P4 (docs) get their own plans.

## Global Constraints

- `from __future__ import annotations`, fully type-hinted, no bare `except`; domain errors from `yokai/errors.py` (AGENTS.md).
- Whole-number Renown only. Poker has **no rake**: nothing from poker goes to the lottery pot.
- Every database write that moves Renown is one atomic transaction; a failed batch changes nothing.
- Config comes from environment variables in `config.py` and is documented in `.env.example`.
- No new dependency (AGENTS.md rule 5). No Discord code in this phase.
- Never mark a task done without running `python -m pytest -q`, `python -m ruff check .` and `python -m ruff format --check .` (PowerShell, repo root, venv active).
- Commit per task; messages end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

## Review Focus

- Restart or abort after one seat cashed out with winnings: the remaining seats must be refunded only their share of what is left, never their full buy-in (tests in Tasks 3 and 4).
- A player who cashed out and then started another game must keep that new game's claim when the table settles or aborts (tests in Task 4).
- A table where every seat already cashed out settles with no payouts (test in Task 4).
- A payout batch that breaks the table-wide limit pays nobody; a seat cannot be paid twice; bets of another table or game are rejected (tests in Task 3).
- Poker appears in the `/admin set_multiplayer` dropdown but cannot be opened yet and a failed attempt costs nothing (Tasks 2 and 4).

## File Structure

- Modify `src/yokai/config.py`, `src/yokai/bot.py`, `.env.example`: `GAMBLING_POKER_MAX_HANDS`.
- Modify `src/yokai/gambling/tables.py`: `poker_max_hands`, `cash_out`, `settle_poker`, claim-safe `_finish` and refund loop.
- Modify `src/yokai/gambling/lobby.py`: poker `GameSpec`, `Lobby.paid_out`, `remaining_seats`, `mark_paid_out`.
- Modify `src/yokai/storage/db.py`: `_close_bet_with_payout` (extracted), `cash_out_lobby`, `_poker_table_totals`, `_poker_refund_amount`, capped refund in `_finish_bet`.
- Create `tests/test_poker_cash_out.py`, `tests/test_poker_table_manager.py`; modify `tests/test_gambling_config.py`, `tests/test_bot_wiring.py`, `tests/test_lobby.py`, `tests/test_admin_cog.py`.
- Modify `docs/superpowers/specs/2026-10-10-poker-design.md`, `TASKS.md`.

Existing fixtures used: `tests/table_helpers.py` (`world(tmp_path, game=..., specs=...)`, `ALL_PLAYABLE`, `announce_ok`, `balance_of`, `CHANNEL`).

---

### Task 1: The hand-cap setting

**Files:** modify `src/yokai/config.py`, `src/yokai/bot.py`, `src/yokai/gambling/tables.py`, `.env.example`, `tests/test_gambling_config.py`, `tests/test_bot_wiring.py`.

**Interfaces:**
- Produces: `Config.gambling_poker_max_hands: int` (default 30, minimum 1, env `GAMBLING_POKER_MAX_HANDS`); `TableManager(..., poker_max_hands: int = 30)` stored as `manager.poker_max_hands` (P3's runner reads it).

- [ ] **Step 1: Write the failing tests**

Apply this diff (save it to a `.patch` file in the scratchpad and run `git apply --ignore-whitespace <file>` from the repo root, or make the same edits by hand):

```diff
diff --git a/tests/test_bot_wiring.py b/tests/test_bot_wiring.py
index 1ca882a..a15191e 100644
--- a/tests/test_bot_wiring.py
+++ b/tests/test_bot_wiring.py
@@ -343,6 +343,7 @@ async def test_the_table_manager_gets_the_jackpot_and_crash_settings() -> None:
     bot = make_bot()
     assert bot.tables.jackpot_rake_percent == bot.config.gambling_jackpot_rake_percent
     assert bot.tables.crash_max_multiplier == bot.config.gambling_crash_max_multiplier
+    assert bot.tables.poker_max_hands == bot.config.gambling_poker_max_hands
 
 
 @pytest.mark.asyncio
diff --git a/tests/test_gambling_config.py b/tests/test_gambling_config.py
index 4628b1e..4489522 100644
--- a/tests/test_gambling_config.py
+++ b/tests/test_gambling_config.py
@@ -92,6 +92,16 @@ def test_crash_cap_has_a_floor_of_two() -> None:
     assert _load({"GAMBLING_CRASH_MAX_MULTIPLIER": "0"}).gambling_crash_max_multiplier == 2
 
 
+def test_poker_hand_cap_defaults_to_thirty_and_can_be_changed() -> None:
+    assert _load().gambling_poker_max_hands == 30
+    assert _load({"GAMBLING_POKER_MAX_HANDS": "12"}).gambling_poker_max_hands == 12
+
+
+def test_poker_hand_cap_has_a_floor_of_one() -> None:
+    assert _load({"GAMBLING_POKER_MAX_HANDS": "0"}).gambling_poker_max_hands == 1
+    assert _load({"GAMBLING_POKER_MAX_HANDS": "-5"}).gambling_poker_max_hands == 1
+
+
 def test_lottery_defaults() -> None:
     cfg = _load()
     assert cfg.lottery_ticket_price == 100
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest -q tests/test_gambling_config.py tests/test_bot_wiring.py`
Expected: `3 failed, 37 passed` (the two new config tests and the wiring test).

- [ ] **Step 3: Implement**

Apply this diff (save it to a `.patch` file in the scratchpad and run `git apply --ignore-whitespace <file>` from the repo root, or make the same edits by hand):

```diff
diff --git a/.env.example b/.env.example
index 832cee8..ece72af 100644
--- a/.env.example
+++ b/.env.example
@@ -53,6 +53,8 @@ GAMBLING_TURN_SECONDS=45
 GAMBLING_JACKPOT_RAKE_PERCENT=2
 # Highest multiplier a crash round can reach
 GAMBLING_CRASH_MAX_MULTIPLIER=100
+# Most hands a poker table plays before it closes and pays everyone out (at least 1)
+GAMBLING_POKER_MAX_HANDS=30
 
 # Lottery
 # Price of one ticket (whole Renown)
diff --git a/src/yokai/bot.py b/src/yokai/bot.py
index 0ef84db..9e3d16b 100644
--- a/src/yokai/bot.py
+++ b/src/yokai/bot.py
@@ -97,6 +97,7 @@ class YokaiBot(commands.Bot):
             pick_seconds=config.gambling_turn_seconds,
             jackpot_rake_percent=config.gambling_jackpot_rake_percent,
             crash_max_multiplier=config.gambling_crash_max_multiplier,
+            poker_max_hands=config.gambling_poker_max_hands,
         )
         self._tables_reconciled = False
         self._tables_reconciled = False
diff --git a/src/yokai/config.py b/src/yokai/config.py
index 84f3b0a..34039a3 100644
--- a/src/yokai/config.py
+++ b/src/yokai/config.py
@@ -68,6 +68,7 @@ class Config:
     gambling_turn_seconds: int = 45
     gambling_jackpot_rake_percent: int = 2
     gambling_crash_max_multiplier: int = 100
+    gambling_poker_max_hands: int = 30
 
     # Lottery
     lottery_ticket_price: int = 100
@@ -217,6 +218,9 @@ def load_config(env_path: Path | str | None = None) -> Config:
             "GAMBLING_CRASH_MAX_MULTIPLIER", os.getenv("GAMBLING_CRASH_MAX_MULTIPLIER"), 100
         ),
     )
+    gambling_poker_max_hands = max(
+        1, _parse_int("GAMBLING_POKER_MAX_HANDS", os.getenv("GAMBLING_POKER_MAX_HANDS"), 30)
+    )
     lottery_ticket_price = max(
         1, _parse_int("LOTTERY_TICKET_PRICE", os.getenv("LOTTERY_TICKET_PRICE"), 100)
     )
@@ -266,6 +270,7 @@ def load_config(env_path: Path | str | None = None) -> Config:
         gambling_turn_seconds=gambling_turn_seconds,
         gambling_jackpot_rake_percent=gambling_jackpot_rake_percent,
         gambling_crash_max_multiplier=gambling_crash_max_multiplier,
+        gambling_poker_max_hands=gambling_poker_max_hands,
         lottery_ticket_price=lottery_ticket_price,
         lottery_number_range=lottery_number_range,
         lottery_max_tickets_per_user=lottery_max_tickets_per_user,
diff --git a/src/yokai/gambling/tables.py b/src/yokai/gambling/tables.py
index e9d0937..d98a8fe 100644
--- a/src/yokai/gambling/tables.py
+++ b/src/yokai/gambling/tables.py
@@ -82,6 +82,7 @@ class TableManager:
         pick_seconds: int,
         jackpot_rake_percent: int = 2,
         crash_max_multiplier: int = 100,
+        poker_max_hands: int = 30,
         specs: Optional[Mapping[str, GameSpec]] = None,
         clock: Callable[[], float] = time.time,
         rng_factory: Callable[[], random.Random] = new_rng,
@@ -94,6 +95,7 @@ class TableManager:
         self.rake_percent = rake_percent
         self.jackpot_rake_percent = jackpot_rake_percent
         self.crash_max_multiplier = crash_max_multiplier
+        self.poker_max_hands = poker_max_hands
         self.join_seconds = join_seconds
         self.pick_seconds = pick_seconds
         self.specs: Mapping[str, GameSpec] = specs if specs is not None else GAME_SPECS
```

- [ ] **Step 4: Run them to verify they pass**

Run: `python -m pytest -q tests/test_gambling_config.py tests/test_bot_wiring.py`
Expected: `40 passed`.

- [ ] **Step 5: Lint and commit**

Run `python -m ruff check .` and `python -m ruff format --check .` (both clean).

```bash
git add -A src tests .env.example
git commit -m "feat(poker): add the GAMBLING_POKER_MAX_HANDS setting" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Poker game spec and lobby cash-out bookkeeping

**Files:** modify `src/yokai/gambling/lobby.py`, `tests/test_lobby.py`, `tests/test_admin_cog.py`.

**Interfaces:**
- Consumes: `GameSpec`, `Lobby`, `Seat`, `NotAtTableError`.
- Produces: `GAME_SPECS["poker"]` (`min_players=2, max_players=6, equal_stakes=True, available=False`, label `"Poker table"`); `Lobby.paid_out: dict[int, int]`; `Lobby.remaining_seats -> dict[int, Seat]`; `Lobby.mark_paid_out(user_id: int, chips: int) -> None` (raises `NotAtTableError` for a stranger, `ValueError` if already paid or negative).

- [ ] **Step 1: Write the failing tests**

Apply this diff (save it to a `.patch` file in the scratchpad and run `git apply --ignore-whitespace <file>` from the repo root, or make the same edits by hand):

```diff
diff --git a/tests/test_admin_cog.py b/tests/test_admin_cog.py
index 5559753..56c4fbf 100644
--- a/tests/test_admin_cog.py
+++ b/tests/test_admin_cog.py
@@ -224,6 +224,7 @@ def test_the_game_dropdown_lists_every_planned_game() -> None:
         "jackpot",
         "crash",
         "bjtable",
+        "poker",
         "lottery",
     ]
     assert [choice.name for choice in GAME_CHOICES][:2] == ["Duel", "Jackpot"]
diff --git a/tests/test_lobby.py b/tests/test_lobby.py
index 8c10c40..216cf26 100644
--- a/tests/test_lobby.py
+++ b/tests/test_lobby.py
@@ -22,9 +22,9 @@ def test_the_duel_spec_is_a_two_seat_equal_stake_game() -> None:
     assert spec.label == "Duel" and spec.rules
 
 
-def test_every_planned_game_is_listed_and_playable_and_only_the_lottery_has_no_table() -> None:
-    assert list(GAME_SPECS) == ["duel", "jackpot", "crash", "bjtable", "lottery"]
-    assert all(spec.available for spec in GAME_SPECS.values())
+def test_every_planned_game_is_listed_and_only_poker_is_not_playable_yet() -> None:
+    assert list(GAME_SPECS) == ["duel", "jackpot", "crash", "bjtable", "poker", "lottery"]
+    assert [name for name, spec in GAME_SPECS.items() if not spec.available] == ["poker"]
     assert [name for name, spec in GAME_SPECS.items() if not spec.is_table] == ["lottery"]
     assert all(spec.label and spec.rules for spec in GAME_SPECS.values())
     assert GAME_SPECS["crash"].window_factor == 0.5 and GAME_SPECS["bjtable"].window_factor == 0.5
@@ -33,6 +33,33 @@ def test_every_planned_game_is_listed_and_playable_and_only_the_lottery_has_no_t
     assert (GAME_SPECS["bjtable"].min_players, GAME_SPECS["bjtable"].max_players) == (2, 5)
 
 
+def test_the_poker_spec_is_a_two_to_six_seat_equal_buy_in_game() -> None:
+    spec = GAME_SPECS["poker"]
+    assert (spec.min_players, spec.max_players, spec.equal_stakes) == (2, 6, True)
+    assert spec.is_table and not spec.available
+    assert spec.label == "Poker table" and "hold'em" in spec.rules
+
+
+def test_cashing_a_seat_out_removes_it_from_the_remaining_seats_but_not_from_the_pot() -> None:
+    lobby = _lobby()
+    lobby.add_seat(Seat(2, 100, 12))
+    lobby.mark_paid_out(2, 30)
+    assert lobby.paid_out == {2: 30}
+    assert list(lobby.remaining_seats) == [1]
+    assert lobby.pot == 200 and set(lobby.seats) == {1, 2}
+
+
+def test_a_seat_can_only_be_cashed_out_once_and_only_if_seated() -> None:
+    lobby = _lobby()
+    with pytest.raises(NotAtTableError):
+        lobby.mark_paid_out(9, 10)
+    lobby.mark_paid_out(1, 10)
+    with pytest.raises(ValueError):
+        lobby.mark_paid_out(1, 10)
+    with pytest.raises(ValueError):
+        _lobby().mark_paid_out(1, -1)
+
+
 def test_a_new_lobby_is_open_with_the_host_seated() -> None:
     lobby = _lobby()
     assert lobby.state is LobbyState.OPEN and lobby.is_active
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest -q tests/test_lobby.py tests/test_admin_cog.py`
Expected: `5 failed` (four lobby tests and the admin dropdown test).

- [ ] **Step 3: Implement**

Apply this diff (save it to a `.patch` file in the scratchpad and run `git apply --ignore-whitespace <file>` from the repo root, or make the same edits by hand):

```diff
diff --git a/src/yokai/gambling/lobby.py b/src/yokai/gambling/lobby.py
index 8072d64..5167155 100644
--- a/src/yokai/gambling/lobby.py
+++ b/src/yokai/gambling/lobby.py
@@ -73,6 +73,18 @@ GAME_SPECS: dict[str, GameSpec] = {
         window_factor=0.5,
         rules="Blackjack against one shared dealer. Every player plays their own hand.",
     ),
+    "poker": GameSpec(
+        name="poker",
+        label="Poker table",
+        min_players=2,
+        max_players=6,
+        equal_stakes=True,
+        available=False,  # until its table runner ships (phase P3)
+        rules=(
+            "Texas hold'em with chips. Everyone buys in for the same amount and plays hands "
+            "until the hand limit; leave or bust and your chips are paid out in Renown."
+        ),
+    ),
     # Not a table: it has its own channel panel and a weekly draw.
     "lottery": GameSpec(
         name="lottery",
@@ -108,6 +120,9 @@ class Lobby:
         self.closes_at = closes_at
         self.state = LobbyState.OPEN
         self.seats: dict[int, Seat] = {}
+        # Poker only: seats already paid out mid-game (user id -> Renown paid). Their stake is
+        # settled, so a later settle or refund must leave them alone.
+        self.paid_out: dict[int, int] = {}
         self.lock = asyncio.Lock()
         self.message_id: Optional[int] = None
 
@@ -127,6 +142,11 @@ class Lobby:
     def pot(self) -> int:
         return sum(seat.stake for seat in self.seats.values())
 
+    @property
+    def remaining_seats(self) -> dict[int, Seat]:
+        """Seats whose stake is still escrowed (everyone, unless poker has paid some out)."""
+        return {u: seat for u, seat in self.seats.items() if u not in self.paid_out}
+
     @property
     def host_seat(self) -> Seat:
         return self.seats[self.host_id]
@@ -156,6 +176,16 @@ class Lobby:
         self.check_can_leave(user_id)
         return self.seats.pop(user_id)
 
+    def mark_paid_out(self, user_id: int, chips: int) -> None:
+        """Record that a seat's stake was settled mid-game for `chips` Renown."""
+        if user_id not in self.seats:
+            raise NotAtTableError()
+        if user_id in self.paid_out:
+            raise ValueError("this seat was already paid out")
+        if chips < 0:
+            raise ValueError("a payout cannot be negative")
+        self.paid_out[user_id] = chips
+
     def mark_running(self) -> None:
         if self.state is not LobbyState.OPEN:
             raise LobbyClosedError()
```

- [ ] **Step 4: Run the tests and the whole suite**

Run: `python -m pytest -q tests/test_lobby.py tests/test_admin_cog.py tests/test_tables_cog.py`, then `python -m pytest -q`.
Expected: all pass. Poker is listed in the dropdown (the framework lists unavailable games and answers "not available yet" when one is picked).

- [ ] **Step 5: Lint and commit**

```bash
git add -A src tests
git commit -m "feat(poker): add the poker game spec and lobby cash-out bookkeeping" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Database cash-out and capped poker refunds

**Files:** modify `src/yokai/storage/db.py`; create `tests/test_poker_cash_out.py`.

**Interfaces:**
- Consumes: the `gambling_bets` table (`lobby_id`, `status`, `payout`), `BetNotOpenError`, `_write_tx`.
- Produces:
  - `Database.cash_out_lobby(lobby_id: str, payouts: Mapping[int, int]) -> dict[int, int]`: bet id to renown paid, returns bet id to the owner's new balance. Raises `ValueError` for an empty or negative batch, a non-poker bet, a bet of another lobby, or a batch over the table-wide limit; `BetNotOpenError` for a bet that is not open. All or nothing. No lottery-pot credit.
  - `Database._close_bet_with_payout(bet_id, user_id, game, stake, payout, now) -> int` (extracted from `settle_lobby`, behaviour unchanged).
  - `refund_bet` / `refund_open_bets` refund an open poker bet `min(stake, (all buy-ins - already paid) * stake // open buy-ins)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_poker_cash_out.py`:

Apply this diff (save it to a `.patch` file in the scratchpad and run `git apply --ignore-whitespace <file>` from the repo root, or make the same edits by hand):

```diff
diff --git a/tests/test_poker_cash_out.py b/tests/test_poker_cash_out.py
new file mode 100644
index 0000000..a6301a7
--- /dev/null
+++ b/tests/test_poker_cash_out.py
@@ -0,0 +1,179 @@
+"""cash_out_lobby pays poker seats mid-game; refunds of poker bets never exceed what is left."""
+
+from __future__ import annotations
+
+from pathlib import Path
+
+import pytest
+
+from yokai.errors import BetNotOpenError
+from yokai.storage.db import Database
+
+
+async def _world(tmp_path: Path) -> tuple[Database, int, int, int]:
+    """Three players, each with 1000 Renown, each buying in for 100 at one poker table."""
+    db = Database(tmp_path / "poker.db")
+    await db.initialize()
+    bets = []
+    for user_id in (1, 2, 3):
+        await db.ensure_wallet(user_id, 1000)
+        bets.append(await db.place_bet(user_id, 100, "poker", 0, lobby_id="L1"))
+    return db, bets[0], bets[1], bets[2]
+
+
+async def _balance(db: Database, user_id: int) -> int:
+    wallet = await db.get_wallet(user_id)
+    assert wallet is not None
+    return wallet.balance
+
+
+async def _status(db: Database, bet_id: int) -> str:
+    async with db.conn.execute("SELECT status FROM gambling_bets WHERE id = ?;", (bet_id,)) as cur:
+        row = await cur.fetchone()
+    assert row is not None
+    return row[0]
+
+
+@pytest.mark.asyncio
+async def test_a_winner_can_leave_with_more_than_their_buy_in(tmp_path: Path) -> None:
+    db, a, b, c = await _world(tmp_path)
+    try:
+        balances = await db.cash_out_lobby("L1", {a: 250})
+        assert balances == {a: 1150}
+        assert await _balance(db, 1) == 1150
+        assert await _status(db, a) == "settled"
+        async with db.conn.execute(
+            "SELECT user_id, delta, reason FROM renown_transactions WHERE reason LIKE 'win:%';"
+        ) as cur:
+            assert await cur.fetchall() == [(1, 250, "win:poker")]
+        assert await db.get_pot() == 0  # poker has no rake: nothing goes to the lottery
+    finally:
+        await db.close()
+
+
+@pytest.mark.asyncio
+async def test_a_busted_player_is_paid_nothing_and_gets_no_transaction_row(tmp_path: Path) -> None:
+    db, a, b, c = await _world(tmp_path)
+    try:
+        assert await db.cash_out_lobby("L1", {b: 0}) == {b: 900}
+        assert await _status(db, b) == "settled"
+        async with db.conn.execute(
+            "SELECT COUNT(*) FROM renown_transactions "
+            "WHERE reason IN ('win:poker', 'push:poker', 'payout:poker');"
+        ) as cur:
+            assert (await cur.fetchone()) == (0,)
+        assert await db.get_pot() == 0
+    finally:
+        await db.close()
+
+
+@pytest.mark.asyncio
+async def test_several_seats_can_be_paid_in_one_transaction(tmp_path: Path) -> None:
+    db, a, b, c = await _world(tmp_path)
+    try:
+        balances = await db.cash_out_lobby("L1", {a: 0, b: 100, c: 200})
+        assert balances == {a: 900, b: 1000, c: 1100}
+    finally:
+        await db.close()
+
+
+@pytest.mark.asyncio
+async def test_payouts_can_never_exceed_the_buy_ins_of_the_whole_table(tmp_path: Path) -> None:
+    db, a, b, c = await _world(tmp_path)
+    try:
+        await db.cash_out_lobby("L1", {a: 200})  # 300 in, 200 out so far
+        with pytest.raises(ValueError, match="more than the table"):
+            await db.cash_out_lobby("L1", {b: 101})
+        assert await _status(db, b) == "open" and await _balance(db, 2) == 900
+        await db.cash_out_lobby("L1", {b: 100})  # exactly the 300 total is fine
+    finally:
+        await db.close()
+
+
+@pytest.mark.asyncio
+async def test_a_batch_that_breaks_the_limit_pays_nobody(tmp_path: Path) -> None:
+    db, a, b, c = await _world(tmp_path)
+    try:
+        with pytest.raises(ValueError):
+            await db.cash_out_lobby("L1", {a: 100, b: 300})
+        assert (await _status(db, a), await _status(db, b)) == ("open", "open")
+        assert (await _balance(db, 1), await _balance(db, 2)) == (900, 900)
+    finally:
+        await db.close()
+
+
+@pytest.mark.asyncio
+async def test_a_seat_cannot_be_paid_twice(tmp_path: Path) -> None:
+    db, a, b, c = await _world(tmp_path)
+    try:
+        await db.cash_out_lobby("L1", {a: 100})
+        with pytest.raises(BetNotOpenError):
+            await db.cash_out_lobby("L1", {a: 100})
+        assert await _balance(db, 1) == 1000
+    finally:
+        await db.close()
+
+
+@pytest.mark.asyncio
+async def test_only_poker_bets_of_that_lobby_can_be_cashed_out(tmp_path: Path) -> None:
+    db, a, b, c = await _world(tmp_path)
+    try:
+        await db.ensure_wallet(4, 1000)
+        other = await db.place_bet(4, 100, "poker", 0, lobby_id="L2")
+        duel = await db.place_bet(4, 100, "duel", 0, lobby_id="L1")
+        with pytest.raises(ValueError):
+            await db.cash_out_lobby("L1", {other: 50})
+        with pytest.raises(ValueError):
+            await db.cash_out_lobby("L1", {duel: 50})
+        assert await _status(db, other) == "open" and await _status(db, duel) == "open"
+    finally:
+        await db.close()
+
+
+@pytest.mark.asyncio
+async def test_empty_and_negative_payouts_are_rejected(tmp_path: Path) -> None:
+    db, a, b, c = await _world(tmp_path)
+    try:
+        with pytest.raises(ValueError):
+            await db.cash_out_lobby("L1", {})
+        with pytest.raises(ValueError):
+            await db.cash_out_lobby("L1", {a: -1})
+    finally:
+        await db.close()
+
+
+@pytest.mark.asyncio
+async def test_refunding_a_poker_bet_with_nothing_paid_returns_the_full_buy_in(
+    tmp_path: Path,
+) -> None:
+    db, a, b, c = await _world(tmp_path)
+    try:
+        assert await db.refund_bet(a) == 1000
+    finally:
+        await db.close()
+
+
+@pytest.mark.asyncio
+async def test_refunds_after_a_cash_out_share_only_what_is_left(tmp_path: Path) -> None:
+    """A won 100 extra and left; a restart must not give B and C their full 100 back."""
+    db, a, b, c = await _world(tmp_path)
+    try:
+        await db.cash_out_lobby("L1", {a: 200})  # 100 of the 300 is left for B and C
+        assert await db.refund_bet(b) == 950  # 100 * 100 // 200 = 50 back
+        assert await db.refund_bet(c) == 950  # the other 50
+        total = sum([await _balance(db, u) for u in (1, 2, 3)])
+        assert total == 3000  # the 300 bought in is exactly the 300 paid out; none created
+    finally:
+        await db.close()
+
+
+@pytest.mark.asyncio
+async def test_the_startup_sweep_applies_the_same_cap(tmp_path: Path) -> None:
+    db, a, b, c = await _world(tmp_path)
+    try:
+        await db.cash_out_lobby("L1", {a: 300})  # everything is left with A
+        assert await db.refund_open_bets() == 2
+        assert (await _balance(db, 2), await _balance(db, 3)) == (900, 900)
+        assert await _balance(db, 1) == 1200
+    finally:
+        await db.close()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest -q tests/test_poker_cash_out.py`
Expected: `10 failed, 1 passed` (the passing one is the unchanged full refund when nothing was paid).

- [ ] **Step 3: Implement**

Apply this diff (save it to a `.patch` file in the scratchpad and run `git apply --ignore-whitespace <file>` from the repo root, or make the same edits by hand):

```diff
diff --git a/src/yokai/storage/db.py b/src/yokai/storage/db.py
index ed0b816..fdd7e9a 100644
--- a/src/yokai/storage/db.py
+++ b/src/yokai/storage/db.py
@@ -1084,15 +1084,20 @@ class Database:
         async with self._write_lock:
             try:
                 async with self.conn.execute(
-                    "SELECT user_id, game, stake FROM gambling_bets "
+                    "SELECT user_id, game, stake, lobby_id FROM gambling_bets "
                     "WHERE id = ? AND status = 'open';",
                     (bet_id,),
                 ) as cursor:
                     row = await cursor.fetchone()
                 if row is None:
                     raise BetNotOpenError(bet_id)
-                user_id, game, stake = row
-                credit = stake if refund else payout
+                user_id, game, stake, lobby_id = row
+                if not refund:
+                    credit = payout
+                elif game == "poker" and lobby_id is not None:
+                    credit = await self._poker_refund_amount(lobby_id, stake)
+                else:
+                    credit = stake
                 status = "refunded" if refund else "settled"
                 # Conditioned on status = 'open', so a racing second close can never
                 # double-pay.
@@ -1137,6 +1142,40 @@ class Database:
             raise ValueError("settle_bet payout must not be negative")
         return await self._finish_bet(bet_id, payout, refund=False)
 
+    async def _close_bet_with_payout(
+        self, bet_id: int, user_id: int, game: str, stake: int, payout: int, now: int
+    ) -> int:
+        """Settle one open bet inside a caller-held transaction; returns the owner's balance.
+
+        Credits `payout`, records a win/push/payout row when it is above zero, and raises
+        BetNotOpenError if the bet is no longer open. The caller commits.
+        """
+        cursor = await self.conn.execute(
+            "UPDATE gambling_bets SET status = 'settled', payout = ? "
+            "WHERE id = ? AND status = 'open';",
+            (payout, bet_id),
+        )
+        if cursor.rowcount == 0:
+            raise BetNotOpenError(bet_id)
+        if payout > 0:
+            await self.conn.execute(
+                "UPDATE wallets SET balance = balance + ? WHERE user_id = ?;",
+                (payout, user_id),
+            )
+        wallet = await self.get_wallet(user_id)
+        assert wallet is not None
+        if payout > 0:
+            if payout > stake:
+                prefix = "win"
+            elif payout == stake:
+                prefix = "push"
+            else:
+                prefix = "payout"
+            await self._insert_transaction(
+                user_id, payout, f"{prefix}:{game}", None, wallet.balance, now
+            )
+        return wallet.balance
+
     async def settle_lobby(self, payouts: Mapping[int, int]) -> dict[int, int]:
         """Settle every bet of one table in ONE transaction; returns {bet_id: owner's new balance}.
 
@@ -1172,35 +1211,83 @@ class Database:
             balances: dict[int, int] = {}
             for bet_id, payout in payouts.items():
                 user_id, _, stake = rows[bet_id]
-                cursor = await self.conn.execute(
-                    "UPDATE gambling_bets SET status = 'settled', payout = ? "
-                    "WHERE id = ? AND status = 'open';",
-                    (payout, bet_id),
+                balances[bet_id] = await self._close_bet_with_payout(
+                    bet_id, user_id, game, stake, payout, now
                 )
-                if cursor.rowcount == 0:
-                    raise BetNotOpenError(bet_id)
-                if payout > 0:
-                    await self.conn.execute(
-                        "UPDATE wallets SET balance = balance + ? WHERE user_id = ?;",
-                        (payout, user_id),
-                    )
-                wallet = await self.get_wallet(user_id)
-                assert wallet is not None
-                if payout > 0:
-                    if payout > stake:
-                        prefix = "win"
-                    elif payout == stake:
-                        prefix = "push"
-                    else:
-                        prefix = "payout"
-                    await self._insert_transaction(
-                        user_id, payout, f"{prefix}:{game}", None, wallet.balance, now
-                    )
-                balances[bet_id] = wallet.balance
             await self._credit_pot(game, lobby_house_take(game, stakes, amounts), now)
             await self.conn.commit()
         return balances
 
+    async def cash_out_lobby(self, lobby_id: str, payouts: Mapping[int, int]) -> dict[int, int]:
+        """Pay out poker seats of one table (one seat leaving, or the final settle) atomically.
+
+        `payouts` maps bet id to the Renown paid for that seat's chips. Unlike `settle_lobby`, a
+        seat may be paid more than its own buy-in; the limit is the whole table: everything paid
+        so far plus this call may not exceed the sum of all buy-ins. Poker has no rake, so
+        nothing goes to the lottery pot. Returns {bet_id: owner's new balance}.
+        """
+        if not payouts:
+            raise ValueError("cash_out_lobby needs at least one bet")
+        if any(amount < 0 for amount in payouts.values()):
+            raise ValueError("cash_out_lobby payouts must not be negative")
+        now = int(time.time())
+        async with self._write_tx():
+            rows: dict[int, tuple[int, int]] = {}
+            for bet_id in payouts:
+                async with self.conn.execute(
+                    "SELECT user_id, game, stake, lobby_id FROM gambling_bets "
+                    "WHERE id = ? AND status = 'open';",
+                    (bet_id,),
+                ) as cursor:
+                    row = await cursor.fetchone()
+                if row is None:
+                    raise BetNotOpenError(bet_id)
+                if row[1] != "poker" or row[3] != lobby_id:
+                    raise ValueError("only poker bets of this table can be cashed out here")
+                rows[bet_id] = (row[0], row[2])
+            total, paid = await self._poker_table_totals(lobby_id)
+            if paid + sum(payouts.values()) > total:
+                raise ValueError("that would pay out more than the table holds")
+            balances: dict[int, int] = {}
+            for bet_id, payout in payouts.items():
+                user_id, stake = rows[bet_id]
+                balances[bet_id] = await self._close_bet_with_payout(
+                    bet_id, user_id, "poker", stake, payout, now
+                )
+            await self.conn.commit()
+        return balances
+
+    async def _poker_table_totals(self, lobby_id: str) -> tuple[int, int]:
+        """(all buy-ins, Renown already paid or refunded) for one poker table."""
+        async with self.conn.execute(
+            "SELECT COALESCE(SUM(stake), 0), "
+            "COALESCE(SUM(CASE WHEN status = 'open' THEN 0 ELSE payout END), 0) "
+            "FROM gambling_bets WHERE lobby_id = ?;",
+            (lobby_id,),
+        ) as cursor:
+            row = await cursor.fetchone()
+        assert row is not None
+        return row[0], row[1]
+
+    async def _poker_refund_amount(self, lobby_id: str, stake: int) -> int:
+        """What an open poker bet is refunded: its buy-in, capped to its share of what is left.
+
+        When some seats were already paid (maybe more than their buy-in), the open seats can
+        only share the rest of the table, or a restart would pay out more than was bought in.
+        """
+        total, paid = await self._poker_table_totals(lobby_id)
+        async with self.conn.execute(
+            "SELECT COALESCE(SUM(stake), 0) FROM gambling_bets "
+            "WHERE lobby_id = ? AND status = 'open';",
+            (lobby_id,),
+        ) as cursor:
+            row = await cursor.fetchone()
+        assert row is not None
+        open_stakes = row[0]
+        if open_stakes <= 0:
+            return 0
+        return max(0, min(stake, (total - paid) * stake // open_stakes))
+
     async def refund_bet(self, bet_id: int) -> int:
         """Return an open bet's stake to the wallet; returns the new balance."""
         return await self._finish_bet(bet_id, 0, refund=True)
```

- [ ] **Step 4: Run the new and the neighbouring tests**

Run: `python -m pytest -q tests/test_poker_cash_out.py tests/test_gambling_settle_lobby.py tests/test_gambling_escrow.py tests/test_table_manager.py`
Expected: all pass (the `settle_lobby` refactor must not change its behaviour).

- [ ] **Step 5: Lint and commit**

```bash
git add -A src tests
git commit -m "feat(poker): pay poker seats mid-game and cap refunds to what is left" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: TableManager cash-out and settle

**Files:** modify `src/yokai/gambling/tables.py`; create `tests/test_poker_table_manager.py`.

**Interfaces:**
- Consumes: Task 2's `Lobby.paid_out / remaining_seats / mark_paid_out` and Task 3's `Database.cash_out_lobby`.
- Produces:
  - `await TableManager.cash_out(lobby: Lobby, user_id: int, chips: int) -> int` (new balance). `LobbyClosedError` unless the lobby is `RUNNING`; `NotAtTableError` for a stranger; `ValueError` if already paid. Releases that user's claim. A database error propagates (the caller's `finish_table` then aborts and refunds).
  - `await TableManager.settle_poker(lobby: Lobby, payouts: Mapping[int, int]) -> dict[int, int]`: `payouts` must cover exactly `lobby.remaining_seats` and, with `lobby.paid_out`, total `lobby.pot` (`ValueError` otherwise, table untouched). Pays them in one transaction and closes the lobby `SETTLED`; if the database call fails, the remaining seats are refunded (capped) and the lobby is `CANCELLED`.
  - `_finish` and `_refund_all_locked` act only on `remaining_seats`, so seats paid earlier are never released or refunded twice.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_poker_table_manager.py`:

Apply this diff (save it to a `.patch` file in the scratchpad and run `git apply --ignore-whitespace <file>` from the repo root, or make the same edits by hand):

```diff
diff --git a/tests/test_poker_table_manager.py b/tests/test_poker_table_manager.py
new file mode 100644
index 0000000..f04eecd
--- /dev/null
+++ b/tests/test_poker_table_manager.py
@@ -0,0 +1,183 @@
+"""TableManager poker money: seats cash out mid-game, the table settles what is left."""
+
+from __future__ import annotations
+
+from pathlib import Path
+from unittest.mock import AsyncMock, patch
+
+import pytest
+
+from tests.table_helpers import ALL_PLAYABLE, CHANNEL, announce_ok, balance_of, world
+from yokai.errors import InvalidGameChoiceError, LobbyClosedError, NotAtTableError
+from yokai.gambling.lobby import Lobby, LobbyState
+from yokai.gambling.tables import TableManager
+
+
+async def _running_poker(manager: TableManager, stake: int = 100) -> Lobby:
+    """Players 1, 2 and 3 are seated with `stake` each and the table has started."""
+    lobby = await manager.open_table(CHANNEL, "poker", 1, stake, announce_ok)
+    await manager.join(lobby, 2, None)
+    await manager.join(lobby, 3, None)
+    await manager.begin(lobby)
+    return lobby
+
+
+def _poker_world(tmp_path: Path):  # type: ignore[no-untyped-def]
+    return world(tmp_path, game="poker", specs=ALL_PLAYABLE)
+
+
+@pytest.mark.asyncio
+async def test_a_busted_player_cashes_out_for_nothing_and_is_freed(tmp_path: Path) -> None:
+    async with _poker_world(tmp_path) as (db, manager, active):
+        lobby = await _running_poker(manager)
+        assert await manager.cash_out(lobby, 2, 0) == 900
+        assert lobby.paid_out == {2: 0} and lobby.state is LobbyState.RUNNING
+        assert not active.is_active(2) and active.is_active(1) and active.is_active(3)
+
+
+@pytest.mark.asyncio
+async def test_a_winner_cashes_out_with_more_than_their_buy_in(tmp_path: Path) -> None:
+    async with _poker_world(tmp_path) as (db, manager, active):
+        lobby = await _running_poker(manager)
+        assert await manager.cash_out(lobby, 1, 250) == 1150
+        assert await balance_of(db, 1) == 1150 and not active.is_active(1)
+
+
+@pytest.mark.asyncio
+async def test_cashing_out_needs_a_running_table_a_seat_and_a_seat_not_yet_paid(
+    tmp_path: Path,
+) -> None:
+    async with _poker_world(tmp_path) as (db, manager, _):
+        lobby = await manager.open_table(CHANNEL, "poker", 1, 100, announce_ok)
+        await manager.join(lobby, 2, None)
+        with pytest.raises(LobbyClosedError):
+            await manager.cash_out(lobby, 2, 0)  # still open, not started
+        await manager.begin(lobby)
+        with pytest.raises(NotAtTableError):
+            await manager.cash_out(lobby, 3, 0)
+        await manager.cash_out(lobby, 2, 0)
+        with pytest.raises(ValueError):
+            await manager.cash_out(lobby, 2, 0)
+        assert await balance_of(db, 2) == 900
+
+
+@pytest.mark.asyncio
+async def test_settling_pays_the_seats_still_at_the_table_and_closes_it(tmp_path: Path) -> None:
+    async with _poker_world(tmp_path) as (db, manager, active):
+        lobby = await _running_poker(manager)
+        await manager.cash_out(lobby, 2, 0)
+        balances = await manager.settle_poker(lobby, {1: 200, 3: 100})
+        assert balances == {1: 1100, 3: 1000}
+        assert lobby.state is LobbyState.SETTLED
+        assert [await balance_of(db, u) for u in (1, 2, 3)] == [1100, 900, 1000]
+        assert not any(active.is_active(u) for u in (1, 2, 3))
+        assert await db.refund_open_bets() == 0 and await db.get_pot() == 0
+        row = await db.get_multiplayer_channel(CHANNEL)
+        assert row is not None and row.table_active is False
+
+
+@pytest.mark.asyncio
+async def test_a_table_where_everyone_already_left_settles_with_no_payouts(
+    tmp_path: Path,
+) -> None:
+    async with _poker_world(tmp_path) as (db, manager, active):
+        lobby = await _running_poker(manager)
+        await manager.cash_out(lobby, 1, 150)
+        await manager.cash_out(lobby, 2, 150)
+        await manager.cash_out(lobby, 3, 0)
+        assert await manager.settle_poker(lobby, {}) == {}
+        assert lobby.state is LobbyState.SETTLED
+
+
+@pytest.mark.asyncio
+async def test_settling_must_cover_exactly_the_remaining_seats(tmp_path: Path) -> None:
+    async with _poker_world(tmp_path) as (db, manager, _):
+        lobby = await _running_poker(manager)
+        await manager.cash_out(lobby, 2, 0)
+        with pytest.raises(ValueError):
+            await manager.settle_poker(lobby, {1: 300})  # seat 3 is missing
+        with pytest.raises(ValueError):
+            await manager.settle_poker(lobby, {1: 100, 2: 100, 3: 100})  # seat 2 already paid
+        assert lobby.state is LobbyState.RUNNING
+
+
+@pytest.mark.asyncio
+async def test_settling_must_pay_out_exactly_what_was_bought_in(tmp_path: Path) -> None:
+    async with _poker_world(tmp_path) as (db, manager, _):
+        lobby = await _running_poker(manager)
+        await manager.cash_out(lobby, 2, 50)
+        with pytest.raises(ValueError):
+            await manager.settle_poker(lobby, {1: 200, 3: 100})  # 350 for a 300 table
+        with pytest.raises(ValueError):
+            await manager.settle_poker(lobby, {1: 100, 3: 100})  # 250: chips would vanish
+        assert lobby.state is LobbyState.RUNNING
+        assert await db.get_pot() == 0
+
+
+@pytest.mark.asyncio
+async def test_a_settled_table_cannot_be_settled_again(tmp_path: Path) -> None:
+    async with _poker_world(tmp_path) as (db, manager, _):
+        lobby = await _running_poker(manager)
+        await manager.settle_poker(lobby, {1: 100, 2: 100, 3: 100})
+        with pytest.raises(LobbyClosedError):
+            await manager.settle_poker(lobby, {1: 100, 2: 100, 3: 100})
+        assert await balance_of(db, 1) == 1000
+
+
+@pytest.mark.asyncio
+async def test_someone_who_left_and_started_another_game_keeps_that_claim(tmp_path: Path) -> None:
+    async with _poker_world(tmp_path) as (db, manager, active):
+        lobby = await _running_poker(manager)
+        await manager.cash_out(lobby, 2, 0)
+        active.claim(2)  # player 2 joined something else after leaving the table
+        await manager.settle_poker(lobby, {1: 150, 3: 150})
+        assert active.is_active(2)
+
+
+@pytest.mark.asyncio
+async def test_a_failed_final_settlement_refunds_only_what_is_left_and_leaves_claims(
+    tmp_path: Path,
+) -> None:
+    async with _poker_world(tmp_path) as (db, manager, active):
+        lobby = await _running_poker(manager)
+        await manager.cash_out(lobby, 1, 200)  # 100 of the 300 is left for players 2 and 3
+        active.claim(1)  # player 1 is already in another game
+        with patch.object(db, "cash_out_lobby", new=AsyncMock(side_effect=RuntimeError("disk"))):
+            with pytest.raises(RuntimeError):
+                await manager.settle_poker(lobby, {2: 50, 3: 50})
+        assert lobby.state is LobbyState.CANCELLED
+        assert [await balance_of(db, u) for u in (1, 2, 3)] == [1100, 950, 950]
+        assert active.is_active(1) and not active.is_active(2) and not active.is_active(3)
+        assert await db.refund_open_bets() == 0
+
+
+@pytest.mark.asyncio
+async def test_aborting_after_a_cash_out_never_creates_chips(tmp_path: Path) -> None:
+    async with _poker_world(tmp_path) as (db, manager, _):
+        lobby = await _running_poker(manager)
+        await manager.cash_out(lobby, 1, 300)  # one player won everything and left
+        await manager.abort(lobby)
+        await manager.abort(lobby)  # idempotent
+        assert [await balance_of(db, u) for u in (1, 2, 3)] == [1200, 900, 900]
+        assert lobby.state is LobbyState.CANCELLED
+
+
+@pytest.mark.asyncio
+async def test_an_open_poker_table_refunds_in_full_when_cancelled(tmp_path: Path) -> None:
+    async with _poker_world(tmp_path) as (db, manager, active):
+        lobby = await manager.open_table(CHANNEL, "poker", 1, 100, announce_ok)
+        await manager.join(lobby, 2, None)
+        await manager.cancel(lobby, 1, is_admin=False)
+        assert [await balance_of(db, u) for u in (1, 2)] == [1000, 1000]
+        assert not active.is_active(1) and not active.is_active(2)
+
+
+@pytest.mark.asyncio
+async def test_poker_cannot_be_opened_until_its_table_ships_and_costs_nothing(
+    tmp_path: Path,
+) -> None:
+    """Guard: with the real game specs poker is listed but not playable (phase P3 flips it)."""
+    async with world(tmp_path, game="poker") as (db, manager, active):
+        with pytest.raises(InvalidGameChoiceError):
+            await manager.open_table(CHANNEL, "poker", 1, 100, announce_ok)
+        assert await balance_of(db, 1) == 1000 and not active.is_active(1)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest -q tests/test_poker_table_manager.py`
Expected: `11 failed, 2 passed` (`AttributeError: 'TableManager' object has no attribute 'cash_out'`; the two passing tests are the plain cancel and the "cannot be opened yet" guard, which check existing behaviour).

- [ ] **Step 3: Implement**

Apply this diff (save it to a `.patch` file in the scratchpad and run `git apply --ignore-whitespace <file>` from the repo root, or make the same edits by hand):

```diff
diff --git a/src/yokai/gambling/tables.py b/src/yokai/gambling/tables.py
index d98a8fe..3e7c102 100644
--- a/src/yokai/gambling/tables.py
+++ b/src/yokai/gambling/tables.py
@@ -19,6 +19,7 @@ from yokai.errors import (
     BetNotOpenError,
     InvalidGameChoiceError,
     LobbyClosedError,
+    NotAtTableError,
     NotHostError,
     TableBusyError,
 )
@@ -276,6 +277,56 @@ class TableManager:
             await self._mark_channel_idle(lobby)
             return {user_id: balances[seat.bet_id] for user_id, seat in lobby.seats.items()}
 
+    # ---- poker ---------------------------------------------------------------------------
+
+    async def cash_out(self, lobby: Lobby, user_id: int, chips: int) -> int:
+        """Pay one poker seat its chips now (it left or busted); returns their new balance.
+
+        The rest of the table keeps playing. The seat's one-game claim is released, so they can
+        start another game right away.
+        """
+        async with lobby.lock:
+            if lobby.state is not LobbyState.RUNNING:
+                raise LobbyClosedError()
+            if user_id not in lobby.seats:
+                raise NotAtTableError()
+            if user_id in lobby.paid_out:
+                raise ValueError("this seat was already paid out")
+            seat = lobby.seats[user_id]
+            balances = await self.db.cash_out_lobby(lobby.id, {seat.bet_id: chips})
+            lobby.mark_paid_out(user_id, chips)
+            self.active_games.release(user_id)
+            return balances[seat.bet_id]
+
+    async def settle_poker(self, lobby: Lobby, payouts: Mapping[int, int]) -> dict[int, int]:
+        """End a poker table: pay the seats still at it and close. Returns their new balances.
+
+        `payouts` must cover exactly the seats not yet cashed out, and together with what was
+        already paid it must equal the table's buy-ins (poker has no rake).
+        """
+        async with lobby.lock:
+            if lobby.state is not LobbyState.RUNNING:
+                raise LobbyClosedError()
+            remaining = lobby.remaining_seats
+            if set(payouts) != set(remaining):
+                raise ValueError("payouts must cover every seat still at the table, and only those")
+            if any(amount < 0 for amount in payouts.values()):
+                raise ValueError("payouts must not be negative")
+            if sum(lobby.paid_out.values()) + sum(payouts.values()) != lobby.pot:
+                raise ValueError("a poker table must pay out exactly what was bought in")
+            balances: dict[int, int] = {}
+            if payouts:
+                by_bet = {remaining[user_id].bet_id: amount for user_id, amount in payouts.items()}
+                try:
+                    paid = await self.db.cash_out_lobby(lobby.id, by_bet)
+                except BaseException:
+                    await self._refund_all_locked(lobby, LobbyState.CANCELLED)
+                    raise
+                balances = {user_id: paid[remaining[user_id].bet_id] for user_id in payouts}
+            self._finish(lobby, LobbyState.SETTLED)
+            await self._mark_channel_idle(lobby)
+            return balances
+
     async def refund_round(self, lobby: Lobby) -> dict[int, int]:
         """End a running table by returning every stake (a tie); returns new balances."""
         async with lobby.lock:
@@ -317,7 +368,7 @@ class TableManager:
 
     def _finish(self, lobby: Lobby, state: LobbyState) -> None:
         lobby.close(state)
-        for user_id in lobby.seats:
+        for user_id in lobby.remaining_seats:  # seats paid out earlier were released then
             self.active_games.release(user_id)
 
     async def _refund_seat(self, seat: Seat) -> tuple[bool, Optional[int]]:
@@ -352,7 +403,7 @@ class TableManager:
         balances: dict[int, int] = {}
         confirmed: set[int] = set()
         try:
-            for user_id, seat in list(lobby.seats.items()):
+            for user_id, seat in list(lobby.remaining_seats.items()):
                 closed, balance = await self._refund_seat(seat)
                 if balance is not None:
                     balances[user_id] = balance
```

- [ ] **Step 4: Run them to verify they pass**

Run: `python -m pytest -q tests/test_poker_table_manager.py tests/test_table_manager.py`
Expected: all pass.

- [ ] **Step 5: Lint and commit**

```bash
git add -A src tests
git commit -m "feat(poker): cash seats out mid-game and settle the table" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Spec, TASKS.md and phase verification

**Files:** modify `docs/superpowers/specs/2026-10-10-poker-design.md`, `TASKS.md`.

- [ ] **Step 1: Update the docs**

Apply this diff (save it to a `.patch` file in the scratchpad and run `git apply --ignore-whitespace <file>` from the repo root, or make the same edits by hand):

```diff
diff --git a/TASKS.md b/TASKS.md
index 661c457..5fe768e 100644
--- a/TASKS.md
+++ b/TASKS.md
@@ -560,3 +560,28 @@ Plan: `docs/superpowers/plans/2026-10-10-poker-p1-engine.md`.
   database or the network.
 
 **STOP — owner review.** P2 (money: `cash_out`) is planned after this review.
+
+### P2 — Money
+
+Plan: `docs/superpowers/plans/2026-10-10-poker-p2-money.md`.
+
+- [ ] P2.1 `GAMBLING_POKER_MAX_HANDS` setting and wiring.
+- [ ] P2.2 Poker game spec (listed, not yet playable) and lobby cash-out bookkeeping.
+- [ ] P2.3 `Database.cash_out_lobby` and capped poker refunds.
+- [ ] P2.4 `TableManager.cash_out` and `settle_poker`.
+
+**Acceptance criteria (P2)**
+- A poker seat can be paid its chips mid-game, including more than its buy-in, and the one-game
+  claim is released; the table keeps running.
+- The table-wide limit holds: Renown paid out never exceeds the sum of buy-ins, and a batch that
+  would break it pays nobody.
+- The final settle must pay exactly what was bought in; seats paid earlier are never paid or
+  refunded again, and a claim they took since is left alone.
+- After a cash-out, an abort, a failed settlement or a restart refunds the remaining seats only
+  their share of what is left, so no Renown is created.
+- Poker cash-outs add nothing to the lottery pot.
+- Poker is listed in the `/admin set_multiplayer` dropdown but cannot be opened yet (P3).
+- `pytest`, `ruff check` and `ruff format --check` are clean.
+
+**STOP — owner review.** P3 (table UI) is planned after this review.
+
diff --git a/docs/superpowers/specs/2026-10-10-poker-design.md b/docs/superpowers/specs/2026-10-10-poker-design.md
index 6e9f32c..6357846 100644
--- a/docs/superpowers/specs/2026-10-10-poker-design.md
+++ b/docs/superpowers/specs/2026-10-10-poker-design.md
@@ -38,7 +38,8 @@ Success criteria:
 ```
 gambling/engines/poker.py     pure: deck, evaluator, betting state machine, side pots
 gambling/tables.py            + TableManager.cash_out(), + poker table settle
-storage/db.py                 + Database.cash_out_bet() (lobby-level conservation check)
+storage/db.py                 + Database.cash_out_lobby() (lobby-level conservation check),
+                                capped poker refunds
 ui/poker_views.py             public table view, My cards, Raise modal, runner (run_poker)
 ui/embeds.py                  + poker table / result / hand-recap builders
 gambling/lobby.py             + GameSpec "poker" (min 2, max 6, equal_stakes)
@@ -71,21 +72,29 @@ Buy-in goes through the existing `open_table` / `join` (escrow, one-active-game
 runs in memory.
 
 - `TableManager.cash_out(lobby, user_id, chips)`: under `lobby.lock`, pays one seat its chips and
-  releases that user's one-game claim. Calls `Database.cash_out_bet(bet_id, chips, lobby_id)`.
-- `Database.cash_out_bet`: in one transaction, marks the bet settled with `payout = chips`, credits the
-  wallet, writes the usual `win:` / `push:` / `payout:` transaction row, and **checks conservation at
-  lobby level**: `payouts already settled in this lobby + chips <= total stakes of this lobby`.
-  This replaces the per-seat `payout <= stake` rule of `settle_lobby` for poker, because a winner may
-  legitimately leave with more than their own buy-in. Idempotent: a bet that is not open raises
-  `BetNotOpenError` and changes nothing.
-- When the table ends, the manager settles the remaining seats with the same lobby-level check and
-  finishes the lobby (`SETTLED`). Final invariant asserted in the manager: total paid out equals total
-  buy-ins.
-- Abort/error path: `abort` refunds every **still-open** bet at its original buy-in. A seat that was
-  already cashed out stays paid. (Bets already settled are skipped by the existing
-  `BetNotOpenError`-tolerant refund path.)
-- `lobby.seats` keeps cashed-out users out of the active player list (the runner tracks
-  `active_players`), while `lobby.pot` stays the total of buy-ins for conservation checks.
+  releases that user's one-game claim. Calls `Database.cash_out_lobby(lobby_id, {bet_id: chips})`.
+- `Database.cash_out_lobby(lobby_id, payouts)` (one seat leaving, or the final settle): in one
+  transaction, marks each bet settled with `payout = chips`, credits the wallet, writes the usual
+  `win:` / `push:` / `payout:` transaction row, and **checks conservation at lobby level**:
+  `Renown already paid or refunded in this lobby + this call <= total buy-ins of this lobby`. This
+  replaces the per-batch `payout <= stake` rule of `settle_lobby` for poker, because a winner may
+  legitimately leave with more than their own buy-in. Poker has no rake, so nothing goes to the
+  lottery pot. A bet that is not open raises `BetNotOpenError` and changes nothing; a bet from
+  another table or another game raises `ValueError`.
+- When the table ends, `TableManager.settle_poker(lobby, payouts)` pays the seats still at the table
+  (`payouts` must cover exactly those) through the same method and finishes the lobby (`SETTLED`).
+  Invariant asserted in the manager: total paid out equals total buy-ins.
+- `Lobby.paid_out` (user id -> Renown) records seats already paid. `Lobby.remaining_seats` is the
+  rest. `_finish` and the refund path use `remaining_seats`, so a seat that left earlier is never
+  refunded again and a new game that player started afterwards keeps its claim. `lobby.pot` stays the
+  total of buy-ins.
+- **Refund cap (found while planning P2):** refunding a poker bet at its full buy-in after another
+  seat cashed out with winnings would pay out more than was bought in. So an open poker bet is
+  refunded `min(stake, (all buy-ins - already paid) * stake // open buy-ins)`. With nothing paid
+  yet this is the full buy-in. This applies on every refund path: `abort`, a failed settlement and
+  the startup sweep.
+- Abort/error path: `abort` refunds every **still-open** bet (capped as above). A seat that was
+  already cashed out stays paid.
 
 ### 3.3 UI and runner (`ui/poker_views.py`)
 
@@ -108,7 +117,8 @@ Follows `bjtable_views.py`: one public message edited in place, a lock around st
 
 ### 3.4 Restart and failure handling
 
-No new persistence. The existing startup sweep refunds every open bet; `TablesCog.reconcile` already
+No new persistence. The existing startup sweep refunds every open bet (poker bets capped to what is
+left of the table, see §3.2); `TablesCog.reconcile` already
 closes a table left marked active and reposts the Play embed. A player who leaves the server mid-hand
 is folded and cashed out after the hand.
 
```

Then tick the four P2 boxes in `TASKS.md` (`- [x]`).

- [ ] **Step 2: Full verification**

Run, in order: `python -m pytest -q`, `python -m ruff check .`, `python -m ruff format --check .`.
Expected: all clean. Report the actual test count.

- [ ] **Step 3: Commit and tag**

```bash
git add docs TASKS.md
git commit -m "docs(poker): record the P2 money design and tasks" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
git tag checkpoint/poker-p2
```

Then stop and wait for the owner's review (working agreement 2). Summarise what changed and list anything unverified. *(Manual, real Discord, after P3: leave a poker table mid-game with winnings, restart the bot, and check balances add up.)*

---

## Self-review

- **Spec coverage (§3.2, §4):** per-seat cash-out above the buy-in, table-wide conservation, no rake, claim release, final settle, abort semantics and the setting (Tasks 1 to 4); the refund cap is new and added to the spec in Task 5. Game spec listing (Task 2). Views, runner and embeds are P3.
- **Placeholders:** none. Every diff was produced by running the work on a scratch branch (1934+ tests, ruff clean) before this plan was written.
- **Type consistency:** `cash_out_lobby`, `cash_out`, `settle_poker`, `paid_out`, `remaining_seats`, `mark_paid_out` and `poker_max_hands` are spelled identically in the interface blocks and the diffs.
