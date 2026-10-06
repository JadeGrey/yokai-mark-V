"""Read-only access to the bot's SQLite file. The bot owns the schema; the panel never migrates."""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from yokai_admin.errors import PanelDbError

logger = logging.getLogger(__name__)

MIN_SCHEMA_VERSION = 5
WRITE_BUSY_TIMEOUT_S = 5.0


def _require_file(db_path: Path) -> None:
    if not db_path.exists():
        raise PanelDbError(
            f"Database not found at {db_path}. Start the bot once so it can create it, "
            "or set DB_PATH in .env."
        )


def open_readonly(db_path: Path) -> sqlite3.Connection:
    """Open the bot's database read-only (rows come back as `sqlite3.Row`)."""
    _require_file(db_path)
    uri = db_path.resolve().as_uri() + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    except sqlite3.Error as exc:
        raise PanelDbError(f"Could not open the database read-only: {exc}") from exc
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def readonly(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Open the database read-only for the duration of a `with` block."""
    conn = open_readonly(db_path)
    try:
        yield conn
    finally:
        conn.close()


def schema_version(conn: sqlite3.Connection) -> int:
    """Return `PRAGMA user_version`, mapping a non-SQLite file to a clear error."""
    try:
        return int(conn.execute("PRAGMA user_version;").fetchone()[0])
    except sqlite3.DatabaseError as exc:
        raise PanelDbError(f"That file is not a SQLite database ({exc}).") from exc


def require_schema(conn: sqlite3.Connection, minimum: int = MIN_SCHEMA_VERSION) -> int:
    """Return the schema version, or raise if the bot's database is too old for the panel."""
    version = schema_version(conn)
    if version < minimum:
        raise PanelDbError(
            f"The database schema is v{version}; the panel needs v{minimum} or newer. "
            "Start the bot once to upgrade it."
        )
    return version


def open_readwrite(db_path: Path, busy_timeout_s: float) -> sqlite3.Connection:
    """Open the bot's database for writing. Autocommit, so transactions are always explicit."""
    _require_file(db_path)
    uri = db_path.resolve().as_uri() + "?mode=rw"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=busy_timeout_s, isolation_level=None)
    except sqlite3.Error as exc:
        raise PanelDbError(f"Could not open the database for writing: {exc}") from exc
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def write_transaction(
    db_path: Path, *, busy_timeout_s: Optional[float] = None
) -> Iterator[sqlite3.Connection]:
    """One atomic write: BEGIN IMMEDIATE, schema check, commit on success, roll back on error.

    BEGIN IMMEDIATE takes SQLite's write lock up front, so everything the block reads is still
    true when it writes; the bot's in-process lock does not cover this second process.
    """
    timeout = WRITE_BUSY_TIMEOUT_S if busy_timeout_s is None else busy_timeout_s
    conn = open_readwrite(db_path, timeout)
    try:
        try:
            conn.execute("BEGIN IMMEDIATE;")
        except sqlite3.Error as exc:
            text = str(exc).lower()
            if "locked" in text or "busy" in text:
                raise PanelDbError(
                    "The database is busy right now. Try again in a moment."
                ) from exc
            raise PanelDbError(f"Could not start a database write: {exc}") from exc
        try:
            require_schema(conn)
            yield conn
            conn.execute("COMMIT;")
        except BaseException:
            try:
                conn.execute("ROLLBACK;")
            except sqlite3.Error:
                logger.warning("Rollback failed; the connection is closing anyway.")
            raise
    finally:
        conn.close()
