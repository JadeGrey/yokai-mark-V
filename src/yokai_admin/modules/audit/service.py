"""Audit page helpers: plain-language labels, paging and the view model."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Optional

from yokai_admin.core.audit import AuditEntry, list_entries, modules_seen
from yokai_admin.core.formatting import format_when
from yokai_admin.core.inputs import parse_page  # noqa: F401  (re-exported for the router)
from yokai_admin.core.people import UserRef, user_refs

PAGE_SIZE = 25
# Details a person reads in this order; anything else follows alphabetically.
_DETAIL_ORDER = ("before", "requested", "applied", "after")
_LABELS = {
    "blacklist.add": "Added to the blacklist",
    "blacklist.remove": "Removed from the blacklist",
    "admin.add": "Made an admin",
    "admin.remove": "Removed from the admin list",
    "balance.adjust": "Adjusted Renown",
    "daily.reset": "Reset the daily cooldown",
    "backup.create": "Created a backup",
    "backup.delete": "Deleted a backup",
    "vacuum": "Compacted the database",
    "restore": "Restored a backup",
}


def action_label(action: str) -> str:
    """Plain wording for known actions; anything else is shown exactly as recorded."""
    return _LABELS.get(action, action)


def _detail_value(key: str, value: Any) -> str:
    if key.endswith("_ts") and isinstance(value, int) and not isinstance(value, bool):
        return format_when(value)
    return str(value)


def detail_text(detail: dict[str, Any]) -> str:
    """Compact text for the interesting details (the name is already in the Target column)."""
    keys = [k for k in _DETAIL_ORDER if k in detail]
    keys += sorted(k for k in detail if k not in _DETAIL_ORDER)
    parts = [
        f"{key.removesuffix('_ts').replace('_', ' ')}: {_detail_value(key, detail[key])}"
        for key in keys
        if key != "name" and detail[key] is not None
    ]
    return ", ".join(parts)


@dataclass(frozen=True, slots=True)
class Row:
    """One audit entry ready to display."""

    entry: AuditEntry
    label: str
    person: Optional[UserRef]
    details: str


@dataclass(frozen=True, slots=True)
class AuditPage:
    """A page of rows plus what the template needs to draw the filters and pager."""

    rows: list[Row]
    total: int
    page: int
    has_older: bool
    has_newer: bool
    modules: list[str]


def _person_id(entry: AuditEntry) -> Optional[int]:
    """The user ID an access entry is about, when its target is one."""
    target = entry.target
    if entry.module in ("access", "users") and target and target.isascii() and target.isdigit():
        return int(target) if len(target) <= 19 else None
    return None


def build_page(conn: sqlite3.Connection, *, module: str, target: str, page: int) -> AuditPage:
    """Read one page of the audit log, resolving people's names in one query."""
    entries, total = list_entries(
        conn,
        module=module or None,
        target=target or None,
        limit=PAGE_SIZE,
        offset=(page - 1) * PAGE_SIZE,
    )
    ids = [pid for pid in (_person_id(e) for e in entries) if pid is not None]
    refs = user_refs(conn, ids)
    rows = []
    for entry in entries:
        pid = _person_id(entry)
        rows.append(
            Row(
                entry=entry,
                label=action_label(entry.action),
                person=refs.get(pid) if pid is not None else None,
                details=detail_text(entry.detail),
            )
        )
    return AuditPage(rows, total, page, page * PAGE_SIZE < total, page > 1, modules_seen(conn))
