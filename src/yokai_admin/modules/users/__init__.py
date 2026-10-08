"""People module: who the bot knows, their wallets, and the two money corrections."""

from __future__ import annotations

from pathlib import Path

from yokai_admin.core.db import readonly
from yokai_admin.core.module import AdminModule, Tile, TileContext
from yokai_admin.modules.users.router import router, users_counts


def users_tile(ctx: TileContext) -> Tile:
    """Dashboard readout: how many people the bot knows."""
    with readonly(ctx.settings.db_path) as conn:
        known, wallets = users_counts(conn)
    return Tile(title="People", value=f"{known} known, {wallets} with wallets")


MODULE = AdminModule(
    id="users",
    title="People",
    router=router,
    nav_order=5,
    templates_dir=Path(__file__).parent / "templates",
    tile=users_tile,
)
