"""The admin audit log: every panel change is recorded in the same transaction as the change."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Optional

ACTOR = "panel"


@dataclass(frozen=True, slots=True)
class AuditEntry:
    """One recorded admin action."""

    id: int
    ts: int
    actor: str
    module: str
    action: str
    target: Optional[str]
    detail: dict[str, Any]
    reason: Optional[str]


def record(
    conn: sqlite3.Connection,
    *,
    module: str,
    action: str,
    target: Optional[str] = None,
    detail: Optional[dict[str, Any]] = None,
    reason: Optional[str] = None,
    now: Optional[int] = None,
    actor: str = ACTOR,
) -> int:
    """Insert an audit row on the caller's connection, so it commits or rolls back together."""
    ts = int(time.time()) if now is None else now
    payload = json.dumps(detail, sort_keys=True, ensure_ascii=False) if detail else None
    cursor = conn.execute(
        "INSERT INTO admin_audit (ts, actor, module, action, target, detail_json, reason) "
        "VALUES (?, ?, ?, ?, ?, ?, ?);",
        (ts, actor, module, action, target, payload, reason),
    )
    assert cursor.lastrowid is not None
    return int(cursor.lastrowid)


def _parse_detail(text: Optional[str]) -> dict[str, Any]:
    if not text:
        return {}
    try:
        value = json.loads(text)
    except ValueError:
        return {"raw": text}
    return value if isinstance(value, dict) else {"raw": text}


def list_entries(
    conn: sqlite3.Connection,
    *,
    module: Optional[str] = None,
    target: Optional[str] = None,
    limit: int = 25,
    offset: int = 0,
) -> tuple[list[AuditEntry], int]:
    """Newest-first page of audit entries plus the total number matching the filters."""
    clauses: list[str] = []
    params: list[Any] = []
    if module:
        clauses.append("module = ?")
        params.append(module)
    if target:
        clauses.append("target = ?")
        params.append(target)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""  # fixed fragments only
    total = int(conn.execute(f"SELECT COUNT(*) FROM admin_audit{where};", params).fetchone()[0])
    rows = conn.execute(
        "SELECT id, ts, actor, module, action, target, detail_json, reason FROM admin_audit"
        f"{where} ORDER BY id DESC LIMIT ? OFFSET ?;",
        [*params, limit, offset],
    ).fetchall()
    entries = [
        AuditEntry(
            id=int(r["id"]),
            ts=int(r["ts"]),
            actor=str(r["actor"]),
            module=str(r["module"]),
            action=str(r["action"]),
            target=r["target"],
            detail=_parse_detail(r["detail_json"]),
            reason=r["reason"],
        )
        for r in rows
    ]
    return entries, total


def modules_seen(conn: sqlite3.Connection) -> list[str]:
    """Module names that have written at least one audit entry, for the filter."""
    rows = conn.execute("SELECT DISTINCT module FROM admin_audit ORDER BY module;")
    return [str(r[0]) for r in rows]


def latest_ts(conn: sqlite3.Connection) -> Optional[int]:
    """Timestamp of the most recent audit entry, or None when the log is empty."""
    value = conn.execute("SELECT MAX(ts) FROM admin_audit;").fetchone()[0]
    return None if value is None else int(value)
