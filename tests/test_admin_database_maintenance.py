"""Tests for VACUUM and the guarded restore."""

from __future__ import annotations

import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Optional

import pytest

from tests.admin_helpers import seed_database
from yokai_admin.core import audit as audit_core
from yokai_admin.core import db as panel_db
from yokai_admin.core.inputs import InputRefused
from yokai_admin.core.settings import PanelSettings
from yokai_admin.errors import PanelDbError
from yokai_admin.modules.database import maintenance
from yokai_admin.modules.database.backups import BackupFile, list_backups, make_backup
from yokai_admin.modules.database.maintenance import (
    maintenance_lock,
    restore_backup,
    vacuum,
)

NOW = 1_790_000_000
BASE_ROWS = 20
EXTRA_ROWS = 10


def _exec(path: Path, *statements: str) -> None:
    raw = sqlite3.connect(path)
    try:
        for sql in statements:
            raw.execute(sql)
        raw.commit()
    finally:
        raw.close()


def _count(path: Path, table: str = "command_log") -> int:
    raw = sqlite3.connect(path)
    try:
        return int(raw.execute(f"SELECT COUNT(*) FROM {table};").fetchone()[0])
    finally:
        raw.close()


def _audit(path: Path) -> list[tuple[str, str, str, str]]:
    raw = sqlite3.connect(path)
    try:
        return raw.execute(
            "SELECT module, action, target, detail_json FROM admin_audit ORDER BY id;"
        ).fetchall()
    finally:
        raw.close()


class World:
    """A live database, a backup of its earlier state, and settings pointing at both."""

    def __init__(self, tmp_path: Path, heartbeat_ts: Optional[int] = None) -> None:
        self.live = tmp_path / "data" / "yokai.db"
        self.live.parent.mkdir()
        seed_database(
            self.live, wallets=2, commands=list(range(BASE_ROWS)), heartbeat_ts=heartbeat_ts
        )
        self.folder = tmp_path / "backups"
        self.settings = PanelSettings(
            db_path=self.live, log_dir=tmp_path / "logs", backup_dir=self.folder
        )
        self.backup: BackupFile = make_backup(self.live, self.folder, now=NOW - 1000)
        _exec(
            self.live,
            *[
                f"INSERT INTO command_log (ts, user_id, command) VALUES ({i}, 1, 'later');"
                for i in range(EXTRA_ROWS)
            ],
        )

    def restore(self, typed: str = "RESTORE", name: Optional[str] = None) -> object:
        return restore_backup(
            self.settings, self.backup.name if name is None else name, typed, now=NOW
        )

    def untouched(self) -> bool:
        return _count(self.live) == BASE_ROWS + EXTRA_ROWS and not list(
            self.live.parent.glob("*.restoring")
        )


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


# ---------------------------------------------------------------- VACUUM


def test_vacuum_reports_sizes_and_is_audited(tmp_path: Path) -> None:
    live = tmp_path / "yokai.db"
    seed_database(live, wallets=1)
    _exec(live, "CREATE TABLE junk (v TEXT);")
    raw = sqlite3.connect(live)
    raw.executemany("INSERT INTO junk VALUES (?);", [("z" * 2000,) for _ in range(500)])
    raw.commit()
    raw.close()
    _exec(live, "DELETE FROM junk;")
    result = vacuum(live, now=NOW)
    assert result.after < result.before
    rows = _audit(live)
    assert len(rows) == 1 and rows[0][:2] == ("database", "vacuum")
    assert f'"before": {result.before}' in rows[0][3] and f'"after": {result.after}' in rows[0][3]
    assert _count(live, "wallets") == 1


def test_vacuum_refuses_a_missing_or_old_database(tmp_path: Path) -> None:
    with pytest.raises(PanelDbError, match="not found"):
        vacuum(tmp_path / "absent.db", now=NOW)
    old = tmp_path / "old.db"
    _exec(old, "PRAGMA user_version = 1;", "CREATE TABLE t (a);")
    with pytest.raises(PanelDbError, match="schema"):
        vacuum(old, now=NOW)


def test_vacuum_on_a_busy_database_says_so_and_audits_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review Focus 5."""
    live = tmp_path / "yokai.db"
    seed_database(live, wallets=1)
    monkeypatch.setattr(panel_db, "WRITE_BUSY_TIMEOUT_S", 0.2)
    holder = sqlite3.connect(live, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE;")
    try:
        with pytest.raises(PanelDbError, match="busy"):
            vacuum(live, now=NOW)
    finally:
        holder.execute("ROLLBACK;")
        holder.close()
    assert _audit(live) == []


def test_only_one_maintenance_action_runs_at_a_time() -> None:
    with maintenance_lock():
        with pytest.raises(PanelDbError, match="Another maintenance action"):
            with maintenance_lock():
                pass
    with maintenance_lock():
        pass  # released again


# ---------------------------------------------------------------- restore: refusals


@pytest.mark.parametrize("typed", ["restore", "Restore", "RESTORE ", " RESTORE", "", "YES"])
def test_the_typed_word_must_be_exactly_restore(world: World, typed: str) -> None:
    before = world.live.read_bytes()
    with pytest.raises(InputRefused, match="RESTORE"):
        world.restore(typed)
    assert world.live.read_bytes() == before and world.untouched()
    assert len(list_backups(world.folder)) == 1  # no safety backup either


@pytest.mark.parametrize(
    "name", ["nope.db", "..\\x.db", "../yokai.db", "C:\\x.db", "", "yokai.db", "x" * 5000]
)
def test_unknown_or_path_like_backup_names_are_refused(world: World, name: str) -> None:
    """Review Focus 1."""
    before = world.live.read_bytes()
    with pytest.raises(InputRefused):
        world.restore(name=name)
    assert world.live.read_bytes() == before and world.untouched()


def test_a_fresh_heartbeat_refuses_the_restore(tmp_path: Path) -> None:
    w = World(tmp_path, heartbeat_ts=NOW - 10)
    before = w.live.read_bytes()
    with pytest.raises(PanelDbError, match="stop the bot"):
        w.restore()
    assert w.live.read_bytes() == before and w.untouched()
    assert len(list_backups(w.folder)) == 1


def test_a_stale_heartbeat_does_not_block_the_restore(tmp_path: Path) -> None:
    w = World(tmp_path, heartbeat_ts=NOW - 10_000)
    w.restore()
    assert _count(w.live) == BASE_ROWS


def test_a_held_write_lock_refuses_the_restore(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(panel_db, "WRITE_BUSY_TIMEOUT_S", 0.2)
    before = world.live.read_bytes()
    holder = sqlite3.connect(world.live, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE;")
    try:
        with pytest.raises(PanelDbError, match="in use"):
            world.restore()
    finally:
        holder.execute("ROLLBACK;")
        holder.close()
    assert world.live.read_bytes() == before and world.untouched()
    assert len(list_backups(world.folder)) == 1


@pytest.mark.skipif(sys.platform != "win32", reason="Windows refuses to replace an open file")
def test_a_file_held_open_by_another_process_is_not_replaced(world: World) -> None:
    """The running-but-disconnected bot has no fresh heartbeat; the OS is the last guard."""
    holder = sqlite3.connect(world.live)
    holder.execute("SELECT COUNT(*) FROM wallets;").fetchone()
    try:
        with pytest.raises(PanelDbError, match="in use"):
            world.restore()
    finally:
        holder.close()
    assert world.untouched()
    assert not list(world.live.parent.glob("*.restoring"))


def _damage(path: Path) -> None:
    data = bytearray(path.read_bytes())
    data[4096:] = b"\xff" * (len(data) - 4096)
    path.write_bytes(bytes(data))


def test_a_corrupt_backup_is_refused(tmp_path: Path) -> None:
    w = World(tmp_path)
    _exec(w.backup.path, "CREATE TABLE pad (v TEXT);")
    raw = sqlite3.connect(w.backup.path)
    raw.executemany("INSERT INTO pad VALUES (?);", [("p" * 500,) for _ in range(200)])
    raw.commit()
    raw.close()
    _damage(w.backup.path)
    before = w.live.read_bytes()
    with pytest.raises(PanelDbError, match="integrity"):
        w.restore()
    assert w.live.read_bytes() == before and w.untouched()
    assert len(list_backups(w.folder)) == 1  # refused before any safety backup was made


def test_a_file_that_is_not_a_database_is_refused(world: World) -> None:
    world.backup.path.write_text("this is not a database at all, just words")
    before = world.live.read_bytes()
    with pytest.raises(PanelDbError, match="not a SQLite|integrity"):
        world.restore()
    assert world.live.read_bytes() == before and world.untouched()


def test_a_backup_from_a_newer_schema_is_refused(world: World) -> None:
    _exec(world.backup.path, "PRAGMA user_version = 99;")
    before = world.live.read_bytes()
    with pytest.raises(PanelDbError, match="newer"):
        world.restore()
    assert world.live.read_bytes() == before and world.untouched()


def test_a_backup_older_than_the_panel_supports_is_refused(world: World) -> None:
    _exec(world.backup.path, "PRAGMA user_version = 1;")
    with pytest.raises(PanelDbError, match="schema"):
        world.restore()
    assert world.untouched()


def test_a_failed_safety_backup_stops_before_anything_is_replaced(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: object, **kwargs: object) -> BackupFile:
        raise PanelDbError("disk full")

    monkeypatch.setattr(maintenance, "make_backup", boom)
    before = world.live.read_bytes()
    with pytest.raises(PanelDbError, match="disk full"):
        world.restore()
    assert world.live.read_bytes() == before and world.untouched()


def test_an_interrupted_copy_leaves_the_live_file_alone(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(path: Path) -> None:
        raise PanelDbError("copy verification failed")

    monkeypatch.setattr(maintenance, "_verify_staged", boom)
    with pytest.raises(PanelDbError, match="copy verification failed"):
        world.restore()
    assert world.untouched()
    assert len(list_backups(world.folder)) == 2  # the original and the safety copy


# ---------------------------------------------------------------- restore: success


def test_a_good_restore_replaces_the_data_and_keeps_a_safety_copy(world: World) -> None:
    """Review Focus 1."""
    result = world.restore()
    assert result.restored == world.backup.name and result.audit_saved
    assert _count(world.live) == BASE_ROWS and _count(world.live, "wallets") == 2
    raw = sqlite3.connect(world.live)
    try:
        assert raw.execute("PRAGMA integrity_check;").fetchone()[0] == "ok"
    finally:
        raw.close()
    assert not list(world.live.parent.glob("*.restoring"))
    assert not (world.live.parent / "yokai.db-wal").exists()
    assert not (world.live.parent / "yokai.db-shm").exists()
    safety = result.safety
    assert safety.safety and safety.path.exists() and safety.name.endswith("-pre-restore.db")
    assert _count(safety.path) == BASE_ROWS + EXTRA_ROWS
    rows = _audit(world.live)
    assert len(rows) == 1 and rows[0][:3] == ("database", "restore", world.backup.name)
    assert safety.name in rows[0][3]


def test_a_stale_wal_from_a_crash_is_not_replayed_onto_the_restored_file(
    tmp_path: Path,
) -> None:
    """A leftover -wal applied to a different database would corrupt it."""
    w = World(tmp_path)
    # Simulate a crash: committed rows that only exist in the -wal file.
    holder = sqlite3.connect(w.live, isolation_level=None)
    holder.execute("PRAGMA journal_mode = WAL;")
    holder.execute("PRAGMA wal_autocheckpoint = 0;")
    holder.execute("INSERT INTO command_log (ts, user_id, command) VALUES (1, 1, 'walrow');")
    crashed = tmp_path / "crashed"
    crashed.mkdir()
    for suffix in ("", "-wal", "-shm"):
        source = Path(str(w.live) + suffix)
        if source.exists():
            shutil.copy(source, crashed / (w.live.name + suffix))
    holder.close()
    live = crashed / "yokai.db"
    assert (crashed / "yokai.db-wal").exists()
    settings = PanelSettings(db_path=live, log_dir=tmp_path / "logs", backup_dir=w.folder)
    result = restore_backup(settings, w.backup.name, "RESTORE", now=NOW)
    assert _count(live) == BASE_ROWS  # the wal row did not come back
    assert _count(result.safety.path) == BASE_ROWS + EXTRA_ROWS + 1  # but it was saved first
    assert not (crashed / "yokai.db-wal").exists()


def test_the_restored_file_does_not_claim_the_bot_is_online(tmp_path: Path) -> None:
    w = World(tmp_path)
    _exec(w.backup.path, f"INSERT OR REPLACE INTO bot_heartbeat VALUES (1, {NOW - 5}, 1, 'x');")
    w.restore()
    assert _count(w.live, "bot_heartbeat") == 0


def test_an_audit_failure_after_the_swap_is_reported_not_raised(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: object, **kwargs: object) -> int:
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(audit_core, "record", boom)
    result = world.restore()
    assert result.audit_saved is False
    assert _count(world.live) == BASE_ROWS


def test_a_safety_backup_can_itself_be_restored(world: World) -> None:
    first = world.restore()
    assert _count(world.live) == BASE_ROWS
    again = restore_backup(world.settings, first.safety.name, "RESTORE", now=NOW + 60)
    assert _count(world.live) == BASE_ROWS + EXTRA_ROWS
    assert again.restored == first.safety.name


def test_restoring_when_the_live_file_is_missing_recreates_it(tmp_path: Path) -> None:
    w = World(tmp_path)
    w.live.unlink()
    result = w.restore()
    assert _count(w.live) == BASE_ROWS and result.audit_saved


def test_a_leftover_wal_after_the_checkpoint_is_removed_before_the_swap(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A -wal that survives next to the restored file would be replayed onto it."""
    real_open = panel_db.open_readwrite
    stale = world.live.parent / "yokai.db-wal"

    class Spy:
        def __init__(self, conn: sqlite3.Connection) -> None:
            self._conn = conn
            self._checkpointed = False

        def execute(self, sql: str, *args: object) -> sqlite3.Cursor:
            self._checkpointed = self._checkpointed or "wal_checkpoint" in sql
            return self._conn.execute(sql, *args)

        def close(self) -> None:
            self._conn.close()
            if self._checkpointed:
                stale.write_bytes(b"stale wal from the old file")

    monkeypatch.setattr(
        maintenance.panel_db, "open_readwrite", lambda *a, **k: Spy(real_open(*a, **k))
    )
    seen: list[bool] = []
    os_replace = maintenance.os.replace

    def spy_replace(src: object, dst: object) -> None:
        if Path(str(dst)) == world.live:
            seen.append(stale.exists())
        os_replace(src, dst)

    monkeypatch.setattr(maintenance.os, "replace", spy_replace)
    world.restore()
    assert seen == [False] and not stale.exists()
    assert _count(world.live) == BASE_ROWS


def test_sidecar_files_left_beside_a_missing_live_file_are_removed(tmp_path: Path) -> None:
    """Review: a hand-moved damaged database leaves its -wal/-shm behind, and SQLite replays
    them onto the restored file (the restore then reports success over the wrong data)."""
    w = World(tmp_path)
    holder = sqlite3.connect(w.live, isolation_level=None)
    holder.execute("PRAGMA journal_mode = WAL;")
    holder.execute("PRAGMA wal_autocheckpoint = 0;")
    holder.execute("INSERT INTO command_log (ts, user_id, command) VALUES (1, 1, 'walrow');")
    moved = tmp_path / "moved"
    moved.mkdir()
    for suffix in ("-wal", "-shm"):
        shutil.copy(Path(str(w.live) + suffix), moved / ("yokai.db" + suffix))
    holder.close()
    live = moved / "yokai.db"  # the damaged file was moved away; its sidecars were not
    settings = PanelSettings(db_path=live, log_dir=tmp_path / "logs", backup_dir=w.folder)
    restore_backup(settings, w.backup.name, "RESTORE", now=NOW)
    assert _count(live) == BASE_ROWS  # the stale wal row did not come back
    assert _count(live, "wallets") == 2
    raw = sqlite3.connect(live)
    try:
        assert raw.execute("PRAGMA integrity_check;").fetchone()[0] == "ok"
    finally:
        raw.close()


def test_a_failure_while_copying_names_the_safety_backup(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(maintenance, "_stage", boom)
    with pytest.raises(PanelDbError, match="Nothing was replaced") as caught:
        world.restore()
    assert "-pre-restore.db" in str(caught.value)
    assert world.untouched()
