"""The lottery embeds obey the layout rules and carry the facts people need."""

from __future__ import annotations

import pytest

from tests.embed_helpers import all_embed_text, emoji_count, total_embed_length
from yokai.theme import COLOR_BRAND, COLOR_SUCCESS
from yokai.ui.embeds import EmbedFactory

NEXT = 1_900_000_000


def _embeds() -> list[tuple[str, object]]:
    return [
        ("panel", EmbedFactory.lottery_panel(1500, 12, 20, 100, 10, NEXT, None)),
        ("panel-last", EmbedFactory.lottery_panel(0, 0, 20, 100, 10, NEXT, (7, [1, 2], 750))),
        ("panel-stopped", EmbedFactory.lottery_panel(0, 0, 20, 100, 10, None, None)),
        ("receipt", EmbedFactory.lottery_receipt([3, 7, 19], 300, 1800, 700, NEXT)),
        ("win", EmbedFactory.lottery_announcement(4, 7, 1500, [1, 2], 750, 0, NEXT)),
        ("rollover", EmbedFactory.lottery_announcement(5, 12, 1500, [], 0, 1500, NEXT)),
        (
            "crowd",
            EmbedFactory.lottery_announcement(6, 3, 999_999, list(range(1, 41)), 24_999, 39, NEXT),
        ),
    ]


@pytest.mark.parametrize("label,embed", _embeds())
def test_every_lottery_embed_obeys_the_layout_rules(label: str, embed) -> None:  # type: ignore[no-untyped-def]
    assert emoji_count(embed) <= 1, label
    assert total_embed_length(embed) <= 6000, label
    assert len(embed.fields) <= 25, label


def test_the_panel_shows_pot_tickets_price_range_and_a_live_countdown() -> None:
    text = all_embed_text(EmbedFactory.lottery_panel(1500, 12, 20, 100, 10, NEXT, None))
    assert "1,500" in text and "12" in text and "100" in text
    assert "1 to 20" in text and "10" in text and f"<t:{NEXT}:R>" in text
    assert "Buy tickets" in text


def test_the_panel_names_the_last_draw() -> None:
    text = all_embed_text(EmbedFactory.lottery_panel(0, 0, 20, 100, 10, NEXT, (7, [1, 2], 750)))
    assert "7" in text and "<@1>" in text and "<@2>" in text and "750" in text


def test_a_panel_before_the_clock_starts_has_no_countdown() -> None:
    text = all_embed_text(EmbedFactory.lottery_panel(0, 0, 20, 100, 10, None, None))
    assert "<t:" not in text


def test_the_receipt_lists_the_numbers_cost_and_balance() -> None:
    text = all_embed_text(EmbedFactory.lottery_receipt([3, 7, 19], 300, 1800, 700, NEXT))
    assert "3, 7, 19" in text and "300" in text and "700" in text and "1,800" in text


def test_a_win_announcement_names_the_number_the_winners_and_the_prize() -> None:
    embed = EmbedFactory.lottery_announcement(4, 7, 1500, [1, 2], 750, 0, NEXT)
    text = all_embed_text(embed)
    assert "**7**" in text and "<@1>" in text and "<@2>" in text and "750" in text
    assert embed.color is not None and embed.color.value == COLOR_SUCCESS


def test_a_rollover_announcement_says_nobody_won_and_shows_the_new_pot() -> None:
    embed = EmbedFactory.lottery_announcement(5, 12, 1500, [], 0, 1500, NEXT)
    text = all_embed_text(embed).lower()
    assert "nobody" in text and "rolls over" in text and "1,500" in text
    assert embed.color is not None and embed.color.value == COLOR_BRAND


def test_a_huge_crowd_of_winners_is_summarised_not_listed_in_full() -> None:
    embed = EmbedFactory.lottery_announcement(6, 3, 999_999, list(range(1, 41)), 24_999, 39, NEXT)
    assert total_embed_length(embed) <= 6000
    assert "40" in all_embed_text(embed)
