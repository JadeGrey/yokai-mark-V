"""Tests for the interactive blackjack view against a real database."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.card_helpers import cards
from yokai.gambling.engines.blackjack import BlackjackGame
from yokai.storage.db import Database
from yokai.ui.game_views import BlackjackView

WELCOME = 200


async def _make(
    tmp_dir: str, shoe: str, stake: int = 10, can_double: bool = True
) -> tuple[Database, BlackjackView, list[int]]:
    db = Database(Path(tmp_dir) / "t.db")
    await db.initialize()
    bet_id = await db.place_bet(1, stake, "blackjack", WELCOME, group_id="g1")
    released: list[int] = []
    view = BlackjackView(
        game=BlackjackGame(cards(shoe)),
        db=db,
        user_id=1,
        user_mention="<@1>",
        stake=stake,
        bet_id=bet_id,
        group_id="g1",
        welcome_bonus=WELCOME,
        can_double=can_double,
        release=lambda: released.append(1),
    )
    return db, view, released


def _interaction(user_id: int = 1) -> MagicMock:
    interaction = MagicMock()
    interaction.user.id = user_id
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.response.edit_message = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    return interaction


async def _balance(db: Database) -> int:
    wallet = await db.get_wallet(1)
    assert wallet is not None
    return wallet.balance


IN_PLAY = "10h 9d 6c 8s 10c"  # player 16 vs dealer 17: hit busts, double busts, stand loses
WIN_ON_STAND = "10h 6d 9c 5s 7c"  # player 19 vs dealer 18 after one draw
DOUBLE_WINS = "5h 9d 6c 8s 10c"  # player 11 doubles into 21 vs dealer 17


@pytest.mark.asyncio
async def test_a_natural_settles_at_the_deal_without_buttons() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db, view, released = await _make(tmp_dir, "As Kd Ks 9h")
        embed = await view.start()
        assert view.finished and released == [1]
        assert await _balance(db) == WELCOME - 10 + 25
        assert "Blackjack!" in (embed.description or "")
        assert all(item.disabled for item in view.children)
        await db.close()


@pytest.mark.asyncio
async def test_a_hand_in_play_hides_the_dealer_hole_card_and_holds_the_stake() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db, view, released = await _make(tmp_dir, IN_PLAY)
        embed = await view.start()
        assert not view.finished and released == []
        assert "??" in (embed.description or "")
        assert await _balance(db) == WELCOME - 10
        await db.close()


@pytest.mark.asyncio
async def test_stand_settles_a_win() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db, view, released = await _make(tmp_dir, WIN_ON_STAND)
        await view.start()
        interaction = _interaction()
        await view.stand_button.callback(interaction)
        assert await _balance(db) == WELCOME - 10 + 20
        assert released == [1]
        interaction.response.edit_message.assert_awaited_once()
        await db.close()


@pytest.mark.asyncio
async def test_hit_bust_loses_the_stake() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db, view, released = await _make(tmp_dir, IN_PLAY)
        await view.start()
        interaction = _interaction()
        await view.hit_button.callback(interaction)
        assert view.finished and released == [1]
        assert await _balance(db) == WELCOME - 10
        embed = interaction.response.edit_message.await_args.kwargs["embed"]
        assert "Bust" in (embed.description or "")
        await db.close()


@pytest.mark.asyncio
async def test_double_places_a_second_bet_and_pays_both_on_a_win() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db, view, released = await _make(tmp_dir, DOUBLE_WINS)
        await view.start()
        await view.double_button.callback(_interaction())
        assert view.finished and released == [1]
        assert await _balance(db) == WELCOME - 20 + 40
        await db.close()


@pytest.mark.asyncio
async def test_double_is_disabled_up_front_when_the_player_cannot_afford_it() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db, view, _ = await _make(tmp_dir, IN_PLAY, can_double=False)
        assert view.double_button.disabled is True
        await db.close()


@pytest.mark.asyncio
async def test_double_after_spending_the_balance_elsewhere_errors_and_the_hand_continues() -> None:
    """Review Focus: a mid-hand /pay must not let Double push the balance negative."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db, view, released = await _make(tmp_dir, DOUBLE_WINS)
        await view.start()
        await db.pay(1, 2, WELCOME - 10, WELCOME)  # player pays away everything left
        interaction = _interaction()
        await view.double_button.callback(interaction)
        assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True
        assert not view.finished and released == []
        assert view.double_button.disabled is True
        assert await _balance(db) == 0
        await db.close()


@pytest.mark.asyncio
async def test_a_double_click_settles_once() -> None:
    """Review Focus: two near-simultaneous presses must not double-settle."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db, view, released = await _make(tmp_dir, WIN_ON_STAND)
        await view.start()
        first, second = _interaction(), _interaction()
        await asyncio.gather(view.stand_button.callback(first), view.stand_button.callback(second))
        assert await _balance(db) == WELCOME - 10 + 20  # paid once, not twice
        assert released == [1]
        first.response.edit_message.assert_awaited_once()
        second.response.edit_message.assert_not_awaited()
        second.response.defer.assert_awaited_once()
        await db.close()


@pytest.mark.asyncio
async def test_timeout_auto_stands_and_settles() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db, view, released = await _make(tmp_dir, WIN_ON_STAND)
        await view.start()
        view.message = MagicMock()
        view.message.edit = AsyncMock()
        await view.on_timeout()
        assert view.finished and released == [1]
        assert await _balance(db) == WELCOME - 10 + 20
        view.message.edit.assert_awaited_once()
        await db.close()


@pytest.mark.asyncio
async def test_only_the_player_can_use_the_buttons() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db, view, _ = await _make(tmp_dir, IN_PLAY)
        assert await view.interaction_check(_interaction(1)) is True
        assert await view.interaction_check(_interaction(2)) is False
        await db.close()


@pytest.mark.asyncio
async def test_abort_refunds_the_stake_and_frees_the_player_once() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db, view, released = await _make(tmp_dir, IN_PLAY)
        await view.start()
        await view.abort()
        await view.abort()
        assert await _balance(db) == WELCOME
        assert released == [1]
        await db.close()


@pytest.mark.asyncio
async def test_a_settlement_failure_refunds_and_frees_the_player() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db, view, released = await _make(tmp_dir, WIN_ON_STAND)
        await view.start()
        with (
            patch.object(db, "settle_bet", new=AsyncMock(side_effect=RuntimeError("db down"))),
            pytest.raises(RuntimeError),
        ):
            await view.stand_button.callback(_interaction())
        assert await _balance(db) == WELCOME
        assert released == [1]
        await db.close()
