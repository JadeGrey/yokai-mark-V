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


def test_daily_on_cooldown_error_reports_wait_time() -> None:
    err = err_mod.DailyOnCooldownError(retry_after_s=5400)
    assert err.title == "Already claimed"
    assert "1h 30m" in err.message
    assert err.retry_after_s == 5400


def test_daily_on_cooldown_error_under_an_hour() -> None:
    err = err_mod.DailyOnCooldownError(retry_after_s=120)
    assert "2m" in err.message
    assert "h" not in err.message.split("Try again in ")[1]


def test_daily_on_cooldown_error_under_a_minute_does_not_say_zero_minutes() -> None:
    """Review Focus: a sub-60s wait must not display as the nonsensical '0m'."""
    err = err_mod.DailyOnCooldownError(retry_after_s=40)
    assert "0m" not in err.message
    assert "less than a minute" in err.message


def test_insufficient_funds_error_reports_amounts() -> None:
    err = err_mod.InsufficientFundsError(balance=40, amount=100)
    assert err.title == "Not enough Renown"
    assert "40" in err.message
    assert "100" in err.message
    assert err.balance == 40
    assert err.amount == 100


def test_invalid_payment_error_carries_the_message() -> None:
    err = err_mod.InvalidPaymentError("You can only pay another human member of the server.")
    assert err.title == "Invalid payment"
    assert "human member" in err.message
