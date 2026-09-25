"""Tests that the bot wires the searcher, buffer and player together."""

from __future__ import annotations

from pathlib import Path

import pytest

from yokai.bot import YokaiBot
from yokai.config import Config
from yokai.health import HealthReport


def make_bot(**overrides: object) -> YokaiBot:
    config = Config(
        discord_token="fake_token",
        guild_id=123,
        owner_id=456,
        db_path=":memory:",  # type: ignore[arg-type]
        audio_buffer_dir=overrides.pop("audio_buffer_dir", Path("data/test-cache")),  # type: ignore[arg-type]
        **overrides,  # type: ignore[arg-type]
    )
    health = HealthReport(ffmpeg_found=True, ffmpeg_path="C:/ffmpeg/bin/ffmpeg.exe")
    return YokaiBot(config, health_report=health)


@pytest.mark.asyncio
async def test_resolver_uses_the_searcher() -> None:
    bot = make_bot()
    assert bot.resolver.searcher is bot.searcher


@pytest.mark.asyncio
async def test_player_gets_the_shared_buffer() -> None:
    bot = make_bot()
    player = bot.get_player(123)
    assert player.buffer is bot.audio_buffer
    assert player.resolver is bot.resolver


@pytest.mark.asyncio
async def test_buffer_can_be_disabled_by_config() -> None:
    bot = make_bot(audio_buffer_mb=0)
    assert bot.audio_buffer.enabled is False
