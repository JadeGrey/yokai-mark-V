"""Access rules: who may be blacklisted or made an admin, and the audited writes that do it."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from yokai_admin.core.audit import record
from yokai_admin.core.db import write_transaction
from yokai_admin.core.inputs import InputRefused, clean_reason, parse_user_id
from yokai_admin.core.people import UserRef, user_refs

_OWNER_MESSAGE = (
    "That ID belongs to the bot owner, who can never be blacklisted or added to the admin list."
)


# Kept as a name of its own so `except AccessRefused` and existing imports keep working.
AccessRefused = InputRefused


@dataclass(frozen=True, slots=True)
class Entry:
    """One row of the blacklist or the admin list."""

    user: UserRef
    text: str
    added_ts: int
    added_by: str
    flag: Optional[str]


def list_blacklist(conn: sqlite3.Connection) -> list[Entry]:
    """Blacklisted people, newest first, flagged when they are also admins."""
    rows = conn.execute(
        "SELECT user_id, reason, added_ts, added_by FROM blacklist ORDER BY added_ts DESC, user_id;"
    ).fetchall()
    admin_ids = {int(r[0]) for r in conn.execute("SELECT user_id FROM admins;")}
    refs = user_refs(conn, [int(r["user_id"]) for r in rows])
    return [
        Entry(
            refs[int(r["user_id"])],
            str(r["reason"]),
            int(r["added_ts"]),
            str(r["added_by"]),
            "Also on the admin list. The blacklist wins."
            if int(r["user_id"]) in admin_ids
            else None,
        )
        for r in rows
    ]


def list_admins(conn: sqlite3.Connection) -> list[Entry]:
    """Admins, newest first, flagged when they are also blacklisted."""
    rows = conn.execute(
        "SELECT user_id, added_ts, added_by FROM admins ORDER BY added_ts DESC, user_id;"
    ).fetchall()
    blacklisted = {int(r[0]) for r in conn.execute("SELECT user_id FROM blacklist;")}
    refs = user_refs(conn, [int(r["user_id"]) for r in rows])
    return [
        Entry(
            refs[int(r["user_id"])],
            "",
            int(r["added_ts"]),
            str(r["added_by"]),
            "Also blacklisted, so the bot ignores this admin entry."
            if int(r["user_id"]) in blacklisted
            else None,
        )
        for r in rows
    ]


def _refuse_owner(user_id: int, owner_id: int) -> None:
    if user_id == owner_id:
        raise AccessRefused(_OWNER_MESSAGE)


def add_blacklist(
    db_path: Path, *, owner_id: int, raw_id: str, raw_reason: str, now: Optional[int] = None
) -> UserRef:
    """Blacklist a person. The change and its audit entry commit together."""
    user_id = parse_user_id(raw_id)
    _refuse_owner(user_id, owner_id)
    reason = clean_reason(raw_reason, required=True)
    ts = int(time.time()) if now is None else now
    with write_transaction(db_path) as conn:
        ref = user_refs(conn, [user_id])[user_id]
        if conn.execute("SELECT 1 FROM blacklist WHERE user_id = ?;", (user_id,)).fetchone():
            raise AccessRefused(f"{ref.label} is already on the blacklist.")
        conn.execute(
            "INSERT INTO blacklist (user_id, reason, added_ts, added_by) "
            "VALUES (?, ?, ?, 'panel');",
            (user_id, reason, ts),
        )
        record(
            conn,
            module="access",
            action="blacklist.add",
            target=str(user_id),
            detail={"name": ref.name},
            reason=reason,
            now=ts,
        )
    return ref


def remove_blacklist(db_path: Path, *, raw_id: str, now: Optional[int] = None) -> UserRef:
    """Take a person off the blacklist, recording the reason they were on it."""
    user_id = parse_user_id(raw_id)
    ts = int(time.time()) if now is None else now
    with write_transaction(db_path) as conn:
        ref = user_refs(conn, [user_id])[user_id]
        row = conn.execute("SELECT reason FROM blacklist WHERE user_id = ?;", (user_id,)).fetchone()
        if row is None:
            raise AccessRefused(
                f"{ref.label} is not on the blacklist. It may have been removed already."
            )
        conn.execute("DELETE FROM blacklist WHERE user_id = ?;", (user_id,))
        record(
            conn,
            module="access",
            action="blacklist.remove",
            target=str(user_id),
            detail={"name": ref.name, "previous_reason": str(row["reason"])},
            now=ts,
        )
    return ref


def add_admin(
    db_path: Path, *, owner_id: int, raw_id: str, raw_note: str, now: Optional[int] = None
) -> UserRef:
    """Make a person an admin. The optional note is kept in the audit log only."""
    user_id = parse_user_id(raw_id)
    _refuse_owner(user_id, owner_id)
    note = clean_reason(raw_note, required=False)
    ts = int(time.time()) if now is None else now
    with write_transaction(db_path) as conn:
        ref = user_refs(conn, [user_id])[user_id]
        if conn.execute("SELECT 1 FROM admins WHERE user_id = ?;", (user_id,)).fetchone():
            raise AccessRefused(f"{ref.label} is already an admin.")
        if conn.execute("SELECT 1 FROM blacklist WHERE user_id = ?;", (user_id,)).fetchone():
            raise AccessRefused(
                f"{ref.label} is blacklisted. "
                "Remove them from the blacklist before making them an admin."
            )
        conn.execute(
            "INSERT INTO admins (user_id, added_ts, added_by) VALUES (?, ?, 'panel');",
            (user_id, ts),
        )
        record(
            conn,
            module="access",
            action="admin.add",
            target=str(user_id),
            detail={"name": ref.name},
            reason=note or None,
            now=ts,
        )
    return ref


def remove_admin(db_path: Path, *, raw_id: str, now: Optional[int] = None) -> UserRef:
    """Take a person off the admin list."""
    user_id = parse_user_id(raw_id)
    ts = int(time.time()) if now is None else now
    with write_transaction(db_path) as conn:
        ref = user_refs(conn, [user_id])[user_id]
        if not conn.execute("SELECT 1 FROM admins WHERE user_id = ?;", (user_id,)).fetchone():
            raise AccessRefused(f"{ref.label} is not an admin. They may have been removed already.")
        conn.execute("DELETE FROM admins WHERE user_id = ?;", (user_id,))
        record(
            conn,
            module="access",
            action="admin.remove",
            target=str(user_id),
            detail={"name": ref.name},
            now=ts,
        )
    return ref
