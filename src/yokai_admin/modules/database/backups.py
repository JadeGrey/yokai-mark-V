"""Backups: consistent online copies of the bot's database, and a listing that is the only way
a request can name a backup file."""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from yokai_admin.core import audit
from yokai_admin.core.db import open_readonly, require_schema, write_transaction
from yokai_admin.core.inputs import InputRefused
from yokai_admin.errors import PanelDbError
from yokai_admin.modules.database.service import integrity_check

logger = logging.getLogger(__name__)

NAME_RE = re.compile(r"^yokai-\d{8}-\d{6}(?:-pre-restore)?(?:-\d+)?\.db$")
_MAX_SUFFIX = 1000


@dataclass(frozen=True, slots=True)
class BackupFile:
    """One backup file found in the backup folder."""

    name: str
    path: Path
    size: int
    modified_ts: int
    safety: bool


def _describe(path: Path) -> Optional[BackupFile]:
    """A BackupFile for a regular file with a backup-shaped name, else None."""
    if not NAME_RE.fullmatch(path.name) or path.is_symlink() or not path.is_file():
        return None
    try:
        info = path.stat()
    except OSError:
        return None
    return BackupFile(
        name=path.name,
        path=path,
        size=info.st_size,
        modified_ts=int(info.st_mtime),
        safety="-pre-restore" in path.name,
    )


def _newest_key(item: BackupFile) -> tuple[str, int]:
    """Sort key: the timestamp in the name, then the same-second suffix."""
    match = re.fullmatch(r"yokai-(\d{8}-\d{6})(?:-pre-restore)?(?:-(\d+))?\.db", item.name)
    assert match is not None
    return match.group(1), int(match.group(2) or 0)


def list_backups(backup_dir: Path) -> list[BackupFile]:
    """Backup files, newest first. A missing folder is an empty list; other files are ignored."""
    try:
        entries = list(backup_dir.iterdir())
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise PanelDbError(f"The backup folder could not be read: {exc}") from exc
    found = [item for item in (_describe(path) for path in entries) if item is not None]
    return sorted(found, key=_newest_key, reverse=True)


def find_backup(backup_dir: Path, name: str) -> BackupFile:
    """The listed backup with exactly this name. Request text never becomes a path directly."""
    for item in list_backups(backup_dir):
        if item.name == name:
            return item
    raise InputRefused("There is no backup with that name.")


def _free_name(backup_dir: Path, now: int, safety: bool) -> str:
    stamp = datetime.fromtimestamp(now).strftime("%Y%m%d-%H%M%S")
    base = f"yokai-{stamp}" + ("-pre-restore" if safety else "")
    for i in range(_MAX_SUFFIX):
        name = f"{base}.db" if i == 0 else f"{base}-{i}.db"
        if not (backup_dir / name).exists():
            return name
    raise PanelDbError("Too many backups were made in the same second. Try again.")


def make_backup(db_path: Path, backup_dir: Path, *, now: int, safety: bool = False) -> BackupFile:
    """Copy the live database with SQLite's online backup API, verify it, and keep it.

    No audit row: restore uses this for its safety copy, and the live file is about to be replaced.
    """
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise PanelDbError(f"The backup folder could not be used: {exc}") from exc
    name = _free_name(backup_dir, now, safety)
    final, temp = backup_dir / name, backup_dir / f"{name}.tmp"
    try:
        source = open_readonly(db_path)
        try:
            require_schema(source)
            target = sqlite3.connect(temp)
            try:
                source.backup(target)
                # A plain single file: no -wal/-shm beside it, easy to open and to restore.
                target.execute("PRAGMA journal_mode = DELETE;")
            finally:
                target.close()
        finally:
            source.close()
        check = open_readonly(temp)
        try:
            problems = integrity_check(check)
        finally:
            check.close()
        if problems != ["ok"]:
            raise PanelDbError(
                f"The backup failed its integrity check and was removed ({problems[0]})."
            )
        os.replace(temp, final)
    except sqlite3.Error as exc:
        raise PanelDbError(f"The backup could not be made: {exc}") from exc
    except OSError as exc:
        raise PanelDbError(f"The backup could not be saved: {exc}") from exc
    finally:
        temp.unlink(missing_ok=True)
    found = _describe(final)
    if found is None:  # pragma: no cover - the name was built to match
        raise PanelDbError("The backup was saved but could not be read back.")
    return found


def _audited(db_path: Path, action: str, found: BackupFile, now: int, done: str) -> None:
    """Write the audit row for something that already happened to a file."""
    try:
        with write_transaction(db_path) as conn:
            audit.record(
                conn,
                module="database",
                action=action,
                target=found.name,
                detail={"size": found.size},
                now=now,
            )
    except (PanelDbError, sqlite3.Error) as exc:
        logger.warning("Audit entry for %s failed: %s", action, exc)
        raise PanelDbError(
            f"{done} ({found.name}), but its audit entry could not be saved: {exc}"
        ) from exc


def create_backup(db_path: Path, backup_dir: Path, *, now: int) -> BackupFile:
    """Make a backup and record it in the audit log."""
    found = make_backup(db_path, backup_dir, now=now)
    _audited(db_path, "backup.create", found, now, "The backup was made")
    return found


def delete_backup(
    db_path: Path, backup_dir: Path, name: str, *, now: Optional[int] = None
) -> BackupFile:
    """Delete one listed backup and record it in the audit log."""
    found = find_backup(backup_dir, name)
    try:
        found.path.unlink()
    except OSError as exc:
        raise PanelDbError(f"The backup could not be deleted: {exc}") from exc
    _audited(
        db_path, "backup.delete", found, int(time.time()) if now is None else now, "It was deleted"
    )
    return found
