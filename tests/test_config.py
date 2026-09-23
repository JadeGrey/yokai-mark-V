"""Tests for configuration loading and validation."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from yokai.config import ConfigError, _parse_bool, _parse_int, load_config


def test_parse_bool() -> None:
    assert _parse_bool("true") is True
    assert _parse_bool("True") is True
    assert _parse_bool("1") is True
    assert _parse_bool("yes") is True
    assert _parse_bool("false") is False
    assert _parse_bool("0") is False
    assert _parse_bool("no") is False
    assert _parse_bool(None, default=True) is True
    assert _parse_bool(None, default=False) is False
    assert _parse_bool("unknown", default=True) is True


def test_parse_int() -> None:
    assert _parse_int("TEST", "123") == 123
    assert _parse_int("TEST", None, default=42) == 42
    assert _parse_int("TEST", "", default=42) == 42
    with pytest.raises(ConfigError, match="Missing required integer"):
        _parse_int("TEST", None)
    with pytest.raises(ConfigError, match="Invalid integer"):
        _parse_int("TEST", "not_a_number")


def test_load_config_missing_token() -> None:
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(ConfigError, match="DISCORD_TOKEN is required"):
            load_config(env_path=Path("non_existent_env"))


def test_load_config_missing_guild_id() -> None:
    env = {"DISCORD_TOKEN": "mock.token.123"}
    with patch.dict(os.environ, env, clear=True):
        with pytest.raises(ConfigError, match="GUILD_ID is required"):
            load_config(env_path=Path("non_existent_env"))


def test_load_config_missing_owner_id() -> None:
    env = {
        "DISCORD_TOKEN": "mock.token.123",
        "GUILD_ID": "123456789",
    }
    with patch.dict(os.environ, env, clear=True):
        with pytest.raises(ConfigError, match="OWNER_ID is required"):
            load_config(env_path=Path("non_existent_env"))


def test_load_config_valid() -> None:
    env = {
        "DISCORD_TOKEN": "fake_token_12345.abcdef.ghijklmn",
        "GUILD_ID": "987654321",
        "OWNER_ID": "1122334455",
        "LOG_LEVEL": "DEBUG",
        "DB_PATH": "custom/path.db",
        "MAX_QUEUE_SIZE": "50",
    }
    with patch.dict(os.environ, env, clear=True):
        cfg = load_config(env_path=Path("non_existent_env"))
        assert cfg.discord_token == "fake_token_12345.abcdef.ghijklmn"
        assert cfg.guild_id == 987654321
        assert cfg.owner_id == 1122334455
        assert cfg.log_level == "DEBUG"
        assert cfg.db_path == Path("custom/path.db")
        assert cfg.max_queue_size == 50
        assert cfg.max_playlist_tracks == 100
        assert cfg.spotify_scraper_enabled is True
