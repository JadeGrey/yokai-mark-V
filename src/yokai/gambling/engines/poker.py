"""Texas hold'em: card evaluator, one-hand betting state machine and side pots. No Discord."""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum
from itertools import combinations
from typing import Mapping, Optional, Sequence

from yokai.errors import InvalidPokerActionError, NotYourTurnError
from yokai.gambling.engines.blackjack import SUITS, Card

HIGH_CARD, PAIR, TWO_PAIR, TRIPS, STRAIGHT, FLUSH, FULL_HOUSE, QUADS, STRAIGHT_FLUSH = range(9)
CATEGORY_NAMES = (
    "high card",
    "a pair",
    "two pair",
    "three of a kind",
    "a straight",
    "a flush",
    "a full house",
    "four of a kind",
    "a straight flush",
)

HandRank = tuple[int, ...]


def build_deck(rng: random.Random) -> list[Card]:
    """A shuffled 52-card deck."""
    deck = [Card(rank, suit) for suit in SUITS for rank in range(1, 14)]
    rng.shuffle(deck)
    return deck


def _value(card: Card) -> int:
    return 14 if card.rank == 1 else card.rank


def evaluate_five(cards: Sequence[Card]) -> HandRank:
    """Rank exactly five cards: (category, tiebreakers...). Higher tuples win."""
    if len(cards) != 5:
        raise ValueError("evaluate_five needs exactly five cards")
    values = sorted((_value(c) for c in cards), reverse=True)
    flush = len({c.suit for c in cards}) == 1
    distinct = sorted(set(values), reverse=True)
    straight_high = 0
    if len(distinct) == 5:
        if distinct[0] - distinct[4] == 4:
            straight_high = distinct[0]
        elif distinct == [14, 5, 4, 3, 2]:
            straight_high = 5  # the wheel: the ace plays low
    counts: dict[int, int] = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    # Most repeated first, then highest value: gives the right kicker order for every category.
    groups = sorted(counts.items(), key=lambda item: (item[1], item[0]), reverse=True)
    shape = [n for _, n in groups]
    ordered = tuple(v for v, _ in groups)
    if straight_high and flush:
        return (STRAIGHT_FLUSH, straight_high)
    if shape == [4, 1]:
        return (QUADS, *ordered)
    if shape == [3, 2]:
        return (FULL_HOUSE, *ordered)
    if flush:
        return (FLUSH, *values)
    if straight_high:
        return (STRAIGHT, straight_high)
    if shape == [3, 1, 1]:
        return (TRIPS, *ordered)
    if shape == [2, 2, 1]:
        return (TWO_PAIR, *ordered)
    if shape == [2, 1, 1, 1]:
        return (PAIR, *ordered)
    return (HIGH_CARD, *values)


def best_hand(cards: Sequence[Card]) -> HandRank:
    """Best five-card rank among five to seven cards."""
    if not 5 <= len(cards) <= 7:
        raise ValueError("best_hand needs five to seven cards")
    return max(evaluate_five(combo) for combo in combinations(cards, 5))


def describe(rank: HandRank) -> str:
    return CATEGORY_NAMES[rank[0]]


class Street(Enum):
    PREFLOP = "preflop"
    FLOP = "flop"
    TURN = "turn"
    RIVER = "river"
    SHOWDOWN = "showdown"


@dataclass(frozen=True)
class LegalActions:
    can_check: bool
    call_amount: int  # chips to call (0 when checking); capped at the player's stack
    min_raise_to: Optional[int]  # total street bet for a minimum raise; None = cannot raise
    max_raise_to: Optional[int]  # the player's all-in total for the street
    all_in_to: int  # total street bet if they push everything


@dataclass(frozen=True)
class PotAward:
    amount: int
    eligible: tuple[int, ...]
    winners: tuple[int, ...]


@dataclass(frozen=True)
class HandResult:
    stacks: dict[int, int]  # chips after the hand
    pots: tuple[PotAward, ...]
    shown: dict[int, tuple[Card, Card]]  # hole cards revealed at a showdown (empty if none)
    ranks: dict[int, HandRank]  # best hand of each showdown player (empty if none)
    showdown: bool


def split_pots(contrib: Mapping[int, int], live: set[int]) -> list[tuple[int, tuple[int, ...]]]:
    """Side pots as (amount, eligible player ids). `live` are the players who did not fold.

    Levels with the same contenders form one pot. Chips no live player can win (impossible in a
    legal hand) are merged into the previous pot.
    """
    pots: list[tuple[int, tuple[int, ...]]] = []
    previous = 0
    for level in sorted({c for c in contrib.values() if c > 0}):
        amount = sum(min(c, level) - min(c, previous) for c in contrib.values())
        eligible = tuple(u for u in contrib if u in live and contrib[u] >= level)
        if pots and (not eligible or eligible == pots[-1][1]):
            pots[-1] = (pots[-1][0] + amount, pots[-1][1])  # same contenders: one pot
        else:
            pots.append((amount, eligible))
        previous = level
    return pots


class PokerHand:
    """One hand of no-limit hold'em for 2 to 10 players, all with chips.

    `order` is the seating order (clockwise); `button` is the dealer's user id. Cards are dealt
    from the front of `deck`: two hole cards each (starting left of the button), then the board.
    """

    def __init__(
        self,
        stacks: Mapping[int, int],
        order: Sequence[int],
        button: int,
        small_blind: int,
        big_blind: int,
        deck: Sequence[Card],
    ) -> None:
        if not 2 <= len(order) <= 10 or set(order) != set(stacks) or len(set(order)) != len(order):
            raise ValueError("order must list each player with a stack exactly once (2 to 10)")
        if button not in stacks:
            raise ValueError("the button must be a seated player")
        if any(stacks[u] <= 0 for u in order):
            raise ValueError("every player in a hand needs chips")
        if not 0 < small_blind <= big_blind:
            raise ValueError("blinds must satisfy 0 < small <= big")
        self.order = list(order)
        self.button = button
        self.big_blind = big_blind
        self.stacks = dict(stacks)
        self.contrib = {u: 0 for u in self.order}
        self.bet = {u: 0 for u in self.order}  # chips put in on the current street
        self.folded: set[int] = set()
        self.board: list[Card] = []
        self.street = Street.PREFLOP
        self.current_bet = 0
        self.min_raise = big_blind
        self._deck = list(deck)
        self._next = 0
        self._needs: set[int] = set()
        self._no_raise: set[int] = set()  # acted already and only faced a short all-in since
        self._turn: Optional[int] = None
        self._result: Optional[HandResult] = None

        start = self.order.index(button)
        self.holes: dict[int, list[Card]] = {u: [] for u in self.order}
        for _ in range(2):
            for i in range(1, len(self.order) + 1):
                self.holes[self.order[(start + i) % len(self.order)]].append(self._draw())

        if len(self.order) == 2:
            small, big, first = button, self._after(button), button
        else:
            small, big = self._after(button), self._after(self._after(button))
            first = self._after(big)
        self._post(small, small_blind)
        self._post(big, big_blind)
        self.current_bet = max(self.bet.values())
        self._needs = {u for u in self.order if not self._is_all_in(u)}
        self._settle_turn(first)

    # ---- queries ---------------------------------------------------------------------------

    @property
    def current(self) -> Optional[int]:
        """The player to act, or None once the hand is over."""
        return self._turn

    @property
    def is_over(self) -> bool:
        return self._result is not None

    @property
    def pot(self) -> int:
        return sum(self.contrib.values())

    def result(self) -> HandResult:
        if self._result is None:
            raise ValueError("the hand is not over")
        return self._result

    def in_hand(self) -> list[int]:
        """Players who have not folded, in seat order."""
        return [u for u in self.order if u not in self.folded]

    def legal_actions(self, user_id: int) -> LegalActions:
        self._require_turn(user_id)
        stack = self.stacks[user_id]
        to_call = min(self.current_bet - self.bet[user_id], stack)
        all_in_to = self.bet[user_id] + stack
        can_raise = (
            user_id not in self._no_raise
            and all_in_to > self.current_bet
            and self._others_can_respond(user_id)
        )
        min_to = self.current_bet + self.min_raise
        return LegalActions(
            can_check=self.current_bet == self.bet[user_id],
            call_amount=to_call,
            min_raise_to=min(min_to, all_in_to) if can_raise else None,
            max_raise_to=all_in_to if can_raise else None,
            all_in_to=all_in_to,
        )

    # ---- actions ---------------------------------------------------------------------------

    def fold(self, user_id: int) -> None:
        self._require_turn(user_id)
        self.folded.add(user_id)
        self._finish_action(user_id)

    def check(self, user_id: int) -> None:
        self._require_turn(user_id)
        if self.current_bet != self.bet[user_id]:
            raise InvalidPokerActionError("You can't check; there is a bet to call.")
        self._finish_action(user_id)

    def call(self, user_id: int) -> None:
        self._require_turn(user_id)
        if self.current_bet == self.bet[user_id]:
            raise InvalidPokerActionError("There is nothing to call; check instead.")
        self._put_in(user_id, min(self.current_bet - self.bet[user_id], self.stacks[user_id]))
        self._finish_action(user_id)

    def raise_to(self, user_id: int, total: int) -> None:
        """Raise so the player's total bet on this street is `total` (an all-in may be short)."""
        legal = self.legal_actions(user_id)  # also checks the turn
        if legal.min_raise_to is None or legal.max_raise_to is None:
            raise InvalidPokerActionError("You can't raise here.")
        if total > legal.max_raise_to:
            raise InvalidPokerActionError(f"You only have enough to raise to {legal.max_raise_to}.")
        if total < legal.min_raise_to:
            raise InvalidPokerActionError(f"The minimum raise is to {legal.min_raise_to}.")
        self._raise(user_id, total)

    def all_in(self, user_id: int) -> None:
        """Push every chip: a call if that does not top the bet, otherwise a (maybe short) raise."""
        self._require_turn(user_id)
        stack = self.stacks[user_id]
        total = self.bet[user_id] + stack
        if total <= self.current_bet or not self.legal_actions(user_id).max_raise_to:
            self._put_in(user_id, min(self.current_bet - self.bet[user_id], stack))
            self._finish_action(user_id)
        else:
            self._raise(user_id, total)

    def auto_action(self, user_id: int) -> str:
        """Timeout action: check when it is free, otherwise fold. Returns 'check' or 'fold'."""
        self._require_turn(user_id)
        if self.current_bet == self.bet[user_id]:
            self.check(user_id)
            return "check"
        self.fold(user_id)
        return "fold"

    # ---- internals -------------------------------------------------------------------------

    def _draw(self) -> Card:
        card = self._deck[self._next]
        self._next += 1
        return card

    def _after(self, user_id: int) -> int:
        return self.order[(self.order.index(user_id) + 1) % len(self.order)]

    def _is_all_in(self, user_id: int) -> bool:
        return self.stacks[user_id] == 0

    def _require_turn(self, user_id: int) -> None:
        if self._turn is None:
            raise InvalidPokerActionError("This hand is over.")
        if user_id != self._turn:
            raise NotYourTurnError()

    def _put_in(self, user_id: int, amount: int) -> None:
        self.stacks[user_id] -= amount
        self.bet[user_id] += amount
        self.contrib[user_id] += amount

    def _post(self, user_id: int, blind: int) -> None:
        self._put_in(user_id, min(blind, self.stacks[user_id]))

    def _others_can_respond(self, user_id: int) -> bool:
        return any(
            u != user_id and u not in self.folded and not self._is_all_in(u) for u in self.order
        )

    def _raise(self, user_id: int, total: int) -> None:
        size = total - self.current_bet
        self._put_in(user_id, total - self.bet[user_id])
        if size >= self.min_raise:
            self.min_raise = size
            self._no_raise.clear()
        else:
            # A short all-in: players who already acted may call or fold but not re-raise.
            self._no_raise |= {u for u in self.order if u not in self._needs and u != user_id}
        self.current_bet = total
        self._needs |= {
            u
            for u in self.order
            if u != user_id and u not in self.folded and not self._is_all_in(u)
        }
        self._finish_action(user_id)

    def _finish_action(self, user_id: int) -> None:
        self._needs.discard(user_id)
        self._settle_turn(self._after(user_id))

    def _settle_turn(self, start: int) -> None:
        """Hand the turn to the next player who must act, or advance the street / end the hand."""
        while True:
            if len(self.in_hand()) == 1:
                self._turn = None
                self._finish_hand()
                return
            self._needs -= self.folded | {u for u in self.order if self._is_all_in(u)}
            if len(self._needs) == 1:
                (only,) = self._needs
                if self.bet[only] >= self.current_bet and not self._others_can_respond(only):
                    self._needs.clear()  # everyone else is all-in: nothing left to decide
            if self._needs:
                cursor = start
                for _ in range(len(self.order)):
                    if cursor in self._needs:
                        self._turn = cursor
                        return
                    cursor = self._after(cursor)
            if self.street is Street.RIVER:
                self._turn = None
                self._finish_hand()
                return
            self._next_street()
            start = self._after(self.button)

    def _next_street(self) -> None:
        self.street = {
            Street.PREFLOP: Street.FLOP,
            Street.FLOP: Street.TURN,
            Street.TURN: Street.RIVER,
        }[self.street]
        for _ in range(3 if self.street is Street.FLOP else 1):
            self.board.append(self._draw())
        self.bet = {u: 0 for u in self.order}
        self.current_bet = 0
        self.min_raise = self.big_blind
        self._no_raise.clear()
        live = [u for u in self.in_hand() if not self._is_all_in(u)]
        self._needs = set(live) if len(live) > 1 else set()

    def _finish_hand(self) -> None:
        contenders = self.in_hand()
        showdown = len(contenders) > 1
        ranks: dict[int, HandRank] = {}
        if showdown:
            while len(self.board) < 5:
                self.board.append(self._draw())
            self.street = Street.SHOWDOWN
            ranks = {u: best_hand([*self.holes[u], *self.board]) for u in contenders}
        stacks = dict(self.stacks)
        awards: list[PotAward] = []
        seats = self._seats_from(self._after(self.button))
        for amount, eligible in split_pots(self.contrib, set(contenders)):
            if len(eligible) == 1 or not showdown:
                winners = eligible
            else:
                top = max(ranks[u] for u in eligible)
                winners = tuple(u for u in eligible if ranks[u] == top)
            share, odd = divmod(amount, len(winners))
            ordered = sorted(winners, key=seats.index)
            for i, winner in enumerate(ordered):
                stacks[winner] += share + (1 if i < odd else 0)
            awards.append(PotAward(amount, eligible, tuple(ordered)))
        shown = {u: (self.holes[u][0], self.holes[u][1]) for u in contenders} if showdown else {}
        self._result = HandResult(stacks, tuple(awards), shown, ranks, showdown)

    def _seats_from(self, start: int) -> list[int]:
        i = self.order.index(start)
        return self.order[i:] + self.order[:i]
