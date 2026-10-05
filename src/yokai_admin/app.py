"""Application factory: wires modules, templates, static files and security together."""

from __future__ import annotations

from typing import Sequence

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from starlette.staticfiles import StaticFiles

from yokai_admin.core.auth import AuthState
from yokai_admin.core.module import load_modules
from yokai_admin.core.security import install_security
from yokai_admin.core.settings import PanelSettings
from yokai_admin.core.templating import STATIC_DIR, build_templates, render


def create_app(
    settings: PanelSettings,
    module_paths: Sequence[str],
    auth: AuthState,
) -> FastAPI:
    """Build the panel. Duplicate module ids raise `ModuleConfigError`; other module failures
    are shown in the nav and never stop the app."""
    slots = load_modules(module_paths)

    app = FastAPI(title="Yokai admin", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.slots = slots
    app.state.templates = build_templates(slots)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    for slot in slots:
        if slot.module is not None:
            app.include_router(slot.module.router, prefix=f"/m/{slot.module.id}")

    first = next((slot.module for slot in slots if slot.module is not None), None)

    @app.get("/", include_in_schema=False)
    def home(request: Request) -> Response:
        if first is None:
            return render(request, "no_modules.html")
        return RedirectResponse(f"/m/{first.id}", status_code=303)

    install_security(app, auth, settings.port)
    return app
