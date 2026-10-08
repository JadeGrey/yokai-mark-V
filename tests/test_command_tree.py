"""The registered slash-command tree has the shape the owner asked for."""

from __future__ import annotations

import pytest
from discord import app_commands

from tests.test_bot_wiring import make_bot
from yokai.cogs import admin, economy, meta, owner


async def _loaded_tree() -> app_commands.CommandTree:
    # Call each extension's setup() directly: bot.load_extension re-executes the module and
    # swaps it in sys.modules, which breaks other tests' patch("yokai.cogs.<name>.send").
    bot = make_bot()
    for module in (meta, economy, admin, owner):
        await module.setup(bot)
    return bot.tree


@pytest.mark.asyncio
async def test_admin_commands_live_under_the_admin_group() -> None:
    tree = await _loaded_tree()
    admin = tree.get_command("admin")
    assert isinstance(admin, app_commands.Group)
    assert {c.name for c in admin.commands} == {
        "economy",
        "diag",
        "set_multiplayer",
        "unset_multiplayer",
    }


@pytest.mark.asyncio
async def test_the_old_top_level_admin_commands_are_gone() -> None:
    tree = await _loaded_tree()
    assert tree.get_command("diag") is None
    assert tree.get_command("economy-admin") is None


@pytest.mark.asyncio
async def test_the_empty_owner_group_is_not_in_the_tree() -> None:
    tree = await _loaded_tree()
    assert tree.get_command("owner") is None


@pytest.mark.asyncio
async def test_admin_subcommand_descriptions_fit_discords_limit() -> None:
    tree = await _loaded_tree()
    admin = tree.get_command("admin")
    assert isinstance(admin, app_commands.Group)
    assert all(0 < len(c.description) <= 100 for c in admin.commands)


def test_admin_commands_never_earn_the_music_reward() -> None:
    """Admin commands are logged as 'admin diag' / 'admin economy', not music commands."""
    from yokai.cogs.music import MUSIC_COMMAND_NAMES

    assert not {"admin diag", "admin economy", "diag", "economy-admin"} & set(MUSIC_COMMAND_NAMES)
