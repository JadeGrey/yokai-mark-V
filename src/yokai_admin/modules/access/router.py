"""Access routes: blacklist and admin list, edited through HTMX forms."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from typing import Any, Callable, Optional

from fastapi import APIRouter, Form, Request
from fastapi.responses import Response

from yokai_admin.core.db import readonly, require_schema
from yokai_admin.core.people import UserRef
from yokai_admin.core.settings import PanelSettings
from yokai_admin.core.templating import render, render_fragment
from yokai_admin.errors import PanelDbError
from yokai_admin.modules.access.service import (
    AccessRefused,
    Entry,
    add_admin,
    add_blacklist,
    list_admins,
    list_blacklist,
    parse_user_id,
    remove_admin,
    remove_blacklist,
)

logger = logging.getLogger(__name__)
router = APIRouter()
EDITING_OFF = (
    "Editing is off. Set OWNER_ID in .env and restart the panel, so the owner can be protected."
)
_SAVE_FAILED = "The database could not be changed right now. Nothing was saved."


@dataclass(frozen=True, slots=True)
class Flash:
    """A one-line result shown above the lists."""

    kind: str  # "ok" or "error"
    text: str


def _context(request: Request, flash: Optional[Flash]) -> dict[str, Any]:
    settings: PanelSettings = request.app.state.settings
    blacklist: list[Entry] = []
    admins: list[Entry] = []
    db_error: Optional[str] = None
    try:
        with readonly(settings.db_path) as conn:
            require_schema(conn)
            blacklist, admins = list_blacklist(conn), list_admins(conn)
    except PanelDbError as exc:
        db_error = str(exc)
    except sqlite3.Error as exc:
        logger.warning("Access could not read the database: %s", exc)
        db_error = f"The database could not be read right now ({exc}). It will retry shortly."
    return {
        "flash": flash,
        "db_error": db_error,
        "blacklist": blacklist,
        "admins": admins,
        "can_edit": settings.owner_id is not None,
    }


def _section(
    request: Request, flash: Optional[Flash] = None, context: Optional[dict[str, Any]] = None
) -> Response:
    data = {**(context if context is not None else _context(request, None)), "flash": flash}
    return render_fragment(request, "access/_lists.html", **data)


def _whole_section(response: Response) -> Response:
    """Make htmx replace the whole #access section even though the request targeted one row."""
    response.headers["HX-Retarget"] = "#access"
    response.headers["HX-Reswap"] = "outerHTML"
    return response


def _mutate(
    request: Request,
    action: Callable[[PanelSettings, int], UserRef],
    success: str,
) -> Response:
    """Run one write and answer with the refreshed section.

    Problems come back as HTTP 200 with an error line, because HTMX does not swap 4xx/5xx.
    """
    settings: PanelSettings = request.app.state.settings
    if settings.owner_id is None:
        return _section(request, Flash("error", EDITING_OFF))
    try:
        ref = action(settings, settings.owner_id)
    except (AccessRefused, PanelDbError) as exc:
        return _section(request, Flash("error", str(exc)))
    except sqlite3.Error as exc:
        logger.warning("Access write failed: %s", exc)
        return _section(request, Flash("error", _SAVE_FAILED))
    return _section(request, Flash("ok", success.replace("{who}", ref.label)))


def _confirm(request: Request, kind: str, raw_id: str) -> Response:
    """The inline 'Remove X?' row. Anything that is not a real row answers with the refreshed
    section instead, redirected to #access so htmx does not nest it inside the table."""
    context = _context(request, None)
    if not context["can_edit"]:
        return _whole_section(_section(request, Flash("error", EDITING_OFF), context))
    try:
        user_id = parse_user_id(raw_id)
    except AccessRefused as exc:
        return _whole_section(_section(request, Flash("error", str(exc)), context))
    entries: list[Entry] = context["blacklist"] if kind == "blacklist" else context["admins"]
    match = next((e for e in entries if e.user.id == user_id), None)
    if match is None:
        verb = "on the blacklist" if kind == "blacklist" else "an admin"
        return _whole_section(
            _section(request, Flash("error", f"That person is not {verb}."), context)
        )
    return render_fragment(
        request,
        "access/_confirm.html",
        entry=match,
        kind=kind,
        list_name="blacklist" if kind == "blacklist" else "admin list",
        columns=4 if kind == "blacklist" else 3,  # the table the row is swapped into
    )


@router.get("")
def page(request: Request) -> Response:
    return render(request, "access/page.html", active="access", **_context(request, None))


@router.get("/lists")
def lists(request: Request) -> Response:
    return _section(request)


@router.post("/blacklist")
def blacklist_add(request: Request, user_id: str = Form(""), reason: str = Form("")) -> Response:
    return _mutate(
        request,
        lambda s, owner: add_blacklist(
            s.db_path, owner_id=owner, raw_id=user_id, raw_reason=reason
        ),
        "{who} is blacklisted. The bot picks this up within a few seconds.",
    )


@router.get("/blacklist/{user_id}/confirm")
def blacklist_confirm(request: Request, user_id: str) -> Response:
    return _confirm(request, "blacklist", user_id)


@router.post("/blacklist/{user_id}/remove")
def blacklist_remove(request: Request, user_id: str) -> Response:
    return _mutate(
        request,
        lambda s, owner: remove_blacklist(s.db_path, raw_id=user_id),
        "{who} was removed from the blacklist. The bot picks this up within a few seconds.",
    )


@router.post("/admins")
def admin_add(request: Request, user_id: str = Form(""), note: str = Form("")) -> Response:
    return _mutate(
        request,
        lambda s, owner: add_admin(s.db_path, owner_id=owner, raw_id=user_id, raw_note=note),
        "{who} is now an admin. The bot picks this up within a few seconds.",
    )


@router.get("/admins/{user_id}/confirm")
def admin_confirm(request: Request, user_id: str) -> Response:
    return _confirm(request, "admins", user_id)


@router.post("/admins/{user_id}/remove")
def admin_remove(request: Request, user_id: str) -> Response:
    return _mutate(
        request,
        lambda s, owner: remove_admin(s.db_path, raw_id=user_id),
        "{who} was removed from the admin list. The bot picks this up within a few seconds.",
    )
