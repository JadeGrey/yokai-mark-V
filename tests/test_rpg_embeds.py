"""Tests for the Guild RPG embeds and the NoCharacterError."""

from __future__ import annotations

import discord

from tests.embed_helpers import emoji_count, total_embed_length
from yokai.errors import NoCharacterError, YokaiError
from yokai.theme import QUIP_VARIANTS, Emoji
from yokai.ui.embeds import EmbedFactory

STATS = [
    ("HP", "100"),
    ("Attack", "10"),
    ("Magic", "10"),
    ("Defense", "5"),
    ("Resist", "5"),
    ("Speed", "10"),
    ("Crit", "5%"),
]


def field_map(embed: discord.Embed) -> dict[str, str]:
    return {field.name: field.value for field in embed.fields}


def test_guild_join_quip_bank_has_at_least_three_variants() -> None:
    assert len(QUIP_VARIANTS["guild_join"]) >= 3
    assert Emoji.SWORDS


def test_guild_joined_embed_shape() -> None:
    embed = EmbedFactory.guild_joined("<@42>", "E", 1, STATS)
    assert "<@42>" in (embed.description or "")
    assert "rank E" in (embed.description or "")
    assert "/card" in (embed.description or "")
    fields = field_map(embed)
    for label, value in STATS:
        assert fields[label] == f"**{value}**"
    assert emoji_count(embed) == 1
    assert total_embed_length(embed) <= 6000


def test_player_card_shows_rank_level_record_and_stats() -> None:
    embed = EmbedFactory.player_card(
        "<@42>", "C", 14, 60, STATS, 31, 9, 1_700_000_000, next_rank=("B", 20)
    )
    fields = field_map(embed)
    assert fields["Rank"] == "**C**"
    assert fields["Level"] == "**14** / 60"
    assert "**31**W" in fields["Record"] and "**9**L" in fields["Record"]
    assert fields["HP"] == "**100**" and fields["Crit"] == "**5%**"
    assert fields["Next promotion"] == "Rank **B** at level **20**"
    assert "<t:1700000000:D>" in (embed.description or "")
    assert "<@42>" in (embed.description or "")
    assert emoji_count(embed) == 1
    assert total_embed_length(embed) <= 6000
    assert len(embed.fields) <= 25


def test_player_card_at_the_top_rank_says_so() -> None:
    embed = EmbedFactory.player_card("<@42>", "SS+", 60, 60, STATS, 0, 0, 1, top_rank=True)
    assert field_map(embed)["Next promotion"] == "Top rank reached."


def test_player_card_omits_the_promotion_field_when_the_rank_is_unknown() -> None:
    embed = EmbedFactory.player_card("<@42>", "ZZ", 3, 60, STATS, 0, 0, 1)
    assert "Next promotion" not in field_map(embed)


def test_player_card_escapes_markdown_in_the_rank() -> None:
    embed = EmbedFactory.player_card("<@42>", "A*B", 3, 60, STATS, 0, 0, 1)
    assert field_map(embed)["Rank"] == "**A\\*B**"


def test_player_card_thumbnail_is_optional() -> None:
    plain = EmbedFactory.player_card("<@42>", "E", 1, 60, STATS, 0, 0, 1)
    assert plain.thumbnail.url is None
    shown = EmbedFactory.player_card(
        "<@42>", "E", 1, 60, STATS, 0, 0, 1, thumbnail_url="https://img.example/a.png"
    )
    assert shown.thumbnail.url == "https://img.example/a.png"


def test_no_character_error_wording_differs_for_self_and_others() -> None:
    mine = NoCharacterError(own=True)
    theirs = NoCharacterError(own=False)
    assert isinstance(mine, YokaiError) and mine.title == "No adventurer yet"
    assert "You haven't joined" in mine.message and "/guild join" in (mine.user_hint or "")
    assert "That member" in theirs.message and "/guild join" in (theirs.user_hint or "")
