"""Lottery rules: pick numbers, one drawn number wins, holders split the pot. Pure functions."""

from __future__ import annotations

import random
from typing import Collection

from yokai.errors import InvalidGameChoiceError, TicketLimitError
from yokai.textutil import escape_md

MAX_TOKEN_LENGTH = 6  # a number up to 100 never needs more; longer tokens are refused unread
MAX_COUNT_DIGITS = 9  # a quick-pick count this long is refused by the cap, not as a typo


def _clip(token: str) -> str:
    """A token safe to show back to the member: shortened and with markdown escaped."""
    return escape_md(token[:20])


def parse_numbers(text: str, number_range: int) -> list[int]:
    """Numbers typed by hand, separated by commas or spaces. Repeats are dropped."""
    numbers: list[int] = []
    for token in text.replace(",", " ").split():
        if len(token) > MAX_TOKEN_LENGTH or not (token.isascii() and token.isdigit()):
            raise InvalidGameChoiceError(f"'{_clip(token)}' is not a ticket number.")
        number = int(token)
        if not 1 <= number <= number_range:
            raise InvalidGameChoiceError(f"Ticket numbers run from 1 to {number_range}.")
        if number not in numbers:
            numbers.append(number)
    return numbers


def parse_count(text: str) -> int:
    """The quick-pick count; blank means none."""
    cleaned = text.strip()
    if not cleaned:
        return 0
    if len(cleaned) > MAX_COUNT_DIGITS or not (cleaned.isascii() and cleaned.isdigit()):
        raise InvalidGameChoiceError("Quick picks must be a whole number, like 3.")
    return int(cleaned)


def quick_picks(
    count: int, taken: Collection[int], number_range: int, rng: random.Random
) -> list[int]:
    """`count` random numbers that are not in `taken`, distinct and sorted."""
    free = [n for n in range(1, number_range + 1) if n not in taken]
    if count > len(free):
        raise InvalidGameChoiceError("There aren't that many free numbers left.")
    return sorted(rng.sample(free, count))


def plan_tickets(
    held: Collection[int],
    numbers_text: str,
    quick_text: str,
    number_range: int,
    max_per_user: int,
    rng: random.Random,
) -> list[int]:
    """The new ticket numbers for one purchase: hand-picked first, then quick picks."""
    chosen = parse_numbers(numbers_text, number_range)
    quick = parse_count(quick_text)
    if not chosen and quick == 0:
        raise InvalidGameChoiceError("Enter some numbers, a quick-pick count, or both.")
    overlap = sorted(set(chosen) & set(held))
    if overlap:
        raise InvalidGameChoiceError(f"You already hold {', '.join(str(n) for n in overlap)}.")
    if len(held) + len(chosen) + quick > max_per_user:
        raise TicketLimitError(len(held), max_per_user)
    picks = quick_picks(quick, set(held) | set(chosen), number_range, rng) if quick else []
    return chosen + picks


def draw_number(number_range: int, rng: random.Random) -> int:
    """The winning number."""
    return rng.randint(1, number_range)


def split_pot(pot: int, winner_count: int) -> tuple[int, int]:
    """Split a pot between holders of the winning number as (each, remainder left in the pot)."""
    if winner_count <= 0:
        return 0, pot
    return divmod(pot, winner_count)


def hit_chance(tickets_sold: int, number_range: int) -> float:
    """Chance that at least one of `tickets_sold` distinct tickets holds the winning number."""
    return 1.0 - (1.0 - 1.0 / number_range) ** tickets_sold
