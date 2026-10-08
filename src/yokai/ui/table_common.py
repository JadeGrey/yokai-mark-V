"""Small helpers shared by the table views."""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING, Any, Optional

import discord

from yokai.errors import InvalidGameChoiceError
from yokai.ui.embeds import EmbedFactory, send

if TYPE_CHECKING:
    from yokai.bot import YokaiBot

logger = logging.getLogger(__name__)

NO_MENTIONS = discord.AllowedMentions.none()


def avatar_url(bot: YokaiBot) -> Optional[str]:
    return str(bot.user.display_avatar.url) if bot.user else None


async def reply_error(bot: YokaiBot, interaction: discord.Interaction, err: Exception) -> None:
    """Answer an interaction with an ephemeral error embed built from a domain error."""
    embed = EmbedFactory.error(
        message=getattr(err, "message", str(err)),
        title=getattr(err, "title", "That didn't work"),
        user_hint=getattr(err, "user_hint", None),
        bot_avatar_url=avatar_url(bot),
    )
    await send(interaction, embed=embed, ephemeral=True)


async def dismiss(interaction: discord.Interaction) -> None:
    """Remove the deferred ephemeral 'thinking' reply once the real message is up."""
    with contextlib.suppress(discord.HTTPException):
        await interaction.delete_original_response()


async def delete_message_quietly(channel: Any, message_id: int) -> None:
    """Delete one of the bot's own messages; a message that is already gone is fine."""
    try:
        await channel.get_partial_message(message_id).delete()
    except discord.NotFound:
        pass
    except discord.HTTPException as exc:
        logger.warning("Could not delete message %d: %s", message_id, exc)


def parse_stake(text: str) -> int:
    """Turn the text typed into the stake form into a whole number of Renown."""
    cleaned = text.strip().replace(",", "")
    if not cleaned.isdigit():
        raise InvalidGameChoiceError("Enter your stake as a whole number, like 100.")
    return int(cleaned)
