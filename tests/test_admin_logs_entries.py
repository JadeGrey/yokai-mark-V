"""Tests for log parsing, filtering and paging."""

from __future__ import annotations

from pathlib import Path

import pytest

from yokai_admin.errors import PanelDbError
from yokai_admin.modules.logs import entries as entries_mod
from yokai_admin.modules.logs.entries import (
    MAX_ENTRY_CHARS,
    MAX_QUERY,
    PAGE_ENTRIES,
    clean_text,
    parse_entries,
    read_page,
)

BOT = (
    "2026-09-24 19:48:57 [INFO] yokai.bot: Ready\n"
    "2026-09-24 19:48:58 [ERROR] yokai.bot: Unexpected error\n"
    "Traceback (most recent call last):\n"
    '  File "tree.py", line 1138, in wrapper\n'
    "ValueError: boom\n"
    "2026-09-24 19:49:00 [WARNING] yokai.music: Slow\n"
    "2026-09-24 19:49:01 [DEBUG] yokai.music: detail\n"
    "2026-09-24 19:49:02 [CRITICAL] yokai: Down\n"
)
SUPERVISOR = (
    "[2026-09-30 23:38:19] [INFO] Supervisor starting.\n"
    "[2026-09-30 23:38:19] [SUCCESS] Activating virtual environment (.venv)...\n"
    "[2026-09-30 23:38:20] [ERROR] Launch failed\n"
)


def _write(tmp_path: Path, text: str, name: str = "yokai.log") -> Path:
    path = tmp_path / name
    path.write_bytes(text.encode("utf-8"))
    return path


def test_the_bot_format_groups_a_traceback_with_its_entry() -> None:
    parsed = parse_entries(BOT)
    assert [e.level for e in parsed] == ["INFO", "ERROR", "WARNING", "DEBUG", "CRITICAL"]
    error = parsed[1]
    assert error.when == "2026-09-24 19:48:58"
    assert "Traceback (most recent call last):" in error.text and "ValueError: boom" in error.text
    assert error.text.startswith("2026-09-24 19:48:58 [ERROR] yokai.bot: Unexpected error")


def test_the_supervisor_format_is_understood() -> None:
    parsed = parse_entries(SUPERVISOR)
    assert [e.level for e in parsed] == ["INFO", "SUCCESS", "ERROR"]
    assert parsed[0].when == "2026-09-30 23:38:19"


def test_lines_before_the_first_entry_become_one_level_less_entry() -> None:
    parsed = parse_entries("cut off tail line\nmore of it\n2026-01-01 00:00:00 [INFO] a: b\n")
    assert [e.level for e in parsed] == [None, "INFO"]
    assert parsed[0].text == "cut off tail line\nmore of it"
    assert parse_entries("") == [] and parse_entries("\n\n") == []


def test_clean_text_removes_dangerous_characters_and_keeps_layout() -> None:
    dirty = "a\x00b\x1b[31mred\x1b[0m‮X﻿Y\tz\r\nnext\rlast"
    assert clean_text(dirty) == "abredXY\tz\nnext\nlast"


def test_the_level_filter_is_a_minimum_and_everything_shows_without_one(tmp_path: Path) -> None:
    path = _write(tmp_path, "orphan\n" + BOT)
    everything = read_page(path, level="", query="", page=1)
    errors = read_page(path, level="ERROR", query="", page=1)
    warnings = read_page(path, level="WARNING", query="", page=1)
    assert everything.total == 6 and everything.level == "ALL"
    assert [e.level for e in errors.entries] == ["CRITICAL", "ERROR"]
    assert [e.level for e in warnings.entries] == ["CRITICAL", "WARNING", "ERROR"]
    assert errors.level == "ERROR" and warnings.total == 3
    assert read_page(path, level="nonsense", query="", page=1).total == 6


def test_unknown_levels_rank_as_info(tmp_path: Path) -> None:
    path = _write(tmp_path, SUPERVISOR)
    infos = read_page(path, level="INFO", query="", page=1)
    warnings = read_page(path, level="WARNING", query="", page=1)
    assert infos.total == 3 and warnings.total == 1


def test_entries_come_newest_first(tmp_path: Path) -> None:
    path = _write(tmp_path, BOT)
    page = read_page(path, level="", query="", page=1)
    assert [e.when for e in page.entries][0] == "2026-09-24 19:49:02"
    assert [e.when for e in page.entries][-1] == "2026-09-24 19:48:57"


@pytest.mark.parametrize("needle", ["boom", "BOOM", "bOoM", "Traceback", "yokai.music"])
def test_search_is_case_insensitive_and_looks_inside_tracebacks(
    tmp_path: Path, needle: str
) -> None:
    path = _write(tmp_path, BOT)
    page = read_page(path, level="", query=needle, page=1)
    assert page.total >= 1 and all(needle.casefold() in e.text.casefold() for e in page.entries)


@pytest.mark.parametrize("needle", ["%", ".*", "[", "\\", "(", "a|b", "_"])
def test_search_text_is_literal(tmp_path: Path, needle: str) -> None:
    path = _write(tmp_path, BOT + "2026-09-24 19:49:03 [INFO] x: 100% done [x] a|b_c\\\n")
    page = read_page(path, level="", query=needle, page=1)
    every = read_page(path, level="", query="", page=1).entries
    expected = [e for e in every if needle.casefold() in e.text.casefold()]
    assert page.entries == expected  # a plain substring test, never a pattern


def test_a_long_query_is_cut_and_whitespace_is_collapsed(tmp_path: Path) -> None:
    path = _write(tmp_path, BOT)
    page = read_page(path, level="", query="  boom   " + "z" * 500, page=1)
    assert len(page.query) == MAX_QUERY and page.query.startswith("boom z")


def test_paging(tmp_path: Path) -> None:
    lines = "".join(
        f"2026-01-01 00:{i // 60:02d}:{i % 60:02d} [INFO] m: line {i}\n" for i in range(250)
    )
    path = _write(tmp_path, lines)
    first = read_page(path, level="", query="", page=1)
    last = read_page(path, level="", query="", page=3)
    beyond = read_page(path, level="", query="", page=99)
    zero = read_page(path, level="", query="", page=0)
    assert first.total == 250 and len(first.entries) == PAGE_ENTRIES
    assert first.entries[0].text.endswith("line 249")
    assert len(last.entries) == 50 and last.entries[-1].text.endswith("line 0")
    assert beyond.entries == [] and beyond.total == 250
    assert zero.page == 1 and zero.entries == first.entries


def test_hostile_content_is_cleaned_but_not_escaped_here(tmp_path: Path) -> None:
    text = "2026-01-01 00:00:00 [INFO] m: <script>alert(1)</script> \x00\x1b[31m‮ end\n"
    page = read_page(_write(tmp_path, text), level="", query="", page=1)
    body = page.entries[0].text
    assert "<script>alert(1)</script>" in body  # escaping is the template's job
    assert "\x00" not in body and "\x1b" not in body and "‮" not in body


def test_invalid_utf8_bom_and_crlf_are_handled(tmp_path: Path) -> None:
    raw = (
        b"\xef\xbb\xbf2026-01-01 00:00:00 [INFO] m: caf\xe9 bad \xff\r\n"
        b"2026-01-01 00:00:01 [INFO] m: ok\r\n"
    )
    path = tmp_path / "yokai.log"
    path.write_bytes(raw)
    page = read_page(path, level="", query="", page=1)
    assert page.total == 2 and "�" in page.entries[1].text
    assert page.entries[1].level == "INFO" and "\r" not in page.entries[1].text


def test_one_enormous_line_is_shortened(tmp_path: Path) -> None:
    path = _write(tmp_path, "2026-01-01 00:00:00 [INFO] m: " + "x" * 5_000_000 + "\n")
    page = read_page(path, level="", query="", page=1)
    assert page.total == 1 and len(page.entries[0].text) <= MAX_ENTRY_CHARS


def test_a_file_over_the_read_limit_is_read_from_a_whole_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(entries_mod, "MAX_READ_BYTES", 400)
    lines = "".join(
        f"2026-01-01 00:00:{i % 60:02d} [INFO] m: entry number {i}\n" for i in range(60)
    )
    lines += "2026-01-01 00:01:00 [ERROR] m: last\nTraceback tail\n"
    page = read_page(_write(tmp_path, lines), level="", query="", page=1)
    assert page.truncated_file is True
    assert all(e.level is not None for e in page.entries)  # no cut-in-half leading entry
    assert page.entries[0].text.endswith("Traceback tail")
    small = read_page(_write(tmp_path, BOT, "small.log"), level="", query="", page=1)
    assert small.truncated_file is False


def test_an_empty_or_missing_file(tmp_path: Path) -> None:
    empty = _write(tmp_path, "")
    page = read_page(empty, level="", query="", page=1)
    assert page.total == 0 and page.entries == []
    with pytest.raises(PanelDbError, match="log file could not be read"):
        read_page(tmp_path / "gone.log", level="", query="", page=1)
