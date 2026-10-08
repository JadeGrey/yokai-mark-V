"""The table embeds obey the layout rules and carry the facts people need."""

from __future__ import annotations

import pytest

from tests.embed_helpers import all_embed_text, emoji_count, total_embed_length
from yokai.theme import COLOR_BRAND, COLOR_IDLE, COLOR_SUCCESS
from yokai.ui.embeds import EmbedFactory


def _embeds() -> list[tuple[str, object]]:
    return [
        ("play", EmbedFactory.table_play("Duel", "Rules here.", 10, 5000)),
        (
            "lobby",
            EmbedFactory.table_lobby("Duel", [(1, 100)], 2, 2, closes_ts=1_700_000_060, pot=100),
        ),
        ("lobby-empty", EmbedFactory.table_lobby("Duel", [], 2, 4, closes_ts=1_700_000_060, pot=0)),
        ("notice", EmbedFactory.table_notice("Table cancelled", "Stakes were refunded.")),
        ("picking", EmbedFactory.duel_picking([1, 2], 100, 200, 2, 1_700_000_045, [1])),
        ("win", EmbedFactory.duel_result({1: 4, 2: 9}, 5, 1, 196, 4)),
        ("tie", EmbedFactory.duel_result({1: 3, 2: 7}, 5, None, 0, 0)),
    ]


@pytest.mark.parametrize("label,embed", _embeds())
def test_every_table_embed_obeys_the_layout_rules(label: str, embed) -> None:  # type: ignore[no-untyped-def]
    assert emoji_count(embed) <= 1, label
    assert total_embed_length(embed) <= 6000, label
    assert len(embed.fields) <= 25, label
    assert "♠" not in all_embed_text(embed) and "♥" not in all_embed_text(embed)


def test_the_play_embed_explains_the_game_and_the_stake_range() -> None:
    embed = EmbedFactory.table_play("Duel", "Rules here.", 10, 5000)
    text = all_embed_text(embed)
    assert "Duel" in text and "Rules here." in text and "Play" in text
    assert "10" in text and "5,000" in text


def test_the_lobby_lists_players_pot_and_a_live_countdown() -> None:
    embed = EmbedFactory.table_lobby("Duel", [(1, 100)], 2, 2, closes_ts=1_700_000_060, pot=100)
    text = all_embed_text(embed)
    assert "<@1>" in text and "100" in text
    assert "<t:1700000060:R>" in text
    assert "1/2" in text


def test_a_notice_is_plain_and_idle_coloured() -> None:
    embed = EmbedFactory.table_notice("Table cancelled", "Stakes were refunded.")
    assert embed.color is not None and embed.color.value == COLOR_IDLE
    assert emoji_count(embed) == 0


def test_the_picking_embed_hides_the_picks_and_shows_who_locked_in() -> None:
    embed = EmbedFactory.duel_picking([1, 2], 100, 200, 2, 1_700_000_045, [1])
    text = all_embed_text(embed)
    assert "<@1>" in text and "<@2>" in text
    assert "Locked in" in text and "<t:1700000045:R>" in text
    assert "hidden" in text.lower() or "secret" in text.lower()


def test_the_result_names_the_winner_the_prize_and_the_hidden_number() -> None:
    embed = EmbedFactory.duel_result({1: 4, 2: 9}, 5, 1, 196, 4)
    text = all_embed_text(embed)
    assert "<@1>" in text and "196" in text and "**5**" in text
    assert "picked **4**" in text and "picked **9**" in text
    assert embed.color is not None and embed.color.value == COLOR_SUCCESS


def test_a_tied_result_says_the_stakes_came_back() -> None:
    embed = EmbedFactory.duel_result({1: 3, 2: 7}, 5, None, 0, 0)
    text = all_embed_text(embed).lower()
    assert "tie" in text and "returned" in text
    assert embed.color is not None and embed.color.value == COLOR_BRAND


def test_a_result_with_no_rake_does_not_mention_one() -> None:
    embed = EmbedFactory.duel_result({1: 4, 2: 9}, 5, 1, 200, 0)
    assert "rake" not in all_embed_text(embed).lower()
