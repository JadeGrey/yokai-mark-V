"""Tests for the People module through the real app."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from tests.admin_helpers import panel_client, post, seed_activity, seed_database
from yokai_admin.core import db as panel_db

USERS = "yokai_admin.modules.users:MODULE"
NOW = int(time.time())


def _scalar(path: Path, sql: str, *params: object) -> object:
    raw = sqlite3.connect(path)
    try:
        return raw.execute(sql, params).fetchone()[0]
    finally:
        raw.close()


def _seeded(tmp_path: Path) -> Path:
    db = tmp_path / "yokai.db"
    seed_database(db, credits={1: 300, 2: 50}, names={1: "Alex", 2: "Mallory", 3: "Riley"})
    seed_activity(
        db,
        plays=[(1, "v1", "Song One", "Artist A", NOW - 5)] * 2,
        bets=[(1, "slots", 50, 120, "settled", None)],
        user_commands=[(1, "play", NOW - 60), (3, "skip", NOW - 30)],
        transactions=[(1, -50, "pay_sent", 2, 450, NOW - 20)],
        daily={1: (NOW - 3_600, 4)},
    )
    return db


def test_the_list_shows_people_with_balances_and_links(tmp_path: Path) -> None:
    db = _seeded(tmp_path)
    with panel_client(tmp_path, [USERS], db_path=db) as (client, _):
        page = client.get("/m/users").text
    assert "<strong>Alex</strong>" in page and "<strong>Riley</strong>" in page
    assert 'href="/m/users/1"' in page and "500" in page
    assert 'aria-current="page">People</a>' in page
    assert 'name="q"' in page


def test_search_filters_and_a_miss_says_so(tmp_path: Path) -> None:
    db = _seeded(tmp_path)
    with panel_client(tmp_path, [USERS], db_path=db) as (client, _):
        hit = client.get("/m/users", params={"q": "mall"}).text
        miss = client.get("/m/users", params={"q": "zzz"}).text
        wildcard = client.get("/m/users", params={"q": "%"}).text
    assert "<strong>Mallory</strong>" in hit and "<strong>Alex</strong>" not in hit
    assert "No one matches" in miss
    assert "<strong>Alex</strong>" not in wildcard  # '%' is literal


def test_the_list_pages(tmp_path: Path) -> None:
    db = tmp_path / "yokai.db"
    seed_database(db, names={i: f"Person {i:02d}" for i in range(1, 31)})
    with panel_client(tmp_path, [USERS], db_path=db) as (client, _):
        first = client.get("/m/users").text
        second = client.get("/m/users", params={"page": "2"}).text
        junk = client.get("/m/users", params={"page": "9" * 5000})
    assert "Older" in first and "Newer" not in first and "Newer" in second
    assert junk.status_code == 200


def test_a_profile_shows_wallet_ledger_games_tracks_and_commands(tmp_path: Path) -> None:
    db = _seeded(tmp_path)
    with panel_client(tmp_path, [USERS], db_path=db) as (client, _):
        page = client.get("/m/users/1").text
    assert "Alex" in page and "500" in page  # balance
    assert "Daily streak" in page and ">4<" in page
    assert "Sent to" in page and "Mallory" in page  # ledger row with the other person
    assert "Slots" in page and "+70" in page  # settled bet: wagered 50, returned 120
    assert "Song One" in page and "Artist A" in page
    assert "play" in page
    assert 'id="person"' in page


def test_a_person_with_no_wallet_gets_a_profile_without_money_controls(tmp_path: Path) -> None:
    db = _seeded(tmp_path)
    with panel_client(tmp_path, [USERS], db_path=db) as (client, _):
        page = client.get("/m/users/3").text
    assert "Riley" in page and "No wallet yet" in page
    assert "/adjust/preview" not in page and "/daily/" not in page


@pytest.mark.parametrize("raw", ["999", "abc", "١٢", "9" * 5000, "0", "-5"])
def test_an_unknown_or_malformed_id_answers_with_a_message_not_an_error(
    tmp_path: Path, raw: str
) -> None:
    """Review Focus 6."""
    db = _seeded(tmp_path)
    with panel_client(tmp_path, [USERS], db_path=db) as (client, _):
        response = client.get(f"/m/users/{raw}")
    assert response.status_code == 200 and "No one" in response.text


def test_the_adjust_flow_previews_then_confirms_then_applies(tmp_path: Path) -> None:
    db = _seeded(tmp_path)
    with panel_client(tmp_path, [USERS], db_path=db) as (client, csrf):
        preview = post(
            client, csrf, "/m/users/1/adjust/preview", amount="+250", reason="event prize"
        )
        assert "500" in preview.text and "750" in preview.text and "+250" in preview.text
        assert 'name="expected" value="500"' in preview.text and "Confirm" in preview.text
        assert _scalar(db, "SELECT balance FROM wallets WHERE user_id = 1") == 500  # nothing yet
        done = post(
            client, csrf, "/m/users/1/adjust", amount="+250", reason="event prize", expected="500"
        )
    assert done.status_code == 200 and 'id="person"' in done.text and "750" in done.text
    assert _scalar(db, "SELECT balance FROM wallets WHERE user_id = 1") == 750
    assert _scalar(db, "SELECT COUNT(*) FROM admin_audit WHERE module = 'users'") == 1


def test_a_clamped_deduction_is_explained_in_the_preview(tmp_path: Path) -> None:
    db = _seeded(tmp_path)
    with panel_client(tmp_path, [USERS], db_path=db) as (client, csrf):
        preview = post(
            client, csrf, "/m/users/1/adjust/preview", amount="-900", reason="reset exploit"
        )
    assert "below 0" in preview.text and "-500" in preview.text


@pytest.mark.parametrize(
    ("form", "message"),
    [
        ({"amount": "abc", "reason": "gift"}, "whole number"),
        ({"amount": "0", "reason": "gift"}, "cannot be zero"),
        ({"amount": "100001", "reason": "gift"}, "100,000"),
        ({"amount": "50", "reason": "x"}, "reason"),
        ({"amount": "50"}, "reason"),
        ({}, "whole number"),
    ],
)
def test_a_refused_preview_keeps_the_typed_values_and_changes_nothing(
    tmp_path: Path, form: dict[str, str], message: str
) -> None:
    db = _seeded(tmp_path)
    with panel_client(tmp_path, [USERS], db_path=db) as (client, csrf):
        response = post(client, csrf, "/m/users/1/adjust/preview", **form)
    assert response.status_code == 200 and message in response.text
    assert 'data-kind="error"' in response.text
    if form.get("reason"):
        assert f'value="{form["reason"]}"' in response.text
    assert _scalar(db, "SELECT balance FROM wallets WHERE user_id = 1") == 500


def test_a_stale_confirm_explains_and_refreshes(tmp_path: Path) -> None:
    db = _seeded(tmp_path)
    with panel_client(tmp_path, [USERS], db_path=db) as (client, csrf):
        response = post(
            client, csrf, "/m/users/1/adjust", amount="+10", reason="gift", expected="400"
        )
    assert "changed from 400 to 500" in response.text and 'id="person"' in response.text
    assert _scalar(db, "SELECT balance FROM wallets WHERE user_id = 1") == 500


def test_the_daily_reset_flow_asks_first(tmp_path: Path) -> None:
    db = _seeded(tmp_path)
    with panel_client(tmp_path, [USERS], db_path=db) as (client, csrf):
        asked = client.get("/m/users/1/daily/confirm", headers={"HX-Request": "true"})
        assert "Reset" in asked.text and "<html" not in asked.text
        before = _scalar(db, "SELECT last_daily_ts FROM wallets WHERE user_id = 1")
        done = post(client, csrf, "/m/users/1/daily/reset")
    assert "can claim" in done.text
    after = _scalar(db, "SELECT last_daily_ts FROM wallets WHERE user_id = 1")
    assert before == NOW - 3_600 and after is not None and after <= int(time.time()) - 86_399
    assert _scalar(db, "SELECT daily_streak FROM wallets WHERE user_id = 1") == 4
    assert _scalar(db, "SELECT COUNT(*) FROM admin_audit WHERE action = 'daily.reset'") == 1


def test_write_endpoints_need_a_session_and_csrf_and_gets_change_nothing(tmp_path: Path) -> None:
    """Review Focus 6."""
    db = _seeded(tmp_path)
    form = {"amount": "+10", "reason": "gift", "expected": "500"}
    with panel_client(tmp_path, [USERS], db_path=db, login=False) as (client, _):
        assert client.post("/m/users/1/adjust", data=form).status_code == 401
    with panel_client(tmp_path, [USERS], db_path=db) as (client, csrf):
        assert client.post("/m/users/1/adjust", data=form).status_code == 403
        assert client.post("/m/users/1/daily/reset").status_code == 403
        for path in (
            "/m/users/1",
            "/m/users/1/adjust/form",
            "/m/users/1/daily/button",
            "/m/users/1/daily/confirm",
        ):
            assert client.get(path, headers={"HX-Request": "true"}).status_code == 200
        assert client.get("/m/users/1/adjust").status_code == 405
        assert client.get("/m/users/1/daily/reset").status_code == 405
    assert _scalar(db, "SELECT balance FROM wallets WHERE user_id = 1") == 500
    assert _scalar(db, "SELECT COUNT(*) FROM admin_audit") == 0


def test_hostile_names_titles_commands_and_reasons_are_escaped(tmp_path: Path) -> None:
    """Review Focus 5."""
    db = tmp_path / "yokai.db"
    evil = "<img src=x onerror=alert(1)>"
    seed_database(db, credits={1: 300}, names={1: evil})
    seed_activity(
        db,
        plays=[(1, "v1", "<script>alert(1)</script>", evil, NOW)],
        user_commands=[(1, "<b>play</b>", NOW)],
        transactions=[(1, 5, "<i>odd</i>", None, 505, NOW)],
    )
    with panel_client(tmp_path, [USERS], db_path=db) as (client, csrf):
        profile = client.get("/m/users/1").text
        listing = client.get("/m/users", params={"q": "img"}).text
        refused = post(client, csrf, "/m/users/1/adjust/preview", amount="5", reason=f'"{evil}')
    for page in (profile, listing, refused.text):
        assert "<script>alert(1)</script>" not in page and "<img src=x" not in page
        assert "<b>play</b>" not in page and "<i>odd</i>" not in page


def test_a_busy_or_missing_database_answers_with_a_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review Focus 6."""
    db = _seeded(tmp_path)
    monkeypatch.setattr(panel_db, "WRITE_BUSY_TIMEOUT_S", 0.2)
    holder = sqlite3.connect(db, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE;")
    try:
        with panel_client(tmp_path, [USERS], db_path=db) as (client, csrf):
            busy = post(
                client, csrf, "/m/users/1/adjust", amount="+10", reason="gift", expected="500"
            )
    finally:
        holder.execute("ROLLBACK;")
        holder.close()
    assert busy.status_code == 200 and "busy right now" in busy.text
    with panel_client(tmp_path, [USERS], db_path=tmp_path / "absent.db") as (client, csrf):
        assert "not found" in client.get("/m/users").text
        assert "not found" in client.get("/m/users/1").text
        attempt = post(client, csrf, "/m/users/1/adjust", amount="5", reason="gift", expected="1")
        assert "not found" in attempt.text


def test_the_dashboard_tile_counts_people(tmp_path: Path) -> None:
    db = _seeded(tmp_path)
    paths = ["yokai_admin.modules.dashboard:MODULE", USERS]
    with panel_client(tmp_path, paths, db_path=db) as (client, _):
        page = client.get("/m/dashboard").text
    assert "People" in page and "3 known, 2 with wallets" in page


def test_the_daily_reset_does_not_overpromise_the_streak(tmp_path: Path) -> None:
    """Review: the reset gives 24 hours to claim, which can be less than the streak had left."""
    db = _seeded(tmp_path)
    with panel_client(tmp_path, [USERS], db_path=db) as (client, csrf):
        asked = client.get("/m/users/1/daily/confirm", headers={"HX-Request": "true"})
        done = post(client, csrf, "/m/users/1/daily/reset")
    for text in (asked.text, done.text):
        assert "is kept." not in text and "24 hours" in text


def test_a_hostile_id_in_the_path_never_reaches_a_url(tmp_path: Path) -> None:
    """Review: the raw path text was echoed into hx-post and hx-get URLs."""
    db = _seeded(tmp_path)
    with panel_client(tmp_path, [USERS], db_path=db) as (client, csrf):
        form = client.get("/m/users/%2E%2E/adjust/form", headers={"HX-Request": "true"})
        button = client.get("/m/users/a%3Fb/daily/button", headers={"HX-Request": "true"})
        refused = post(client, csrf, "/m/users/%2E%2E/adjust/preview", amount="x", reason="gift")
    for response in (form, button, refused):
        assert "/m/users/.." not in response.text and "a?b" not in response.text
        assert "hx-post" not in response.text and "hx-get" not in response.text


def test_a_failed_read_during_a_preview_does_not_claim_a_failed_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    users_router = sys.modules["yokai_admin.modules.users.router"]

    def broken(*args: object, **kwargs: object) -> None:
        raise sqlite3.OperationalError("disk I/O error")

    db = _seeded(tmp_path)
    monkeypatch.setattr(users_router, "preview_adjustment", broken)
    with panel_client(tmp_path, [USERS], db_path=db) as (client, csrf):
        response = post(client, csrf, "/m/users/1/adjust/preview", amount="5", reason="gift")
    assert "could not be read" in response.text and "Nothing was saved" not in response.text
