"""Jinja setup shared by the shell and every module, plus the page-rendering helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Sequence

from fastapi import Request
from fastapi.responses import Response
from starlette.templating import Jinja2Templates

from yokai_admin.core.formatting import describe_age, format_number, format_signed, format_when
from yokai_admin.core.module import ModuleSlot

PACKAGE_DIR = Path(__file__).resolve().parent.parent
CORE_TEMPLATES = PACKAGE_DIR / "templates"
STATIC_DIR = PACKAGE_DIR / "static"


@dataclass(frozen=True, slots=True)
class NavItem:
    """One rail entry: a working module link, or a module that failed to load."""

    title: str
    href: str
    active: bool
    error: Optional[str]
    path: str


def build_templates(slots: Sequence[ModuleSlot]) -> Jinja2Templates:
    """Core templates plus each loaded module's own template directory (autoescape is on)."""
    directories: list[Path] = [CORE_TEMPLATES]
    for slot in slots:
        if slot.module is not None and slot.module.templates_dir is not None:
            directories.append(slot.module.templates_dir)
    templates = Jinja2Templates(directory=directories)
    templates.env.filters["when"] = format_when
    templates.env.filters["number"] = format_number
    templates.env.filters["signed"] = format_signed
    templates.env.filters["age"] = describe_age
    return templates


def _nav(slots: Sequence[ModuleSlot], active: str) -> list[NavItem]:
    items: list[NavItem] = []
    for slot in slots:
        if slot.module is None:
            items.append(NavItem("Module failed to load", "", False, slot.error, slot.path))
        else:
            module = slot.module
            href = f"/m/{module.id}"
            items.append(NavItem(module.title, href, module.id == active, None, slot.path))
    return items


def render(request: Request, name: str, *, active: str = "", **context: Any) -> Response:
    """Render a full page inside the shell. `active` is the module id to highlight."""
    state = request.app.state
    context = {
        "nav": _nav(state.slots, active),
        "csrf_token": request.state.session.csrf,
        **context,
    }
    return state.templates.TemplateResponse(request, name, context)


def render_fragment(request: Request, name: str, **context: Any) -> Response:
    """Render a template without the shell (for HTMX partials)."""
    return request.app.state.templates.TemplateResponse(request, name, context)
