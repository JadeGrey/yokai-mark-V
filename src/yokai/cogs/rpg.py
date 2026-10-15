"""Guild RPG slash commands: /guild join and /card (more arrive in later phases)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

from yokai.errors import NoCharacterError
from yokai.rpg.content import Content
from yokai.rpg.runtime import RpgRuntime
from yokai.rpg.stats import format_stats, stats_at_level
from yokai.ui.embeds import EmbedFactory, send

if TYPE_CHECKING:
    from yokai.bot import YokaiBot

logger = logging.getLogger(__name__)


def _avatar_of(member: discord.abc.User) -> Optional[str]:
    """The member's avatar URL, or None when it is not a plain string (tests, odd objects)."""
    url = getattr(getattr(member, "display_avatar", None), "url", None)
    return url if isinstance(url, str) else None


def _promotion(content: Content, rank_id: str) -> tuple[Optional[tuple[str, int]], bool]:
    """(next rank as (id, min level), reached the top). Both empty if the rank is unknown."""
    try:
        upcoming = content.next_rank(rank_id)
    except KeyError:
        logger.warning("A character has rank %r, which is not in the content files.", rank_id)
        return None, False
    if upcoming is None:
        return None, True
    return (upcoming.id, upcoming.min_level), False


class RpgCog(commands.Cog, name="Guild"):
    """The adventurers' guild: create a character and view player cards."""

    guild_group = app_commands.Group(name="guild", description="The adventurers' guild.")

    def __init__(self, bot: YokaiBot) -> None:
        self.bot = bot

    def _avatar_url(self) -> Optional[str]:
        return str(self.bot.user.display_avatar.url) if self.bot.user else None

    def _runtime(self) -> RpgRuntime:
        runtime = self.bot.rpg
        if runtime is None:
            raise RuntimeError("The Guild cog was loaded without an RPG runtime.")
        return runtime

    async def _send_domain_error(self, interaction: discord.Interaction, err: Exception) -> None:
        title = getattr(err, "title", "That didn't work")
        message = getattr(err, "message", str(err))
        hint = getattr(err, "user_hint", None)
        embed = EmbedFactory.error(
            message=message,
            title=title,
            user_hint=hint,
            context="Guild",
            bot_avatar_url=self._avatar_url(),
        )
        await send(interaction, embed=embed, ephemeral=True)

    @guild_group.command(name="join", description="Join the adventurers' guild.")
    async def join(self, interaction: discord.Interaction) -> None:
        """Joining twice is answered before deferring so it stays ephemeral."""
        runtime = self._runtime()
        user_id = interaction.user.id
        if await runtime.store.get_character(user_id) is not None:
            embed = EmbedFactory.info(
                title="Already on the roster",
                description="You've already joined the guild. Use /card to see your card.",
                context="Guild",
                bot_avatar_url=self._avatar_url(),
            )
            await send(interaction, embed=embed, ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        character, _ = await runtime.store.create_character(user_id, runtime.content.ranks[0].id)
        stats = stats_at_level(runtime.content.levels, character.level)
        embed = EmbedFactory.guild_joined(
            user_mention=f"<@{user_id}>",
            rank=character.rank,
            level=character.level,
            stats=format_stats(stats),
            bot_avatar_url=self._avatar_url(),
        )
        await send(interaction, embed=embed)

    @app_commands.command(name="card", description="Show an adventurer's player card.")
    @app_commands.describe(member="Whose card to show (defaults to you)")
    async def card(
        self, interaction: discord.Interaction, member: Optional[discord.Member] = None
    ) -> None:
        """Anyone can view anyone's card. Missing characters are answered before deferring."""
        runtime = self._runtime()
        target = member or interaction.user
        character = None if target.bot else await runtime.store.get_character(target.id)
        if character is None:
            await self._send_domain_error(
                interaction, NoCharacterError(own=target.id == interaction.user.id)
            )
            return

        await interaction.response.defer(thinking=True)
        content = runtime.content
        next_rank, top_rank = _promotion(content, character.rank)
        embed = EmbedFactory.player_card(
            user_mention=f"<@{target.id}>",
            rank=character.rank,
            level=character.level,
            level_cap=content.levels.cap,
            stats=format_stats(stats_at_level(content.levels, character.level)),
            wins=character.battles_won,
            losses=character.battles_lost,
            joined_ts=character.created_ts,
            next_rank=next_rank,
            top_rank=top_rank,
            thumbnail_url=_avatar_of(target),
            bot_avatar_url=self._avatar_url(),
        )
        await send(interaction, embed=embed)


async def setup(bot: YokaiBot) -> None:
    """Extension setup entrypoint."""
    await bot.add_cog(RpgCog(bot))
