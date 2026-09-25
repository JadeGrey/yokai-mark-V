"""Tests for domain error types."""

from __future__ import annotations

from yokai.errors import UnmatchedTrackError, YokaiError


def test_unmatched_track_error_carries_title_and_hint() -> None:
    err = UnmatchedTrackError("Song X")
    assert isinstance(err, YokaiError)
    assert "Song X" in err.message
    assert err.user_hint
