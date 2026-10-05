"""Dashboard module: bot status and vital signs. Home page of the panel."""

from __future__ import annotations

from pathlib import Path

from yokai_admin.core.module import AdminModule
from yokai_admin.modules.dashboard.router import router

MODULE = AdminModule(
    id="dashboard",
    title="Dashboard",
    router=router,
    nav_order=0,
    templates_dir=Path(__file__).parent / "templates",
)
