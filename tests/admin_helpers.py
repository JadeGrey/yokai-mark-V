"""Shared fixtures for the admin panel tests: sample modules, a seeded database, a client."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional, Sequence

from fastapi import APIRouter, Request
from fastapi.responses import Response
from starlette.testclient import TestClient

from yokai.storage.db import Database
from yokai_admin.app import create_app
from yokai_admin.core.auth import SESSION_COOKIE, AuthState
from yokai_admin.core.module import AdminModule, Tile, TileContext
from yokai_admin.core.settings import PanelSettings
from yokai_admin.core.templating import render


def _router(label: str) -> APIRouter:
    router = APIRouter()

    @router.get("")
    def index() -> dict[str, str]:
        return {"module": label}

    return router


def ok_tile(ctx: TileContext) -> Tile:
    return Tile(title="Queue", value="3 tracks", status="ok")


def failing_tile(ctx: TileContext) -> Tile:
    raise RuntimeError("tile exploded")


ALPHA = AdminModule(id="alpha", title="Alpha", router=_router("alpha"), nav_order=20, tile=ok_tile)
BETA = AdminModule(id="beta", title="Beta", router=_router("beta"), nav_order=10)
ALPHA_AGAIN = AdminModule(id="alpha", title="Alpha again", router=_router("alpha-again"))
BROKEN_TILE = AdminModule(
    id="broken-tile", title="Broken tile", router=_router("broken"), tile=failing_tile
)
NOT_A_MODULE = "just a string, not an AdminModule"

_demo_router = APIRouter()


@_demo_router.get("")
def _demo_page(request: Request) -> Response:
    return render(
        request, "demo/page.html", active="demo", message=request.query_params.get("m", "")
    )


DEMO = AdminModule(
    id="demo",
    title="Demo",
    router=_demo_router,
    nav_order=5,
    templates_dir=Path(__file__).parent / "admin_templates",
)


@contextmanager
def panel_client(
    tmp_path: Path,
    module_paths: Sequence[str],
    *,
    db_path: Optional[Path] = None,
    token: str = "t0ken",
    login: bool = True,
) -> Iterator[tuple[TestClient, str]]:
    """Run the real app in a TestClient; yields (client, csrf_token) after the token exchange."""
    settings = PanelSettings(db_path=db_path or tmp_path / "yokai.db", log_dir=tmp_path / "logs")
    auth = AuthState(token=token)
    app = create_app(settings, module_paths, auth)
    with TestClient(app, base_url=f"http://127.0.0.1:{settings.port}") as client:
        csrf = ""
        if login:
            response = client.get(f"/auth?token={token}", follow_redirects=False)
            assert response.status_code == 303
            session = auth.lookup(client.cookies.get(SESSION_COOKIE))
            assert session is not None
            csrf = session.csrf
        yield client, csrf


def seed_database(
    path: Path,
    *,
    heartbeat_ts: Optional[int] = None,
    wallets: int = 0,
    commands: Sequence[int] = (),
) -> None:
    """Create a real current-schema database with the given heartbeat, wallets and commands."""

    async def run() -> None:
        db = Database(path)
        await db.initialize()
        try:
            if heartbeat_ts is not None:
                await db.write_heartbeat(pid=4242, version="0.1.0", now=heartbeat_ts)
            for user_id in range(1, wallets + 1):
                await db.credit_wallet(user_id, 10, reason="daily", welcome_bonus=200)
            for ts in commands:
                await db.conn.execute(
                    "INSERT INTO command_log (ts, user_id, command) VALUES (?, 1, 'play');", (ts,)
                )
            await db.conn.commit()
        finally:
            await db.close()

    asyncio.run(run())
