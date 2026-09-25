"""Lightweight stage timing used for latency logs and the benchmark harness."""

from __future__ import annotations

import logging
import math
import time
from types import TracebackType
from typing import Optional, Sequence

logger = logging.getLogger("yokai.timing")


class StageTimer:
    """Context manager that logs how long a named stage took, in milliseconds.

    Usage:
        with StageTimer("stream_extract", video=track.video_id) as timer:
            ...
        timer.elapsed_ms
    """

    def __init__(self, stage: str, **fields: object) -> None:
        self.stage = stage
        self.fields = fields
        self.elapsed_ms: float = 0.0
        self._start: float = 0.0

    def __enter__(self) -> StageTimer:
        self._start = time.perf_counter()
        return self

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        self.elapsed_ms = (time.perf_counter() - self._start) * 1000.0
        extra = " ".join(f"{key}={value}" for key, value in self.fields.items())
        status = "error" if exc_type is not None else "ok"
        logger.info("stage=%s ms=%.0f status=%s %s", self.stage, self.elapsed_ms, status, extra)


def percentile(values: Sequence[float], pct: float) -> float:
    """Nearest-rank percentile; returns 0.0 for empty input."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100.0 * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]
