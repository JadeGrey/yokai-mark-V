"""Tests that the bot wires the searcher, buffer and player together."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

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


def _bot_disconnect_event(bot: YokaiBot) -> tuple[MagicMock, MagicMock, MagicMock]:
    member = MagicMock()
    member.id = 999
    member.guild.id = 123
    before = MagicMock()
    before.channel = MagicMock()
    after = MagicMock()
    after.channel = None
    bot._connection.user = MagicMock(id=999)  # the bot itself
    bot.presence = AsyncMock()  # type: ignore[assignment]
    return member, before, after


@pytest.mark.asyncio
async def test_own_stop_does_not_stop_the_player_again() -> None:
    """/stop disconnects voice; the resulting voice-state event must not re-run stop()."""
    bot = make_bot()
    player = MagicMock()
    player.voice_client = None  # stop() already cleared it
    player.stop = AsyncMock()
    bot.players[123] = player

    await bot.on_voice_state_update(*_bot_disconnect_event(bot))

    player.stop.assert_not_awaited()


@pytest.mark.asyncio
async def test_external_disconnect_stops_the_player() -> None:
    bot = make_bot()
    player = MagicMock()
    player.voice_client = MagicMock()  # still set: a kick, not our own stop()
    player.stop = AsyncMock()
    bot.players[123] = player

    await bot.on_voice_state_update(*_bot_disconnect_event(bot))

    player.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_buffer_can_be_disabled_by_config() -> None:
    bot = make_bot(audio_buffer_mb=0)
    assert bot.audio_buffer.enabled is False
