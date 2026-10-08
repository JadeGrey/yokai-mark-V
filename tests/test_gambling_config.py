"""Tests for the gambling configuration variables."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from yokai.config import Config, ConfigError, load_config

BASE_ENV = {"DISCORD_TOKEN": "t", "GUILD_ID": "1", "OWNER_ID": "2"}


def _load(extra: dict[str, str] | None = None) -> Config:
    with patch.dict(os.environ, {**BASE_ENV, **(extra or {})}, clear=True):
        return load_config(env_path=Path("non_existent_env"))


def test_gambling_defaults() -> None:
    cfg = _load()
    assert cfg.gambling_min_bet == 10
    assert cfg.gambling_max_bet == 5000


def test_gambling_overrides() -> None:
    cfg = _load({"GAMBLING_MIN_BET": "25", "GAMBLING_MAX_BET": "900"})
    assert (cfg.gambling_min_bet, cfg.gambling_max_bet) == (25, 900)


def test_min_bet_is_floored_at_one() -> None:
    assert _load({"GAMBLING_MIN_BET": "0"}).gambling_min_bet == 1


def test_max_bet_below_min_bet_is_a_config_error() -> None:
    with pytest.raises(ConfigError, match="GAMBLING_MAX_BET"):
        _load({"GAMBLING_MIN_BET": "100", "GAMBLING_MAX_BET": "50"})


def test_table_defaults() -> None:
    cfg = _load()
    assert cfg.gambling_duel_rake_percent == 2
    assert cfg.gambling_lobby_join_seconds == 60
    assert cfg.gambling_turn_seconds == 45


def test_table_overrides() -> None:
    cfg = _load(
        {
            "GAMBLING_DUEL_RAKE_PERCENT": "5",
            "GAMBLING_LOBBY_JOIN_SECONDS": "90",
            "GAMBLING_TURN_SECONDS": "30",
        }
    )
    assert (
        cfg.gambling_duel_rake_percent,
        cfg.gambling_lobby_join_seconds,
        cfg.gambling_turn_seconds,
    ) == (5, 90, 30)


def test_rake_is_clamped_between_zero_and_fifty() -> None:
    assert _load({"GAMBLING_DUEL_RAKE_PERCENT": "-3"}).gambling_duel_rake_percent == 0
    assert _load({"GAMBLING_DUEL_RAKE_PERCENT": "0"}).gambling_duel_rake_percent == 0
    assert _load({"GAMBLING_DUEL_RAKE_PERCENT": "99"}).gambling_duel_rake_percent == 50


def test_timers_have_a_five_second_floor() -> None:
    cfg = _load({"GAMBLING_LOBBY_JOIN_SECONDS": "0", "GAMBLING_TURN_SECONDS": "1"})
    assert (cfg.gambling_lobby_join_seconds, cfg.gambling_turn_seconds) == (5, 5)


def test_jackpot_and_crash_defaults() -> None:
    cfg = _load()
    assert cfg.gambling_jackpot_rake_percent == 2
    assert cfg.gambling_crash_max_multiplier == 100


def test_jackpot_and_crash_overrides() -> None:
    cfg = _load({"GAMBLING_JACKPOT_RAKE_PERCENT": "4", "GAMBLING_CRASH_MAX_MULTIPLIER": "50"})
    assert (cfg.gambling_jackpot_rake_percent, cfg.gambling_crash_max_multiplier) == (4, 50)


def test_jackpot_rake_is_clamped_between_zero_and_fifty() -> None:
    assert _load({"GAMBLING_JACKPOT_RAKE_PERCENT": "-1"}).gambling_jackpot_rake_percent == 0
    assert _load({"GAMBLING_JACKPOT_RAKE_PERCENT": "80"}).gambling_jackpot_rake_percent == 50


def test_crash_cap_has_a_floor_of_two() -> None:
    assert _load({"GAMBLING_CRASH_MAX_MULTIPLIER": "1"}).gambling_crash_max_multiplier == 2
    assert _load({"GAMBLING_CRASH_MAX_MULTIPLIER": "0"}).gambling_crash_max_multiplier == 2


def test_lottery_defaults() -> None:
    cfg = _load()
    assert cfg.lottery_ticket_price == 100
    assert cfg.lottery_number_range == 20
    assert cfg.lottery_max_tickets_per_user == 10
    assert cfg.lottery_draw_interval_hours == 168


def test_lottery_overrides() -> None:
    cfg = _load(
        {
            "LOTTERY_TICKET_PRICE": "250",
            "LOTTERY_NUMBER_RANGE": "30",
            "LOTTERY_MAX_TICKETS_PER_USER": "5",
            "LOTTERY_DRAW_INTERVAL_HOURS": "24",
        }
    )
    assert (
        cfg.lottery_ticket_price,
        cfg.lottery_number_range,
        cfg.lottery_max_tickets_per_user,
        cfg.lottery_draw_interval_hours,
    ) == (250, 30, 5, 24)


def test_lottery_values_are_clamped_to_sensible_bounds() -> None:
    cfg = _load(
        {
            "LOTTERY_TICKET_PRICE": "0",
            "LOTTERY_NUMBER_RANGE": "1",
            "LOTTERY_MAX_TICKETS_PER_USER": "0",
            "LOTTERY_DRAW_INTERVAL_HOURS": "0",
        }
    )
    assert (
        cfg.lottery_ticket_price,
        cfg.lottery_number_range,
        cfg.lottery_max_tickets_per_user,
        cfg.lottery_draw_interval_hours,
    ) == (1, 2, 1, 1)
    assert _load({"LOTTERY_NUMBER_RANGE": "5000"}).lottery_number_range == 100
