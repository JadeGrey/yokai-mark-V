"""The pure lottery rules: parsing choices, quick picks, planning a purchase, the draw."""

from __future__ import annotations

import random

import pytest

from yokai.errors import InvalidGameChoiceError, TicketLimitError
from yokai.gambling.engines.lottery import (
    draw_number,
    hit_chance,
    parse_count,
    parse_numbers,
    plan_tickets,
    quick_picks,
    split_pot,
)


def test_numbers_may_be_separated_by_commas_or_spaces_and_repeats_are_dropped() -> None:
    assert parse_numbers("3, 7  19,7", 20) == [3, 7, 19]


def test_blank_text_means_no_numbers() -> None:
    assert parse_numbers("", 20) == []
    assert parse_numbers("  , ,", 20) == []


@pytest.mark.parametrize("text", ["0", "21", "-3", "abc", "3.5", "1;2", "7x", "٣"])
def test_anything_that_is_not_a_number_in_range_is_refused(text: str) -> None:
    with pytest.raises(InvalidGameChoiceError):
        parse_numbers(text, 20)


def test_a_refusal_escapes_markdown_and_mention_syntax_before_showing_the_text() -> None:
    """Embeds cannot ping, but live markdown, links and <@id> mentions must be neutralised."""
    with pytest.raises(InvalidGameChoiceError) as caught:
        parse_numbers("**bold**", 20)
    assert r"\*\*bold\*\*" in caught.value.message
    with pytest.raises(InvalidGameChoiceError) as caught:
        parse_numbers("<@123>", 20)
    assert "<@123>" not in caught.value.message and r"\<@123\>" in caught.value.message


def test_a_huge_number_is_refused_not_computed() -> None:
    with pytest.raises(InvalidGameChoiceError):
        parse_numbers("9" * 5000, 20)


def test_counts_are_whole_numbers_and_blank_is_zero() -> None:
    assert parse_count("") == 0 and parse_count("  ") == 0
    assert parse_count(" 4 ") == 4
    for bad in ("-1", "2.5", "x", "٣"):
        with pytest.raises(InvalidGameChoiceError):
            parse_count(bad)


def test_quick_picks_are_distinct_free_and_sorted() -> None:
    picks = quick_picks(5, taken={1, 2, 3}, number_range=20, rng=random.Random(4))
    assert picks == sorted(picks) and len(set(picks)) == 5
    assert not set(picks) & {1, 2, 3} and all(1 <= p <= 20 for p in picks)


def test_quick_picks_are_deterministic_for_a_seed() -> None:
    assert quick_picks(4, set(), 20, random.Random(9)) == quick_picks(
        4, set(), 20, random.Random(9)
    )


def test_quick_picks_cannot_exceed_the_free_numbers() -> None:
    with pytest.raises(InvalidGameChoiceError):
        quick_picks(3, taken=set(range(1, 19)), number_range=20, rng=random.Random(1))


def test_the_draw_stays_in_range_and_can_reach_every_number() -> None:
    rng = random.Random(11)
    drawn = {draw_number(20, rng) for _ in range(2000)}
    assert drawn == set(range(1, 21))


def test_the_pot_splits_evenly_and_the_remainder_stays() -> None:
    assert split_pot(1000, 3) == (333, 1)
    assert split_pot(1000, 1) == (1000, 0)
    assert split_pot(0, 2) == (0, 0)


def test_with_no_winners_the_whole_pot_is_left() -> None:
    assert split_pot(1000, 0) == (0, 1000)


def test_the_hit_chance_matches_the_published_table() -> None:
    assert round(hit_chance(10, 20), 2) == 0.40
    assert round(hit_chance(20, 20), 2) == 0.64
    assert round(hit_chance(5, 30), 2) == 0.16
    assert hit_chance(0, 20) == 0.0


def test_a_purchase_of_hand_picked_numbers_only() -> None:
    assert plan_tickets([], "3, 7", "", 20, 10, random.Random(1)) == [3, 7]


def test_a_purchase_of_quick_picks_only_avoids_what_is_held() -> None:
    picks = plan_tickets([1, 2, 3], "", "4", 20, 10, random.Random(1))
    assert len(picks) == 4 and not set(picks) & {1, 2, 3}


def test_a_purchase_can_mix_both_without_overlap() -> None:
    picks = plan_tickets([], "5, 6", "3", 20, 10, random.Random(2))
    assert picks[:2] == [5, 6] and len(picks) == 5 and len(set(picks)) == 5


def test_an_empty_purchase_is_refused() -> None:
    with pytest.raises(InvalidGameChoiceError):
        plan_tickets([], "", "", 20, 10, random.Random(1))
    with pytest.raises(InvalidGameChoiceError):
        plan_tickets([], " ", "0", 20, 10, random.Random(1))


def test_numbers_already_held_are_refused() -> None:
    with pytest.raises(InvalidGameChoiceError, match="already hold 7"):
        plan_tickets([7], "7, 8", "", 20, 10, random.Random(1))


def test_going_past_the_cap_is_refused_before_anything_is_picked() -> None:
    with pytest.raises(TicketLimitError):
        plan_tickets([1] * 0 + list(range(1, 9)), "9, 10, 11", "", 20, 10, random.Random(1))
    with pytest.raises(TicketLimitError):
        plan_tickets([], "", "11", 20, 10, random.Random(1))


def test_a_huge_quick_pick_count_is_refused_without_sampling() -> None:
    with pytest.raises(TicketLimitError):
        plan_tickets([], "", "99999999", 20, 10, random.Random(1))
