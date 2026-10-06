"""Remembers each user's latest display name so the admin panel can show people by name."""

from __future__ import annotations

import asyncio
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
    """Writes a user's name to the database when it is new, changed, or a day old.

    `touch` runs inside `interaction_check`, before a command can even acknowledge the
    interaction, and Discord gives that only 3 seconds. A locked database would hold a plain
    `await` for SQLite's 5 second busy timeout, so the write runs as a background task instead and
    `touch` returns at once.
    """

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
        # (name, time of the last write attempt). About ten people use the bot, so this stays tiny.
        self._seen: dict[int, tuple[str, float]] = {}
        # Strong references: a task nobody holds can be garbage-collected before it finishes.
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def pending(self) -> int:
        """How many writes are still running in the background."""
        return len(self._tasks)

    def touch(self, user_id: int, raw_name: object) -> None:
        """Schedule recording the name if it needs recording. Never blocks and never raises."""
        name = clean_name(raw_name)
        if not name:
            return
        now = self._clock()
        last = self._seen.get(user_id)
        if last is not None and last[0] == name and now - last[1] < self.ttl_s:
            return
        # Mark the person as handled before the write finishes, so a burst of interactions (an
        # autocomplete fires on every keystroke) starts one write, not one per keystroke.
        self._seen[user_id] = (name, now)
        task = asyncio.create_task(self._write(user_id, name, now))
        self._tasks.add(task)
        task.add_done_callback(self._finished)

    async def _write(self, user_id: int, name: str, started: float) -> None:
        try:
            await self._db.upsert_user_name(user_id, name)
        except (sqlite3.Error, RuntimeError, ValueError) as exc:
            # ValueError: aiosqlite raises it (not sqlite3.Error) once its connection is closed.
            logger.warning("Could not remember the name of user %d: %s", user_id, exc)
            # Pretend the write happened `ttl_s - retry_s` ago, so the next try is `retry_s` away.
            self._seen[user_id] = (name, started - self.ttl_s + self.retry_s)

    def _finished(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.error("Remembering a user name failed unexpectedly: %s", task.exception())

    async def wait_idle(self) -> None:
        """Wait for every scheduled write to finish (used by tests and by shutdown)."""
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def close(self) -> None:
        """Cancel writes still in flight; call before the database closes."""
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
