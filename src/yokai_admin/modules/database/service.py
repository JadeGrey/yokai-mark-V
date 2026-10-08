"""Database read side: the table list, a validated paged table browser, and the integrity check."""

from __future__ import annotations

import sqlite3
import unicodedata
from dataclasses import dataclass

from yokai_admin.core.inputs import InputRefused

PAGE_ROWS = 50
MAX_CELL_CHARS = 120
_WHITESPACE = {"\t", "\n", "\r"}


@dataclass(frozen=True, slots=True)
class TableInfo:
    """One table and how many rows it holds."""

    name: str
    rows: int


@dataclass(frozen=True, slots=True)
class TablePage:
    """One page of a table, every cell already turned into display text."""

    name: str
    columns: list[str]
    rows: list[list[str]]
    total: int
    page: int


def _quote(identifier: str) -> str:
    """An identifier safe to put in SQL: double quotes doubled, the whole name quoted."""
    return '"' + identifier.replace('"', '""') + '"'


def _table_names(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite\\_%' "
        "ESCAPE '\\' ORDER BY name;"
    ).fetchall()
    return [str(row[0]) for row in rows]


def list_tables(conn: sqlite3.Connection) -> list[TableInfo]:
    """Every user table with its row count, sorted by name (SQLite's own tables are hidden)."""
    return [
        TableInfo(name, int(conn.execute(f"SELECT COUNT(*) FROM {_quote(name)};").fetchone()[0]))
        for name in _table_names(conn)
    ]


def format_cell(value: object) -> str:
    """Display text for one value: NULL, a blob size, or clean text cut to a readable length."""
    if value is None:
        return "NULL"
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<{len(bytes(value))} bytes>"
    text = str(value)
    cleaned = "".join(
        " " if ch in _WHITESPACE else ch
        for ch in text
        if ch in _WHITESPACE or unicodedata.category(ch) not in ("Cc", "Cf")
    )
    if len(cleaned) > MAX_CELL_CHARS:
        return cleaned[: MAX_CELL_CHARS - 1] + "…"
    return cleaned


def read_table(conn: sqlite3.Connection, name: str, page: int) -> TablePage:
    """One page of a table. The name must be exactly one of the database's own table names."""
    if name not in _table_names(conn):
        raise InputRefused("There is no table with that name.")
    page = max(1, page)
    quoted = _quote(name)
    total = int(conn.execute(f"SELECT COUNT(*) FROM {quoted};").fetchone()[0])
    cursor = conn.execute(
        f"SELECT * FROM {quoted} LIMIT ? OFFSET ?;", (PAGE_ROWS, (page - 1) * PAGE_ROWS)
    )
    columns = [str(col[0]) for col in cursor.description]
    rows = [[format_cell(value) for value in row] for row in cursor.fetchall()]
    return TablePage(name=name, columns=columns, rows=rows, total=total, page=page)


def integrity_check(conn: sqlite3.Connection, limit: int = 20) -> list[str]:
    """`["ok"]`, or up to `limit` problems SQLite found."""
    try:
        rows = conn.execute("PRAGMA integrity_check;").fetchall()
    except sqlite3.DatabaseError as exc:
        return [f"SQLite could not finish the check: {exc}"]
    lines = [str(row[0]) for row in rows]
    return lines[:limit] if lines != ["ok"] else ["ok"]
