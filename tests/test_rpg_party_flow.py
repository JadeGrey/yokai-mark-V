"""Party lobbies in play: the lobby view, /guild mission entry points, the host rules, the
all-or-nothing launch and a shared fight."""

from __future__ import annotations

import asyncio
import random
from pathlib import Path
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from tests.embed_helpers import emoji_count, total_embed_length
from tests.rpg_helpers import write_content_dir
from tests.test_rpg_fights import (
    ENEMIES,
    MISSIONS,
    FakeThread,
    wait_for_view,
)
from tests.test_rpg_fights import (
    make_bot as make_fight_bot,
)
from yokai.cogs.rpg import RpgCog, _LobbyHost
from yokai.errors import (
    AlreadyFightingError,
    MissionRankError,
    NoAttemptsError,
    NoCharacterError,
    ThreadUnavailableError,
    YokaiError,
)
from yokai.rpg.fights import Entrant, FightRunner, Participant, launch_party_fight
from yokai.rpg.models import MAX_PARTY
from yokai.rpg.party import Lobby, PartyMember
from yokai.rpg.runtime import RpgRuntime
from yokai.storage.db import Database
from yokai.ui.embeds import EmbedFactory
from yokai.ui.party_views import CLOSED, OPEN, PartyLobbyView

LEADER = 1
RAID_ID = "raid_c_bandit_fortress"


def interaction(user_id: int) -> MagicMock:
    """A fake interaction whose response tracks whether it has been answered."""
    state = {"done": False}

    def answered(*_: Any, **__: Any) -> None:
        state["done"] = True

    click = MagicMock(spec=discord.Interaction)
    click.user = MagicMock(spec=discord.Member)
    click.user.id = user_id
    click.user.bot = False
    click.user.display_name = f"Member {user_id}"
    click.response = MagicMock()
    click.response.defer = AsyncMock(side_effect=answered)
    click.response.edit_message = AsyncMock(side_effect=answered)
    click.response.send_message = AsyncMock(side_effect=answered)
    click.response.is_done = MagicMock(side_effect=lambda: state["done"])
    click.edit_original_response = AsyncMock()
    click.original_response = AsyncMock()
    click.followup = MagicMock()
    click.followup.send = AsyncMock()
    click.channel = MagicMock(spec=discord.TextChannel)
    return click


class FakeHost:
    """Stands in for the cog's lobby host."""

    def __init__(self) -> None:
        self.admit_error: Optional[YokaiError] = None
        self.dropped: dict[int, YokaiError] = {}
        self.launch_error: Optional[YokaiError] = None
        self.heads_up: Optional[str] = None
        self.launches: list[list[int]] = []
        self.thread = MagicMock()
        self.thread.mention = "<#900>"

    async def admit(self, user: Any) -> tuple[PartyMember, Optional[str]]:
        if self.admit_error:
            raise self.admit_error
        return PartyMember(user.id, f"Member {user.id}", "C", 14), self.heads_up

    async def rejected(self, lobby: Lobby) -> dict[int, YokaiError]:
        return {uid: err for uid, err in self.dropped.items() if uid in lobby.members}

    async def launch(self, lobby: Lobby) -> Any:
        if self.launch_error:
            raise self.launch_error
        self.launches.append(list(lobby.members))
        return self.thread


def make_view(
    raid: bool = True, *, timeout: float = 180.0
) -> tuple[PartyLobbyView, FakeHost, Lobby]:
    from pathlib import Path as P

    from yokai.rpg.content import load_content

    content = load_content(P("content/rpg"))
    mission = content.missions[RAID_ID if raid else "c_ox_pasture"]
    lobby = Lobby(mission, leader_id=LEADER)
    lobby.add(PartyMember(LEADER, "Member 1", "C", 14))
    host = FakeHost()
    bot = MagicMock()
    bot.user = None
    view = PartyLobbyView(bot, lobby, host, timeout=timeout)
    view.message = MagicMock()
    view.message.edit = AsyncMock()
    return view, host, lobby


def last_embed(sent: AsyncMock) -> discord.Embed:
    return sent.await_args.kwargs["embed"]


# --------------------------------------------------------------------------- joining


@pytest.mark.asyncio
async def test_joining_takes_a_seat_and_redraws_the_lobby() -> None:
    view, host, lobby = make_view()
    click = interaction(2)
    with patch("yokai.ui.party_views.send", new=AsyncMock()) as sent:
        await view.join_button.callback(click)
    assert list(lobby.members) == [LEADER, 2]
    embed = click.response.edit_message.await_args.kwargs["embed"]
    assert "Party (2/4)" in [f.name for f in embed.fields]
    assert "<@2>" in embed.fields[0].value and "Raid lobby" in (embed.title or "")
    sent.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_joiner_with_worn_gear_gets_a_private_heads_up() -> None:
    view, host, lobby = make_view()
    host.heads_up = "Your gear is worn: Rusty Blade: 20%."
    with patch("yokai.ui.party_views.send", new=AsyncMock()) as sent:
        await view.join_button.callback(interaction(2))
    assert sent.await_args.kwargs["ephemeral"] is True
    assert "Rusty Blade" in (last_embed(sent).description or "")
    assert 2 in lobby.members


@pytest.mark.asyncio
async def test_joining_is_refused_when_ineligible_twice_or_full() -> None:
    view, host, lobby = make_view()
    host.admit_error = MissionRankError("C", "D")
    with patch("yokai.ui.party_views.send", new=AsyncMock()) as sent:
        await view.join_button.callback(interaction(2))
        assert "Rank too low" in (last_embed(sent).title or "") and 2 not in lobby.members
        host.admit_error = None
        await view.join_button.callback(interaction(LEADER))  # already seated
        assert "Already in the party" in (last_embed(sent).title or "")
        for user in (2, 3, 4):
            await view.join_button.callback(interaction(user))
        assert lobby.size == MAX_PARTY and view.join_button.disabled
        await view.join_button.callback(interaction(5))
        assert "Party is full" in (last_embed(sent).title or "") and 5 not in lobby.members
        assert sent.await_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_leaving_frees_the_seat_but_the_leader_must_cancel() -> None:
    view, host, lobby = make_view()
    with patch("yokai.ui.party_views.send", new=AsyncMock()) as sent:
        await view.join_button.callback(interaction(2))
        await view.leave_button.callback(interaction(2))
        assert list(lobby.members) == [LEADER] and not view.join_button.disabled
        await view.leave_button.callback(interaction(LEADER))
        assert "You're the leader" in (last_embed(sent).title or "") and LEADER in lobby.members
        await view.leave_button.callback(interaction(9))
        assert "Not in the party" in (last_embed(sent).title or "")


# --------------------------------------------------------------------------- starting


@pytest.mark.asyncio
async def test_start_is_leader_only_and_needs_enough_members() -> None:
    view, host, lobby = make_view(raid=True)
    with patch("yokai.ui.party_views.send", new=AsyncMock()) as sent:
        await view.join_button.callback(interaction(2))
        await view.start_button.callback(interaction(2))
        assert "Leader only" in (last_embed(sent).title or "")
        await view.leave_button.callback(interaction(2))
        await view.start_button.callback(interaction(LEADER))
        assert "at least 2" in (last_embed(sent).description or "")
    assert host.launches == [] and view.state == OPEN


@pytest.mark.asyncio
async def test_start_launches_once_and_closes_the_lobby() -> None:
    view, host, lobby = make_view()
    with patch("yokai.ui.party_views.send", new=AsyncMock()):
        await view.join_button.callback(interaction(2))
        await view.join_button.callback(interaction(3))
        click = interaction(LEADER)
        await view.start_button.callback(click)
        assert host.launches == [[LEADER, 2, 3]] and view.state == CLOSED
        final = click.edit_original_response.await_args.kwargs
        assert final["view"] is None and "Party set out" in (final["embed"].title or "")
        assert "<#900>" in (final["embed"].description or "")
        # a second press (or a late join) only acknowledges
        again = interaction(LEADER)
        await view.start_button.callback(again)
        await view.join_button.callback(interaction(4))
    assert len(host.launches) == 1 and 4 not in lobby.members
    again.response.defer.assert_awaited_once()


@pytest.mark.asyncio
async def test_two_simultaneous_starts_launch_one_fight() -> None:
    view, host, lobby = make_view(raid=False)
    with patch("yokai.ui.party_views.send", new=AsyncMock()):
        await asyncio.gather(
            view.start_button.callback(interaction(LEADER)),
            view.start_button.callback(interaction(LEADER)),
        )
    assert len(host.launches) == 1


@pytest.mark.asyncio
async def test_members_who_became_ineligible_are_removed_and_the_leader_starts_again() -> None:
    view, host, lobby = make_view()
    with patch("yokai.ui.party_views.send", new=AsyncMock()):
        await view.join_button.callback(interaction(2))
        await view.join_button.callback(interaction(3))
        host.dropped = {3: NoAttemptsError(0, 3, 1_900_000_000)}
        click = interaction(LEADER)
        await view.start_button.callback(click)
        assert host.launches == [] and list(lobby.members) == [LEADER, 2]
        assert view.state == OPEN
        redraw = click.edit_original_response.await_args.kwargs["embed"]
        assert "Heads up" in [f.name for f in redraw.fields]
        assert "<@3>" in redraw.fields[-1].value
        host.dropped = {}
        await view.start_button.callback(interaction(LEADER))
    assert host.launches == [[LEADER, 2]]


@pytest.mark.asyncio
async def test_a_leader_who_can_no_longer_go_closes_the_lobby() -> None:
    view, host, lobby = make_view()
    with patch("yokai.ui.party_views.send", new=AsyncMock()):
        await view.join_button.callback(interaction(2))
        host.dropped = {LEADER: AlreadyFightingError()}
        click = interaction(LEADER)
        await view.start_button.callback(click)
    assert view.state == CLOSED and host.launches == []
    final = click.edit_original_response.await_args.kwargs
    assert "Lobby closed" in (final["embed"].title or "") and final["view"] is None


@pytest.mark.asyncio
async def test_a_failed_launch_reopens_the_lobby_with_the_error() -> None:
    view, host, lobby = make_view()
    with patch("yokai.ui.party_views.send", new=AsyncMock()) as sent:
        await view.join_button.callback(interaction(2))
        host.launch_error = ThreadUnavailableError()
        click = interaction(LEADER)
        await view.start_button.callback(click)
        assert view.state == OPEN and list(lobby.members) == [LEADER, 2]
        assert not view.start_button.disabled
        assert (
            "thread"
            in (last_embed(sent).title or "").lower() + (last_embed(sent).description or "").lower()
        )
        host.launch_error = None
        await view.start_button.callback(interaction(LEADER))
    assert host.launches == [[LEADER, 2]]


# ------------------------------------------------------------------- cancel and timeout


@pytest.mark.asyncio
async def test_only_the_leader_can_cancel_and_nothing_is_charged() -> None:
    view, host, lobby = make_view()
    with patch("yokai.ui.party_views.send", new=AsyncMock()) as sent:
        await view.cancel_button.callback(interaction(2))
        assert "Leader only" in (last_embed(sent).title or "") and view.state == OPEN
        click = interaction(LEADER)
        await view.cancel_button.callback(click)
    assert view.state == CLOSED and host.launches == []
    final = click.response.edit_message.await_args.kwargs
    assert "Lobby cancelled" in (final["embed"].title or "") and final["view"] is None


@pytest.mark.asyncio
async def test_an_idle_lobby_expires_and_a_started_one_does_not() -> None:
    view, host, lobby = make_view()
    await view.on_timeout()
    edit = view.message.edit.await_args.kwargs
    assert "Lobby expired" in (edit["embed"].title or "") and edit["view"] is None
    assert view.state == CLOSED

    started, host2, _ = make_view(raid=False)
    with patch("yokai.ui.party_views.send", new=AsyncMock()):
        await started.start_button.callback(interaction(LEADER))
    await started.on_timeout()
    started.message.edit.assert_not_awaited()


def test_the_lobby_embed_obeys_the_layout_rules() -> None:
    view, _, lobby = make_view()
    for user in (2, 3, 4):
        lobby.add(PartyMember(user, "Member " + "x" * 30, "SS+", 60))
    embed = view.render()
    assert emoji_count(embed) == 1 and total_embed_length(embed) <= 6000
    assert "closes <t:" in (embed.description or "")
    assert "Party (4/4)" in [f.name for f in embed.fields]
    solo = EmbedFactory.party_lobby("Pest", "E", 1, 1, [("<@1>", "E", 1)], 1, 4, 1, False)
    assert "Party lobby" in (solo.title or "") and "up to 4" in (solo.description or "")


# -------------------------------------------------------------------- /guild mission


USER_LEADER = 1


async def world(
    tmp_path: Path, *, ranks: Optional[dict[int, str]] = None
) -> tuple[Database, RpgRuntime, RpgCog]:
    db = Database(tmp_path / "party.db")
    await db.initialize()
    runtime = RpgRuntime.create(db, Path("content/rpg"))
    for user_id, rank in (ranks or {1: "C", 2: "C", 3: "D"}).items():
        await runtime.store.create_character(user_id, rank, now=1)
    async with db.transaction() as conn:
        await conn.execute("UPDATE rpg_characters SET level = 14 WHERE rank = 'C';")
        await conn.execute("UPDATE rpg_characters SET level = 8 WHERE rank = 'D';")
    bot = MagicMock()
    bot.user = None
    bot.rpg = runtime
    bot.config.rpg_tz = "UTC"
    bot.config.rpg_base_attempts = 8
    bot.config.rpg_turn_seconds = 45
    bot.config.economy_welcome_bonus = 200
    cog = RpgCog(bot)
    today, _ = cog._day()
    await runtime.store.get_or_create_board(
        today, lambda: [RAID_ID, "c_ox_pasture", "raid_b_sunken_temple"]
    )
    return db, runtime, cog


async def run_mission(cog: RpgCog, user_id: int, mission: str, party: Optional[bool]) -> Any:
    click = interaction(user_id)
    click.response.is_done = MagicMock(return_value=False)
    with (
        patch("yokai.cogs.rpg.send", new=AsyncMock(return_value=MagicMock())) as sent,
        patch("yokai.cogs.rpg.launch_fight", new=AsyncMock(return_value=MagicMock())) as solo,
    ):
        await cog.mission.callback(cog, click, mission, party)
    return sent, solo


@pytest.mark.asyncio
async def test_a_raid_always_opens_a_public_lobby_and_charges_nothing(tmp_path: Path) -> None:
    db, runtime, cog = await world(tmp_path)
    try:
        sent, solo = await run_mission(cog, 1, RAID_ID, None)
        solo.assert_not_awaited()
        assert "ephemeral" not in sent.await_args.kwargs
        view = sent.await_args.kwargs["view"]
        assert isinstance(view, PartyLobbyView) and list(view.lobby.members) == [1]
        assert "Raid lobby" in (sent.await_args.kwargs["embed"].title or "")
        assert view.message is sent.return_value
        leader = await runtime.store.get_character(1)
        assert leader is not None and leader.attempts_used == 0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_regular_mission_is_solo_unless_party_is_requested(tmp_path: Path) -> None:
    db, runtime, cog = await world(tmp_path)
    try:
        sent, solo = await run_mission(cog, 1, "c_ox_pasture", None)
        solo.assert_awaited_once()
        sent, solo = await run_mission(cog, 1, "c_ox_pasture", False)
        solo.assert_awaited_once()
        sent, solo = await run_mission(cog, 1, "c_ox_pasture", True)
        solo.assert_not_awaited()
        assert isinstance(sent.await_args.kwargs["view"], PartyLobbyView)
        assert "Party lobby" in (sent.await_args.kwargs["embed"].title or "")
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_the_leader_is_checked_before_a_lobby_opens(tmp_path: Path) -> None:
    db, runtime, cog = await world(tmp_path)
    try:
        sent, _ = await run_mission(cog, 3, RAID_ID, None)  # rank D, raid needs C
        assert "Rank too low" in (sent.await_args.kwargs["embed"].title or "")
        assert sent.await_args.kwargs["ephemeral"] is True
        async with db.transaction() as conn:
            await conn.execute(
                "UPDATE rpg_characters SET attempts_used = 7, attempts_day = ?;", (cog._day()[0],)
            )
        sent, _ = await run_mission(cog, 1, RAID_ID, None)
        assert "Out of attempts" in (sent.await_args.kwargs["embed"].title or "")
        runtime.active_users.add(1)
        async with db.transaction() as conn:
            await conn.execute("UPDATE rpg_characters SET attempts_used = 0;")
        sent, _ = await run_mission(cog, 1, RAID_ID, None)
        assert "Already in a fight" in (sent.await_args.kwargs["embed"].title or "")
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_the_board_and_autocomplete_label_raids(tmp_path: Path) -> None:
    db, runtime, cog = await world(tmp_path)
    try:
        click = interaction(1)
        click.response.defer = AsyncMock()
        with patch("yokai.cogs.rpg.send", new=AsyncMock()) as sent:
            await cog.board.callback(cog, click)
        embed = last_embed(sent)
        text = "\n".join(f.value for f in embed.fields)
        assert "Raid: Bandit Fortress" in text and "cost 3" in text
        choices = await cog.mission_autocomplete(interaction(1), "")
        labels = {c.value: c.name for c in choices}
        assert "raid, rank C" in labels[RAID_ID] and "raid" not in labels["c_ox_pasture"]
    finally:
        await db.close()


# ------------------------------------------------------------------------ the host


def host_for(cog: RpgCog, runtime: RpgRuntime, mission_id: str = RAID_ID) -> _LobbyHost:
    today, resets_ts = cog._day()
    return _LobbyHost(
        cog,
        MagicMock(spec=discord.TextChannel),
        runtime.content.missions[mission_id],
        today,
        resets_ts,
    )


@pytest.mark.asyncio
async def test_the_host_admits_only_eligible_members(tmp_path: Path) -> None:
    db, runtime, cog = await world(tmp_path)
    try:
        host = host_for(cog, runtime)
        member, heads_up = await host.admit(interaction(2).user)
        assert (member.user_id, member.rank, member.level, heads_up) == (2, "C", 14, None)
        with pytest.raises(MissionRankError):
            await host.admit(interaction(3).user)
        with pytest.raises(NoCharacterError):
            await host.admit(interaction(99).user)
        runtime.active_users.add(2)
        with pytest.raises(AlreadyFightingError):
            await host.admit(interaction(2).user)
        runtime.active_users.discard(2)
        async with db.transaction() as conn:
            await conn.execute(
                "UPDATE rpg_characters SET attempts_used = 6, attempts_day = ? WHERE user_id = 2;",
                (cog._day()[0],),
            )
        with pytest.raises(NoAttemptsError):
            await host.admit(interaction(2).user)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_rank_ahead_of_the_mission_may_join(tmp_path: Path) -> None:
    db, runtime, cog = await world(tmp_path, ranks={1: "C", 2: "A"})
    try:
        host = host_for(cog, runtime)
        member, _ = await host.admit(interaction(2).user)
        assert member.rank == "A"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_the_host_rechecks_with_fresh_data_at_start(tmp_path: Path) -> None:
    db, runtime, cog = await world(tmp_path)
    try:
        host = host_for(cog, runtime)
        lobby = Lobby(runtime.content.missions[RAID_ID], leader_id=1)
        for user in (1, 2):
            member, _ = await host.admit(interaction(user).user)
            lobby.add(member)
        assert await host.rejected(lobby) == {}
        async with db.transaction() as conn:
            await conn.execute(
                "UPDATE rpg_characters SET attempts_used = 7, attempts_day = ? WHERE user_id = 2;",
                (cog._day()[0],),
            )
        runtime.active_users.add(1)
        rejected = await host.rejected(lobby)
        assert isinstance(rejected[2], NoAttemptsError)
        assert isinstance(rejected[1], AlreadyFightingError)
        await runtime.store.admin_reset_character(0, 2)
        assert isinstance((await host.rejected(lobby))[2], NoCharacterError)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_the_host_launches_everyone_with_the_leader_first(tmp_path: Path) -> None:
    db, runtime, cog = await world(tmp_path)
    try:
        host = host_for(cog, runtime)
        lobby = Lobby(runtime.content.missions[RAID_ID], leader_id=1)
        for user in (1, 2):
            member, _ = await host.admit(interaction(user).user)
            lobby.add(member)
        thread = MagicMock()
        with patch("yokai.cogs.rpg.launch_party_fight", new=AsyncMock(return_value=thread)) as go:
            assert await host.launch(lobby) is thread
        entrants = go.await_args.kwargs["entrants"]
        assert [e.user.id for e in entrants] == [1, 2]
        assert [e.name for e in entrants] == ["Member 1", "Member 2"]
        assert go.await_args.kwargs["mission"].id == RAID_ID
        await runtime.store.admin_reset_character(0, 2)
        with pytest.raises(NoCharacterError):
            await host.launch(lobby)
    finally:
        await db.close()


# -------------------------------------------------------------- launching a party fight


def member_obj(user_id: int) -> MagicMock:
    member = MagicMock()
    member.id = user_id
    return member


async def party_runtime(tmp_path: Path, users: tuple[int, ...] = (7, 8, 9)):  # type: ignore[no-untyped-def]
    db = Database(tmp_path / "launch.db")
    await db.initialize()
    content_dir = tmp_path / "content"
    content_dir.mkdir()
    write_content_dir(content_dir, enemies=ENEMIES, missions=MISSIONS)
    runtime = RpgRuntime.create(db, content_dir)
    for user_id in users:
        await runtime.store.create_character(user_id, "E", now=1)
    return db, runtime


def channel_with(thread: Any = None, fail: bool = False) -> MagicMock:
    channel = MagicMock()
    channel.id = 42
    thread = thread or FakeThread()
    if fail:
        channel.create_thread = AsyncMock(side_effect=discord.Forbidden(MagicMock(), "no"))
    else:
        channel.create_thread = AsyncMock(return_value=thread)
    channel.thread = thread
    return channel


async def entrants_for(runtime: RpgRuntime, users: tuple[int, ...]) -> list[Entrant]:
    out = []
    for user_id in users:
        character = await runtime.store.get_character(user_id)
        assert character is not None
        out.append(Entrant(member_obj(user_id), f"Member {user_id}", character))
    return out


async def party_launch(
    runtime: RpgRuntime, channel: MagicMock, users: tuple[int, ...] = (7, 8, 9), **kw: Any
):  # type: ignore[no-untyped-def]
    return await launch_party_fight(
        bot=make_fight_bot(),
        runtime=runtime,
        channel=channel,
        entrants=await entrants_for(runtime, users),
        mission=runtime.content.missions[kw.get("mission", "m_sponge")],
        today="2026-10-14",
        resets_ts=1_900_000_000,
        base_attempts=kw.get("base_attempts", 8),
        turn_seconds=5,
    )


async def stop_tasks(runtime: RpgRuntime) -> None:
    for task in list(runtime.tasks):
        task.cancel()
    await asyncio.gather(*runtime.tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_a_party_launch_charges_everyone_and_opens_one_thread(tmp_path: Path) -> None:
    db, runtime = await party_runtime(tmp_path)
    channel = channel_with()
    try:
        thread = await party_launch(runtime, channel)
        assert thread is channel.thread
        channel.create_thread.assert_awaited_once()
        assert "Member 7's party" in channel.create_thread.await_args.kwargs["name"]
        assert len(channel.thread.added) == 3
        assert {7, 8, 9} <= runtime.active_users and len(runtime.tasks) == 1
        for user in (7, 8, 9):
            character = await runtime.store.get_character(user)
            assert character is not None and character.attempts_used == 1
        async with db.conn.execute("SELECT COUNT(*) FROM rpg_fights;") as cursor:
            assert (await cursor.fetchone())[0] == 1  # type: ignore[index]
        await stop_tasks(runtime)
        # cancelling the fight refunds all three and frees them
        for user in (7, 8, 9):
            character = await runtime.store.get_character(user)
            assert character is not None and character.attempts_used == 0
        assert not runtime.active_users
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_one_member_without_attempts_stops_the_whole_party(tmp_path: Path) -> None:
    db, runtime = await party_runtime(tmp_path)
    try:
        async with db.transaction() as conn:
            await conn.execute(
                "UPDATE rpg_characters SET attempts_used = 8, attempts_day = '2026-10-14' "
                "WHERE user_id = 9;"
            )
        channel = channel_with()
        with pytest.raises(NoAttemptsError):
            await party_launch(runtime, channel)
        channel.create_thread.assert_not_awaited()
        for user in (7, 8):
            character = await runtime.store.get_character(user)
            assert character is not None and character.attempts_used == 0
        assert not runtime.active_users and not runtime.tasks
        async with db.conn.execute("SELECT COUNT(*) FROM rpg_fights;") as cursor:
            assert (await cursor.fetchone())[0] == 0  # type: ignore[index]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_member_already_fighting_blocks_the_launch(tmp_path: Path) -> None:
    db, runtime = await party_runtime(tmp_path)
    try:
        runtime.active_users.add(8)
        with pytest.raises(AlreadyFightingError):
            await party_launch(runtime, channel_with())
        assert runtime.active_users == {8}
        for user in (7, 8, 9):
            character = await runtime.store.get_character(user)
            assert character is not None and character.attempts_used == 0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_thread_that_cannot_be_opened_refunds_the_whole_party(tmp_path: Path) -> None:
    db, runtime = await party_runtime(tmp_path)
    try:
        with pytest.raises(ThreadUnavailableError):
            await party_launch(runtime, channel_with(fail=True))
        for user in (7, 8, 9):
            character = await runtime.store.get_character(user)
            assert character is not None and character.attempts_used == 0
        assert not runtime.active_users
        async with db.conn.execute("SELECT status FROM rpg_fights;") as cursor:
            assert [row[0] for row in await cursor.fetchall()] == ["aborted"]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_the_same_member_cannot_be_listed_twice(tmp_path: Path) -> None:
    db, runtime = await party_runtime(tmp_path)
    try:
        with pytest.raises(ValueError):
            await party_launch(runtime, channel_with(), users=(7, 7))
        assert not runtime.active_users
    finally:
        await db.close()


# ------------------------------------------------------------------ a shared fight


async def party_runner(tmp_path: Path, mission_id: str, users: tuple[int, ...]):  # type: ignore[no-untyped-def]
    db, runtime = await party_runtime(tmp_path, users)
    mission = runtime.content.missions[mission_id]
    fight_id = await runtime.store.begin_fight(
        mission_id=mission.id, kind="mission", user_ids=list(users), cost=1,
        today="2026-10-14", base_attempts=8, streak_rules=runtime.content.balance.streak,
    )  # fmt: skip
    participants = []
    for user_id in users:
        character = await runtime.store.get_character(user_id)
        assert character is not None
        participants.append(Participant(user_id, f"Member {user_id}", character))
    runtime.active_users.update(users)
    thread = FakeThread()
    runner = FightRunner(
        bot=make_fight_bot(), runtime=runtime, fight_id=fight_id, mission=mission,
        participants=participants, thread=thread,  # type: ignore[arg-type]
        turn_seconds=0.3, rng=random.Random(2), idle_delay=0, archive_delay=0,
    )  # fmt: skip
    return db, runtime, runner, thread


def press(user_id: int) -> MagicMock:
    click = MagicMock(spec=discord.Interaction)
    click.user = MagicMock(spec=discord.Member)
    click.user.id = user_id
    click.response = MagicMock()
    click.response.defer = AsyncMock()
    click.response.send_message = AsyncMock()
    return click


@pytest.mark.asyncio
async def test_every_member_of_a_won_party_fight_is_paid_separately(tmp_path: Path) -> None:
    db, runtime, runner, thread = await party_runner(tmp_path, "m_glass", (7, 8))
    try:
        task = asyncio.create_task(runner.run())
        view = await wait_for_view(runner, 1)
        await view.attack_button.callback(press(7))
        await view.attack_button.callback(press(8))
        await asyncio.wait_for(task, 10)
        final = thread.last_embed()
        assert "<@7>" in str(final.to_dict()) and "<@8>" in str(final.to_dict())
        for user in (7, 8):
            character = await runtime.store.get_character(user)
            assert character is not None and character.battles_won == 1
            assert character.xp > 0 or character.level > 1
        for user in (7, 8):
            row = await (
                await db.conn.execute("SELECT balance FROM wallets WHERE user_id = ?;", (user,))
            ).fetchone()
            assert row is not None and row[0] > 200
        assert not runtime.active_users
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_an_idle_member_still_shares_the_win(tmp_path: Path) -> None:
    db, runtime, runner, thread = await party_runner(tmp_path, "m_glass", (7, 8))
    try:
        task = asyncio.create_task(runner.run())
        view = await wait_for_view(runner, 1)
        await view.attack_button.callback(press(7))  # member 8 never acts
        await asyncio.wait_for(task, 10)
        for user in (7, 8):
            character = await runtime.store.get_character(user)
            assert character is not None and character.battles_won == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_lost_party_fight_pays_nobody_and_wears_everyones_gear(tmp_path: Path) -> None:
    db, runtime, runner, thread = await party_runner(tmp_path, "m_titan", (7, 8))
    try:
        await asyncio.wait_for(runner.run(), 20)
        for user in (7, 8):
            character = await runtime.store.get_character(user)
            assert character is not None and character.battles_lost == 1 and character.xp == 0
        assert "Mission failed" in (thread.last_embed().title or "")
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_crashing_party_fight_refunds_everyone(tmp_path: Path) -> None:
    db, runtime, runner, thread = await party_runner(tmp_path, "m_titan", (7, 8, 9))
    try:
        thread.fail_send = True
        await asyncio.wait_for(runner.run(), 10)
        for user in (7, 8, 9):
            character = await runtime.store.get_character(user)
            assert character is not None and character.attempts_used == 0
        assert not runtime.active_users
    finally:
        await db.close()
