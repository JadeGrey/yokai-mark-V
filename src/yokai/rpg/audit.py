"""Records admin RPG actions in the shared admin audit log, inside the caller's transaction."""

from __future__ import annotations

import json
import time
from typing import Any, Optional

import aiosqlite


async def record_admin_action(
    conn: aiosqlite.Connection,
    actor_id: int,
    action: str,
    target_id: Optional[int],
    detail: dict[str, Any],
    *,
    now: Optional[int] = None,
) -> None:
    """Add a row to admin_audit (module "rpg"). Call it inside `Database.transaction()`."""
    await conn.execute(
        "INSERT INTO admin_audit (ts, actor, module, action, target, detail_json, reason) "
        "VALUES (?, ?, 'rpg', ?, ?, ?, NULL);",
        (
            int(time.time()) if now is None else now,
            str(actor_id),
            action,
            None if target_id is None else str(target_id),
            json.dumps(detail, sort_keys=True),
        ),
    )
