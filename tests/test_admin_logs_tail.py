"""Tests for the live tail's incremental reads."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from yokai_admin.errors import PanelDbError
from yokai_admin.modules.logs import tail as tail_mod
from yokai_admin.modules.logs.tail import INITIAL_LINES, initial_tail, read_new


def _append(path: Path, text: str) -> None:
    with path.open("ab") as handle:
        handle.write(text.encode("utf-8"))


def _numbered(count: int, start: int = 0) -> str:
    return "".join(f"line {i}\n" for i in range(start, start + count))


def test_initial_tail_of_a_short_long_and_empty_file(tmp_path: Path) -> None:
    short = tmp_path / "short.log"
    short.write_text(_numbered(3))
    chunk = initial_tail(short)
    assert chunk.lines == ["line 0", "line 1", "line 2"] and chunk.pos == short.stat().st_size
    long = tmp_path / "long.log"
    long.write_text(_numbered(INITIAL_LINES + 50))
    last = initial_tail(long)
    assert len(last.lines) == INITIAL_LINES
    assert last.lines[0] == "line 50" and last.lines[-1] == f"line {INITIAL_LINES + 49}"
    empty = tmp_path / "empty.log"
    empty.write_text("")
    nothing = initial_tail(empty)
    assert nothing.lines == [] and nothing.pos == 0


def test_initial_tail_holds_back_a_partial_last_line(tmp_path: Path) -> None:
    path = tmp_path / "yokai.log"
    path.write_bytes(b"done 1\ndone 2\nhalf writ")
    chunk = initial_tail(path)
    assert chunk.lines == ["done 1", "done 2"]
    assert chunk.pos == len("done 1\ndone 2\n")
    _append(path, "ten\n")
    assert read_new(path, chunk.pos, chunk.ino).lines == ["half written"]


def test_initial_tail_of_a_huge_file_starts_on_a_whole_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tail_mod, "INITIAL_READ_BYTES", 200)
    path = tmp_path / "yokai.log"
    path.write_text(_numbered(500))
    chunk = initial_tail(path)
    assert chunk.lines and all(line.startswith("line ") for line in chunk.lines)
    assert chunk.lines[-1] == "line 499" and chunk.lines[0] != "line 0"
    assert not chunk.lines[0].startswith("ine")


def test_read_new_returns_only_appended_complete_lines(tmp_path: Path) -> None:
    path = tmp_path / "yokai.log"
    path.write_text(_numbered(2))
    first = initial_tail(path)
    nothing = read_new(path, first.pos, first.ino)
    assert nothing.lines == [] and nothing.pos == first.pos and not nothing.rotated
    _append(path, "a\nb\n")
    got = read_new(path, first.pos, first.ino)
    assert got.lines == ["a", "b"] and got.pos == path.stat().st_size and not got.rotated
    assert read_new(path, got.pos, got.ino).lines == []


def test_a_partial_line_is_delivered_once_and_never_duplicated(tmp_path: Path) -> None:
    path = tmp_path / "yokai.log"
    path.write_text("")
    pos, ino = 0, os.stat(path).st_ino
    seen: list[str] = []
    for piece in ("al", "pha\nbe", "ta\n", "gam", "ma\n\n", "x\n"):
        _append(path, piece)
        chunk = read_new(path, pos, ino)
        seen.extend(chunk.lines)
        pos = chunk.pos
    assert seen == ["alpha", "beta", "gamma", "", "x"]


def test_more_than_one_poll_of_new_data_arrives_over_several_polls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tail_mod, "MAX_POLL_BYTES", 100)
    path = tmp_path / "yokai.log"
    path.write_text("")
    pos, ino = 0, os.stat(path).st_ino
    _append(path, _numbered(80))
    collected: list[str] = []
    for _ in range(200):
        chunk = read_new(path, pos, ino)
        collected.extend(chunk.lines)
        pos = chunk.pos
        if pos == path.stat().st_size:
            break
    assert collected == [f"line {i}" for i in range(80)]


def test_a_single_line_longer_than_a_poll_is_cut_not_stuck(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tail_mod, "MAX_POLL_BYTES", 50)
    path = tmp_path / "yokai.log"
    path.write_text("x" * 500 + "\nnext\n")
    pos, ino = 0, os.stat(path).st_ino
    lines: list[str] = []
    for _ in range(50):
        chunk = read_new(path, pos, ino)
        lines.extend(chunk.lines)
        pos = chunk.pos
        if pos == path.stat().st_size:
            break
    assert "next" in lines and pos == path.stat().st_size


def test_rotation_by_rename_restarts_from_the_new_file(tmp_path: Path) -> None:
    """Review Focus 4: RotatingFileHandler renames yokai.log away and starts a new one."""
    path = tmp_path / "yokai.log"
    path.write_text(_numbered(5))
    first = initial_tail(path)
    os.replace(path, tmp_path / "yokai.log.1")
    path.write_text("fresh 1\nfresh 2\n")
    chunk = read_new(path, first.pos, first.ino)
    assert chunk.rotated is True and chunk.lines == ["fresh 1", "fresh 2"]
    assert chunk.ino != first.ino and chunk.pos == path.stat().st_size
    again = read_new(path, chunk.pos, chunk.ino)
    assert again.lines == [] and again.rotated is False


def test_truncation_counts_as_rotation(tmp_path: Path) -> None:
    path = tmp_path / "yokai.log"
    path.write_text(_numbered(20))
    first = initial_tail(path)
    path.write_text("after truncate\n")
    chunk = read_new(path, first.pos, first.ino)
    assert chunk.rotated is True and chunk.lines == ["after truncate"]


@pytest.mark.parametrize("pos", [-1, -(10**9), 10**12, 10**30, 999999])
def test_a_tampered_position_restarts_instead_of_failing(tmp_path: Path, pos: int) -> None:
    path = tmp_path / "yokai.log"
    path.write_text(_numbered(3))
    chunk = read_new(path, pos, os.stat(path).st_ino)
    assert chunk.rotated is True and chunk.lines == ["line 0", "line 1", "line 2"]


def test_a_wrong_inode_restarts(tmp_path: Path) -> None:
    path = tmp_path / "yokai.log"
    path.write_text(_numbered(2))
    chunk = read_new(path, 4, os.stat(path).st_ino + 12345)
    assert chunk.rotated is True and chunk.lines == ["line 0", "line 1"]


def test_a_vanished_file_explains(tmp_path: Path) -> None:
    with pytest.raises(PanelDbError, match="log file could not be read"):
        read_new(tmp_path / "gone.log", 0, 0)
    with pytest.raises(PanelDbError, match="log file could not be read"):
        initial_tail(tmp_path / "gone.log")


def test_lines_are_cleaned_and_shortened(tmp_path: Path) -> None:
    path = tmp_path / "yokai.log"
    path.write_bytes(b"ok\r\n\x1b[31mred\x1b[0m \x00 \xe2\x80\xaeevil\n" + b"y" * 9000 + b"\n")
    chunk = initial_tail(path)
    assert chunk.lines[0] == "ok" and chunk.lines[1] == "red  evil"
    assert len(chunk.lines[2]) <= 4000


@pytest.mark.skipif(sys.platform != "win32", reason="Windows blocks renaming open files")
def test_tail_reads_do_not_block_rotation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "yokai.log"
    path.write_bytes(b"a\nb\n")
    real = tail_mod.open_shared
    renamed: list[int] = []

    def opened_then_rotated(target: Path):  # type: ignore[no-untyped-def]
        handle = real(target)
        os.replace(target, tmp_path / f"yokai.log.{len(renamed) + 1}")
        renamed.append(1)
        path.write_bytes(b"a\nb\n")
        return handle

    monkeypatch.setattr(tail_mod, "open_shared", opened_then_rotated)
    assert initial_tail(path).lines == ["a", "b"]
    assert read_new(path, 0, 0).lines == ["a", "b"] and len(renamed) == 2
