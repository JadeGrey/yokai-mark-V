"""Tests for the /owner command group."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from yokai.cogs import owner as owner_module
from yokai.cogs.owner import OwnerCog


def test_the_owner_group_is_named_owner() -> None:
    assert OwnerCog.owner.name == "owner"


@pytest.mark.asyncio
async def test_an_empty_owner_group_is_not_registered() -> None:
    """Review Focus: Discord rejects a command group with no subcommands."""
    bot = MagicMock()
    bot.add_cog = AsyncMock()
    assert not OwnerCog.owner.commands  # nothing owner-only exists yet

    await owner_module.setup(bot)

    bot.add_cog.assert_not_awaited()
