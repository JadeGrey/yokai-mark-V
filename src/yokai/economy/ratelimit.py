"""In-memory rate limiter for music-command Renown rewards.

Per-process and not persisted: a bot restart simply clears every window. That is an accepted
trade-off for a single-process bot with about 10 trusted users (see the design spec, Risks).
"""

from __future__ import annotations

import time
from collections import deque
from typing import Optional


class CommandRewardLimiter:
    """Allows at most `limit` rewarded calls per rolling `window_s` seconds, per user."""

    def __init__(self, limit: int, window_s: float) -> None:
        self.limit = limit
        self.window_s = window_s
        self._hits: dict[int, deque[float]] = {}

    def try_consume(self, user_id: int, now: Optional[float] = None) -> bool:
        """Return True and record a hit if `user_id` is under the limit right now."""
        ts = now if now is not None else time.monotonic()
        hits = self._hits.setdefault(user_id, deque())
        cutoff = ts - self.window_s
        while hits and hits[0] <= cutoff:
            hits.popleft()
        if len(hits) >= self.limit:
            return False
        hits.append(ts)
        return True
