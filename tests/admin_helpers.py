"""Shared fixtures for the admin panel tests: sample modules, a seeded database, a client."""

from __future__ import annotations

from fastapi import APIRouter

from yokai_admin.core.module import AdminModule, Tile, TileContext


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
