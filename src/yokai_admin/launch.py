"""Starting the panel: build the server and the single-use launch URL, then serve on loopback."""

from __future__ import annotations

import logging
import webbrowser
from typing import Callable, Optional

import uvicorn

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


class _AnnouncingServer(uvicorn.Server):
    """A uvicorn server that calls `announce` only once the port is bound.

    uvicorn runs the ASGI lifespan startup *before* it binds the socket, so announcing from a
    lifespan hook would open the browser even when the port turns out to be taken.
    """

    def __init__(self, config: uvicorn.Config, announce: Callable[[], None]) -> None:
        super().__init__(config)
        self._announce = announce

    async def startup(self, sockets: Optional[list] = None) -> None:  # type: ignore[type-arg]
        await super().startup(sockets=sockets)  # exits the process (SystemExit) if the bind fails
        if self.started and not self.should_exit:
            self._announce()


def build_server(
    settings: PanelSettings,
    *,
    opener: Optional[Callable[[str], object]] = webbrowser.open,
) -> tuple[uvicorn.Server, str]:
    """Create the server and its single-use launch URL. Nothing is opened or logged until the
    server has bound its port; then the URL is printed to the console and passed to `opener`."""
    auth = AuthState()
    url = auth_url(settings.port, auth.token)

    def announce() -> None:
        # Console only: the root logger of `python -m yokai_admin` has no file handler.
        logger.info("Admin panel ready. This link works once: %s", url)
        if opener is not None:
            opener(url)

    config = uvicorn.Config(
        create_app(settings, MODULE_PATHS, auth),
        host=HOST,
        port=settings.port,
        log_config=None,  # keep our own logging setup
        log_level="warning",
        access_log=False,  # request lines would include the launch token
    )
    return _AnnouncingServer(config, announce), url


def run(
    settings: PanelSettings,
    *,
    opener: Optional[Callable[[str], object]] = webbrowser.open,
) -> int:
    """Serve the panel until stopped. Returns 0 on a normal stop, 1 when the port is unusable."""
    server, _ = build_server(settings, opener=opener)
    try:
        server.run()
    except KeyboardInterrupt:
        logger.info("Admin panel stopped.")
        return 0
    except SystemExit:
        # uvicorn has already logged the operating system's reason, then exits with code 1.
        logger.error(
            "Could not listen on %s:%d. Set ADMIN_PORT to a free port in .env.",
            HOST,
            settings.port,
        )
        return 1
    except OSError as exc:
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
    logger.info("Admin panel stopped.")
    return 0
