"""Logs routes: file list, viewer, live tail (polling) and download. Everything here is GET."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Request
from fastapi.responses import Response

from yokai_admin.core.inputs import InputRefused, parse_page
from yokai_admin.core.settings import PanelSettings
from yokai_admin.core.templating import render, render_fragment
from yokai_admin.errors import PanelDbError
from yokai_admin.modules.logs.entries import LEVELS, MAX_QUERY, PAGE_ENTRIES, read_page
from yokai_admin.modules.logs.files import LogFile, find_log_file, list_log_files, open_shared
from yokai_admin.modules.logs.tail import TailChunk, initial_tail, read_new

logger = logging.getLogger(__name__)
router = APIRouter()
CURRENT_LOG = "yokai.log"
_MAX_DIGITS = 20
_MAX_INO_DIGITS = 40  # file IDs on some Windows volumes (ReFS) are wider than 64 bits
MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024
_NO_CURRENT = "No current log file yet. It appears here once the bot has started."
_ROTATED = "The log was rotated. Showing the new file from its start."


@dataclass(frozen=True, slots=True)
class Flash:
    """A one-line problem shown above the page."""

    kind: str
    text: str


@dataclass(frozen=True, slots=True)
class Line:
    """One line of the live tail; `marker` lines are notices, not log text."""

    text: str
    marker: bool = False


def _log_dir(request: Request) -> Path:
    settings: PanelSettings = request.app.state.settings
    return settings.log_dir


def _digits(raw: str, limit: int = _MAX_DIGITS) -> Optional[int]:
    """A non-negative whole number from plain ASCII digits, else None."""
    if raw.isascii() and raw.isdigit() and len(raw) <= limit:
        return int(raw)
    return None


def _files_page(request: Request, flash: Optional[Flash] = None) -> Response:
    files: list[LogFile] = []
    problem: Optional[str] = flash.text if flash else None
    try:
        files = list_log_files(_log_dir(request))
    except PanelDbError as exc:
        problem = str(exc)
    return render(
        request,
        "logs/page.html",
        active="logs",
        files=files,
        problem=problem,
        log_dir=_log_dir(request),
    )


@router.get("")
def files(request: Request) -> Response:
    return _files_page(request)


@router.get("/view")
def view(
    request: Request, file: str = "", level: str = "", q: str = "", page: str = "1"
) -> Response:
    result = None
    problem: Optional[str] = None
    try:
        found = find_log_file(_log_dir(request), file)
        result = read_page(found.path, level=level, query=q[: MAX_QUERY * 4], page=parse_page(page))
    except (InputRefused, PanelDbError) as exc:
        problem = str(exc)
    return render(
        request,
        "logs/view.html",
        active="logs",
        file=file if result else "",
        result=result,
        problem=problem,
        levels=LEVELS,
        has_older=result is not None and result.page * PAGE_ENTRIES < result.total,
        has_newer=result is not None and result.page > 1,
    )


@router.get("/download")
def download(request: Request, file: str = "") -> Response:
    try:
        found = find_log_file(_log_dir(request), file)
    except (InputRefused, PanelDbError) as exc:
        return _files_page(request, Flash("error", str(exc)))
    try:
        with open_shared(found.path) as handle:
            size = os.fstat(handle.fileno()).st_size
            if size > MAX_DOWNLOAD_BYTES:
                return _files_page(
                    request,
                    Flash("error", f"{found.name} is too large to download here. Copy it from the folder."),
                )
            data = handle.read(size)
    except OSError as exc:
        logger.warning("Could not read %s for download: %s", found.name, exc)
        return _files_page(request, Flash("error", "The log file could not be read."))
    # One bounded read: the file may grow or be rotated while it is being sent otherwise.
    return Response(
        content=data,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{found.name}"'},
    )


def _items(chunk: TailChunk) -> list[Line]:
    """Newest first, with a rotation notice after (older than) the new file's lines."""
    items = [Line(text) for text in reversed(chunk.lines)]
    if chunk.rotated:
        items.append(Line(_ROTATED, marker=True))
    return items


def _current(request: Request) -> LogFile:
    return find_log_file(_log_dir(request), CURRENT_LOG)


def _tail_message(exc: Exception) -> str:
    return _NO_CURRENT if isinstance(exc, InputRefused) else str(exc)


@router.get("/live")
def live(request: Request) -> Response:
    context: dict[str, Any] = {"items": [], "pos": 0, "ino": 0, "problem": None}
    try:
        chunk = initial_tail(_current(request).path)
        context.update(items=_items(chunk), pos=chunk.pos, ino=chunk.ino)
    except (InputRefused, PanelDbError) as exc:
        context["problem"] = _tail_message(exc)
    return render(request, "logs/live.html", active="logs", **context)


@router.get("/live/poll")
def poll(request: Request, pos: str = "", ino: str = "") -> Response:
    start, ident = _digits(pos), _digits(ino, _MAX_INO_DIGITS)
    context: dict[str, Any] = {"items": [], "pos": start or 0, "ino": ident or 0, "problem": None}
    try:
        path = _current(request).path
        if start is None or ident is None or ident == 0:
            # tampered, or no file existed at the last poll (id 0): read the tail afresh
            chunk = initial_tail(path)
        else:
            chunk = read_new(path, start, ident)
        context.update(items=_items(chunk), pos=chunk.pos, ino=chunk.ino)
    except (InputRefused, PanelDbError) as exc:
        context["problem"] = _tail_message(exc)  # keep polling; the file may come back
    return render_fragment(request, "logs/_poll.html", **context)


@router.get("/live/pause")
def pause(request: Request, pos: str = "", ino: str = "") -> Response:
    return render_fragment(
        request,
        "logs/_poller.html",
        pos=_digits(pos) or 0,
        ino=_digits(ino, _MAX_INO_DIGITS) or 0,
        paused=True,
        problem=None,
    )
