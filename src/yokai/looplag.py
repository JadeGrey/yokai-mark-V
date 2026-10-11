"""Event-loop lag monitor: logs when something blocks the loop long enough to hurt audio."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


class LoopLagMonitor:
    """Sleeps `interval_s` in a loop and warns when it wakes up more than `threshold_s` late.

    A late wake-up means synchronous work (or GIL contention) held the loop, which is the
    first thing to rule in or out when music stutters.
    """

    def __init__(self, interval_s: float = 0.5, threshold_s: float = 0.5) -> None:
        self.interval_s = interval_s
        self.threshold_s = threshold_s
        self._task: Optional[asyncio.Task[None]] = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._run())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        while True:
            before = time.monotonic()
            await asyncio.sleep(self.interval_s)
            lag = time.monotonic() - before - self.interval_s
            if lag > self.threshold_s:
                logger.warning("Event loop was blocked for about %.2fs.", lag)
