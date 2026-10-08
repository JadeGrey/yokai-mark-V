"""Tests for the /admin command group."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from tests.moderation_helpers import fake_moderation
from yokai.cogs.admin import AdminCog
from yokai.theme import COLOR_WARNING
from yokai.ui.views import DiagView


def make_bot() -> MagicMock:
    bot = MagicMock()
    bot.user = None
    bot.config.owner_id = 999
    bot.moderation = fake_moderation(owner_id=999, admins={555})
    bot.config.economy_welcome_bonus = 200
    return bot


def make_interaction(user_id: int = 111) -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    user.bot = False
    interaction.user = user
    interaction.response.defer = AsyncMock()
    return interaction


def make_target(user_id: int = 222, *, bot: bool = False) -> MagicMock:
    target = MagicMock(spec=discord.Member)
    target.id = user_id
    target.bot = bot
    return target


def test_the_admin_group_is_named_admin() -> None:
    assert AdminCog.admin.name == "admin"


@pytest.mark.asyncio
async def test_economy_refuses_non_admins() -> None:
    bot = make_bot()
    bot.db.admin_adjust_wallet = AsyncMock()
    cog = AdminCog(bot)
    interaction = make_interaction(111)  # neither owner (999) nor admin (555)

    with patch("yokai.cogs.admin.send", new=AsyncMock()) as sent:
        await cog.economy.callback(cog, interaction, user=make_target(), amount=100)

    bot.db.admin_adjust_wallet.assert_not_awaited()
    interaction.response.defer.assert_not_awaited()
    assert sent.await_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_economy_allows_the_owner() -> None:
    bot = make_bot()
    bot.db.admin_adjust_wallet = AsyncMock(return_value=(100, 300))
    cog = AdminCog(bot)
    interaction = make_interaction(999)

    with patch("yokai.cogs.admin.send", new=AsyncMock()) as sent:
        await cog.economy.callback(cog, interaction, user=make_target(), amount=100)

    bot.db.admin_adjust_wallet.assert_awaited_once_with(222, 100, welcome_bonus=200)
    assert sent.await_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_economy_allows_an_adminlist_member() -> None:
    bot = make_bot()
    bot.db.admin_adjust_wallet = AsyncMock(return_value=(100, 300))
    cog = AdminCog(bot)
    interaction = make_interaction(555)  # admin, not the owner

    with patch("yokai.cogs.admin.send", new=AsyncMock()):
        await cog.economy.callback(cog, interaction, user=make_target(), amount=100)

    bot.db.admin_adjust_wallet.assert_awaited_once()


@pytest.mark.asyncio
async def test_economy_rejects_a_bot_target() -> None:
    """Review Focus: /admin economy must not be able to create a wallet for a bot."""
    bot = make_bot()
    bot.db.admin_adjust_wallet = AsyncMock()
    cog = AdminCog(bot)
    interaction = make_interaction(999)

    with patch("yokai.cogs.admin.send", new=AsyncMock()) as sent:
        await cog.economy.callback(cog, interaction, user=make_target(bot=True), amount=100)

    bot.db.admin_adjust_wallet.assert_not_awaited()
    interaction.response.defer.assert_not_awaited()
    assert sent.await_args.kwargs["ephemeral"] is True


def make_diag_bot(*, owner_id: int = 123456789, admins: set[int] | None = None) -> MagicMock:
    bot = MagicMock()
    bot.config.owner_id = owner_id
    bot.moderation = fake_moderation(owner_id=owner_id, admins=admins or set())
    bot.config.db_path = ":memory:"
    bot.user.display_avatar.url = "https://example.com/avatar.png"
    bot.spotify.get_provider_status.return_value = {
        "official_configured": True,
        "official_circuit_available": True,
        "official_failures": 0,
        "scraper_enabled": True,
        "scraper_circuit_available": True,
        "scraper_failures": 0,
    }
    bot.resolver.consecutive_failures = 0
    bot.resolver.is_degraded = False
    bot.health.davey_available = True
    bot.health.davey_version = "1.0.0"
    bot.health.ffmpeg_path = "C:/ffmpeg/ffmpeg.exe"
    bot.health.ffmpeg_version = "7.1"
    bot.health.deno_path = "C:/deno/deno.exe"
    bot.health.deno_version = "2.0.0"
    bot.health.ytdlp_version = "2026.03.01"
    bot.health.ytdlp_age_days = 5
    bot.health.ytdlp_outdated = False
    bot.error_ring_buffer = ["Error 1"]
    bot.resolver.error_ring_buffer = ["Error 2"]
    return bot


def make_followup_interaction(user_id: int) -> MagicMock:
    interaction = MagicMock()
    interaction.user.id = user_id
    interaction.response.defer = AsyncMock()
    interaction.response.is_done.return_value = True
    interaction.followup.send = AsyncMock()
    return interaction


@pytest.mark.asyncio
async def test_diag_denies_non_admins_with_the_admins_only_embed() -> None:
    bot = make_diag_bot()
    cog = AdminCog(bot)
    interaction = MagicMock()
    interaction.user.id = 999999999  # not the owner, not an admin
    interaction.response.is_done.return_value = False
    interaction.response.send_message = AsyncMock()

    await cog.diag.callback(cog, interaction)

    interaction.response.send_message.assert_awaited_once()
    kwargs = interaction.response.send_message.call_args[1]
    assert kwargs["ephemeral"] is True
    assert "Access denied" in (kwargs["embed"].description or "")
    assert "Yokai admins" in (kwargs["embed"].description or "")
    assert "Admins only" in (kwargs["embed"].title or "")


@pytest.mark.asyncio
async def test_diag_runs_for_the_owner_and_attaches_the_diag_view() -> None:
    bot = make_diag_bot()
    cog = AdminCog(bot)
    interaction = make_followup_interaction(123456789)

    await cog.diag.callback(cog, interaction)

    interaction.followup.send.assert_awaited_once()
    kwargs = interaction.followup.send.call_args[1]
    assert "Diagnostic Telemetry" in (kwargs["embed"].title or "")
    assert isinstance(kwargs.get("view"), DiagView)


@pytest.mark.asyncio
async def test_diag_runs_for_an_adminlist_member_and_locks_buttons_to_them() -> None:
    bot = make_diag_bot(admins={555})
    cog = AdminCog(bot)
    interaction = make_followup_interaction(555)  # admin, not the owner

    await cog.diag.callback(cog, interaction)

    view = interaction.followup.send.call_args[1]["view"]
    assert isinstance(view, DiagView)
    assert view.allowed_user_ids == {555}


@pytest.mark.asyncio
async def test_diag_marks_attention_with_status_color_not_extra_emoji() -> None:
    bot = make_diag_bot(owner_id=42)
    bot.user = None
    bot.error_ring_buffer = []
    bot.resolver.error_ring_buffer = []
    bot.resolver.is_degraded = True
    bot.resolver.consecutive_failures = 3
    bot.health.ytdlp_age_days = 40
    bot.health.ytdlp_outdated = True
    cog = AdminCog(bot)
    interaction = MagicMock()
    interaction.user.id = 42
    interaction.response.defer = AsyncMock()
    message = MagicMock(spec=discord.Message)

    with patch("yokai.cogs.admin.send", new=AsyncMock(return_value=message)) as sent:
        await cog.diag.callback(cog, interaction)

    assert sent.await_args.kwargs["view"].message is message  # so on_timeout can disable it
    embed = sent.await_args.kwargs["embed"]
    assert embed.color.value == COLOR_WARNING and "Diagnostic Telemetry" in embed.title
    assert embed.title.startswith("⚠️")
    values = " ".join(f.value for f in embed.fields)
    assert "(outdated)" in values
    assert "⚠️" not in values
