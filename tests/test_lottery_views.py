"""The Buy tickets button and form, against a real database and mocked Discord."""

from __future__ import annotations

import random
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from tests.moderation_helpers import fake_moderation
from yokai.storage.db import Database
from yokai.ui.lottery_views import BUY_CUSTOM_ID, BuyView, TicketsModal, handle_buy

CHANNEL = 10
WEEK = 7 * 24 * 3600


async def _world(tmp_path: Path, balance: int = 1000):  # type: ignore[no-untyped-def]
    db = Database(tmp_path / "views.db")
    await db.initialize()
    await db.ensure_wallet(1, balance)
    await db.set_multiplayer_channel(CHANNEL, "lottery", 1)
    await db.ensure_lottery_schedule(1, 10**12)  # a draw far in the future: sales are open
    return db


def make_bot(db: Database) -> MagicMock:
    bot = MagicMock()
    bot.user = None
    bot.db = db
    bot.config.economy_welcome_bonus = 0
    bot.config.lottery_ticket_price = 100
    bot.config.lottery_number_range = 20
    bot.config.lottery_max_tickets_per_user = 10
    bot.moderation = fake_moderation(owner_id=999)
    panel = MagicMock()
    panel.update_panel = AsyncMock()
    bot.get_cog = MagicMock(return_value=panel)
    bot.rng = random.Random(3)
    return bot


def make_interaction(user_id: int = 1, channel_id: int = CHANNEL) -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock(spec=discord.Member)
    interaction.user.id = user_id
    interaction.channel_id = channel_id
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.response.send_modal = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


async def _submit(bot, numbers: str, quick: str):  # type: ignore[no-untyped-def]
    modal = TicketsModal(bot)
    modal.numbers._value = numbers  # type: ignore[attr-defined]
    modal.quick._value = quick  # type: ignore[attr-defined]
    interaction = make_interaction()
    with (
        patch("yokai.ui.lottery_views.send", new=AsyncMock()) as sent,
        patch("yokai.ui.table_common.send", new=AsyncMock()) as failed,
        patch("yokai.ui.lottery_views.new_rng", return_value=bot.rng),
    ):
        await modal.on_submit(interaction)
    return interaction, sent, failed


def test_the_buy_view_is_persistent_with_a_fixed_custom_id() -> None:
    view = BuyView(MagicMock())
    assert view.timeout is None and view.is_persistent()
    assert [item.custom_id for item in view.children] == [BUY_CUSTOM_ID]  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_pressing_buy_in_the_lottery_channel_opens_the_form(tmp_path: Path) -> None:
    db = await _world(tmp_path)
    try:
        interaction = make_interaction()
        await handle_buy(make_bot(db), interaction)
        modal = interaction.response.send_modal.await_args.args[0]
        assert isinstance(modal, TicketsModal)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_pressing_buy_elsewhere_is_refused_ephemerally(tmp_path: Path) -> None:
    db = await _world(tmp_path)
    try:
        interaction = make_interaction(channel_id=12345)
        with patch("yokai.ui.table_common.send", new=AsyncMock()) as sent:
            await handle_buy(make_bot(db), interaction)
        interaction.response.send_modal.assert_not_awaited()
        assert sent.await_args.kwargs["ephemeral"] is True
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_pressing_buy_in_a_table_channel_is_refused(tmp_path: Path) -> None:
    db = await _world(tmp_path)
    try:
        await db.set_multiplayer_channel(30, "duel", 2)
        interaction = make_interaction(channel_id=30)
        with patch("yokai.ui.table_common.send", new=AsyncMock()):
            await handle_buy(make_bot(db), interaction)
        interaction.response.send_modal.assert_not_awaited()
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_pressing_buy_with_too_little_renown_is_refused(tmp_path: Path) -> None:
    db = await _world(tmp_path, balance=50)
    try:
        interaction = make_interaction()
        with patch("yokai.ui.table_common.send", new=AsyncMock()) as sent:
            await handle_buy(make_bot(db), interaction)
        interaction.response.send_modal.assert_not_awaited()
        assert "isn't enough" in sent.await_args.kwargs["embed"].description
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_pressing_buy_after_the_draw_time_is_refused(tmp_path: Path) -> None:
    db = await _world(tmp_path)
    try:
        await db.conn.execute("UPDATE lottery_state SET next_draw_ts = 2 WHERE id = 1;")
        await db.conn.commit()
        interaction = make_interaction()
        with patch("yokai.ui.table_common.send", new=AsyncMock()) as sent:
            await handle_buy(make_bot(db), interaction)
        interaction.response.send_modal.assert_not_awaited()
        assert "closed" in sent.await_args.kwargs["embed"].description.lower()
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_hand_picked_numbers_are_bought_and_receipted_privately(tmp_path: Path) -> None:
    db = await _world(tmp_path)
    try:
        bot = make_bot(db)
        interaction, sent, failed = await _submit(bot, "3, 7, 19", "")
        interaction.response.defer.assert_awaited_once()
        assert await db.user_lottery_numbers(1) == [3, 7, 19]
        assert sent.await_args.kwargs["ephemeral"] is True
        assert "3, 7, 19" in sent.await_args.kwargs["embed"].description
        failed.assert_not_awaited()
        bot.get_cog.return_value.update_panel.assert_awaited_once()
        wallet = await db.get_wallet(1)
        assert wallet is not None and wallet.balance == 700
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_quick_picks_fill_in_without_clashing_with_chosen_numbers(tmp_path: Path) -> None:
    db = await _world(tmp_path)
    try:
        bot = make_bot(db)
        await _submit(bot, "5, 6", "3")
        held = await db.user_lottery_numbers(1)
        assert len(held) == 5 and {5, 6} <= set(held)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_hostile_input_is_an_ephemeral_error_and_costs_nothing(tmp_path: Path) -> None:
    db = await _world(tmp_path)
    try:
        bot = make_bot(db)
        for numbers, quick in (
            ("abc", ""),
            ("0, 21", ""),
            ("-5", ""),
            ("@everyone **x**", ""),
            ("9" * 4000, ""),
            ("", "99999999"),
            ("", "-3"),
            ("", ""),
            ("1,2,3,4,5,6,7,8,9,10,11", ""),
        ):
            _, sent, failed = await _submit(bot, numbers, quick)
            assert failed.await_args.kwargs["ephemeral"] is True, (numbers, quick)
            sent.assert_not_awaited()
        assert await db.user_lottery_numbers(1) == []
        wallet = await db.get_wallet(1)
        assert wallet is not None and wallet.balance == 1000
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_database_failure_is_answered_not_left_hanging(tmp_path: Path) -> None:
    import sqlite3

    db = await _world(tmp_path)
    try:
        bot = make_bot(db)
        broken = AsyncMock(side_effect=sqlite3.OperationalError("disk I/O error"))
        with patch.object(db, "buy_lottery_tickets", new=broken):
            _, sent, failed = await _submit(bot, "3", "")
        assert failed.await_args.kwargs["ephemeral"] is True
        sent.assert_not_awaited()
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_panel_that_cannot_be_updated_does_not_undo_the_purchase(tmp_path: Path) -> None:
    db = await _world(tmp_path)
    try:
        bot = make_bot(db)
        bot.get_cog.return_value.update_panel = AsyncMock(
            side_effect=discord.HTTPException(MagicMock(status=500, reason="x"), "boom")
        )
        interaction, sent, failed = await _submit(bot, "3", "")
        assert await db.user_lottery_numbers(1) == [3]
        sent.assert_awaited_once()
        failed.assert_not_awaited()
    finally:
        await db.close()


class _SentToDiscord(Exception):
    """Raised by the capturing adapter once discord.py has decided what to send."""


@pytest.mark.asyncio
async def test_the_ticket_form_asks_discord_for_a_private_thinking_reply() -> None:
    """A plain defer on a form submitted from a button is a silent update of the button's own
    message (and ignores `ephemeral`). Only `thinking=True` gives the private reply the form
    needs, so nothing can ever be deleted or edited on the lottery panel by accident."""
    from discord.webhook.async_ import async_context

    sent: dict[str, object] = {}

    class CapturingAdapter:
        async def create_interaction_response(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            payload = kwargs["params"].payload
            sent["type"] = payload["type"]
            sent["data"] = payload.get("data")
            raise _SentToDiscord

    parent = MagicMock()
    parent.type = discord.InteractionType.modal_submit
    interaction = MagicMock()
    interaction.response = discord.InteractionResponse(parent)

    modal = TicketsModal(MagicMock())
    modal.numbers._value = "3"  # type: ignore[attr-defined]
    modal.quick._value = ""  # type: ignore[attr-defined]
    token = async_context.set(CapturingAdapter())
    try:
        with pytest.raises(_SentToDiscord):
            await modal.on_submit(interaction)
    finally:
        async_context.reset(token)

    assert sent["type"] == discord.InteractionResponseType.deferred_channel_message.value
    assert sent["data"] == {"flags": 64}
