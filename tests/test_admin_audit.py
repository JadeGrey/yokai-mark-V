"""Tests for the Audit module."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from tests.admin_helpers import panel_client, seed_database
from yokai_admin.modules.access.service import add_admin, add_blacklist, remove_blacklist
from yokai_admin.modules.audit.service import action_label, detail_text, parse_page

AUDIT = "yokai_admin.modules.audit:MODULE"
OWNER = 111


def _history(db: Path) -> None:
    seed_database(db, names={42: "Mallory", 7: "Alex"})
    add_blacklist(db, owner_id=OWNER, raw_id="42", raw_reason="spamming the queue", now=1_000)
    add_admin(db, owner_id=OWNER, raw_id="7", raw_note="helps moderate", now=2_000)
    remove_blacklist(db, raw_id="42", now=3_000)


def test_the_log_lists_entries_newest_first_with_names_and_reasons(tmp_path: Path) -> None:
    db = tmp_path / "yokai.db"
    _history(db)
    with panel_client(tmp_path, [AUDIT], db_path=db) as (client, _):
        page = client.get("/m/audit").text
    newest = page.index("Removed from the blacklist")
    assert newest < page.index("Made an admin") < page.index("Added to the blacklist")
    assert "Mallory" in page and "Alex" in page and "spamming the queue" in page
    assert "helps moderate" in page and "previous reason: spamming the queue" in page
    assert 'aria-current="page">Audit log</a>' in page


def test_filters_narrow_the_log_and_the_form_keeps_its_values(tmp_path: Path) -> None:
    db = tmp_path / "yokai.db"
    _history(db)
    with panel_client(tmp_path, [AUDIT], db_path=db) as (client, _):
        mine = client.get("/m/audit", params={"module": "access", "target": "7"}).text
        none = client.get("/m/audit", params={"module": "nothing"}).text
    assert "Made an admin" in mine and "Added to the blacklist" not in mine
    assert 'value="7"' in mine
    assert "No matching entries." in none


def test_paging_shows_older_and_newer_links(tmp_path: Path) -> None:
    db = tmp_path / "yokai.db"
    seed_database(db)
    for i in range(1, 28):  # 27 entries: one full page of 25 plus 2
        add_admin(db, owner_id=OWNER, raw_id=str(1000 + i), raw_note="", now=i)
    with panel_client(tmp_path, [AUDIT], db_path=db) as (client, _):
        first = client.get("/m/audit").text
        second = client.get("/m/audit", params={"page": "2"}).text
    assert "page=2" in first and "Older" in first and "Newer" not in first
    assert "Newer" in second and "Older" not in second


@pytest.mark.parametrize("raw", ["", "abc", "0", "-3", "1.5", "99999999999999999999"])
def test_a_bad_page_number_falls_back_to_the_first_page(tmp_path: Path, raw: str) -> None:
    db = tmp_path / "yokai.db"
    _history(db)
    assert parse_page(raw) == 1
    with panel_client(tmp_path, [AUDIT], db_path=db) as (client, _):
        assert client.get("/m/audit", params={"page": raw}).status_code == 200


def test_hostile_text_in_an_entry_is_escaped(tmp_path: Path) -> None:
    db = tmp_path / "yokai.db"
    seed_database(db, names={42: "<img src=x onerror=alert(1)>"})
    add_blacklist(
        db, owner_id=OWNER, raw_id="42", raw_reason="<script>alert(1)</script> bad", now=1
    )
    with panel_client(tmp_path, [AUDIT], db_path=db) as (client, _):
        page = client.get("/m/audit").text
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page
    assert "<img src=x" not in page


def test_an_empty_log_and_a_missing_database_both_render(tmp_path: Path) -> None:
    db = tmp_path / "yokai.db"
    seed_database(db)
    with panel_client(tmp_path, [AUDIT], db_path=db) as (client, _):
        assert "No admin actions have been recorded yet." in client.get("/m/audit").text
    with panel_client(tmp_path, [AUDIT], db_path=tmp_path / "absent.db") as (client, _):
        page = client.get("/m/audit")
        assert page.status_code == 200 and "not found" in page.text


def test_the_audit_log_is_read_only(tmp_path: Path) -> None:
    db = tmp_path / "yokai.db"
    seed_database(db)
    with panel_client(tmp_path, [AUDIT], db_path=db) as (client, csrf):
        for method in ("post", "put", "delete"):
            response = getattr(client, method)("/m/audit", headers={"X-CSRF-Token": csrf})
            assert response.status_code == 405


def test_helpers_describe_actions_and_details_plainly() -> None:
    assert action_label("blacklist.add") == "Added to the blacklist"
    assert action_label("something.new") == "something.new"
    assert detail_text({"name": "Sami", "previous_reason": "spam"}) == "previous reason: spam"
    assert detail_text({"raw": "{oops"}) == "raw: {oops"
    assert detail_text({}) == ""


def test_the_dashboard_tile_shows_how_long_ago_the_last_action_was(tmp_path: Path) -> None:
    db = tmp_path / "yokai.db"
    seed_database(db, heartbeat_ts=int(time.time()))
    paths = ["yokai_admin.modules.dashboard:MODULE", AUDIT]
    with panel_client(tmp_path, paths, db_path=db) as (client, _):
        assert "None yet" in client.get("/m/dashboard").text
    add_admin(db, owner_id=OWNER, raw_id="7", raw_note="", now=int(time.time()) - 120)
    with panel_client(tmp_path, paths, db_path=db) as (client, _):
        page = client.get("/m/dashboard").text
    assert "Last admin action" in page and "2 minutes ago" in page
