"""Tests for the admin panel's own settings loader."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from yokai_admin.core.settings import HOST, PanelSettings, load_settings
from yokai_admin.errors import PanelConfigError

NO_ENV_FILE = Path("non_existent_env")


def test_defaults_need_no_discord_secrets() -> None:
    with patch.dict(os.environ, {}, clear=True):
        settings = load_settings(env_path=NO_ENV_FILE)
    assert settings == PanelSettings(db_path=Path("data/yokai.db"), log_dir=Path("logs"), port=8765)


def test_overrides_are_read_from_the_environment() -> None:
    env = {"DB_PATH": "custom/bot.db", "LOG_DIR": "custom/logs", "ADMIN_PORT": "9000"}
    with patch.dict(os.environ, env, clear=True):
        settings = load_settings(env_path=NO_ENV_FILE)
    assert settings.db_path == Path("custom/bot.db")
    assert settings.log_dir == Path("custom/logs")
    assert settings.port == 9000


@pytest.mark.parametrize("value", ["abc", "80", "1023", "70000", "0", "-1"])
def test_a_bad_port_is_rejected_with_a_clear_message(value: str) -> None:
    with patch.dict(os.environ, {"ADMIN_PORT": value}, clear=True):
        with pytest.raises(PanelConfigError, match="ADMIN_PORT"):
            load_settings(env_path=NO_ENV_FILE)


def test_the_host_is_loopback_and_cannot_be_configured() -> None:
    assert HOST == "127.0.0.1"
    with patch.dict(os.environ, {"ADMIN_HOST": "0.0.0.0"}, clear=True):
        settings = load_settings(env_path=NO_ENV_FILE)
    assert not hasattr(settings, "host")
