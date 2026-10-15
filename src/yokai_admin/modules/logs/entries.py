"""Turn a log file into entries, filter them, and cut one page (newest first)."""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from yokai_admin.core.inputs import parse_page  # noqa: F401  (re-exported for the router)
from yokai_admin.errors import PanelDbError
from yokai_admin.modules.logs.files import open_shared

PAGE_ENTRIES = 100
MAX_READ_BYTES = 8 * 1024 * 1024
MAX_ENTRY_CHARS = 4000
MAX_QUERY = 100
LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
_RANK = {
    "DEBUG": 10,
    "INFO": 20,
    "SUCCESS": 20,
    "WARNING": 30,
    "WARN": 30,
    "ERROR": 40,
    "CRITICAL": 50,
}
_UNKNOWN_RANK = 20
# "2026-09-24 19:48:58 [ERROR] ..." (the bot) and "[2026-09-30 23:38:19] [INFO] ..." (supervisor)
_HEAD = re.compile(
    r"^(?:(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) |\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] )"
    r"\[([A-Za-z]+)\]"
)
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_TRUNCATED = " … [shortened]"


@dataclass(frozen=True, slots=True)
class LogEntry:
    """One log record: its header line plus any continuation lines (a traceback)."""

    text: str
    level: Optional[str]
    when: Optional[str]


@dataclass(frozen=True, slots=True)
class LogPage:
    """One page of entries, newest first."""

    entries: list[LogEntry]
    total: int
    page: int
    truncated_file: bool
    level: str
    query: str


def clean_text(text: str) -> str:
    """Normalise line endings and drop control, ANSI and bidirectional-override characters."""
    text = _ANSI.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))
    return "".join(
        ch for ch in text if ch in "\n\t" or unicodedata.category(ch) not in ("Cc", "Cf")
    )


def level_rank(level: Optional[str]) -> int:
    """Severity of a level label; labels the panel does not know count as INFO."""
    return _RANK.get((level or "").upper(), _UNKNOWN_RANK)


def _entry(lines: list[str], level: Optional[str], when: Optional[str]) -> LogEntry:
    text = "\n".join(lines)
    if len(text) > MAX_ENTRY_CHARS:
        text = text[: MAX_ENTRY_CHARS - len(_TRUNCATED)] + _TRUNCATED
    return LogEntry(text=text, level=level, when=when)


def parse_entries(text: str) -> list[LogEntry]:
    """Group cleaned text into entries; leading lines with no header form one level-less entry."""
    entries: list[LogEntry] = []
    lines: list[str] = []
    level: Optional[str] = None
    when: Optional[str] = None
    for line in clean_text(text).split("\n"):
        head = _HEAD.match(line)
        if head:
            if lines:
                entries.append(_entry(lines, level, when))
            lines, level, when = [line], head.group(3).upper(), head.group(1) or head.group(2)
        elif line.strip() or lines:
            lines.append(line)
    if lines:
        entries.append(_entry(lines, level, when))
    for index, item in enumerate(entries):  # trim trailing blank lines left by the file's end
        stripped = item.text.rstrip("\n")
        if stripped != item.text:
            entries[index] = LogEntry(stripped, item.level, item.when)
    return entries


def _read_text(path: Path) -> tuple[str, bool]:
    """The file's text (its last MAX_READ_BYTES at most, from a whole line) and a cut flag."""
    try:
        with open_shared(path) as handle:
            size = handle.seek(0, os.SEEK_END)
            cut = size > MAX_READ_BYTES
            handle.seek(size - MAX_READ_BYTES if cut else 0)
            data = handle.read(MAX_READ_BYTES)
    except OSError as exc:
        raise PanelDbError(f"The log file could not be read: {exc}") from exc
    if cut:
        newline = data.find(b"\n")
        data = data[newline + 1 :] if newline != -1 else b""
    return data.decode("utf-8", errors="replace"), cut


def read_page(path: Path, *, level: str, query: str, page: int) -> LogPage:
    """Read one page of `path`, newest entry first, filtered by minimum level and text."""
    text, cut = _read_text(path)
    parsed = parse_entries(text)
    if cut and parsed and parsed[0].level is None:
        parsed = parsed[1:]  # the first entry was cut in half by the read limit
    wanted = level.upper() if level.upper() in LEVELS else "ALL"
    needle = " ".join(clean_text(query).split())[:MAX_QUERY]
    if wanted != "ALL":
        floor = _RANK[wanted]
        parsed = [e for e in parsed if e.level is not None and level_rank(e.level) >= floor]
    if needle:
        folded = needle.casefold()
        parsed = [e for e in parsed if folded in e.text.casefold()]
    parsed.reverse()
    number = max(1, page)
    start = (number - 1) * PAGE_ENTRIES
    return LogPage(
        entries=parsed[start : start + PAGE_ENTRIES],
        total=len(parsed),
        page=number,
        truncated_file=cut,
        level=wanted,
        query=needle,
    )
