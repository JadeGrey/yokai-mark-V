"""Tests for verified online backups and the backup listing."""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

import pytest

from tests.admin_helpers import seed_database
from yokai_admin.core import audit as audit_core
from yokai_admin.core.inputs import InputRefused
from yokai_admin.errors import PanelDbError
from yokai_admin.modules.database import backups
from yokai_admin.modules.database.backups import (
    create_backup,
    delete_backup,
    find_backup,
    list_backups,
    make_backup,
)

NOW = 1_790_000_000


def _live(tmp_path: Path) -> Path:
    path = tmp_path / "yokai.db"
    seed_database(path, wallets=3, names={1: "Alex"})
    return path


def _count(path: Path, table: str) -> int:
    raw = sqlite3.connect(path)
    try:
        return int(raw.execute(f"SELECT COUNT(*) FROM {table};").fetchone()[0])
    finally:
        raw.close()


def _audit_rows(path: Path) -> list[tuple[str, str, str, str]]:
    raw = sqlite3.connect(path)
    try:
        return raw.execute(
            "SELECT module, action, target, detail_json FROM admin_audit ORDER BY id;"
        ).fetchall()
    finally:
        raw.close()


def test_a_backup_is_a_verified_copy_with_a_timestamp_name(tmp_path: Path) -> None:
    live, folder = _live(tmp_path), tmp_path / "backups"
    made = create_backup(live, folder, now=NOW)
    assert made.name.startswith("yokai-") and made.name.endswith(".db")
    assert made.path.parent == folder and not made.safety and made.size > 0
    raw = sqlite3.connect(f"{made.path.as_uri()}?mode=ro", uri=True)
    try:
        assert raw.execute("PRAGMA integrity_check;").fetchone()[0] == "ok"
        assert raw.execute("PRAGMA user_version;").fetchone()[0] == 5
        assert raw.execute("PRAGMA journal_mode;").fetchone()[0] == "delete"
    finally:
        raw.close()
    assert _count(made.path, "wallets") == 3 and _count(made.path, "user_names") == 1
    assert not list(folder.glob("*.tmp"))


def test_a_second_backup_in_the_same_second_gets_a_suffix_and_never_overwrites(
    tmp_path: Path,
) -> None:
    """Review Focus 2."""
    live, folder = _live(tmp_path), tmp_path / "backups"
    first = create_backup(live, folder, now=NOW)
    second = create_backup(live, folder, now=NOW)
    third = create_backup(live, folder, now=NOW)
    assert len({first.name, second.name, third.name}) == 3
    assert second.name.endswith("-1.db") and third.name.endswith("-2.db")
    assert first.path.exists() and second.path.exists()


def test_a_backup_is_consistent_while_the_bot_keeps_writing(tmp_path: Path) -> None:
    live, folder = _live(tmp_path), tmp_path / "backups"
    stop = threading.Event()

    def writer() -> None:
        raw = sqlite3.connect(live, timeout=10)
        i = 0
        while not stop.is_set():
            raw.execute(
                "INSERT INTO command_log (ts, user_id, command) VALUES (?, 1, 'play');", (i,)
            )
            raw.commit()
            i += 1
        raw.close()

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        time.sleep(0.2)
        made = make_backup(live, folder, now=NOW)  # the audit write would starve
    finally:
        stop.set()
        thread.join()
    raw = sqlite3.connect(made.path)
    try:
        assert raw.execute("PRAGMA integrity_check;").fetchone()[0] == "ok"
    finally:
        raw.close()
    assert 0 < _count(made.path, "command_log") <= _count(live, "command_log")


def test_a_backup_that_fails_verification_is_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    live, folder = _live(tmp_path), tmp_path / "backups"
    monkeypatch.setattr(backups, "integrity_check", lambda conn, limit=20: ["page 3 is broken"])
    with pytest.raises(PanelDbError, match="failed its integrity check"):
        create_backup(live, folder, now=NOW)
    assert list(folder.iterdir()) == []
    assert _audit_rows(live) == []


def test_backup_refuses_a_missing_old_or_unusable_setup(tmp_path: Path) -> None:
    folder = tmp_path / "backups"
    with pytest.raises(PanelDbError, match="not found"):
        create_backup(tmp_path / "absent.db", folder, now=NOW)
    old = tmp_path / "old.db"
    raw = sqlite3.connect(old)
    raw.execute("PRAGMA user_version = 1;")
    raw.execute("CREATE TABLE t (a);")
    raw.commit()
    raw.close()
    with pytest.raises(PanelDbError, match="schema"):
        create_backup(old, folder, now=NOW)
    live = _live(tmp_path)
    blocker = tmp_path / "blocked"
    blocker.write_text("a file, not a folder")
    with pytest.raises(PanelDbError, match="backup folder"):
        create_backup(live, blocker, now=NOW)


def test_creating_and_deleting_are_audited(tmp_path: Path) -> None:
    """Review Focus 7."""
    live, folder = _live(tmp_path), tmp_path / "backups"
    made = create_backup(live, folder, now=NOW)
    rows = _audit_rows(live)
    assert len(rows) == 1 and rows[0][:3] == ("database", "backup.create", made.name)
    assert f'"size": {made.size}' in rows[0][3]
    delete_backup(live, folder, made.name, now=NOW + 5)
    rows = _audit_rows(live)
    assert [r[1] for r in rows] == ["backup.create", "backup.delete"]
    assert rows[1][2] == made.name and not made.path.exists()


def test_make_backup_has_no_audit_row_and_marks_safety_copies(tmp_path: Path) -> None:
    live, folder = _live(tmp_path), tmp_path / "backups"
    safety = make_backup(live, folder, now=NOW, safety=True)
    assert safety.safety and safety.name.endswith("-pre-restore.db")
    assert _audit_rows(live) == []
    assert [b.name for b in list_backups(folder)] == [safety.name]


def test_an_audit_failure_after_the_file_exists_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    live, folder = _live(tmp_path), tmp_path / "backups"

    def boom(*args: object, **kwargs: object) -> int:
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(audit_core, "record", boom)
    with pytest.raises(PanelDbError, match="audit entry could not be saved") as caught:
        create_backup(live, folder, now=NOW)
    assert "yokai-" in str(caught.value)
    assert len(list(folder.glob("yokai-*.db"))) == 1


def test_listing_is_newest_first_and_ignores_everything_else(tmp_path: Path) -> None:
    folder = tmp_path / "backups"
    folder.mkdir()
    names = [
        "yokai-20260101-010101.db",
        "yokai-20260301-010101.db",
        "yokai-20260201-010101-pre-restore.db",
        "yokai-20260301-010101-1.db",
    ]
    for i, name in enumerate(names):
        path = folder / name
        path.write_bytes(b"x" * (i + 1))
    (folder / "notes.txt").write_text("hi")
    (folder / "yokai.db").write_bytes(b"live")
    (folder / "yokai-20260401-010101.db.tmp").write_bytes(b"half")
    (folder / "yokai-20260501-010101.db").mkdir()
    listed = list_backups(folder)
    assert [b.name for b in listed] == [
        "yokai-20260301-010101-1.db",
        "yokai-20260301-010101.db",
        "yokai-20260201-010101-pre-restore.db",
        "yokai-20260101-010101.db",
    ]
    assert [b.safety for b in listed] == [False, False, True, False]
    assert list_backups(tmp_path / "missing") == []


@pytest.mark.parametrize(
    "name",
    [
        "..\\x.db",
        "../x.db",
        "C:\\x.db",
        "/etc/passwd",
        "yokai-20200101-000000.db/../../x",
        "yokai-20200101-000000.db\x00",
        "",
        "x" * 5000,
        "notes.txt",
        "yokai.db",
        "yokai-20260501-010101.db",  # a directory with a valid name
        "yokai-20200101-000000.DB",
    ],
)
def test_names_that_are_not_in_the_listing_are_refused(tmp_path: Path, name: str) -> None:
    """Review Focus 3."""
    live, folder = _live(tmp_path), tmp_path / "backups"
    folder.mkdir()
    (folder / "notes.txt").write_text("keep me")
    (folder / "yokai-20260501-010101.db").mkdir()
    outside = tmp_path / "x.db"
    outside.write_bytes(b"outside")
    with pytest.raises(InputRefused):
        find_backup(folder, name)
    with pytest.raises(InputRefused):
        delete_backup(live, folder, name, now=NOW)
    assert (folder / "notes.txt").exists() and outside.exists() and live.exists()
    assert _audit_rows(live) == []


def test_delete_removes_exactly_that_file(tmp_path: Path) -> None:
    live, folder = _live(tmp_path), tmp_path / "backups"
    keep = create_backup(live, folder, now=NOW)
    drop = create_backup(live, folder, now=NOW + 100)
    gone = delete_backup(live, folder, drop.name, now=NOW + 200)
    assert gone.name == drop.name
    assert keep.path.exists() and not drop.path.exists()
