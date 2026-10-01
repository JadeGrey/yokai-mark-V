"""Tests for the gambling error types."""

from __future__ import annotations

from yokai.errors import (
    AlreadyInGameError,
    BetNotOpenError,
    BetOutOfRangeError,
    InsufficientFundsError,
    InvalidGameChoiceError,
    YokaiError,
)


def test_insufficient_funds_default_wording_is_unchanged() -> None:
    err = InsufficientFundsError(balance=5, amount=10)
    assert err.message == "You have **5** Renown, which isn't enough to send **10**."


def test_insufficient_funds_can_describe_a_bet() -> None:
    err = InsufficientFundsError(balance=5, amount=10, action="bet")
    assert err.message == "You have **5** Renown, which isn't enough to bet **10**."


def test_bet_out_of_range_shows_the_limits() -> None:
    err = BetOutOfRangeError(10, 5000)
    assert "10" in err.message and "5,000" in err.message
    assert err.user_hint


def test_every_new_error_is_a_yokai_error_with_a_title() -> None:
    for err in (
        BetOutOfRangeError(1, 2),
        AlreadyInGameError(),
        BetNotOpenError(3),
        InvalidGameChoiceError("nope"),
    ):
        assert isinstance(err, YokaiError)
        assert err.title and err.message
