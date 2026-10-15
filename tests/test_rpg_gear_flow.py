"""Gear in play: cards, the low-gear warning, loadouts and loot in fights."""

from __future__ import annotations

import asyncio
import random
from pathlib import Path
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from tests.test_rpg_cog_missions import make_bot, make_interaction, todays_missions
from tests.test_rpg_fights import FakeThread, wait_for_view
from tests.test_rpg_fights import make_bot as make_fight_bot
from yokai.cogs.rpg import RpgCog
from yokai.rpg.fights import FightRunner, Participant
from yokai.rpg.gear import compute_loadout
from yokai.rpg.gear_store import ItemDrop
from yokai.rpg.models import ItemInstance
from yokai.rpg.runtime import RpgRuntime
from yokai.storage.db import Database
from yokai.ui.rpg_views import ConfirmView

USER = 111


async def make_runtime(tmp_path: Path) -> tuple[Database, RpgRuntime]:
    db = Database(tmp_path / "flow.db")
    await db.initialize()
    runtime = RpgRuntime.create(db, Path("content/rpg"))
    await runtime.store.create_character(USER, "E", now=1_700_000_000)
    return db, runtime


async def wear(
    runtime: RpgRuntime, content_id: str, *, durability: Optional[int] = None, level: int = 60
) -> ItemInstance:
    definition = runtime.content.items[content_id]
    item = await runtime.gear.grant_item(
        USER,
        ItemDrop(
            content_id,
            definition.stats,
            definition.max_durability if durability is None else durability,
        ),
        inventory_cap=60,
        now=1,
    )
    return await runtime.gear.equip_item(USER, item.id, level=level, items=runtime.content.items)


def card_fields(sent: Any) -> dict[str, str]:
    return {f.name: f.value for f in sent.await_args.kwargs["embed"].fields}


# --------------------------------------------------------------------------------- cards


@pytest.mark.asyncio
async def test_the_card_shows_gear_by_slot_and_the_set_bonuses(tmp_path: Path) -> None:
    db, runtime = await make_runtime(tmp_path)
    try:
        await wear(runtime, "oni_weapon_t1_common")
        await wear(runtime, "oni_head_t1_common", durability=40)
        await wear(runtime, "kitsune_body_t1_common", durability=0)
        cog = RpgCog(make_bot(runtime))
        target = MagicMock(spec=discord.Member)
        target.id, target.bot = USER, False
        with patch("yokai.cogs.rpg.send", new=AsyncMock()) as sent:
            await cog.card.callback(cog, make_interaction(222), member=target)
        fields = card_fields(sent)
        assert "Worn Oni Kanabo" in fields["Gear"] and "100%" in fields["Gear"]
        assert "Worn Oni Horned Mask" in fields["Gear"] and "40%" in fields["Gear"]
        assert (
            "broken" in fields["Gear"]
            and "**Boots**" in fields["Gear"]
            and "empty" in fields["Gear"]
        )
        assert "Oni \\(2\\)" in fields["Set bonuses"] and "+8% Attack" in fields["Set bonuses"]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_the_card_stats_include_gear_and_ignore_broken_pieces(tmp_path: Path) -> None:
    db, runtime = await make_runtime(tmp_path)
    try:
        cog = RpgCog(make_bot(runtime))
        target = MagicMock(spec=discord.Member)
        target.id, target.bot = USER, False

        async def attack_on_card() -> str:
            with patch("yokai.cogs.rpg.send", new=AsyncMock()) as sent:
                await cog.card.callback(cog, make_interaction(222), member=target)
            return card_fields(sent)["Attack"]

        assert await attack_on_card() == "**10**"
        weapon = await wear(runtime, "oni_weapon_t1_common")
        assert await attack_on_card() == f"**{10 + weapon.rolls.attack}**"
        async with db.transaction() as conn:
            await conn.execute("UPDATE rpg_items SET durability = 0 WHERE id = ?;", (weapon.id,))
        assert await attack_on_card() == "**10**"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_card_with_no_gear_still_lists_the_empty_slots(tmp_path: Path) -> None:
    db, runtime = await make_runtime(tmp_path)
    try:
        cog = RpgCog(make_bot(runtime))
        target = MagicMock(spec=discord.Member)
        target.id, target.bot = USER, False
        with patch("yokai.cogs.rpg.send", new=AsyncMock()) as sent:
            await cog.card.callback(cog, make_interaction(222), member=target)
        fields = card_fields(sent)
        assert fields["Gear"].count("empty") == 5 and "Set bonuses" not in fields
    finally:
        await db.close()


# ---------------------------------------------------------------- the low-gear warning


async def start_mission(cog: RpgCog, runtime: RpgRuntime, interaction: MagicMock) -> Any:
    chosen = next(m for m in await todays_missions(cog, runtime) if m.rank == "E")
    thread = MagicMock()
    thread.mention = "<#900>"
    with (
        patch("yokai.cogs.rpg.launch_fight", new=AsyncMock(return_value=thread)) as launch,
        patch("yokai.cogs.rpg.send", new=AsyncMock()) as sent,
    ):
        await cog.mission.callback(cog, interaction, mission=chosen.id)
    return launch, sent


@pytest.mark.asyncio
async def test_healthy_gear_starts_the_mission_without_a_warning(tmp_path: Path) -> None:
    db, runtime = await make_runtime(tmp_path)
    try:
        await wear(runtime, "oni_weapon_t1_common", durability=26)  # 26% is not yet "low"
        cog = RpgCog(make_bot(runtime))
        launch, sent = await start_mission(cog, runtime, make_interaction())
        launch.assert_awaited_once()
        assert "Mission started" in (sent.await_args.kwargs["embed"].title or "")
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_worn_gear_shows_a_warning_before_any_attempt_is_spent(tmp_path: Path) -> None:
    db, runtime = await make_runtime(tmp_path)
    try:
        await wear(runtime, "oni_weapon_t1_common", durability=25)
        await wear(runtime, "oni_head_t1_common", durability=0)
        await wear(runtime, "oni_body_t1_common")
        cog = RpgCog(make_bot(runtime))
        interaction = make_interaction()
        launch, sent = await start_mission(cog, runtime, interaction)

        launch.assert_not_awaited()
        interaction.response.defer.assert_not_awaited()
        embed = sent.await_args.kwargs["embed"]
        assert sent.await_args.kwargs["ephemeral"] is True
        assert "Your gear is worn" in (embed.title or "")
        text = embed.description or ""
        assert "Worn Oni Kanabo: 25%" in text
        assert "Worn Oni Horned Mask: broken \\(0%\\)" in text  # markdown-escaped on purpose
        assert "Body" not in text and "Worn Oni Plate" not in text
        assert isinstance(sent.await_args.kwargs["view"], ConfirmView)
        character = await runtime.store.get_character(USER)
        assert character is not None and character.attempts_used == 0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_start_anyway_launches_the_fight_and_cancel_does_not(tmp_path: Path) -> None:
    db, runtime = await make_runtime(tmp_path)
    try:
        await wear(runtime, "oni_weapon_t1_common", durability=0)
        cog = RpgCog(make_bot(runtime))
        launch, sent = await start_mission(cog, runtime, make_interaction())
        view: ConfirmView = sent.await_args.kwargs["view"]

        thread = MagicMock()
        thread.mention = "<#901>"
        clicked = make_interaction()
        clicked.response.is_done = MagicMock(side_effect=[False, True])  # edited, then followups
        clicked.response.edit_message = AsyncMock()
        with (
            patch("yokai.cogs.rpg.launch_fight", new=AsyncMock(return_value=thread)) as go,
            patch("yokai.cogs.rpg.send", new=AsyncMock()) as after,
        ):
            await view.start_button.callback(clicked)
        clicked.response.edit_message.assert_awaited_once()
        go.assert_awaited_once()
        assert go.await_args.kwargs["channel"] is not None
        assert "Mission started" in (after.await_args.kwargs["embed"].title or "")

        again = make_interaction()
        again.response.defer = AsyncMock()
        await view.start_button.callback(again)  # a second press does nothing
        again.response.defer.assert_awaited_once()
        go.assert_awaited_once()

        launch2, sent2 = await start_mission(cog, runtime, make_interaction())
        view2: ConfirmView = sent2.await_args.kwargs["view"]
        cancelled = make_interaction()
        cancelled.response.edit_message = AsyncMock()
        await view2.cancel_button.callback(cancelled)
        launch2.assert_not_awaited()
        assert "No attempt was spent" in (
            cancelled.response.edit_message.await_args.kwargs["embed"].description or ""
        )
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_only_the_member_may_answer_the_warning(tmp_path: Path) -> None:
    db, runtime = await make_runtime(tmp_path)
    try:
        await wear(runtime, "oni_weapon_t1_common", durability=0)
        cog = RpgCog(make_bot(runtime))
        _, sent = await start_mission(cog, runtime, make_interaction())
        view: ConfirmView = sent.await_args.kwargs["view"]
        with patch("yokai.ui.rpg_views.screen", new=AsyncMock(return_value=False)):
            assert await view.interaction_check(make_interaction(USER)) is True
            assert await view.interaction_check(make_interaction(999)) is False
        with patch("yokai.ui.rpg_views.screen", new=AsyncMock(return_value=True)):
            assert await view.interaction_check(make_interaction(USER)) is False
    finally:
        await db.close()


# -------------------------------------------------------------------------- in a fight

GLASS_ENEMIES = """\
[[enemy]]
id = "glass"
name = "Glass"
mult = { hp = 1, attack = 1, magic = 1, defense = 1, resist = 1, speed = 1, crit = 1 }
actions = [{ do = "attack", weight = 1 }]
"""

GLASS_MISSIONS = """\
[[mission]]
id = "m_glass"
name = "Glass Test"
rank = "E"
attempt_cost = 1
stages = [{ enemies = ["glass"], level = 1 }]
"""


async def glass_runner(
    tmp_path: Path, *, wear_items: list[tuple[str, int]], loot_chance: int = 0
) -> tuple[Database, RpgRuntime, FightRunner, FakeThread]:
    """A fight against one enemy that dies to any hit, with chosen gear and loot chance."""
    import shutil

    from yokai.rpg.content import load_content

    content_dir = tmp_path / "content"
    shutil.copytree("content/rpg", content_dir)
    (content_dir / "enemies.toml").write_text(GLASS_ENEMIES, encoding="utf-8")
    (content_dir / "missions.toml").write_text(GLASS_MISSIONS, encoding="utf-8")
    text = (content_dir / "balance.toml").read_text(encoding="utf-8")
    text = text.replace("drop_chance_percent = 35", f"drop_chance_percent = {loot_chance}")
    (content_dir / "balance.toml").write_text(text, encoding="utf-8")
    load_content(content_dir)

    db = Database(tmp_path / "glass.db")
    await db.initialize()
    runtime = RpgRuntime.create(db, content_dir)
    await runtime.store.create_character(USER, "E", now=1)
    for content_id, durability in wear_items:
        await wear(runtime, content_id, durability=durability)
    mission = runtime.content.missions["m_glass"]
    fight_id = await runtime.store.begin_fight(
        mission_id=mission.id, kind="mission", user_ids=[USER], cost=1, today="2026-10-14",
        base_attempts=8, streak_rules=runtime.content.balance.streak,
    )  # fmt: skip
    character = await runtime.store.get_character(USER)
    assert character is not None
    loadout = compute_loadout(
        runtime.content, character.level, await runtime.gear.equipped_items(USER)
    )
    runtime.active_users.add(USER)
    thread = FakeThread()
    runner = FightRunner(
        bot=make_fight_bot(), runtime=runtime, fight_id=fight_id, mission=mission,
        participants=[Participant(USER, "Sami", character, loadout)],
        thread=thread, turn_seconds=2, rng=random.Random(3), idle_delay=0, archive_delay=0,
    )  # fmt: skip
    return db, runtime, runner, thread


@pytest.mark.asyncio
async def test_the_fight_uses_the_gear_loadout_stats_and_skills(tmp_path: Path) -> None:
    items = [(f"oni_{slot}_t1_common", 100) for slot in ("weapon", "head", "body", "boots")]
    db, runtime, runner, _ = await glass_runner(tmp_path, wear_items=items)
    try:
        hero = runner.fight.players[0]
        loadout = compute_loadout(runtime.content, 1, await runtime.gear.equipped_items(USER))
        assert hero.stats == loadout.stats and hero.hp == loadout.stats.hp
        assert hero.skills == ("rampage",)
        assert hero.stats.attack > 10
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_win_wears_gear_adds_loot_and_the_result_says_so(tmp_path: Path) -> None:
    db, runtime, runner, thread = await glass_runner(
        tmp_path, wear_items=[("oni_weapon_t1_common", 52)], loot_chance=100
    )
    try:
        task = asyncio.create_task(runner.run())
        view = await wait_for_view(runner, 1)
        interaction = MagicMock(spec=discord.Interaction)
        interaction.user = MagicMock(spec=discord.Member)
        interaction.user.id = USER
        interaction.response = MagicMock()
        interaction.response.defer = AsyncMock()
        await view.attack_button.callback(interaction)
        await asyncio.wait_for(task, 5)

        final = thread.last_embed()
        fields = {f.name: f.value for f in final.fields}
        assert "Mission complete" in (final.title or "")
        assert "Loot" in fields and f"<@{USER}>" in fields["Loot"]
        assert "Gear" in fields and "Worn Oni Kanabo is at 49%" in fields["Gear"]
        owned = await runtime.gear.list_items(USER)
        assert len(owned) == 2  # the worn weapon plus the drop
        weapon = next(i for i in owned if i.equipped_slot == "weapon")
        assert weapon.durability == 49
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_win_that_breaks_gear_says_it_counts_for_nothing(tmp_path: Path) -> None:
    db, runtime, runner, thread = await glass_runner(
        tmp_path, wear_items=[("oni_weapon_t1_common", 2)]
    )
    try:
        task = asyncio.create_task(runner.run())
        view = await wait_for_view(runner, 1)
        interaction = MagicMock(spec=discord.Interaction)
        interaction.user = MagicMock(spec=discord.Member)
        interaction.user.id = USER
        interaction.response = MagicMock()
        interaction.response.defer = AsyncMock()
        await view.attack_button.callback(interaction)
        await asyncio.wait_for(task, 5)
        fields = {f.name: f.value for f in thread.last_embed().fields}
        assert "broken (0%)" in fields["Gear"] and "counts for nothing" in fields["Gear"]
        assert "Loot" not in fields
    finally:
        await db.close()
