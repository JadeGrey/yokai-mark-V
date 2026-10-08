"""Tests for the gambling error types."""

from __future__ import annotations

from yokai.errors import (
    AlreadyInGameError,
    BetNotOpenError,
    BetOutOfRangeError,
    ChannelNotConfiguredError,
    InsufficientFundsError,
    InvalidGameChoiceError,
    LobbyClosedError,
    LotteryClosedError,
    NotAtTableError,
    NotHostError,
    NotYourTurnError,
    TableBusyError,
    TablePermissionError,
    TicketLimitError,
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


def test_table_errors_are_plain_domain_errors() -> None:
    for err in (
        TableBusyError(),
        ChannelNotConfiguredError(),
        LobbyClosedError(),
        NotAtTableError(),
        NotHostError(),
        TablePermissionError(["Send Messages", "Embed Links"], "tables"),
    ):
        assert isinstance(err, YokaiError)
        assert err.message and err.title


def test_lobby_closed_error_accepts_a_custom_message_without_a_hint() -> None:
    err = LobbyClosedError("This table is full.", user_hint=None)
    assert err.message == "This table is full." and err.user_hint is None


def test_table_permission_error_names_what_is_missing() -> None:
    err = TablePermissionError(["Send Messages", "Embed Links"], "tables")
    assert "Send Messages" in err.message and "Embed Links" in err.message
    assert "#tables" in err.message


def test_not_your_turn_is_a_plain_domain_error() -> None:
    err = NotYourTurnError()
    assert isinstance(err, YokaiError) and err.title == "Not your turn"
    assert "turn" in err.message.lower() and err.user_hint


def test_lottery_closed_error_has_a_default_and_accepts_a_custom_message() -> None:
    default = LotteryClosedError()
    assert isinstance(default, YokaiError) and "closed" in default.message.lower()
    custom = LotteryClosedError("Tickets are not on sale yet.", user_hint=None)
    assert custom.message == "Tickets are not on sale yet." and custom.user_hint is None


def test_ticket_limit_error_names_the_cap_and_what_the_member_holds() -> None:
    err = TicketLimitError(held=8, maximum=10)
    assert isinstance(err, YokaiError) and "10" in err.message and "8" in err.message
