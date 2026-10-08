"""Plain data shared by the multiplayer table code."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class MultiplayerChannel:
    """A channel dedicated to one multiplayer game."""

    channel_id: int
    game: str
    play_message_id: Optional[int]
    table_message_id: Optional[int]
    table_active: bool


@dataclass(frozen=True)
class LotteryState:
    pot: int
    draw_no: int  # the draw tickets are currently being sold for
    next_draw_ts: Optional[int]  # None until the draw clock has started


@dataclass(frozen=True)
class LotteryDraw:
    draw_no: int
    ts: int
    winning_number: int
    pot: int  # the pot the draw was made against
    winners: tuple[int, ...]  # user ids; empty when the pot rolled over
    prize_each: int
    announced: bool


@dataclass(frozen=True)
class LotteryPurchase:
    draw_no: int
    numbers: tuple[int, ...]
    cost: int
    new_balance: int
    pot: int
