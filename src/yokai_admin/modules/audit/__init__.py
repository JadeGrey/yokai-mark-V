"""Audit module: a read-only view of everything changed from the panel."""

from __future__ import annotations

from pathlib import Path

from yokai_admin.core.audit import latest_ts
from yokai_admin.core.db import readonly
from yokai_admin.core.formatting import describe_age
from yokai_admin.core.module import AdminModule, Tile, TileContext
from yokai_admin.modules.audit.router import router


def audit_tile(ctx: TileContext) -> Tile:
    """Dashboard readout: how long ago the panel last changed something."""
    with readonly(ctx.settings.db_path) as conn:
        last = latest_ts(conn)
    if last is None:
        return Tile(title="Last admin action", value="None yet")
    return Tile(title="Last admin action", value=f"{describe_age(max(0, ctx.now - last))} ago")


MODULE = AdminModule(
    id="audit",
    title="Audit log",
    router=router,
    nav_order=20,
    templates_dir=Path(__file__).parent / "templates",
    tile=audit_tile,
)
