"""Tests for music data models."""

from __future__ import annotations

from yokai.music.models import MAX_AVAILABLE_WAIT_S, StreamInfo


def test_stream_info_wait_seconds() -> None:
    assert StreamInfo(url="u").wait_seconds(now=100) == 0
    assert StreamInfo(url="u", available_at=90).wait_seconds(now=100) == 0
    assert StreamInfo(url="u", available_at=114).wait_seconds(now=100) == 14
    assert StreamInfo(url="u", available_at=10_000).wait_seconds(now=100) == MAX_AVAILABLE_WAIT_S
