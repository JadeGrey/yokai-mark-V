"""Tests for the Database module's read side: table list, validated browser, integrity check."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from tests.admin_helpers import seed_database
from yokai_admin.core.db import readonly
from yokai_admin.core.inputs import InputRefused
from yokai_admin.modules.database.service import (
    MAX_CELL_CHARS,
    PAGE_ROWS,
    format_cell,
    integrity_check,
    list_tables,
    read_table,
)


def _db(tmp_path: Path) -> Path:
    path = tmp_path / "yokai.db"
    seed_database(path, wallets=3, names={1: "Alex"})
    return path


def _extra(path: Path, *statements: str) -> None:
    raw = sqlite3.connect(path)
    try:
        for sql in statements:
            raw.execute(sql)
        raw.commit()
    finally:
        raw.close()


def test_list_tables_counts_rows_and_hides_sqlite_internals(tmp_path: Path) -> None:
    path = _db(tmp_path)
    with readonly(path) as conn:
        tables = {t.name: t.rows for t in list_tables(conn)}
        names = [t.name for t in list_tables(conn)]
    assert tables["wallets"] == 3 and tables["user_names"] == 1
    assert not any(name.startswith("sqlite_") for name in names)
    assert names == sorted(names)


def test_read_table_pages_with_columns_and_totals(tmp_path: Path) -> None:
    path = _db(tmp_path)
    _extra(
        path,
        "CREATE TABLE big (id INTEGER PRIMARY KEY, label TEXT);",
        *[f"INSERT INTO big (label) VALUES ('row {i}');" for i in range(PAGE_ROWS + 7)],
    )
    with readonly(path) as conn:
        first = read_table(conn, "big", 1)
        second = read_table(conn, "big", 2)
        beyond = read_table(conn, "big", 99)
    assert first.columns == ["id", "label"] and first.total == PAGE_ROWS + 7
    assert len(first.rows) == PAGE_ROWS and first.rows[0] == ["1", "row 0"]
    assert len(second.rows) == 7 and second.rows[0][1] == f"row {PAGE_ROWS}"
    assert beyond.rows == [] and beyond.total == PAGE_ROWS + 7


@pytest.mark.parametrize(
    "name",
    [
        "wallets; DROP TABLE wallets",
        '"wallets"',
        "wallets--",
        "sqlite_master",
        "sqlite_sequence",
        "WALLETS",
        "wallets ",
        " wallets",
        "",
        "x" * 5000,
        "wallets\x00",
        "nope",
    ],
)
def test_unknown_table_names_are_refused(tmp_path: Path, name: str) -> None:
    """Review Focus 3."""
    path = _db(tmp_path)
    with readonly(path) as conn, pytest.raises(InputRefused):
        read_table(conn, name, 1)
    with readonly(path) as conn:
        assert {t.name for t in list_tables(conn)} >= {"wallets"}


def test_a_table_name_with_a_quote_is_browsable_and_quoted(tmp_path: Path) -> None:
    path = _db(tmp_path)
    _extra(
        path,
        'CREATE TABLE "odd""name" (a TEXT);',
        'INSERT INTO "odd""name" VALUES (\'hello\');',
    )
    with readonly(path) as conn:
        page = read_table(conn, 'odd"name', 1)
        counted = {t.name: t.rows for t in list_tables(conn)}
    assert page.rows == [["hello"]] and counted['odd"name'] == 1


def test_format_cell_handles_every_kind_of_value() -> None:
    assert format_cell(None) == "NULL"
    assert format_cell(b"\x00\x01\x02") == "<3 bytes>"
    assert format_cell(42) == "42" and format_cell(1.5) == "1.5"
    assert format_cell("<script>alert(1)</script>") == "<script>alert(1)</script>"  # escaped later
    long = format_cell("y" * 500)
    assert len(long) == MAX_CELL_CHARS and long.endswith("…")
    assert format_cell("tab\tand\nnewline") == "tab and newline"
    assert format_cell("a\x00b‮C") == "abC"  # control and bidi characters dropped


def test_integrity_check_reports_ok_on_a_good_database(tmp_path: Path) -> None:
    path = _db(tmp_path)
    with readonly(path) as conn:
        assert integrity_check(conn) == ["ok"]


def test_integrity_check_reports_problems_up_to_the_limit(tmp_path: Path) -> None:
    path = _db(tmp_path)
    _extra(
        path,
        "CREATE TABLE junk (id INTEGER PRIMARY KEY, v TEXT);",
        "CREATE INDEX junk_v ON junk (v);",
        *[f"INSERT INTO junk (v) VALUES ('value {i}');" for i in range(400)],
    )
    broken = tmp_path / "broken.db"
    shutil.copy(path, broken)
    # Damage the last pages (the junk table and its index) so the b-tree no longer matches.
    data = bytearray(broken.read_bytes())
    for offset in range(len(data) - 4096 * 3, len(data), 97):
        data[offset] ^= 0xFF
    broken.write_bytes(bytes(data))
    with readonly(broken) as conn:
        problems = integrity_check(conn, limit=5)
    assert problems != ["ok"] and 1 <= len(problems) <= 5
