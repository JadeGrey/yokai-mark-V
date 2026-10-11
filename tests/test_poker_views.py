"""The poker table runner and view: turns, timeouts, busts, leaving, private cards, raising."""

from __future__ import annotations

import random
from contextlib import contextmanager
from pathlib import Path
from typing import Awaitable, Callable, Iterator, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from tests.embed_helpers import all_embed_text
from tests.moderation_helpers import fake_moderation
from tests.poker_helpers import stacked_deck
from tests.table_helpers import ALL_PLAYABLE, CHANNEL, announce_ok, balance_of, world
from yokai.gambling.engines.poker import build_deck as real_build_deck
from yokai.gambling.engines.pokertable import PokerTable
from yokai.gambling.lobby import Lobby, LobbyState
from yokai.ui.poker_views import PokerTableView, RaiseModal, run_poker
from yokai.ui.table_views import GAME_RUNNERS

A_WINS = ("As Ad", "7c 2d", "Ks 9h 4d 3c Jh")  # user 1 holds aces, user 2 junk, nothing helps 2
B_WINS = ("7c 2d", "As Ad", "Ks 9h 4d 3c Jh")


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


async def _running(manager, users=(1, 2), stake: int = 100, max_hands: int = 1) -> Lobby:  # type: ignore[no-untyped-def]
    manager.poker_max_hands = max_hands
    lobby = await manager.open_table(CHANNEL, "poker", users[0], stake, announce_ok)
    for user_id in users[1:]:
        await manager.join(lobby, user_id, None)
    await manager.begin(lobby)
    return lobby


def deck_for(holes: tuple[str, str, str], button: int = 1, order=(1, 2)):  # type: ignore[no-untyped-def]
    """A build_deck replacement that deals user 1 and 2 the given holes, then the board."""
    one, two, board = holes
    return lambda rng: stacked_deck(list(order), button, {1: one, 2: two}, board)


Step = Optional[Callable[["PokerTableView", int], Awaitable[bool]]]


def scripted_wait(steps: list[Step]):  # type: ignore[no-untyped-def]
    """Replacement for _await_action: each call runs the next scripted move (None = timeout)."""
    queue = list(steps)

    async def wait(view: PokerTableView, seconds: float) -> bool:
        step = queue.pop(0)
        if step is None:
            return False
        who = view.hand.current
        assert who is not None
        return await step(view, who)

    return wait


def press(button_name: str) -> Step:
    async def action(view: PokerTableView, who: int) -> bool:
        await getattr(view, button_name).callback(make_interaction(who))
        return True

    return action


CALL, FOLD, ALL_IN, LEAVE = (
    press("call_button"),
    press("fold_button"),
    press("all_in_button"),
    press("leave_button"),
)


@contextmanager
def fast_runner(steps: list[Step], deck=None) -> Iterator[None]:  # type: ignore[no-untyped-def]
    with (
        patch("yokai.ui.poker_views._await_action", new=scripted_wait(steps)),
        patch("yokai.ui.poker_views.RECAP_PAUSE_S", 0),
        patch(
            "yokai.gambling.engines.pokertable.build_deck",
            side_effect=deck or real_build_deck,
        ),
    ):
        yield


def embeds_of(message: MagicMock, *, live: bool) -> list[discord.Embed]:
    """Embeds sent to the table message: `live` = with the action buttons attached."""
    found = []
    for call in message.edit.await_args_list:
        if (call.kwargs.get("view") is not None) == live:
            found.append(call.kwargs["embed"])
    return found


def poker_world(tmp_path: Path, users=(1, 2, 3)):  # type: ignore[no-untyped-def]
    return world(tmp_path, game="poker", specs=ALL_PLAYABLE, users=users)


def make_view(bot, lobby, holes=A_WINS, users=(1, 2)):  # type: ignore[no-untyped-def]
    table = PokerTable(list(users), 100, 30, random.Random)
    with patch("yokai.gambling.engines.pokertable.build_deck", side_effect=deck_for(holes)):
        hand = table.start_hand()
    view = PokerTableView(bot, lobby, table, hand)
    view.message = make_message()
    return view, table, hand


# ---- the runner: whole sessions ------------------------------------------------------------


def test_poker_has_a_runner_and_is_playable() -> None:
    from yokai.gambling.lobby import GAME_SPECS

    assert GAME_RUNNERS["poker"] is run_poker
    assert GAME_SPECS["poker"].available


@pytest.mark.asyncio
async def test_a_hand_played_to_showdown_pays_the_winner_and_closes_the_table(
    tmp_path: Path,
) -> None:
    async with poker_world(tmp_path) as (db, manager, active):
        lobby = await _running(manager)
        message = make_message()
        # heads-up: user 1 (button, small blind) calls, user 2 checks; then check down 3 streets
        with fast_runner([CALL] * 8, deck_for(A_WINS)):
            await run_poker(make_bot(db, manager), lobby, message)
        assert (await balance_of(db, 1), await balance_of(db, 2)) == (1002, 998)
        assert lobby.state is LobbyState.SETTLED and await db.get_pot() == 0
        assert not active.is_active(1) and not active.is_active(2)
        assert await db.refund_open_bets() == 0
        final = message.edit.await_args.kwargs
        assert final["view"] is None and "+2" in all_embed_text(final["embed"])


@pytest.mark.asyncio
async def test_hole_cards_only_appear_publicly_at_a_showdown(tmp_path: Path) -> None:
    async with poker_world(tmp_path) as (db, manager, _):
        lobby = await _running(manager)
        message = make_message()
        with fast_runner([CALL] * 8, deck_for(A_WINS)):
            await run_poker(make_bot(db, manager), lobby, message)
        for embed in embeds_of(message, live=True):
            text = all_embed_text(embed)
            assert "As Ad" not in text and "7c 2d" not in text
        recap = "\n".join(all_embed_text(e) for e in embeds_of(message, live=False))
        assert "As Ad" in recap and "7c 2d" in recap and "wins" in recap


@pytest.mark.asyncio
async def test_a_fold_ends_the_hand_without_revealing_cards(tmp_path: Path) -> None:
    async with poker_world(tmp_path) as (db, manager, _):
        lobby = await _running(manager)
        message = make_message()
        with fast_runner([FOLD], deck_for(A_WINS)):
            await run_poker(make_bot(db, manager), lobby, message)
        assert (await balance_of(db, 1), await balance_of(db, 2)) == (999, 1001)
        recap = "\n".join(all_embed_text(e) for e in embeds_of(message, live=False))
        assert "As Ad" not in recap and "7c 2d" not in recap


@pytest.mark.asyncio
async def test_a_missed_turn_checks_when_free_otherwise_folds(tmp_path: Path) -> None:
    async with poker_world(tmp_path) as (db, manager, _):
        lobby = await _running(manager)
        message = make_message()
        with fast_runner([None], deck_for(A_WINS)):  # user 1 faces the big blind: auto-fold
            await run_poker(make_bot(db, manager), lobby, message)
        assert (await balance_of(db, 1), await balance_of(db, 2)) == (999, 1001)


@pytest.mark.asyncio
async def test_two_missed_turns_in_a_row_remove_the_player_and_pay_out_their_chips(
    tmp_path: Path,
) -> None:
    async with poker_world(tmp_path) as (db, manager, active):
        lobby = await _running(manager, max_hands=5)
        message = make_message()
        # hand 1: user 1 times out and folds. hand 2: user 2 (button) calls, user 1 times out
        # (a free check) and is removed after the hand.
        with fast_runner([None, CALL, None]):
            await run_poker(make_bot(db, manager), lobby, message)
        assert (await balance_of(db, 1), await balance_of(db, 2)) == (997, 1003)
        assert lobby.state is LobbyState.SETTLED and not active.is_active(1)
        assert "sat out" in "\n".join(all_embed_text(e) for e in embeds_of(message, live=False))


@pytest.mark.asyncio
async def test_a_busted_player_is_paid_nothing_and_the_winner_takes_the_table(
    tmp_path: Path,
) -> None:
    async with poker_world(tmp_path) as (db, manager, _):
        lobby = await _running(manager, max_hands=5)
        message = make_message()
        with fast_runner([ALL_IN, CALL], deck_for(B_WINS)):
            await run_poker(make_bot(db, manager), lobby, message)
        assert (await balance_of(db, 1), await balance_of(db, 2)) == (900, 1100)
        assert lobby.state is LobbyState.SETTLED and await db.get_pot() == 0
        assert "busted" in "\n".join(all_embed_text(e) for e in embeds_of(message, live=False))


@pytest.mark.asyncio
async def test_leaving_on_your_turn_folds_and_pays_out_your_chips(tmp_path: Path) -> None:
    async with poker_world(tmp_path) as (db, manager, _):
        lobby = await _running(manager, max_hands=5)
        message = make_message()
        with fast_runner([LEAVE], deck_for(A_WINS)):
            await run_poker(make_bot(db, manager), lobby, message)
        assert (await balance_of(db, 1), await balance_of(db, 2)) == (999, 1001)
        assert lobby.state is LobbyState.SETTLED


@pytest.mark.asyncio
async def test_the_table_closes_at_the_hand_cap(tmp_path: Path) -> None:
    async with poker_world(tmp_path) as (db, manager, _):
        lobby = await _running(manager, max_hands=2)
        message = make_message()
        # hand 1: user 1 (button) folds; hand 2: user 2 (button) folds. Even again: 100 each.
        with fast_runner([FOLD, FOLD]):
            await run_poker(make_bot(db, manager), lobby, message)
        assert (await balance_of(db, 1), await balance_of(db, 2)) == (1000, 1000)
        assert lobby.state is LobbyState.SETTLED


@pytest.mark.asyncio
async def test_three_players_play_on_after_one_busts(tmp_path: Path) -> None:
    async with poker_world(tmp_path) as (db, manager, _):
        lobby = await _running(manager, users=(1, 2, 3), max_hands=1)
        message = make_message()
        with fast_runner([FOLD, FOLD]):  # 3 players: button 1, SB 2, BB 3; 1 and 2 fold
            await run_poker(make_bot(db, manager), lobby, message)
        total = sum([await balance_of(db, u) for u in (1, 2, 3)])
        assert total == 3000 and await balance_of(db, 3) == 1001


@pytest.mark.asyncio
async def test_a_failure_refunds_everyone_and_tells_the_players(tmp_path: Path) -> None:
    async with poker_world(tmp_path) as (db, manager, active):
        lobby = await _running(manager)
        message = make_message()
        with (
            fast_runner([]),
            patch.object(PokerTable, "start_hand", side_effect=RuntimeError("boom")),
        ):
            with pytest.raises(RuntimeError):
                await run_poker(make_bot(db, manager), lobby, message)
        assert (await balance_of(db, 1), await balance_of(db, 2)) == (1000, 1000)
        assert lobby.state is LobbyState.CANCELLED and not active.is_active(1)
        assert "refunded" in all_embed_text(message.edit.await_args.kwargs["embed"]).lower()


# ---- the view: who can do what -----------------------------------------------------------


@pytest.mark.asyncio
async def test_only_the_player_to_act_can_use_the_action_buttons(tmp_path: Path) -> None:
    async with poker_world(tmp_path) as (db, manager, _):
        lobby = await _running(manager)
        view, table, hand = make_view(make_bot(db, manager), lobby)
        interaction = make_interaction(2)  # user 1 is up
        with patch("yokai.ui.table_common.send", new=AsyncMock()) as sent:
            await view.fold_button.callback(interaction)
        assert sent.await_args.kwargs["ephemeral"] is True
        assert "turn" in sent.await_args.kwargs["embed"].description.lower()
        assert hand.current == 1 and not view.acted.is_set()
        interaction.response.edit_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_stranger_cannot_act_or_look_at_cards(tmp_path: Path) -> None:
    async with poker_world(tmp_path) as (db, manager, _):
        lobby = await _running(manager)
        view, table, hand = make_view(make_bot(db, manager), lobby)
        with patch("yokai.ui.table_common.send", new=AsyncMock()) as sent:
            await view.call_button.callback(make_interaction(9))
            await view.cards_button.callback(make_interaction(9))
            await view.leave_button.callback(make_interaction(9))
        assert sent.await_count == 3 and hand.current == 1


@pytest.mark.asyncio
async def test_my_cards_shows_only_the_askers_hole_cards_privately(tmp_path: Path) -> None:
    async with poker_world(tmp_path) as (db, manager, _):
        lobby = await _running(manager)
        view, table, hand = make_view(make_bot(db, manager), lobby)
        with patch("yokai.ui.poker_views.send", new=AsyncMock()) as sent:
            await view.cards_button.callback(make_interaction(2))
        assert sent.await_args.kwargs["ephemeral"] is True
        text = all_embed_text(sent.await_args.kwargs["embed"])
        assert "7c 2d" in text and "As Ad" not in text


@pytest.mark.asyncio
async def test_a_folded_player_can_still_see_their_cards(tmp_path: Path) -> None:
    async with poker_world(tmp_path) as (db, manager, _):
        lobby = await _running(manager)
        view, table, hand = make_view(make_bot(db, manager), lobby)
        await view.fold_button.callback(make_interaction(1))
        with patch("yokai.ui.poker_views.send", new=AsyncMock()) as sent:
            await view.cards_button.callback(make_interaction(1))
        assert "As Ad" in all_embed_text(sent.await_args.kwargs["embed"])


@pytest.mark.asyncio
async def test_the_call_button_checks_when_free_and_names_the_price_otherwise(
    tmp_path: Path,
) -> None:
    async with poker_world(tmp_path) as (db, manager, _):
        lobby = await _running(manager)
        view, table, hand = make_view(make_bot(db, manager), lobby)
        assert view.call_button.label == "Call 1"  # the small blind owes one more chip
        await view.call_button.callback(make_interaction(1))
        assert view.call_button.label == "Check"  # the big blind may check


@pytest.mark.asyncio
async def test_a_player_who_cannot_raise_has_no_all_in_or_raise_button(tmp_path: Path) -> None:
    async with poker_world(tmp_path) as (db, manager, _):
        lobby = await _running(manager)
        view, table, hand = make_view(make_bot(db, manager), lobby)
        assert not view.all_in_button.disabled and not view.raise_button.disabled
        await view.all_in_button.callback(make_interaction(1))  # user 1 shoves 100
        assert view.all_in_button.disabled and view.raise_button.disabled
        assert view.call_button.label == "Call 98"


@pytest.mark.asyncio
async def test_a_legal_raise_moves_the_action_and_refreshes_the_message(tmp_path: Path) -> None:
    async with poker_world(tmp_path) as (db, manager, _):
        lobby = await _running(manager)
        view, table, hand = make_view(make_bot(db, manager), lobby)
        interaction = make_interaction(1)
        await view.raise_to(interaction, 6)
        assert hand.current == 2 and hand.current_bet == 6 and view.acted.is_set()
        interaction.response.edit_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_an_illegal_raise_is_answered_privately_and_changes_nothing(tmp_path: Path) -> None:
    async with poker_world(tmp_path) as (db, manager, _):
        lobby = await _running(manager)
        view, table, hand = make_view(make_bot(db, manager), lobby)
        with patch("yokai.ui.table_common.send", new=AsyncMock()) as sent:
            await view.raise_to(make_interaction(1), 3)
        assert sent.await_args.kwargs["ephemeral"] is True
        assert "minimum raise is to 4" in sent.await_args.kwargs["embed"].description
        assert hand.current == 1 and hand.current_bet == 2


@pytest.mark.asyncio
async def test_the_raise_form_parses_the_amount_and_rejects_junk(tmp_path: Path) -> None:
    async with poker_world(tmp_path) as (db, manager, _):
        lobby = await _running(manager)
        view, table, hand = make_view(make_bot(db, manager), lobby)
        modal = RaiseModal(view, 4, 100)
        modal.amount._value = "abc"
        with patch("yokai.ui.table_common.send", new=AsyncMock()) as sent:
            await modal.on_submit(make_interaction(1))
        assert "whole number" in sent.await_args.kwargs["embed"].description
        assert hand.current == 1
        modal.amount._value = "1,0"  # commas are stripped like in the stake form: 10
        await modal.on_submit(make_interaction(1))
        assert hand.current_bet == 10


@pytest.mark.asyncio
async def test_leaving_off_turn_is_remembered_and_the_player_is_folded_when_up(
    tmp_path: Path,
) -> None:
    async with poker_world(tmp_path) as (db, manager, _):
        lobby = await _running(manager)
        view, table, hand = make_view(make_bot(db, manager), lobby)
        with patch("yokai.ui.poker_views.send", new=AsyncMock()) as sent:
            await view.leave_button.callback(make_interaction(2))  # user 1 is up
        assert 2 in table.leaving and hand.current == 1
        assert sent.await_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_buttons_do_nothing_once_the_hand_is_closed(tmp_path: Path) -> None:
    async with poker_world(tmp_path) as (db, manager, _):
        lobby = await _running(manager)
        view, table, hand = make_view(make_bot(db, manager), lobby)
        view.closed = True
        with patch("yokai.ui.table_common.send", new=AsyncMock()) as sent:
            await view.call_button.callback(make_interaction(1))
        assert sent.await_count == 1 and hand.current == 1
