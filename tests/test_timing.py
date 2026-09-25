"""Tests for stage timing and percentile helpers."""

from __future__ import annotations

import logging
import time

import pytest

from yokai.music.timing import StageTimer, percentile


def test_percentile_nearest_rank() -> None:
    values = [float(n) for n in range(1, 11)]
    assert percentile(values, 50) == 5.0
    assert percentile(values, 95) == 10.0
    assert percentile(values, 100) == 10.0
    assert percentile([7.0], 95) == 7.0


def test_percentile_empty_is_zero() -> None:
    assert percentile([], 50) == 0.0


def test_percentile_is_order_independent() -> None:
    assert percentile([9.0, 1.0, 5.0, 3.0, 7.0], 50) == 5.0


def test_stage_timer_measures_and_logs(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="yokai.timing"):
        with StageTimer("extract", video="abc123") as timer:
            time.sleep(0.02)
    assert timer.elapsed_ms >= 15.0
    assert "stage=extract" in caplog.text
    assert "status=ok" in caplog.text
    assert "video=abc123" in caplog.text


def test_stage_timer_marks_errors_and_does_not_swallow(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="yokai.timing"):
        with pytest.raises(ValueError):
            with StageTimer("search"):
                raise ValueError("boom")
    assert "status=error" in caplog.text
