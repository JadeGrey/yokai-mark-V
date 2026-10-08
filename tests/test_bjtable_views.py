"""The blackjack table runner: turn order, timeouts, dealer play and settlement."""

from __future__ import annotations

from pathlib import Path
from typing import Awaitable, Callable, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from tests.card_helpers import cards
from tests.moderation_helpers import fake_moderation
from tests.table_helpers import ALL_PLAYABLE, CHANNEL, announce_ok, balance_of, world
from yokai.gambling.lobby import LobbyState
from yokai.ui.bjtable_views import BlackjackTableView, run_bjtable


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
    interaction.response.edit_message = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


async def _running(manager, stakes=(100, 100)):  # type: ignore[no-untyped-def]
    lobby = await manager.open_table(CHANNEL, "bjtable", 1, stakes[0], announce_ok)
    for user_id, stake in enumerate(stakes[1:], start=2):
        await manager.join(lobby, user_id, stake)
    await manager.begin(lobby)
    return lobby


Action = Callable[[BlackjackTableView, int], Awaitable[bool]]


def scripted_wait(actions: list[Optional[Action]]):  # type: ignore[no-untyped-def]
    """Replacement for _await_action: each call runs the next scripted move (None = timeout)."""
    queue = list(actions)

    async def wait(view: BlackjackTableView, seconds: float) -> bool:
        step = queue.pop(0)
        if step is None:
            return False
        who = view.table.current
        assert who is not None
        return await step(view, who)

    return wait


def press(button_name: str) -> Action:
    async def action(view: BlackjackTableView, who: int) -> bool:
        button = getattr(view, button_name)
        await button.callback(make_interaction(who))
        return True

    return action


@pytest.mark.asyncio
async def test_stand_then_timeout_settles_every_hand_against_the_dealer(tmp_path: Path) -> None:
    async with world(tmp_path, specs=ALL_PLAYABLE) as (db, manager, active):
        lobby = await _running(manager)
        message = make_message()
        shoe = cards("10h 9d 7h 8c 9s 10d")  # 18, 18 against a dealer 17
        with (
            patch("yokai.ui.bjtable_views.build_shoe", return_value=shoe),
            patch(
                "yokai.ui.bjtable_views._await_action",
                new=scripted_wait([press("stand_button"), None]),  # player 2 times out
            ),
        ):
            await run_bjtable(make_bot(db, manager), lobby, message)
        assert (await balance_of(db, 1), await balance_of(db, 2)) == (1100, 1100)
        assert lobby.state is LobbyState.SETTLED and await db.get_pot() == 0
        assert not active.is_active(1) and not active.is_active(2)
        final = message.edit.await_args.kwargs
        assert "(17)" in final["embed"].description and final["view"] is None


@pytest.mark.asyncio
async def test_a_bust_loses_the_stake_into_the_pot_and_the_other_player_still_plays(
    tmp_path: Path,
) -> None:
    async with world(tmp_path, specs=ALL_PLAYABLE) as (db, manager, _):
        lobby = await _running(manager)
        message = make_message()
        shoe = cards("10h 9d 7h 6c 9s 10d 10c")  # player 1: 16 then a ten = bust
        with (
            patch("yokai.ui.bjtable_views.build_shoe", return_value=shoe),
            patch(
                "yokai.ui.bjtable_views._await_action",
                new=scripted_wait([press("hit_button"), press("stand_button")]),
            ),
        ):
            await run_bjtable(make_bot(db, manager), lobby, message)
        assert await balance_of(db, 1) == 900
        assert await balance_of(db, 2) == 1100  # 18 beats the dealer's 17
        assert await db.get_pot() == 100


@pytest.mark.asyncio
async def test_a_dealer_natural_settles_at_once_with_no_turns(tmp_path: Path) -> None:
    async with world(tmp_path, specs=ALL_PLAYABLE) as (db, manager, _):
        lobby = await _running(manager)
        message = make_message()
        shoe = cards("10h 9d As 8c 9s Kd")
        wait = AsyncMock(side_effect=AssertionError("nobody should be waited on"))
        with (
            patch("yokai.ui.bjtable_views.build_shoe", return_value=shoe),
            patch("yokai.ui.bjtable_views._await_action", new=wait),
        ):
            await run_bjtable(make_bot(db, manager), lobby, message)
        assert (await balance_of(db, 1), await balance_of(db, 2)) == (900, 900)
        wait.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_natural_is_paid_three_to_two(tmp_path: Path) -> None:
    async with world(tmp_path, specs=ALL_PLAYABLE) as (db, manager, _):
        lobby = await _running(manager, (100, 100))
        message = make_message()
        shoe = cards("Ah 10d 9h Kc 8s 8d")  # player 1 natural, player 2 has 18, dealer 17
        with (
            patch("yokai.ui.bjtable_views.build_shoe", return_value=shoe),
            patch(
                "yokai.ui.bjtable_views._await_action",
                new=scripted_wait([press("stand_button")]),
            ),
        ):
            await run_bjtable(make_bot(db, manager), lobby, message)
        assert await balance_of(db, 1) == 1000 - 100 + 250
        assert await balance_of(db, 2) == 1100


@pytest.mark.asyncio
async def test_only_the_player_whose_turn_it_is_can_act(tmp_path: Path) -> None:
    async with world(tmp_path, specs=ALL_PLAYABLE) as (db, manager, _):
        lobby = await _running(manager)
        from yokai.gambling.engines.bjtable import BlackjackTable

        table = BlackjackTable(cards("10h 9d 7h 8c 9s 10d"), [1, 2])
        view = BlackjackTableView(make_bot(db, manager), lobby, table)
        view.message = make_message()
        interaction = make_interaction(2)  # player 1 is up
        with patch("yokai.ui.table_common.send", new=AsyncMock()) as sent:
            await view.hit_button.callback(interaction)
        assert sent.await_args.kwargs["ephemeral"] is True
        assert "turn" in sent.await_args.kwargs["embed"].description.lower()
        assert len(table.hands[2]) == 2 and not view.acted.is_set()
        interaction.response.edit_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_stranger_cannot_act(tmp_path: Path) -> None:
    async with world(tmp_path, specs=ALL_PLAYABLE) as (db, manager, _):
        lobby = await _running(manager)
        from yokai.gambling.engines.bjtable import BlackjackTable

        table = BlackjackTable(cards("10h 9d 7h 8c 9s 10d"), [1, 2])
        view = BlackjackTableView(make_bot(db, manager), lobby, table)
        with patch("yokai.ui.table_common.send", new=AsyncMock()) as sent:
            await view.stand_button.callback(make_interaction(3))
        assert sent.await_args.kwargs["ephemeral"] is True
        assert table.current == 1


@pytest.mark.asyncio
async def test_a_press_that_races_the_timeout_is_not_applied_twice(tmp_path: Path) -> None:
    """The timeout only stands the player it was waiting on, and only if they are still up."""
    async with world(tmp_path, specs=ALL_PLAYABLE) as (db, manager, _):
        lobby = await _running(manager)
        message = make_message()
        shoe = cards("10h 9d 7h 8c 9s 10d")

        async def stand_then_report_timeout(view: BlackjackTableView, seconds: float) -> bool:
            # player 1 stands just before the runner decides it timed out
            await view.stand_button.callback(make_interaction(1))
            return False

        calls = {"n": 0}

        async def wait(view: BlackjackTableView, seconds: float) -> bool:
            calls["n"] += 1
            if calls["n"] == 1:
                return await stand_then_report_timeout(view, seconds)
            return False  # player 2 times out

        with (
            patch("yokai.ui.bjtable_views.build_shoe", return_value=shoe),
            patch("yokai.ui.bjtable_views._await_action", new=wait),
        ):
            await run_bjtable(make_bot(db, manager), lobby, message)
        # both hands were stood exactly once: 18 against 17 wins for both
        assert (await balance_of(db, 1), await balance_of(db, 2)) == (1100, 1100)


@pytest.mark.asyncio
async def test_a_failure_while_playing_refunds_everyone_and_tells_the_players(
    tmp_path: Path,
) -> None:
    async with world(tmp_path, specs=ALL_PLAYABLE) as (db, manager, active):
        lobby = await _running(manager)
        message = make_message()
        shoe = cards("10h 9d 7h 8c 9s 10d")
        failing = AsyncMock(side_effect=RuntimeError("disk"))
        with (
            patch("yokai.ui.bjtable_views.build_shoe", return_value=shoe),
            patch(
                "yokai.ui.bjtable_views._await_action",
                new=scripted_wait([press("stand_button"), press("stand_button")]),
            ),
            patch.object(db, "settle_lobby", new=failing),
        ):
            with pytest.raises(RuntimeError):
                await run_bjtable(make_bot(db, manager), lobby, message)
        assert lobby.state is LobbyState.CANCELLED
        assert (await balance_of(db, 1), await balance_of(db, 2)) == (1000, 1000)
        assert not active.is_active(1)
        assert "cancelled" in message.edit.await_args.kwargs["embed"].title.lower()


@pytest.mark.asyncio
async def test_a_hit_that_races_the_timeout_is_not_followed_by_a_forced_stand(
    tmp_path: Path,
) -> None:
    """A press that lands while the runner is deciding the turn timed out keeps their turn."""
    async with world(tmp_path, specs=ALL_PLAYABLE) as (db, manager, _):
        lobby = await _running(manager)
        message = make_message()
        shoe = cards("10h 9d 7h 2c 9s 10d 3c")  # player 1: 12, hits a three for 15
        calls = {"n": 0}

        async def wait(view: BlackjackTableView, seconds: float) -> bool:
            calls["n"] += 1
            who = view.table.current
            assert who is not None
            if calls["n"] == 1:
                # player 1 hits (not ending the hand) just before the runner sees a timeout
                await view.hit_button.callback(make_interaction(who))
                return False
            await view.stand_button.callback(make_interaction(who))
            return True

        with (
            patch("yokai.ui.bjtable_views.build_shoe", return_value=shoe),
            patch("yokai.ui.bjtable_views._await_action", new=wait),
        ):
            await run_bjtable(make_bot(db, manager), lobby, message)

        # wait 1: player 1's racing hit; wait 2: player 1 stands on 15; wait 3: player 2 stands
        assert calls["n"] == 3
        assert await balance_of(db, 1) == 900  # 15 loses to the dealer's 17
        assert await balance_of(db, 2) == 1100  # 18 beats 17


@pytest.mark.asyncio
async def test_the_result_is_posted_after_the_last_players_own_message_edit_finishes(
    tmp_path: Path,
) -> None:
    """Regression: the last action's in-play edit and the result edit raced, and a slow
    in-play edit landing last left a frozen board with live buttons and no visible result."""
    import asyncio

    from yokai.ui import bjtable_views

    async with world(tmp_path, specs=ALL_PLAYABLE) as (db, manager, _):
        lobby = await _running(manager)
        message = make_message()
        order: list[str] = []
        release = asyncio.Event()

        async def message_edit(**kwargs):  # type: ignore[no-untyped-def]
            if kwargs.get("view") is None:
                order.append("result-edit")

        message.edit = AsyncMock(side_effect=message_edit)
        holder: dict[str, BlackjackTableView] = {}
        real_wait = bjtable_views._await_action

        async def spying_wait(view: BlackjackTableView, seconds: float) -> bool:
            holder["view"] = view
            return await real_wait(view, seconds)

        shoe = cards("10h 10d 7h 8c 6c 10s 10c")  # P1 18, P2 16 and busts on the next ten
        with (
            patch("yokai.ui.bjtable_views.build_shoe", return_value=shoe),
            patch("yokai.ui.bjtable_views._await_action", new=spying_wait),
        ):
            runner = asyncio.create_task(run_bjtable(make_bot(db, manager), lobby, message))
            for _ in range(100):
                if "view" in holder:
                    break
                await asyncio.sleep(0.01)
            view = holder["view"]
            await view.stand_button.callback(make_interaction(1))

            slow = make_interaction(2)

            async def slow_edit(**kwargs):  # type: ignore[no-untyped-def]
                await release.wait()
                order.append("handler-edit")

            slow.response.edit_message = AsyncMock(side_effect=slow_edit)
            last_action = asyncio.create_task(view.hit_button.callback(slow))
            for _ in range(60):  # give the runner every chance to jump the queue
                if "result-edit" in order:
                    break
                await asyncio.sleep(0.02)
            assert "result-edit" not in order, "the result was posted while the last edit was open"
            release.set()
            await last_action
            await runner

        assert order == ["handler-edit", "result-edit"]
        assert lobby.state is LobbyState.SETTLED


@pytest.mark.asyncio
async def test_a_press_after_the_round_is_over_is_refused_not_applied(tmp_path: Path) -> None:
    async with world(tmp_path, specs=ALL_PLAYABLE) as (db, manager, _):
        lobby = await _running(manager)
        from yokai.gambling.engines.bjtable import BlackjackTable

        table = BlackjackTable(cards("10h 9d 7h 8c 9s 10d 3c"), [1, 2])
        view = BlackjackTableView(make_bot(db, manager), lobby, table)
        view.message = make_message()
        view.closed = True  # the runner has taken over to settle
        interaction = make_interaction(1)
        with patch("yokai.ui.table_common.send", new=AsyncMock()) as sent:
            await view.hit_button.callback(interaction)
        assert len(table.hands[1]) == 2  # no card was dealt
        assert sent.await_args.kwargs["ephemeral"] is True
        interaction.response.edit_message.assert_not_awaited()
