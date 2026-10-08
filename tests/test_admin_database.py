"""Tests for the Database module through the real app."""

from __future__ import annotations

import os
import sqlite3
import sys
import time
from pathlib import Path

import pytest

from tests.admin_helpers import panel_client, post, seed_database
from yokai_admin.core import db as panel_db
from yokai_admin.modules.database.backups import list_backups, make_backup

DATABASE = "yokai_admin.modules.database:MODULE"
HX = {"HX-Request": "true"}
NOW = int(time.time())


class Setup:
    def __init__(self, tmp_path: Path, heartbeat_ts: int | None = None) -> None:
        self.tmp = tmp_path
        self.live = tmp_path / "yokai.db"
        self.folder = tmp_path / "backups"
        seed_database(self.live, wallets=3, commands=list(range(12)), heartbeat_ts=heartbeat_ts)

    def client(self, **kwargs: object):  # type: ignore[no-untyped-def]
        return panel_client(
            self.tmp, [DATABASE], db_path=self.live, backup_dir=self.folder, **kwargs
        )

    def rows(self, table: str = "command_log") -> int:
        raw = sqlite3.connect(self.live)
        try:
            return int(raw.execute(f"SELECT COUNT(*) FROM {table};").fetchone()[0])
        finally:
            raw.close()

    def audit(self) -> list[tuple[str, str]]:
        raw = sqlite3.connect(self.live)
        try:
            return raw.execute("SELECT action, target FROM admin_audit ORDER BY id;").fetchall()
        finally:
            raw.close()

    def backup(self, now: int = NOW - 500) -> str:
        made = make_backup(self.live, self.folder, now=now)
        os.utime(made.path, (now, now))  # the listing and tile read the file's own age
        return made.name


@pytest.fixture
def s(tmp_path: Path) -> Setup:
    return Setup(tmp_path)


def test_the_overview_lists_tables_size_schema_and_the_bot_state(s: Setup) -> None:
    with s.client() as (client, _):
        page = client.get("/m/database").text
    assert 'aria-current="page">Database</a>' in page
    assert "wallets" in page and "command_log" in page and "Schema" in page
    assert ">12<" in page  # command_log row count
    assert "KB" in page and "offline" in page.lower()
    assert "No backups yet" in page
    assert 'href="/m/database/table?name=wallets"' in page


def test_the_table_browser_shows_columns_rows_and_pages(s: Setup) -> None:
    raw = sqlite3.connect(s.live)
    raw.execute("CREATE TABLE big (id INTEGER PRIMARY KEY, label TEXT);")
    raw.executemany("INSERT INTO big (label) VALUES (?);", [(f"row {i}",) for i in range(120)])
    raw.commit()
    raw.close()
    with s.client() as (client, _):
        first = client.get("/m/database/table", params={"name": "big"}).text
        third = client.get("/m/database/table", params={"name": "big", "page": "3"}).text
        junk = client.get("/m/database/table", params={"name": "big", "page": "9" * 5000})
    assert "<th" in first and "label" in first and "row 0" in first and "row 50" not in first
    assert "Older" in first and "Newer" not in first
    assert "row 100" in third and "Older" not in third and "Newer" in third
    assert junk.status_code == 200


@pytest.mark.parametrize(
    "name", ["wallets; DROP TABLE wallets", "sqlite_master", "WALLETS", "", "x" * 5000, "nope"]
)
def test_unknown_table_names_answer_with_a_message(s: Setup, name: str) -> None:
    with s.client() as (client, _):
        response = client.get("/m/database/table", params={"name": name})
    assert response.status_code == 200 and "no table with that name" in response.text.lower()
    assert s.rows("wallets") == 3


def test_hostile_table_and_cell_text_is_escaped(s: Setup) -> None:
    evil = "<img src=x onerror=alert(1)>"
    raw = sqlite3.connect(s.live)
    raw.execute('CREATE TABLE "<b>t</b>" (c TEXT);')
    raw.execute('INSERT INTO "<b>t</b>" VALUES (?);', (evil,))
    raw.commit()
    raw.close()
    with s.client() as (client, _):
        overview = client.get("/m/database").text
        browsed = client.get("/m/database/table", params={"name": "<b>t</b>"}).text
    for page in (overview, browsed):
        assert "<img src=x" not in page and "<b>t</b>" not in page
    assert "&lt;img src=x" in browsed


def test_integrity_check_reports_ok_and_problems(s: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    with s.client() as (client, csrf):
        ok = post(client, csrf, "/m/database/integrity")
        database_router = sys.modules["yokai_admin.modules.database.router"]
        monkeypatch.setattr(
            database_router, "integrity_check", lambda conn, limit=20: ["page 4 is broken <b>"]
        )
        bad = post(client, csrf, "/m/database/integrity")
    assert "Integrity check passed" in ok.text and 'id="db-body"' in ok.text
    assert "page 4 is broken &lt;b&gt;" in bad.text and "problem" in bad.text.lower()
    assert s.audit() == []  # a read-only check is not audited


def test_creating_a_backup_lists_it_and_audits_it(s: Setup) -> None:
    with s.client() as (client, csrf):
        done = post(client, csrf, "/m/database/backup")
    names = [b.name for b in list_backups(s.folder)]
    assert len(names) == 1 and names[0] in done.text and "Backup saved" in done.text
    assert s.audit() == [("backup.create", names[0])]


def test_deleting_a_backup_asks_first_and_removes_only_that_file(s: Setup) -> None:
    keep, drop = s.backup(NOW - 900), s.backup(NOW - 100)
    (s.folder / "notes.txt").write_text("not a backup")
    with s.client() as (client, csrf):
        asked = client.get(f"/m/database/backups/{drop}/delete/confirm", headers=HX)
        assert drop in asked.text and "Delete" in asked.text and "<html" not in asked.text
        assert (s.folder / drop).exists()
        done = post(client, csrf, f"/m/database/backups/{drop}/delete")
        missing = post(client, csrf, f"/m/database/backups/{drop}/delete")
    assert not (s.folder / drop).exists() and (s.folder / keep).exists()
    assert (s.folder / "notes.txt").exists()
    assert "Deleted" in done.text and "no backup with that name" in missing.text.lower()
    assert s.audit() == [("backup.delete", drop)]


@pytest.mark.parametrize(
    "name", ["..%5Cyokai.db", "..%2Fyokai.db", "C%3A%5Cx.db", "yokai.db", "notes.txt", "nope"]
)
def test_path_like_backup_names_never_touch_other_files(s: Setup, name: str) -> None:
    """Review Focus 3."""
    s.backup()
    (s.folder / "notes.txt").write_text("keep")
    with s.client() as (client, csrf):
        asked = client.get(f"/m/database/backups/{name}/delete/confirm", headers=HX)
        deleted = post(client, csrf, f"/m/database/backups/{name}/delete")
        restored = post(client, csrf, f"/m/database/backups/{name}/restore", confirm="RESTORE")
    for response in (asked, deleted, restored):
        assert response.status_code in (200, 404)
    assert s.live.exists() and (s.folder / "notes.txt").exists()
    assert len(list_backups(s.folder)) == 1 and s.audit() == []


def test_vacuum_asks_first_then_reports_sizes(s: Setup) -> None:
    with s.client() as (client, csrf):
        asked = client.get("/m/database/vacuum/confirm", headers=HX)
        assert "Compact" in asked.text and "<html" not in asked.text
        done = post(client, csrf, "/m/database/vacuum")
    assert "Compacted" in done.text and "to" in done.text
    assert [a for a, _ in s.audit()] == ["vacuum"]


def test_restore_needs_the_typed_word_and_applies_the_backup(s: Setup) -> None:
    name = s.backup()
    raw = sqlite3.connect(s.live)
    raw.execute("INSERT INTO command_log (ts, user_id, command) VALUES (1, 1, 'later');")
    raw.commit()
    raw.close()
    with s.client() as (client, csrf):
        form = client.get(f"/m/database/backups/{name}/restore/confirm", headers=HX)
        assert (
            'name="confirm"' in form.text
            and "RESTORE" in form.text
            and "stop the bot" in form.text.lower()
        )
        wrong = post(client, csrf, f"/m/database/backups/{name}/restore", confirm="restore")
        assert "Type RESTORE exactly" in wrong.text and s.rows() == 13
        done = post(client, csrf, f"/m/database/backups/{name}/restore", confirm="RESTORE")
    assert "Restored" in done.text and s.rows() == 12
    assert any(b.safety for b in list_backups(s.folder))
    assert ("restore", name) in s.audit()


def test_restore_is_refused_while_the_bot_looks_online(tmp_path: Path) -> None:
    online = Setup(tmp_path, heartbeat_ts=NOW - 5)
    name = online.backup()
    raw = sqlite3.connect(online.live)
    raw.execute("INSERT INTO command_log (ts, user_id, command) VALUES (1, 1, 'later');")
    raw.commit()
    raw.close()
    with online.client() as (client, csrf):
        page = client.get("/m/database").text
        refused = post(client, csrf, f"/m/database/backups/{name}/restore", confirm="RESTORE")
    assert "online" in page.lower()
    assert refused.status_code == 200 and "stop the bot" in refused.text.lower()
    assert online.rows() == 13 and len(list_backups(online.folder)) == 1


def test_writes_need_a_session_and_csrf_and_gets_change_nothing(s: Setup) -> None:
    """Review Focus 6."""
    name = s.backup()
    posts = [
        "/m/database/integrity",
        "/m/database/vacuum",
        "/m/database/backup",
        f"/m/database/backups/{name}/delete",
        f"/m/database/backups/{name}/restore",
    ]
    with s.client(login=False) as (client, _):
        for path in posts:
            assert client.post(path, data={"confirm": "RESTORE"}).status_code == 401
    with s.client() as (client, csrf):
        for path in posts:
            assert client.post(path, data={"confirm": "RESTORE"}).status_code == 403
        for path in (
            "/m/database",
            "/m/database/body",
            "/m/database/table?name=wallets",
            "/m/database/vacuum/confirm",
            f"/m/database/backups/{name}/delete/confirm",
            f"/m/database/backups/{name}/restore/confirm",
        ):
            assert client.get(path, headers=HX).status_code == 200
        for path in posts:
            assert client.get(path).status_code == 405
    assert [b.name for b in list_backups(s.folder)] == [name]
    assert s.audit() == [] and s.rows() == 12


def test_a_busy_or_missing_database_answers_with_a_message(
    s: Setup, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review Focus 6."""
    monkeypatch.setattr(panel_db, "WRITE_BUSY_TIMEOUT_S", 0.2)
    holder = sqlite3.connect(s.live, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE;")
    try:
        with s.client() as (client, csrf):
            busy = post(client, csrf, "/m/database/vacuum")
            busy_backup = post(client, csrf, "/m/database/backup")
    finally:
        holder.execute("ROLLBACK;")
        holder.close()
    assert busy.status_code == 200 and "busy" in busy.text
    assert busy_backup.status_code == 200 and "audit entry" in busy_backup.text
    gone = Setup.__new__(Setup)
    gone.tmp, gone.live, gone.folder = tmp_path, tmp_path / "absent.db", tmp_path / "bk"
    with gone.client() as (client, csrf):
        assert "not found" in client.get("/m/database").text
        assert "not found" in client.get("/m/database/table?name=wallets").text
        assert "not found" in post(client, csrf, "/m/database/backup").text


def test_only_backup_shaped_files_are_listed(s: Setup) -> None:
    s.folder.mkdir()
    (s.folder / "notes.txt").write_text("hi")
    (s.folder / "yokai.db").write_bytes(b"x")
    (s.folder / "yokai-a&b.db").write_bytes(b"x")
    real = s.backup()
    with s.client() as (client, _):
        page = client.get("/m/database").text
    assert (
        real in page
        and "notes.txt" not in page
        and "yokai-a&amp;b" not in page
        and "yokai-a&b" not in page
    )


def test_the_audit_log_labels_database_actions(s: Setup) -> None:
    with s.client() as (client, csrf):
        post(client, csrf, "/m/database/backup")
        post(client, csrf, "/m/database/vacuum")
    with panel_client(
        s.tmp, ["yokai_admin.modules.audit:MODULE"], db_path=s.live, backup_dir=s.folder
    ) as (client, _):
        page = client.get("/m/audit").text
    assert "Created a backup" in page and "Compacted the database" in page


def test_the_dashboard_tile_shows_size_and_the_last_backup(s: Setup) -> None:
    paths = ["yokai_admin.modules.dashboard:MODULE", DATABASE]
    with panel_client(s.tmp, paths, db_path=s.live, backup_dir=s.folder) as (client, _):
        before = client.get("/m/dashboard").text
        s.backup(NOW - 7200)
        after = client.get("/m/dashboard").text
    assert "Database" in before and "no backups yet" in before
    assert "last backup 2 hours ago" in after
