"""The pure lobby state machine."""

from __future__ import annotations

import pytest

from yokai.errors import LobbyClosedError, NotAtTableError
from yokai.gambling.lobby import GAME_SPECS, GameSpec, Lobby, LobbyState, Seat

TRIO = GameSpec("trio", "Trio", min_players=2, max_players=3)


def _lobby(spec: GameSpec = TRIO) -> Lobby:
    lobby = Lobby("L1", spec, channel_id=10, host_id=1, closes_at=1000.0)
    lobby.add_seat(Seat(1, 100, 11))
    return lobby


def test_the_duel_spec_is_a_two_seat_equal_stake_game() -> None:
    spec = GAME_SPECS["duel"]
    assert (spec.min_players, spec.max_players, spec.equal_stakes) == (2, 2, True)
    assert spec.label == "Duel" and spec.rules


def test_every_planned_game_is_listed_and_playable_and_only_the_lottery_has_no_table() -> None:
    assert list(GAME_SPECS) == ["duel", "jackpot", "crash", "bjtable", "poker", "lottery"]
    assert all(spec.available for spec in GAME_SPECS.values())
    assert [name for name, spec in GAME_SPECS.items() if not spec.is_table] == ["lottery"]
    assert all(spec.label and spec.rules for spec in GAME_SPECS.values())
    assert GAME_SPECS["crash"].window_factor == 0.5 and GAME_SPECS["bjtable"].window_factor == 0.5
    assert (GAME_SPECS["jackpot"].min_players, GAME_SPECS["jackpot"].max_players) == (2, 10)
    assert (GAME_SPECS["crash"].min_players, GAME_SPECS["crash"].max_players) == (1, 10)
    assert (GAME_SPECS["bjtable"].min_players, GAME_SPECS["bjtable"].max_players) == (2, 5)


def test_the_poker_spec_is_a_two_to_six_seat_equal_buy_in_game() -> None:
    spec = GAME_SPECS["poker"]
    assert (spec.min_players, spec.max_players, spec.equal_stakes) == (2, 6, True)
    assert spec.is_table and spec.available
    assert spec.label == "Poker table" and "hold'em" in spec.rules


def test_cashing_a_seat_out_removes_it_from_the_remaining_seats_but_not_from_the_pot() -> None:
    lobby = _lobby()
    lobby.add_seat(Seat(2, 100, 12))
    lobby.mark_paid_out(2, 30)
    assert lobby.paid_out == {2: 30}
    assert list(lobby.remaining_seats) == [1]
    assert lobby.pot == 200 and set(lobby.seats) == {1, 2}


def test_a_seat_can_only_be_cashed_out_once_and_only_if_seated() -> None:
    lobby = _lobby()
    with pytest.raises(NotAtTableError):
        lobby.mark_paid_out(9, 10)
    lobby.mark_paid_out(1, 10)
    with pytest.raises(ValueError):
        lobby.mark_paid_out(1, 10)
    with pytest.raises(ValueError):
        _lobby().mark_paid_out(1, -1)


def test_a_new_lobby_is_open_with_the_host_seated() -> None:
    lobby = _lobby()
    assert lobby.state is LobbyState.OPEN and lobby.is_active
    assert lobby.host_seat == Seat(1, 100, 11)
    assert lobby.pot == 100 and not lobby.can_start and not lobby.is_full


def test_seats_fill_up_to_the_maximum() -> None:
    lobby = _lobby()
    lobby.add_seat(Seat(2, 50, 12))
    assert lobby.can_start and lobby.pot == 150
    lobby.add_seat(Seat(3, 25, 13))
    assert lobby.is_full
    with pytest.raises(LobbyClosedError, match="full"):
        lobby.add_seat(Seat(4, 10, 14))


def test_the_same_member_cannot_sit_twice() -> None:
    lobby = _lobby()
    with pytest.raises(LobbyClosedError, match="already seated"):
        lobby.add_seat(Seat(1, 100, 99))


def test_a_guest_can_leave_but_the_host_cannot() -> None:
    lobby = _lobby()
    lobby.add_seat(Seat(2, 50, 12))
    assert lobby.remove_seat(2) == Seat(2, 50, 12)
    with pytest.raises(LobbyClosedError, match="host"):
        lobby.remove_seat(1)
    with pytest.raises(NotAtTableError):
        lobby.remove_seat(3)


def test_seats_only_change_while_open() -> None:
    lobby = _lobby()
    lobby.add_seat(Seat(2, 50, 12))
    lobby.mark_running()
    with pytest.raises(LobbyClosedError):
        lobby.add_seat(Seat(3, 25, 13))
    with pytest.raises(LobbyClosedError):
        lobby.remove_seat(2)


def test_transitions_follow_open_running_settled() -> None:
    lobby = _lobby()
    lobby.mark_running()
    assert lobby.state is LobbyState.RUNNING and lobby.is_active
    lobby.close(LobbyState.SETTLED)
    assert lobby.state is LobbyState.SETTLED and not lobby.is_active


def test_an_open_or_running_lobby_can_be_cancelled_but_a_closed_one_cannot_change() -> None:
    open_lobby = _lobby()
    open_lobby.close(LobbyState.CANCELLED)
    assert open_lobby.state is LobbyState.CANCELLED
    with pytest.raises(LobbyClosedError):
        open_lobby.close(LobbyState.SETTLED)
    with pytest.raises(LobbyClosedError):
        open_lobby.mark_running()


def test_close_only_accepts_terminal_states() -> None:
    lobby = _lobby()
    with pytest.raises(ValueError):
        lobby.close(LobbyState.RUNNING)


def test_a_lobby_cannot_start_running_twice() -> None:
    lobby = _lobby()
    lobby.mark_running()
    with pytest.raises(LobbyClosedError):
        lobby.mark_running()
