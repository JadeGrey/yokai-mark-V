"""Tests for the panel's display formatting helpers."""

from __future__ import annotations

import pytest

from yokai_admin.core.formatting import format_bytes, format_when


def test_format_when_survives_timestamps_the_platform_cannot_represent() -> None:
    for junk in (10**18, -(10**18), 2**63, 10**30):
        assert format_when(junk) == "–"
    assert format_when(None) == "–"
    assert format_when(1_790_000_000).startswith("2026-")


@pytest.mark.parametrize(
    ("size", "text"),
    [
        (0, "0 B"),
        (512, "512 B"),
        (1536, "1.5 KB"),
        (5 * 1024**2, "5.0 MB"),
        (3 * 1024**3, "3.0 GB"),
        (-5, "0 B"),
        (None, "–"),
    ],
)
def test_format_bytes(size: int | None, text: str) -> None:
    assert format_bytes(size) == text
