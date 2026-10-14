"""/admin challenges: admins only; list, reroll and set; bad input changes nothing."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from discord import app_commands

from tests.challenge_helpers import tpl
from tests.moderation_helpers import fake_moderation
from tests.test_bot_wiring import make_bot
from yokai.cogs.admin import AdminCog


def _setup(user_id: int = 456):
    bot = make_bot()
    bot.moderation = fake_moderation(owner_id=456)
    daily = [tpl("d_a", "tracks", 5, 10), tpl("d_b", "voice_minutes", 5, 10, category="voice")]
    bot.challenges.active_slate = AsyncMock(return_value=("2026-10-13", daily))
    bot.challenges.reroll = AsyncMock(return_value=daily[:1])
    bot.challenges.set_slate = AsyncMock(return_value=daily[1:])
    interaction = MagicMock()
    interaction.user.id = user_id
    interaction.response.defer = AsyncMock()
    interaction.response.is_done.return_value = False
    return bot, AdminCog(bot), interaction


def _choice(value: str) -> app_commands.Choice[str]:
    return app_commands.Choice(name=value, value=value)


async def _run(cog, interaction, action: str, templates=None, period: str = "daily"):
    with patch("yokai.cogs.admin.send", new=AsyncMock()) as send:
        await cog.challenges.callback(cog, interaction, _choice(period), _choice(action), templates)
    return send


async def test_non_admins_are_refused_ephemerally_and_nothing_changes() -> None:
    bot, cog, interaction = _setup(user_id=999)
    send = await _run(cog, interaction, "reroll")
    assert send.await_args.kwargs["ephemeral"] is True
    bot.challenges.reroll.assert_not_awaited()
    interaction.response.defer.assert_not_awaited()


async def test_list_shows_the_active_slate_ids() -> None:
    _, cog, interaction = _setup()
    send = await _run(cog, interaction, "list")
    text = send.await_args.kwargs["embed"].description
    assert "d_a" in text and "d_b" in text


async def test_reroll_calls_the_engine_and_confirms() -> None:
    bot, cog, interaction = _setup()
    send = await _run(cog, interaction, "reroll", period="weekly")
    bot.challenges.reroll.assert_awaited_once_with("weekly")
    assert send.await_args.kwargs["ephemeral"] is True


async def test_set_parses_a_comma_separated_list() -> None:
    bot, cog, interaction = _setup()
    await _run(cog, interaction, "set", templates=" d_b , ")
    bot.challenges.set_slate.assert_awaited_once_with("daily", ["d_b"])


async def test_set_without_templates_is_refused_before_deferring() -> None:
    bot, cog, interaction = _setup()
    send = await _run(cog, interaction, "set", templates="  ,, ")
    interaction.response.defer.assert_not_awaited()
    bot.challenges.set_slate.assert_not_awaited()
    assert send.await_args.kwargs["ephemeral"] is True


async def test_garbage_ids_surface_the_engines_message_as_an_ephemeral_error() -> None:
    bot, cog, interaction = _setup()
    bot.challenges.set_slate.side_effect = ValueError("Unknown template id(s): nope")
    send = await _run(cog, interaction, "set", templates="nope")
    embed = send.await_args.kwargs["embed"]
    assert "Unknown template" in embed.description
    assert send.await_args.kwargs["ephemeral"] is True


async def test_markdown_in_a_bad_id_arrives_escaped() -> None:
    bot, cog, interaction = _setup()
    bot.challenges.set_slate.side_effect = ValueError("Unknown template id(s): **bold** _x_")
    send = await _run(cog, interaction, "set", templates="**bold** _x_")
    text = send.await_args.kwargs["embed"].description
    assert r"\*\*bold\*\* \_x\_" in text
    assert "**bold**" not in text


async def test_an_oversized_set_is_cut_off_before_it_reaches_the_engine() -> None:
    bot, cog, interaction = _setup()
    await _run(cog, interaction, "set", templates=",".join(f"x{i}" for i in range(500)))
    ids = bot.challenges.set_slate.await_args.args[1]
    assert len(ids) <= 12
