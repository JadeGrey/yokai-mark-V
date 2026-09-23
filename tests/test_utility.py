"""Tests for utility commands, telemetry aggregations, and DiagView updater."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from yokai.cogs.meta import MetaCog, _format_uptime
from yokai.music.resolvers.ytdlp import YtDlpResolver
from yokai.storage.db import Database
from yokai.theme import COLOR_ERROR, COLOR_SUCCESS, COLOR_WARNING
from yokai.ui.embeds import EmbedFactory
from yokai.ui.views import DiagView


def _total_embed_length(embed: discord.Embed) -> int:
    total = len(embed.title or "") + len(embed.description or "")
    if embed.footer and embed.footer.text:
        total += len(embed.footer.text)
    if embed.author and embed.author.name:
        total += len(embed.author.name)
    for f in embed.fields:
        total += len(f.name or "") + len(f.value or "")
    return total


# ---------------------------------------------------------------------------
# 1. Database Usage Statistics Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_usage_stats_empty_db() -> None:
    """Empty database returns empty operator and command rankings."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "empty.db")
        await db.initialize()

        users, cmds = await db.get_usage_stats()
        assert users == []
        assert cmds == []

        users_7d, cmds_7d = await db.get_usage_stats(period_days=7)
        assert users_7d == []
        assert cmds_7d == []

        await db.close()


@pytest.mark.asyncio
async def test_usage_stats_period_filtering() -> None:
    """Usage queries correctly filter by period boundaries (7d, 30d, all)."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "periods.db")
        await db.initialize()

        now = int(time.time())
        t_2d = now - (2 * 86400)
        t_15d = now - (15 * 86400)
        t_45d = now - (45 * 86400)

        # Insert command logs with explicit timestamps
        await db.conn.execute(
            "INSERT INTO command_log (ts, user_id, command) VALUES (?, ?, ?)",
            (t_2d, 100, "play"),
        )
        await db.conn.execute(
            "INSERT INTO command_log (ts, user_id, command) VALUES (?, ?, ?)",
            (t_15d, 200, "skip"),
        )
        await db.conn.execute(
            "INSERT INTO command_log (ts, user_id, command) VALUES (?, ?, ?)",
            (t_45d, 300, "queue"),
        )

        # Insert play events with explicit timestamps
        await db.conn.execute(
            """
            INSERT INTO play_events (
                ts, user_id, video_id, title, artist, duration_s, origin, outcome, listened_s
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (t_2d, 100, "v1", "Song 1", "Artist", 180, "search", "completed", 180),
        )
        await db.conn.execute(
            """
            INSERT INTO play_events (
                ts, user_id, video_id, title, artist, duration_s, origin, outcome, listened_s
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (t_15d, 200, "v2", "Song 2", "Artist", 210, "search", "completed", 210),
        )
        await db.conn.commit()

        # 7-day filter
        u_7d, c_7d = await db.get_usage_stats(period_days=7)
        assert len(u_7d) == 1
        assert u_7d[0] == (100, 1, 1)
        assert len(c_7d) == 1
        assert c_7d[0] == ("play", 1)

        # 30-day filter
        u_30d, c_30d = await db.get_usage_stats(period_days=30)
        assert len(u_30d) == 2
        user_ids_30d = {u[0] for u in u_30d}
        assert user_ids_30d == {100, 200}
        assert len(c_30d) == 2

        # All-time filter
        u_all, c_all = await db.get_usage_stats(period_days=None)
        assert len(u_all) == 3
        user_ids_all = {u[0] for u in u_all}
        assert user_ids_all == {100, 200, 300}
        assert len(c_all) == 3

        await db.close()


@pytest.mark.asyncio
async def test_usage_stats_deterministic_tie_breaking() -> None:
    """Equal counts must break ties deterministically by user_id and command name."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "ties.db")
        await db.initialize()

        # Two users with 1 command each
        await db.log_command(user_id=500, command="zeta")
        await db.log_command(user_id=100, command="alpha")

        users, cmds = await db.get_usage_stats()
        # user 100 < 500
        assert users[0][0] == 100
        assert users[1][0] == 500

        # alpha < zeta
        assert cmds[0][0] == "alpha"
        assert cmds[1][0] == "zeta"

        await db.close()


# ---------------------------------------------------------------------------
# 2. Embed Formatting Tests
# ---------------------------------------------------------------------------


def test_ping_embed_latency_coloring() -> None:
    """Ping status color transitions based on the worst measured latency."""
    # Fast: all < 150ms -> green
    embed_fast = EmbedFactory.ping(gateway_ms=45.0, rest_ms=80.0, db_ms=2.0)
    assert embed_fast.color is not None and embed_fast.color.value == COLOR_SUCCESS
    assert "Fast. As expected." in (embed_fast.description or "")

    # Warning: worst between 150ms and 400ms -> amber
    embed_warn = EmbedFactory.ping(gateway_ms=45.0, rest_ms=220.0, db_ms=1.5)
    assert embed_warn.color is not None and embed_warn.color.value == COLOR_WARNING
    assert "Acceptable signal latency." in (embed_warn.description or "")

    # Error: worst >= 400ms -> red
    embed_err = EmbedFactory.ping(gateway_ms=500.0, rest_ms=80.0, db_ms=1.5)
    assert embed_err.color is not None and embed_err.color.value == COLOR_ERROR
    assert "High latency detected." in (embed_err.description or "")


def test_ping_embed_voice_and_resolver_formatting() -> None:
    """Voice and resolver latencies format correctly when present, None, or NaN."""
    # Voice not connected, resolver no data
    embed = EmbedFactory.ping(
        gateway_ms=30.0,
        rest_ms=50.0,
        db_ms=1.0,
        voice_ms=None,
        voice_avg_ms=None,
        resolver_ms=None,
    )
    voice_field = next(f for f in embed.fields if f.name == "Voice")
    resolver_field = next(f for f in embed.fields if f.name == "Resolver")
    assert voice_field.value == "*Not connected*"
    assert resolver_field.value == "*No data yet*"

    # Voice connected with average, resolver with data
    embed_conn = EmbedFactory.ping(
        gateway_ms=30.0,
        rest_ms=50.0,
        db_ms=1.0,
        voice_ms=42.5,
        voice_avg_ms=40.1,
        resolver_ms=125.8,
    )
    voice_f2 = next(f for f in embed_conn.fields if f.name == "Voice")
    resolver_f2 = next(f for f in embed_conn.fields if f.name == "Resolver")
    assert "`42.5 ms`" in (voice_f2.value or "")
    assert "avg `40.1 ms`" in (voice_f2.value or "")
    assert resolver_f2.value == "`125.8 ms`"


def test_about_embed_structure() -> None:
    """About embed contains mandatory disclaimer, stack components, and metadata."""
    stack = {
        "Python": "3.14.0",
        "discord.py": "2.7.1",
        "yt-dlp": "2026.03.01",
        "ytmusicapi": "1.12.3",
        "FFmpeg": "7.1",
    }
    embed = EmbedFactory.about(
        version="0.1.0",
        uptime_str="1d 4h 12m 30s",
        host_os="Windows 11",
        stack_info=stack,
        owner_id=737486185466691585,
    )

    assert "About Yokai v0.1.0" in (embed.title or "")
    assert "Windows 11" in (embed.description or "")
    assert "<@737486185466691585>" in (embed.description or "")

    field_names = [f.name for f in embed.fields]
    for comp in stack:
        assert comp in field_names

    notice_field = next(f for f in embed.fields if f.name == "Notice")
    assert "Unaffiliated fan project inspired by Echo's Yokai drone" in (notice_field.value or "")
    assert "© Ubisoft" in (notice_field.value or "")


def test_diag_embed_error_truncation() -> None:
    """Diag embed handles empty errors and clamps long error lists within limits."""
    # Empty errors
    embed_clean = EmbedFactory.diag(diagnostics={"Status": "OK"}, recent_errors=[])
    err_field = next(f for f in embed_clean.fields if "Recent Exceptions" in (f.name or ""))
    assert err_field.value == "*No errors recorded.*"

    # Many errors exceeding character limits
    giant_errors = [f"Error #{i}: " + ("bad token " * 20) for i in range(30)]
    embed_full = EmbedFactory.diag(diagnostics={"Status": "Degraded"}, recent_errors=giant_errors)
    assert _total_embed_length(embed_full) <= 6000
    err_field_full = next(f for f in embed_full.fields if "Recent Exceptions" in (f.name or ""))
    assert len(err_field_full.value or "") <= 1024


def test_format_uptime_helper() -> None:
    """_format_uptime correctly outputs days, hours, minutes, seconds."""
    assert _format_uptime(45) == "45s"
    assert _format_uptime(125) == "2m 5s"
    assert _format_uptime(3665) == "1h 1m 5s"
    assert _format_uptime(90061) == "1d 1h 1m 1s"


# ---------------------------------------------------------------------------
# 3. Telemetry and DiagView Tests
# ---------------------------------------------------------------------------


def test_ytdlp_resolver_rolling_latency() -> None:
    """YtDlpResolver correctly calculates rolling average latency over up to 10 samples."""
    resolver = YtDlpResolver()
    assert resolver.get_average_latency() is None

    resolver.resolve_latencies.append(100.0)
    resolver.resolve_latencies.append(200.0)
    assert resolver.get_average_latency() == 150.0

    # Maxlen 10 eviction
    for _ in range(12):
        resolver.resolve_latencies.append(50.0)
    assert len(resolver.resolve_latencies) == 10
    assert resolver.get_average_latency() == 50.0


@pytest.mark.asyncio
async def test_diag_view_permissions() -> None:
    """DiagView restricts interactions to the configured owner ID."""
    owner_id = 1111111111
    view = DiagView(owner_id=owner_id)

    # Authorized owner
    mock_owner = MagicMock()
    mock_owner.user.id = owner_id
    mock_owner.response.is_done.return_value = False
    assert await view.interaction_check(mock_owner) is True

    # Unauthorized user
    mock_intruder = MagicMock()
    mock_intruder.user.id = 9999999999
    mock_intruder.response.is_done.return_value = False
    mock_intruder.response.send_message = AsyncMock()
    assert await view.interaction_check(mock_intruder) is False
    mock_intruder.response.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_diag_view_update_ytdlp_execution() -> None:
    """DiagView runs pip update as argument list without shell=True."""
    owner_id = 1111111111
    view = DiagView(owner_id=owner_id)

    mock_interaction = MagicMock()
    mock_interaction.user.id = owner_id
    mock_interaction.response.defer = AsyncMock()
    mock_interaction.followup.send = AsyncMock()
    button = view.children[0]
    assert isinstance(button, discord.ui.Button)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[sys.executable, "-m", "pip", "install", "-U", "yt-dlp[default]"],
            returncode=0,
            stdout="Successfully installed yt-dlp",
            stderr="",
        )

        await view.update_ytdlp_button.callback(mock_interaction)

        # Verify subprocess.run call
        mock_run.assert_called_once()
        args, kwargs = mock_run.call_args
        assert args[0] == [sys.executable, "-m", "pip", "install", "-U", "yt-dlp[default]"]
        assert kwargs.get("shell") is not True

        # Verify button is disabled
        assert button.disabled is True

        # Verify owner received success message
        mock_interaction.followup.send.assert_awaited_once()
        call_kwargs = mock_interaction.followup.send.call_args[1]
        sent_embed = call_kwargs.get("embed")
        assert sent_embed is not None
        assert "yt-dlp Updated" in (sent_embed.title or "")
        assert "Restart Required" in (sent_embed.description or "")


# ---------------------------------------------------------------------------
# 4. MetaCog Command Authorization & Execution Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_diag_command_access_denial_for_non_owner() -> None:
    """MetaCog.diag immediately sends permission denial for non-owners."""
    mock_bot = MagicMock()
    mock_bot.config.owner_id = 123456789
    mock_bot.user.display_avatar.url = "https://example.com/avatar.png"

    cog = MetaCog(mock_bot)

    mock_interaction = MagicMock()
    mock_interaction.user.id = 999999999  # Not owner
    mock_interaction.response.is_done.return_value = False
    mock_interaction.response.send_message = AsyncMock()

    await cog.diag.callback(cog, mock_interaction)

    mock_interaction.response.send_message.assert_awaited_once()
    embed = mock_interaction.response.send_message.call_args[1]["embed"]
    assert "Access denied" in (embed.description or "")
    assert mock_interaction.response.send_message.call_args[1]["ephemeral"] is True


@pytest.mark.asyncio
async def test_diag_command_owner_execution() -> None:
    """MetaCog.diag gathers telemetry and attaches DiagView for the bot owner."""
    mock_bot = MagicMock()
    mock_bot.config.owner_id = 123456789
    mock_bot.config.db_path = ":memory:"
    mock_bot.user.display_avatar.url = "https://example.com/avatar.png"
    mock_bot.spotify.get_provider_status.return_value = {
        "official_configured": True,
        "official_circuit_available": True,
        "official_failures": 0,
        "scraper_enabled": True,
        "scraper_circuit_available": True,
        "scraper_failures": 0,
    }
    mock_bot.resolver.consecutive_failures = 0
    mock_bot.resolver.is_degraded = False
    mock_bot.health.davey_available = True
    mock_bot.health.davey_version = "1.0.0"
    mock_bot.health.ffmpeg_path = "C:/ffmpeg/ffmpeg.exe"
    mock_bot.health.ffmpeg_version = "7.1"
    mock_bot.health.deno_path = "C:/deno/deno.exe"
    mock_bot.health.deno_version = "2.0.0"
    mock_bot.health.ytdlp_version = "2026.03.01"
    mock_bot.health.ytdlp_age_days = 5
    mock_bot.health.ytdlp_outdated = False
    mock_bot.error_ring_buffer = ["Error 1"]
    mock_bot.resolver.error_ring_buffer = ["Error 2"]

    cog = MetaCog(mock_bot)
    mock_interaction = MagicMock()
    mock_interaction.user.id = 123456789  # Owner
    mock_interaction.response.defer = AsyncMock()
    mock_interaction.response.is_done.return_value = True
    mock_interaction.followup.send = AsyncMock()

    await cog.diag.callback(cog, mock_interaction)
    mock_interaction.followup.send.assert_awaited_once()
    call_kwargs = mock_interaction.followup.send.call_args[1]
    embed = call_kwargs["embed"]
    assert "Diagnostic Telemetry" in (embed.title or "")
    assert isinstance(call_kwargs.get("view"), DiagView)


@pytest.mark.asyncio
async def test_ping_command_execution() -> None:
    """MetaCog.ping gathers latencies and sends ping embed."""
    mock_bot = MagicMock()
    mock_bot.latency = 0.05
    mock_bot.application_info = AsyncMock()
    mock_bot.db.ping = AsyncMock(return_value=1.5)
    mock_bot.resolver.get_average_latency = MagicMock(return_value=120.0)
    mock_bot.user.display_avatar.url = "https://example.com/avatar.png"

    cog = MetaCog(mock_bot)
    mock_interaction = MagicMock()
    mock_interaction.guild.voice_client = None
    mock_interaction.response.defer = AsyncMock()
    mock_interaction.response.is_done.return_value = True
    mock_interaction.followup.send = AsyncMock()

    await cog.ping.callback(cog, mock_interaction)
    mock_interaction.followup.send.assert_awaited_once()
    embed = mock_interaction.followup.send.call_args[1]["embed"]
    assert "System Latencies" in (embed.title or "")


@pytest.mark.asyncio
async def test_usage_command_execution() -> None:
    """MetaCog.usage filters by period and renders leaderboard."""
    mock_bot = MagicMock()
    mock_bot.db.get_usage_stats = AsyncMock(return_value=([(100, 5, 2)], [("play", 4)]))
    mock_bot.user.display_avatar.url = "https://example.com/avatar.png"

    cog = MetaCog(mock_bot)
    mock_interaction = MagicMock()
    mock_interaction.response.defer = AsyncMock()
    mock_interaction.response.is_done.return_value = True
    mock_interaction.followup.send = AsyncMock()

    await cog.usage.callback(cog, mock_interaction, period="7d")
    mock_interaction.followup.send.assert_awaited_once()
    mock_bot.db.get_usage_stats.assert_awaited_once_with(period_days=7)
    embed = mock_interaction.followup.send.call_args[1]["embed"]
    assert "Yokai Usage Statistics" in (embed.title or "")
    assert "Last 7 Days" in (embed.description or "")


@pytest.mark.asyncio
async def test_about_command_execution() -> None:
    """MetaCog.about calculates uptime and formats credits embed."""
    mock_bot = MagicMock()
    mock_bot.start_time = time.time() - 3600
    mock_bot.config.owner_id = 737486185466691585
    mock_bot.health.ytdlp_version = "2026.03.01"
    mock_bot.health.ffmpeg_version = "7.1"
    mock_bot.user.display_avatar.url = "https://example.com/avatar.png"

    cog = MetaCog(mock_bot)
    mock_interaction = MagicMock()
    mock_interaction.response.defer = AsyncMock()
    mock_interaction.response.is_done.return_value = True
    mock_interaction.followup.send = AsyncMock()

    await cog.about.callback(cog, mock_interaction)
    mock_interaction.followup.send.assert_awaited_once()
    embed = mock_interaction.followup.send.call_args[1]["embed"]
    assert "About Yokai" in (embed.title or "")


@pytest.mark.asyncio
async def test_bot_error_ring_buffer_on_tree_error() -> None:
    """YokaiBot records sanitized errors to error_ring_buffer on app command error."""
    from yokai.bot import YokaiBot
    from yokai.config import Config

    cfg = Config(
        discord_token="fake_token",
        guild_id=123,
        owner_id=456,
        db_path=":memory:",
    )
    health = MagicMock()
    health.voice_capable = True
    bot = YokaiBot(cfg, health_report=health)

    assert len(bot.error_ring_buffer) == 0

    mock_interaction = MagicMock()
    mock_interaction.command.qualified_name = "test_cmd"
    mock_interaction.user.id = 789
    mock_interaction.user.display_avatar.url = "https://example.com/avatar.png"
    mock_interaction.response.is_done.return_value = False
    mock_interaction.response.send_message = AsyncMock()

    # Simulate command error
    err = discord.app_commands.AppCommandError("Simulated failure")
    with patch.object(bot.db, "log_command", new=AsyncMock()):
        await bot.on_tree_error(mock_interaction, err)

    assert len(bot.error_ring_buffer) == 1
    assert "test_cmd" in bot.error_ring_buffer[0]
    assert "Simulated failure" in bot.error_ring_buffer[0]
