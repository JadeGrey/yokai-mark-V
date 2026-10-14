"""The pool of challenges. Edit here to add or retune challenges; rewards are base values."""

from __future__ import annotations

from yokai.challenges.models import Template
from yokai.challenges.periods import DAILY, WEEKLY

POOL: tuple[Template, ...] = (
    # Daily: music
    Template("d_music_3", DAILY, "music", "tracks", 3, 30, "Listen to 3 tracks in a call"),
    Template("d_music_5", DAILY, "music", "tracks", 5, 40, "Listen to 5 tracks in a call"),
    Template("d_music_8", DAILY, "music", "tracks", 8, 55, "Listen to 8 tracks in a call"),
    # Daily: games
    Template("d_games_3", DAILY, "games", "rounds_played", 3, 30, "Play 3 gambling rounds"),
    Template("d_games_5", DAILY, "games", "rounds_played", 5, 40, "Play 5 gambling rounds"),
    Template(
        "d_win_slots",
        DAILY,
        "games",
        "rounds_won",
        1,
        35,
        "Win a round of slots",
        game=frozenset({"slots"}),
    ),
    Template(
        "d_win_blackjack",
        DAILY,
        "games",
        "rounds_won",
        1,
        35,
        "Win a hand of blackjack",
        game=frozenset({"blackjack", "bjtable"}),
    ),
    # Daily: voice
    Template(
        "d_voice_20",
        DAILY,
        "voice",
        "voice_minutes",
        20,
        30,
        "Spend 20 minutes in voice with company",
    ),
    Template(
        "d_voice_30",
        DAILY,
        "voice",
        "voice_minutes",
        30,
        40,
        "Spend 30 minutes in voice with company",
    ),
    # Weekly: music
    Template("w_music_25", WEEKLY, "music", "tracks", 25, 120, "Listen to 25 tracks in a call"),
    Template(
        "w_music_artists_10", WEEKLY, "music", "artists", 10, 120, "Listen to 10 different artists"
    ),
    # Weekly: games
    Template("w_games_20", WEEKLY, "games", "rounds_played", 20, 110, "Play 20 gambling rounds"),
    Template("w_games_win_5", WEEKLY, "games", "rounds_won", 5, 130, "Win 5 gambling rounds"),
    Template("w_games_kinds_3", WEEKLY, "games", "games", 3, 110, "Play 3 different games"),
    # Weekly: voice
    Template(
        "w_voice_180",
        WEEKLY,
        "voice",
        "voice_minutes",
        180,
        120,
        "Spend 3 hours in voice with company",
    ),
)

TEMPLATES_BY_ID: dict[str, Template] = {t.id: t for t in POOL}
