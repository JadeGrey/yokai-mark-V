"""The registered slash-command tree has the shape the owner asked for."""

from __future__ import annotations

import pytest
from discord import app_commands

from tests.test_bot_wiring import make_bot
from yokai.cogs import admin, economy, gear, meta, owner, rpg, trade


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
        "challenges",
        "rpg",
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


async def _rpg_tree() -> app_commands.CommandTree:
    bot = make_bot()
    for module in (economy, admin, rpg, gear, trade):
        await module.setup(bot)
    return bot.tree


@pytest.mark.asyncio
async def test_the_guild_rpg_commands_have_the_agreed_shape() -> None:
    tree = await _rpg_tree()
    guild = tree.get_command("guild")
    gear_group = tree.get_command("gear")
    card = tree.get_command("card")
    assert isinstance(guild, app_commands.Group) and isinstance(gear_group, app_commands.Group)
    assert isinstance(card, app_commands.Command)
    trade_command = tree.get_command("trade")
    assert isinstance(trade_command, app_commands.Command)
    assert [p.name for p in trade_command.parameters] == ["member"]
    assert {c.name for c in guild.commands} == {"join", "board", "mission", "rank", "leaderboard"}
    assert {c.name for c in gear_group.commands} == {
        "inventory", "equip", "unequip", "repair", "shop", "buy", "sell",
    }  # fmt: skip
    admin_group = tree.get_command("admin")
    assert isinstance(admin_group, app_commands.Group)
    rpg_group = next(c for c in admin_group.commands if c.name == "rpg")
    assert isinstance(rpg_group, app_commands.Group)
    assert {c.name for c in rpg_group.commands} == {"grant", "level", "rank", "reset"}


@pytest.mark.asyncio
async def test_every_rpg_command_fits_discords_limits() -> None:
    tree = await _rpg_tree()
    commands = list(tree.walk_commands())
    assert len(tree.get_commands()) < 100
    for command in commands:
        assert 0 < len(command.description) <= 100, command.qualified_name
        assert len(command.qualified_name) <= 100
        if isinstance(command, app_commands.Command):
            assert len(command.parameters) <= 25
            for parameter in command.parameters:
                assert 0 < len(parameter.description) <= 100, (
                    f"{command.qualified_name}.{parameter.name}"
                )
