"""Promotion in play: /guild rank, the Challenge button, results, cards and the leaderboard."""

from __future__ import annotations

import asyncio
import random
import re
import shutil
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from tests.embed_helpers import emoji_count, total_embed_length
from tests.test_rpg_cog_missions import make_bot, make_interaction
from tests.test_rpg_fights import FakeThread, wait_for_view
from tests.test_rpg_fights import make_bot as make_fight_bot
from yokai.cogs.rpg import RpgCog
from yokai.rpg.fights import FightRunner, Participant
from yokai.rpg.runtime import RpgRuntime
from yokai.rpg.store import PromotionRequest
from yokai.storage.db import Database
from yokai.ui.embeds import EmbedFactory
from yokai.ui.rpg_views import ConfirmView, GuildLeaderboardView, PromotionView

USER = 111


async def make_world(
    tmp_path: Path, *, level: int = 5, rank: str = "E", attempts: int = 8
) -> tuple[Database, RpgRuntime, RpgCog]:
    db = Database(tmp_path / "rank.db")
    await db.initialize()
    runtime = RpgRuntime.create(db, Path("content/rpg"))
    await runtime.store.create_character(USER, rank, now=1_700_000_000)
    await set_columns(db, level=level)
    bot = make_bot(runtime, attempts=attempts)
    bot.config.rpg_promotion_cooldown_hours = 6
    bot.db = db
    return db, runtime, RpgCog(bot)


async def set_columns(db: Database, user: int = USER, **columns: Any) -> None:
    assignments = ", ".join(f"{name} = ?" for name in columns)
    async with db.transaction() as conn:
        await conn.execute(
            f"UPDATE rpg_characters SET {assignments} WHERE user_id = ?;",
            (*columns.values(), user),
        )


async def call_rank(cog: RpgCog, interaction: MagicMock | None = None) -> Any:
    with patch("yokai.cogs.rpg.send", new=AsyncMock()) as sent:
        await cog.rank.callback(cog, interaction or make_interaction())
    return sent


def description(sent: Any) -> str:
    return sent.await_args.kwargs["embed"].description or ""


# ------------------------------------------------------------------------- /guild rank


@pytest.mark.asyncio
async def test_rank_without_a_character_is_a_private_error(tmp_path: Path) -> None:
    db, _, cog = await make_world(tmp_path)
    try:
        sent = await call_rank(cog, make_interaction(999))
        assert "No adventurer yet" in (sent.await_args.kwargs["embed"].title or "")
        assert sent.await_args.kwargs["ephemeral"] is True
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_ready_member_sees_the_trial_and_a_challenge_button(tmp_path: Path) -> None:
    db, _, cog = await make_world(tmp_path, level=5)
    try:
        sent = await call_rank(cog)
        text = description(sent)
        assert "Rank **E**" in text and "level **5**" in text
        assert "Trial of the Back Alleys" in text and "2 attempts" in text
        assert "You can challenge now" in text and "6-hour cooldown" in text
        view = sent.await_args.kwargs["view"]
        assert isinstance(view, PromotionView)
        assert view.challenge_button.label == "Challenge for Rank D"
        assert sent.await_args.kwargs["ephemeral"] is True
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_member_below_the_threshold_sees_how_far_they_are(tmp_path: Path) -> None:
    db, _, cog = await make_world(tmp_path, level=3)
    try:
        sent = await call_rank(cog)
        assert "You need 2 more level(s)" in description(sent)
        assert "view" not in sent.await_args.kwargs
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_member_in_cooldown_sees_when_they_can_retry(tmp_path: Path) -> None:
    db, _, cog = await make_world(tmp_path, level=9)
    try:
        until = int(time.time()) + 3600
        await set_columns(db, promo_cooldown_until=until)
        sent = await call_rank(cog)
        assert f"<t:{until}:R>" in description(sent) and "view" not in sent.await_args.kwargs
        await set_columns(db, promo_cooldown_until=int(time.time()) - 5)  # it has ended
        sent = await call_rank(cog)
        assert isinstance(sent.await_args.kwargs["view"], PromotionView)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_the_top_rank_and_an_unknown_rank_get_plain_messages(tmp_path: Path) -> None:
    """Review Focus 5 and 7."""
    db, _, cog = await make_world(tmp_path, level=60, rank="SS+")
    try:
        sent = await call_rank(cog)
        assert "highest rank in the guild" in description(sent)
        assert "view" not in sent.await_args.kwargs
        await set_columns(db, rank="Retired")
        sent = await call_rank(cog)
        assert "no longer in the guild's records" in description(sent)
        assert "view" not in sent.await_args.kwargs
    finally:
        await db.close()


# ------------------------------------------------------------------- the Challenge button


async def press_challenge(cog: RpgCog, *, interaction: MagicMock | None = None) -> tuple[Any, Any]:
    """Open /guild rank, then press Challenge. Returns the launch mock and the send mock."""
    sent = await call_rank(cog)
    view: PromotionView = sent.await_args.kwargs["view"]
    thread = MagicMock()
    thread.mention = "<#900>"
    clicked = interaction or make_interaction()
    clicked.response.is_done = MagicMock(return_value=False)
    with (
        patch("yokai.cogs.rpg.launch_fight", new=AsyncMock(return_value=thread)) as launch,
        patch("yokai.cogs.rpg.send", new=AsyncMock()) as after,
    ):
        await view.challenge_button.callback(clicked)
    return launch, after


@pytest.mark.asyncio
async def test_challenge_starts_the_trial_with_a_validated_request(tmp_path: Path) -> None:
    db, runtime, cog = await make_world(tmp_path, level=5)
    try:
        launch, after = await press_challenge(cog)
        launch.assert_awaited_once()
        kwargs = launch.await_args.kwargs
        assert kwargs["mission"].id == "promotion_D" and kwargs["mission"].kind == "promotion"
        assert kwargs["promotion"] == PromotionRequest("E", "D", 5)
        assert kwargs["promotion_cooldown_s"] == 6 * 3600
        assert "Promotion trial started" in (after.await_args.kwargs["embed"].title or "")
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_second_press_does_nothing(tmp_path: Path) -> None:
    db, _, cog = await make_world(tmp_path, level=5)
    try:
        sent = await call_rank(cog)
        view: PromotionView = sent.await_args.kwargs["view"]
        with (
            patch("yokai.cogs.rpg.launch_fight", new=AsyncMock(return_value=MagicMock())) as launch,
            patch("yokai.cogs.rpg.send", new=AsyncMock()),
        ):
            first = make_interaction()
            first.response.is_done = MagicMock(return_value=False)
            await view.challenge_button.callback(first)
            second = make_interaction()
            await view.challenge_button.callback(second)
        launch.assert_awaited_once()
        second.response.defer.assert_awaited_once()
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_only_the_member_may_press_challenge(tmp_path: Path) -> None:
    db, _, cog = await make_world(tmp_path, level=5)
    try:
        sent = await call_rank(cog)
        view: PromotionView = sent.await_args.kwargs["view"]
        with patch("yokai.ui.rpg_views.screen", new=AsyncMock(return_value=False)):
            assert await view.interaction_check(make_interaction(USER)) is True
            assert await view.interaction_check(make_interaction(222)) is False
        with patch("yokai.ui.rpg_views.screen", new=AsyncMock(return_value=True)):
            assert await view.interaction_check(make_interaction(USER)) is False
    finally:
        await db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"level": 3}, "Not ready to promote"),
        ({"rank": "D"}, "Rank C opens"),  # promoted meanwhile: the next rank is C at level 12
        ({"rank": "Retired"}, "Your rank changed"),
        ({"promo_cooldown_until": 4_000_000_000}, "Still recovering"),
        ({"attempts_used": 8, "attempts_day": "TODAY"}, "Out of attempts"),
    ],
)
async def test_the_button_rechecks_everything_with_fresh_data(
    tmp_path: Path, change: dict[str, Any], expected: str
) -> None:
    db, _, cog = await make_world(tmp_path, level=5)
    try:
        sent = await call_rank(cog)
        view: PromotionView = sent.await_args.kwargs["view"]
        if change.get("attempts_day") == "TODAY":
            change = {**change, "attempts_day": cog._day()[0]}
        await set_columns(db, **change)
        clicked = make_interaction()
        clicked.response.is_done = MagicMock(return_value=False)
        with (
            patch("yokai.cogs.rpg.launch_fight", new=AsyncMock()) as launch,
            patch("yokai.cogs.rpg.send", new=AsyncMock()) as after,
        ):
            await view.challenge_button.callback(clicked)
        launch.assert_not_awaited()
        embed = after.await_args.kwargs["embed"]
        assert expected in (embed.title or "") + (embed.description or "")
        assert after.await_args.kwargs["ephemeral"] is True
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_the_button_refuses_when_already_fighting_or_outside_a_text_channel(
    tmp_path: Path,
) -> None:
    db, runtime, cog = await make_world(tmp_path, level=5)
    try:
        sent = await call_rank(cog)
        view: PromotionView = sent.await_args.kwargs["view"]
        runtime.active_users.add(USER)
        with (
            patch("yokai.cogs.rpg.launch_fight", new=AsyncMock()) as launch,
            patch("yokai.cogs.rpg.send", new=AsyncMock()) as after,
        ):
            await view.challenge_button.callback(make_interaction())
        assert "Already in a fight" in (after.await_args.kwargs["embed"].title or "")
        launch.assert_not_awaited()
        runtime.active_users.discard(USER)

        sent = await call_rank(cog)
        view = sent.await_args.kwargs["view"]
        with (
            patch("yokai.cogs.rpg.launch_fight", new=AsyncMock()) as launch,
            patch("yokai.cogs.rpg.send", new=AsyncMock()) as after,
        ):
            await view.challenge_button.callback(make_interaction(text_channel=False))
        assert "Can't open a thread" in (after.await_args.kwargs["embed"].title or "")
        launch.assert_not_awaited()
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_worn_gear_adds_the_confirm_step_before_the_trial(tmp_path: Path) -> None:
    from yokai.rpg.gear_store import ItemDrop

    db, runtime, cog = await make_world(tmp_path, level=5)
    try:
        definition = runtime.content.items["oni_weapon_t1_common"]
        item = await runtime.gear.grant_item(
            USER, ItemDrop(definition.id, definition.stats, 0), inventory_cap=60
        )
        await runtime.gear.equip_item(USER, item.id, level=60, items=runtime.content.items)
        launch, after = await press_challenge(cog)
        launch.assert_not_awaited()
        confirm: ConfirmView = after.await_args.kwargs["view"]
        assert isinstance(confirm, ConfirmView)
        assert "Your gear is worn" in (after.await_args.kwargs["embed"].title or "")

        thread = MagicMock()
        thread.mention = "<#901>"
        clicked = make_interaction()
        clicked.response.is_done = MagicMock(side_effect=[False, True])
        clicked.response.edit_message = AsyncMock()
        with (
            patch("yokai.cogs.rpg.launch_fight", new=AsyncMock(return_value=thread)) as go,
            patch("yokai.cogs.rpg.send", new=AsyncMock()),
        ):
            await confirm.start_button.callback(clicked)
        assert go.await_args.kwargs["promotion"] == PromotionRequest("E", "D", 5)
    finally:
        await db.close()


# ------------------------------------------------------------------------------ the card


@pytest.mark.asyncio
async def test_the_card_says_when_you_can_promote_or_must_wait(tmp_path: Path) -> None:
    db, _, cog = await make_world(tmp_path, level=5)
    try:
        target = MagicMock(spec=discord.Member)
        target.id, target.bot = USER, False

        async def promotion_field() -> str:
            with patch("yokai.cogs.rpg.send", new=AsyncMock()) as sent:
                await cog.card.callback(cog, make_interaction(222), member=target)
            return {f.name: f.value for f in sent.await_args.kwargs["embed"].fields}[
                "Next promotion"
            ]

        assert "ready: use /guild rank" in await promotion_field()
        until = int(time.time()) + 600
        await set_columns(db, promo_cooldown_until=until)
        assert f"recovering, try again <t:{until}:R>" in await promotion_field()
        await set_columns(db, promo_cooldown_until=None, level=2)
        text = await promotion_field()
        assert "ready" not in text and "recovering" not in text
    finally:
        await db.close()


# ----------------------------------------------------------------- the runner and result

GLASS = """\
[[enemy]]
id = "glass"
name = "Glass"
mult = { hp = 1, attack = 1, magic = 1, defense = 1, resist = 1, speed = 1, crit = 1 }
actions = [{ do = "attack", weight = 1 }]

[[enemy]]
id = "titan"
name = "Titan"
mult = { hp = 100000, attack = 100000, magic = 1, defense = 100, resist = 1, speed = 1, crit = 1 }
actions = [{ do = "attack", weight = 1 }]
"""


async def trial_runner(
    tmp_path: Path, boss: str
) -> tuple[Database, RpgRuntime, FightRunner, FakeThread, int]:
    content_dir = tmp_path / "content"
    shutil.copytree("content/rpg", content_dir)
    enemies = (content_dir / "enemies.toml").read_text(encoding="utf-8") + "\n" + GLASS
    (content_dir / "enemies.toml").write_text(enemies, encoding="utf-8")
    ranks = (content_dir / "ranks.toml").read_text(encoding="utf-8")
    pattern = re.compile(
        r'(name = "Trial of the Back Alleys"\nattempt_cost = 2\npower = 100\n)stages = \[.*?\n\]\n',
        re.S,
    )
    assert pattern.search(ranks)
    ranks = pattern.sub(
        lambda m: m.group(1) + f'stages = [{{ enemies = ["{boss}"], level = 5 }}]\n', ranks
    )
    (content_dir / "ranks.toml").write_text(ranks, encoding="utf-8")

    db = Database(tmp_path / "trial.db")
    await db.initialize()
    runtime = RpgRuntime.create(db, content_dir)
    await runtime.store.create_character(USER, "E", now=1)
    await set_columns(db, level=5)
    trial = runtime.content.promotion_mission("D")
    assert trial is not None
    fight_id = await runtime.store.begin_fight(
        mission_id=trial.id, kind="promotion", user_ids=[USER], cost=trial.attempt_cost,
        today="2026-10-14", base_attempts=8, streak_rules=runtime.content.balance.streak,
        promotion=PromotionRequest("E", "D", 5),
    )  # fmt: skip
    character = await runtime.store.get_character(USER)
    assert character is not None
    runtime.active_users.add(USER)
    thread = FakeThread()
    runner = FightRunner(
        bot=make_fight_bot(), runtime=runtime, fight_id=fight_id, mission=trial,
        participants=[Participant(USER, "Sami", character)], thread=thread,
        turn_seconds=0.2, rng=random.Random(3), idle_delay=0, archive_delay=0,
        promotion_to="D", promotion_cooldown_s=6 * 3600,
    )  # fmt: skip
    return db, runtime, runner, thread, fight_id


def fresh_interaction() -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock(spec=discord.Member)
    interaction.user.id = USER
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    return interaction


@pytest.mark.asyncio
async def test_winning_a_trial_promotes_pays_renown_but_no_xp_or_loot(tmp_path: Path) -> None:
    db, runtime, runner, thread, _ = await trial_runner(tmp_path, "glass")
    try:
        task = asyncio.create_task(runner.run())
        view = await wait_for_view(runner, 1)
        await view.attack_button.callback(fresh_interaction())
        await asyncio.wait_for(task, 10)

        final = thread.last_embed()
        fields = {f.name: f.value for f in final.fields}
        assert "Promotion passed" in (final.title or "")
        assert "You are now rank **D**" in (final.description or "")
        assert "Reward" in fields and "+0 XP" in fields["Reward"] and "Loot" not in fields
        character = await runtime.store.get_character(USER)
        assert character is not None
        assert (character.rank, character.xp, character.level) == ("D", 0, 5)
        assert character.promo_cooldown_until is None and character.battles_won == 1
        wallet = await db.get_wallet(USER)
        assert wallet is not None and wallet.balance > 200  # welcome bonus plus the Renown
        assert len(await runtime.gear.list_items(USER)) == 0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_losing_a_trial_starts_the_cooldown_and_says_when_to_retry(tmp_path: Path) -> None:
    db, runtime, runner, thread, _ = await trial_runner(tmp_path, "titan")
    try:
        await asyncio.wait_for(runner.run(), 10)
        final = thread.last_embed()
        assert "Promotion failed" in (final.title or "")
        character = await runtime.store.get_character(USER)
        assert character is not None and character.rank == "E"
        assert character.promo_cooldown_until is not None
        assert character.promo_cooldown_until > int(time.time()) + 6 * 3600 - 60
        assert f"<t:{character.promo_cooldown_until}:R>" in (final.description or "")
        assert not [f for f in final.fields if f.name in ("Reward", "Rewards")]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_trial_that_crashes_refunds_and_sets_no_cooldown(tmp_path: Path) -> None:
    db, runtime, runner, thread, fight_id = await trial_runner(tmp_path, "titan")
    try:
        thread.fail_send = True
        await asyncio.wait_for(runner.run(), 10)
        character = await runtime.store.get_character(USER)
        assert character is not None
        assert (character.attempts_used, character.promo_cooldown_until) == (0, None)
        async with db.conn.execute("SELECT status FROM rpg_fights WHERE id = ?;", (fight_id,)) as c:
            row = await c.fetchone()
        assert row is not None and row[0] == "aborted"
    finally:
        await db.close()


# ------------------------------------------------------------------ the leaderboard view


async def add(
    runtime: RpgRuntime, db: Database, user: int, rank: str, level: int, wins: int
) -> None:
    await runtime.store.create_character(user, rank, now=user)
    await set_columns(db, user, level=level, battles_won=wins)


def line_users(embed: discord.Embed) -> list[str]:
    return re.findall(r"<@(\d+)>", embed.description or "")


@pytest.mark.asyncio
async def test_the_leaderboard_command_is_public_and_starts_on_rank(tmp_path: Path) -> None:
    db, runtime, cog = await make_world(tmp_path)
    try:
        await runtime.store.admin_reset_character(1, USER)
        await add(runtime, db, 1, "E", 10, 50)
        await add(runtime, db, 2, "SS", 47, 5)
        await add(runtime, db, 3, "A", 33, 20)
        interaction = make_interaction()
        with patch("yokai.cogs.rpg.send", new=AsyncMock()) as sent:
            await cog.leaderboard.callback(cog, interaction, sort=None, page=None)
        interaction.response.defer.assert_awaited_once_with(thinking=True)
        assert "ephemeral" not in sent.await_args.kwargs
        embed = sent.await_args.kwargs["embed"]
        assert line_users(embed) == ["2", "3", "1"]
        assert "rank **SS**" in (embed.description or "") and "By rank" in (embed.footer.text or "")
        view = sent.await_args.kwargs["view"]
        assert isinstance(view, GuildLeaderboardView) and view.allowed_user_ids is None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_the_sort_dropdown_and_pages_work(tmp_path: Path) -> None:
    db, runtime, cog = await make_world(tmp_path)
    try:
        await runtime.store.admin_reset_character(1, USER)
        for n in range(1, 13):
            await add(runtime, db, n, "E", n, 100 - n)
        view = GuildLeaderboardView(cog.bot, runtime, "level")
        first = await view.build_embed()
        assert line_users(first)[:3] == ["12", "11", "10"] and len(line_users(first)) == 10
        assert "page 1/2" in (first.footer.text or "") and view.prev_button.disabled
        assert not view.next_button.disabled

        nxt = make_interaction()
        nxt.response.edit_message = AsyncMock()
        await view.next_button.callback(nxt)
        second = nxt.response.edit_message.await_args.kwargs["embed"]
        assert line_users(second) == ["2", "1"] and view.next_button.disabled

        picked = make_interaction()
        picked.response.edit_message = AsyncMock()
        select = next(i for i in view.children if isinstance(i, discord.ui.Select))
        with patch.object(type(select), "values", ["wins"], create=True):
            await select.callback(picked)
        by_wins = picked.response.edit_message.await_args.kwargs["embed"]
        assert line_users(by_wins)[:2] == ["1", "2"] and "By battles won" in (
            by_wins.footer.text or ""
        )
        assert view.page == 1
        assert [o.default for o in select.options] == [False, False, True]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_the_leaderboard_copes_with_an_empty_guild_and_a_far_page(tmp_path: Path) -> None:
    db, runtime, cog = await make_world(tmp_path)
    try:
        await runtime.store.admin_reset_character(1, USER)
        empty = await GuildLeaderboardView(cog.bot, runtime).build_embed()
        assert "Nobody has joined" in (empty.description or "")
        await add(runtime, db, 1, "E", 1, 0)
        far = GuildLeaderboardView(cog.bot, runtime, "rank", page=50)
        embed = await far.build_embed()
        assert line_users(embed) == ["1"] and far.page == 1
        bad = GuildLeaderboardView(cog.bot, runtime, "renown")
        assert bad.sort == "rank"
    finally:
        await db.close()


# ------------------------------------------------------------------------------ embeds


def test_rank_status_and_leaderboard_embeds_respect_the_layout_rules() -> None:
    status = EmbedFactory.rank_status("E", 5, ["Next: **Rank D**", "You can challenge now."], True)
    assert status.color.value == 0x00E676 and emoji_count(status) == 1
    waiting = EmbedFactory.rank_status("E", 3, ["You need 2 more level(s)."], False)
    assert waiting.color.value != 0x00E676
    rows = [(i, 1000 + i, "SS+", 60, 12345) for i in range(1, 11)]
    board = EmbedFactory.guild_leaderboard(rows, "rank", 1, 3)
    assert emoji_count(board) == 1 and total_embed_length(board) <= 6000
    assert "12,345 win(s)" in (board.description or "") and "page 1/3" in (board.footer.text or "")
    assert "Nobody" in (EmbedFactory.guild_leaderboard([], "level", 1, 1).description or "")


def test_the_promotion_result_variants() -> None:
    won = EmbedFactory.fight_result(True, "Trial", 4, [("<@1>", 0, 40, 0)], [], promotion_to="D")
    assert "Promotion passed" in (won.title or "") and "rank **D**" in (won.description or "")
    lost = EmbedFactory.fight_result(
        False, "Trial", 6, [], [], promotion_to="D", cooldown_ts=1_900_000_000
    )
    assert "Promotion failed" in (lost.title or "") and "<t:1900000000:R>" in (
        lost.description or ""
    )
    plain = EmbedFactory.fight_result(False, "Mission", 6, [], [])
    assert "Mission failed" in (plain.title or "") and "No rewards" in (plain.description or "")
    assert emoji_count(won) == 1 and emoji_count(lost) == 1
