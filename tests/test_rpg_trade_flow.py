"""/trade in play: command checks, the session view, pickers, the Renown modal and the commit."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from tests.embed_helpers import emoji_count, total_embed_length
from tests.test_rpg_party_flow import interaction
from yokai.cogs.trade import TradeCog
from yokai.rpg.gear_store import ItemDrop
from yokai.rpg.models import ItemInstance
from yokai.rpg.runtime import RpgRuntime
from yokai.rpg.trade import TradeSession
from yokai.storage.db import Database
from yokai.ui.embeds import EmbedFactory
from yokai.ui.trade_views import ItemPickerView, RenownModal, TradeView

A, B = 5, 6
ITEM = "oni_weapon_t1_common"


async def world(tmp_path: Path, cash: int = 1000):  # type: ignore[no-untyped-def]
    db = Database(tmp_path / "tradeflow.db")
    await db.initialize()
    runtime = RpgRuntime.create(db, Path("content/rpg"))
    for user in (A, B):
        await runtime.store.create_character(user, "E", now=1)
        await db.credit_wallet(user, cash, "test", welcome_bonus=0)
    bot = MagicMock()
    bot.user = None
    bot.rpg = runtime
    bot.db = db
    bot.config.economy_welcome_bonus = 0
    return db, runtime, bot


async def give(runtime: RpgRuntime, user: int, content_id: str = ITEM) -> ItemInstance:
    definition = runtime.content.items[content_id]
    drop = ItemDrop(content_id, definition.stats, definition.max_durability)
    return await runtime.gear.grant_item(user, drop, inventory_cap=60, now=1)


def make_view(bot: Any, runtime: RpgRuntime) -> TradeView:
    session = TradeSession(A, B)
    runtime.trading[A] = runtime.trading[B] = session
    view = TradeView(bot, runtime, session, {A: "Aiko", B: "Ben"})
    view.message = MagicMock()
    view.message.edit = AsyncMock()
    return view


def member(user_id: int, bot: bool = False) -> MagicMock:
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    user.bot = bot
    user.display_name = f"Member {user_id}"
    return user


async def pick(picker: ItemPickerView, item_id: int, click: Any) -> None:
    select = next(c for c in picker.children if isinstance(c, discord.ui.Select))
    with (
        patch.object(type(select), "values", [str(item_id)], create=True),
        patch("yokai.ui.trade_views.send", new=AsyncMock()),
    ):
        await picker._picked(click)


async def owner_of(db: Database, item_id: int) -> Optional[int]:
    async with db.conn.execute("SELECT owner_id FROM rpg_items WHERE id = ?;", (item_id,)) as c:
        row = await c.fetchone()
    return row[0] if row else None


# ------------------------------------------------------------------------ the command


async def run_trade(cog: TradeCog, user_id: int, target: Any) -> Any:
    click = interaction(user_id)
    with patch("yokai.cogs.trade.send", new=AsyncMock(return_value=MagicMock())) as sent:
        await cog.trade.callback(cog, click, target)
    return sent


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["bot", "self", "no_me", "no_them", "fight_me", "fight_them", "busy_me", "busy_them"]
)
async def test_the_command_refuses_privately_before_posting(tmp_path: Path, case: str) -> None:
    db, runtime, bot = await world(tmp_path)
    try:
        cog = TradeCog(bot)
        user, target = A, member(B)
        if case == "bot":
            target = member(B, bot=True)
        elif case == "self":
            target = member(A)
        elif case == "no_me":
            user = 99
        elif case == "no_them":
            target = member(98)
        elif case == "fight_me":
            runtime.active_users.add(A)
        elif case == "fight_them":
            runtime.active_users.add(B)
        elif case == "busy_me":
            runtime.trading[A] = TradeSession(A, 77)
        elif case == "busy_them":
            runtime.trading[B] = TradeSession(B, 77)
        sent = await run_trade(cog, user, target)
        assert sent.await_args.kwargs["ephemeral"] is True and "view" not in sent.await_args.kwargs
        assert runtime.trading.get(A) is None or case == "busy_me"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_the_command_opens_a_public_trade_and_registers_both_members(tmp_path: Path) -> None:
    db, runtime, bot = await world(tmp_path)
    try:
        cog = TradeCog(bot)
        sent = await run_trade(cog, A, member(B))
        view = sent.await_args.kwargs["view"]
        assert isinstance(view, TradeView) and "ephemeral" not in sent.await_args.kwargs
        assert runtime.trading[A] is runtime.trading[B] is view.session
        assert view.message is sent.return_value
        assert view.allowed_user_ids == {A, B}
        assert "Trade" in (sent.await_args.kwargs["embed"].title or "")
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_trade_that_could_not_be_posted_frees_both_members(tmp_path: Path) -> None:
    db, runtime, bot = await world(tmp_path)
    try:
        cog = TradeCog(bot)
        click = interaction(A)
        with patch("yokai.cogs.trade.send", new=AsyncMock(return_value=None)):
            await cog.trade.callback(cog, click, member(B))
        assert runtime.trading == {}
    finally:
        await db.close()


# --------------------------------------------------------------------- building a deal


@pytest.mark.asyncio
async def test_adding_an_item_through_the_picker_updates_the_offer(tmp_path: Path) -> None:
    db, runtime, bot = await world(tmp_path)
    try:
        mine = await give(runtime, A)
        view = make_view(bot, runtime)
        click = interaction(A)
        with patch("yokai.ui.trade_views.send", new=AsyncMock()) as sent:
            await view.add_button.callback(click)
        picker = sent.await_args.kwargs["view"]
        assert sent.await_args.kwargs["ephemeral"] is True and isinstance(picker, ItemPickerView)
        await pick(picker, mine.id, interaction(A))
        assert view.session.offers[A].items == [mine.id]
        edit = view.message.edit.await_args.kwargs["embed"]
        assert f"#{mine.id}" in edit.fields[0].value
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_the_picker_hides_equipped_and_already_offered_items(tmp_path: Path) -> None:
    db, runtime, bot = await world(tmp_path)
    try:
        spare = await give(runtime, A)
        worn = await give(runtime, A, "oni_head_t1_common")
        offered = await give(runtime, A, "oni_body_t1_common")
        await runtime.gear.equip_item(A, worn.id, level=60, items=runtime.content.items)
        view = make_view(bot, runtime)
        view.session.add_item(A, offered.id)
        with patch("yokai.ui.trade_views.send", new=AsyncMock()) as sent:
            await view.add_button.callback(interaction(A))
        picker = sent.await_args.kwargs["view"]
        assert [i.id for i in picker.items] == [spare.id]
        # with nothing left, the member is told so instead of getting an empty picker
        view.session.add_item(A, spare.id)
        with patch("yokai.ui.trade_views.send", new=AsyncMock()) as sent:
            await view.add_button.callback(interaction(A))
        assert "Nothing to offer" in (sent.await_args.kwargs["embed"].title or "")
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_someone_elses_or_an_equipped_item_cannot_be_forced_into_an_offer(
    tmp_path: Path,
) -> None:
    db, runtime, bot = await world(tmp_path)
    try:
        theirs = await give(runtime, B)
        view = make_view(bot, runtime)
        picker = ItemPickerView(view, A, [], remove=False)
        click = interaction(A)
        select = next(c for c in picker.children if isinstance(c, discord.ui.Select))
        with (
            patch.object(type(select), "values", [str(theirs.id)], create=True),
            patch("yokai.ui.trade_views.send", new=AsyncMock()) as sent,
        ):
            await picker._picked(click)
        assert view.session.offers[A].items == []
        assert "Items changed" in (sent.await_args.kwargs["embed"].title or "")
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_removing_an_item_and_the_empty_cases(tmp_path: Path) -> None:
    db, runtime, bot = await world(tmp_path)
    try:
        mine = await give(runtime, A)
        view = make_view(bot, runtime)
        with patch("yokai.ui.trade_views.send", new=AsyncMock()) as sent:
            await view.remove_button.callback(interaction(A))
        assert "Nothing to remove" in (sent.await_args.kwargs["embed"].title or "")
        view.session.add_item(A, mine.id)
        with patch("yokai.ui.trade_views.send", new=AsyncMock()) as sent:
            await view.remove_button.callback(interaction(A))
        picker = sent.await_args.kwargs["view"]
        assert picker.remove is True
        await pick(picker, mine.id, interaction(A))
        assert view.session.offers[A].items == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_the_renown_modal_validates_and_checks_the_balance(tmp_path: Path) -> None:
    db, runtime, bot = await world(tmp_path, cash=100)
    try:
        view = make_view(bot, runtime)

        async def submit(text: str) -> Any:
            modal = RenownModal(view, A)
            modal.amount._value = text  # type: ignore[attr-defined]
            click = interaction(A)
            with patch("yokai.ui.trade_views.send", new=AsyncMock()) as sent:
                await modal.on_submit(click)
            return sent, click

        sent, _ = await submit("abc")
        assert "Invalid amount" in (sent.await_args.kwargs["embed"].title or "")
        sent, _ = await submit("-5")
        assert "Invalid amount" in (sent.await_args.kwargs["embed"].title or "")
        sent, _ = await submit("101")
        assert "Not enough Renown" in (sent.await_args.kwargs["embed"].title or "")
        assert view.session.offers[A].renown == 0
        sent, click = await submit("1,00")
        sent.assert_not_awaited()
        assert view.session.offers[A].renown == 100
        click.response.defer.assert_awaited_once()
        assert view.message.edit.await_count == 1
    finally:
        await db.close()


# ----------------------------------------------------------------------- confirming


async def confirm(view: TradeView, user: int) -> Any:
    click = interaction(user)
    with patch("yokai.ui.trade_views.send", new=AsyncMock()) as sent:
        await view.confirm_button.callback(click)
    return click, sent


@pytest.mark.asyncio
async def test_an_empty_trade_cannot_be_confirmed(tmp_path: Path) -> None:
    db, runtime, bot = await world(tmp_path)
    try:
        view = make_view(bot, runtime)
        _, sent = await confirm(view, A)
        assert "Nothing to trade" in (sent.await_args.kwargs["embed"].title or "")
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_both_confirming_swaps_everything_and_frees_the_members(tmp_path: Path) -> None:
    db, runtime, bot = await world(tmp_path)
    try:
        mine = await give(runtime, A)
        theirs = await give(runtime, B, "oni_head_t1_common")
        view = make_view(bot, runtime)
        view.session.add_item(A, mine.id)
        view.session.add_item(B, theirs.id)
        view.session.set_renown(B, 250)
        click, _ = await confirm(view, A)
        assert (
            click.response.edit_message.await_args.kwargs["embed"]
            .fields[0]
            .name.endswith("(confirmed)")
        )
        click, _ = await confirm(view, B)
        assert await owner_of(db, mine.id) == B and await owner_of(db, theirs.id) == A
        wallets = [(await db.get_wallet(u)).balance for u in (A, B)]  # type: ignore[union-attr]
        assert wallets == [1250, 750]
        final = click.edit_original_response.await_args.kwargs
        assert final["view"] is None and "Trade complete" in (final["embed"].title or "")
        assert "250" in (final["embed"].description or "")
        assert runtime.trading == {} and view.finished and view.session.closed
        click, sent = await confirm(view, A)  # the finished trade refuses more presses
        assert "Trade is closed" in (sent.await_args.kwargs["embed"].title or "")
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_changing_the_deal_after_confirming_clears_both_confirmations(
    tmp_path: Path,
) -> None:
    db, runtime, bot = await world(tmp_path)
    try:
        mine = await give(runtime, A)
        view = make_view(bot, runtime)
        view.session.add_item(A, mine.id)
        await confirm(view, A)
        assert view.session.confirmed == {A}
        modal = RenownModal(view, B)
        modal.amount._value = "10"  # type: ignore[attr-defined]
        with patch("yokai.ui.trade_views.send", new=AsyncMock()):
            await modal.on_submit(interaction(B))
        assert view.session.confirmed == set()
        click, _ = await confirm(view, B)  # B alone is not enough
        assert await owner_of(db, mine.id) == A
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_an_item_equipped_after_offering_is_dropped_and_the_trade_reopens(
    tmp_path: Path,
) -> None:
    db, runtime, bot = await world(tmp_path)
    try:
        mine = await give(runtime, A)
        view = make_view(bot, runtime)
        view.session.add_item(A, mine.id)
        view.session.set_renown(B, 50)
        await runtime.gear.equip_item(A, mine.id, level=60, items=runtime.content.items)
        await confirm(view, A)
        click, sent = await confirm(view, B)
        assert await owner_of(db, mine.id) == A
        assert view.session.offers[A].items == [] and view.session.confirmed == set()
        assert not view.finished and runtime.trading[A] is view.session
        assert "Items changed" in (sent.await_args.kwargs["embed"].title or "")
        redraw = click.edit_original_response.await_args.kwargs["embed"]
        assert "Heads up" in [f.name for f in redraw.fields]
        assert (await db.get_wallet(B)).balance == 1000  # type: ignore[union-attr]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_missing_funds_reopen_the_trade_and_name_the_member(tmp_path: Path) -> None:
    db, runtime, bot = await world(tmp_path)
    try:
        view = make_view(bot, runtime)
        view.session.set_renown(A, 900)
        async with db.transaction() as conn:
            await conn.execute("UPDATE wallets SET balance = 10 WHERE user_id = ?;", (A,))
        await confirm(view, A)
        click, sent = await confirm(view, B)
        assert "Not enough Renown" in (sent.await_args.kwargs["embed"].title or "")
        assert "<@5>" in (sent.await_args.kwargs["embed"].description or "")
        assert not view.finished and view.session.confirmed == set()
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_fight_blocks_every_action_and_the_commit(tmp_path: Path) -> None:
    db, runtime, bot = await world(tmp_path)
    try:
        mine = await give(runtime, A)
        view = make_view(bot, runtime)
        view.session.add_item(A, mine.id)
        await confirm(view, A)
        runtime.active_users.add(B)
        for button in (view.add_button, view.remove_button, view.renown_button):
            with patch("yokai.ui.trade_views.send", new=AsyncMock()) as sent:
                await button.callback(interaction(A))
            assert "Trading is locked" in (sent.await_args.kwargs["embed"].title or "")
        click, sent = await confirm(view, B)
        assert "Trading is locked" in (sent.await_args.kwargs["embed"].title or "")
        assert await owner_of(db, mine.id) == A
    finally:
        await db.close()


# ------------------------------------------------------------- cancel, timeout, strangers


@pytest.mark.asyncio
async def test_either_side_can_cancel_and_nothing_moves(tmp_path: Path) -> None:
    db, runtime, bot = await world(tmp_path)
    try:
        mine = await give(runtime, A)
        view = make_view(bot, runtime)
        view.session.add_item(A, mine.id)
        click = interaction(B)
        await view.cancel_button.callback(click)
        final = click.response.edit_message.await_args.kwargs
        assert "Trade cancelled" in (final["embed"].title or "") and final["view"] is None
        assert runtime.trading == {} and await owner_of(db, mine.id) == A
        again = interaction(A)
        await view.cancel_button.callback(again)
        again.response.defer.assert_awaited_once()
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_an_idle_trade_expires_and_frees_both_members(tmp_path: Path) -> None:
    db, runtime, bot = await world(tmp_path)
    try:
        view = make_view(bot, runtime)
        await view.on_timeout()
        edit = view.message.edit.await_args.kwargs
        assert "Trade expired" in (edit["embed"].title or "") and edit["view"] is None
        assert runtime.trading == {}
        view.message.edit.reset_mock()
        await view.on_timeout()
        view.message.edit.assert_not_awaited()
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_strangers_cannot_press_the_trade_buttons(tmp_path: Path) -> None:
    db, runtime, bot = await world(tmp_path)
    try:
        view = make_view(bot, runtime)
        with patch("yokai.moderation.gate.screen", new=AsyncMock(return_value=False)):
            assert await view.interaction_check(interaction(A)) is True
            stranger = interaction(99)
            assert await view.interaction_check(stranger) is False
        stranger.response.send_message.assert_awaited_once()
        picker = ItemPickerView(view, A, [], remove=False)
        with patch("yokai.moderation.gate.screen", new=AsyncMock(return_value=False)):
            assert await picker.interaction_check(interaction(B)) is False
    finally:
        await db.close()


# ---------------------------------------------------------------------------- embeds


def test_the_trade_embed_obeys_the_layout_rules() -> None:
    lines = [f"`#{n}` **{'Long Name ' * 3}**" for n in range(6)]
    embed = EmbedFactory.trade_session(
        [("A" * 50, "<@1>", lines, 1_000_000, True), ("Ben", "<@2>", [], 0, False)],
        1_900_000_000,
        False,
        note="Something changed",
    )
    assert emoji_count(embed) == 1 and total_embed_length(embed) <= 6000
    assert embed.fields[0].name.endswith("(confirmed)") and "Nothing yet" in embed.fields[1].value
    assert "a gift" in (EmbedFactory.trade_session([], 1, True).description or "")
