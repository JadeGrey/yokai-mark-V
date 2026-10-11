# Poker P3: table UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make poker playable: a table opened from the Play embed that plays hand after hand on one Discord message, with action buttons, private hole cards, a Raise form, Leave, turn timers, and a final payout.

**Architecture:** A new pure `PokerTable` (`engines/pokertable.py`) keeps the table between hands (stacks, blinds from the buy-in, button, busts, leavers, idle timeouts, hand cap) on top of the P1 `PokerHand`. `ui/poker_views.py` adds the per-hand `PokerTableView` (same pattern as the blackjack table: one lock, an `acted` event, a deadline) and `run_poker`, which plays hands, pays out busts and leavers through P2's `TableManager.cash_out` and ends with `settle_poker`. Three new embeds go through `EmbedFactory`. Finally poker is registered as a runner and flipped to available.

**Tech Stack:** Python 3.12+, discord.py (views, modals), pytest, ruff. No new dependency.

**Spec:** `docs/superpowers/specs/2026-10-10-poker-design.md` (§2 decisions, §3.3 UI and runner, §3.4 failure handling). P1 (engine) and P2 (money) are done; P4 (wrap-up) comes after owner review.

## Global Constraints

- `from __future__ import annotations`, fully type-hinted, no bare `except`, logging not print (AGENTS.md).
- Every user-visible message goes through `EmbedFactory`; errors are ephemeral; `allowed_mentions` none, mentions only inside embeds; at most one emoji and one short quip per embed; error text plain.
- No suit glyphs anywhere (cards print as `As Kh 10d`); the one-emoji tests count them.
- Slash commands and privileged intents: none added. Buttons only act for the player whose turn it is; every interaction goes through the moderation `screen`.
- Hole cards are only ever sent ephemerally to their owner, and shown publicly only at a showdown.
- Whole-number Renown; poker has no rake; nothing from poker goes to the lottery pot.
- No new dependency (AGENTS.md rule 5).
- Never mark a task done without running `python -m pytest -q`, `python -m ruff check .` and `python -m ruff format --check .` (PowerShell, repo root, venv active).
- Commit per task; messages end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

## Review Focus

- A player's hole cards must never appear in a public embed during play or after a fold-win; only the owner sees them via My cards (tests in Task 3). Check the recap path too.
- A player who vanishes: a missed turn checks or folds, two in a row remove them, Leave off-turn folds them when it comes round (tests in Tasks 1 and 3). The bot deliberately does not watch server membership (see Rulings in the spec's §3.4).
- Chips and Renown are conserved over a whole session with busts, leavers and the hand cap (tests in Tasks 1 and 3); the lottery pot stays 0.
- A button press landing at the same moment as a timeout, or after the hand closed, must not act twice or act on a finished hand. The runner reuses the blackjack table's lock-plus-flag pattern; there is no dedicated race test here, so this needs a careful read of `PokerTableView._act` and `_play_hand`.
- A failure at any point (a database error, a deleted message) refunds the seats still at the table and tells the channel (test in Task 3).
- The Raise form answers a modal-submit by editing the table message. That works in discord.py but is only verified here with mocks, so it needs the manual Discord check in `TASKS.md`.

## File Structure

- Create `src/yokai/gambling/engines/pokertable.py`: `blinds_for`, `CashOut`, `PokerTable`, `IDLE_LIMIT`.
- Modify `src/yokai/ui/embeds.py`: `poker_table`, `poker_hand_result`, `poker_final`.
- Create `src/yokai/ui/poker_views.py`: `PokerTableView`, `RaiseModal`, `run_poker`, `parse_chips`.
- Modify `src/yokai/ui/table_views.py` (register the runner) and `src/yokai/gambling/lobby.py` (poker becomes available).
- Create `tests/test_poker_table_engine.py`, `tests/test_poker_embeds.py`, `tests/poker_helpers.py`, `tests/test_poker_views.py`; modify `tests/test_poker_engine.py` (use the shared deck helper), `tests/test_lobby.py`, `tests/test_poker_table_manager.py`.
- Modify `docs/superpowers/specs/2026-10-10-poker-design.md`, `README.md`, `TASKS.md`.

---

### Task 1: The multi-hand table

**Files:** create `src/yokai/gambling/engines/pokertable.py`, `tests/test_poker_table_engine.py`.

**Interfaces:**
- Consumes: P1's `PokerHand`, `HandResult`, `build_deck` (`yokai.gambling.engines.poker`); `NotAtTableError`.
- Produces:
  - `blinds_for(buy_in: int) -> tuple[int, int]` (1% and 2% rounded up, floors 1 and 2); `IDLE_LIMIT = 2`.
  - `CashOut(user_id: int, chips: int, reason: str)`, reason one of `"bust"`, `"left"`, `"inactive"`.
  - `PokerTable(order: Sequence[int], buy_in: int, max_hands: int, rng_factory: Callable[[], random.Random])` with attributes `stacks`, `hand_no`, `button`, `leaving`, `small_blind`, `big_blind`, `max_hands`, `buy_in`; properties `seated`, `is_finished`; methods `request_leave(user_id)`, `record_action(user_id)`, `record_timeout(user_id) -> bool`, `take_cash_outs() -> list[CashOut]`, `start_hand() -> PokerHand`, `finish_hand(result: HandResult)`, `final_payouts() -> dict[int, int]`.
  - Rules: the button starts at the first seat and moves to the next seated player each hand; `start_hand` refuses a finished table and a table with a broke or leaving seat still listed; `finish_hand` refuses a result that changes the chip total; the table ends at `max_hands` or with fewer than two players.

- [ ] **Step 1: Write the failing tests**

Apply this diff (save it to a `.patch` file in the scratchpad and run `git apply --ignore-whitespace <file>` from the repo root, or make the same edits by hand):

```diff
diff --git a/tests/test_poker_table_engine.py b/tests/test_poker_table_engine.py
new file mode 100644
index 0000000..7d3e784
--- /dev/null
+++ b/tests/test_poker_table_engine.py
@@ -0,0 +1,186 @@
+"""The multi-hand poker table: blinds from the buy-in, the button, busts, leavers, timeouts."""
+
+from __future__ import annotations
+
+import random
+
+import pytest
+
+from yokai.errors import NotAtTableError
+from yokai.gambling.engines.poker import HandResult
+from yokai.gambling.engines.pokertable import (
+    IDLE_LIMIT,
+    CashOut,
+    PokerTable,
+    blinds_for,
+)
+
+
+def make_table(users=(1, 2, 3), buy_in: int = 100, max_hands: int = 30) -> PokerTable:  # type: ignore[no-untyped-def]
+    return PokerTable(list(users), buy_in, max_hands, lambda: random.Random(7))
+
+
+def result(stacks: dict[int, int]) -> HandResult:
+    return HandResult(stacks=stacks, pots=(), shown={}, ranks={}, showdown=False)
+
+
+@pytest.mark.parametrize(
+    ("buy_in", "blinds"),
+    [(10, (1, 2)), (50, (1, 2)), (100, (1, 2)), (150, (2, 3)), (1000, (10, 20)), (5000, (50, 100))],
+)
+def test_blinds_are_one_and_two_percent_rounded_up_with_a_floor(
+    buy_in: int, blinds: tuple[int, int]
+) -> None:
+    assert blinds_for(buy_in) == blinds
+
+
+def test_a_new_table_gives_everyone_the_same_stack_and_the_blinds() -> None:
+    table = make_table(buy_in=1000)
+    assert table.stacks == {1: 1000, 2: 1000, 3: 1000}
+    assert (table.small_blind, table.big_blind) == (10, 20)
+    assert table.seated == [1, 2, 3] and table.hand_no == 0 and not table.is_finished
+
+
+def test_bad_tables_are_rejected() -> None:
+    with pytest.raises(ValueError):
+        PokerTable([1], 100, 30, lambda: random.Random(1))
+    with pytest.raises(ValueError):
+        PokerTable([1, 2], 0, 30, lambda: random.Random(1))
+    with pytest.raises(ValueError):
+        PokerTable([1, 2], 100, 0, lambda: random.Random(1))
+
+
+def test_the_button_starts_with_the_first_seat_and_moves_left_each_hand() -> None:
+    table = make_table()
+    buttons = []
+    for _ in range(4):
+        hand = table.start_hand()
+        buttons.append(hand.button)
+        table.finish_hand(result(dict(table.stacks)))
+    assert buttons == [1, 2, 3, 1]
+
+
+def test_each_hand_deals_the_current_stacks_with_the_table_blinds() -> None:
+    table = make_table(buy_in=1000)
+    hand = table.start_hand()
+    assert table.hand_no == 1
+    assert hand.order == [1, 2, 3] and hand.big_blind == 20
+    assert hand.stacks[2] == 990 and hand.stacks[3] == 980  # small blind 2, big blind 3
+
+
+def test_finishing_a_hand_moves_the_chips() -> None:
+    table = make_table()
+    table.start_hand()
+    table.finish_hand(result({1: 160, 2: 70, 3: 70}))
+    assert table.stacks == {1: 160, 2: 70, 3: 70}
+
+
+def test_finishing_a_hand_that_changes_the_chip_total_is_refused() -> None:
+    table = make_table()
+    table.start_hand()
+    with pytest.raises(ValueError):
+        table.finish_hand(result({1: 200, 2: 100, 3: 100}))
+    assert table.stacks == {1: 100, 2: 100, 3: 100}
+
+
+def test_a_busted_player_is_cashed_out_for_nothing_and_leaves_the_table() -> None:
+    table = make_table()
+    table.start_hand()
+    table.finish_hand(result({1: 200, 2: 100, 3: 0}))
+    assert table.take_cash_outs() == [CashOut(3, 0, "bust")]
+    assert table.seated == [1, 2] and 3 not in table.stacks
+    assert table.take_cash_outs() == []
+
+
+def test_a_player_who_left_is_cashed_out_with_their_chips_after_the_hand() -> None:
+    table = make_table()
+    table.start_hand()
+    table.request_leave(2)  # pressed Leave while the hand was running
+    table.finish_hand(result({1: 130, 2: 90, 3: 80}))
+    assert table.take_cash_outs() == [CashOut(2, 90, "left")]
+    assert table.seated == [1, 3]
+
+
+def test_leaving_needs_a_seat() -> None:
+    table = make_table()
+    with pytest.raises(NotAtTableError):
+        table.request_leave(9)
+
+
+def test_the_button_skips_seats_that_are_gone() -> None:
+    table = make_table(users=(1, 2, 3, 4))
+    table.start_hand()  # button 1
+    table.finish_hand(result({1: 100, 2: 0, 3: 100, 4: 200}))
+    table.take_cash_outs()
+    assert table.start_hand().button == 3  # 2 busted, so the button passes it
+
+
+def test_consecutive_timeouts_remove_a_player_but_an_action_resets_the_count() -> None:
+    table = make_table()
+    assert IDLE_LIMIT == 2
+    assert table.record_timeout(1) is False
+    table.record_action(1)
+    assert table.record_timeout(1) is False
+    assert table.record_timeout(1) is True
+    assert 1 in table.leaving
+    assert table.take_cash_outs() == [CashOut(1, 100, "inactive")]
+
+
+def test_the_table_ends_at_the_hand_cap() -> None:
+    table = make_table(max_hands=2)
+    for _ in range(2):
+        assert not table.is_finished
+        table.start_hand()
+        table.finish_hand(result(dict(table.stacks)))
+    assert table.is_finished
+
+
+def test_the_table_ends_when_fewer_than_two_players_are_left() -> None:
+    table = make_table(users=(1, 2))
+    table.start_hand()
+    table.finish_hand(result({1: 200, 2: 0}))
+    table.take_cash_outs()
+    assert table.is_finished and table.final_payouts() == {1: 200}
+
+
+def test_starting_a_hand_on_a_finished_table_is_refused() -> None:
+    table = make_table(max_hands=1)
+    table.start_hand()
+    table.finish_hand(result(dict(table.stacks)))
+    with pytest.raises(ValueError):
+        table.start_hand()
+
+
+def test_starting_a_hand_with_a_broke_or_leaving_seat_still_listed_is_refused() -> None:
+    table = make_table()
+    table.start_hand()
+    table.finish_hand(result({1: 200, 2: 100, 3: 0}))
+    with pytest.raises(ValueError):
+        table.start_hand()  # player 3 must be cashed out first
+
+
+def test_chips_are_conserved_across_a_whole_session() -> None:
+    table = make_table(users=(1, 2, 3, 4), buy_in=500, max_hands=40)
+    rng = random.Random(3)
+    paid = 0
+    while not table.is_finished:
+        for cash in table.take_cash_outs():
+            paid += cash.chips
+        if table.is_finished:
+            break
+        hand = table.start_hand()
+        while not hand.is_over:
+            user = hand.current
+            assert user is not None
+            if rng.random() < 0.4:
+                hand.all_in(user)
+            elif hand.legal_actions(user).can_check:
+                hand.check(user)
+            else:
+                hand.call(user)
+        table.finish_hand(hand.result())
+        if rng.random() < 0.2 and len(table.seated) > 2:
+            table.request_leave(rng.choice(table.seated))
+    paid += sum(c.chips for c in table.take_cash_outs())
+    paid += sum(table.final_payouts().values())
+    assert paid == 4 * 500
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest -q tests/test_poker_table_engine.py`
Expected: collection ERROR, `ModuleNotFoundError: No module named 'yokai.gambling.engines.pokertable'`.

- [ ] **Step 3: Implement**

Apply this diff (save it to a `.patch` file in the scratchpad and run `git apply --ignore-whitespace <file>` from the repo root, or make the same edits by hand):

```diff
diff --git a/src/yokai/gambling/engines/pokertable.py b/src/yokai/gambling/engines/pokertable.py
new file mode 100644
index 0000000..6e0d8f6
--- /dev/null
+++ b/src/yokai/gambling/engines/pokertable.py
@@ -0,0 +1,143 @@
+"""A poker cash-game table across many hands: stacks, blinds, the button, busts and leavers.
+
+Pure bookkeeping around `PokerHand`: no Discord and no database. The runner plays the hands,
+this keeps the table between them and says who is owed what when someone leaves.
+"""
+
+from __future__ import annotations
+
+import random
+from dataclasses import dataclass
+from typing import Callable, Optional, Sequence
+
+from yokai.errors import NotAtTableError
+from yokai.gambling.engines.poker import HandResult, PokerHand, build_deck
+
+IDLE_LIMIT = 2  # consecutive timed-out turns before a player is removed from the table
+
+
+def blinds_for(buy_in: int) -> tuple[int, int]:
+    """Small and big blind: 1% and 2% of the buy-in rounded up, never below 1 and 2."""
+    small = max(1, -(-buy_in // 100))
+    big = max(2, -(-(buy_in * 2) // 100))
+    return small, big
+
+
+@dataclass(frozen=True)
+class CashOut:
+    """A seat leaving the table with `chips`: it busted, asked to leave, or sat out."""
+
+    user_id: int
+    chips: int
+    reason: str  # "bust", "left" or "inactive"
+
+
+class PokerTable:
+    """Equal stacks at the start; hands are dealt one at a time until the table is finished."""
+
+    def __init__(
+        self,
+        order: Sequence[int],
+        buy_in: int,
+        max_hands: int,
+        rng_factory: Callable[[], random.Random],
+    ) -> None:
+        if len(set(order)) != len(order) or len(order) < 2:
+            raise ValueError("a poker table needs at least two different players")
+        if buy_in <= 0 or max_hands < 1:
+            raise ValueError("the buy-in and the hand cap must be positive")
+        self.order = list(order)
+        self.buy_in = buy_in
+        self.max_hands = max_hands
+        self.small_blind, self.big_blind = blinds_for(buy_in)
+        self.stacks: dict[int, int] = {user_id: buy_in for user_id in self.order}
+        self.hand_no = 0
+        self.button: Optional[int] = None
+        self.leaving: set[int] = set()
+        self._idle: dict[int, int] = {}
+        self._seated: list[int] = list(self.order)
+        self._rng_factory = rng_factory
+
+    @property
+    def seated(self) -> list[int]:
+        """Players still at the table, in seat order."""
+        return list(self._seated)
+
+    @property
+    def is_finished(self) -> bool:
+        return self.hand_no >= self.max_hands or len(self._seated) < 2
+
+    # ---- who is still playing --------------------------------------------------------------
+
+    def request_leave(self, user_id: int) -> None:
+        """Mark a player to be cashed out once the current hand is over."""
+        if user_id not in self._seated:
+            raise NotAtTableError()
+        self.leaving.add(user_id)
+
+    def record_action(self, user_id: int) -> None:
+        self._idle.pop(user_id, None)
+
+    def record_timeout(self, user_id: int) -> bool:
+        """Count a missed turn; True when it was the last one allowed and they are now leaving."""
+        self._idle[user_id] = self._idle.get(user_id, 0) + 1
+        if self._idle[user_id] >= IDLE_LIMIT:
+            self.leaving.add(user_id)
+            return True
+        return False
+
+    def take_cash_outs(self) -> list[CashOut]:
+        """Remove busted and leaving players from the table and say what each is owed."""
+        out: list[CashOut] = []
+        for user_id in list(self._seated):
+            chips = self.stacks[user_id]
+            if chips == 0:
+                reason = "bust"
+            elif user_id in self.leaving:
+                reason = "inactive" if self._idle.get(user_id, 0) >= IDLE_LIMIT else "left"
+            else:
+                continue
+            self._seated.remove(user_id)
+            del self.stacks[user_id]
+            out.append(CashOut(user_id, chips, reason))
+        return out
+
+    # ---- hands ----------------------------------------------------------------------------
+
+    def start_hand(self) -> PokerHand:
+        if self.is_finished:
+            raise ValueError("this table is finished")
+        if any(self.stacks[user_id] <= 0 or user_id in self.leaving for user_id in self._seated):
+            raise ValueError("cash out busted and leaving players before the next hand")
+        self.button = self._next_button()
+        self.hand_no += 1
+        stacks = {user_id: self.stacks[user_id] for user_id in self._seated}
+        return PokerHand(
+            stacks,
+            self._seated,
+            self.button,
+            self.small_blind,
+            self.big_blind,
+            build_deck(self._rng_factory()),
+        )
+
+    def finish_hand(self, result: HandResult) -> None:
+        """Take the stacks a finished hand ended with; the chip total must not change."""
+        before = sum(self.stacks[user_id] for user_id in result.stacks)
+        if set(result.stacks) - set(self.stacks) or sum(result.stacks.values()) != before:
+            raise ValueError("a hand must keep the chips it started with")
+        self.stacks.update(result.stacks)
+
+    def final_payouts(self) -> dict[int, int]:
+        """What every player still at the table is owed when it closes."""
+        return {user_id: self.stacks[user_id] for user_id in self._seated}
+
+    def _next_button(self) -> int:
+        if self.button is None:
+            return self._seated[0]
+        start = self.order.index(self.button)
+        for step in range(1, len(self.order) + 1):
+            candidate = self.order[(start + step) % len(self.order)]
+            if candidate in self._seated:
+                return candidate
+        raise ValueError("nobody is seated")  # unreachable: a table is never empty here
```

- [ ] **Step 4: Run them to verify they pass**

Run: `python -m pytest -q tests/test_poker_table_engine.py`
Expected: `22 passed`.

- [ ] **Step 5: Lint and commit**

Run `python -m ruff check .` and `python -m ruff format --check .` (both clean).

```bash
git add src tests
git commit -m "feat(poker): add the multi-hand table" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Poker embeds

**Files:** modify `src/yokai/ui/embeds.py`; create `tests/test_poker_embeds.py`.

**Interfaces:**
- Consumes: `EmbedFactory._create_base`, `_grid`, `Emoji.DICE`, `quip_bank`.
- Produces (`EmbedFactory` classmethods, all return `discord.Embed`):
  - `poker_table(hand_no, max_hands, street, board: Sequence[str], pot, small_blind, big_blind, seats: Sequence[tuple[int, int, int, str, bool]], deadline_ts: int | None, bot_avatar_url=None)`; seats are `(user, chips, street bet, status, is_button)`.
  - `poker_hand_result(hand_no, max_hands, board, awards: Sequence[tuple[int, Sequence[int], bool]], shown: Mapping[int, tuple[str, str]], stacks: Mapping[int, int], left: Sequence[tuple[int, int, str]], bot_avatar_url=None)`; awards are `(amount, winners, returned)`.
  - `poker_final(hands_played, max_hands, buy_in, results: Sequence[tuple[int, int]], bot_avatar_url=None)`; results are `(user, Renown paid)`.

- [ ] **Step 1: Write the failing tests**

Apply this diff (save it to a `.patch` file in the scratchpad and run `git apply --ignore-whitespace <file>` from the repo root, or make the same edits by hand):

```diff
diff --git a/tests/test_poker_embeds.py b/tests/test_poker_embeds.py
new file mode 100644
index 0000000..a847efe
--- /dev/null
+++ b/tests/test_poker_embeds.py
@@ -0,0 +1,118 @@
+"""The poker embeds obey the layout rules and show the table state without private cards."""
+
+from __future__ import annotations
+
+import pytest
+
+from tests.embed_helpers import all_embed_text, emoji_count, total_embed_length
+from yokai.ui.embeds import EmbedFactory
+
+SEATS = [
+    (1, 980, 20, "to act", True),
+    (2, 990, 10, "", False),
+    (3, 1000, 0, "folded", False),
+    (4, 0, 500, "all-in", False),
+]
+
+
+def _table(**overrides):  # type: ignore[no-untyped-def]
+    args = dict(
+        hand_no=3,
+        max_hands=30,
+        street="flop",
+        board=["As", "Kh", "10d"],
+        pot=530,
+        small_blind=10,
+        big_blind=20,
+        seats=SEATS,
+        deadline_ts=1_700_000_045,
+    )
+    args.update(overrides)
+    return EmbedFactory.poker_table(**args)
+
+
+def _recap(**overrides):  # type: ignore[no-untyped-def]
+    args = dict(
+        hand_no=3,
+        max_hands=30,
+        board=["As", "Kh", "10d", "2c", "9s"],
+        awards=[(300, [1], False), (40, [2, 3], False), (25, [4], True)],
+        shown={1: ("As Ad", "three of a kind"), 2: ("Kc Qd", "a pair")},
+        stacks={1: 1300, 2: 960, 3: 1020, 4: 25},
+        left=[(4, 25, "bust")],
+    )
+    args.update(overrides)
+    return EmbedFactory.poker_hand_result(**args)
+
+
+def _final(**overrides):  # type: ignore[no-untyped-def]
+    args = dict(
+        hands_played=12,
+        max_hands=30,
+        buy_in=1000,
+        results=[(1, 1500), (2, 1000), (3, 500), (4, 0)],
+    )
+    args.update(overrides)
+    return EmbedFactory.poker_final(**args)
+
+
+@pytest.mark.parametrize(
+    "label,embed",
+    [
+        ("table", _table()),
+        ("table-preflop", _table(street="preflop", board=[], deadline_ts=None)),
+        ("recap", _recap()),
+        ("recap-fold", _recap(shown={}, awards=[(30, [1], False)], left=[])),
+        ("final", _final()),
+        ("final-flat", _final(results=[(1, 1000), (2, 1000)])),
+    ],
+)
+def test_every_poker_embed_obeys_the_layout_rules(label: str, embed) -> None:  # type: ignore[no-untyped-def]
+    assert emoji_count(embed) <= 1, label
+    assert total_embed_length(embed) <= 6000, label
+    assert len(embed.fields) <= 25, label
+    text = all_embed_text(embed)
+    assert not any(glyph in text for glyph in "♠♥♦♣"), label
+
+
+def test_the_table_shows_board_pot_blinds_stacks_bets_and_who_is_up() -> None:
+    text = all_embed_text(_table())
+    assert "As Kh 10d" in text and "Flop" in text
+    assert "530" in text and "10/20" in text
+    assert "Hand 3/30" in text
+    assert "<@1>" in text and "980" in text and "to act" in text
+    assert "<@3>" in text and "folded" in text
+    assert "<@4>" in text and "all-in" in text
+    assert "<t:1700000045:R>" in text
+
+
+def test_the_dealer_button_is_marked_once() -> None:
+    assert all_embed_text(_table()).count("(D)") == 1
+
+
+def test_a_table_before_the_flop_says_so_and_has_no_countdown_when_nobody_is_up() -> None:
+    text = all_embed_text(_table(street="preflop", board=[], deadline_ts=None))
+    assert "No community cards yet" in text and "<t:" not in text
+
+
+def test_the_recap_names_winners_splits_returns_and_shows_hands() -> None:
+    text = all_embed_text(_recap())
+    assert "<@1>" in text and "300" in text
+    assert "split" in text and "40" in text
+    assert "back" in text and "25" in text  # the uncalled chips are returned, not "won"
+    assert "As Ad" in text and "three of a kind" in text
+    assert "<@4>" in text and "bust" in text
+
+
+def test_a_hand_won_without_a_showdown_reveals_no_cards() -> None:
+    text = all_embed_text(_recap(shown={}, awards=[(30, [1], False)], left=[]))
+    assert "wins" in text and "30" in text
+    assert "As Ad" not in text and "Kc Qd" not in text
+
+
+def test_the_final_embed_shows_each_players_result() -> None:
+    text = all_embed_text(_final())
+    assert "12/30" in text or "12 hands" in text
+    assert "<@1>" in text and "+500" in text
+    assert "<@3>" in text and "-500" in text
+    assert "<@2>" in text and "even" in text.lower()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest -q tests/test_poker_embeds.py`
Expected: collection ERROR, `AttributeError: type object 'EmbedFactory' has no attribute 'poker_table'`.

- [ ] **Step 3: Implement**

Apply this diff (save it to a `.patch` file in the scratchpad and run `git apply --ignore-whitespace <file>` from the repo root, or make the same edits by hand):

```diff
diff --git a/src/yokai/ui/embeds.py b/src/yokai/ui/embeds.py
index 8ef322a..b77c52d 100644
--- a/src/yokai/ui/embeds.py
+++ b/src/yokai/ui/embeds.py
@@ -1113,6 +1113,113 @@ Press **Play** to open a table.",
             fields=_grid(fields),
         )
 
+    @classmethod
+    def poker_table(
+        cls,
+        hand_no: int,
+        max_hands: int,
+        street: str,
+        board: Sequence[str],
+        pot: int,
+        small_blind: int,
+        big_blind: int,
+        seats: Sequence[
+            tuple[int, int, int, str, bool]
+        ],  # (user, chips, street bet, status, button)
+        deadline_ts: Optional[int],
+        bot_avatar_url: Optional[str] = None,
+    ) -> discord.Embed:
+        """A poker hand in play: community cards, the pot and every seat. No hole cards."""
+        board_line = f"**Board** `{' '.join(board)}`" if board else "No community cards yet."
+        lines = [f"Hand {hand_no}/{max_hands} · {street.capitalize()}", board_line, ""]
+        for user_id, chips, bet, status, is_button in seats:
+            marker = " (D)" if is_button else ""
+            bet_part = f" · bet {bet:,}" if bet else ""
+            status_part = f" · {status}" if status else ""
+            lines.append(f"<@{user_id}>{marker} — **{chips:,}**{bet_part}{status_part}")
+        fields: list[FieldSpec] = [
+            ("Pot", f"**{pot:,}**", True),
+            ("Blinds", f"**{small_blind:,}/{big_blind:,}**", True),
+        ]
+        if deadline_ts is not None:
+            fields.append(("Turn ends", f"<t:{deadline_ts}:R>", True))
+        return cls._create_base(
+            title=f"{Emoji.DICE} Poker table",
+            description="\n".join(lines),
+            color=COLOR_BRAND,
+            context="Gambling",
+            bot_avatar_url=bot_avatar_url,
+            fields=_grid(fields),
+        )
+
+    @classmethod
+    def poker_hand_result(
+        cls,
+        hand_no: int,
+        max_hands: int,
+        board: Sequence[str],
+        awards: Sequence[tuple[int, Sequence[int], bool]],  # (amount, winners, returned)
+        shown: Mapping[int, tuple[str, str]],  # user -> (hole cards, hand name) at a showdown
+        stacks: Mapping[int, int],
+        left: Sequence[tuple[int, int, str]],  # (user, chips paid out, bust/left/inactive)
+        bot_avatar_url: Optional[str] = None,
+    ) -> discord.Embed:
+        """How a hand ended: who took each pot, the cards shown, the stacks and who left."""
+        lines = [f"Hand {hand_no}/{max_hands} is over."]
+        if board:
+            lines.append(f"**Board** `{' '.join(board)}`")
+        for amount, winners, returned in awards:
+            names = ", ".join(f"<@{user_id}>" for user_id in winners)
+            if returned:
+                lines.append(f"{names} gets **{amount:,}** back (uncalled)")
+            elif len(winners) == 1:
+                lines.append(f"{names} wins **{amount:,}**")
+            else:
+                lines.append(f"{names} split **{amount:,}**")
+        for user_id, (cards_text, hand_name) in shown.items():
+            lines.append(f"<@{user_id}> `{cards_text}` — {hand_name}")
+        phrases = {"bust": "busted", "left": "left the table", "inactive": "sat out too long"}
+        for user_id, chips, reason in left:
+            lines.append(f"<@{user_id}> {phrases.get(reason, reason)} and was paid **{chips:,}**")
+        remaining = [f"<@{user_id}> **{chips:,}**" for user_id, chips in stacks.items() if chips]
+        if remaining:
+            lines.append("**Stacks** " + " · ".join(remaining))
+        return cls._create_base(
+            title=f"{Emoji.DICE} Poker table",
+            description="\n".join(lines),
+            color=COLOR_BRAND,
+            context="Gambling",
+            bot_avatar_url=bot_avatar_url,
+        )
+
+    @classmethod
+    def poker_final(
+        cls,
+        hands_played: int,
+        max_hands: int,
+        buy_in: int,
+        results: Sequence[tuple[int, int]],  # (user, Renown paid out)
+        bot_avatar_url: Optional[str] = None,
+    ) -> discord.Embed:
+        """A closed poker table: what every player bought in for and left with."""
+        lines = [f"The table closed after **{hands_played}/{max_hands}** hands.", ""]
+        profit = False
+        for user_id, paid in results:
+            net = paid - buy_in
+            profit = profit or net > 0
+            change = f"+{net:,}" if net > 0 else (f"-{-net:,}" if net < 0 else "even")
+            lines.append(
+                f"<@{user_id}> — bought in **{buy_in:,}**, left with **{paid:,}** ({change})"
+            )
+        return cls._create_base(
+            title=f"{Emoji.DICE} Poker table",
+            description="\n".join(lines),
+            color=COLOR_SUCCESS if profit else COLOR_BRAND,
+            context="Gambling",
+            bot_avatar_url=bot_avatar_url,
+            quip=quip_bank.get("game_win") if profit else None,
+        )
+
     @classmethod
     def lottery_panel(
         cls,
```

- [ ] **Step 4: Run them to verify they pass**

Run: `python -m pytest -q tests/test_poker_embeds.py`
Expected: `12 passed`.

- [ ] **Step 5: Lint and commit**

```bash
git add src tests
git commit -m "feat(poker): add the poker table, recap and final embeds" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Views, runner and going live

**Files:** create `src/yokai/ui/poker_views.py`, `tests/poker_helpers.py`, `tests/test_poker_views.py`; modify `src/yokai/ui/table_views.py`, `src/yokai/gambling/lobby.py`, `tests/test_poker_engine.py`, `tests/test_lobby.py`, `tests/test_poker_table_manager.py`.

**Interfaces:**
- Consumes: Task 1's `PokerTable`, `CashOut`; Task 2's embeds; P1's `PokerHand`, `best_hand`, `describe`, `HandResult`; P2's `TableManager.cash_out`, `settle_poker`, `poker_max_hands`, `pick_seconds`, `rng_factory`, `clock`; the shared `finish_table`, `reply_error`, `avatar_url`, `send`, `screen`.
- Produces:
  - `parse_chips(text: str) -> int` (strips commas; `InvalidPokerActionError` on anything but digits).
  - `PokerTableView(bot, lobby, table, hand)`: buttons `fold_button`, `call_button` (label "Check" or "Call N"), `raise_button`, `all_in_button` (both disabled when the player cannot raise), `cards_button`, `leave_button`; `raise_to(interaction, amount)`; `build_embed()`; `sync_buttons()`; attributes `hand`, `table`, `acted`, `lock`, `closed`, `deadline_ts`.
  - `RaiseModal(table_view, min_to, max_to)` with a `TextInput` attribute `amount`.
  - `run_poker(bot, lobby, message)` (registered as `GAME_RUNNERS["poker"]`); module constant `RECAP_PAUSE_S`; `_await_action(view, seconds) -> bool` (tests replace it).
  - `GAME_SPECS["poker"].available` becomes True.
  - `tests/poker_helpers.py::stacked_deck(order, button, holes: dict[int, str], board: str) -> list[Card]`.
- Behaviour: a missed turn checks when free and folds otherwise (`PokerHand.auto_action`) and counts toward `IDLE_LIMIT`; a leaver is folded when their turn comes (immediately if it is their turn when they press Leave) and paid out after the hand; busted players are paid 0; the recap shows hole cards only at a showdown; after each hand busted and leaving players are paid with `cash_out`; when the table finishes the rest are paid with `settle_poker`; any exception refunds the seats still at the table (`finish_table`).

- [ ] **Step 1: Write the failing tests**

Apply this diff (save it to a `.patch` file in the scratchpad and run `git apply --ignore-whitespace <file>` from the repo root, or make the same edits by hand):

```diff
diff --git a/tests/poker_helpers.py b/tests/poker_helpers.py
new file mode 100644
index 0000000..e5d6e6a
--- /dev/null
+++ b/tests/poker_helpers.py
@@ -0,0 +1,20 @@
+"""Shared helpers for the poker tests: a deck stacked to deal exactly the cards a test wants."""
+
+from __future__ import annotations
+
+import random
+
+from tests.card_helpers import cards
+from yokai.gambling.engines.blackjack import Card
+from yokai.gambling.engines.poker import build_deck
+
+
+def stacked_deck(order: list[int], button: int, holes: dict[int, str], board: str) -> list[Card]:
+    """A deck that deals `holes` and then `board` (flop, turn, river) to a hand with `order`."""
+    start = order.index(button)
+    seats = [order[(start + i) % len(order)] for i in range(1, len(order) + 1)]
+    first = [cards(holes[u])[0] for u in seats]
+    second = [cards(holes[u])[1] for u in seats]
+    chosen = [*first, *second, *cards(board)]
+    rest = [c for c in build_deck(random.Random(0)) if str(c) not in {str(x) for x in chosen}]
+    return [*chosen, *rest]
diff --git a/tests/test_lobby.py b/tests/test_lobby.py
index 216cf26..ab9934d 100644
--- a/tests/test_lobby.py
+++ b/tests/test_lobby.py
@@ -22,9 +22,9 @@ def test_the_duel_spec_is_a_two_seat_equal_stake_game() -> None:
     assert spec.label == "Duel" and spec.rules
 
 
-def test_every_planned_game_is_listed_and_only_poker_is_not_playable_yet() -> None:
+def test_every_planned_game_is_listed_and_playable_and_only_the_lottery_has_no_table() -> None:
     assert list(GAME_SPECS) == ["duel", "jackpot", "crash", "bjtable", "poker", "lottery"]
-    assert [name for name, spec in GAME_SPECS.items() if not spec.available] == ["poker"]
+    assert all(spec.available for spec in GAME_SPECS.values())
     assert [name for name, spec in GAME_SPECS.items() if not spec.is_table] == ["lottery"]
     assert all(spec.label and spec.rules for spec in GAME_SPECS.values())
     assert GAME_SPECS["crash"].window_factor == 0.5 and GAME_SPECS["bjtable"].window_factor == 0.5
@@ -36,7 +36,7 @@ def test_every_planned_game_is_listed_and_only_poker_is_not_playable_yet() -> No
 def test_the_poker_spec_is_a_two_to_six_seat_equal_buy_in_game() -> None:
     spec = GAME_SPECS["poker"]
     assert (spec.min_players, spec.max_players, spec.equal_stakes) == (2, 6, True)
-    assert spec.is_table and not spec.available
+    assert spec.is_table and spec.available
     assert spec.label == "Poker table" and "hold'em" in spec.rules
 
 
diff --git a/tests/test_poker_engine.py b/tests/test_poker_engine.py
index af633e6..b0dd9ea 100644
--- a/tests/test_poker_engine.py
+++ b/tests/test_poker_engine.py
@@ -7,8 +7,8 @@ import random
 import pytest
 
 from tests.card_helpers import cards
+from tests.poker_helpers import stacked_deck
 from yokai.errors import InvalidPokerActionError, NotYourTurnError
-from yokai.gambling.engines.blackjack import Card
 from yokai.gambling.engines.poker import (
     FLUSH,
     FULL_HOUSE,
@@ -31,17 +31,6 @@ from yokai.gambling.engines.poker import (
 A, B, C, D = 1, 2, 3, 4
 
 
-def stacked_deck(order: list[int], button: int, holes: dict[int, str], board: str) -> list[Card]:
-    """A deck that deals `holes` and then `board` (flop, turn, river) to a hand with `order`."""
-    start = order.index(button)
-    seats = [order[(start + i) % len(order)] for i in range(1, len(order) + 1)]
-    first = [cards(holes[u])[0] for u in seats]
-    second = [cards(holes[u])[1] for u in seats]
-    chosen = [*first, *second, *cards(board)]
-    rest = [c for c in build_deck(random.Random(0)) if str(c) not in {str(x) for x in chosen}]
-    return [*chosen, *rest]
-
-
 def hand(
     stacks: dict[int, int],
     holes: dict[int, str],
diff --git a/tests/test_poker_table_manager.py b/tests/test_poker_table_manager.py
index 46c1f1d..969ad5b 100644
--- a/tests/test_poker_table_manager.py
+++ b/tests/test_poker_table_manager.py
@@ -8,7 +8,7 @@ from unittest.mock import AsyncMock, patch
 import pytest
 
 from tests.table_helpers import ALL_PLAYABLE, CHANNEL, announce_ok, balance_of, world
-from yokai.errors import InvalidGameChoiceError, LobbyClosedError, NotAtTableError
+from yokai.errors import LobbyClosedError, NotAtTableError
 from yokai.gambling.lobby import Lobby, LobbyState
 from yokai.gambling.tables import TableManager
 
@@ -173,22 +173,9 @@ async def test_an_open_poker_table_refunds_in_full_when_cancelled(tmp_path: Path
 
 
 @pytest.mark.asyncio
-async def test_poker_cannot_be_opened_until_its_table_ships_and_costs_nothing(
-    tmp_path: Path,
-) -> None:
-    """Guard: with the real game specs poker is listed but not playable (phase P3 flips it)."""
+async def test_poker_can_be_opened_with_the_real_game_specs(tmp_path: Path) -> None:
+    """Guard: poker is playable out of the box (it ships with its table runner in phase P3)."""
     async with world(tmp_path, game="poker") as (db, manager, active):
-        with pytest.raises(InvalidGameChoiceError):
-            await manager.open_table(CHANNEL, "poker", 1, 100, announce_ok)
-        assert await balance_of(db, 1) == 1000 and not active.is_active(1)
-
-
-@pytest.mark.asyncio
-async def test_aborting_after_a_bust_hands_the_busted_buy_in_to_the_remaining_seats(
-    tmp_path: Path,
-) -> None:
-    async with _poker_world(tmp_path) as (db, manager, _):
-        lobby = await _running_poker(manager)
-        await manager.cash_out(lobby, 2, 0)  # player 2 busted
-        await manager.abort(lobby)
-        assert [await balance_of(db, u) for u in (1, 2, 3)] == [1050, 900, 1050]
+        lobby = await manager.open_table(CHANNEL, "poker", 1, 100, announce_ok)
+        assert lobby.spec.name == "poker" and active.is_active(1)
+        assert await balance_of(db, 1) == 900
diff --git a/tests/test_poker_views.py b/tests/test_poker_views.py
new file mode 100644
index 0000000..de077ca
--- /dev/null
+++ b/tests/test_poker_views.py
@@ -0,0 +1,419 @@
+"""The poker table runner and view: turns, timeouts, busts, leaving, private cards, raising."""
+
+from __future__ import annotations
+
+import random
+from contextlib import contextmanager
+from pathlib import Path
+from typing import Awaitable, Callable, Iterator, Optional
+from unittest.mock import AsyncMock, MagicMock, patch
+
+import discord
+import pytest
+
+from tests.embed_helpers import all_embed_text
+from tests.moderation_helpers import fake_moderation
+from tests.poker_helpers import stacked_deck
+from tests.table_helpers import ALL_PLAYABLE, CHANNEL, announce_ok, balance_of, world
+from yokai.gambling.engines.poker import build_deck as real_build_deck
+from yokai.gambling.engines.pokertable import PokerTable
+from yokai.gambling.lobby import Lobby, LobbyState
+from yokai.ui.poker_views import PokerTableView, RaiseModal, run_poker
+from yokai.ui.table_views import GAME_RUNNERS
+
+A_WINS = ("As Ad", "7c 2d", "Ks 9h 4d 3c Jh")  # user 1 holds aces, user 2 junk, nothing helps 2
+B_WINS = ("7c 2d", "As Ad", "Ks 9h 4d 3c Jh")
+
+
+def make_bot(db, manager) -> MagicMock:  # type: ignore[no-untyped-def]
+    bot = MagicMock()
+    bot.user = None
+    bot.db = db
+    bot.tables = manager
+    bot.moderation = fake_moderation(owner_id=999)
+    return bot
+
+
+def make_message() -> MagicMock:
+    message = MagicMock(spec=discord.Message)
+    message.edit = AsyncMock()
+    return message
+
+
+def make_interaction(user_id: int) -> MagicMock:
+    interaction = MagicMock(spec=discord.Interaction)
+    interaction.user = MagicMock(spec=discord.Member)
+    interaction.user.id = user_id
+    interaction.response = MagicMock()
+    interaction.response.edit_message = AsyncMock()
+    interaction.response.send_message = AsyncMock()
+    interaction.response.is_done = MagicMock(return_value=False)
+    interaction.followup = MagicMock()
+    interaction.followup.send = AsyncMock()
+    return interaction
+
+
+async def _running(manager, users=(1, 2), stake: int = 100, max_hands: int = 1) -> Lobby:  # type: ignore[no-untyped-def]
+    manager.poker_max_hands = max_hands
+    lobby = await manager.open_table(CHANNEL, "poker", users[0], stake, announce_ok)
+    for user_id in users[1:]:
+        await manager.join(lobby, user_id, None)
+    await manager.begin(lobby)
+    return lobby
+
+
+def deck_for(holes: tuple[str, str, str], button: int = 1, order=(1, 2)):  # type: ignore[no-untyped-def]
+    """A build_deck replacement that deals user 1 and 2 the given holes, then the board."""
+    one, two, board = holes
+    return lambda rng: stacked_deck(list(order), button, {1: one, 2: two}, board)
+
+
+Step = Optional[Callable[["PokerTableView", int], Awaitable[bool]]]
+
+
+def scripted_wait(steps: list[Step]):  # type: ignore[no-untyped-def]
+    """Replacement for _await_action: each call runs the next scripted move (None = timeout)."""
+    queue = list(steps)
+
+    async def wait(view: PokerTableView, seconds: float) -> bool:
+        step = queue.pop(0)
+        if step is None:
+            return False
+        who = view.hand.current
+        assert who is not None
+        return await step(view, who)
+
+    return wait
+
+
+def press(button_name: str) -> Step:
+    async def action(view: PokerTableView, who: int) -> bool:
+        await getattr(view, button_name).callback(make_interaction(who))
+        return True
+
+    return action
+
+
+CALL, FOLD, ALL_IN, LEAVE = (
+    press("call_button"),
+    press("fold_button"),
+    press("all_in_button"),
+    press("leave_button"),
+)
+
+
+@contextmanager
+def fast_runner(steps: list[Step], deck=None) -> Iterator[None]:  # type: ignore[no-untyped-def]
+    with (
+        patch("yokai.ui.poker_views._await_action", new=scripted_wait(steps)),
+        patch("yokai.ui.poker_views.RECAP_PAUSE_S", 0),
+        patch(
+            "yokai.gambling.engines.pokertable.build_deck",
+            side_effect=deck or real_build_deck,
+        ),
+    ):
+        yield
+
+
+def embeds_of(message: MagicMock, *, live: bool) -> list[discord.Embed]:
+    """Embeds sent to the table message: `live` = with the action buttons attached."""
+    found = []
+    for call in message.edit.await_args_list:
+        if (call.kwargs.get("view") is not None) == live:
+            found.append(call.kwargs["embed"])
+    return found
+
+
+def poker_world(tmp_path: Path, users=(1, 2, 3)):  # type: ignore[no-untyped-def]
+    return world(tmp_path, game="poker", specs=ALL_PLAYABLE, users=users)
+
+
+def make_view(bot, lobby, holes=A_WINS, users=(1, 2)):  # type: ignore[no-untyped-def]
+    table = PokerTable(list(users), 100, 30, random.Random)
+    with patch("yokai.gambling.engines.pokertable.build_deck", side_effect=deck_for(holes)):
+        hand = table.start_hand()
+    view = PokerTableView(bot, lobby, table, hand)
+    view.message = make_message()
+    return view, table, hand
+
+
+# ---- the runner: whole sessions ------------------------------------------------------------
+
+
+def test_poker_has_a_runner_and_is_playable() -> None:
+    from yokai.gambling.lobby import GAME_SPECS
+
+    assert GAME_RUNNERS["poker"] is run_poker
+    assert GAME_SPECS["poker"].available
+
+
+@pytest.mark.asyncio
+async def test_a_hand_played_to_showdown_pays_the_winner_and_closes_the_table(
+    tmp_path: Path,
+) -> None:
+    async with poker_world(tmp_path) as (db, manager, active):
+        lobby = await _running(manager)
+        message = make_message()
+        # heads-up: user 1 (button, small blind) calls, user 2 checks; then check down 3 streets
+        with fast_runner([CALL] * 8, deck_for(A_WINS)):
+            await run_poker(make_bot(db, manager), lobby, message)
+        assert (await balance_of(db, 1), await balance_of(db, 2)) == (1002, 998)
+        assert lobby.state is LobbyState.SETTLED and await db.get_pot() == 0
+        assert not active.is_active(1) and not active.is_active(2)
+        assert await db.refund_open_bets() == 0
+        final = message.edit.await_args.kwargs
+        assert final["view"] is None and "+2" in all_embed_text(final["embed"])
+
+
+@pytest.mark.asyncio
+async def test_hole_cards_only_appear_publicly_at_a_showdown(tmp_path: Path) -> None:
+    async with poker_world(tmp_path) as (db, manager, _):
+        lobby = await _running(manager)
+        message = make_message()
+        with fast_runner([CALL] * 8, deck_for(A_WINS)):
+            await run_poker(make_bot(db, manager), lobby, message)
+        for embed in embeds_of(message, live=True):
+            text = all_embed_text(embed)
+            assert "As Ad" not in text and "7c 2d" not in text
+        recap = "\n".join(all_embed_text(e) for e in embeds_of(message, live=False))
+        assert "As Ad" in recap and "7c 2d" in recap and "wins" in recap
+
+
+@pytest.mark.asyncio
+async def test_a_fold_ends_the_hand_without_revealing_cards(tmp_path: Path) -> None:
+    async with poker_world(tmp_path) as (db, manager, _):
+        lobby = await _running(manager)
+        message = make_message()
+        with fast_runner([FOLD], deck_for(A_WINS)):
+            await run_poker(make_bot(db, manager), lobby, message)
+        assert (await balance_of(db, 1), await balance_of(db, 2)) == (999, 1001)
+        recap = "\n".join(all_embed_text(e) for e in embeds_of(message, live=False))
+        assert "As Ad" not in recap and "7c 2d" not in recap
+
+
+@pytest.mark.asyncio
+async def test_a_missed_turn_checks_when_free_otherwise_folds(tmp_path: Path) -> None:
+    async with poker_world(tmp_path) as (db, manager, _):
+        lobby = await _running(manager)
+        message = make_message()
+        with fast_runner([None], deck_for(A_WINS)):  # user 1 faces the big blind: auto-fold
+            await run_poker(make_bot(db, manager), lobby, message)
+        assert (await balance_of(db, 1), await balance_of(db, 2)) == (999, 1001)
+
+
+@pytest.mark.asyncio
+async def test_two_missed_turns_in_a_row_remove_the_player_and_pay_out_their_chips(
+    tmp_path: Path,
+) -> None:
+    async with poker_world(tmp_path) as (db, manager, active):
+        lobby = await _running(manager, max_hands=5)
+        message = make_message()
+        # hand 1: user 1 times out and folds. hand 2: user 2 (button) calls, user 1 times out
+        # (a free check) and is removed after the hand.
+        with fast_runner([None, CALL, None]):
+            await run_poker(make_bot(db, manager), lobby, message)
+        assert (await balance_of(db, 1), await balance_of(db, 2)) == (997, 1003)
+        assert lobby.state is LobbyState.SETTLED and not active.is_active(1)
+        assert "sat out" in "\n".join(all_embed_text(e) for e in embeds_of(message, live=False))
+
+
+@pytest.mark.asyncio
+async def test_a_busted_player_is_paid_nothing_and_the_winner_takes_the_table(
+    tmp_path: Path,
+) -> None:
+    async with poker_world(tmp_path) as (db, manager, _):
+        lobby = await _running(manager, max_hands=5)
+        message = make_message()
+        with fast_runner([ALL_IN, CALL], deck_for(B_WINS)):
+            await run_poker(make_bot(db, manager), lobby, message)
+        assert (await balance_of(db, 1), await balance_of(db, 2)) == (900, 1100)
+        assert lobby.state is LobbyState.SETTLED and await db.get_pot() == 0
+        assert "busted" in "\n".join(all_embed_text(e) for e in embeds_of(message, live=False))
+
+
+@pytest.mark.asyncio
+async def test_leaving_on_your_turn_folds_and_pays_out_your_chips(tmp_path: Path) -> None:
+    async with poker_world(tmp_path) as (db, manager, _):
+        lobby = await _running(manager, max_hands=5)
+        message = make_message()
+        with fast_runner([LEAVE], deck_for(A_WINS)):
+            await run_poker(make_bot(db, manager), lobby, message)
+        assert (await balance_of(db, 1), await balance_of(db, 2)) == (999, 1001)
+        assert lobby.state is LobbyState.SETTLED
+
+
+@pytest.mark.asyncio
+async def test_the_table_closes_at_the_hand_cap(tmp_path: Path) -> None:
+    async with poker_world(tmp_path) as (db, manager, _):
+        lobby = await _running(manager, max_hands=2)
+        message = make_message()
+        # hand 1: user 1 (button) folds; hand 2: user 2 (button) folds. Even again: 100 each.
+        with fast_runner([FOLD, FOLD]):
+            await run_poker(make_bot(db, manager), lobby, message)
+        assert (await balance_of(db, 1), await balance_of(db, 2)) == (1000, 1000)
+        assert lobby.state is LobbyState.SETTLED
+
+
+@pytest.mark.asyncio
+async def test_three_players_play_on_after_one_busts(tmp_path: Path) -> None:
+    async with poker_world(tmp_path) as (db, manager, _):
+        lobby = await _running(manager, users=(1, 2, 3), max_hands=1)
+        message = make_message()
+        with fast_runner([FOLD, FOLD]):  # 3 players: button 1, SB 2, BB 3; 1 and 2 fold
+            await run_poker(make_bot(db, manager), lobby, message)
+        total = sum([await balance_of(db, u) for u in (1, 2, 3)])
+        assert total == 3000 and await balance_of(db, 3) == 1001
+
+
+@pytest.mark.asyncio
+async def test_a_failure_refunds_everyone_and_tells_the_players(tmp_path: Path) -> None:
+    async with poker_world(tmp_path) as (db, manager, active):
+        lobby = await _running(manager)
+        message = make_message()
+        with (
+            fast_runner([]),
+            patch.object(PokerTable, "start_hand", side_effect=RuntimeError("boom")),
+        ):
+            with pytest.raises(RuntimeError):
+                await run_poker(make_bot(db, manager), lobby, message)
+        assert (await balance_of(db, 1), await balance_of(db, 2)) == (1000, 1000)
+        assert lobby.state is LobbyState.CANCELLED and not active.is_active(1)
+        assert "refunded" in all_embed_text(message.edit.await_args.kwargs["embed"]).lower()
+
+
+# ---- the view: who can do what -----------------------------------------------------------
+
+
+@pytest.mark.asyncio
+async def test_only_the_player_to_act_can_use_the_action_buttons(tmp_path: Path) -> None:
+    async with poker_world(tmp_path) as (db, manager, _):
+        lobby = await _running(manager)
+        view, table, hand = make_view(make_bot(db, manager), lobby)
+        interaction = make_interaction(2)  # user 1 is up
+        with patch("yokai.ui.table_common.send", new=AsyncMock()) as sent:
+            await view.fold_button.callback(interaction)
+        assert sent.await_args.kwargs["ephemeral"] is True
+        assert "turn" in sent.await_args.kwargs["embed"].description.lower()
+        assert hand.current == 1 and not view.acted.is_set()
+        interaction.response.edit_message.assert_not_awaited()
+
+
+@pytest.mark.asyncio
+async def test_a_stranger_cannot_act_or_look_at_cards(tmp_path: Path) -> None:
+    async with poker_world(tmp_path) as (db, manager, _):
+        lobby = await _running(manager)
+        view, table, hand = make_view(make_bot(db, manager), lobby)
+        with patch("yokai.ui.table_common.send", new=AsyncMock()) as sent:
+            await view.call_button.callback(make_interaction(9))
+            await view.cards_button.callback(make_interaction(9))
+            await view.leave_button.callback(make_interaction(9))
+        assert sent.await_count == 3 and hand.current == 1
+
+
+@pytest.mark.asyncio
+async def test_my_cards_shows_only_the_askers_hole_cards_privately(tmp_path: Path) -> None:
+    async with poker_world(tmp_path) as (db, manager, _):
+        lobby = await _running(manager)
+        view, table, hand = make_view(make_bot(db, manager), lobby)
+        with patch("yokai.ui.poker_views.send", new=AsyncMock()) as sent:
+            await view.cards_button.callback(make_interaction(2))
+        assert sent.await_args.kwargs["ephemeral"] is True
+        text = all_embed_text(sent.await_args.kwargs["embed"])
+        assert "7c 2d" in text and "As Ad" not in text
+
+
+@pytest.mark.asyncio
+async def test_a_folded_player_can_still_see_their_cards(tmp_path: Path) -> None:
+    async with poker_world(tmp_path) as (db, manager, _):
+        lobby = await _running(manager)
+        view, table, hand = make_view(make_bot(db, manager), lobby)
+        await view.fold_button.callback(make_interaction(1))
+        with patch("yokai.ui.poker_views.send", new=AsyncMock()) as sent:
+            await view.cards_button.callback(make_interaction(1))
+        assert "As Ad" in all_embed_text(sent.await_args.kwargs["embed"])
+
+
+@pytest.mark.asyncio
+async def test_the_call_button_checks_when_free_and_names_the_price_otherwise(
+    tmp_path: Path,
+) -> None:
+    async with poker_world(tmp_path) as (db, manager, _):
+        lobby = await _running(manager)
+        view, table, hand = make_view(make_bot(db, manager), lobby)
+        assert view.call_button.label == "Call 1"  # the small blind owes one more chip
+        await view.call_button.callback(make_interaction(1))
+        assert view.call_button.label == "Check"  # the big blind may check
+
+
+@pytest.mark.asyncio
+async def test_a_player_who_cannot_raise_has_no_all_in_or_raise_button(tmp_path: Path) -> None:
+    async with poker_world(tmp_path) as (db, manager, _):
+        lobby = await _running(manager)
+        view, table, hand = make_view(make_bot(db, manager), lobby)
+        assert not view.all_in_button.disabled and not view.raise_button.disabled
+        await view.all_in_button.callback(make_interaction(1))  # user 1 shoves 100
+        assert view.all_in_button.disabled and view.raise_button.disabled
+        assert view.call_button.label == "Call 98"
+
+
+@pytest.mark.asyncio
+async def test_a_legal_raise_moves_the_action_and_refreshes_the_message(tmp_path: Path) -> None:
+    async with poker_world(tmp_path) as (db, manager, _):
+        lobby = await _running(manager)
+        view, table, hand = make_view(make_bot(db, manager), lobby)
+        interaction = make_interaction(1)
+        await view.raise_to(interaction, 6)
+        assert hand.current == 2 and hand.current_bet == 6 and view.acted.is_set()
+        interaction.response.edit_message.assert_awaited_once()
+
+
+@pytest.mark.asyncio
+async def test_an_illegal_raise_is_answered_privately_and_changes_nothing(tmp_path: Path) -> None:
+    async with poker_world(tmp_path) as (db, manager, _):
+        lobby = await _running(manager)
+        view, table, hand = make_view(make_bot(db, manager), lobby)
+        with patch("yokai.ui.table_common.send", new=AsyncMock()) as sent:
+            await view.raise_to(make_interaction(1), 3)
+        assert sent.await_args.kwargs["ephemeral"] is True
+        assert "minimum raise is to 4" in sent.await_args.kwargs["embed"].description
+        assert hand.current == 1 and hand.current_bet == 2
+
+
+@pytest.mark.asyncio
+async def test_the_raise_form_parses_the_amount_and_rejects_junk(tmp_path: Path) -> None:
+    async with poker_world(tmp_path) as (db, manager, _):
+        lobby = await _running(manager)
+        view, table, hand = make_view(make_bot(db, manager), lobby)
+        modal = RaiseModal(view, 4, 100)
+        modal.amount._value = "abc"
+        with patch("yokai.ui.table_common.send", new=AsyncMock()) as sent:
+            await modal.on_submit(make_interaction(1))
+        assert "whole number" in sent.await_args.kwargs["embed"].description
+        assert hand.current == 1
+        modal.amount._value = "1,0"  # commas are stripped like in the stake form: 10
+        await modal.on_submit(make_interaction(1))
+        assert hand.current_bet == 10
+
+
+@pytest.mark.asyncio
+async def test_leaving_off_turn_is_remembered_and_the_player_is_folded_when_up(
+    tmp_path: Path,
+) -> None:
+    async with poker_world(tmp_path) as (db, manager, _):
+        lobby = await _running(manager)
+        view, table, hand = make_view(make_bot(db, manager), lobby)
+        with patch("yokai.ui.poker_views.send", new=AsyncMock()) as sent:
+            await view.leave_button.callback(make_interaction(2))  # user 1 is up
+        assert 2 in table.leaving and hand.current == 1
+        assert sent.await_args.kwargs["ephemeral"] is True
+
+
+@pytest.mark.asyncio
+async def test_buttons_do_nothing_once_the_hand_is_closed(tmp_path: Path) -> None:
+    async with poker_world(tmp_path) as (db, manager, _):
+        lobby = await _running(manager)
+        view, table, hand = make_view(make_bot(db, manager), lobby)
+        view.closed = True
+        with patch("yokai.ui.table_common.send", new=AsyncMock()) as sent:
+            await view.call_button.callback(make_interaction(1))
+        assert sent.await_count == 1 and hand.current == 1
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest -q tests/test_poker_views.py`
Expected: collection ERROR, `ModuleNotFoundError: No module named 'yokai.ui.poker_views'`.
Run: `python -m pytest -q tests/test_lobby.py tests/test_poker_table_manager.py tests/test_poker_engine.py`
Expected: `3 failed` (the tests that now expect poker to be playable); the refactored engine tests still pass.

- [ ] **Step 3: Implement**

Apply this diff (save it to a `.patch` file in the scratchpad and run `git apply --ignore-whitespace <file>` from the repo root, or make the same edits by hand):

```diff
diff --git a/src/yokai/gambling/lobby.py b/src/yokai/gambling/lobby.py
index 5167155..20715c0 100644
--- a/src/yokai/gambling/lobby.py
+++ b/src/yokai/gambling/lobby.py
@@ -79,7 +79,6 @@ GAME_SPECS: dict[str, GameSpec] = {
         min_players=2,
         max_players=6,
         equal_stakes=True,
-        available=False,  # until its table runner ships (phase P3)
         rules=(
             "Texas hold'em with chips. Everyone buys in for the same amount and plays hands "
             "until the hand limit; leave or bust and your chips are paid out in Renown."
diff --git a/src/yokai/ui/poker_views.py b/src/yokai/ui/poker_views.py
new file mode 100644
index 0000000..c0f3487
--- /dev/null
+++ b/src/yokai/ui/poker_views.py
@@ -0,0 +1,362 @@
+"""The poker table: action buttons, private hole cards, the Raise form and the hand loop."""
+
+from __future__ import annotations
+
+import asyncio
+import contextlib
+import logging
+from typing import TYPE_CHECKING, Callable
+
+import discord
+
+from yokai.errors import InvalidPokerActionError, LobbyClosedError, NotAtTableError, YokaiError
+from yokai.gambling.engines.poker import HandResult, PokerHand, best_hand, describe
+from yokai.gambling.engines.pokertable import CashOut, PokerTable
+from yokai.gambling.lobby import Lobby
+from yokai.moderation.gate import screen
+from yokai.ui.embeds import EmbedFactory, send
+from yokai.ui.table_common import avatar_url, finish_table, reply_error
+
+if TYPE_CHECKING:
+    from yokai.bot import YokaiBot
+
+logger = logging.getLogger(__name__)
+
+RECAP_PAUSE_S = 6.0  # how long a finished hand stays on screen before the next one is dealt
+
+Move = Callable[[PokerHand, int], None]
+
+
+def parse_chips(text: str) -> int:
+    """Turn the text typed into the Raise form into a whole number of chips."""
+    cleaned = text.strip().replace(",", "")
+    if not cleaned.isdigit():
+        raise InvalidPokerActionError(
+            "Enter the total you want to raise to as a whole number, like 200."
+        )
+    return int(cleaned)
+
+
+def _cards(cards: object) -> list[str]:
+    return [str(card) for card in cards]  # type: ignore[attr-defined]
+
+
+class PokerTableView(discord.ui.View):
+    """The buttons under one hand. Only the player whose turn it is can act."""
+
+    def __init__(self, bot: YokaiBot, lobby: Lobby, table: PokerTable, hand: PokerHand) -> None:
+        super().__init__(timeout=None)
+        self.bot = bot
+        self.lobby = lobby
+        self.table = table
+        self.hand = hand
+        self.message: discord.Message | None = None
+        self.acted = asyncio.Event()
+        self.lock = asyncio.Lock()
+        self.closed = False  # set by the runner, under the lock, once the hand is being wrapped up
+        self.deadline_ts = 0
+        self.reset_deadline()
+        self.sync_buttons()
+
+    async def interaction_check(self, interaction: discord.Interaction) -> bool:
+        return not await screen(interaction)
+
+    def reset_deadline(self) -> None:
+        self.deadline_ts = int(self.bot.tables.clock() + self.bot.tables.pick_seconds)
+
+    def build_embed(self) -> discord.Embed:
+        """The public table: board, pot and seats. Hole cards are never shown here."""
+        hand = self.hand
+        seats = []
+        for user_id in hand.order:
+            if user_id in hand.folded:
+                status = "folded"
+            elif user_id == hand.current:
+                status = "to act"
+            elif hand.stacks[user_id] == 0:
+                status = "all-in"
+            else:
+                status = ""
+            seats.append(
+                (user_id, hand.stacks[user_id], hand.bet[user_id], status, user_id == hand.button)
+            )
+        return EmbedFactory.poker_table(
+            hand_no=self.table.hand_no,
+            max_hands=self.table.max_hands,
+            street=hand.street.value,
+            board=_cards(hand.board),
+            pot=hand.pot,
+            small_blind=self.table.small_blind,
+            big_blind=self.table.big_blind,
+            seats=seats,
+            deadline_ts=None if hand.is_over else self.deadline_ts,
+            bot_avatar_url=avatar_url(self.bot),
+        )
+
+    def sync_buttons(self) -> None:
+        """Label and enable the buttons for whoever is up now."""
+        who = self.hand.current
+        acting = (self.fold_button, self.call_button, self.raise_button, self.all_in_button)
+        if who is None:
+            for button in acting:
+                button.disabled = True
+            return
+        legal = self.hand.legal_actions(who)
+        self.fold_button.disabled = False
+        self.call_button.disabled = False
+        self.call_button.label = "Check" if legal.can_check else f"Call {legal.call_amount:,}"
+        can_raise = legal.max_raise_to is not None
+        self.raise_button.disabled = not can_raise
+        self.all_in_button.disabled = not can_raise
+
+    async def _act(self, interaction: discord.Interaction, move: Move) -> None:
+        async with self.lock:
+            if self.closed:
+                await reply_error(
+                    self.bot, interaction, LobbyClosedError("This hand is over.", user_hint=None)
+                )
+                return
+            user_id = interaction.user.id
+            try:
+                move(self.hand, user_id)
+            except YokaiError as err:
+                await reply_error(self.bot, interaction, err)
+                return
+            self.table.record_action(user_id)
+            self.reset_deadline()
+            self.sync_buttons()
+            self.acted.set()
+            await interaction.response.edit_message(embed=self.build_embed(), view=self)
+
+    async def raise_to(self, interaction: discord.Interaction, amount: int) -> None:
+        """Raise so the player's total bet this round is `amount` (the Raise form calls this)."""
+        await self._act(interaction, lambda hand, user_id: hand.raise_to(user_id, amount))
+
+    @discord.ui.button(label="Fold", style=discord.ButtonStyle.secondary)
+    async def fold_button(
+        self, interaction: discord.Interaction, button: discord.ui.Button
+    ) -> None:
+        await self._act(interaction, lambda hand, user_id: hand.fold(user_id))
+
+    @discord.ui.button(label="Call", style=discord.ButtonStyle.success)
+    async def call_button(
+        self, interaction: discord.Interaction, button: discord.ui.Button
+    ) -> None:
+        def check_or_call(hand: PokerHand, user_id: int) -> None:
+            if hand.legal_actions(user_id).can_check:
+                hand.check(user_id)
+            else:
+                hand.call(user_id)
+
+        await self._act(interaction, check_or_call)
+
+    @discord.ui.button(label="Raise", style=discord.ButtonStyle.primary)
+    async def raise_button(
+        self, interaction: discord.Interaction, button: discord.ui.Button
+    ) -> None:
+        try:
+            legal = self.hand.legal_actions(interaction.user.id)
+        except YokaiError as err:
+            await reply_error(self.bot, interaction, err)
+            return
+        if legal.min_raise_to is None or legal.max_raise_to is None:
+            await reply_error(
+                self.bot, interaction, InvalidPokerActionError("You can't raise here.")
+            )
+            return
+        await interaction.response.send_modal(
+            RaiseModal(self, legal.min_raise_to, legal.max_raise_to)
+        )
+
+    @discord.ui.button(label="All-in", style=discord.ButtonStyle.danger)
+    async def all_in_button(
+        self, interaction: discord.Interaction, button: discord.ui.Button
+    ) -> None:
+        await self._act(interaction, lambda hand, user_id: hand.all_in(user_id))
+
+    @discord.ui.button(label="My cards", style=discord.ButtonStyle.secondary, row=1)
+    async def cards_button(
+        self, interaction: discord.Interaction, button: discord.ui.Button
+    ) -> None:
+        user_id = interaction.user.id
+        hole = self.hand.holes.get(user_id)
+        if hole is None:
+            await reply_error(self.bot, interaction, NotAtTableError())
+            return
+        lines = [f"Your cards: `{' '.join(_cards(hole))}`"]
+        if self.hand.board:
+            lines.append(f"Board: `{' '.join(_cards(self.hand.board))}`")
+            lines.append(f"You have **{describe(best_hand([*hole, *self.hand.board]))}**.")
+        embed = EmbedFactory.info(
+            title="Your cards",
+            description="\n".join(lines),
+            context="Gambling",
+            bot_avatar_url=avatar_url(self.bot),
+        )
+        await send(interaction, embed=embed, ephemeral=True)
+
+    @discord.ui.button(label="Leave", style=discord.ButtonStyle.secondary, row=1)
+    async def leave_button(
+        self, interaction: discord.Interaction, button: discord.ui.Button
+    ) -> None:
+        user_id = interaction.user.id
+        try:
+            self.table.request_leave(user_id)
+        except YokaiError as err:
+            await reply_error(self.bot, interaction, err)
+            return
+        if self.hand.current == user_id:
+            await self._act(interaction, lambda hand, who: hand.fold(who))
+            return
+        embed = EmbedFactory.info(
+            title="Leaving the table",
+            description="You'll be folded and paid out your chips when this hand ends.",
+            context="Gambling",
+            bot_avatar_url=avatar_url(self.bot),
+        )
+        await send(interaction, embed=embed, ephemeral=True)
+
+
+class RaiseModal(discord.ui.Modal):
+    """Asks how much to raise to, then hands the number to the table view."""
+
+    def __init__(self, table_view: PokerTableView, min_to: int, max_to: int) -> None:
+        super().__init__(title="Raise", timeout=300)
+        self.table_view = table_view
+        self.amount = discord.ui.TextInput(
+            label="Raise to (your total bet this round)",
+            placeholder=f"{min_to:,} to {max_to:,}",
+            min_length=1,
+            max_length=12,
+        )
+        self.add_item(self.amount)
+
+    async def on_submit(self, interaction: discord.Interaction) -> None:
+        try:
+            amount = parse_chips(self.amount.value)
+        except YokaiError as err:
+            await reply_error(self.table_view.bot, interaction, err)
+            return
+        await self.table_view.raise_to(interaction, amount)
+
+
+async def _await_action(view: PokerTableView, seconds: float) -> bool:
+    """Wait for a player to act; False if `seconds` pass first. Tests replace this."""
+    try:
+        await asyncio.wait_for(view.acted.wait(), timeout=seconds)
+    except asyncio.TimeoutError:
+        return False
+    return True
+
+
+async def _refresh(view: PokerTableView, message: discord.Message) -> None:
+    if view.hand.is_over:
+        return
+    with contextlib.suppress(discord.HTTPException):
+        await message.edit(embed=view.build_embed(), view=view)
+
+
+async def _play_hand(
+    bot: YokaiBot,
+    table: PokerTable,
+    hand: PokerHand,
+    view: PokerTableView,
+    message: discord.Message,
+) -> None:
+    """Drive one hand to its end: wait for each player, fold leavers, act for the absent."""
+    while not hand.is_over:
+        who = hand.current
+        assert who is not None
+        if who in table.leaving:
+            async with view.lock:
+                if hand.current == who:
+                    hand.fold(who)
+                    view.reset_deadline()
+                    view.sync_buttons()
+            await _refresh(view, message)
+            continue
+        view.acted.clear()
+        if await _await_action(view, bot.tables.pick_seconds):
+            continue  # someone acted; the button handler already updated the message
+        async with view.lock:
+            if view.acted.is_set():
+                continue  # a press landed while we waited for the lock
+            if hand.current == who:  # still their turn: they ran out of time
+                hand.auto_action(who)
+                table.record_timeout(who)
+                view.reset_deadline()
+                view.sync_buttons()
+        await _refresh(view, message)
+    # A player's last action edits the message from its own handler while holding the lock. Wait
+    # for that edit to finish before the recap goes up, or it could land last over the recap.
+    async with view.lock:
+        view.closed = True
+    view.stop()
+
+
+def _recap_embed(
+    bot: YokaiBot, table: PokerTable, hand: PokerHand, result: HandResult, left: list[CashOut]
+) -> discord.Embed:
+    awards = [
+        (pot.amount, list(pot.winners), len(pot.eligible) == 1 and index > 0)
+        for index, pot in enumerate(result.pots)
+    ]
+    shown = {
+        user_id: (" ".join(_cards(cards)), describe(result.ranks[user_id]))
+        for user_id, cards in result.shown.items()
+    }
+    return EmbedFactory.poker_hand_result(
+        hand_no=table.hand_no,
+        max_hands=table.max_hands,
+        board=_cards(hand.board) if result.showdown else [],
+        awards=awards,
+        shown=shown,
+        stacks=dict(table.stacks),
+        left=[(cash.user_id, cash.chips, cash.reason) for cash in left],
+        bot_avatar_url=avatar_url(bot),
+    )
+
+
+async def run_poker(bot: YokaiBot, lobby: Lobby, message: discord.Message) -> None:
+    """Run a started poker table: hand after hand until it closes. Any failure refunds the rest."""
+    tables = bot.tables
+    buy_in = lobby.host_seat.stake
+    table = PokerTable(list(lobby.seats), buy_in, tables.poker_max_hands, tables.rng_factory)
+    paid: dict[int, int] = {}
+
+    async def cash_out_flagged() -> list[CashOut]:
+        """Pay out everyone who busted or is leaving, one by one."""
+        leaving = table.take_cash_outs()
+        for cash in leaving:
+            await tables.cash_out(lobby, cash.user_id, cash.chips)
+            paid[cash.user_id] = cash.chips
+        return leaving
+
+    async def play() -> discord.Embed:
+        while True:
+            await cash_out_flagged()  # also picks up a Leave pressed between hands
+            if table.is_finished:
+                break
+            hand = table.start_hand()
+            view = PokerTableView(bot, lobby, table, hand)
+            view.message = message
+            await message.edit(embed=view.build_embed(), view=view)
+            await _play_hand(bot, table, hand, view, message)
+            result = hand.result()
+            table.finish_hand(result)
+            left = await cash_out_flagged()
+            with contextlib.suppress(discord.HTTPException):
+                await message.edit(embed=_recap_embed(bot, table, hand, result, left), view=None)
+            await asyncio.sleep(RECAP_PAUSE_S)
+        payouts = table.final_payouts()
+        await tables.settle_poker(lobby, payouts)
+        paid.update(payouts)
+        return EmbedFactory.poker_final(
+            hands_played=table.hand_no,
+            max_hands=table.max_hands,
+            buy_in=buy_in,
+            results=[(user_id, paid[user_id]) for user_id in lobby.seats],
+            bot_avatar_url=avatar_url(bot),
+        )
+
+    await finish_table(bot, lobby, message, play)
diff --git a/src/yokai/ui/table_views.py b/src/yokai/ui/table_views.py
index b295c5d..723ca57 100644
--- a/src/yokai/ui/table_views.py
+++ b/src/yokai/ui/table_views.py
@@ -22,6 +22,7 @@ from yokai.ui.crash_views import run_crash
 from yokai.ui.duel_views import run_duel
 from yokai.ui.embeds import EmbedFactory
 from yokai.ui.jackpot_views import run_jackpot
+from yokai.ui.poker_views import run_poker
 from yokai.ui.table_common import (
     NO_MENTIONS,
     avatar_url,
@@ -48,6 +49,7 @@ GAME_RUNNERS: dict[str, GameRunner] = {
     "jackpot": run_jackpot,
     "crash": run_crash,
     "bjtable": run_bjtable,
+    "poker": run_poker,
 }
 
 
```

- [ ] **Step 4: Run the new tests and the whole suite**

Run: `python -m pytest -q tests/test_poker_views.py`
Expected: `22 passed`.
Run: `python -m pytest -q`
Expected: all pass (the admin dropdown and cog tests already list poker from P2).

- [ ] **Step 5: Lint and commit**

```bash
git add src tests
git commit -m "feat(poker): add the poker table views and runner and make poker playable" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Docs and phase verification

**Files:** modify `docs/superpowers/specs/2026-10-10-poker-design.md`, `README.md`, `TASKS.md`.

- [ ] **Step 1: Update the docs**

Apply this diff (save it to a `.patch` file in the scratchpad and run `git apply --ignore-whitespace <file>` from the repo root, or make the same edits by hand):

```diff
diff --git a/README.md b/README.md
index 51cb936..b5499d3 100644
--- a/README.md
+++ b/README.md
@@ -20,9 +20,9 @@ Yokai is a private, single-server Discord bot (~10 users) hosted locally on the
   - `/about`: Detailed runtime stack versions, system uptime, and credits.
   - `/admin diag`: Admin-only ephemeral diagnostic dashboard with in-place **Update yt-dlp** button.
   - `/admin economy`: Admin-only Renown grant or deduction (was `/economy-admin`).
-  - `/admin set_multiplayer game:<name>`: Admin-only. Makes the current channel the table channel for a multiplayer game and posts its persistent **Play** embed. Every game is listed and playable: Duel, Jackpot, Crash, Blackjack table and the Lottery (whose channel gets a ticket panel instead of a Play embed).
+  - `/admin set_multiplayer game:<name>`: Admin-only. Makes the current channel the table channel for a multiplayer game and posts its persistent **Play** embed. Every game is listed and playable: Duel, Jackpot, Crash, Blackjack table, Poker table and the Lottery (whose channel gets a ticket panel instead of a Play embed).
   - `/admin unset_multiplayer`: Admin-only. Stops using the current channel as a table channel.
-- **Multiplayer tables:** dedicated channels with an always-on Play button. A finished game stays on screen until the next table is requested. Games: Jackpot (a stake-weighted draw takes the pot), Crash (cash out before the multiplier crashes), Blackjack table (shared dealer, up to five players) and Duel (pick a number 1 to 10; the pick closest to a hidden number wins the pot, a tie refunds both). Everything the house keeps feeds a lottery pot.
+- **Multiplayer tables:** dedicated channels with an always-on Play button. A finished game stays on screen until the next table is requested. Games: Jackpot (a stake-weighted draw takes the pot), Crash (cash out before the multiplier crashes), Blackjack table (shared dealer, up to five players), Poker table (Texas hold'em for two to six; everyone buys in for the host's stake, play hands until the hand limit, and chips are paid back out in Renown when you leave or bust) and Duel (pick a number 1 to 10; the pick closest to a hidden number wins the pot, a tie refunds both). Everything the house keeps feeds a lottery pot.
 - **Lottery:** a weekly draw in its own channel. Buy numbered tickets with a button (hand-picked numbers, quick picks, or both), one number from 1 to 20 is drawn, everyone holding it splits the pot, and an unclaimed pot rolls over and grows. Everything the house wins in any game feeds the pot.
 - **Operational Hardening:** Auto-disconnects when idle or left alone in voice channels, graceful process supervision (`run.ps1`), and zero privileged intents.
 
diff --git a/TASKS.md b/TASKS.md
index e44a4c7..a3d61f2 100644
--- a/TASKS.md
+++ b/TASKS.md
@@ -585,3 +585,31 @@ Plan: `docs/superpowers/plans/2026-10-10-poker-p2-money.md`.
 
 **STOP — owner review.** P3 (table UI) is planned after this review.
 
+### P3 — Table UI
+
+Plan: `docs/superpowers/plans/2026-10-10-poker-p3-table-ui.md`.
+
+- [ ] P3.1 The multi-hand table (`PokerTable`): blinds, button, busts, leavers, hand cap.
+- [ ] P3.2 Poker embeds (table, hand recap, final).
+- [ ] P3.3 Views and runner: action buttons, My cards, Raise form, Leave, timers; poker playable.
+- [ ] P3.4 Docs.
+
+**Acceptance criteria (P3)**
+- A poker table opens from the Play embed, fills with equal buy-ins, and plays hand after hand on one
+  message edited in place; only the acting player's buttons work.
+- Hole cards are only ever shown ephemerally to their owner, and publicly only at a showdown.
+- A missed turn checks when free and folds otherwise; two in a row remove the player; Leave folds and
+  pays out; a busted player is paid nothing and removed.
+- The table closes at the hand cap or when fewer than two players are left, and pays everyone their
+  chips: Renown in equals Renown out, nothing goes to the lottery pot.
+- A failure at any point refunds the seats still at the table and tells the channel.
+- Poker is playable (listed, openable, `/admin set_multiplayer game:Poker table` works).
+- `pytest`, `ruff check` and `ruff format --check` are clean.
+- *(manual, real Discord)* Dedicate a channel to Poker; open a table with 3 members; play several
+  hands using Call, Raise (below the minimum, junk text, a valid raise), All-in and Fold; check My
+  cards is private and a stranger is refused; let a turn time out, then leave with Leave; bust a
+  player; restart the bot mid-hand and check balances and that a fresh Play embed returns; play to
+  the hand cap (`GAMBLING_POKER_MAX_HANDS=3` is handy) and check the final embed and `/balance`.
+
+**STOP — owner review.** P4 (docs and wrap-up) is planned after this review.
+
diff --git a/docs/superpowers/specs/2026-10-10-poker-design.md b/docs/superpowers/specs/2026-10-10-poker-design.md
index 65235cb..6cba22e 100644
--- a/docs/superpowers/specs/2026-10-10-poker-design.md
+++ b/docs/superpowers/specs/2026-10-10-poker-design.md
@@ -37,6 +37,7 @@ Success criteria:
 
 ```
 gambling/engines/poker.py     pure: deck, evaluator, betting state machine, side pots
+gambling/engines/pokertable.py pure: stacks, blinds, button, busts, leavers, hand cap
 gambling/tables.py            + TableManager.cash_out(), + poker table settle
 storage/db.py                 + Database.cash_out_lobby() (lobby-level conservation check),
                                 capped poker refunds
@@ -105,14 +106,21 @@ Follows `bjtable_views.py`: one public message edited in place, a lock around st
 `acted` event, a deadline per decision, and `finish_table` so any failure refunds and tells players.
 
 - Buttons: **Fold**, **Check / Call N**, **Raise** (opens a modal; validated against min-raise and
-  stack, whole numbers only), **All-in**, **My cards** (ephemeral hole cards, any seated player), and
-  **Leave** (folds now, cashes out after the hand).
+  stack, whole numbers only), **All-in**, **My cards** (ephemeral hole cards and, from the flop, the
+  best hand the player has; any seated player, folded or not), and **Leave** (folds now if it is
+  their turn, otherwise when it comes round; cashed out after the hand). **Raise** and **All-in**
+  are disabled for a player who can only call or fold, so All-in never silently turns into a call.
 - Only the acting player can use action buttons; everyone else gets the standard not-your-turn error.
   `interaction_check` runs the moderation `screen` like the other tables.
 - Turn timer `GAMBLING_TURN_SECONDS` (45 s). On timeout: check if free, otherwise fold. Two
   consecutive timeouts remove the player (folded, cashed out after the hand).
-- Between hands: a short recap embed (winners, pots, shown hands), then the next hand. Busted players
-  (0 chips) are cashed out for 0 and removed.
+- Between hands: a recap embed for a few seconds (winners, pots, shown hands, stacks, who left),
+  then the next hand. Busted players (0 chips) are cashed out for 0 and removed. A later pot that
+  only one player is eligible for (uncalled chips) is shown as "gets N back", not as a win.
+- The pure `PokerTable` (`engines/pokertable.py`) keeps the table between hands: blinds are 1% / 2% of
+  the buy-in rounded up (minimum 1 and 2), the button moves to the next seated player each hand, and
+  the table ends at the hand cap or when fewer than two players are left. The runner only plays
+  hands and calls `TableManager.cash_out` / `settle_poker`.
 - Embeds go through `EmbedFactory`; errors ephemeral; `allowed_mentions` none; at most one emoji and
   one quip per embed; plain error text.
 - Hole cards are only ever sent in ephemeral replies; they are never put in the public embed except
@@ -120,10 +128,12 @@ Follows `bjtable_views.py`: one public message edited in place, a lock around st
 
 ### 3.4 Restart and failure handling
 
-No new persistence. The existing startup sweep refunds every open bet (poker bets capped to what is
-left of the table, see §3.2); `TablesCog.reconcile` already
-closes a table left marked active and reposts the Play embed. A player who leaves the server mid-hand
-is folded and cashed out after the hand.
+No new persistence. The existing startup sweep refunds every open bet (poker bets get their share of
+what is left of the table, see §3.2); `TablesCog.reconcile` already
+closes a table left marked active and reposts the Play embed. A player who leaves the server or goes
+quiet mid-game is removed by the two-missed-turns rule (folded each turn, cashed out after the hand);
+the bot does not watch server membership, because without the members intent its cache cannot tell
+"left" from "not cached".
 
 ## 4. Configuration
 
```

Then tick the four P3 boxes in `TASKS.md` (`- [x]`).

- [ ] **Step 2: Full verification**

Run, in order: `python -m pytest -q`, `python -m ruff check .`, `python -m ruff format --check .`.
Expected: all clean. Report the actual test count.

- [ ] **Step 3: Commit and tag**

```bash
git add docs README.md TASKS.md
git commit -m "docs(poker): document the poker table and record phase P3" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
git tag checkpoint/poker-p3
```

Then stop and wait for the owner's review (working agreement 2). Summarise what changed and list anything unverified. The manual real-Discord checklist in `TASKS.md` (P3 acceptance) has not been run and must be listed as unverified.

---

## Self-review

- **Spec coverage (§2, §3.3, §3.4):** all buttons and the Raise form, private hole cards, timers and the two-miss rule, Leave, bust payouts, recap and final embeds, hand cap and settlement, failure refund (Tasks 1 to 3); docs and the server-leaver ruling (Task 4). The restart path is P2's sweep plus the existing `TablesCog.reconcile`.
- **Placeholders:** none. Every diff was produced by building the phase on a scratch branch (1994 tests, ruff clean) before this plan was written.
- **Type consistency:** `PokerTable`, `CashOut`, `PokerTableView`, `RaiseModal`, `run_poker`, `parse_chips`, `poker_table`, `poker_hand_result` and `poker_final` are spelled identically in the interface blocks and the diffs.
