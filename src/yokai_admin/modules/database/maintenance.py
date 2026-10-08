"""Maintenance that changes the database file: VACUUM and a guarded restore.

Restore is the only operation that replaces the live file, so every step before the final
`os.replace` is reversible: any refusal or failure there leaves the live database untouched.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

from yokai_admin.core import audit
from yokai_admin.core import db as panel_db
from yokai_admin.core.inputs import InputRefused
from yokai_admin.core.settings import PanelSettings
from yokai_admin.errors import PanelDbError
from yokai_admin.modules.dashboard.service import bot_status
from yokai_admin.modules.database.backups import BackupFile, find_backup, make_backup
from yokai_admin.modules.database.service import integrity_check

logger = logging.getLogger(__name__)

CONFIRM_WORD = "RESTORE"
_lock = threading.Lock()


@contextmanager
def maintenance_lock() -> Iterator[None]:
    """Only one file-level action at a time; a second request is refused, not queued."""
    if not _lock.acquire(blocking=False):
        raise PanelDbError("Another maintenance action is running. Wait for it to finish.")
    try:
        yield
    finally:
        _lock.release()


def _is_busy(exc: sqlite3.Error) -> bool:
    text = str(exc).lower()
    return "locked" in text or "busy" in text


# ---------------------------------------------------------------- VACUUM


@dataclass(frozen=True, slots=True)
class VacuumResult:
    """File sizes in bytes around a VACUUM."""

    before: int
    after: int


def _settled_size(conn: sqlite3.Connection, db_path: Path) -> int:
    """File size after folding the write-ahead log into the main file."""
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE);").fetchall()
    return db_path.stat().st_size


def vacuum(db_path: Path, *, now: Optional[int] = None) -> VacuumResult:
    """Compact the database, then record the action in the audit log."""
    stamp = int(time.time()) if now is None else now
    with maintenance_lock():
        conn = panel_db.open_readwrite(db_path, panel_db.WRITE_BUSY_TIMEOUT_S)
        try:
            panel_db.require_schema(conn)
            before = _settled_size(conn, db_path)
            conn.execute("VACUUM;")
            after = _settled_size(conn, db_path)
        except sqlite3.Error as exc:
            if _is_busy(exc):
                raise PanelDbError(
                    "The database is busy right now. Try again in a moment."
                ) from exc
            raise PanelDbError(f"The database could not be compacted: {exc}") from exc
        finally:
            conn.close()
        try:
            with panel_db.write_transaction(db_path) as txn:
                audit.record(
                    txn,
                    module="database",
                    action="vacuum",
                    target=db_path.name,
                    detail={"before": before, "after": after},
                    now=stamp,
                )
        except (PanelDbError, sqlite3.Error) as exc:
            raise PanelDbError(
                f"The database was compacted, but its audit entry could not be saved: {exc}"
            ) from exc
    return VacuumResult(before=before, after=after)


# ---------------------------------------------------------------- restore


@dataclass(frozen=True, slots=True)
class RestoreResult:
    """What a restore did. `safety` is None only when there was no live database to save."""

    restored: str
    safety: Optional[BackupFile]
    audit_saved: bool


def _live_exists(db_path: Path) -> bool:
    return db_path.exists()


def _refuse_if_bot_online(db_path: Path, now: int) -> None:
    try:
        with panel_db.readonly(db_path) as conn:
            status = bot_status(conn, now)
    except (PanelDbError, sqlite3.Error) as exc:
        raise PanelDbError(
            "The live database could not be read to confirm the bot is stopped "
            f"({exc}). If it is damaged, move it out of the way by hand and restore again."
        ) from exc
    if status.state == "online":
        raise PanelDbError(
            f"The bot looks online (last heartbeat {status.age_s} s ago). "
            "Please stop the bot first, then restore."
        )


def _check_backup(path: Path, live_version: Optional[int]) -> None:
    """The chosen file must be a healthy database the bot and panel can use."""
    conn = panel_db.open_readonly(path)
    try:
        version = panel_db.schema_version(conn)
        if version < panel_db.MIN_SCHEMA_VERSION:
            raise PanelDbError(
                f"That backup uses schema v{version}, older than the panel supports "
                f"(v{panel_db.MIN_SCHEMA_VERSION}). It was not restored."
            )
        if live_version is not None and version > live_version:
            raise PanelDbError(
                f"That backup comes from a newer schema (v{version}) than the live database "
                f"(v{live_version}); an older bot could not use it. It was not restored."
            )
        problems = integrity_check(conn)
    finally:
        conn.close()
    if problems != ["ok"]:
        raise PanelDbError(f"That backup failed its integrity check ({problems[0]}).")


def _live_version(db_path: Path) -> Optional[int]:
    if not _live_exists(db_path):
        return None
    with panel_db.readonly(db_path) as conn:
        return panel_db.schema_version(conn)


def _probe_not_in_use(db_path: Path) -> None:
    """Nobody may hold the write lock; a short BEGIN IMMEDIATE proves it."""
    conn = panel_db.open_readwrite(db_path, panel_db.WRITE_BUSY_TIMEOUT_S)
    try:
        conn.execute("BEGIN IMMEDIATE;")
        conn.execute("ROLLBACK;")
    except sqlite3.Error as exc:
        raise PanelDbError(
            "The database file is in use. Stop the bot (and anything else using it), "
            f"then try again. ({exc})"
        ) from exc
    finally:
        conn.close()


def _verify_staged(path: Path) -> None:
    conn = panel_db.open_readonly(path)
    try:
        problems = integrity_check(conn)
    finally:
        conn.close()
    if problems != ["ok"]:
        raise PanelDbError(f"The copy to restore failed its integrity check ({problems[0]}).")


def _stage(backup: Path, staging: Path) -> None:
    """Copy the backup beside the live file as a clean, verified, single-file database."""
    staging.unlink(missing_ok=True)
    source = panel_db.open_readonly(backup)
    try:
        target = sqlite3.connect(staging)
        try:
            source.backup(target)
            target.execute("PRAGMA journal_mode = DELETE;")
        finally:
            target.close()
    finally:
        source.close()
    _verify_staged(staging)


def _in_use_error(detail: str, safety: Optional[BackupFile]) -> PanelDbError:
    saved = f" A safety backup was saved as {safety.name}." if safety else ""
    return PanelDbError(
        f"The database file is in use ({detail}). Stop the bot, then try again.{saved}"
    )


def _swap(db_path: Path, staging: Path, safety: Optional[BackupFile]) -> None:
    """Fold the live WAL into the file, remove leftovers, and replace the file.

    A -wal or -shm left from the old file and applied to the restored one would corrupt it.
    """
    if _live_exists(db_path):
        try:
            conn = panel_db.open_readwrite(db_path, panel_db.WRITE_BUSY_TIMEOUT_S)
            try:
                busy = conn.execute("PRAGMA wal_checkpoint(TRUNCATE);").fetchone()[0]
            finally:
                conn.close()
        except (PanelDbError, sqlite3.Error) as exc:
            raise _in_use_error(str(exc), safety) from exc
        if busy:
            raise _in_use_error("another connection is still reading it", safety)
        for suffix in ("-wal", "-shm"):
            try:
                db_path.with_name(db_path.name + suffix).unlink(missing_ok=True)
            except OSError as exc:
                raise _in_use_error(str(exc), safety) from exc
    try:
        os.replace(staging, db_path)
    except OSError as exc:
        raise _in_use_error(str(exc), safety) from exc


def _record_restore(db_path: Path, name: str, safety: Optional[BackupFile], now: int) -> bool:
    """Audit the restore inside the restored file and clear the stale heartbeat it carried."""
    try:
        with panel_db.write_transaction(db_path) as conn:
            conn.execute("DELETE FROM bot_heartbeat;")
            audit.record(
                conn,
                module="database",
                action="restore",
                target=name,
                detail={"safety_backup": safety.name if safety else None},
                now=now,
            )
    except (PanelDbError, sqlite3.Error) as exc:
        logger.warning("The restore audit entry could not be saved: %s", exc)
        return False
    return True


def restore_backup(settings: PanelSettings, name: str, typed: str, *, now: int) -> RestoreResult:
    """Replace the live database with a listed backup, safely. See the module docstring."""
    if typed != CONFIRM_WORD:
        raise InputRefused(f"Type {CONFIRM_WORD} exactly (capital letters) to confirm.")
    db_path, folder = settings.db_path, settings.effective_backup_dir
    with maintenance_lock():
        found = find_backup(folder, name)
        had_live = _live_exists(db_path)
        if had_live:
            _refuse_if_bot_online(db_path, now)
        _check_backup(found.path, _live_version(db_path) if had_live else None)
        safety: Optional[BackupFile] = None
        if had_live:
            _probe_not_in_use(db_path)
            safety = make_backup(db_path, folder, now=now, safety=True)
        staging = db_path.with_name(db_path.name + ".restoring")
        try:
            _stage(found.path, staging)
            _swap(db_path, staging, safety)
        finally:
            staging.unlink(missing_ok=True)
        saved = _record_restore(db_path, found.name, safety, now)
    return RestoreResult(restored=found.name, safety=safety, audit_saved=saved)
