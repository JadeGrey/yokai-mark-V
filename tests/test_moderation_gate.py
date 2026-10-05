"""Tests for the blacklist gate used by the command tree and views."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import discord

from yokai.moderation.gate import YokaiTree, deny_if_blacklisted
from yokai.moderation.store import ModerationStore


def _store(black: frozenset[int]) -> ModerationStore:
    db = MagicMock()
    db.get_moderation_lists = AsyncMock(return_value=(black, frozenset()))
    return ModerationStore(db, owner_id=1)


def _interaction(user_id: int, store: object | None) -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock(id=user_id)
    interaction.type = discord.InteractionType.application_command
    interaction.client = MagicMock()
    interaction.client.user = None
    interaction.client.moderation = store
    return interaction


async def test_allowed_user_passes_without_a_reply() -> None:
    interaction = _interaction(10, _store(frozenset({5})))
    with patch("yokai.moderation.gate.send", new=AsyncMock()) as sent:
        assert await deny_if_blacklisted(interaction) is False
    sent.assert_not_awaited()


async def test_blacklisted_user_gets_an_ephemeral_blocked_embed() -> None:
    interaction = _interaction(5, _store(frozenset({5})))
    with patch("yokai.moderation.gate.send", new=AsyncMock()) as sent:
        assert await deny_if_blacklisted(interaction) is True
    embed = sent.await_args.kwargs["embed"]
    assert "Access blocked" in (embed.title or "")
    assert sent.await_args.kwargs["ephemeral"] is True


async def test_blacklisted_autocomplete_is_denied_without_a_reply() -> None:
    """Review Focus 3: autocomplete cannot receive a message, so just deny quietly."""
    interaction = _interaction(5, _store(frozenset({5})))
    interaction.type = discord.InteractionType.autocomplete
    with patch("yokai.moderation.gate.send", new=AsyncMock()) as sent:
        assert await deny_if_blacklisted(interaction) is True
    sent.assert_not_awaited()


async def test_a_client_without_a_real_store_is_never_denied() -> None:
    interaction = _interaction(5, MagicMock())  # e.g. a MagicMock bot in unrelated tests
    with patch("yokai.moderation.gate.send", new=AsyncMock()) as sent:
        assert await deny_if_blacklisted(interaction) is False
    sent.assert_not_awaited()


async def test_tree_interaction_check_follows_the_gate() -> None:
    tree = YokaiTree(discord.Client(intents=discord.Intents.none()))
    blocked = _interaction(5, _store(frozenset({5})))
    allowed = _interaction(10, _store(frozenset({5})))
    with patch("yokai.moderation.gate.send", new=AsyncMock()):
        assert await tree.interaction_check(blocked) is False
        assert await tree.interaction_check(allowed) is True
