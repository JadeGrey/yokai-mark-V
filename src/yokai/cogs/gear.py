"""Gear slash commands: /gear inventory, equip, unequip, repair, shop, buy and sell."""

from __future__ import annotations

import logging
import random
import time
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

from yokai.challenges.periods import load_tz
from yokai.errors import (
    GearLockedError,
    GearNotFoundError,
    NoCharacterError,
    NotInStockError,
    YokaiError,
)
from yokai.rpg import gear, guild
from yokai.rpg.gear import compute_loadout, durability_percent, format_item_stats
from yokai.rpg.gear_store import ItemDrop
from yokai.rpg.models import SLOTS, Character, ItemDef, ItemInstance
from yokai.rpg.runtime import RpgRuntime
from yokai.theme import SEP
from yokai.ui.embeds import EmbedFactory, send
from yokai.ui.rpg_views import InventoryPaginator

if TYPE_CHECKING:
    from yokai.bot import YokaiBot

logger = logging.getLogger(__name__)

MAX_AUTOCOMPLETE = 25
SLOT_CHOICES = [app_commands.Choice(name=slot.title(), value=slot) for slot in SLOTS]


def _parse_item_id(text: str) -> int:
    """An item id typed or chosen from autocomplete ('12' or '#12'). Raises GearNotFoundError."""
    cleaned = text.strip().lstrip("#")
    if not cleaned.isdigit() or len(cleaned) > 12:
        raise GearNotFoundError()
    return int(cleaned)


class GearCog(commands.Cog, name="Gear"):
    """Equipment: wear it, repair it, buy it from the guild shop, sell what you don't need."""

    gear_group = app_commands.Group(name="gear", description="Your equipment and the guild shop.")

    def __init__(self, bot: YokaiBot) -> None:
        self.bot = bot
        self._rng = random.Random()

    # ------------------------------------------------------------------ helpers

    def _avatar_url(self) -> Optional[str]:
        return str(self.bot.user.display_avatar.url) if self.bot.user else None

    def _runtime(self) -> RpgRuntime:
        runtime = self.bot.rpg
        if runtime is None:
            raise RuntimeError("The Gear cog was loaded without an RPG runtime.")
        return runtime

    def _day(self) -> tuple[str, int]:
        tz = load_tz(self.bot.config.rpg_tz)
        now = int(time.time())
        return guild.day_key(now, tz), guild.next_day_ts(now, tz)

    async def _send_domain_error(self, interaction: discord.Interaction, err: Exception) -> None:
        embed = EmbedFactory.error(
            message=getattr(err, "message", str(err)),
            title=getattr(err, "title", "That didn't work"),
            user_hint=getattr(err, "user_hint", None),
            context="Gear",
            bot_avatar_url=self._avatar_url(),
        )
        await send(interaction, embed=embed, ephemeral=True)

    async def _character(
        self, interaction: discord.Interaction, runtime: RpgRuntime
    ) -> Optional[Character]:
        character = await runtime.store.get_character(interaction.user.id)
        if character is None:
            await self._send_domain_error(interaction, NoCharacterError(own=True))
        return character

    async def _locked(self, interaction: discord.Interaction, runtime: RpgRuntime) -> bool:
        """Gear cannot be changed mid-fight; True means the member was told so."""
        if interaction.user.id in runtime.active_users:
            await self._send_domain_error(interaction, GearLockedError())
            return True
        return False

    def _definition(self, runtime: RpgRuntime, item: ItemInstance) -> Optional[ItemDef]:
        return runtime.content.items.get(item.content_id)

    # ------------------------------------------------------------------ /gear inventory

    @gear_group.command(name="inventory", description="See the gear you own.")
    @app_commands.describe(page="Page to open (defaults to 1)")
    async def inventory(self, interaction: discord.Interaction, page: Optional[int] = None) -> None:
        runtime = self._runtime()
        character = await self._character(interaction, runtime)
        if character is None:
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        view = InventoryPaginator(
            self.bot, runtime, interaction.user.id, character.level, page or 1
        )
        embed = await view.build_embed()
        view.message = await send(interaction, embed=embed, view=view, ephemeral=True)

    # ------------------------------------------------------------------ /gear equip

    @gear_group.command(name="equip", description="Wear a piece of gear.")
    @app_commands.describe(item="Which item to wear")
    async def equip(self, interaction: discord.Interaction, item: str) -> None:
        runtime = self._runtime()
        character = await self._character(interaction, runtime)
        if character is None or await self._locked(interaction, runtime):
            return
        try:
            item_id = _parse_item_id(item)
            worn = await runtime.gear.equip_item(
                interaction.user.id,
                item_id,
                level=character.level,
                items=runtime.content.items,
            )
        except YokaiError as err:
            await self._send_domain_error(interaction, err)
            return
        definition = runtime.content.items[worn.content_id]
        loadout = compute_loadout(
            runtime.content, character.level, await runtime.gear.equipped_items(interaction.user.id)
        )
        lines = [f"**{definition.name}**{SEP}{format_item_stats(worn.rolls)}"]
        lines.extend(loadout.bonus_lines)
        if worn.durability <= 0:
            lines.append("It is broken, so it counts for nothing until repaired.")
        embed = EmbedFactory.success(
            title=f"Equipped in the {definition.slot} slot",
            description="\n".join(lines),
            context="Gear",
            bot_avatar_url=self._avatar_url(),
        )
        await send(interaction, embed=embed, ephemeral=True)

    @equip.autocomplete("item")
    async def equip_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        runtime = self._runtime()
        character = await runtime.store.get_character(interaction.user.id)
        if character is None:
            return []
        wearable = []
        for item in await runtime.gear.list_items(interaction.user.id):
            definition = self._definition(runtime, item)
            if item.equipped_slot or definition is None or definition.req_level > character.level:
                continue
            wearable.append((item, definition))
        return self._item_choices(wearable, current)

    def _item_choices(
        self, pairs: list[tuple[ItemInstance, ItemDef]], current: str
    ) -> list[app_commands.Choice[str]]:
        needle = current.strip().casefold().lstrip("#")
        choices: list[app_commands.Choice[str]] = []
        for item, definition in sorted(pairs, key=lambda p: -p[0].id):
            if needle and needle not in definition.name.casefold() and needle != str(item.id):
                continue
            percent = durability_percent(item.durability, definition.max_durability)
            label = f"#{item.id} {definition.name} ({definition.slot}, {percent}%)"
            choices.append(app_commands.Choice(name=label[:100], value=str(item.id)))
        return choices[:MAX_AUTOCOMPLETE]

    # ------------------------------------------------------------------ /gear unequip

    @gear_group.command(name="unequip", description="Take off the gear in a slot.")
    @app_commands.describe(slot="Which slot to empty")
    @app_commands.choices(slot=SLOT_CHOICES)
    async def unequip(
        self, interaction: discord.Interaction, slot: app_commands.Choice[str]
    ) -> None:
        runtime = self._runtime()
        if await self._character(interaction, runtime) is None:
            return
        if await self._locked(interaction, runtime):
            return
        removed = await runtime.gear.unequip_slot(interaction.user.id, slot.value)
        build = EmbedFactory.success if removed else EmbedFactory.info
        embed = build(
            title="Slot emptied" if removed else "Nothing to remove",
            description=(
                f"Your {slot.value} slot is empty now."
                if removed
                else f"You aren't wearing anything in the {slot.value} slot."
            ),
            context="Gear",
            bot_avatar_url=self._avatar_url(),
        )
        await send(interaction, embed=embed, ephemeral=True)

    # ------------------------------------------------------------------ /gear repair

    @gear_group.command(name="repair", description="Repair damaged gear for Renown.")
    @app_commands.describe(item="One item to repair (leave empty to repair everything)")
    async def repair(self, interaction: discord.Interaction, item: Optional[str] = None) -> None:
        runtime = self._runtime()
        if await self._character(interaction, runtime) is None:
            return
        if await self._locked(interaction, runtime):
            return
        try:
            ids = None if not item else [_parse_item_id(item)]
            result = await runtime.gear.repair_items(
                interaction.user.id,
                ids,
                items=runtime.content.items,
                rules=runtime.content.balance.gear,
                welcome_bonus=self.bot.config.economy_welcome_bonus,
            )
        except YokaiError as err:
            await self._send_domain_error(interaction, err)
            return
        embed = EmbedFactory.success(
            title="Gear repaired",
            description=f"Repaired **{result.repaired}** item(s) for **{result.cost:,}** Renown.",
            context="Gear",
            bot_avatar_url=self._avatar_url(),
        )
        await send(interaction, embed=embed, ephemeral=True)

    @repair.autocomplete("item")
    async def repair_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        runtime = self._runtime()
        damaged = []
        for item in await runtime.gear.list_items(interaction.user.id):
            definition = self._definition(runtime, item)
            if definition is not None and item.durability < definition.max_durability:
                damaged.append((item, definition))
        return self._item_choices(damaged, current)

    # ------------------------------------------------------------------ /gear sell

    @gear_group.command(name="sell", description="Sell gear you don't need to the guild.")
    @app_commands.describe(item="Which item to sell")
    async def sell(self, interaction: discord.Interaction, item: str) -> None:
        runtime = self._runtime()
        if await self._character(interaction, runtime) is None:
            return
        if await self._locked(interaction, runtime):
            return
        try:
            item_id = _parse_item_id(item)
            owned = await runtime.gear.get_item(item_id)
            name = None
            if owned is not None and owned.owner_id == interaction.user.id:
                definition = self._definition(runtime, owned)
                name = definition.name if definition else owned.content_id
            value = await runtime.gear.sell_item(
                interaction.user.id,
                item_id,
                items=runtime.content.items,
                rules=runtime.content.balance.gear,
                welcome_bonus=self.bot.config.economy_welcome_bonus,
            )
        except YokaiError as err:
            await self._send_domain_error(interaction, err)
            return
        embed = EmbedFactory.success(
            title="Sold",
            description=f"The guild bought **{name}** for **{value:,}** Renown.",
            context="Gear",
            bot_avatar_url=self._avatar_url(),
        )
        await send(interaction, embed=embed, ephemeral=True)

    @sell.autocomplete("item")
    async def sell_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        runtime = self._runtime()
        spare = []
        for item in await runtime.gear.list_items(interaction.user.id):
            definition = self._definition(runtime, item)
            if definition is not None and item.equipped_slot is None:
                spare.append((item, definition))
        return self._item_choices(spare, current)

    # ------------------------------------------------------------------ /gear shop and buy

    async def _stock(self, runtime: RpgRuntime, day: str) -> list[ItemDef]:
        content = runtime.content
        ids = await runtime.gear.get_or_create_shop(day, lambda: gear.draw_shop(content, day))
        return [
            content.items[i] for i in ids if i in content.items and not content.items[i].retired
        ]

    @gear_group.command(name="shop", description="See today's guild shop.")
    async def shop(self, interaction: discord.Interaction) -> None:
        runtime = self._runtime()
        if await self._character(interaction, runtime) is None:
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        day, resets_ts = self._day()
        stock = await self._stock(runtime, day)
        tiers: dict[int, list[tuple[str, str, str, str, int, str]]] = {}
        for item in stock:
            tags = runtime.content.tags
            tiers.setdefault(item.req_level, []).append(
                (
                    item.name,
                    tags[item.tag].name if item.tag in tags else item.tag,
                    item.slot,
                    item.rarity,
                    item.price,
                    f"{tags[item.tag].name if item.tag in tags else item.tag}: "
                    f"{format_item_stats(item.stats)}",
                )
            )
        wallet = await self.bot.db.get_wallet(interaction.user.id)
        cap = self.bot.config.rpg_shop_daily_cap
        bought = await runtime.gear.purchases_today(interaction.user.id, day)
        embed = EmbedFactory.shop_panel(
            sorted(tiers.items()),
            wallet.balance if wallet else 0,
            max(0, cap - bought),
            cap,
            resets_ts,
            bot_avatar_url=self._avatar_url(),
        )
        await send(interaction, embed=embed, ephemeral=True)

    @gear_group.command(name="buy", description="Buy an item from today's guild shop.")
    @app_commands.describe(item="Which item to buy")
    async def buy(self, interaction: discord.Interaction, item: str) -> None:
        runtime = self._runtime()
        if await self._character(interaction, runtime) is None:
            return
        day, _ = self._day()
        stock = {i.id: i for i in await self._stock(runtime, day)}
        chosen = stock.get(item.strip())
        if chosen is None:
            await self._send_domain_error(interaction, NotInStockError())
            return
        rules = runtime.content.balance.gear
        drop = ItemDrop(chosen.id, gear.roll_stats(chosen, rules, self._rng), chosen.max_durability)
        try:
            bought = await runtime.gear.buy_shop_item(
                interaction.user.id,
                drop,
                price=chosen.price,
                day=day,
                daily_cap=self.bot.config.rpg_shop_daily_cap,
                inventory_cap=rules.inventory_cap,
                welcome_bonus=self.bot.config.economy_welcome_bonus,
            )
        except YokaiError as err:
            await self._send_domain_error(interaction, err)
            return
        embed = EmbedFactory.success(
            title="Purchased",
            description=(
                f"Bought **{chosen.name}** for **{chosen.price:,}** Renown.\n"
                f"{format_item_stats(bought.rolls)}{SEP}needs level {chosen.req_level}\n"
                f"Wear it with /gear equip."
            ),
            context="Gear",
            bot_avatar_url=self._avatar_url(),
        )
        await send(interaction, embed=embed, ephemeral=True)

    @buy.autocomplete("item")
    async def buy_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        runtime = self._runtime()
        day, _ = self._day()
        needle = current.strip().casefold()
        choices = []
        for item in await self._stock(runtime, day):
            if needle and needle not in item.name.casefold():
                continue
            label = f"{item.name} ({item.slot}, lvl {item.req_level}) - {item.price:,}"
            choices.append(app_commands.Choice(name=label[:100], value=item.id))
        return choices[:MAX_AUTOCOMPLETE]


async def setup(bot: YokaiBot) -> None:
    """Extension setup entrypoint."""
    await bot.add_cog(GearCog(bot))
