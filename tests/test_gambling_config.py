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
