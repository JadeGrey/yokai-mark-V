"""The crash runner against a fake clock: cash-outs are judged by the server's time."""

from __future__ import annotations

from pathlib import Path
from typing import Awaitable, Callable, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from tests.moderation_helpers import fake_moderation
from tests.table_helpers import ALL_PLAYABLE, CHANNEL, announce_ok, balance_of, world
from yokai.gambling.lobby import LobbyState
from yokai.ui.crash_views import CrashView, run_crash


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def make_bot(db, manager) -> MagicMock:  # type: ignore[no-untyped-def]
    bot = MagicMock()
    bot.user = None
    bot.db = db
    bot.tables = manager
    bot.moderation = fake_moderation(owner_id=999)
    return bot


def make_message() -> MagicMock:
    message = MagicMock(spec=discord.Message)
    message.edit = AsyncMock()
    return message


def make_interaction(user_id: int) -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock(spec=discord.Member)
    interaction.user.id = user_id
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


Script = Callable[[CrashView, float], Awaitable[None]]


def fake_pause(clock: FakeClock, script: Optional[Script] = None):  # type: ignore[no-untyped-def]
    """A replacement for the runner's wait: advance the clock, then let the test act."""

    async def pause(view: CrashView, seconds: float) -> None:
        clock.now += seconds
        if script is not None:
            await script(view, clock.now - view.started_at)

    return pause


async def _running(manager, stakes=(100, 100)):  # type: ignore[no-untyped-def]
    lobby = await manager.open_table(CHANNEL, "crash", 1, stakes[0], announce_ok)
    for user_id, stake in enumerate(stakes[1:], start=2):
        await manager.join(lobby, user_id, stake)
    await manager.begin(lobby)
    return lobby


async def _press(view: CrashView, user_id: int) -> tuple[AsyncMock, AsyncMock]:
    """Press Cash out as `user_id`; returns (success reply, error reply) mocks."""
    ok, error = AsyncMock(), AsyncMock()
    with patch("yokai.ui.crash_views.send", new=ok), patch("yokai.ui.table_common.send", new=error):
        await view.cash_out_button.callback(make_interaction(user_id))
    return ok, error


@pytest.mark.asyncio
async def test_a_player_who_cashes_out_in_time_is_paid_and_the_other_loses(tmp_path: Path) -> None:
    async with world(tmp_path, specs=ALL_PLAYABLE) as (db, manager, active):
        clock = FakeClock()
        manager.clock = clock
        lobby = await _running(manager)
        message = make_message()
        pressed = {"done": False}

        async def script(view: CrashView, elapsed: float) -> None:
            if elapsed >= 5.0 and not pressed["done"]:
                pressed["done"] = True
                await _press(view, 1)

        with (
            patch("yokai.ui.crash_views.draw_crash_point", return_value=1000),
            patch("yokai.ui.crash_views._pause", new=fake_pause(clock, script)),
        ):
            await run_crash(make_bot(db, manager), lobby, message)

        # 5 s -> floor(exp(0.35) * 100) = 141 hundredths: 100 -> 141
        assert await balance_of(db, 1) == 1000 - 100 + 141
        assert await balance_of(db, 2) == 900
        assert await db.get_pot() == 100  # the lost stake feeds the lottery pot
        assert lobby.state is LobbyState.SETTLED
        assert not active.is_active(1) and not active.is_active(2)
        final = message.edit.await_args.kwargs
        assert "10.00x" in final["embed"].description and final["view"] is None


@pytest.mark.asyncio
async def test_a_press_after_the_crash_time_loses_even_if_the_message_still_shows_a_lower_number(
    tmp_path: Path,
) -> None:
    async with world(tmp_path, specs=ALL_PLAYABLE) as (db, manager, _):
        clock = FakeClock()
        manager.clock = clock
        lobby = await _running(manager, (100,))
        message = make_message()
        late: dict[str, object] = {}

        async def script(view: CrashView, elapsed: float) -> None:
            # the crash is at 2.00x, about 9.9 s in; press at 12 s: the runner has not woken yet
            if elapsed >= 9.0 and "press" not in late:
                clock.now = view.started_at + 12.0
                late["view"] = view
                late["press"] = await _press(view, 1)

        with (
            patch("yokai.ui.crash_views.draw_crash_point", return_value=200),
            patch("yokai.ui.crash_views._pause", new=fake_pause(clock, script)),
        ):
            await run_crash(make_bot(db, manager), lobby, message)

        ok, error = late["press"]  # type: ignore[misc]
        ok.assert_not_awaited()
        assert error.await_args.kwargs["ephemeral"] is True
        assert "crashed" in error.await_args.kwargs["embed"].description
        assert late["view"].crash.cashed_out == {}  # type: ignore[attr-defined]
        assert await balance_of(db, 1) == 900  # the stake is lost


@pytest.mark.asyncio
async def test_everyone_cashing_out_ends_the_round_before_the_crash(tmp_path: Path) -> None:
    async with world(tmp_path, specs=ALL_PLAYABLE) as (db, manager, _):
        clock = FakeClock()
        manager.clock = clock
        lobby = await _running(manager)
        message = make_message()
        calls = {"n": 0}

        async def script(view: CrashView, elapsed: float) -> None:
            calls["n"] += 1
            if calls["n"] == 1:
                await _press(view, 1)
                await _press(view, 2)
                view.wake.set()

        with (
            patch("yokai.ui.crash_views.draw_crash_point", return_value=10_000),
            patch("yokai.ui.crash_views._pause", new=fake_pause(clock, script)),
        ):
            await run_crash(make_bot(db, manager), lobby, message)

        assert calls["n"] == 1  # it did not wait for the 100x crash
        assert await balance_of(db, 1) > 1000 - 100 and await balance_of(db, 2) > 1000 - 100
        assert await db.get_pot() == 0


@pytest.mark.asyncio
async def test_a_crash_point_of_one_loses_everyone_immediately(tmp_path: Path) -> None:
    async with world(tmp_path, specs=ALL_PLAYABLE) as (db, manager, _):
        clock = FakeClock()
        manager.clock = clock
        lobby = await _running(manager)
        message = make_message()
        with (
            patch("yokai.ui.crash_views.draw_crash_point", return_value=100),
            patch("yokai.ui.crash_views._pause", new=fake_pause(clock)),
        ):
            await run_crash(make_bot(db, manager), lobby, message)
        assert (await balance_of(db, 1), await balance_of(db, 2)) == (900, 900)
        assert await db.get_pot() == 200
        assert "1.00x" in message.edit.await_args.kwargs["embed"].description


@pytest.mark.asyncio
async def test_a_solo_round_runs(tmp_path: Path) -> None:
    async with world(tmp_path, specs=ALL_PLAYABLE) as (db, manager, active):
        clock = FakeClock()
        manager.clock = clock
        lobby = await _running(manager, (200,))
        assert len(lobby.seats) == 1
        message = make_message()
        with (
            patch("yokai.ui.crash_views.draw_crash_point", return_value=150),
            patch("yokai.ui.crash_views._pause", new=fake_pause(clock)),
        ):
            await run_crash(make_bot(db, manager), lobby, message)
        assert lobby.state is LobbyState.SETTLED and await balance_of(db, 1) == 800
        assert not active.is_active(1)


@pytest.mark.asyncio
async def test_a_stranger_cannot_cash_out(tmp_path: Path) -> None:
    async with world(tmp_path, specs=ALL_PLAYABLE) as (db, manager, _):
        clock = FakeClock()
        manager.clock = clock
        lobby = await _running(manager, (100,))
        from yokai.gambling.engines.crash import CrashRound

        view = CrashView(make_bot(db, manager), lobby, CrashRound([1], 500), clock())
        interaction = make_interaction(3)
        with patch("yokai.ui.table_common.send", new=AsyncMock()) as sent:
            await view.cash_out_button.callback(interaction)
        assert sent.await_args.kwargs["ephemeral"] is True
        assert "not seated" in sent.await_args.kwargs["embed"].description
        assert view.crash.cashed_out == {}


@pytest.mark.asyncio
async def test_a_failure_while_settling_refunds_everyone_and_tells_the_players(
    tmp_path: Path,
) -> None:
    async with world(tmp_path, specs=ALL_PLAYABLE) as (db, manager, active):
        clock = FakeClock()
        manager.clock = clock
        lobby = await _running(manager)
        message = make_message()
        failing = AsyncMock(side_effect=RuntimeError("disk"))
        with (
            patch("yokai.ui.crash_views.draw_crash_point", return_value=100),
            patch("yokai.ui.crash_views._pause", new=fake_pause(clock)),
            patch.object(db, "settle_lobby", new=failing),
        ):
            with pytest.raises(RuntimeError):
                await run_crash(make_bot(db, manager), lobby, message)
        assert lobby.state is LobbyState.CANCELLED
        assert (await balance_of(db, 1), await balance_of(db, 2)) == (1000, 1000)
        assert not active.is_active(1)
        assert "cancelled" in message.edit.await_args.kwargs["embed"].title.lower()


@pytest.mark.asyncio
async def test_a_slow_edit_never_delays_the_crash(tmp_path: Path) -> None:
    """Edits run in the background: the round still ends on the server clock."""
    async with world(tmp_path, specs=ALL_PLAYABLE) as (db, manager, _):
        clock = FakeClock()
        manager.clock = clock
        lobby = await _running(manager, (100,))
        message = make_message()
        import asyncio

        gate = asyncio.Event()

        async def slow_edit(**kwargs):  # type: ignore[no-untyped-def]
            # Only the in-flight tick edits (which carry the view) are slow; the first message
            # and the final result are not.
            if kwargs.get("view") is not None and message.edit.await_count > 1:
                await gate.wait()

        message.edit = AsyncMock(side_effect=slow_edit)

        async def script(view: CrashView, elapsed: float) -> None:
            return None  # the gate is never released: a stuck edit must not hold up settlement

        with (
            patch("yokai.ui.crash_views.draw_crash_point", return_value=300),
            patch("yokai.ui.crash_views._pause", new=fake_pause(clock, script)),
        ):
            await asyncio.wait_for(run_crash(make_bot(db, manager), lobby, message), timeout=5)
        assert lobby.state is LobbyState.SETTLED
