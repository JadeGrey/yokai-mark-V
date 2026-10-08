"""Shared fixtures for the admin panel tests: sample modules, a seeded database, a client."""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Mapping, Optional, Sequence

from fastapi import APIRouter, Request
from fastapi.responses import Response
from httpx import Response as HttpxResponse
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
    owner_id: Optional[int] = 111,
) -> Iterator[tuple[TestClient, str]]:
    """Run the real app in a TestClient; yields (client, csrf_token) after the token exchange."""
    settings = PanelSettings(
        db_path=db_path or tmp_path / "yokai.db", log_dir=tmp_path / "logs", owner_id=owner_id
    )
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
    admins: Sequence[int] = (),
    blacklist: Sequence[tuple[int, str]] = (),
    names: Optional[Mapping[int, str]] = None,
    credits: Optional[Mapping[int, int]] = None,
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
            for user_id, amount in (credits or {}).items():
                await db.credit_wallet(user_id, amount, reason="daily", welcome_bonus=200)
            for ts in commands:
                await db.conn.execute(
                    "INSERT INTO command_log (ts, user_id, command) VALUES (?, 1, 'play');", (ts,)
                )
            for user_id in admins:
                await db.conn.execute(
                    "INSERT INTO admins (user_id, added_ts, added_by) VALUES (?, 1, 'test');",
                    (user_id,),
                )
            for user_id, reason in blacklist:
                await db.conn.execute(
                    "INSERT INTO blacklist (user_id, reason, added_ts, added_by) "
                    "VALUES (?, ?, 1, 'test');",
                    (user_id, reason),
                )
            await db.conn.commit()
            for user_id, name in (names or {}).items():
                await db.upsert_user_name(user_id, name, now=1)
        finally:
            await db.close()

    asyncio.run(run())


def post(client: TestClient, csrf: str, path: str, **form: str) -> HttpxResponse:
    """POST form data the way HTMX does: CSRF header plus HX-Request."""
    return client.post(path, data=form, headers={"X-CSRF-Token": csrf, "HX-Request": "true"})


def seed_activity(
    path: Path,
    *,
    plays: Sequence[tuple[int, str, str, Optional[str], int]] = (),
    bets: Sequence[tuple[int, str, int, int, str, Optional[str]]] = (),
    user_commands: Sequence[tuple[int, str, int]] = (),
    transactions: Sequence[tuple[int, int, str, Optional[int], int, int]] = (),
    daily: Optional[Mapping[int, tuple[Optional[int], int]]] = None,
) -> None:
    """Insert raw activity rows: plays (user, video_id, title, artist, ts), bets (user, game,
    stake, payout, status, group_id), commands (user, command, ts), ledger rows (user, delta,
    reason, counterparty, balance_after, ts) and daily state ({user: (last_daily_ts, streak)})."""
    conn = sqlite3.connect(path)
    try:
        for user_id, video_id, title, artist, ts in plays:
            conn.execute(
                "INSERT INTO play_events (ts, user_id, video_id, title, artist, duration_s, "
                "origin, outcome, listened_s) "
                "VALUES (?, ?, ?, ?, ?, 180, 'search', 'completed', 180);",
                (ts, user_id, video_id, title, artist),
            )
        for user_id, game, stake, payout, status, group_id in bets:
            conn.execute(
                "INSERT INTO gambling_bets (ts, user_id, game, stake, status, payout, group_id) "
                "VALUES (1, ?, ?, ?, ?, ?, ?);",
                (user_id, game, stake, status, payout, group_id),
            )
        for user_id, command, ts in user_commands:
            conn.execute(
                "INSERT INTO command_log (ts, user_id, command) VALUES (?, ?, ?);",
                (ts, user_id, command),
            )
        for user_id, delta, reason, counterparty, after, ts in transactions:
            conn.execute(
                "INSERT INTO renown_transactions (ts, user_id, delta, reason, counterparty_id, "
                "balance_after) VALUES (?, ?, ?, ?, ?, ?);",
                (ts, user_id, delta, reason, counterparty, after),
            )
        for user_id, (last_daily_ts, streak) in (daily or {}).items():
            conn.execute(
                "UPDATE wallets SET last_daily_ts = ?, daily_streak = ? WHERE user_id = ?;",
                (last_daily_ts, streak, user_id),
            )
        conn.commit()
    finally:
        conn.close()
