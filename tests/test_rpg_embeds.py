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


# ------------------------------------------------------------------------- board and fights

BOARD = [
    ("E", True, [("Pest Control", 1, 1, 3, 4), ("Midnight Gate", 1, 4, 12, 16)]),
    ("D", False, [("Alley Cats", 1, 6, 0, 0)]),
]


def test_guild_board_lists_open_missions_with_rewards_and_locks_the_rest() -> None:
    embed = EmbedFactory.guild_board(BOARD, "E", "Attempts: **5/8** left.", 1_900_000_000)
    fields = field_map(embed)
    assert "**Pest Control**" in fields["Rank E"] and "+16 Renown" in fields["Rank E"]
    assert "cost 1" in fields["Rank E"] and "lvl 4" in fields["Rank E"]
    assert "Rank D (locked)" in fields
    assert "+0" not in fields["Rank D (locked)"] and "Alley Cats" in fields["Rank D (locked)"]
    assert "**5/8**" in (embed.description or "")
    assert "<t:1900000000:R>" in (embed.description or "")
    assert emoji_count(embed) == 1 and total_embed_length(embed) <= 6000


def test_guild_board_escapes_mission_names_and_handles_an_empty_rank() -> None:
    embed = EmbedFactory.guild_board(
        [("E", True, [("Bad*Name", 1, 1, 1, 1)]), ("D", True, [])], "E", "x", 1
    )
    fields = field_map(embed)
    assert "Bad\\*Name" in fields["Rank E"]
    assert fields["Rank D"] == "No missions today."


PARTY = [
    ("Sami", 80, 100, [], "ready"),
    ("Friend*", 0, 120, [], "down"),
    ("Slowpoke", 50, 100, ["Rend"], "waiting"),
    ("Gone", 10, 100, [], "away"),
]
FOES = [("Gaki A", 30, 70, []), ("Gaki B", 0, 70, ["Hex"])]


def test_fight_round_shows_party_foes_and_the_last_round() -> None:
    embed = EmbedFactory.fight_round(
        "Pest Control",
        "Stage 1 of 2",
        3,
        PARTY,
        FOES,
        ["**A** strikes **B** for **5**"],
        deadline_ts=1_900_000_000,
    )
    fields = field_map(embed)
    assert "80/100" in fields["Party"] and "ready" in fields["Party"]
    assert "down" in fields["Party"] and "away" in fields["Party"] and "choosing" in fields["Party"]
    assert "Friend\\*" in fields["Party"] and "Rend" in fields["Party"]
    assert "30/70" in fields["Foes"] and "Hex" in fields["Foes"]
    assert fields["Last round"].startswith("**A** strikes")
    assert "<t:1900000000:R>" in (embed.description or "") and "Stage 1 of 2" in (
        embed.description or ""
    )
    assert "Round 3" in (embed.footer.text or "")
    assert emoji_count(embed) == 1


def test_fight_round_without_a_deadline_or_log_is_still_valid() -> None:
    embed = EmbedFactory.fight_round("M", "Stage 1 of 1", 1, PARTY[:1], FOES[:1], [])
    assert "Last round" not in field_map(embed)
    assert "resolving" in (embed.description or "")


def test_fight_round_stays_within_discord_limits_with_huge_inputs() -> None:
    players = [("N" * 80, 100, 100, ["S" * 50] * 5, "ready")] * 6
    foes = [("F" * 80, 100, 100, ["T" * 50] * 5)] * 6
    embed = EmbedFactory.fight_round("M" * 200, "Stage", 40, players, foes, ["L" * 500] * 20)
    assert total_embed_length(embed) <= 6000
    assert all(len(f.value) <= 1024 for f in embed.fields)


def test_hp_bar_is_empty_at_zero_and_never_empty_while_alive() -> None:
    from yokai.ui.embeds import _hp_bar

    assert _hp_bar(0, 100) == "─" * 10
    assert _hp_bar(1, 1000) == "━" + "─" * 9
    assert _hp_bar(100, 100) == "━" * 10
    assert _hp_bar(5, 0) == "─" * 10


def test_fight_result_win_lists_rewards_and_level_ups() -> None:
    embed = EmbedFactory.fight_result(
        True,
        "Pest Control",
        4,
        [("<@1>", 12, 40, 0), ("<@2>", 5, 10, 1), ("<@3>", 5, 10, 3)],
        ["The party stands victorious"],
    )
    fields = field_map(embed)
    assert "Mission complete" in (embed.title or "")
    assert "<@1>" in fields["Rewards"] and "+12 XP" in fields["Rewards"]
    assert "level up" in fields["Rewards"] and "level up x3" in fields["Rewards"]
    assert "cleared in 4 round(s)" in (embed.description or "")
    assert emoji_count(embed) == 1


def test_fight_result_loss_has_no_rewards_field() -> None:
    embed = EmbedFactory.fight_result(False, "Pest Control", 7, [("<@1>", 0, 0, 0)], [])
    assert "Mission failed" in (embed.title or "")
    assert "Rewards" not in field_map(embed)
    assert "No rewards" in (embed.description or "")


def test_fight_quips_exist() -> None:
    assert len(QUIP_VARIANTS["fight_win"]) >= 3 and len(QUIP_VARIANTS["fight_loss"]) >= 3


def card_with(xp: tuple[int, int] | None, level: int = 14) -> discord.Embed:
    return EmbedFactory.player_card(
        "<@42>", "C", level, 60, STATS, 1, 0, 1_700_000_000, next_rank=("B", 20), xp=xp
    )


def test_player_card_shows_xp_progress_right_after_the_promotion_field() -> None:
    embed = card_with((412, 1000))
    names = [field.name for field in embed.fields]
    assert names.index("Experience") == names.index("Next promotion") + 1
    text = field_map(embed)["Experience"]
    assert "**412** / 1,000 XP" in text and "41% to level 15" in text
    assert "━" in text  # a bar (filled and empty segments)
    assert emoji_count(embed) == 1 and total_embed_length(embed) <= 6000


def test_xp_bar_edges_empty_just_started_and_almost_there() -> None:
    assert "0% to level 15" in field_map(card_with((0, 500)))["Experience"]
    assert "99% to level 15" in field_map(card_with((499, 500)))["Experience"]
    assert "100% to level 15" in field_map(card_with((500, 500)))["Experience"]


def test_player_card_at_the_level_cap_says_max_level() -> None:
    embed = card_with(None, level=60)
    assert field_map(embed)["Experience"] == "Max level reached."
    assert "Experience" not in field_map(card_with(None, level=14))
