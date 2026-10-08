"""Tests for the Access module through the real app."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from tests.admin_helpers import panel_client, post, seed_database
from yokai_admin.core import db as panel_db

ACCESS = "yokai_admin.modules.access:MODULE"


def _count(path: Path, table: str) -> int:
    raw = sqlite3.connect(path)
    try:
        return int(raw.execute(f"SELECT COUNT(*) FROM {table};").fetchone()[0])  # fixed names
    finally:
        raw.close()


def test_the_page_lists_both_lists_with_names_and_ids(tmp_path: Path) -> None:
    db = tmp_path / "yokai.db"
    seed_database(
        db, blacklist=[(5, "spamming the queue")], admins=[7], names={5: "Mallory", 7: "Alex"}
    )
    with panel_client(tmp_path, [ACCESS], db_path=db) as (client, _):
        page = client.get("/m/access").text
    assert "<strong>Mallory</strong>" in page and '<span class="id">5</span>' in page
    assert "spamming the queue" in page and "<strong>Alex</strong>" in page
    assert 'aria-current="page">Access</a>' in page
    assert 'hx-post="/m/access/blacklist"' in page and 'hx-post="/m/access/admins"' in page


def test_empty_lists_say_so(tmp_path: Path) -> None:
    db = tmp_path / "yokai.db"
    seed_database(db)
    with panel_client(tmp_path, [ACCESS], db_path=db) as (client, _):
        page = client.get("/m/access").text
    assert "Nobody is blacklisted." in page and "No admins yet." in page


def test_adding_to_the_blacklist_returns_the_refreshed_section_with_a_confirmation(
    tmp_path: Path,
) -> None:
    db = tmp_path / "yokai.db"
    seed_database(db, names={42: "Mallory"})
    with panel_client(tmp_path, [ACCESS], db_path=db) as (client, csrf):
        response = post(
            client, csrf, "/m/access/blacklist", user_id="42", reason="spamming the queue"
        )
    assert response.status_code == 200 and "<html" not in response.text
    assert 'id="access"' in response.text and "Mallory (42) is blacklisted" in response.text
    assert "within a few seconds" in response.text
    assert _count(db, "blacklist") == 1 and _count(db, "admin_audit") == 1


@pytest.mark.parametrize(
    ("form", "message"),
    [
        ({"user_id": "abc", "reason": "spamming"}, "digits only"),
        ({"user_id": "42", "reason": "x"}, "reason"),
        ({"user_id": "111", "reason": "spamming"}, "bot owner"),
        ({"user_id": "١٢٣", "reason": "spamming"}, "digits only"),
        ({}, "digits only"),
    ],
)
def test_refusals_are_shown_as_a_normal_response_and_change_nothing(
    tmp_path: Path, form: dict[str, str], message: str
) -> None:
    db = tmp_path / "yokai.db"
    seed_database(db)
    with panel_client(tmp_path, [ACCESS], db_path=db) as (client, csrf):
        response = post(client, csrf, "/m/access/blacklist", **form)
    assert response.status_code == 200 and message in response.text  # HTMX swaps 200s only
    assert 'data-kind="error"' in response.text
    assert _count(db, "blacklist") == 0 and _count(db, "admin_audit") == 0


def test_with_no_owner_id_the_server_refuses_edits_and_the_page_hides_the_forms(
    tmp_path: Path,
) -> None:
    """Review Focus 4: hiding the forms is not enough; the endpoints refuse too."""
    db = tmp_path / "yokai.db"
    seed_database(db, blacklist=[(5, "spam")])
    with panel_client(tmp_path, [ACCESS], db_path=db, owner_id=None) as (client, csrf):
        page = client.get("/m/access").text
        response = post(client, csrf, "/m/access/blacklist", user_id="42", reason="spamming")
        removal = post(client, csrf, "/m/access/blacklist/5/remove")
    assert "Set OWNER_ID" in page and "<form" not in page and "hx-post" not in page
    assert "Set OWNER_ID" in response.text and "Set OWNER_ID" in removal.text
    assert _count(db, "blacklist") == 1 and _count(db, "admin_audit") == 0


def test_removal_asks_first_then_removes_and_audits(tmp_path: Path) -> None:
    db = tmp_path / "yokai.db"
    seed_database(db, blacklist=[(5, "spam")], names={5: "Mallory"})
    with panel_client(tmp_path, [ACCESS], db_path=db) as (client, csrf):
        confirm = client.get("/m/access/blacklist/5/confirm", headers={"HX-Request": "true"})
        assert "Remove" in confirm.text and "Mallory" in confirm.text
        assert "<html" not in confirm.text
        assert _count(db, "blacklist") == 1  # asking changes nothing
        done = post(client, csrf, "/m/access/blacklist/5/remove")
    assert "Mallory (5) was removed from the blacklist" in done.text
    assert _count(db, "blacklist") == 0 and _count(db, "admin_audit") == 1


def test_confirming_a_person_who_is_not_listed_explains_instead_of_failing(
    tmp_path: Path,
) -> None:
    db = tmp_path / "yokai.db"
    seed_database(db)
    with panel_client(tmp_path, [ACCESS], db_path=db) as (client, csrf):
        confirm = client.get("/m/access/blacklist/99/confirm", headers={"HX-Request": "true"})
        gone = post(client, csrf, "/m/access/admins/99/remove")
    assert confirm.status_code == 200 and "not on the blacklist" in confirm.text
    assert gone.status_code == 200 and "not an admin" in gone.text


def test_admins_can_be_added_with_a_note_and_removed(tmp_path: Path) -> None:
    db = tmp_path / "yokai.db"
    seed_database(db, names={7: "Alex"})
    with panel_client(tmp_path, [ACCESS], db_path=db) as (client, csrf):
        added = post(client, csrf, "/m/access/admins", user_id="7", note="helps moderate")
        assert "Alex (7) is now an admin" in added.text and _count(db, "admins") == 1
        removed = post(client, csrf, "/m/access/admins/7/remove")
    assert "Alex (7) was removed from the admin list" in removed.text
    assert _count(db, "admins") == 0


def test_hostile_names_and_reasons_are_escaped(tmp_path: Path) -> None:
    """Review Focus 3."""
    db = tmp_path / "yokai.db"
    seed_database(
        db, blacklist=[(5, "<script>alert(1)</script>")], names={5: "<img src=x onerror=alert(1)>"}
    )
    with panel_client(tmp_path, [ACCESS], db_path=db) as (client, _):
        page = client.get("/m/access").text
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page
    assert "<img src=x" not in page and "&lt;img src=x" in page


def test_write_endpoints_need_a_session_and_the_csrf_token(tmp_path: Path) -> None:
    """Review Focus 6."""
    db = tmp_path / "yokai.db"
    seed_database(db)
    form = {"user_id": "42", "reason": "spamming"}
    with panel_client(tmp_path, [ACCESS], db_path=db, login=False) as (client, _):
        assert client.post("/m/access/blacklist", data=form).status_code == 401
    with panel_client(tmp_path, [ACCESS], db_path=db) as (client, csrf):
        assert client.post("/m/access/blacklist", data=form).status_code == 403
        assert client.post("/m/access/admins/7/remove").status_code == 403
        assert client.get("/m/access/blacklist/5/remove").status_code == 405  # POST-only
    assert _count(db, "blacklist") == 0


def test_a_busy_database_answers_with_a_friendly_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review Focus 4."""
    db = tmp_path / "yokai.db"
    seed_database(db)
    monkeypatch.setattr(panel_db, "WRITE_BUSY_TIMEOUT_S", 0.2)
    holder = sqlite3.connect(db, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE;")
    try:
        with panel_client(tmp_path, [ACCESS], db_path=db) as (client, csrf):
            response = post(client, csrf, "/m/access/blacklist", user_id="42", reason="spamming")
    finally:
        holder.execute("ROLLBACK;")
        holder.close()
    assert response.status_code == 200 and "busy right now" in response.text


def test_a_missing_database_renders_the_page_with_a_reason(tmp_path: Path) -> None:
    with panel_client(tmp_path, [ACCESS], db_path=tmp_path / "absent.db") as (client, csrf):
        page = client.get("/m/access")
        attempt = post(client, csrf, "/m/access/blacklist", user_id="42", reason="spamming")
    assert page.status_code == 200 and "not found" in page.text
    assert attempt.status_code == 200 and "not found" in attempt.text


def test_the_dashboard_tile_counts_both_lists(tmp_path: Path) -> None:
    db = tmp_path / "yokai.db"
    seed_database(db, blacklist=[(5, "aaa"), (6, "bbb")], admins=[7], heartbeat_ts=int(time.time()))
    paths = ["yokai_admin.modules.dashboard:MODULE", ACCESS]
    with panel_client(tmp_path, paths, db_path=db) as (client, _):
        page = client.get("/m/dashboard").text
    assert "Access lists" in page and "2 blacklisted, 1 admin" in page


def test_absurdly_long_digit_strings_are_refused_not_a_server_error(tmp_path: Path) -> None:
    """Review finding: int() raises past 4300 digits, which used to escape as an unhandled 500
    that htmx silently ignores."""
    db = tmp_path / "yokai.db"
    seed_database(db, blacklist=[(5, "spam")])
    huge = "9" * 5000
    with panel_client(tmp_path, [ACCESS], db_path=db) as (client, csrf):
        add = post(client, csrf, "/m/access/blacklist", user_id=huge, reason="spamming")
        remove = post(client, csrf, f"/m/access/blacklist/{huge}/remove")
        confirm = client.get(f"/m/access/blacklist/{huge}/confirm", headers={"HX-Request": "true"})
    for response in (add, remove, confirm):
        assert response.status_code == 200 and 'data-kind="error"' in response.text
    assert _count(db, "blacklist") == 1 and _count(db, "admin_audit") == 0


def test_confirming_a_person_who_is_gone_retargets_the_whole_section(tmp_path: Path) -> None:
    """Review finding: the confirm fragment replaces one table row, so the 'not listed' answer,
    which is a whole section, must be redirected to the section or htmx nests it inside <tbody>."""
    db = tmp_path / "yokai.db"
    seed_database(db)
    with panel_client(tmp_path, [ACCESS], db_path=db) as (client, _):
        gone = client.get("/m/access/blacklist/99/confirm", headers={"HX-Request": "true"})
    assert gone.headers["hx-retarget"] == "#access" and gone.headers["hx-reswap"] == "outerHTML"


def test_the_confirm_route_understands_the_same_ids_as_the_remove_route(tmp_path: Path) -> None:
    """Review finding: '0005' was removable but reported as 'not on the blacklist' by confirm."""
    db = tmp_path / "yokai.db"
    seed_database(db, blacklist=[(5, "spam")], names={5: "Mallory"})
    with panel_client(tmp_path, [ACCESS], db_path=db) as (client, _):
        confirm = client.get("/m/access/blacklist/0005/confirm", headers={"HX-Request": "true"})
    assert '<tr class="confirm">' in confirm.text and "Mallory" in confirm.text
    assert "hx-retarget" not in confirm.headers  # a real confirm row swaps in place


def test_the_confirm_row_takes_keyboard_focus_and_fits_the_table(tmp_path: Path) -> None:
    """Review finding: after pressing Remove, focus fell to <body>; the row also used one column
    count for both tables."""
    db = tmp_path / "yokai.db"
    seed_database(db, blacklist=[(5, "spam")], admins=[7])
    with panel_client(tmp_path, [ACCESS], db_path=db) as (client, _):
        bl = client.get("/m/access/blacklist/5/confirm", headers={"HX-Request": "true"}).text
        ad = client.get("/m/access/admins/7/confirm", headers={"HX-Request": "true"}).text
    assert "autofocus" in bl and 'colspan="4"' in bl  # blacklist table has four columns
    assert "autofocus" in ad and 'colspan="3"' in ad  # admin table has three


def test_remove_buttons_say_who_they_remove_even_for_unnamed_people(tmp_path: Path) -> None:
    """Review finding: every unnamed row read 'Remove Unknown user ...', indistinguishable."""
    db = tmp_path / "yokai.db"
    seed_database(db, blacklist=[(5, "spam"), (6, "spam")])
    with panel_client(tmp_path, [ACCESS], db_path=db) as (client, _):
        page = client.get("/m/access").text
    assert 'aria-label="Remove Unknown user (5) from the blacklist"' in page
    assert 'aria-label="Remove Unknown user (6) from the blacklist"' in page


def test_a_refused_form_keeps_what_was_typed_and_only_in_that_form(tmp_path: Path) -> None:
    """P-A3 deferred minor: a too-short reason used to wipe the whole form."""
    db = tmp_path / "yokai.db"
    seed_database(db)
    with panel_client(tmp_path, [ACCESS], db_path=db) as (client, csrf):
        refused = post(client, csrf, "/m/access/blacklist", user_id="42", reason="x")
        ok = post(client, csrf, "/m/access/blacklist", user_id="42", reason="spamming the queue")
    assert 'autocomplete="off" value="42"' in refused.text
    assert 'value="x"' in refused.text
    admin_form = refused.text.split('hx-post="/m/access/admins"')[1]
    assert 'value="42"' not in admin_form  # the other form is untouched
    assert 'value="42"' not in ok.text and "is blacklisted" in ok.text  # cleared after success


def test_a_kept_value_is_escaped(tmp_path: Path) -> None:
    db = tmp_path / "yokai.db"
    seed_database(db)
    with panel_client(tmp_path, [ACCESS], db_path=db) as (client, csrf):
        refused = post(
            client, csrf, "/m/access/blacklist", user_id='"><script>x</script>', reason="y"
        )
    assert "<script>x</script>" not in refused.text and "&lt;script&gt;" in refused.text
