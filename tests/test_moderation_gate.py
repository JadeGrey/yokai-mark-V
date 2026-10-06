"""Tests for the blacklist gate used by the command tree and views."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import discord

from yokai.moderation.gate import YokaiTree, deny_if_blacklisted, screen
from yokai.moderation.names import UserNameCache
from yokai.moderation.store import ModerationStore
from yokai.music.models import MusicQueue, Track
from yokai.music.player import GuildPlayer
from yokai.ui.views import BaseView, RecommendView


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


async def test_blacklisted_user_is_refused_by_a_live_base_view() -> None:
    """Review Focus 5: an old button on a still-live view cannot be used once blacklisted."""
    view = BaseView(allowed_user_ids=None)  # open to everyone, so only the gate can refuse
    interaction = _interaction(5, _store(frozenset({5})))
    with patch("yokai.moderation.gate.send", new=AsyncMock()) as sent:
        assert await view.interaction_check(interaction) is False
    sent.assert_awaited_once()


async def test_non_blacklisted_user_still_passes_an_open_base_view() -> None:
    view = BaseView(allowed_user_ids=None)
    interaction = _interaction(10, _store(frozenset({5})))
    assert await view.interaction_check(interaction) is True


async def test_blacklisted_requester_is_refused_by_the_recommend_view() -> None:
    bot = MagicMock()
    bot.config.max_queue_size = 50
    bot.presence = AsyncMock()
    player = GuildPlayer(guild_id=1, bot=bot, resolver=MagicMock())
    player.queue = MusicQueue(max_size=50)
    view = RecommendView(
        player=player,
        tracks=[Track(video_id="r1", title="Rec 1", duration_s=100)],
        requester_id=5,
        seed_title="Seed",
    )
    interaction = _interaction(5, _store(frozenset({5})))
    interaction.user = MagicMock(spec=discord.Member, id=5)
    with patch("yokai.moderation.gate.send", new=AsyncMock()):
        assert await view.interaction_check(interaction) is False


def _named_interaction(user_id: int, name: str, store: object, names: object) -> MagicMock:
    interaction = _interaction(user_id, store)
    interaction.user = MagicMock(id=user_id, display_name=name)
    interaction.client.names = names
    return interaction


def _name_db() -> MagicMock:
    db = MagicMock()
    db.upsert_user_name = AsyncMock()
    return db


async def test_screen_records_the_name_of_an_allowed_user() -> None:
    db = _name_db()
    interaction = _named_interaction(10, "Sami", _store(frozenset({5})), UserNameCache(db))
    assert await screen(interaction) is False
    db.upsert_user_name.assert_awaited_once_with(10, "Sami")


async def test_screen_does_not_record_or_allow_a_blacklisted_user() -> None:
    db = _name_db()
    interaction = _named_interaction(5, "Mallory", _store(frozenset({5})), UserNameCache(db))
    with patch("yokai.moderation.gate.send", new=AsyncMock()):
        assert await screen(interaction) is True
    db.upsert_user_name.assert_not_awaited()


async def test_screen_works_when_the_client_has_no_name_cache() -> None:
    interaction = _named_interaction(10, "Sami", _store(frozenset()), MagicMock())
    assert await screen(interaction) is False


async def test_screen_survives_a_database_error_while_recording() -> None:
    db = _name_db()
    db.upsert_user_name.side_effect = ValueError("closed")
    interaction = _named_interaction(10, "Sami", _store(frozenset()), UserNameCache(db))
    assert await screen(interaction) is False  # the command still runs


async def test_a_live_view_records_names_too() -> None:
    db = _name_db()
    view = BaseView(allowed_user_ids=None)
    interaction = _named_interaction(10, "Sami", _store(frozenset()), UserNameCache(db))
    assert await view.interaction_check(interaction) is True
    db.upsert_user_name.assert_awaited_once_with(10, "Sami")
