"""Tests for /guild board, /guild mission and its autocomplete."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from yokai.cogs.rpg import RpgCog
from yokai.errors import ThreadUnavailableError
from yokai.rpg import guild
from yokai.rpg.runtime import RpgRuntime
from yokai.storage.db import Database

USER = 111


async def make_runtime(tmp_path: Path) -> tuple[Database, RpgRuntime]:
    db = Database(tmp_path / "missions.db")
    await db.initialize()
    runtime = RpgRuntime.create(db, Path("content/rpg"))
    await runtime.store.create_character(USER, "E", now=1)
    return db, runtime


def make_bot(runtime: RpgRuntime, *, attempts: int = 8) -> MagicMock:
    bot = MagicMock()
    bot.user = None
    bot.rpg = runtime
    bot.config.rpg_tz = "UTC"
    bot.config.rpg_base_attempts = attempts
    bot.config.rpg_voice_minutes_per_attempt = 30
    bot.config.rpg_voice_max_bonus = 8
    bot.config.rpg_turn_seconds = 45
    return bot


def make_interaction(user_id: int = USER, *, text_channel: bool = True) -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    user.bot = False
    user.display_name = "Sami"
    interaction.user = user
    interaction.response.defer = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.channel = MagicMock(spec=discord.TextChannel if text_channel else discord.Thread)
    return interaction


async def todays_missions(cog: RpgCog, runtime: RpgRuntime) -> list[Any]:
    today, _ = cog._day()
    return await cog._board_for(today, runtime)


@pytest.mark.asyncio
async def test_board_without_a_character_is_a_private_error(tmp_path: Path) -> None:
    db, runtime = await make_runtime(tmp_path)
    try:
        cog = RpgCog(make_bot(runtime))
        interaction = make_interaction(user_id=999)
        with patch("yokai.cogs.rpg.send", new=AsyncMock()) as sent:
            await cog.board.callback(cog, interaction)
        interaction.response.defer.assert_not_awaited()
        assert sent.await_args.kwargs["ephemeral"] is True
        assert "No adventurer yet" in (sent.await_args.kwargs["embed"].title or "")
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_board_shows_open_ranks_with_rewards_and_locks_the_rest(tmp_path: Path) -> None:
    db, runtime = await make_runtime(tmp_path)
    try:
        cog = RpgCog(make_bot(runtime))
        interaction = make_interaction()
        with patch("yokai.cogs.rpg.send", new=AsyncMock()) as sent:
            await cog.board.callback(cog, interaction)

        interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        assert sent.await_args.kwargs["ephemeral"] is True
        embed = sent.await_args.kwargs["embed"]
        fields = {f.name: f.value for f in embed.fields}
        assert "Rank E" in fields and "Rank D (locked)" in fields and "Rank SS+ (locked)" in fields
        assert fields["Rank E"].count("\n") == 2  # three missions
        assert "XP" in fields["Rank E"] and "Renown" in fields["Rank E"]
        assert "XP" not in fields["Rank D (locked)"]
        assert "**8/8**" in (embed.description or "")
        assert "You are rank **E**" in (embed.description or "")
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_board_shows_spent_attempts_and_the_streak(tmp_path: Path) -> None:
    db, runtime = await make_runtime(tmp_path)
    try:
        cog = RpgCog(make_bot(runtime))
        today, _ = cog._day()
        await runtime.store.begin_fight(
            mission_id="e_pest_control", kind="mission", user_ids=[USER], cost=3, today=today,
            base_attempts=8, streak_rules=runtime.content.balance.streak,
        )  # fmt: skip
        with patch("yokai.cogs.rpg.send", new=AsyncMock()) as sent:
            await cog.board.callback(cog, make_interaction())
        description = sent.await_args.kwargs["embed"].description or ""
        assert "**5/8**" in description and "Streak: **1** day(s)" in description
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_the_board_is_the_same_for_everyone_and_stored(tmp_path: Path) -> None:
    db, runtime = await make_runtime(tmp_path)
    try:
        await runtime.store.create_character(222, "SS+", now=1)
        cog = RpgCog(make_bot(runtime))
        first = await todays_missions(cog, runtime)
        today, _ = cog._day()
        assert await runtime.store.get_board(today) == [m.id for m in first]
        assert [m.id for m in await todays_missions(cog, runtime)] == [m.id for m in first]
        assert len(first) == 26  # 3 regular missions per rank plus a raid for ranks C and up
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_mission_launches_a_fight_and_replies_privately(tmp_path: Path) -> None:
    db, runtime = await make_runtime(tmp_path)
    try:
        cog = RpgCog(make_bot(runtime))
        chosen = next(m for m in await todays_missions(cog, runtime) if m.rank == "E")
        interaction = make_interaction()
        thread = MagicMock()
        thread.mention = "<#900>"
        with (
            patch("yokai.cogs.rpg.launch_fight", new=AsyncMock(return_value=thread)) as launch,
            patch("yokai.cogs.rpg.send", new=AsyncMock()) as sent,
        ):
            await cog.mission.callback(cog, interaction, mission=chosen.id)

        interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        kwargs = launch.await_args.kwargs
        assert kwargs["mission"].id == chosen.id and kwargs["member_name"] == "Sami"
        assert kwargs["base_attempts"] == 8 and kwargs["turn_seconds"] == 45
        assert kwargs["channel"] is interaction.channel
        assert sent.await_args.kwargs["ephemeral"] is True
        embed = sent.await_args.kwargs["embed"]
        assert "Mission started" in (embed.title or "") and "<#900>" in (embed.description or "")
    finally:
        await db.close()


async def expect_refusal(cog: RpgCog, interaction: MagicMock, mission: str, expected: str) -> None:
    with (
        patch("yokai.cogs.rpg.launch_fight", new=AsyncMock()) as launch,
        patch("yokai.cogs.rpg.send", new=AsyncMock()) as sent,
    ):
        await cog.mission.callback(cog, interaction, mission=mission)
    interaction.response.defer.assert_not_awaited()
    launch.assert_not_awaited()
    assert sent.await_args.kwargs["ephemeral"] is True
    embed = sent.await_args.kwargs["embed"]
    assert expected in (embed.title or "") + (embed.description or "")


@pytest.mark.asyncio
async def test_mission_refusals_happen_before_deferring_and_stay_private(tmp_path: Path) -> None:
    db, runtime = await make_runtime(tmp_path)
    try:
        cog = RpgCog(make_bot(runtime))
        board = await todays_missions(cog, runtime)
        easy = next(m for m in board if m.rank == "E")
        hard = next(m for m in board if m.rank == "D")

        await expect_refusal(cog, make_interaction(999), easy.id, "No adventurer yet")
        await expect_refusal(cog, make_interaction(), "made_up_mission", "Not on the board")
        await expect_refusal(cog, make_interaction(), hard.id, "needs rank D")
        await expect_refusal(
            cog, make_interaction(text_channel=False), easy.id, "couldn't open a fight thread"
        )
        runtime.active_users.add(USER)
        await expect_refusal(cog, make_interaction(), easy.id, "already in a guild fight")
        runtime.active_users.discard(USER)

        broke = RpgCog(make_bot(runtime, attempts=0))
        await expect_refusal(broke, make_interaction(), easy.id, "Out of attempts")
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_mission_not_on_todays_board_is_refused_even_if_it_exists(tmp_path: Path) -> None:
    db, runtime = await make_runtime(tmp_path)
    try:
        cog = RpgCog(make_bot(runtime))
        on_board = {m.id for m in await todays_missions(cog, runtime)}
        off_board = next(
            m for m in runtime.content.missions.values() if m.rank == "E" and m.id not in on_board
        )
        await expect_refusal(cog, make_interaction(), off_board.id, "Not on the board")
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_launch_failure_is_reported_privately_after_deferring(tmp_path: Path) -> None:
    db, runtime = await make_runtime(tmp_path)
    try:
        cog = RpgCog(make_bot(runtime))
        chosen = next(m for m in await todays_missions(cog, runtime) if m.rank == "E")
        interaction = make_interaction()
        with (
            patch(
                "yokai.cogs.rpg.launch_fight", new=AsyncMock(side_effect=ThreadUnavailableError())
            ),
            patch("yokai.cogs.rpg.send", new=AsyncMock()) as sent,
        ):
            await cog.mission.callback(cog, interaction, mission=chosen.id)
        interaction.response.defer.assert_awaited_once()
        assert sent.await_args.kwargs["ephemeral"] is True
        assert "Can't open a thread" in (sent.await_args.kwargs["embed"].title or "")
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_autocomplete_offers_only_enterable_missions_and_filters_by_text(
    tmp_path: Path,
) -> None:
    db, runtime = await make_runtime(tmp_path)
    try:
        cog = RpgCog(make_bot(runtime))
        board = await todays_missions(cog, runtime)
        choices = await cog.mission_autocomplete(make_interaction(), "")
        by_value = {c.value: c.name for c in choices}
        assert set(by_value) == {m.id for m in board if m.rank == "E"}
        assert all("rank E" in name and len(name) <= 100 for name in by_value.values())

        first = next(m for m in board if m.rank == "E")
        narrowed = await cog.mission_autocomplete(make_interaction(), first.name[:4].upper())
        assert first.id in {c.value for c in narrowed}
        assert await cog.mission_autocomplete(make_interaction(), "zzzzzz") == []
        assert await cog.mission_autocomplete(make_interaction(999), "") == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_autocomplete_works_before_anyone_has_opened_the_board(tmp_path: Path) -> None:
    db, runtime = await make_runtime(tmp_path)
    try:
        cog = RpgCog(make_bot(runtime))
        today, _ = cog._day()
        assert await runtime.store.get_board(today) is None
        choices = await cog.mission_autocomplete(make_interaction(), "")
        expected = {
            m
            for m in guild.draw_board(runtime.content, today)
            if runtime.content.missions[m].rank == "E"
        }
        assert {c.value for c in choices} == expected
        assert await runtime.store.get_board(today) is None  # autocomplete never writes
    finally:
        await db.close()
