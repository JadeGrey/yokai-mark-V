"""Incremental reads of a growing log file, for the live tail.

The page polls with the byte position and file identity it last saw. Only complete lines are
returned; a half-written last line waits for the next poll. A replaced or shortened file is a
rotation: reading restarts at the top of the new file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from yokai_admin.errors import PanelDbError
from yokai_admin.modules.logs.entries import MAX_ENTRY_CHARS, clean_text
from yokai_admin.modules.logs.files import open_shared

INITIAL_LINES = 100
INITIAL_READ_BYTES = 256 * 1024
MAX_POLL_BYTES = 256 * 1024


@dataclass(frozen=True, slots=True)
class TailChunk:
    """New complete lines, where to resume, and whether the file was replaced or shortened."""

    lines: list[str]
    pos: int
    ino: int
    rotated: bool


def _lines(data: bytes) -> list[str]:
    """Complete lines of `data`, cleaned and shortened (a trailing newline adds no empty line)."""
    text = clean_text(data.decode("utf-8", errors="replace"))
    if text.endswith("\n"):
        text = text[:-1]
    return [line[:MAX_ENTRY_CHARS] for line in text.split("\n")] if text or data else []


def _unreadable(exc: OSError) -> PanelDbError:
    return PanelDbError(f"The log file could not be read: {exc}")


def initial_tail(path: Path) -> TailChunk:
    """The last INITIAL_LINES complete lines, and the position just after the last of them."""
    try:
        with open_shared(path) as handle:
            info = os.fstat(handle.fileno())
            start = max(0, info.st_size - INITIAL_READ_BYTES)
            handle.seek(start)
            data = handle.read(info.st_size - start)
    except OSError as exc:
        raise _unreadable(exc) from exc
    if start > 0:
        newline = data.find(b"\n")
        start += newline + 1 if newline != -1 else len(data)
        data = data[newline + 1 :] if newline != -1 else b""
    end = data.rfind(b"\n")
    if end == -1:
        return TailChunk(lines=[], pos=start, ino=info.st_ino, rotated=False)
    complete = data[: end + 1]
    return TailChunk(
        lines=_lines(complete)[-INITIAL_LINES:],
        pos=start + len(complete),
        ino=info.st_ino,
        rotated=False,
    )


def read_new(path: Path, pos: int, ino: int) -> TailChunk:
    """Complete lines added since `pos`; restarts at the top if the file is not the same one."""
    try:
        with open_shared(path) as handle:
            info = os.fstat(handle.fileno())
            rotated = info.st_ino != ino or pos < 0 or pos > info.st_size
            if rotated:
                pos = 0
            handle.seek(pos)
            data = handle.read(MAX_POLL_BYTES)
    except OSError as exc:
        raise _unreadable(exc) from exc
    end = data.rfind(b"\n")
    if end == -1:
        if len(data) >= MAX_POLL_BYTES:  # one line longer than a poll: cut it rather than stall
            return TailChunk(_lines(data + b"\n"), pos + len(data), info.st_ino, rotated)
        return TailChunk(lines=[], pos=pos, ino=info.st_ino, rotated=rotated)
    complete = data[: end + 1]
    return TailChunk(_lines(complete), pos + len(complete), info.st_ino, rotated)
