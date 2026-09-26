"""Tests for domain error types."""

from __future__ import annotations

import pytest

from yokai import errors as err_mod
from yokai.errors import UnmatchedTrackError, YokaiError


def test_unmatched_track_error_carries_title_and_hint() -> None:
    err = UnmatchedTrackError("Song X")
    assert isinstance(err, YokaiError)
    assert "Song X" in err.message
    assert err.user_hint


@pytest.mark.parametrize(
    ("cls", "expected"),
    [
        (err_mod.NotFoundError, "Nothing found"),
        (err_mod.LoginRequiredError, "Login required"),
        (err_mod.UnavailableError, "Track unavailable"),
        (err_mod.BotCheckError, "YouTube blocked the request"),
        (err_mod.ExtractionTimeoutError, "Took too long"),
        (err_mod.UnsupportedError, "Not supported"),
        (err_mod.SpotifyResolutionError, "Spotify lookup failed"),
        (err_mod.VoiceChannelError, "Voice channel needed"),
    ],
)
def test_domain_errors_carry_a_specific_title(cls: type, expected: str) -> None:
    assert cls().title == expected


def test_special_domain_error_titles() -> None:
    assert err_mod.UnmatchedTrackError("Song X").title == "No match found"
    assert err_mod.VoicePermissionError("Connect", "General").title == "Missing permission"
    assert err_mod.VoiceError("boom").title == "Voice problem"
    assert err_mod.YokaiError("boom").title == "That didn't work"
