"""The jackpot, crash and blackjack table embeds obey the layout rules."""

from __future__ import annotations

import pytest

from tests.embed_helpers import all_embed_text, emoji_count, total_embed_length
from yokai.theme import COLOR_BRAND, COLOR_SUCCESS
from yokai.ui.embeds import EmbedFactory

TEN = [(uid, 100 * uid) for uid in range(1, 11)]
CRASH_ROWS = [(uid, 100, 231 if uid % 2 else None) for uid in range(1, 11)]
SEATS = [
    (1, 100, ["10h", "6c"], 16, "to act"),
    (2, 50, ["As", "Kd"], 21, "blackjack"),
    (3, 25, ["9d", "9s", "10c"], 28, "bust"),
    (4, 10, ["10d", "8c"], 18, "stand"),
    (5, 10, ["2d", "3c"], 5, "waiting"),
]


def _embeds() -> list[tuple[str, object]]:
    return [
        ("jackpot-drawing", EmbedFactory.jackpot_drawing(TEN, 5500, 2)),
        ("jackpot-result", EmbedFactory.jackpot_result(TEN, 4, 5390, 110)),
        ("crash-flying", EmbedFactory.crash_flying(CRASH_ROWS, 231)),
        ("crash-result", EmbedFactory.crash_result(CRASH_ROWS, 412, {1: 231, 3: 231})),
        ("bj-playing", EmbedFactory.bjtable_hand(SEATS, ["9h", "??"], None, 1_700_000_045, False)),
        ("bj-final", EmbedFactory.bjtable_hand(SEATS, ["9h", "10c"], 19, None, True)),
    ]


@pytest.mark.parametrize("label,embed", _embeds())
def test_every_embed_obeys_the_layout_rules(label: str, embed) -> None:  # type: ignore[no-untyped-def]
    assert emoji_count(embed) <= 1, label
    assert total_embed_length(embed) <= 6000, label
    assert len(embed.fields) <= 25, label
    text = all_embed_text(embed)
    assert "♠" not in text and "♥" not in text and "♦" not in text and "♣" not in text


def test_the_jackpot_shows_each_players_chance_of_winning() -> None:
    text = all_embed_text(EmbedFactory.jackpot_drawing([(1, 100), (2, 300)], 400, 2))
    assert "<@1>" in text and "25%" in text and "<@2>" in text and "75%" in text
    assert "400" in text and "2%" in text


def test_the_jackpot_result_names_the_winner_and_the_prize() -> None:
    embed = EmbedFactory.jackpot_result([(1, 100), (2, 300)], 2, 392, 8)
    text = all_embed_text(embed)
    assert "<@2> wins 392" in text and "8" in text
    assert embed.color is not None and embed.color.value == COLOR_SUCCESS


def test_a_jackpot_with_no_rake_does_not_mention_one() -> None:
    text = all_embed_text(EmbedFactory.jackpot_result([(1, 100), (2, 100)], 1, 200, 0)).lower()
    assert "rake" not in text


def test_the_flying_crash_shows_the_multiplier_and_who_is_still_in() -> None:
    text = all_embed_text(EmbedFactory.crash_flying([(1, 100, None), (2, 50, 231)], 187))
    assert "1.87x" in text and "<@1>" in text and "still in" in text
    assert "<@2>" in text and "2.31x" in text


def test_the_crash_result_shows_the_crash_point_and_each_net() -> None:
    embed = EmbedFactory.crash_result([(1, 100, 231), (2, 100, None)], 412, {1: 231, 2: 0})
    text = all_embed_text(embed)
    assert "4.12x" in text and "+131" in text and "-100" in text
    assert embed.color is not None


def test_a_crash_where_nobody_cashed_out_reads_as_a_loss() -> None:
    embed = EmbedFactory.crash_result([(1, 100, None)], 100, {1: 0})
    assert "1.00x" in all_embed_text(embed) and "-100" in all_embed_text(embed)


def test_the_blackjack_table_lists_every_hand_and_hides_the_dealers_hole_card() -> None:
    embed = EmbedFactory.bjtable_hand(SEATS, ["9h", "??"], None, 1_700_000_045, False)
    text = all_embed_text(embed)
    assert text.count("<@") == 5 and "10h 6c" in text and "to act" in text
    assert "9h ??" in text and "<t:1700000045:R>" in text
    assert embed.color is not None and embed.color.value == COLOR_BRAND


def test_the_final_blackjack_table_shows_the_dealers_total_and_no_countdown() -> None:
    text = all_embed_text(EmbedFactory.bjtable_hand(SEATS, ["9h", "10c"], 19, None, True))
    assert "9h 10c" in text and "(19)" in text and "<t:" not in text
