"""Tests for the event-loop lag monitor."""

from __future__ import annotations

import asyncio

import pytest

from yokai.looplag import LoopLagMonitor


@pytest.mark.asyncio
async def test_blocking_the_loop_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    monitor = LoopLagMonitor(interval_s=0.01, threshold_s=0.05)
    with caplog.at_level("WARNING", logger="yokai.looplag"):
        monitor.start()
        await asyncio.sleep(0.03)
        import time

        time.sleep(0.2)  # block the loop
        await asyncio.sleep(0.05)
        await monitor.stop()
    assert any("Event loop was blocked" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_healthy_loop_logs_nothing(caplog: pytest.LogCaptureFixture) -> None:
    monitor = LoopLagMonitor(interval_s=0.01, threshold_s=1.0)
    with caplog.at_level("WARNING", logger="yokai.looplag"):
        monitor.start()
        await asyncio.sleep(0.1)
        await monitor.stop()
    assert caplog.records == []
