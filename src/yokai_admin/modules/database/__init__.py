"""Database module: table overview and browser, integrity, backups, VACUUM and restore."""

from __future__ import annotations

from pathlib import Path

from yokai_admin.core.formatting import describe_age, format_bytes
from yokai_admin.core.module import AdminModule, Tile, TileContext
from yokai_admin.modules.database.backups import list_backups
from yokai_admin.modules.database.router import router


def database_tile(ctx: TileContext) -> Tile:
    """Dashboard readout: file size and how long ago the newest backup was made."""
    size = ctx.settings.db_path.stat().st_size
    newest = [b for b in list_backups(ctx.settings.effective_backup_dir) if not b.safety][:1]
    if newest:
        age = describe_age(max(0, ctx.now - newest[0].modified_ts))
        return Tile(title="Database", value=f"{format_bytes(size)}, last backup {age} ago")
    return Tile(
        title="Database",
        value=f"{format_bytes(size)}, no backups yet",
        status="warn",
    )


MODULE = AdminModule(
    id="database",
    title="Database",
    router=router,
    nav_order=6,
    templates_dir=Path(__file__).parent / "templates",
    tile=database_tile,
)
