"""Tests for the Dashboard module through the real app."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from tests.admin_helpers import panel_client, seed_database

DASH = "yokai_admin.modules.dashboard:MODULE"
H = "tests.admin_helpers"


def test_an_online_bot_shows_the_status_vitals_and_a_ping(tmp_path: Path) -> None:
    db = tmp_path / "yokai.db"
    seed_database(db, heartbeat_ts=int(time.time()) - 5, wallets=2)
    with panel_client(tmp_path, [DASH], db_path=db) as (client, _):
        page = client.get("/m/dashboard").text
    assert ">Online<" in page and 'data-state="online"' in page
    assert "Last heartbeat" in page and "4242" in page and "0.1.0" in page
    assert "Schema version" in page and "Wallets" in page
    assert "Commands in the last 24 hours" in page
    assert '<a href="/m/dashboard" aria-current="page">Dashboard</a>' in page


def test_a_stale_heartbeat_reads_offline(tmp_path: Path) -> None:
    db = tmp_path / "yokai.db"
    seed_database(db, heartbeat_ts=int(time.time()) - 300)
    with panel_client(tmp_path, [DASH], db_path=db) as (client, _):
        page = client.get("/m/dashboard").text
    assert ">Offline<" in page and 'data-state="offline"' in page and "5 minutes ago" in page


def test_no_heartbeat_row_says_so(tmp_path: Path) -> None:
    db = tmp_path / "yokai.db"
    seed_database(db)
    with panel_client(tmp_path, [DASH], db_path=db) as (client, _):
        assert "No heartbeat on record" in client.get("/m/dashboard").text


def test_a_missing_database_still_renders_with_the_reason(tmp_path: Path) -> None:
    """Review Focus 4: no 500, a clear message."""
    with panel_client(tmp_path, [DASH], db_path=tmp_path / "absent.db") as (client, _):
        response = client.get("/m/dashboard")
    assert response.status_code == 200
    assert ">No data<" in response.text and "not found" in response.text


def test_an_old_schema_renders_with_the_upgrade_hint(tmp_path: Path) -> None:
    old = tmp_path / "old.db"
    raw = sqlite3.connect(old)
    raw.execute("PRAGMA user_version = 3;")
    raw.execute("CREATE TABLE t (x INTEGER);")
    raw.commit()
    raw.close()
    with panel_client(tmp_path, [DASH], db_path=old) as (client, _):
        response = client.get("/m/dashboard")
    assert response.status_code == 200 and "needs v4" in response.text


def test_a_database_that_is_not_sqlite_renders_without_a_server_error(tmp_path: Path) -> None:
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"not a database" * 100)
    with panel_client(tmp_path, [DASH], db_path=junk) as (client, _):
        response = client.get("/m/dashboard")
    assert response.status_code == 200 and "not a SQLite database" in response.text


def test_the_status_fragment_has_no_shell_and_keeps_polling(tmp_path: Path) -> None:
    db = tmp_path / "yokai.db"
    seed_database(db, heartbeat_ts=int(time.time()))
    with panel_client(tmp_path, [DASH], db_path=db) as (client, csrf):
        fragment = client.get("/m/dashboard/status", headers={"HX-Request": "true"}).text
        full = client.get("/m/dashboard").text
    assert "<html" not in fragment and "<aside" not in fragment
    assert 'hx-get="/m/dashboard/status"' in fragment and 'hx-trigger="every 5s"' in fragment
    assert 'hx-swap="outerHTML"' in fragment
    assert 'class="stale-note"' in fragment  # shown by panel.js when polling fails
    assert 'id="status"' in full and csrf in full


def test_the_fragment_is_locked_without_a_session(tmp_path: Path) -> None:
    with panel_client(tmp_path, [DASH], login=False) as (client, _):
        assert client.get("/m/dashboard/status").status_code == 401


def test_other_modules_tiles_appear_and_a_failing_tile_is_isolated(tmp_path: Path) -> None:
    db = tmp_path / "yokai.db"
    seed_database(db, heartbeat_ts=int(time.time()))
    paths = [DASH, f"{H}:ALPHA", f"{H}:BROKEN_TILE"]
    with panel_client(tmp_path, paths, db_path=db) as (client, _):
        response = client.get("/m/dashboard")
    assert response.status_code == 200
    assert "Queue" in response.text and "3 tracks" in response.text
    assert "Could not read this tile." in response.text


def test_log_lines_are_escaped_and_an_empty_log_says_so(tmp_path: Path) -> None:
    """Review Focus 3: a hostile log line is text, never markup."""
    db = tmp_path / "yokai.db"
    seed_database(db)
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "yokai.log").write_text(
        "2026-10-05 10:00:00 [ERROR] yokai: <script>alert(1)</script>\n", encoding="utf-8"
    )
    with panel_client(tmp_path, [DASH], db_path=db) as (client, _):
        page = client.get("/m/dashboard").text
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page

    (logs / "yokai.log").write_text(
        "2026-10-05 10:00:00 [INFO] yokai: all quiet\n", encoding="utf-8"
    )
    with panel_client(tmp_path, [DASH], db_path=db) as (client, _):
        assert "No errors in the current log." in client.get("/m/dashboard").text


def test_modules_that_failed_to_load_are_listed_on_the_dashboard(tmp_path: Path) -> None:
    db = tmp_path / "yokai.db"
    seed_database(db)
    with panel_client(tmp_path, [DASH, f"{H}:MISSING"], db_path=db) as (client, _):
        page = client.get("/m/dashboard").text
    assert "1 module did not load" in page and f"{H}:MISSING" in page
