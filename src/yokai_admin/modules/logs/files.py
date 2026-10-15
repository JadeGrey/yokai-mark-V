"""The log folder's files. A request can only name a file that this listing already contains."""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Optional

from yokai_admin.core.inputs import InputRefused
from yokai_admin.errors import PanelDbError

NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}[.]log(?:[.][0-9]{1,3})?$")
MAX_FILES = 200


@dataclass(frozen=True, slots=True)
class LogFile:
    """One log file. `current` is a file still being written (no rotation number)."""

    name: str
    path: Path
    size: int
    modified_ts: int
    current: bool


def _describe(path: Path) -> Optional[LogFile]:
    """A LogFile for a regular file with a log-shaped name, else None."""
    if not NAME_RE.fullmatch(path.name) or path.is_symlink() or not path.is_file():
        return None
    try:
        info = path.stat()
    except OSError:
        return None
    return LogFile(
        name=path.name,
        path=path,
        size=info.st_size,
        modified_ts=int(info.st_mtime),
        current=not path.name.rsplit(".", 1)[-1].isdigit(),
    )


def _order(item: LogFile) -> tuple[int, str, int]:
    """Current files first (by name), then rotated copies by number, 2 before 10."""
    if item.current:
        return (0, item.name, 0)
    base, number = item.name.rsplit(".", 1)
    return (1, base, int(number))


def open_shared(path: Path) -> BinaryIO:
    """Open a log for reading without blocking the bot's own log rotation.

    Python's `open()` on Windows does not allow the file to be renamed or deleted while it is
    open, so a panel read could make the bot's log rotation fail. Opening through
    CreateFileW with FILE_SHARE_DELETE avoids that. Elsewhere a plain open is already safe.
    """
    if sys.platform != "win32":
        return path.open("rb")
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel32.CreateFileW
    create.restype = wintypes.HANDLE
    create.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    generic_read, share_all, open_existing, normal = 0x80000000, 0x7, 3, 0x80
    handle = create(str(path), generic_read, share_all, None, open_existing, normal, None)
    if handle is None or handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY)  # type: ignore[attr-defined]
    return os.fdopen(descriptor, "rb")  # type: ignore[return-value]


def list_log_files(log_dir: Path) -> list[LogFile]:
    """The log files in `log_dir`. A missing folder is an empty list."""
    try:
        entries = list(log_dir.iterdir())
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise PanelDbError(f"The log folder could not be read: {exc}") from exc
    found = [item for item in (_describe(path) for path in entries) if item is not None]
    return sorted(found, key=_order)[:MAX_FILES]


def find_log_file(log_dir: Path, name: str) -> LogFile:
    """The listed file with exactly this name. Request text never becomes a path directly."""
    for item in list_log_files(log_dir):
        if item.name == name:
            return item
    raise InputRefused("There is no log file with that name.")
