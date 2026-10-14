"""CHALLENGE_* settings: defaults, clamping and bad values."""

from __future__ import annotations

from pathlib import Path

import pytest

from yokai.config import ConfigError, load_config


def _load(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **env: str):
    for key in (
        "CHALLENGE_ENABLED",
        "CHALLENGE_TZ",
        "CHALLENGE_MIN_WAGER",
        "CHALLENGE_REWARD_SCALE",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DISCORD_TOKEN", "x")
    monkeypatch.setenv("GUILD_ID", "1")
    monkeypatch.setenv("OWNER_ID", "2")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return load_config(env_path=tmp_path / "missing.env")


def test_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = _load(monkeypatch, tmp_path)
    assert config.challenge_enabled is True
    assert config.challenge_tz == "UTC"
    assert config.challenge_min_wager == 10
    assert config.challenge_reward_scale == 1.0


def test_values_are_read(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = _load(
        monkeypatch,
        tmp_path,
        CHALLENGE_ENABLED="false",
        CHALLENGE_MIN_WAGER="25",
        CHALLENGE_REWARD_SCALE="1.5",
    )
    assert config.challenge_enabled is False
    assert config.challenge_min_wager == 25
    assert config.challenge_reward_scale == 1.5


def test_scale_and_wager_are_clamped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = _load(monkeypatch, tmp_path, CHALLENGE_REWARD_SCALE="999", CHALLENGE_MIN_WAGER="-5")
    assert config.challenge_reward_scale == 10.0
    assert config.challenge_min_wager == 1
    low = _load(monkeypatch, tmp_path, CHALLENGE_REWARD_SCALE="0")
    assert low.challenge_reward_scale == 0.1


@pytest.mark.parametrize("bad", ["abc", "nan", "inf"])
def test_bad_scale_is_a_config_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, bad: str
) -> None:
    with pytest.raises(ConfigError, match="CHALLENGE_REWARD_SCALE"):
        _load(monkeypatch, tmp_path, CHALLENGE_REWARD_SCALE=bad)


def test_unknown_timezone_is_a_config_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with pytest.raises(ConfigError, match="CHALLENGE_TZ"):
        _load(monkeypatch, tmp_path, CHALLENGE_TZ="Not/AZone")
