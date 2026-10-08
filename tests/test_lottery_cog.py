"""LotteryCog: the panel, the scheduler and the announcements."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from yokai.cogs.lottery import LotteryCog
from yokai.storage.db import Database
from yokai.ui.lottery_views import BuyView

CHANNEL = 10
WEEK = 7 * 24 * 3600
NOW = 1_000_000


async def _db(tmp_path: Path, *, with_channel: bool = True) -> Database:
    db = Database(tmp_path / "cog.db")
    await db.initialize()
    for user_id in (1, 2):
        await db.ensure_wallet(user_id, 1000)
    if with_channel:
        await db.set_multiplayer_channel(CHANNEL, "lottery", 1)
    return db


def make_bot(db: Database) -> MagicMock:
    bot = MagicMock()
    bot.user = None
    bot.db = db
    bot.config.economy_welcome_bonus = 0
    bot.config.lottery_ticket_price = 100
    bot.config.lottery_number_range = 20
    bot.config.lottery_max_tickets_per_user = 10
    bot.config.lottery_draw_interval_hours = 168
    bot.add_view = MagicMock()
    return bot


def make_channel() -> MagicMock:
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = CHANNEL
    sent = MagicMock(spec=discord.Message)
    sent.id = 5000
    channel.send = AsyncMock(return_value=sent)
    partial = MagicMock()
    partial.edit = AsyncMock()
    channel.get_partial_message = MagicMock(return_value=partial)
    return channel


@pytest.mark.asyncio
async def test_loading_the_cog_registers_the_buy_view_and_starts_the_scheduler(
    tmp_path: Path,
) -> None:
    db = await _db(tmp_path)
    try:
        bot = make_bot(db)
        cog = LotteryCog(bot)
        await cog.cog_load()
        try:
            assert isinstance(bot.add_view.call_args.args[0], BuyView)
            assert cog._task is not None and not cog._task.done()
        finally:
            await cog.cog_unload()
        assert cog._task is None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_posting_the_panel_sends_the_embed_and_starts_the_clock(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        cog = LotteryCog(make_bot(db))
        channel = make_channel()
        message = await cog.post_panel(channel)
        assert message.id == 5000
        kwargs = channel.send.await_args.kwargs
        assert isinstance(kwargs["view"], BuyView) and "Lottery" in kwargs["embed"].title
        assert kwargs["allowed_mentions"].everyone is False
        assert (await db.get_lottery_state()).next_draw_ts is not None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_the_panel_reflects_the_pot_and_tickets(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        await db.ensure_lottery_schedule(NOW, WEEK)
        await db.buy_lottery_tickets(
            1, [3, 7], price=100, max_per_user=10, welcome_bonus=0, now=NOW + 5
        )
        embed = await LotteryCog(make_bot(db)).panel_embed()
        text = " ".join(f.value for f in embed.fields)
        assert "200" in text and "2" in text
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_updating_the_panel_edits_the_stored_message(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        bot = make_bot(db)
        channel = make_channel()
        bot.get_channel = MagicMock(return_value=channel)
        await LotteryCog(bot).update_panel()
        channel.get_partial_message.assert_called_once_with(1)
        channel.get_partial_message.return_value.edit.assert_awaited_once()
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_updating_with_no_lottery_channel_is_a_quiet_no_op(tmp_path: Path) -> None:
    db = await _db(tmp_path, with_channel=False)
    try:
        bot = make_bot(db)
        bot.get_channel = MagicMock(return_value=None)
        await LotteryCog(bot).update_panel()  # must not raise
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_tick_with_no_lottery_channel_draws_nothing(tmp_path: Path) -> None:
    db = await _db(tmp_path, with_channel=False)
    try:
        await db.ensure_lottery_schedule(NOW, WEEK)
        cog = LotteryCog(make_bot(db))
        await cog.tick(now=NOW + WEEK + 5)
        assert (await db.get_lottery_state()).draw_no == 1
        assert await db.get_last_draw() is None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_tick_starts_the_clock_when_the_lottery_has_a_channel_but_no_schedule(
    tmp_path: Path,
) -> None:
    db = await _db(tmp_path)
    try:
        bot = make_bot(db)
        bot.get_channel = MagicMock(return_value=make_channel())
        await LotteryCog(bot).tick(now=NOW)
        assert (await db.get_lottery_state()).next_draw_ts == NOW + WEEK
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_due_tick_draws_pays_and_announces_in_the_channel(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        await db.ensure_lottery_schedule(NOW, WEEK)
        await db.buy_lottery_tickets(
            1, [7], price=100, max_per_user=10, welcome_bonus=0, now=NOW + 5
        )
        bot = make_bot(db)
        channel = make_channel()
        bot.get_channel = MagicMock(return_value=channel)
        cog = LotteryCog(bot)
        with patch("yokai.cogs.lottery.draw_number", return_value=7):
            await cog.tick(now=NOW + WEEK)
        wallet = await db.get_wallet(1)
        assert wallet is not None and wallet.balance == 900 + 100
        announcement = channel.send.await_args.kwargs["embed"]
        assert "<@1>" in announcement.description and "draw #1" in announcement.title
        assert channel.send.await_args.kwargs["allowed_mentions"].everyone is False
        assert await db.get_unannounced_draws() == []
        assert (await db.get_lottery_state()).draw_no == 2
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_tick_before_the_draw_time_does_nothing(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        await db.ensure_lottery_schedule(NOW, WEEK)
        bot = make_bot(db)
        channel = make_channel()
        bot.get_channel = MagicMock(return_value=channel)
        await LotteryCog(bot).tick(now=NOW + WEEK - 1)
        channel.send.assert_not_awaited()
        assert (await db.get_lottery_state()).draw_no == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_failed_announcement_is_retried_on_the_next_tick_without_a_second_draw(
    tmp_path: Path,
) -> None:
    db = await _db(tmp_path)
    try:
        await db.ensure_lottery_schedule(NOW, WEEK)
        await db.buy_lottery_tickets(
            1, [7], price=100, max_per_user=10, welcome_bonus=0, now=NOW + 5
        )
        bot = make_bot(db)
        channel = make_channel()
        response = MagicMock(status=500, reason="Server Error")
        channel.send = AsyncMock(
            side_effect=[discord.HTTPException(response, "boom"), MagicMock(id=6000)]
        )
        bot.get_channel = MagicMock(return_value=channel)
        cog = LotteryCog(bot)
        with patch("yokai.cogs.lottery.draw_number", return_value=7):
            await cog.tick(now=NOW + WEEK)  # the draw is made, the announcement fails
            assert [d.draw_no for d in await db.get_unannounced_draws()] == [1]
            await cog.tick(now=NOW + WEEK + 60)  # retried; no second draw
        assert await db.get_unannounced_draws() == []
        assert channel.send.await_count == 2
        assert (await db.get_lottery_state()).draw_no == 2
        wallet = await db.get_wallet(1)
        assert wallet is not None and wallet.balance == 1000  # paid exactly once
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_bot_that_was_offline_draws_once_on_the_first_tick(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        await db.ensure_lottery_schedule(NOW, WEEK)
        bot = make_bot(db)
        bot.get_channel = MagicMock(return_value=make_channel())
        cog = LotteryCog(bot)
        await cog.tick(now=NOW + 3 * WEEK + 5)
        await cog.tick(now=NOW + 3 * WEEK + 65)
        assert (await db.get_lottery_state()).draw_no == 2  # one draw, not three
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_the_scheduler_survives_a_failing_tick(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        cog = LotteryCog(make_bot(db))
        calls = {"n": 0}

        async def flaky(now=None):  # type: ignore[no-untyped-def]
            calls["n"] += 1
            if calls["n"] == 1:
                raise discord.HTTPException(MagicMock(status=500, reason="x"), "boom")

        with patch.object(cog, "tick", new=flaky), patch("yokai.cogs.lottery.CHECK_SECONDS", 0.01):
            await cog.cog_load()
            import asyncio

            await asyncio.sleep(0.15)
            await cog.cog_unload()
        assert calls["n"] >= 2  # it kept going after the first failure
    finally:
        await db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [OSError("network is down"), TimeoutError("slow")])
async def test_the_scheduler_survives_any_failure_not_just_discord_and_database_errors(
    tmp_path: Path, error: Exception
) -> None:
    """Regression: a plain OSError (a dropped connection) escaped the loop and ended the
    weekly lottery until the next restart, silently."""
    import asyncio

    db = await _db(tmp_path)
    try:
        cog = LotteryCog(make_bot(db))
        calls = {"n": 0}

        async def flaky(now=None):  # type: ignore[no-untyped-def]
            calls["n"] += 1
            if calls["n"] == 1:
                raise error

        with patch.object(cog, "tick", new=flaky), patch("yokai.cogs.lottery.CHECK_SECONDS", 0.01):
            await cog.cog_load()
            await asyncio.sleep(0.15)
            task = cog._task
            await cog.cog_unload()
        assert task is not None and calls["n"] >= 2  # it kept going after the failure
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_dropped_connection_while_announcing_leaves_the_draw_for_a_retry(
    tmp_path: Path,
) -> None:
    db = await _db(tmp_path)
    try:
        await db.ensure_lottery_schedule(NOW, WEEK)
        await db.buy_lottery_tickets(
            1, [7], price=100, max_per_user=10, welcome_bonus=0, now=NOW + 5
        )
        bot = make_bot(db)
        channel = make_channel()
        channel.send = AsyncMock(side_effect=[OSError("network is down"), MagicMock(id=6000)])
        bot.get_channel = MagicMock(return_value=channel)
        cog = LotteryCog(bot)
        with patch("yokai.cogs.lottery.draw_number", return_value=7):
            await cog.tick(now=NOW + WEEK)  # must not raise
            assert [d.draw_no for d in await db.get_unannounced_draws()] == [1]
            await cog.tick(now=NOW + WEEK + 60)
        assert await db.get_unannounced_draws() == []
        wallet = await db.get_wallet(1)
        assert wallet is not None and wallet.balance == 1000  # 900 left after the ticket + 100
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_slow_panel_edit_cannot_overwrite_a_newer_one(tmp_path: Path) -> None:
    """Regression: two panel updates raced; the slower, older edit landed last and the panel
    showed a stale pot until the next purchase."""
    import asyncio

    db = await _db(tmp_path)
    try:
        await db.ensure_lottery_schedule(NOW, WEEK)
        bot = make_bot(db)
        channel = make_channel()
        bot.get_channel = MagicMock(return_value=channel)
        landed: list[str] = []
        gate = asyncio.Event()
        calls = {"n": 0}

        async def slow_first_edit(**kwargs):  # type: ignore[no-untyped-def]
            calls["n"] += 1
            if calls["n"] == 1:
                await gate.wait()
            pot_field = next(f.value for f in kwargs["embed"].fields if f.name == "Pot")
            landed.append(pot_field)

        channel.get_partial_message.return_value.edit = AsyncMock(side_effect=slow_first_edit)
        cog = LotteryCog(bot)
        first = asyncio.create_task(cog.update_panel())  # reads pot 0, then its edit stalls
        await asyncio.sleep(0.05)
        await db.buy_lottery_tickets(
            1, [3, 7], price=100, max_per_user=10, welcome_bonus=0, now=NOW + 5
        )  # the pot becomes 200
        second = asyncio.create_task(cog.update_panel())
        await asyncio.sleep(0.05)
        gate.set()
        await asyncio.gather(first, second)
        assert "200" in landed[-1], landed  # the newest state is what the panel ends on
    finally:
        await db.close()
