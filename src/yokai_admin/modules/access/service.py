"""Access rules: who may be blacklisted or made an admin, and the audited writes that do it."""

from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from yokai_admin.core.audit import record
from yokai_admin.core.db import write_transaction
from yokai_admin.core.people import UserRef, user_refs
from yokai_admin.errors import PanelError

MIN_REASON = 3
MAX_REASON = 200
# Same set the bot strips from display names: control, zero-width and bidi override characters.
_UNSAFE = re.compile(r"[\x00-\x1f\x7f-\x9f​-‏ -‮⁦-⁩]")
_OWNER_MESSAGE = (
    "That ID belongs to the bot owner, who can never be blacklisted or added to the admin list."
)


class AccessRefused(PanelError):
    """A request the panel refuses on purpose; the message is shown to the owner."""


@dataclass(frozen=True, slots=True)
class Entry:
    """One row of the blacklist or the admin list."""

    user: UserRef
    text: str
    added_ts: int
    added_by: str
    flag: Optional[str]


def parse_user_id(raw: str) -> int:
    """Turn typed text into a Discord user ID, or refuse with a message that says what to type."""
    text = raw.strip()
    # int() would also accept digits from other scripts, so check for plain ASCII digits first.
    if not (text.isascii() and text.isdigit()):
        raise AccessRefused(
            "Enter the Discord user ID as digits only, for example 123456789012345678."
        )
    # A Discord ID has at most 19 digits. Checking the length first also keeps int() from raising
    # on absurdly long input (it refuses more than 4300 digits).
    if len(text) > 19 or not 0 < (value := int(text)) < 2**63:
        raise AccessRefused("That is not a valid Discord user ID.")
    return value


def clean_reason(raw: str, *, required: bool) -> str:
    """Normalise a reason or note; refuse when it is too short (if required) or too long."""
    text = " ".join(_UNSAFE.sub(" ", raw).split())
    if required and len(text) < MIN_REASON:
        raise AccessRefused(f"Give a short reason, at least {MIN_REASON} characters.")
    if len(text) > MAX_REASON:
        raise AccessRefused(f"Keep the reason under {MAX_REASON} characters.")
    return text


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
