"""Logs module: list, read, live-tail and download the bot's log files (read-only)."""

from __future__ import annotations

from pathlib import Path

from yokai_admin.core.formatting import format_bytes
from yokai_admin.core.module import AdminModule, Tile, TileContext
from yokai_admin.modules.logs.files import list_log_files
from yokai_admin.modules.logs.router import router


def logs_tile(ctx: TileContext) -> Tile:
    """Dashboard readout: how many log files there are and how big they are together."""
    files = list_log_files(ctx.settings.log_dir)
    if not files:
        return Tile(title="Logs", value="no log files yet")
    total = sum(f.size for f in files)
    noun = "file" if len(files) == 1 else "files"
    return Tile(title="Logs", value=f"{len(files)} {noun}, {format_bytes(total)}")


MODULE = AdminModule(
    id="logs",
    title="Logs",
    router=router,
    nav_order=7,
    templates_dir=Path(__file__).parent / "templates",
    tile=logs_tile,
)
