"""Liveness heartbeat: one row the admin panel reads to show whether the bot is online."""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from yokai.storage.db import Database

logger = logging.getLogger(__name__)


class HeartbeatWriter:
    """Upserts `bot_heartbeat` every `interval_s` seconds while the bot is connected."""

    def __init__(
        self,
        db: Database,
        version: str,
        interval_s: float = 30.0,
        pid: Optional[int] = None,
    ) -> None:
        self._db = db
        self._version = version
        self.interval_s = interval_s
        self._pid = os.getpid() if pid is None else pid
        self._task: Optional[asyncio.Task[None]] = None

    def start(self) -> None:
        """Start the loop, if not already running (on_ready can fire again after a reconnect)."""
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """Cancel the loop and clear the row so the panel shows offline right away."""
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        try:
            await self._db.clear_heartbeat()
        except (sqlite3.Error, RuntimeError) as exc:
            logger.warning("Could not clear the heartbeat on shutdown: %s", exc)

    async def beat(self) -> None:
        """Write one heartbeat now."""
        await self._db.write_heartbeat(self._pid, self._version)

    async def _run(self) -> None:
        while True:
            try:
                await self.beat()
            except (sqlite3.Error, RuntimeError) as exc:
                logger.warning("Heartbeat write failed: %s", exc)
            await asyncio.sleep(self.interval_s)
