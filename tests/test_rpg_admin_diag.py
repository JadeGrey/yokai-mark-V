"""Tests for the Guild RPG line in /admin diag."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tests.test_admin_cog import make_diag_bot, make_followup_interaction
from yokai.cogs.admin import AdminCog
from yokai.theme import COLOR_SUCCESS, COLOR_WARNING


def rpg_bot(*, threads: bool | None) -> MagicMock:
    bot = make_diag_bot()
    bot.user = None
    bot.rpg.content.missions = {f"m{i}": object() for i in range(28)}
    bot.rpg.content.enemies = {f"e{i}": object() for i in range(16)}
    bot.rpg.active_users = {1, 2}
    if threads is None:
        bot.get_guild.return_value = None
    else:
        bot.get_guild.return_value.me.guild_permissions.create_public_threads = threads
        bot.get_guild.return_value.me.guild_permissions.send_messages_in_threads = threads
    return bot


async def run_diag(bot: MagicMock) -> MagicMock:
    cog = AdminCog(bot)
    interaction = make_followup_interaction(123456789)
    await cog.diag.callback(cog, interaction)
    return interaction


def rpg_field(interaction: MagicMock) -> str:
    embed = interaction.followup.send.call_args[1]["embed"]
    return next(f.value for f in embed.fields if f.name == "Guild RPG")


@pytest.mark.asyncio
async def test_diag_reports_content_counts_and_thread_permissions() -> None:
    interaction = await run_diag(rpg_bot(threads=True))
    text = rpg_field(interaction)
    assert "28 missions" in text and "16 enemies" in text and "2 fighting" in text
    assert "threads: ok" in text
    assert interaction.followup.send.call_args[1]["embed"].color.value == COLOR_SUCCESS


@pytest.mark.asyncio
async def test_diag_warns_when_thread_permissions_are_missing() -> None:
    interaction = await run_diag(rpg_bot(threads=False))
    assert "threads: MISSING" in rpg_field(interaction)
    assert interaction.followup.send.call_args[1]["embed"].color.value == COLOR_WARNING


@pytest.mark.asyncio
async def test_diag_does_not_warn_when_the_server_is_not_cached_yet() -> None:
    interaction = await run_diag(rpg_bot(threads=None))
    assert "threads: unknown" in rpg_field(interaction)
    assert interaction.followup.send.call_args[1]["embed"].color.value == COLOR_SUCCESS


@pytest.mark.asyncio
async def test_diag_says_disabled_when_the_rpg_is_off() -> None:
    bot = make_diag_bot()
    bot.user = None
    bot.rpg = None
    interaction = await run_diag(bot)
    assert rpg_field(interaction) == "Disabled"
