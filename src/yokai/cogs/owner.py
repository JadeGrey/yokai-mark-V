"""Owner-only slash commands, grouped under /owner."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from discord import app_commands
from discord.ext import commands

if TYPE_CHECKING:
    from yokai.bot import YokaiBot

logger = logging.getLogger(__name__)


class OwnerCog(commands.Cog, name="Owner"):
    """Tools only the bot owner may use, all under the /owner group.

    Subcommands must check `bot.config.owner_id` themselves.
    """

    owner = app_commands.Group(name="owner", description="Owner-only tools.")

    def __init__(self, bot: YokaiBot) -> None:
        self.bot = bot


async def setup(bot: YokaiBot) -> None:
    """Extension setup entrypoint; an empty group is skipped because Discord rejects it."""
    if not OwnerCog.owner.commands:
        logger.info("No /owner subcommands yet; the group is not registered.")
        return
    await bot.add_cog(OwnerCog(bot))
