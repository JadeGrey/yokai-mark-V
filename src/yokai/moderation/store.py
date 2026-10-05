"""Cached, read-only view of the blacklist and adminlist the admin panel manages."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from yokai.storage.db import Database

logger = logging.getLogger(__name__)


class ModerationStore:
    """Answers "is this user blacklisted / an admin?" from a short-lived cache.

    The panel edits the tables from another process, so there is no push channel: the cache
    simply expires after `ttl_s` seconds and the next check reloads. The owner is special-cased
    and never touches the database.
    """

    def __init__(
        self,
        db: Database,
        owner_id: int,
        ttl_s: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._db = db
        self.owner_id = owner_id
        self.ttl_s = ttl_s
        self._clock = clock
        self._lock = asyncio.Lock()
        self._loaded_at: Optional[float] = None
        self._blacklisted: frozenset[int] = frozenset()
        self._admins: frozenset[int] = frozenset()

    async def is_blacklisted(self, user_id: int) -> bool:
        """True if the user is on the blacklist. The owner never is."""
        if user_id == self.owner_id:
            return False
        await self._ensure_fresh()
        return user_id in self._blacklisted

    async def is_admin(self, user_id: int) -> bool:
        """True for the owner, or for an adminlist member who is not also blacklisted."""
        if user_id == self.owner_id:
            return True
        await self._ensure_fresh()
        return user_id in self._admins and user_id not in self._blacklisted

    def _is_fresh(self) -> bool:
        return self._loaded_at is not None and self._clock() - self._loaded_at < self.ttl_s

    async def _ensure_fresh(self) -> None:
        if self._is_fresh():
            return
        async with self._lock:
            if self._is_fresh():  # another waiter refreshed while we queued for the lock
                return
            try:
                blacklisted, admins = await self._db.get_moderation_lists()
            except (sqlite3.Error, RuntimeError) as exc:
                # Keep the last good copy (empty, i.e. fail open, if there never was one) and
                # wait one TTL before trying again so a broken DB is not hit on every command.
                logger.warning("Could not refresh moderation lists, using the last copy: %s", exc)
                self._loaded_at = self._clock()
                return
            self._blacklisted = blacklisted
            self._admins = admins
            self._loaded_at = self._clock()
