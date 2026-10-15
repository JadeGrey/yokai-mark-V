"""The trade message and its private pickers: items, Renown and confirmation."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Optional

import discord

from yokai.errors import (
    TradeClosedError,
    TradeFightingError,
    TradeFundsError,
    TradeItemsGoneError,
    TradeNothingError,
    TradeRenownError,
    YokaiError,
)
from yokai.rpg.gear import durability_percent
from yokai.rpg.models import RARITIES, ItemInstance
from yokai.rpg.runtime import RpgRuntime
from yokai.rpg.trade import SESSION_SECONDS, TradeSession
from yokai.theme import SEP
from yokai.ui.embeds import EmbedFactory, send
from yokai.ui.table_common import avatar_url
from yokai.ui.views import BaseView

if TYPE_CHECKING:
    from yokai.bot import YokaiBot

logger = logging.getLogger(__name__)

PICKER_TIMEOUT = 90.0
PAGE_SIZE = 25


def item_line(runtime: RpgRuntime, item: ItemInstance) -> str:
    """ "`#12` **Name** · rarity · lvl 12 · 85%" for an item in an offer or a picker."""
    definition = runtime.content.items.get(item.content_id)
    if definition is None:
        return f"`#{item.id}` retired item"
    condition = (
        "broken"
        if item.durability <= 0
        else f"{durability_percent(item.durability, definition.max_durability)}%"
    )
    return (
        f"`#{item.id}` **{definition.name}**{SEP}{definition.rarity}"
        f"{SEP}lvl {definition.req_level}{SEP}{condition}"
    )


class TradeView(BaseView):
    """The public trade message. Only the two members may press its buttons."""

    def __init__(
        self,
        bot: YokaiBot,
        runtime: RpgRuntime,
        session: TradeSession,
        names: dict[int, str],
        *,
        timeout: float = SESSION_SECONDS,
    ) -> None:
        super().__init__(allowed_user_ids=list(session.members), timeout=timeout)
        self.bot = bot
        self.runtime = runtime
        self.session = session
        self.names = names
        self.note: Optional[str] = None
        self.finished = False
        self._commit_lock = asyncio.Lock()

    # ------------------------------------------------------------------ rendering

    async def build_embed(self) -> discord.Embed:
        sides = []
        for user_id in self.session.members:
            offer = self.session.offers[user_id]
            lines = []
            for item_id in offer.items:
                item = await self.runtime.gear.get_item(item_id)
                lines.append(item_line(self.runtime, item) if item else f"`#{item_id}` gone")
            sides.append(
                (
                    self.names.get(user_id, f"Adventurer {user_id % 10000}"),
                    f"<@{user_id}>",
                    lines,
                    offer.renown,
                    user_id in self.session.confirmed,
                )
            )
        return EmbedFactory.trade_session(
            sides,
            self.session.expires_ts,
            self.session.is_gift,
            note=self.note,
            bot_avatar_url=avatar_url(self.bot),
        )

    async def refresh(self) -> None:
        """Redraw the public message after an offer changed (from a picker or the modal)."""
        if self.message is None or self.finished:
            return
        try:
            await self.message.edit(embed=await self.build_embed(), view=self)
        except discord.HTTPException as exc:
            logger.debug("Could not redraw the trade message: %s", exc)

    async def _fail(self, interaction: discord.Interaction, err: YokaiError) -> None:
        embed = EmbedFactory.error(
            message=err.message,
            title=getattr(err, "title", "That didn't work"),
            user_hint=getattr(err, "user_hint", None),
            context="Guild",
            bot_avatar_url=avatar_url(self.bot),
        )
        await send(interaction, embed=embed, ephemeral=True)

    def check_open(self, user_id: int) -> None:
        """Raise unless the trade is open and neither member is in a guild fight."""
        if self.finished or self.session.closed:
            raise TradeClosedError()
        for member in self.session.members:
            if member in self.runtime.active_users:
                raise TradeFightingError(member, you=member == user_id)

    def release(self) -> None:
        """Close the session and free both members to open another trade."""
        self.finished = True
        self.session.close()
        for member in self.session.members:
            if self.runtime.trading.get(member) is self.session:
                del self.runtime.trading[member]
        self.stop()

    # ------------------------------------------------------------------ buttons

    @discord.ui.button(label="Add item", style=discord.ButtonStyle.primary)
    async def add_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        try:
            self.check_open(interaction.user.id)
            owned = await self.runtime.gear.list_items(interaction.user.id)
        except YokaiError as err:
            await self._fail(interaction, err)
            return
        taken = self.session.offered_item_ids()
        candidates = [i for i in owned if i.equipped_slot is None and i.id not in taken]
        candidates.sort(key=lambda i: (-self._rank(i), -self._req(i), -i.id))
        if not candidates:
            info = EmbedFactory.info(
                title="Nothing to offer",
                description="You have no unequipped gear that isn't already in this trade.",
                context="Guild",
                bot_avatar_url=avatar_url(self.bot),
            )
            await send(interaction, embed=info, ephemeral=True)
            return
        picker = ItemPickerView(self, interaction.user.id, candidates, remove=False)
        await send(interaction, embed=picker.prompt(), view=picker, ephemeral=True)

    @discord.ui.button(label="Remove item", style=discord.ButtonStyle.secondary)
    async def remove_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        try:
            self.check_open(interaction.user.id)
        except YokaiError as err:
            await self._fail(interaction, err)
            return
        mine = []
        for item_id in self.session.offers[interaction.user.id].items:
            item = await self.runtime.gear.get_item(item_id)
            if item is not None:
                mine.append(item)
        if not mine:
            info = EmbedFactory.info(
                title="Nothing to remove",
                description="You haven't put any items in this trade.",
                context="Guild",
                bot_avatar_url=avatar_url(self.bot),
            )
            await send(interaction, embed=info, ephemeral=True)
            return
        picker = ItemPickerView(self, interaction.user.id, mine, remove=True)
        await send(interaction, embed=picker.prompt(), view=picker, ephemeral=True)

    @discord.ui.button(label="Set Renown", style=discord.ButtonStyle.secondary)
    async def renown_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        try:
            self.check_open(interaction.user.id)
        except YokaiError as err:
            await self._fail(interaction, err)
            return
        await interaction.response.send_modal(RenownModal(self, interaction.user.id))

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success)
    async def confirm_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        async with self._commit_lock:
            try:
                self.check_open(interaction.user.id)
                both = self.session.confirm(interaction.user.id)
            except YokaiError as err:
                await self._fail(interaction, err)
                return
            if not both:
                self.note = None
                await interaction.response.edit_message(embed=await self.build_embed(), view=self)
                return
            await interaction.response.defer()
            await self._commit(interaction)

    async def _commit(self, interaction: discord.Interaction) -> None:
        a, b = self.session.members
        offer_a, offer_b = self.session.offers[a], self.session.offers[b]
        try:
            result = await self.runtime.gear.commit_trade(
                user_a=a,
                user_b=b,
                items_a=offer_a.items,
                items_b=offer_b.items,
                renown_a=offer_a.renown,
                renown_b=offer_b.renown,
                inventory_cap=self.runtime.content.balance.gear.inventory_cap,
                welcome_bonus=self.bot.config.economy_welcome_bonus,
                fighting=self.runtime.active_users,
            )
        except YokaiError as err:
            if isinstance(err, TradeItemsGoneError):
                self.session.drop_items(err.item_ids)
            self.session.clear_confirmations()
            self.note = err.message
            await interaction.edit_original_response(embed=await self.build_embed(), view=self)
            await self._fail(interaction, err)
            return
        self.release()
        received = {
            a: [item_line(self.runtime, i) for i in result.to_a],
            b: [item_line(self.runtime, i) for i in result.to_b],
        }
        renown = {a: offer_b.renown, b: offer_a.renown}
        lines = []
        for user_id in (a, b):
            got = received[user_id] + (
                [f"**{renown[user_id]:,}** Renown"] if renown[user_id] else []
            )
            lines.append(f"<@{user_id}> receives:\n" + ("\n".join(got) if got else "nothing"))
        done = EmbedFactory.success(
            title="Trade complete",
            description="\n\n".join(lines),
            context="Guild",
            bot_avatar_url=avatar_url(self.bot),
        )
        await interaction.edit_original_response(embed=done, view=None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if self.finished:
            await interaction.response.defer()
            return
        self.release()
        embed = EmbedFactory.info(
            title="Trade cancelled",
            description=f"<@{interaction.user.id}> cancelled the trade. Nothing moved.",
            context="Guild",
            bot_avatar_url=avatar_url(self.bot),
        )
        await interaction.response.edit_message(embed=embed, view=None)

    async def on_timeout(self) -> None:
        if self.finished:
            return
        self.release()
        if self.message is None:
            return
        embed = EmbedFactory.info(
            title="Trade expired",
            description="Nobody finished the trade in time. Nothing moved.",
            context="Guild",
            bot_avatar_url=avatar_url(self.bot),
        )
        try:
            await self.message.edit(embed=embed, view=None)
        except discord.HTTPException as exc:
            logger.debug("Could not close the expired trade: %s", exc)

    # ------------------------------------------------------------------ helpers

    def _rank(self, item: ItemInstance) -> int:
        definition = self.runtime.content.items.get(item.content_id)
        return RARITIES.index(definition.rarity) if definition else -1

    def _req(self, item: ItemInstance) -> int:
        definition = self.runtime.content.items.get(item.content_id)
        return definition.req_level if definition else 0


class ItemPickerView(BaseView):
    """A private, paged list of items to add to (or remove from) the member's offer."""

    def __init__(
        self,
        trade: TradeView,
        user_id: int,
        items: list[ItemInstance],
        *,
        remove: bool,
    ) -> None:
        super().__init__(allowed_user_ids=[user_id], timeout=PICKER_TIMEOUT)
        self.trade = trade
        self.user_id = user_id
        self.items = items
        self.remove = remove
        self.page = 1
        self.total_pages = max(1, -(-len(items) // PAGE_SIZE))
        self._rebuild()

    def prompt(self) -> discord.Embed:
        verb = "take out of" if self.remove else "add to"
        return EmbedFactory.info(
            title="Remove an item" if self.remove else "Add an item",
            description=(
                f"Choose an item to {verb} the trade (page {self.page}/{self.total_pages})."
            ),
            context="Guild",
            bot_avatar_url=avatar_url(self.trade.bot),
        )

    def _rebuild(self) -> None:
        for child in list(self.children):
            if isinstance(child, (discord.ui.Select, discord.ui.Button)):
                self.remove_item(child)
        start = (self.page - 1) * PAGE_SIZE
        options = []
        for item in self.items[start : start + PAGE_SIZE]:
            definition = self.trade.runtime.content.items.get(item.content_id)
            name = definition.name if definition else item.content_id
            detail = f"{definition.rarity} lvl {definition.req_level}" if definition else "retired"
            options.append(
                discord.SelectOption(
                    label=f"#{item.id} {name}"[:100], description=detail, value=str(item.id)
                )
            )
        select = discord.ui.Select(placeholder="Pick an item", options=options)
        select.callback = self._picked  # type: ignore[method-assign]
        self.add_item(select)
        if self.total_pages > 1:
            prev_button = discord.ui.Button(label="Prev", disabled=self.page <= 1)
            next_button = discord.ui.Button(label="Next", disabled=self.page >= self.total_pages)
            prev_button.callback = self._prev  # type: ignore[method-assign]
            next_button.callback = self._next  # type: ignore[method-assign]
            self.add_item(prev_button)
            self.add_item(next_button)

    async def _prev(self, interaction: discord.Interaction) -> None:
        self.page = max(1, self.page - 1)
        self._rebuild()
        await interaction.response.edit_message(embed=self.prompt(), view=self)

    async def _next(self, interaction: discord.Interaction) -> None:
        self.page = min(self.total_pages, self.page + 1)
        self._rebuild()
        await interaction.response.edit_message(embed=self.prompt(), view=self)

    async def _picked(self, interaction: discord.Interaction) -> None:
        select = next(c for c in self.children if isinstance(c, discord.ui.Select))
        item_id = int(select.values[0])
        try:
            self.trade.check_open(self.user_id)
            if self.remove:
                changed = self.trade.session.remove_item(self.user_id, item_id)
            else:
                item = await self.trade.runtime.gear.get_item(item_id)
                if item is None or item.owner_id != self.user_id or item.equipped_slot:
                    raise TradeItemsGoneError([item_id])
                changed = self.trade.session.add_item(self.user_id, item_id)
        except YokaiError as err:
            await self.trade._fail(interaction, err)
            return
        self.trade.note = None
        text = "Updated your offer." if changed else "That item was already handled."
        done = EmbedFactory.success(
            title="Offer updated",
            description=text,
            context="Guild",
            bot_avatar_url=avatar_url(self.trade.bot),
        )
        await interaction.response.edit_message(embed=done, view=None)
        self.stop()
        await self.trade.refresh()


class RenownModal(discord.ui.Modal, title="Offer Renown"):
    """Type how much Renown to put in your side of the trade (0 takes it back out)."""

    amount: discord.ui.TextInput = discord.ui.TextInput(
        label="Renown to offer (0 to remove)", max_length=12, required=True
    )

    def __init__(self, trade: TradeView, user_id: int) -> None:
        super().__init__()
        self.trade = trade
        self.user_id = user_id
        self.amount.default = str(trade.session.offers[user_id].renown)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        text = str(self.amount.value).replace(",", "").replace("_", "").strip()
        try:
            self.trade.check_open(self.user_id)
            try:
                value = int(text)
            except ValueError:
                raise TradeRenownError() from None
            if value < 0:
                raise TradeRenownError()
            wallet = await self.trade.bot.db.get_wallet(self.user_id)
            balance = wallet.balance if wallet else 0
            if value > balance:
                raise TradeFundsError(self.user_id, value, balance)
            self.trade.session.set_renown(self.user_id, value)
        except (TradeNothingError, YokaiError) as err:
            await self.trade._fail(interaction, err)
            return
        self.trade.note = None
        await interaction.response.defer()
        await self.trade.refresh()
