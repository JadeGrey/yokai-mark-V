"""The poker embeds obey the layout rules and show the table state without private cards."""

from __future__ import annotations

import pytest

from tests.embed_helpers import all_embed_text, emoji_count, total_embed_length
from yokai.ui.embeds import EmbedFactory

SEATS = [
    (1, 980, 20, "to act", True),
    (2, 990, 10, "", False),
    (3, 1000, 0, "folded", False),
    (4, 0, 500, "all-in", False),
]


def _table(**overrides):  # type: ignore[no-untyped-def]
    args = dict(
        hand_no=3,
        max_hands=30,
        street="flop",
        board=["As", "Kh", "10d"],
        pot=530,
        small_blind=10,
        big_blind=20,
        seats=SEATS,
        deadline_ts=1_700_000_045,
    )
    args.update(overrides)
    return EmbedFactory.poker_table(**args)


def _recap(**overrides):  # type: ignore[no-untyped-def]
    args = dict(
        hand_no=3,
        max_hands=30,
        board=["As", "Kh", "10d", "2c", "9s"],
        awards=[(300, [1], False), (40, [2, 3], False), (25, [4], True)],
        shown={1: ("As Ad", "three of a kind"), 2: ("Kc Qd", "a pair")},
        stacks={1: 1300, 2: 960, 3: 1020, 4: 25},
        left=[(4, 25, "bust")],
    )
    args.update(overrides)
    return EmbedFactory.poker_hand_result(**args)


def _final(**overrides):  # type: ignore[no-untyped-def]
    args = dict(
        hands_played=12,
        max_hands=30,
        buy_in=1000,
        results=[(1, 1500), (2, 1000), (3, 500), (4, 0)],
    )
    args.update(overrides)
    return EmbedFactory.poker_final(**args)


@pytest.mark.parametrize(
    "label,embed",
    [
        ("table", _table()),
        ("table-preflop", _table(street="preflop", board=[], deadline_ts=None)),
        ("recap", _recap()),
        ("recap-fold", _recap(shown={}, awards=[(30, [1], False)], left=[])),
        ("final", _final()),
        ("final-flat", _final(results=[(1, 1000), (2, 1000)])),
    ],
)
def test_every_poker_embed_obeys_the_layout_rules(label: str, embed) -> None:  # type: ignore[no-untyped-def]
    assert emoji_count(embed) <= 1, label
    assert total_embed_length(embed) <= 6000, label
    assert len(embed.fields) <= 25, label
    text = all_embed_text(embed)
    assert not any(glyph in text for glyph in "♠♥♦♣"), label


def test_the_table_shows_board_pot_blinds_stacks_bets_and_who_is_up() -> None:
    text = all_embed_text(_table())
    assert "As Kh 10d" in text and "Flop" in text
    assert "530" in text and "10/20" in text
    assert "Hand 3/30" in text
    assert "<@1>" in text and "980" in text and "to act" in text
    assert "<@3>" in text and "folded" in text
    assert "<@4>" in text and "all-in" in text
    assert "<t:1700000045:R>" in text


def test_the_dealer_button_is_marked_once() -> None:
    assert all_embed_text(_table()).count("(D)") == 1


def test_a_table_before_the_flop_says_so_and_has_no_countdown_when_nobody_is_up() -> None:
    text = all_embed_text(_table(street="preflop", board=[], deadline_ts=None))
    assert "No community cards yet" in text and "<t:" not in text


def test_the_recap_names_winners_splits_returns_and_shows_hands() -> None:
    text = all_embed_text(_recap())
    assert "<@1>" in text and "300" in text
    assert "split" in text and "40" in text
    assert "back" in text and "25" in text  # the uncalled chips are returned, not "won"
    assert "As Ad" in text and "three of a kind" in text
    assert "<@4>" in text and "bust" in text


def test_a_hand_won_without_a_showdown_reveals_no_cards() -> None:
    text = all_embed_text(_recap(shown={}, awards=[(30, [1], False)], left=[]))
    assert "wins" in text and "30" in text
    assert "As Ad" not in text and "Kc Qd" not in text


def test_the_final_embed_shows_each_players_result() -> None:
    text = all_embed_text(_final())
    assert "12/30" in text or "12 hands" in text
    assert "<@1>" in text and "+500" in text
    assert "<@3>" in text and "-500" in text
    assert "<@2>" in text and "even" in text.lower()
