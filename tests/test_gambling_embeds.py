"""Tests for the gambling theme additions and embed builders."""

from __future__ import annotations

import discord

from tests.embed_helpers import all_embed_text, emoji_count, total_embed_length
from yokai.theme import QUIP_VARIANTS, Emoji
from yokai.ui.embeds import EmbedFactory

SUIT_GLYPHS = "♠♥♦♣♤♡♢♧"


def _all_samples() -> list[discord.Embed]:
    return [
        EmbedFactory.spinning("Slots", "`[ ? ] [ ? ] [ ? ]`", Emoji.SLOTS),
        EmbedFactory.slots_result("<@1>", ("SEVEN", "SEVEN", "SEVEN"), 10, 500, 690),
        EmbedFactory.slots_result("<@1>", ("BAR", "LEMON", "BELL"), 10, 0, 190),
        EmbedFactory.slots_result("<@1>", ("CHERRY", "CHERRY", "LEMON"), 10, 14, 204),
        EmbedFactory.roulette_result("<@1>", "straight 17", 17, "black", 10, 360, 550),
        EmbedFactory.roulette_result("<@1>", "red", 0, "green", 10, 0, 190),
        EmbedFactory.blackjack_hand("<@1>", ["10h", "6c"], 16, ["9d", "??"], None, 10),
        EmbedFactory.blackjack_hand(
            "<@1>", ["As", "Kd"], 21, ["Ks", "9h"], 19, 10, "blackjack", 25, 215
        ),
        EmbedFactory.blackjack_hand(
            "<@1>", ["10h", "6c", "10d"], 26, ["9d", "8s"], 17, 10, "bust", 0, 190
        ),
        EmbedFactory.blackjack_hand(
            "<@1>", ["10h", "8c"], 18, ["10d", "8s"], 18, 10, "push", 10, 200
        ),
        EmbedFactory.gamble_stats("<@1>", [("blackjack", 1, 10, 20), ("slots", 2, 20, 40)]),
        EmbedFactory.gamble_stats("<@1>", []),
    ]


def test_new_emoji_and_quip_keys_exist() -> None:
    assert Emoji.SLOTS and Emoji.DICE
    for key in ("game_win", "game_loss", "game_push", "game_blackjack", "game_bust"):
        assert len(QUIP_VARIANTS[key]) >= 3


def test_every_gambling_embed_obeys_the_layout_rules() -> None:
    for embed in _all_samples():
        assert emoji_count(embed) <= 1, embed.title
        assert total_embed_length(embed) <= 6000
        assert not any(glyph in all_embed_text(embed) for glyph in SUIT_GLYPHS)


def test_slots_win_shows_text_reels_and_a_signed_net() -> None:
    embed = EmbedFactory.slots_result("<@1>", ("SEVEN", "SEVEN", "SEVEN"), 10, 500, 690)
    assert "[SEVEN] [SEVEN] [SEVEN]" in (embed.description or "")
    text = all_embed_text(embed)
    assert "+490" in text and "690" in text


def test_slots_loss_and_push_net_text() -> None:
    loss = all_embed_text(EmbedFactory.slots_result("<@1>", ("BAR", "LEMON", "BELL"), 10, 0, 190))
    assert "-10" in loss
    push = EmbedFactory.slots_result("<@1>", ("BAR", "LEMON", "BELL"), 10, 10, 200)
    assert any(f.name == "Net" and f.value == "**0**" for f in push.fields)


def test_blackjack_in_play_shows_the_hidden_dealer_card_and_no_result() -> None:
    embed = EmbedFactory.blackjack_hand("<@1>", ["10h", "6c"], 16, ["9d", "??"], None, 10)
    assert "`9d ??`" in (embed.description or "")
    assert "10h 6c" in (embed.description or "")
    assert not any(f.name == "Net" for f in embed.fields)


def test_blackjack_finished_shows_verdict_net_and_balance() -> None:
    embed = EmbedFactory.blackjack_hand(
        "<@1>", ["As", "Kd"], 21, ["Ks", "9h"], 19, 10, "blackjack", 25, 215
    )
    assert "Blackjack!" in (embed.description or "")
    text = all_embed_text(embed)
    assert "+15" in text and "215" in text


def test_roulette_result_names_the_pocket_and_colour() -> None:
    embed = EmbedFactory.roulette_result("<@1>", "straight 17", 17, "black", 10, 360, 550)
    description = embed.description or ""
    assert "17" in description and "black" in description and "straight 17" in description


def test_gamble_stats_lists_games_with_net_or_says_none_yet() -> None:
    filled = EmbedFactory.gamble_stats("<@1>", [("slots", 2, 20, 40)])
    assert "`slots`" in (filled.description or "") and "+20" in all_embed_text(filled)
    empty = EmbedFactory.gamble_stats("<@1>", [])
    assert "No settled games yet" in (empty.description or "")
