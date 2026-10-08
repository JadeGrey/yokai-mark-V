"""Tests that the bot wires the searcher, buffer and player together."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from yokai.bot import YokaiBot
from yokai.config import Config
from yokai.errors import NotFoundError
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


@pytest.mark.asyncio
async def test_tree_error_uses_the_domain_error_title() -> None:
    bot = make_bot()
    interaction = MagicMock()
    interaction.command.qualified_name = "play"
    interaction.user.id = 7
    error = discord.app_commands.CommandInvokeError(MagicMock(), NotFoundError())
    with (
        patch.object(bot.db, "log_command", new=AsyncMock()),
        patch("yokai.bot.send", new=AsyncMock()) as sent,
    ):
        await bot.on_tree_error(interaction, error)
    embed = sent.await_args.kwargs["embed"]
    assert embed.title == "❌ Nothing found"
    assert sent.await_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_tree_error_for_unexpected_exceptions_is_generic_but_specific_enough() -> None:
    bot = make_bot()
    interaction = MagicMock()
    interaction.command.qualified_name = "play"
    interaction.user.id = 7
    error = discord.app_commands.CommandInvokeError(MagicMock(), RuntimeError("kaboom"))
    with (
        patch.object(bot.db, "log_command", new=AsyncMock()),
        patch("yokai.bot.send", new=AsyncMock()) as sent,
    ):
        await bot.on_tree_error(interaction, error)
    embed = sent.await_args.kwargs["embed"]
    assert embed.title == "❌ Something broke"
    assert "kaboom" not in (embed.description or "")


@pytest.mark.asyncio
async def test_music_command_completion_credits_renown_within_the_rate_limit() -> None:
    bot = make_bot()
    bot.db.log_command = AsyncMock()
    bot.db.credit_wallet = AsyncMock()
    interaction = MagicMock()
    interaction.user.id = 111
    command = MagicMock()
    command.qualified_name = "play"

    await bot.on_app_command_completion(interaction, command)

    bot.db.credit_wallet.assert_awaited_once_with(
        111,
        bot.config.economy_command_reward,
        reason="command",
        welcome_bonus=bot.config.economy_welcome_bonus,
    )


@pytest.mark.asyncio
async def test_non_music_command_completion_does_not_credit_renown() -> None:
    bot = make_bot()
    bot.db.log_command = AsyncMock()
    bot.db.credit_wallet = AsyncMock()
    interaction = MagicMock()
    interaction.user.id = 111
    command = MagicMock()
    command.qualified_name = "ping"

    await bot.on_app_command_completion(interaction, command)

    bot.db.credit_wallet.assert_not_awaited()


@pytest.mark.asyncio
async def test_music_command_reward_respects_the_rate_limit() -> None:
    bot = make_bot(economy_command_rate_limit=2)
    bot.db.log_command = AsyncMock()
    bot.db.credit_wallet = AsyncMock()
    interaction = MagicMock()
    interaction.user.id = 111
    command = MagicMock()
    command.qualified_name = "skip"

    for _ in range(3):
        await bot.on_app_command_completion(interaction, command)

    assert bot.db.credit_wallet.await_count == 2


@pytest.mark.asyncio
async def test_music_command_completion_skips_credit_when_reward_is_disabled() -> None:
    """Review Focus: ECONOMY_COMMAND_REWARD=0 must skip crediting, not call credit_wallet(0)."""
    bot = make_bot(economy_command_reward=0)
    bot.db.log_command = AsyncMock()
    bot.db.credit_wallet = AsyncMock()
    interaction = MagicMock()
    interaction.user.id = 111
    command = MagicMock()
    command.qualified_name = "play"

    await bot.on_app_command_completion(interaction, command)

    bot.db.credit_wallet.assert_not_awaited()


@pytest.mark.asyncio
async def test_voice_earn_tracker_starts_and_stops_with_the_bot() -> None:
    bot = make_bot()
    bot.db.initialize = AsyncMock()
    bot.db.refund_open_bets = AsyncMock(return_value=0)
    bot.db.close = AsyncMock()
    bot.audio_buffer.start = AsyncMock()
    bot.searcher.warm_up = AsyncMock()
    bot.load_extension = AsyncMock()
    bot.tree.sync = AsyncMock(return_value=[])
    bot.spotify.close = AsyncMock()
    with patch.object(bot.voice_earn_tracker, "start") as start_mock:
        await bot.setup_hook()
    start_mock.assert_called_once()

    with patch.object(bot.voice_earn_tracker, "stop", new=AsyncMock()) as stop_mock:
        await bot.close()
    stop_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_setup_hook_refunds_open_bets_and_loads_the_gambling_cog() -> None:
    """Review Focus: a restart returns every stake left open by the previous process."""
    bot = make_bot()
    bot.db.initialize = AsyncMock()
    bot.db.refund_open_bets = AsyncMock(return_value=3)
    bot.audio_buffer.start = AsyncMock()
    bot.searcher.warm_up = AsyncMock()
    bot.load_extension = AsyncMock()
    bot.tree.sync = AsyncMock(return_value=[])
    with patch.object(bot.voice_earn_tracker, "start"):
        await bot.setup_hook()
    bot.db.refund_open_bets.assert_awaited_once()
    loaded = [call.args[0] for call in bot.load_extension.await_args_list]
    assert "yokai.cogs.gambling" in loaded
    assert not bot.active_games.is_active(1)


@pytest.mark.asyncio
async def test_bot_owns_a_moderation_store_and_the_yokai_tree() -> None:
    from yokai.moderation import ModerationStore, YokaiTree

    bot = make_bot()
    assert isinstance(bot.moderation, ModerationStore)
    assert bot.moderation.owner_id == 456
    assert isinstance(bot.tree, YokaiTree)


@pytest.mark.asyncio
async def test_a_blacklisted_user_is_stopped_at_the_tree() -> None:
    bot = make_bot()
    bot.db.get_moderation_lists = AsyncMock(return_value=(frozenset({7}), frozenset()))
    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock(id=7)
    interaction.type = discord.InteractionType.application_command
    interaction.client = bot
    with patch("yokai.moderation.gate.send", new=AsyncMock()) as sent:
        assert await bot.tree.interaction_check(interaction) is False
    sent.assert_awaited_once()


@pytest.mark.asyncio
async def test_on_ready_starts_the_heartbeat_and_close_stops_it() -> None:
    bot = make_bot()
    bot.db.close = AsyncMock()
    bot.spotify.close = AsyncMock()
    with patch.object(bot.heartbeat, "start") as start_mock:
        await bot.on_ready()
    start_mock.assert_called_once()

    with (
        patch.object(bot.heartbeat, "stop", new=AsyncMock()) as stop_mock,
        patch.object(bot.voice_earn_tracker, "stop", new=AsyncMock()),
    ):
        await bot.close()
    stop_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_the_bot_owns_a_name_cache_and_the_tree_feeds_it() -> None:
    from yokai.moderation import UserNameCache

    bot = make_bot()
    assert isinstance(bot.names, UserNameCache)
    bot.db.get_moderation_lists = AsyncMock(return_value=(frozenset(), frozenset()))
    bot.db.upsert_user_name = AsyncMock()
    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock(id=7, display_name="Sami")
    interaction.type = discord.InteractionType.application_command
    interaction.client = bot
    assert await bot.tree.interaction_check(interaction) is True
    await bot.names.wait_idle()
    bot.db.upsert_user_name.assert_awaited_once_with(7, "Sami")


@pytest.mark.asyncio
async def test_setup_hook_loads_the_admin_and_owner_extensions() -> None:
    bot = make_bot()
    bot.db.initialize = AsyncMock()
    bot.db.refund_open_bets = AsyncMock(return_value=0)
    bot.audio_buffer.start = AsyncMock()
    bot.searcher.warm_up = AsyncMock()
    bot.load_extension = AsyncMock()
    bot.tree.sync = AsyncMock(return_value=[])
    with patch.object(bot.voice_earn_tracker, "start"):
        await bot.setup_hook()
    loaded = [call.args[0] for call in bot.load_extension.await_args_list]
    assert "yokai.cogs.admin" in loaded
    assert "yokai.cogs.owner" in loaded


@pytest.mark.asyncio
async def test_the_bot_owns_a_table_manager_wired_from_config() -> None:
    from yokai.gambling.tables import TableManager

    bot = make_bot()
    assert isinstance(bot.tables, TableManager)
    assert bot.tables.active_games is bot.active_games
    assert bot.tables.rake_percent == bot.config.gambling_duel_rake_percent
    assert bot.tables.join_seconds == bot.config.gambling_lobby_join_seconds
    assert bot.tables.pick_seconds == bot.config.gambling_turn_seconds


@pytest.mark.asyncio
async def test_setup_hook_loads_the_tables_extension() -> None:
    bot = make_bot()
    bot.db.initialize = AsyncMock()
    bot.db.refund_open_bets = AsyncMock(return_value=0)
    bot.audio_buffer.start = AsyncMock()
    bot.searcher.warm_up = AsyncMock()
    bot.load_extension = AsyncMock()
    bot.tree.sync = AsyncMock(return_value=[])
    with patch.object(bot.voice_earn_tracker, "start"):
        await bot.setup_hook()
    loaded = [call.args[0] for call in bot.load_extension.await_args_list]
    assert "yokai.cogs.tables" in loaded


@pytest.mark.asyncio
async def test_on_ready_reconciles_the_table_channels_once() -> None:
    bot = make_bot()
    cog = MagicMock()
    cog.reconcile = AsyncMock()
    bot.get_cog = MagicMock(return_value=cog)  # type: ignore[method-assign]
    with patch.object(bot.heartbeat, "start"):
        await bot.on_ready()
        await bot.on_ready()
    cog.reconcile.assert_awaited_once()


@pytest.mark.asyncio
async def test_the_table_manager_gets_the_jackpot_and_crash_settings() -> None:
    bot = make_bot()
    assert bot.tables.jackpot_rake_percent == bot.config.gambling_jackpot_rake_percent
    assert bot.tables.crash_max_multiplier == bot.config.gambling_crash_max_multiplier


@pytest.mark.asyncio
async def test_setup_hook_loads_the_lottery_extension() -> None:
    bot = make_bot()
    bot.db.initialize = AsyncMock()
    bot.db.refund_open_bets = AsyncMock(return_value=0)
    bot.audio_buffer.start = AsyncMock()
    bot.searcher.warm_up = AsyncMock()
    bot.load_extension = AsyncMock()
    bot.tree.sync = AsyncMock(return_value=[])
    with patch.object(bot.voice_earn_tracker, "start"):
        await bot.setup_hook()
    loaded = [call.args[0] for call in bot.load_extension.await_args_list]
    assert "yokai.cogs.lottery" in loaded
