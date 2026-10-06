"""Blacklist enforcement for slash commands, autocomplete and component interactions."""

from __future__ import annotations

import logging
from typing import Optional

import discord
from discord import app_commands

from yokai.moderation.names import UserNameCache
from yokai.moderation.store import ModerationStore
from yokai.ui.embeds import EmbedFactory, send

logger = logging.getLogger(__name__)


def blocked_embed(bot_avatar_url: Optional[str] = None) -> discord.Embed:
    """Plain, ephemeral-friendly explanation shown to a blacklisted user."""
    return EmbedFactory.error(
        message="You're blocked from using Yokai.",
        title="Access blocked",
        user_hint="If you think this is a mistake, ask the bot owner.",
        context="Permissions",
        bot_avatar_url=bot_avatar_url,
        include_quip=False,
    )


def _store_of(interaction: discord.Interaction) -> Optional[ModerationStore]:
    store = getattr(interaction.client, "moderation", None)
    return store if isinstance(store, ModerationStore) else None


async def deny_if_blacklisted(interaction: discord.Interaction) -> bool:
    """Return True if the user is blacklisted, after telling them (when Discord allows it).

    A client with no real `ModerationStore` is treated as "nobody is blacklisted".
    """
    store = _store_of(interaction)
    if store is None or not await store.is_blacklisted(interaction.user.id):
        return False

    logger.info("Blocked blacklisted user %d", interaction.user.id)
    # Autocomplete interactions accept only choices, never a message.
    if interaction.type is not discord.InteractionType.autocomplete:
        client_user = interaction.client.user
        avatar_url = str(client_user.display_avatar.url) if client_user else None
        await send(interaction, embed=blocked_embed(avatar_url), ephemeral=True)
    return True


async def screen(interaction: discord.Interaction) -> bool:
    """Run everything an interaction must pass; True means stop (the user is blacklisted).

    Also remembers the user's current display name for the admin panel.
    """
    if await deny_if_blacklisted(interaction):
        return True
    names = getattr(interaction.client, "names", None)
    if isinstance(names, UserNameCache):
        user = interaction.user
        await names.touch(user.id, getattr(user, "display_name", None) or getattr(user, "name", ""))
    return False


class YokaiTree(app_commands.CommandTree):
    """Command tree whose global check rejects blacklisted users before any command runs."""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return not await screen(interaction)
