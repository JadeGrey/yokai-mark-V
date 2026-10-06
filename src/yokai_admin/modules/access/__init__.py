"""Access module: the blacklist and the admin list."""

from __future__ import annotations

from pathlib import Path

from yokai_admin.core.db import readonly
from yokai_admin.core.module import AdminModule, Tile, TileContext
from yokai_admin.modules.access.router import router


def access_tile(ctx: TileContext) -> Tile:
    """Dashboard readout: how many people are blacklisted and how many are admins."""
    with readonly(ctx.settings.db_path) as conn:
        blocked = int(conn.execute("SELECT COUNT(*) FROM blacklist;").fetchone()[0])
        admins = int(conn.execute("SELECT COUNT(*) FROM admins;").fetchone()[0])
    noun = "admin" if admins == 1 else "admins"
    return Tile(title="Access lists", value=f"{blocked} blacklisted, {admins} {noun}")


MODULE = AdminModule(
    id="access",
    title="Access",
    router=router,
    nav_order=10,
    templates_dir=Path(__file__).parent / "templates",
    tile=access_tile,
)
