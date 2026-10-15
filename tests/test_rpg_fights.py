"""Tests for the fight runner and its views, with a fake thread and a real database."""

from __future__ import annotations

import asyncio
import random
from pathlib import Path
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from tests.rpg_helpers import BALANCE, mutate, write_content_dir
from yokai.errors import AlreadyFightingError, ThreadUnavailableError
from yokai.rpg.combat.engine import Fight
from yokai.rpg.combat.models import Action
from yokai.rpg.fights import FightRunner, Participant, launch_fight
from yokai.rpg.models import SkillDef, Stats
from yokai.rpg.runtime import RpgRuntime
from yokai.storage.db import Database
from yokai.ui.rpg_views import FightView, SkillSelect, TargetSelect

ENEMIES = """\
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

[[enemy]]
id = "sponge"
name = "Sponge"
mult = { hp = 100000, attack = 1, magic = 1, defense = 100, resist = 100, speed = 1, crit = 1 }
actions = [{ do = "attack", weight = 1 }]
"""

MISSIONS = """\
[[mission]]
id = "m_glass"
name = "Glass Test"
rank = "E"
attempt_cost = 1
stages = [{ enemies = ["glass"], level = 1 }]

[[mission]]
id = "m_glass_pair"
name = "Glass Pair"
rank = "E"
attempt_cost = 1
stages = [{ enemies = ["glass", "glass"], level = 1 }]

[[mission]]
id = "m_titan"
name = "Titan Test"
rank = "E"
attempt_cost = 1
stages = [{ enemies = ["titan"], level = 1 }]

[[mission]]
id = "m_sponge"
name = "Sponge Test"
rank = "E"
attempt_cost = 1
stages = [{ enemies = ["sponge"], level = 1 }]
"""

WELCOME = 200
USER = 7


class FakeMessage:
    def __init__(self) -> None:
        self.edits: list[tuple[Optional[discord.Embed], Any]] = []

    async def edit(self, *, embed: Optional[discord.Embed] = None, view: Any = None) -> None:
        self.edits.append((embed, view))


class FakeThread:
    id = 555

    def __init__(self, fail_send: bool = False) -> None:
        self.message = FakeMessage()
        self.sent: list[tuple[Optional[discord.Embed], Any]] = []
        self.thread_edits: list[dict[str, Any]] = []
        self.added: list[Any] = []
        self.fail_send = fail_send

    async def send(self, *, embed: Optional[discord.Embed] = None, view: Any = None, **kwargs: Any):  # type: ignore[no-untyped-def]
        if self.fail_send:
            raise RuntimeError("cannot post")
        self.sent.append((embed, view))
        return self.message

    async def edit(self, **kwargs: Any) -> None:
        self.thread_edits.append(kwargs)

    async def add_user(self, user: Any) -> None:
        self.added.append(user)

    def all_embeds(self) -> list[discord.Embed]:
        embeds = [e for e, _ in self.sent] + [e for e, _ in self.message.edits]
        return [e for e in embeds if e is not None]

    def last_embed(self) -> discord.Embed:
        return self.all_embeds()[-1]


def make_interaction(user_id: int = USER) -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock(spec=discord.Member)
    interaction.user.id = user_id
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.response.edit_message = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.original_response = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


def make_bot() -> MagicMock:
    bot = MagicMock()
    bot.user = None
    bot.config.economy_welcome_bonus = WELCOME
    return bot


async def build(
    tmp_path: Path,
    mission_id: str,
    *,
    turn_seconds: float = 0.2,
    thread: Optional[FakeThread] = None,
    max_rounds: int = 40,
    idle_delay: float = 0,
) -> tuple[Database, RpgRuntime, FightRunner, FakeThread]:
    db = Database(tmp_path / "fight.db")
    await db.initialize()
    content_dir = tmp_path / "content"
    content_dir.mkdir()
    balance = mutate(BALANCE, "max_rounds = 40", f"max_rounds = {max_rounds}")
    write_content_dir(content_dir, enemies=ENEMIES, missions=MISSIONS, balance=balance)
    runtime = RpgRuntime.create(db, content_dir)
    await runtime.store.create_character(USER, "E", now=1)
    mission = runtime.content.missions[mission_id]
    fight_id = await runtime.store.begin_fight(
        mission_id=mission.id,
        kind="mission",
        user_ids=[USER],
        cost=1,
        today="2026-10-14",
        base_attempts=8,
        streak_rules=runtime.content.balance.streak,
    )
    character = await runtime.store.get_character(USER)
    assert character is not None
    runtime.active_users.add(USER)
    fake = thread or FakeThread()
    runner = FightRunner(
        bot=make_bot(),
        runtime=runtime,
        fight_id=fight_id,
        mission=mission,
        participants=[Participant(USER, "Sami", character)],
        thread=fake,  # type: ignore[arg-type]
        turn_seconds=turn_seconds,
        rng=random.Random(1),
        idle_delay=idle_delay,
        archive_delay=0,
    )
    return db, runtime, runner, fake


async def wait_for_view(runner: FightRunner, round_no: int) -> FightView:
    for _ in range(400):
        view = runner.view
        if view is not None and view.round_no == round_no and not view.closed:
            return view
        await asyncio.sleep(0.005)
    raise AssertionError(f"no live view for round {round_no}")


async def fight_status(db: Database, fight_id: int) -> str:
    async with db.conn.execute("SELECT status FROM rpg_fights WHERE id = ?;", (fight_id,)) as cur:
        row = await cur.fetchone()
    assert row is not None
    return row[0]


@pytest.mark.asyncio
async def test_a_won_fight_pays_posts_the_result_and_archives_the_thread(tmp_path: Path) -> None:
    db, runtime, runner, thread = await build(tmp_path, "m_glass")
    try:
        task = asyncio.create_task(runner.run())
        view = await wait_for_view(runner, 1)
        await view.attack_button.callback(make_interaction())
        await asyncio.wait_for(task, 5)

        assert await fight_status(db, runner.fight_id) == "won"
        character = await runtime.store.get_character(USER)
        assert character is not None and character.battles_won == 1 and character.xp == 3
        wallet = await db.get_wallet(USER)
        assert wallet is not None and wallet.balance == WELCOME + 4
        final = thread.last_embed()
        assert "Mission complete" in (final.title or "")
        assert any("+3 XP" in f.value for f in final.fields)
        assert USER not in runtime.active_users
        assert thread.thread_edits == [{"archived": True, "locked": True}]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_lost_fight_pays_nothing(tmp_path: Path) -> None:
    db, runtime, runner, thread = await build(tmp_path, "m_titan", turn_seconds=0.05)
    try:
        await asyncio.wait_for(runner.run(), 5)
        assert await fight_status(db, runner.fight_id) == "lost"
        character = await runtime.store.get_character(USER)
        assert character is not None and character.battles_lost == 1 and character.xp == 0
        assert await db.get_wallet(USER) is None
        assert "Mission failed" in (thread.last_embed().title or "")
        assert USER not in runtime.active_users
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_player_who_never_acts_is_marked_away_and_the_fight_stops_waiting(
    tmp_path: Path,
) -> None:
    """Review Focus 5: two missed rounds mean away; an all-away party never stalls the fight."""
    db, runtime, runner, thread = await build(tmp_path, "m_sponge", turn_seconds=0.05, max_rounds=6)
    try:
        await asyncio.wait_for(runner.run(), 5)
        assert runner.missed[USER] == 2  # it would keep counting if the runner still waited
        assert runner.fight.outcome == "lost"
        assert await fight_status(db, runner.fight_id) == "lost"
        assert runner.fight.round_no - 1 == 6  # it ran to max_rounds, then stopped
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_an_away_player_can_come_back_by_acting_during_the_idle_window(
    tmp_path: Path,
) -> None:
    db, runtime, runner, thread = await build(
        tmp_path, "m_sponge", turn_seconds=0.05, max_rounds=3, idle_delay=0.4
    )
    try:
        task = asyncio.create_task(runner.run())
        view = await wait_for_view(runner, 3)  # rounds 1 and 2 timed out, so the player is away
        assert runner.missed[USER] == 2
        await view.attack_button.callback(make_interaction())  # they come back during round 3
        await asyncio.wait_for(task, 5)
        assert runner.missed[USER] == 0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_stale_button_from_a_resolved_round_is_rejected(tmp_path: Path) -> None:
    """Review Focus 7 (part): an old view cannot lock in an action for the next round."""
    db, runtime, runner, thread = await build(tmp_path, "m_sponge", turn_seconds=0.05, max_rounds=3)
    try:
        task = asyncio.create_task(runner.run())
        old = await wait_for_view(runner, 1)
        await wait_for_view(runner, 2)
        interaction = make_interaction()
        with patch("yokai.ui.table_common.send", new=AsyncMock()) as sent:
            await old.attack_button.callback(interaction)
        assert "already" in (sent.await_args.kwargs["embed"].description or "")
        assert not runner.fight.has_chosen("p7")
        await asyncio.wait_for(task, 5)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_only_the_fighters_may_press_the_buttons(tmp_path: Path) -> None:
    db, runtime, runner, thread = await build(tmp_path, "m_sponge", turn_seconds=0.3, max_rounds=2)
    try:
        task = asyncio.create_task(runner.run())
        view = await wait_for_view(runner, 1)
        with (
            patch("yokai.ui.rpg_views.screen", new=AsyncMock(return_value=False)),
            patch("yokai.ui.table_common.send", new=AsyncMock()) as sent,
        ):
            assert await view.interaction_check(make_interaction(USER)) is True
            assert await view.interaction_check(make_interaction(999)) is False
        assert sent.await_args.kwargs["ephemeral"] is True
        assert "Only the adventurers" in (sent.await_args.kwargs["embed"].description or "")
        with patch("yokai.ui.rpg_views.screen", new=AsyncMock(return_value=True)):
            assert await view.interaction_check(make_interaction(USER)) is False
        await asyncio.wait_for(task, 5)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_locking_in_twice_is_rejected_and_defend_counts_as_a_choice(tmp_path: Path) -> None:
    db, runtime, runner, thread = await build(tmp_path, "m_sponge", turn_seconds=1.0, max_rounds=2)
    try:
        task = asyncio.create_task(runner.run())
        view = await wait_for_view(runner, 1)
        await view.defend_button.callback(make_interaction())
        # The runner moves on at once; press again during the same round's remaining moments
        interaction = make_interaction()
        with patch("yokai.ui.table_common.send", new=AsyncMock()) as sent:
            await view.defend_button.callback(interaction)
        assert sent.await_args is not None  # told it is too late or already locked
        await asyncio.wait_for(task, 5)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_crash_before_settlement_refunds_the_attempts(tmp_path: Path) -> None:
    db, runtime, runner, thread = await build(
        tmp_path, "m_glass", thread=FakeThread(fail_send=True)
    )
    try:
        await asyncio.wait_for(runner.run(), 5)
        assert await fight_status(db, runner.fight_id) == "aborted"
        character = await runtime.store.get_character(USER)
        assert character is not None and character.attempts_used == 0
        assert USER not in runtime.active_users
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_cancelling_a_running_fight_refunds_the_attempts(tmp_path: Path) -> None:
    db, runtime, runner, thread = await build(tmp_path, "m_sponge", turn_seconds=5)
    try:
        task = asyncio.create_task(runner.run())
        await wait_for_view(runner, 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await fight_status(db, runner.fight_id) == "aborted"
        character = await runtime.store.get_character(USER)
        assert character is not None and character.attempts_used == 0
        assert USER not in runtime.active_users
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_fight_with_two_foes_asks_for_a_target(tmp_path: Path) -> None:
    db, runtime, runner, thread = await build(tmp_path, "m_glass_pair", turn_seconds=2)
    try:
        task = asyncio.create_task(runner.run())
        view = await wait_for_view(runner, 1)
        interaction = make_interaction()
        await view.attack_button.callback(interaction)
        assert not runner.fight.has_chosen("p7")
        prompt = interaction.response.send_message.await_args.kwargs["view"]
        select = prompt.children[0]
        assert isinstance(select, TargetSelect) and len(select.options) == 2

        pick = make_interaction()
        with patch.object(TargetSelect, "values", ["f1_1"], create=True):
            await select.callback(pick)
        assert runner.fight.has_chosen("p7")
        pick.response.edit_message.assert_awaited_once()
        await asyncio.wait_for(task, 5)
        assert runner.fight.foes[0].hp == 1 or runner.fight.finished
    finally:
        await db.close()


# ------------------------------------------------------------------ skills in the view


def skill_fight() -> tuple[Fight, FightView]:
    from yokai.rpg.combat.models import SIDE_FOES, SIDE_PLAYERS, Combatant
    from yokai.rpg.models import CombatRules

    skills = {
        "smash": SkillDef("smash", "Smash", "damage", "foe", power=200, cooldown=2),
        "mend": SkillDef("mend", "Mend", "heal", "ally", scaling="magic", power=100),
    }
    rules = CombatRules(40, 150, 75, 0, 50, 10, 40)
    hero = Combatant("p1", "Hero", SIDE_PLAYERS, Stats(100, 40, 40, 0, 0, 10, 0), 100,
                     skills=("smash", "mend"), user_id=1)  # fmt: skip
    ally = Combatant("p2", "Ally", SIDE_PLAYERS, Stats(100, 40, 40, 0, 0, 10, 0), 100, user_id=2)
    foes = [
        Combatant(f"f{i}", f"Foe {i}", SIDE_FOES, Stats(1000, 1, 1, 0, 0, 1, 0), 1000)
        for i in (1, 2)
    ]
    fight = Fight([hero, ally], [foes], skills, rules, random.Random(1))
    view = FightView(
        bot=make_bot(),
        fight=fight,
        player_ids={1, 2},
        waiting_for={1, 2},
        refresh=AsyncMock(),
    )
    return fight, view


@pytest.mark.asyncio
async def test_the_skill_button_exists_only_when_someone_has_skills() -> None:
    _, view = skill_fight()
    assert any(getattr(item, "label", None) == "Skill" for item in view.children)
    plain = FightView(
        bot=make_bot(),
        fight=Fight(
            [MagicMock(skills=(), alive=True, id="p1", user_id=1)],
            [[MagicMock()]],
            {},
            MagicMock(),
            random.Random(1),
        ),
        player_ids={1},
        waiting_for=set(),
        refresh=AsyncMock(),
    )
    assert not any(getattr(item, "label", None) == "Skill" for item in plain.children)


@pytest.mark.asyncio
async def test_choosing_a_foe_targeted_skill_asks_for_a_target_then_locks_in() -> None:
    fight, view = skill_fight()
    opener = make_interaction(1)
    await view.skill_button.callback(opener)
    prompt = opener.response.send_message.await_args.kwargs["view"]
    skill_select = prompt.children[0]
    assert isinstance(skill_select, SkillSelect)
    assert [o.value for o in skill_select.options] == ["smash", "mend"]

    chosen = make_interaction(1)
    with patch.object(SkillSelect, "values", ["smash"], create=True):
        await skill_select.callback(chosen)
    target_view = chosen.response.edit_message.await_args.kwargs["view"]
    target_select = target_view.children[0]
    assert isinstance(target_select, TargetSelect) and len(target_select.options) == 2

    aimed = make_interaction(1)
    with patch.object(TargetSelect, "values", ["f2"], create=True):
        await target_select.callback(aimed)
    assert fight.has_chosen("p1")
    assert fight._pending["p1"] == Action("skill", skill_id="smash", target_id="f2")  # noqa: SLF001
    view.refresh.assert_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_an_ally_skill_with_two_allies_asks_who_to_help() -> None:
    fight, view = skill_fight()
    opener = make_interaction(1)
    await view.skill_button.callback(opener)
    skill_select = opener.response.send_message.await_args.kwargs["view"].children[0]
    chosen = make_interaction(1)
    with patch.object(SkillSelect, "values", ["mend"], create=True):
        await skill_select.callback(chosen)
    target_select = chosen.response.edit_message.await_args.kwargs["view"].children[0]
    assert {o.value for o in target_select.options} == {"p1", "p2"}


@pytest.mark.asyncio
async def test_the_skill_button_says_so_when_nothing_is_ready() -> None:
    fight, view = skill_fight()
    fight.players[0].cooldowns = {"smash": 2, "mend": 2}
    with patch("yokai.ui.table_common.send", new=AsyncMock()) as sent:
        await view.skill_button.callback(make_interaction(1))
    assert "None of your skills are ready" in (sent.await_args.kwargs["embed"].description or "")


@pytest.mark.asyncio
async def test_the_round_wakes_up_once_every_waited_for_player_has_chosen() -> None:
    fight, view = skill_fight()
    assert not view.all_chosen.is_set()
    await view.defend_button.callback(make_interaction(1))
    assert not view.all_chosen.is_set()
    await view.defend_button.callback(make_interaction(2))
    assert view.all_chosen.is_set()


@pytest.mark.asyncio
async def test_a_downed_player_cannot_act() -> None:
    fight, view = skill_fight()
    fight.players[1].hp = 0
    with patch("yokai.ui.table_common.send", new=AsyncMock()) as sent:
        await view.attack_button.callback(make_interaction(2))
    assert "down" in (sent.await_args.kwargs["embed"].description or "")
    assert not fight.has_chosen("p2")


# ------------------------------------------------------------------ launching


def make_channel(fail: bool = False) -> MagicMock:
    channel = MagicMock()
    channel.id = 42
    thread = FakeThread()
    if fail:
        channel.create_thread = AsyncMock(side_effect=discord.Forbidden(MagicMock(), "no"))
    else:
        channel.create_thread = AsyncMock(return_value=thread)
    channel.thread = thread
    return channel


async def launch(tmp_path: Path, channel: MagicMock, runtime: RpgRuntime, **kw: Any):  # type: ignore[no-untyped-def]
    member = MagicMock()
    member.id = USER
    character = await runtime.store.get_character(USER)
    assert character is not None
    return await launch_fight(
        bot=make_bot(),
        runtime=runtime,
        channel=channel,
        member=member,
        member_name="Sami",
        character=character,
        mission=runtime.content.missions["m_sponge"],
        today="2026-10-14",
        resets_ts=1_900_000_000,
        base_attempts=kw.get("base_attempts", 8),
        turn_seconds=5,
    )


async def runtime_with_character(tmp_path: Path) -> tuple[Database, RpgRuntime]:
    db = Database(tmp_path / "launch.db")
    await db.initialize()
    content_dir = tmp_path / "content"
    content_dir.mkdir()
    write_content_dir(content_dir, enemies=ENEMIES, missions=MISSIONS)
    runtime = RpgRuntime.create(db, content_dir)
    await runtime.store.create_character(USER, "E", now=1)
    return db, runtime


@pytest.mark.asyncio
async def test_launching_spends_attempts_opens_a_thread_and_starts_the_fight(
    tmp_path: Path,
) -> None:
    db, runtime = await runtime_with_character(tmp_path)
    channel = make_channel()
    try:
        thread = await launch(tmp_path, channel, runtime)
        assert thread is channel.thread
        assert USER in runtime.active_users and len(runtime.tasks) == 1
        kwargs = channel.create_thread.await_args.kwargs
        assert kwargs["type"] == discord.ChannelType.public_thread
        assert "Sponge Test" in kwargs["name"] and "Sami" in kwargs["name"]
        character = await runtime.store.get_character(USER)
        assert character is not None and character.attempts_used == 1
        for task in list(runtime.tasks):
            task.cancel()
        await asyncio.gather(*runtime.tasks, return_exceptions=True)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_launching_while_already_fighting_is_refused_and_changes_nothing(
    tmp_path: Path,
) -> None:
    db, runtime = await runtime_with_character(tmp_path)
    try:
        runtime.active_users.add(USER)
        with pytest.raises(AlreadyFightingError):
            await launch(tmp_path, make_channel(), runtime)
        character = await runtime.store.get_character(USER)
        assert character is not None and character.attempts_used == 0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_thread_that_cannot_be_opened_refunds_the_attempts(tmp_path: Path) -> None:
    db, runtime = await runtime_with_character(tmp_path)
    try:
        with pytest.raises(ThreadUnavailableError):
            await launch(tmp_path, make_channel(fail=True), runtime)
        character = await runtime.store.get_character(USER)
        assert character is not None and character.attempts_used == 0
        assert USER not in runtime.active_users
        async with db.conn.execute("SELECT status FROM rpg_fights;") as cursor:
            assert [row[0] for row in await cursor.fetchall()] == ["aborted"]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_launching_without_attempts_releases_the_active_slot(tmp_path: Path) -> None:
    from yokai.errors import NoAttemptsError

    db, runtime = await runtime_with_character(tmp_path)
    try:
        with pytest.raises(NoAttemptsError):
            await launch(tmp_path, make_channel(), runtime, base_attempts=0)
        assert USER not in runtime.active_users
    finally:
        await db.close()
