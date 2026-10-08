"""People routes: the list, a person's profile, and the two money actions behind a confirm step."""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from fastapi import APIRouter, Form, Request
from fastapi.responses import Response

from yokai_admin.core.db import readonly, require_schema
from yokai_admin.core.inputs import InputRefused, clean_reason, parse_page, parse_user_id
from yokai_admin.core.settings import PanelSettings
from yokai_admin.core.templating import render, render_fragment
from yokai_admin.errors import PanelDbError
from yokai_admin.modules.users.actions import (
    AdjustPreview,
    adjust_balance,
    parse_amount,
    preview_adjustment,
    reset_daily,
)
from yokai_admin.modules.users.service import (
    PAGE_SIZE,
    Person,
    Profile,
    list_people,
    load_profile,
)

logger = logging.getLogger(__name__)
router = APIRouter()
_SAVE_FAILED = "The database could not be changed right now. Nothing was saved."
_NOT_KNOWN = "No one with that ID is known to the bot yet."
_MAX_QUERY = 80


@dataclass(frozen=True, slots=True)
class Flash:
    """A one-line result shown above the profile."""

    kind: str  # "ok" or "error"
    text: str


def _clean_query(raw: str) -> str:
    return " ".join(raw.split())[:_MAX_QUERY]


def _db_message(exc: Exception) -> str:
    if isinstance(exc, PanelDbError):
        return str(exc)
    logger.warning("People could not read the database: %s", exc)
    return f"The database could not be read right now ({exc}). It will retry shortly."


@router.get("")
def people(request: Request, q: str = "", page: str = "1") -> Response:
    settings: PanelSettings = request.app.state.settings
    query, number = _clean_query(q), parse_page(page)
    rows: list[Person] = []
    total = 0
    db_error: Optional[str] = None
    try:
        with readonly(settings.db_path) as conn:
            require_schema(conn)
            rows, total = list_people(
                conn, query=query, limit=PAGE_SIZE, offset=(number - 1) * PAGE_SIZE
            )
    except (PanelDbError, sqlite3.Error) as exc:
        db_error = _db_message(exc)
    return render(
        request,
        "users/page.html",
        active="users",
        rows=rows,
        total=total,
        query=query,
        page_number=number,
        has_older=number * PAGE_SIZE < total,
        has_newer=number > 1,
        db_error=db_error,
        profile_view=None,
    )


def _profile_context(
    request: Request, raw_id: str, flash: Optional[Flash], **extra: Any
) -> dict[str, Any]:
    settings: PanelSettings = request.app.state.settings
    profile: Optional[Profile] = None
    problem: Optional[str] = None
    user_id: Optional[int] = None
    try:
        user_id = parse_user_id(raw_id)
        with readonly(settings.db_path) as conn:
            require_schema(conn)
            profile = load_profile(conn, user_id, int(time.time()))
        if profile is None:
            problem = _NOT_KNOWN
    except InputRefused:
        problem = _NOT_KNOWN
    except (PanelDbError, sqlite3.Error) as exc:
        problem = _db_message(exc)
    return {"profile": profile, "problem": problem, "flash": flash, "user_id": user_id, **extra}


def _person(request: Request, raw_id: str, flash: Optional[Flash] = None) -> Response:
    context = _profile_context(request, raw_id, flash)
    return render_fragment(request, "users/_person.html", **context)


@router.get("/{user_id}")
def person_page(request: Request, user_id: str) -> Response:
    context = _profile_context(request, user_id, None)
    return render(request, "users/page.html", active="users", profile_view=True, **context)


@router.get("/{user_id}/adjust/form")
def adjust_form(request: Request, user_id: str) -> Response:
    return render_fragment(request, "users/_adjust_form.html", user_id=user_id, form={})


@router.post("/{user_id}/adjust/preview")
def adjust_preview(
    request: Request, user_id: str, amount: str = Form(""), reason: str = Form("")
) -> Response:
    settings: PanelSettings = request.app.state.settings
    form = {"amount": amount, "reason": reason}
    try:
        uid = parse_user_id(user_id)
        value = parse_amount(amount)
        clean = clean_reason(reason, required=True)
        with readonly(settings.db_path) as conn:
            require_schema(conn)
            preview: AdjustPreview = preview_adjustment(conn, uid, value)
    except (InputRefused, PanelDbError, sqlite3.Error) as exc:
        message = str(exc) if isinstance(exc, (InputRefused, PanelDbError)) else _SAVE_FAILED
        return render_fragment(
            request,
            "users/_adjust_form.html",
            user_id=user_id,
            form=form,
            form_flash=Flash("error", message),
        )
    return render_fragment(
        request, "users/_adjust_confirm.html", user_id=uid, preview=preview, reason=clean
    )


def _act(request: Request, raw_id: str, action: Callable[[PanelSettings], str]) -> Response:
    """Run one write; answer with the refreshed profile and a one-line result."""
    settings: PanelSettings = request.app.state.settings
    try:
        message = action(settings)
    except (InputRefused, PanelDbError) as exc:
        return _person(request, raw_id, Flash("error", str(exc)))
    except sqlite3.Error as exc:
        logger.warning("People write failed: %s", exc)
        return _person(request, raw_id, Flash("error", _SAVE_FAILED))
    return _person(request, raw_id, Flash("ok", message))


@router.post("/{user_id}/adjust")
def adjust(
    request: Request,
    user_id: str,
    amount: str = Form(""),
    reason: str = Form(""),
    expected: str = Form(""),
) -> Response:
    def run(settings: PanelSettings) -> str:
        stamp = expected.strip()
        if not (stamp.isascii() and stamp.isdigit()) or len(stamp) > 19:
            raise InputRefused("That confirmation is out of date. Preview the change again.")
        result = adjust_balance(
            settings.db_path,
            raw_id=user_id,
            raw_amount=amount,
            raw_reason=reason,
            expected_balance=int(stamp),
        )
        note = " (the balance cannot go below 0)" if result.applied != result.requested else ""
        return (
            f"{result.user.label}: {result.before:,} to {result.after:,} Renown "
            f"({result.applied:+,}){note}."
        )

    return _act(request, user_id, run)


@router.get("/{user_id}/daily/button")
def daily_button(request: Request, user_id: str) -> Response:
    return render_fragment(request, "users/_daily_button.html", user_id=user_id)


@router.get("/{user_id}/daily/confirm")
def daily_confirm(request: Request, user_id: str) -> Response:
    context = _profile_context(request, user_id, None)
    profile: Optional[Profile] = context["profile"]
    if profile is None or profile.wallet is None:
        # Not a real confirm: answer with the whole section, redirected to #person.
        response = render_fragment(request, "users/_person.html", **context)
        response.headers["HX-Retarget"] = "#person"
        response.headers["HX-Reswap"] = "outerHTML"
        return response
    return render_fragment(request, "users/_daily_confirm.html", **context)


@router.post("/{user_id}/daily/reset")
def daily_reset(request: Request, user_id: str) -> Response:
    def run(settings: PanelSettings) -> str:
        result = reset_daily(settings.db_path, raw_id=user_id)
        return (
            f"{result.user.label} can claim their daily reward now. "
            f"Their streak of {result.streak} is kept."
        )

    return _act(request, user_id, run)


def users_counts(conn: sqlite3.Connection) -> tuple[int, int]:
    """(people known, people with wallets) for the Dashboard readout."""
    sql = "SELECT COUNT(*) FROM (SELECT user_id FROM wallets UNION SELECT user_id FROM user_names);"
    known = int(conn.execute(sql).fetchone()[0])
    wallets = int(conn.execute("SELECT COUNT(*) FROM wallets;").fetchone()[0])
    return known, wallets
