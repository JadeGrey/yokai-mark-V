"""Tests for the People read side: the list, the search and the profile aggregates."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.admin_helpers import seed_activity, seed_database
from yokai.economy.daily import DAILY_COOLDOWN_S as BOT_DAILY_COOLDOWN_S
from yokai_admin.core.db import readonly
from yokai_admin.modules.users.service import (
    DAILY_COOLDOWN_S,
    Person,
    list_people,
    load_profile,
    reason_label,
)

NOW = 1_000_000


def _people_db(tmp_path: Path) -> Path:
    path = tmp_path / "b.db"
    seed_database(path, credits={1: 300, 2: 1}, names={1: "Alex", 3: "Riley", 5: "Mal_lory 100%"})
    seed_activity(path, user_commands=[(1, "play", NOW - 100), (3, "skip", NOW - 50)])
    return path


def _ids(rows: list[Person]) -> list[int]:
    return [row.user.id for row in rows]


def test_the_daily_cooldown_matches_the_bot() -> None:
    """The panel mirrors the bot's constant; this test catches the two drifting apart."""
    assert DAILY_COOLDOWN_S == BOT_DAILY_COOLDOWN_S


def test_the_list_covers_wallet_holders_and_named_people_most_recent_first(
    tmp_path: Path,
) -> None:
    path = _people_db(tmp_path)
    with readonly(path) as conn:
        rows, total = list_people(conn, query="", limit=25, offset=0)
    assert total == 4 and set(_ids(rows)) == {1, 2, 3, 5}
    assert _ids(rows)[:2] == [
        3,
        1,
    ]  # Riley (50 s ago), then Alex (100 s ago); the rest never ran one
    by_id = {row.user.id: row for row in rows}
    assert by_id[1].balance == 500 and by_id[3].balance is None  # Riley has a name but no wallet
    assert by_id[1].user.name == "Alex" and by_id[2].user.name is None


def test_search_matches_a_name_fragment_or_an_id_fragment(tmp_path: Path) -> None:
    path = _people_db(tmp_path)
    with readonly(path) as conn:
        assert _ids(list_people(conn, query="ale", limit=25, offset=0)[0]) == [
            1
        ]  # case-insensitive
        assert _ids(list_people(conn, query="RILEY", limit=25, offset=0)[0]) == [3]
        assert _ids(list_people(conn, query="2", limit=25, offset=0)[0]) == [2]  # an ID fragment
        assert list_people(conn, query="nobody", limit=25, offset=0) == ([], 0)


@pytest.mark.parametrize(
    ("query", "expected"),
    [("%", [5]), ("_", [5]), ("\\", []), ("100%", [5]), ("l_l", [5]), ("a_e", [])],
)
def test_search_wildcards_are_literal(tmp_path: Path, query: str, expected: list[int]) -> None:
    """Review Focus 5: '%' and '_' must not match everything. Only "Mal_lory 100%" contains
    those characters; without escaping, '%' would match all four people and 'a_e' would match
    'Alex'."""
    path = _people_db(tmp_path)
    with readonly(path) as conn:
        assert _ids(list_people(conn, query=query, limit=25, offset=0)[0]) == expected


def test_the_list_pages_with_a_total(tmp_path: Path) -> None:
    path = tmp_path / "b.db"
    seed_database(path, names={i: f"Person {i:02d}" for i in range(1, 31)})
    with readonly(path) as conn:
        first, total = list_people(conn, query="", limit=25, offset=0)
        second, _ = list_people(conn, query="", limit=25, offset=25)
    assert total == 30 and len(first) == 25 and len(second) == 5
    assert not set(_ids(first)) & set(_ids(second))


def test_an_unknown_id_has_no_profile(tmp_path: Path) -> None:
    path = _people_db(tmp_path)
    with readonly(path) as conn:
        assert load_profile(conn, 999, NOW) is None


def test_a_named_person_without_a_wallet_still_has_a_profile(tmp_path: Path) -> None:
    path = _people_db(tmp_path)
    with readonly(path) as conn:
        profile = load_profile(conn, 3, NOW)
    assert profile is not None and profile.wallet is None and profile.user.name == "Riley"
    assert profile.commands_total == 1 and profile.last_seen_ts == NOW - 50


def test_the_wallet_view_includes_streak_and_daily_readiness(tmp_path: Path) -> None:
    path = tmp_path / "b.db"
    seed_database(path, credits={1: 100, 2: 100, 3: 100})
    seed_activity(path, daily={1: (NOW - 3_600, 4), 2: (NOW - DAILY_COOLDOWN_S, 2), 3: (None, 0)})
    with readonly(path) as conn:
        cooling = load_profile(conn, 1, NOW)
        ready = load_profile(conn, 2, NOW)
        never = load_profile(conn, 3, NOW)
    assert cooling and cooling.wallet and cooling.wallet.balance == 300
    assert cooling.wallet.daily_streak == 4
    assert cooling.wallet.daily_ready_in == DAILY_COOLDOWN_S - 3_600
    assert ready and ready.wallet and ready.wallet.daily_ready_in is None  # exactly 24 h: allowed
    assert never and never.wallet and never.wallet.daily_ready_in is None


def test_transactions_are_newest_first_labelled_and_name_the_other_person(tmp_path: Path) -> None:
    path = tmp_path / "b.db"
    seed_database(path, credits={1: 300}, names={1: "Alex", 2: "Mallory"})
    seed_activity(
        path,
        transactions=[
            (1, -50, "pay_sent", 2, 450, NOW - 10),
            (1, 25, "win:slots", None, 475, NOW - 5),
        ],
    )
    with readonly(path) as conn:
        profile = load_profile(conn, 1, NOW)
    assert profile is not None
    newest = profile.transactions[0]
    assert (newest.delta, newest.label, newest.balance_after) == (25, "Win (slots)", 475)
    sent = profile.transactions[1]
    assert sent.label == "Sent to" and sent.counterparty is not None
    assert sent.counterparty.name == "Mallory"
    assert [t.reason for t in profile.transactions][-2:] == ["daily", "welcome"]


def test_only_the_newest_25_transactions_are_loaded(tmp_path: Path) -> None:
    path = tmp_path / "b.db"
    seed_database(path, credits={1: 1})
    seed_activity(path, transactions=[(1, 1, "voice", None, 202 + i, NOW + i) for i in range(40)])
    with readonly(path) as conn:
        profile = load_profile(conn, 1, NOW)
    assert profile is not None and len(profile.transactions) == 25


def test_gambling_counts_settled_bets_only_and_groups_multi_bet_games(tmp_path: Path) -> None:
    path = tmp_path / "b.db"
    seed_database(path, credits={1: 1})
    seed_activity(
        path,
        bets=[
            (1, "slots", 50, 0, "settled", None),
            (1, "slots", 50, 120, "settled", None),
            (1, "roulette", 10, 20, "settled", "g1"),
            (1, "roulette", 10, 0, "settled", "g1"),  # same group: one play
            (1, "blackjack", 100, 0, "open", None),  # unfinished: ignored
            (1, "slots", 5, 5, "refunded", None),  # refunded: ignored
        ],
    )
    with readonly(path) as conn:
        profile = load_profile(conn, 1, NOW)
    assert profile is not None
    games = {g.game: g for g in profile.games}
    assert set(games) == {"slots", "roulette"}
    slots, roulette = games["slots"], games["roulette"]
    assert (slots.plays, slots.wagered, slots.returned, slots.net) == (2, 100, 120, 20)
    assert (roulette.plays, roulette.wagered, roulette.returned) == (1, 20, 20)


def test_tracks_and_commands_are_ranked_and_capped_at_five(tmp_path: Path) -> None:
    path = tmp_path / "b.db"
    seed_database(path, credits={1: 1})
    plays = [(1, "v1", "Song One", "Artist A", NOW - i) for i in range(3)]
    plays += [(1, "v2", "Song Two", None, NOW - 10)]
    plays += [(1, f"x{i}", f"Other {i}", "Z", NOW - 20 - i) for i in range(6)]
    commands = [(1, "play", NOW)] * 4 + [(1, "skip", NOW)] * 2
    commands += [(1, f"c{i}", NOW) for i in range(6)]
    seed_activity(path, plays=plays, user_commands=commands)
    with readonly(path) as conn:
        profile = load_profile(conn, 1, NOW)
    assert profile is not None
    top = profile.tracks[0]
    assert (top.title, top.artist, top.plays) == ("Song One", "Artist A", 3)
    assert profile.tracks[1].title == "Song Two" and profile.tracks[1].artist is None
    assert len(profile.tracks) == 5
    assert (profile.commands[0].command, profile.commands[0].count) == ("play", 4)
    assert len(profile.commands) == 5 and profile.commands_total == 12


@pytest.mark.parametrize(
    ("reason", "label"),
    [
        ("daily", "Daily reward"),
        ("welcome", "Welcome bonus"),
        ("admin", "Adjusted by an admin"),
        ("pay_received", "Received from"),
        ("bet:slots", "Bet (slots)"),
        ("payout:roulette", "Payout (roulette)"),
        ("refund:blackjack", "Refund (blackjack)"),
        ("something_new", "something_new"),
    ],
)
def test_reason_labels(reason: str, label: str) -> None:
    assert reason_label(reason) == label
