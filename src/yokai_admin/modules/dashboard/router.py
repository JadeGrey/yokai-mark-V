"""Dashboard routes: the full page and the HTMX status fragment."""

from __future__ import annotations

import logging
import sqlite3
import time
from typing import Any, Optional

from fastapi import APIRouter, Request
from fastapi.responses import Response

from yokai_admin.core.db import readonly, require_schema
from yokai_admin.core.module import TileContext, render_tiles
from yokai_admin.core.templating import render, render_fragment
from yokai_admin.errors import PanelDbError
from yokai_admin.modules.dashboard.service import (
    BotStatus,
    Overview,
    bot_status,
    format_bytes,
    overview,
    recent_errors,
    status_note,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _snapshot(request: Request) -> dict[str, Any]:
    settings = request.app.state.settings
    now = int(time.time())
    status: Optional[BotStatus] = None
    stats: Optional[Overview] = None
    db_error: Optional[str] = None
    try:
        with readonly(settings.db_path) as conn:
            require_schema(conn)
            status = bot_status(conn, now)
            stats = overview(conn, settings.db_path, now)
    except PanelDbError as exc:
        db_error = str(exc)
    except sqlite3.Error as exc:
        logger.warning("Dashboard could not read the database: %s", exc)
        db_error = f"The database could not be read right now ({exc}). It will retry shortly."

    slots = request.app.state.slots
    return {
        "status": status,
        "stats": stats,
        "db_size": format_bytes(stats.db_size_bytes) if stats else "",
        "note": status_note(status, db_error),
        "errors": recent_errors(settings.log_dir),
        "tiles": render_tiles(slots, TileContext(settings=settings, now=now)),
    }


@router.get("")
def page(request: Request) -> Response:
    load_errors = [slot for slot in request.app.state.slots if slot.error]
    return render(
        request,
        "dashboard/page.html",
        active="dashboard",
        load_errors=load_errors,
        **_snapshot(request),
    )


@router.get("/status")
def status_fragment(request: Request) -> Response:
    return render_fragment(request, "dashboard/_status.html", **_snapshot(request))
