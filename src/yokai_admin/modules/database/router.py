"""Database routes: overview, table browser, integrity, VACUUM, backups and restore.

Every action answers with the whole `#db-body` region (HTTP 200, with a one-line result), so the
page never shows stale numbers after a change.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from fastapi import APIRouter, Form, Request
from fastapi.responses import Response

from yokai_admin.core.db import readonly, require_schema
from yokai_admin.core.formatting import format_bytes
from yokai_admin.core.inputs import InputRefused, parse_page
from yokai_admin.core.settings import PanelSettings
from yokai_admin.core.templating import render, render_fragment
from yokai_admin.errors import PanelDbError
from yokai_admin.modules.dashboard.service import bot_status
from yokai_admin.modules.database.backups import (
    BackupFile,
    create_backup,
    delete_backup,
    find_backup,
    list_backups,
)
from yokai_admin.modules.database.maintenance import (
    maintenance_lock,
    restore_backup,
    vacuum,
)
from yokai_admin.modules.database.service import (
    PAGE_ROWS,
    integrity_check,
    list_tables,
    read_table,
)

logger = logging.getLogger(__name__)
router = APIRouter()
_FAILED = "That did not work. Nothing was changed that the page does not say."


@dataclass(frozen=True, slots=True)
class Flash:
    """A one-line result shown at the top of the page."""

    kind: str  # "ok" or "error"
    text: str


def _db_message(exc: Exception) -> str:
    if isinstance(exc, PanelDbError):
        return str(exc)
    logger.warning("The Database page could not read the database: %s", exc)
    return f"The database could not be read right now ({exc}). It will retry shortly."


def _body_context(
    request: Request, flash: Optional[Flash] = None, integrity: Optional[list[str]] = None
) -> dict[str, Any]:
    settings: PanelSettings = request.app.state.settings
    now = int(time.time())
    context: dict[str, Any] = {
        "flash": flash,
        "integrity": integrity,
        "db_error": None,
        "size": None,
        "schema": None,
        "tables": [],
        "bot": None,
        "backups": [],
        "backup_error": None,
        "backup_dir": settings.effective_backup_dir,
    }
    try:
        with readonly(settings.db_path) as conn:
            context["schema"] = require_schema(conn)
            context["tables"] = list_tables(conn)
            context["bot"] = bot_status(conn, now)
        context["size"] = settings.db_path.stat().st_size
    except (PanelDbError, sqlite3.Error, OSError) as exc:
        context["db_error"] = _db_message(exc)
    try:
        context["backups"] = list_backups(settings.effective_backup_dir)
    except PanelDbError as exc:
        context["backup_error"] = str(exc)
    return context


def _body(
    request: Request, flash: Optional[Flash] = None, integrity: Optional[list[str]] = None
) -> Response:
    return render_fragment(
        request, "database/_body.html", **_body_context(request, flash, integrity)
    )


@router.get("")
def overview(request: Request) -> Response:
    return render(request, "database/page.html", active="database", **_body_context(request))


@router.get("/body")
def body(request: Request) -> Response:
    return _body(request)


@router.get("/table")
def table(request: Request, name: str = "", page: str = "1") -> Response:
    settings: PanelSettings = request.app.state.settings
    number = parse_page(page)
    view = None
    problem: Optional[str] = None
    try:
        with readonly(settings.db_path) as conn:
            require_schema(conn)
            view = read_table(conn, name, number)
    except InputRefused as exc:
        problem = str(exc)
    except (PanelDbError, sqlite3.Error) as exc:
        problem = _db_message(exc)
    return render(
        request,
        "database/table.html",
        active="database",
        view=view,
        problem=problem,
        has_older=view is not None and view.page * PAGE_ROWS < view.total,
        has_newer=view is not None and view.page > 1,
    )


Work = Callable[[PanelSettings, int], tuple[Flash, Optional[list[str]]]]


def _respond(request: Request, work: Work) -> Response:
    """Run one action and answer with the refreshed page region and a one-line result."""
    settings: PanelSettings = request.app.state.settings
    integrity: Optional[list[str]] = None
    try:
        flash, integrity = work(settings, int(time.time()))
    except (InputRefused, PanelDbError) as exc:
        flash = Flash("error", str(exc))
    except (sqlite3.Error, OSError) as exc:
        logger.warning("A Database action failed: %s", exc)
        flash = Flash("error", _FAILED)
    return _body(request, flash, integrity)


@router.post("/integrity")
def integrity(request: Request) -> Response:
    def work(settings: PanelSettings, now: int) -> tuple[Flash, Optional[list[str]]]:
        with readonly(settings.db_path) as conn:
            require_schema(conn)
            lines = integrity_check(conn)
        if lines == ["ok"]:
            return Flash("ok", "Integrity check passed. SQLite found no problems."), None
        count = f"{len(lines)} problem" + ("" if len(lines) == 1 else "s")
        return Flash("error", f"The integrity check found {count}. Take a backup now."), lines

    return _respond(request, work)


@router.get("/vacuum/confirm")
def vacuum_confirm(request: Request) -> Response:
    return render_fragment(request, "database/_vacuum_confirm.html")


@router.post("/vacuum")
def vacuum_now(request: Request) -> Response:
    def work(settings: PanelSettings, now: int) -> tuple[Flash, Optional[list[str]]]:
        result = vacuum(settings.db_path, now=now)
        sizes = f"{format_bytes(result.before)} to {format_bytes(result.after)}"
        text = f"Compacted the database: {sizes}."
        return Flash("ok", text), None

    return _respond(request, work)


@router.post("/backup")
def backup(request: Request) -> Response:
    def work(settings: PanelSettings, now: int) -> tuple[Flash, Optional[list[str]]]:
        with maintenance_lock():
            made = create_backup(settings.db_path, settings.effective_backup_dir, now=now)
        return Flash("ok", f"Backup saved as {made.name} ({format_bytes(made.size)})."), None

    return _respond(request, work)


def _confirm(request: Request, name: str, action: str) -> Response:
    settings: PanelSettings = request.app.state.settings
    try:
        found: BackupFile = find_backup(settings.effective_backup_dir, name)
    except (InputRefused, PanelDbError) as exc:
        response = _body(request, Flash("error", str(exc)))
        response.headers["HX-Retarget"] = "#db-body"
        response.headers["HX-Reswap"] = "outerHTML"
        return response
    return render_fragment(request, "database/_backup_confirm.html", backup=found, action=action)


@router.get("/backups/{name}/delete/confirm")
def delete_confirm(request: Request, name: str) -> Response:
    return _confirm(request, name, "delete")


@router.get("/backups/{name}/restore/confirm")
def restore_confirm(request: Request, name: str) -> Response:
    return _confirm(request, name, "restore")


@router.post("/backups/{name}/delete")
def delete(request: Request, name: str) -> Response:
    def work(settings: PanelSettings, now: int) -> tuple[Flash, Optional[list[str]]]:
        with maintenance_lock():
            gone = delete_backup(settings.db_path, settings.effective_backup_dir, name, now=now)
        return Flash("ok", f"Deleted {gone.name}."), None

    return _respond(request, work)


@router.post("/backups/{name}/restore")
def restore(request: Request, name: str, confirm: str = Form("")) -> Response:
    def work(settings: PanelSettings, now: int) -> tuple[Flash, Optional[list[str]]]:
        result = restore_backup(settings, name, confirm, now=now)
        text = f"Restored {result.restored}."
        if result.safety:
            text += f" The database as it was is saved as {result.safety.name}."
        if not result.audit_saved:
            text += " The audit entry could not be saved."
        return Flash("ok", text), None

    return _respond(request, work)
