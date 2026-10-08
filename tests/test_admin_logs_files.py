"""Tests for the log file listing and the only way to name a file."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from yokai_admin.core.inputs import InputRefused
from yokai_admin.errors import PanelDbError
from yokai_admin.modules.logs.files import MAX_FILES, find_log_file, list_log_files


def _make(folder: Path, *names: str) -> None:
    folder.mkdir(exist_ok=True)
    for name in names:
        (folder / name).write_text(f"contents of {name}")


def test_listing_puts_current_files_first_then_rotated_in_numeric_order(tmp_path: Path) -> None:
    _make(
        tmp_path,
        "yokai.log.10",
        "yokai.log.2",
        "yokai.log",
        "supervisor.log",
        "yokai.log.1",
        "yokai.log.9",
    )
    listed = list_log_files(tmp_path)
    assert [f.name for f in listed] == [
        "supervisor.log",
        "yokai.log",
        "yokai.log.1",
        "yokai.log.2",
        "yokai.log.9",
        "yokai.log.10",
    ]
    assert [f.current for f in listed] == [True, True, False, False, False, False]
    assert listed[1].size == len("contents of yokai.log") and listed[1].modified_ts > 0
    assert listed[1].path == tmp_path / "yokai.log"


def test_listing_ignores_everything_that_is_not_a_log_file(tmp_path: Path) -> None:
    _make(
        tmp_path,
        "yokai.log",
        "notes.txt",
        "yokai.log.bak",
        "yokai.log~",
        "yokai.log.",
        "yokai.log.99999",
        "yokai.LOG",
        ".log",
        "a b.log",
        "x" * 60 + ".log",
    )
    (tmp_path / "folder.log").mkdir()
    assert [f.name for f in list_log_files(tmp_path)] == ["yokai.log"]


def test_a_symlink_named_like_a_log_is_ignored(tmp_path: Path) -> None:
    _make(tmp_path, "yokai.log")
    secret = tmp_path.parent / f"{tmp_path.name}-secret.txt"
    secret.write_text("not a log")
    link = tmp_path / "evil.log"
    try:
        os.symlink(secret, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available here")
    assert [f.name for f in list_log_files(tmp_path)] == ["yokai.log"]
    with pytest.raises(InputRefused):
        find_log_file(tmp_path, "evil.log")


def test_a_missing_folder_is_empty_and_an_unreadable_one_explains(tmp_path: Path) -> None:
    assert list_log_files(tmp_path / "absent") == []
    not_a_folder = tmp_path / "file.txt"
    not_a_folder.write_text("x")
    with pytest.raises(PanelDbError, match="log folder could not be read"):
        list_log_files(not_a_folder)


def test_the_listing_is_capped(tmp_path: Path) -> None:
    _make(tmp_path, *[f"app{i}.log" for i in range(MAX_FILES + 25)])
    assert len(list_log_files(tmp_path)) == MAX_FILES


def test_find_returns_exactly_the_listed_file(tmp_path: Path) -> None:
    _make(tmp_path, "yokai.log", "yokai.log.1")
    found = find_log_file(tmp_path, "yokai.log.1")
    assert found.name == "yokai.log.1" and found.path == tmp_path / "yokai.log.1"


@pytest.mark.parametrize(
    "name",
    [
        "..\\yokai.log",
        "../yokai.log",
        "C:\\x.log",
        "/etc/passwd",
        "yokai.log:stream",
        "yokai.log.",
        "yokai.log ",
        " yokai.log",
        "yokai.LOG",
        "CON",
        "NUL.log",
        "yokai.log.99999999",
        "yokai.log\x00",
        "",
        "x" * 5000,
        "%2e%2e%2fyokai.log",
        "folder.log",
        "missing.log",
        "notes.txt",
    ],
)
def test_names_that_are_not_in_the_listing_are_refused(tmp_path: Path, name: str) -> None:
    """Review Focus 1."""
    _make(tmp_path, "yokai.log", "notes.txt")
    (tmp_path / "folder.log").mkdir()
    (tmp_path.parent / "yokai.log").write_text("outside the log folder")
    with pytest.raises(InputRefused):
        find_log_file(tmp_path, name)
