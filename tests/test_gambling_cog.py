"""Tests for the slots, roulette and stats commands against a real database."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest
from discord import app_commands

from tests.card_helpers import cards
from yokai.cogs.gambling import GamblingCog
from yokai.gambling.engines.slots import SlotsResult
from yokai.gambling.sessions import ActiveGames
from yokai.storage.db import Database
from yokai.ui.game_views import BlackjackView

WELCOME = 200
SEVENS = SlotsResult(("SEVEN", "SEVEN", "SEVEN"), 500)  # 50x
MISS = SlotsResult(("BAR", "LEMON", "BELL"), 0)


async def _setup(tmp_dir: str) -> tuple[GamblingCog, MagicMock, Database]:
    db = Database(Path(tmp_dir) / "t.db")
    await db.initialize()
    bot = MagicMock()
    bot.user = None
    bot.db = db
    bot.active_games = ActiveGames()
    bot.config.gambling_min_bet = 10
    bot.config.gambling_max_bet = 5000
    bot.config.economy_welcome_bonus = WELCOME
    return GamblingCog(bot), bot, db


def _interaction(user_id: int = 111) -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    user.bot = False
    interaction.user = user
    interaction.response.defer = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)
    return interaction


def _pending() -> MagicMock:
    message = MagicMock(spec=discord.Message)
    message.edit = AsyncMock()
    return message


async def _balance(db: Database, user_id: int) -> int:
    wallet = await db.get_wallet(user_id)
    assert wallet is not None
    return wallet.balance


@pytest.mark.asyncio
async def test_a_bet_below_the_minimum_is_rejected_ephemerally_before_deferring() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        cog, bot, db = await _setup(tmp_dir)
        interaction = _interaction()
        with patch("yokai.cogs.gambling.send", new=AsyncMock()) as sent:
            await cog.slots.callback(cog, interaction, bet=5)
        assert sent.await_args.kwargs["ephemeral"] is True
        interaction.response.defer.assert_not_awaited()
        assert await db.get_wallet(111) is None
        assert not bot.active_games.is_active(111)
        await db.close()


@pytest.mark.asyncio
async def test_a_bet_above_the_balance_is_rejected_and_costs_nothing() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        cog, bot, db = await _setup(tmp_dir)
        interaction = _interaction()
        with patch("yokai.cogs.gambling.send", new=AsyncMock()) as sent:
            await cog.slots.callback(cog, interaction, bet=WELCOME + 1)
        assert sent.await_args.kwargs["ephemeral"] is True
        interaction.response.defer.assert_not_awaited()
        assert await _balance(db, 111) == WELCOME
        assert not bot.active_games.is_active(111)
        await db.close()


@pytest.mark.asyncio
async def test_a_second_game_while_one_is_running_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        cog, bot, db = await _setup(tmp_dir)
        bot.active_games.claim(111)
        interaction = _interaction()
        with patch("yokai.cogs.gambling.send", new=AsyncMock()) as sent:
            await cog.slots.callback(cog, interaction, bet=10)
        assert sent.await_args.kwargs["ephemeral"] is True
        assert "in progress" in sent.await_args.kwargs["embed"].description
        assert bot.active_games.is_active(111)  # the running game keeps its claim
        assert await _balance(db, 111) == WELCOME
        await db.close()


@pytest.mark.asyncio
async def test_a_slots_win_pays_out_and_reveals_the_reels() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        cog, bot, db = await _setup(tmp_dir)
        interaction = _interaction()
        pending = _pending()
        with (
            patch("yokai.cogs.gambling.SPIN_DELAY_S", 0),
            patch("yokai.cogs.gambling.slots_engine.spin", return_value=SEVENS),
            patch("yokai.cogs.gambling.send", new=AsyncMock(return_value=pending)),
        ):
            await cog.slots.callback(cog, interaction, bet=10)
        assert await _balance(db, 111) == WELCOME - 10 + 500
        embed = pending.edit.await_args.kwargs["embed"]
        assert "[SEVEN] [SEVEN] [SEVEN]" in embed.description
        assert pending.edit.await_args.kwargs["view"] is not None
        assert not bot.active_games.is_active(111)
        await db.close()


@pytest.mark.asyncio
async def test_a_new_user_can_bet_the_whole_welcome_bonus_and_lose_to_exactly_zero() -> None:
    """Review Focus: a loss never leaves a negative balance."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        cog, bot, db = await _setup(tmp_dir)
        interaction = _interaction(222)
        with (
            patch("yokai.cogs.gambling.SPIN_DELAY_S", 0),
            patch("yokai.cogs.gambling.slots_engine.spin", return_value=MISS),
            patch("yokai.cogs.gambling.send", new=AsyncMock(return_value=_pending())),
        ):
            await cog.slots.callback(cog, interaction, bet=WELCOME)
        assert await _balance(db, 222) == 0
        assert not bot.active_games.is_active(222)
        await db.close()


@pytest.mark.asyncio
async def test_a_failure_after_the_debit_refunds_the_stake_and_frees_the_player() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        cog, bot, db = await _setup(tmp_dir)
        interaction = _interaction()
        with (
            patch("yokai.cogs.gambling.SPIN_DELAY_S", 0),
            patch("yokai.cogs.gambling.slots_engine.spin", return_value=SEVENS),
            patch("yokai.cogs.gambling.send", new=AsyncMock(return_value=_pending())),
            patch.object(db, "settle_bet", new=AsyncMock(side_effect=RuntimeError("db down"))),
            pytest.raises(RuntimeError),
        ):
            await cog.slots.callback(cog, interaction, bet=10)
        assert await _balance(db, 111) == WELCOME
        assert not bot.active_games.is_active(111)
        await db.close()


@pytest.mark.asyncio
async def test_roulette_rejects_a_bad_pick_before_touching_the_wallet() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        cog, bot, db = await _setup(tmp_dir)
        interaction = _interaction()
        choice = app_commands.Choice(name="dozen", value="dozen")
        with patch("yokai.cogs.gambling.send", new=AsyncMock()) as sent:
            await cog.roulette.callback(cog, interaction, bet=10, choice=choice, pick=9)
        assert sent.await_args.kwargs["ephemeral"] is True
        interaction.response.defer.assert_not_awaited()
        assert await db.get_wallet(111) is None
        await db.close()


@pytest.mark.asyncio
async def test_a_roulette_straight_up_win_pays_thirty_six_to_one_total() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        cog, bot, db = await _setup(tmp_dir)
        interaction = _interaction()
        pending = _pending()
        choice = app_commands.Choice(name="straight", value="straight")
        with (
            patch("yokai.cogs.gambling.SPIN_DELAY_S", 0),
            patch("yokai.cogs.gambling.roulette_engine.spin", return_value=17),
            patch("yokai.cogs.gambling.send", new=AsyncMock(return_value=pending)),
        ):
            await cog.roulette.callback(cog, interaction, bet=10, choice=choice, pick=17)
        assert await _balance(db, 111) == WELCOME - 10 + 360
        assert "17" in pending.edit.await_args.kwargs["embed"].description
        assert pending.edit.await_args.kwargs["view"] is None
        await db.close()


@pytest.mark.asyncio
async def test_a_roulette_loss_keeps_only_the_remainder() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        cog, bot, db = await _setup(tmp_dir)
        interaction = _interaction()
        choice = app_commands.Choice(name="red", value="red")
        with (
            patch("yokai.cogs.gambling.SPIN_DELAY_S", 0),
            patch("yokai.cogs.gambling.roulette_engine.spin", return_value=0),
            patch("yokai.cogs.gambling.send", new=AsyncMock(return_value=_pending())),
        ):
            await cog.roulette.callback(cog, interaction, bet=50, choice=choice, pick=None)
        assert await _balance(db, 111) == WELCOME - 50
        await db.close()


@pytest.mark.asyncio
async def test_gamble_stats_reports_settled_games() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        cog, bot, db = await _setup(tmp_dir)
        bet_id = await db.place_bet(111, 10, "slots", WELCOME)
        await db.settle_bet(bet_id, 40)
        with patch("yokai.cogs.gambling.send", new=AsyncMock()) as sent:
            await cog.gamble_stats.callback(cog, _interaction(111), user=None)
        embed = sent.await_args.kwargs["embed"]
        assert "`slots`" in embed.description and "+30" in embed.description
        await db.close()


@pytest.mark.asyncio
async def test_blackjack_natural_pays_immediately_and_sends_no_buttons() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        cog, bot, db = await _setup(tmp_dir)
        interaction = _interaction()
        with (
            patch(
                "yokai.cogs.gambling.blackjack_engine.build_shoe",
                return_value=cards("As Kd Ks 9h"),
            ),
            patch("yokai.cogs.gambling.send", new=AsyncMock(return_value=_pending())) as sent,
        ):
            await cog.blackjack.callback(cog, interaction, bet=10)
        assert sent.await_args.kwargs["view"] is None
        assert await _balance(db, 111) == WELCOME - 10 + 25
        assert not bot.active_games.is_active(111)
        await db.close()


@pytest.mark.asyncio
async def test_blackjack_in_play_keeps_the_player_claimed_until_the_hand_ends() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        cog, bot, db = await _setup(tmp_dir)
        interaction = _interaction()
        pending = _pending()
        with (
            patch(
                "yokai.cogs.gambling.blackjack_engine.build_shoe",
                return_value=cards("10h 6d 9c 5s 7c"),
            ),
            patch("yokai.cogs.gambling.send", new=AsyncMock(return_value=pending)) as sent,
        ):
            await cog.blackjack.callback(cog, interaction, bet=10)
        view = sent.await_args.kwargs["view"]
        assert isinstance(view, BlackjackView)
        assert view.message is pending
        assert bot.active_games.is_active(111)
        assert await _balance(db, 111) == WELCOME - 10

        press = MagicMock()
        press.user.id = 111
        press.response.is_done = MagicMock(return_value=False)
        press.response.edit_message = AsyncMock()
        await view.stand_button.callback(press)
        assert await _balance(db, 111) == WELCOME - 10 + 20
        assert not bot.active_games.is_active(111)
        await db.close()


@pytest.mark.asyncio
async def test_blackjack_rejects_a_bad_bet_before_deferring() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        cog, bot, db = await _setup(tmp_dir)
        interaction = _interaction()
        with patch("yokai.cogs.gambling.send", new=AsyncMock()) as sent:
            await cog.blackjack.callback(cog, interaction, bet=1)
        assert sent.await_args.kwargs["ephemeral"] is True
        interaction.response.defer.assert_not_awaited()
        assert await db.get_wallet(111) is None
        await db.close()


@pytest.mark.asyncio
async def test_blackjack_failure_after_the_bet_refunds_it() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        cog, bot, db = await _setup(tmp_dir)
        interaction = _interaction()
        with (
            patch(
                "yokai.cogs.gambling.blackjack_engine.build_shoe",
                return_value=cards("10h 6d 9c 5s 7c"),
            ),
            patch("yokai.cogs.gambling.send", new=AsyncMock(side_effect=RuntimeError("boom"))),
            pytest.raises(RuntimeError),
        ):
            await cog.blackjack.callback(cog, interaction, bet=10)
        assert await _balance(db, 111) == WELCOME
        assert not bot.active_games.is_active(111)
        await db.close()
