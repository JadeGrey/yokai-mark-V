"""Audit page helpers: plain-language labels, paging and the view model."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Optional

from yokai_admin.core.audit import AuditEntry, list_entries, modules_seen
from yokai_admin.core.people import UserRef, user_refs

PAGE_SIZE = 25
_LABELS = {
    "blacklist.add": "Added to the blacklist",
    "blacklist.remove": "Removed from the blacklist",
    "admin.add": "Made an admin",
    "admin.remove": "Removed from the admin list",
}


def action_label(action: str) -> str:
    """Plain wording for known actions; anything else is shown exactly as recorded."""
    return _LABELS.get(action, action)


def detail_text(detail: dict[str, Any]) -> str:
    """Compact text for the interesting details (the name is already in the Target column)."""
    parts = [
        f"{key.replace('_', ' ')}: {value}"
        for key, value in detail.items()
        if key != "name" and value is not None
    ]
    return ", ".join(parts)


def parse_page(raw: str) -> int:
    """A positive page number, or 1 for anything else (empty, text, zero, negative, absurd)."""
    text = raw.strip()
    if not (text.isascii() and text.isdigit()):
        return 1
    value = int(text)
    return value if 0 < value <= 100_000 else 1


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
    if entry.module == "access" and target and target.isascii() and target.isdigit():
        return int(target)
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
