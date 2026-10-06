"""Resolve Discord user IDs to the display names the bot has seen."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Iterable, Optional

_CHUNK = 500


@dataclass(frozen=True, slots=True)
class UserRef:
    """A person the panel can show: their ID and, when the bot has seen them, their name."""

    id: int
    name: Optional[str]

    @property
    def display(self) -> str:
        return self.name or "Unknown user"

    @property
    def label(self) -> str:
        return f"{self.display} ({self.id})"


def user_refs(conn: sqlite3.Connection, ids: Iterable[int]) -> dict[int, UserRef]:
    """Return a `UserRef` for every requested ID (name is None when the bot never saw them)."""
    unique = sorted(set(ids))
    refs = {user_id: UserRef(user_id, None) for user_id in unique}
    for start in range(0, len(unique), _CHUNK):
        chunk = unique[start : start + _CHUNK]
        # Only "?" placeholders are formatted into the SQL; the IDs travel as parameters.
        marks = ",".join("?" * len(chunk))
        rows = conn.execute(
            f"SELECT user_id, display_name FROM user_names WHERE user_id IN ({marks});", chunk
        )
        for row in rows:
            refs[int(row["user_id"])] = UserRef(int(row["user_id"]), str(row["display_name"]))
    return refs
