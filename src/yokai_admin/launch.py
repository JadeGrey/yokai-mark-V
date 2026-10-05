"""Starting the panel: build the app and the single-use launch URL, then serve on loopback."""

from __future__ import annotations

import logging
import webbrowser
from typing import Callable

import uvicorn
from fastapi import FastAPI

from yokai_admin.app import create_app
from yokai_admin.core.auth import AuthState
from yokai_admin.core.db import readonly, require_schema
from yokai_admin.core.settings import HOST, PanelSettings
from yokai_admin.modules import MODULE_PATHS

logger = logging.getLogger("yokai_admin")


def auth_url(port: int, token: str) -> str:
    """The link that logs the first browser in. The token works exactly once."""
    return f"http://{HOST}:{port}/auth?token={token}"


def preflight(settings: PanelSettings) -> None:
    """Fail early, with a clear reason, when the bot's database is missing or too old."""
    with readonly(settings.db_path) as conn:
        require_schema(conn)


def build_panel(
    settings: PanelSettings, *, opener: Callable[[str], object] = webbrowser.open
) -> tuple[FastAPI, str]:
    """Create the app and its launch URL; `opener` is called once when the server starts."""
    auth = AuthState()
    url = auth_url(settings.port, auth.token)

    def announce() -> None:
        # Console only: the root logger of `python -m yokai_admin` has no file handler.
        logger.info("Admin panel ready. This link works once: %s", url)
        opener(url)

    return create_app(settings, MODULE_PATHS, auth, on_startup=announce), url


def run(settings: PanelSettings, *, open_browser: bool = True) -> int:
    """Serve the panel until stopped. Returns 1 when the port cannot be used."""
    opener: Callable[[str], object] = webbrowser.open if open_browser else (lambda _url: None)
    app, _ = build_panel(settings, opener=opener)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=HOST,
            port=settings.port,
            log_config=None,  # keep our own logging setup
            log_level="warning",
            access_log=False,  # request lines would include the launch token
        )
    )
    try:
        server.run()
    # uvicorn logs a bind failure and then calls sys.exit(1); an OSError can also escape.
    except (OSError, SystemExit) as exc:
        logger.error(
            "Could not listen on %s:%d (%s). Set ADMIN_PORT to a free port in .env.",
            HOST,
            settings.port,
            exc,
        )
        return 1
    if not server.started:
        logger.error(
            "The panel did not start on %s:%d. Set ADMIN_PORT to a free port in .env.",
            HOST,
            settings.port,
        )
        return 1
    return 0
