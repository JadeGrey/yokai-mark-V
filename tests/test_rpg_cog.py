"""Tests for the /guild join and /card commands."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest
from discord.ext import commands

from yokai.cogs.rpg import RpgCog
from yokai.rpg.runtime import RpgRuntime
from yokai.storage.db import Database


async def make_runtime(tmp_path: Path) -> tuple[Database, RpgRuntime]:
    db = Database(tmp_path / "rpg_cog.db")
    await db.initialize()
    return db, RpgRuntime.create(db, Path("content/rpg"))


def make_bot(runtime: RpgRuntime) -> MagicMock:
    bot = MagicMock()
    bot.user = None
    bot.rpg = runtime
    return bot


def make_interaction(user_id: int = 111) -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    user.bot = False
    interaction.user = user
    interaction.response.defer = AsyncMock()
    return interaction


def make_member(user_id: int, is_bot: bool = False) -> MagicMock:
    member = MagicMock(spec=discord.Member)
    member.id = user_id
    member.bot = is_bot
    return member


@pytest.mark.asyncio
async def test_join_creates_the_character_and_replies_publicly(tmp_path: Path) -> None:
    db, runtime = await make_runtime(tmp_path)
    try:
        cog = RpgCog(make_bot(runtime))
        interaction = make_interaction(111)
        with patch("yokai.cogs.rpg.send", new=AsyncMock()) as sent:
            await cog.join.callback(cog, interaction)

        interaction.response.defer.assert_awaited_once()
        embed = sent.await_args.kwargs["embed"]
        assert "<@111>" in (embed.description or "")
        assert "rank E" in (embed.description or "")
        assert sent.await_args.kwargs.get("ephemeral", False) is False
        character = await runtime.store.get_character(111)
        assert character is not None and character.rank == "E" and character.level == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_join_twice_replies_ephemerally_without_deferring(tmp_path: Path) -> None:
    db, runtime = await make_runtime(tmp_path)
    try:
        await runtime.store.create_character(111, "E", now=1)
        cog = RpgCog(make_bot(runtime))
        interaction = make_interaction(111)
        with patch("yokai.cogs.rpg.send", new=AsyncMock()) as sent:
            await cog.join.callback(cog, interaction)

        interaction.response.defer.assert_not_awaited()
        assert sent.await_args.kwargs["ephemeral"] is True
        assert "Already on the roster" in (sent.await_args.kwargs["embed"].title or "")
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_card_for_yourself_without_a_character_is_an_ephemeral_error(
    tmp_path: Path,
) -> None:
    db, runtime = await make_runtime(tmp_path)
    try:
        cog = RpgCog(make_bot(runtime))
        interaction = make_interaction(111)
        with patch("yokai.cogs.rpg.send", new=AsyncMock()) as sent:
            await cog.card.callback(cog, interaction, member=None)

        interaction.response.defer.assert_not_awaited()
        assert sent.await_args.kwargs["ephemeral"] is True
        embed = sent.await_args.kwargs["embed"]
        assert "No adventurer yet" in (embed.title or "")
        assert "You haven't joined" in (embed.description or "")
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_card_for_another_member_is_public_and_shows_their_stats(tmp_path: Path) -> None:
    db, runtime = await make_runtime(tmp_path)
    try:
        await runtime.store.create_character(222, "E", now=1_700_000_000)
        cog = RpgCog(make_bot(runtime))
        interaction = make_interaction(111)
        with patch("yokai.cogs.rpg.send", new=AsyncMock()) as sent:
            await cog.card.callback(cog, interaction, member=make_member(222))

        interaction.response.defer.assert_awaited_once()
        assert sent.await_args.kwargs.get("ephemeral", False) is False
        embed = sent.await_args.kwargs["embed"]
        names = {field.name: field.value for field in embed.fields}
        assert "<@222>" in (embed.description or "")
        assert names["Rank"] == "**E**" and names["Level"] == "**1** / 60"
        assert names["HP"] == "**100**"
        assert names["Next promotion"] == "Rank **D** at level **5**"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_card_for_a_member_without_a_character_uses_the_third_person(
    tmp_path: Path,
) -> None:
    db, runtime = await make_runtime(tmp_path)
    try:
        cog = RpgCog(make_bot(runtime))
        interaction = make_interaction(111)
        with patch("yokai.cogs.rpg.send", new=AsyncMock()) as sent:
            await cog.card.callback(cog, interaction, member=make_member(333))

        interaction.response.defer.assert_not_awaited()
        assert sent.await_args.kwargs["ephemeral"] is True
        assert "That member" in (sent.await_args.kwargs["embed"].description or "")
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_card_for_a_bot_account_is_an_ephemeral_error(tmp_path: Path) -> None:
    """Review Focus 5: a bot can never have a character, and asking must not crash."""
    db, runtime = await make_runtime(tmp_path)
    try:
        cog = RpgCog(make_bot(runtime))
        interaction = make_interaction(111)
        with patch("yokai.cogs.rpg.send", new=AsyncMock()) as sent:
            await cog.card.callback(cog, interaction, member=make_member(444, is_bot=True))

        interaction.response.defer.assert_not_awaited()
        assert sent.await_args.kwargs["ephemeral"] is True
        assert "No adventurer yet" in (sent.await_args.kwargs["embed"].title or "")
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_card_survives_a_rank_that_is_missing_from_the_content(tmp_path: Path) -> None:
    """Review Focus 2: content edited after a character was created must not crash /card."""
    db, runtime = await make_runtime(tmp_path)
    try:
        await runtime.store.create_character(555, "RETIRED-RANK", now=1)
        cog = RpgCog(make_bot(runtime))
        interaction = make_interaction(111)
        with patch("yokai.cogs.rpg.send", new=AsyncMock()) as sent:
            await cog.card.callback(cog, interaction, member=make_member(555))

        embed = sent.await_args.kwargs["embed"]
        names = {field.name: field.value for field in embed.fields}
        assert names["Rank"] == "**RETIRED-RANK**"
        assert "Next promotion" not in names
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_card_clamps_a_level_outside_the_content_range(tmp_path: Path) -> None:
    """Review Focus 4: a stored level above the cap still shows sane stats."""
    db, runtime = await make_runtime(tmp_path)
    try:
        await runtime.store.create_character(666, "E", now=1)
        async with db.transaction() as conn:
            await conn.execute("UPDATE rpg_characters SET level = 9999 WHERE user_id = 666;")
        cog = RpgCog(make_bot(runtime))
        interaction = make_interaction(111)
        with patch("yokai.cogs.rpg.send", new=AsyncMock()) as sent:
            await cog.card.callback(cog, interaction, member=make_member(666))

        names = {field.name: field.value for field in sent.await_args.kwargs["embed"].fields}
        # Level 60 is the cap: 100 + 12 * 59 = 808 HP.
        assert names["HP"] == "**808**"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_the_cog_registers_guild_join_and_card(tmp_path: Path) -> None:
    db, runtime = await make_runtime(tmp_path)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    try:
        bot.rpg = runtime  # type: ignore[attr-defined]
        await bot.add_cog(RpgCog(bot))  # type: ignore[arg-type]
        names = {command.qualified_name for command in bot.tree.walk_commands()}
        assert {"guild join", "card"} <= names
    finally:
        await bot.close()
        await db.close()


@pytest.mark.asyncio
async def test_card_shows_the_members_xp_progress_and_max_level(tmp_path: Path) -> None:
    db, runtime = await make_runtime(tmp_path)
    try:
        await runtime.store.create_character(222, "E", now=1_700_000_000)
        await runtime.store.create_character(333, "SS+", now=1_700_000_000)
        async with db.transaction() as conn:
            await conn.execute("UPDATE rpg_characters SET level = 3, xp = 40 WHERE user_id = 222;")
            await conn.execute("UPDATE rpg_characters SET level = 60, xp = 0 WHERE user_id = 333;")
        cog = RpgCog(make_bot(runtime))
        needed = runtime.content.balance.xp.xp_to_next(3)

        async def card_fields(user_id: int) -> dict[str, str]:
            with patch("yokai.cogs.rpg.send", new=AsyncMock()) as sent:
                await cog.card.callback(cog, make_interaction(111), member=make_member(user_id))
            return {f.name: f.value for f in sent.await_args.kwargs["embed"].fields}

        fields = await card_fields(222)
        assert f"**40** / {needed:,} XP" in fields["Experience"]
        assert "to level 4" in fields["Experience"]
        assert (await card_fields(333))["Experience"] == "Max level reached."
    finally:
        await db.close()
