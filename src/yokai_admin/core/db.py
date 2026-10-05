"""Read-only access to the bot's SQLite file. The bot owns the schema; the panel never migrates."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from yokai_admin.errors import PanelDbError

MIN_SCHEMA_VERSION = 4


def open_readonly(db_path: Path) -> sqlite3.Connection:
    """Open the bot's database read-only (rows come back as `sqlite3.Row`)."""
    if not db_path.exists():
        raise PanelDbError(
            f"Database not found at {db_path}. Start the bot once so it can create it, "
            "or set DB_PATH in .env."
        )
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
