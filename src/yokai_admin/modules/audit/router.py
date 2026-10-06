"""Audit routes: a read-only, filterable, paged view of the admin log."""

from __future__ import annotations

import logging
import sqlite3
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import Response

from yokai_admin.core.db import readonly, require_schema
from yokai_admin.core.templating import render
from yokai_admin.errors import PanelDbError
from yokai_admin.modules.audit.service import AuditPage, build_page, parse_page

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("")
def page(request: Request, module: str = "", target: str = "", page: str = "1") -> Response:
    settings = request.app.state.settings
    number = parse_page(page)
    view: Optional[AuditPage] = None
    db_error: Optional[str] = None
    try:
        with readonly(settings.db_path) as conn:
            require_schema(conn)
            view = build_page(conn, module=module.strip(), target=target.strip(), page=number)
    except PanelDbError as exc:
        db_error = str(exc)
    except sqlite3.Error as exc:
        logger.warning("Audit could not read the database: %s", exc)
        db_error = f"The database could not be read right now ({exc}). Reload in a moment."
    return render(
        request,
        "audit/page.html",
        active="audit",
        view=view,
        db_error=db_error,
        module=module.strip(),
        target=target.strip(),
        page_number=number,
    )
