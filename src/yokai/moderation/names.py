"""Remembers each user's latest display name so the admin panel can show people by name."""

from __future__ import annotations

import logging
import re
import sqlite3
import time
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from yokai.storage.db import Database

logger = logging.getLogger(__name__)

MAX_NAME_CHARS = 80
# Control characters, zero-width characters and the bidirectional overrides/isolates that can make
# text on the panel display in a different order than it was typed.
_UNSAFE = re.compile(r"[\x00-\x1f\x7f-\x9f​-‏ -‮⁦-⁩]")


def clean_name(raw: object) -> str:
    """Return a display name that is safe to store and show; empty if nothing usable is left."""
    if not isinstance(raw, str):
        return ""
    text = " ".join(_UNSAFE.sub("", raw).split())
    return text[:MAX_NAME_CHARS]


class UserNameCache:
    """Writes a user's name to the database when it is new, changed, or a day old."""

    def __init__(
        self,
        db: Database,
        ttl_s: float = 86_400.0,
        retry_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._db = db
        self.ttl_s = ttl_s
        self.retry_s = retry_s
        self._clock = clock
        # (name, time of last successful write). About ten people use the bot, so this stays tiny.
        self._seen: dict[int, tuple[str, float]] = {}

    async def touch(self, user_id: int, raw_name: object) -> None:
        """Record the name if it needs recording. Never raises for database problems."""
        name = clean_name(raw_name)
        if not name:
            return
        now = self._clock()
        last = self._seen.get(user_id)
        if last is not None and last[0] == name and now - last[1] < self.ttl_s:
            return
        try:
            await self._db.upsert_user_name(user_id, name)
        except (sqlite3.Error, RuntimeError, ValueError) as exc:
            # ValueError: aiosqlite raises it (not sqlite3.Error) once its connection is closed.
            logger.warning("Could not remember the name of user %d: %s", user_id, exc)
            # Pretend the write happened `ttl_s - retry_s` ago, so the next try is `retry_s` away.
            self._seen[user_id] = (name, now - self.ttl_s + self.retry_s)
            return
        self._seen[user_id] = (name, now)
