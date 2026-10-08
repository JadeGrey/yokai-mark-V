"""The lottery channel's Buy tickets button and form."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

import aiosqlite
import discord

from yokai.errors import (
    ChannelNotConfiguredError,
    InsufficientFundsError,
    LotteryClosedError,
    YokaiError,
)
from yokai.gambling.engines.lottery import plan_tickets
from yokai.gambling.rng import new_rng
from yokai.moderation.gate import screen
from yokai.ui.embeds import EmbedFactory, send
from yokai.ui.table_common import avatar_url, reply_error

if TYPE_CHECKING:
    from yokai.bot import YokaiBot

logger = logging.getLogger(__name__)

BUY_CUSTOM_ID = "yokai:lottery:buy"


class TicketsModal(discord.ui.Modal):
    """Hand-picked numbers and/or a quick-pick count."""

    numbers = discord.ui.TextInput(
        label="Numbers (e.g. 3, 7, 19)",
        placeholder="Leave blank for quick picks only",
        required=False,
        max_length=100,
    )
    quick = discord.ui.TextInput(
        label="Quick picks (how many)",
        placeholder="Random free numbers, e.g. 3",
        required=False,
        max_length=3,
    )

    def __init__(self, bot: YokaiBot) -> None:
        super().__init__(title="Buy lottery tickets", timeout=300)
        self.bot = bot

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return not await screen(interaction)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        # thinking=True makes this a private reply of its own; a plain defer on a form opened
        # from a button silently updates the button's message and ignores `ephemeral`.
        await interaction.response.defer(ephemeral=True, thinking=True)
        config = self.bot.config
        user_id = interaction.user.id
        try:
            held = await self.bot.db.user_lottery_numbers(user_id)
            numbers = plan_tickets(
                held,
                self.numbers.value,
                self.quick.value,
                config.lottery_number_range,
                config.lottery_max_tickets_per_user,
                new_rng(),
            )
            purchase = await self.bot.db.buy_lottery_tickets(
                user_id,
                numbers,
                price=config.lottery_ticket_price,
                max_per_user=config.lottery_max_tickets_per_user,
                welcome_bonus=config.economy_welcome_bonus,
            )
            state = await self.bot.db.get_lottery_state()
        except YokaiError as err:
            await reply_error(self.bot, interaction, err)
            return
        except aiosqlite.Error as exc:
            logger.error("Buying lottery tickets failed: %s", exc, exc_info=exc)
            await reply_error(
                self.bot,
                interaction,
                YokaiError(
                    "Something went wrong on my end, and nothing was charged.",
                    user_hint="Try again in a moment.",
                ),
            )
            return
        embed = EmbedFactory.lottery_receipt(
            purchase.numbers,
            purchase.cost,
            purchase.pot,
            purchase.new_balance,
            state.next_draw_ts,
            bot_avatar_url=avatar_url(self.bot),
        )
        await send(interaction, embed=embed, ephemeral=True)
        panel = self.bot.get_cog("Lottery")
        if panel is not None:
            try:
                await panel.update_panel()  # type: ignore[attr-defined]
            except (discord.HTTPException, aiosqlite.Error) as exc:
                logger.warning("Could not update the lottery panel: %s", exc)


async def handle_buy(bot: YokaiBot, interaction: discord.Interaction) -> None:
    """Buy tickets was pressed: check the channel, the sales window and the balance first."""
    channel_id = interaction.channel_id
    try:
        row = await bot.db.get_multiplayer_channel(channel_id) if channel_id else None
        if row is None or row.game != "lottery":
            raise ChannelNotConfiguredError()
        state = await bot.db.get_lottery_state()
        if state.next_draw_ts is None or int(time.time()) >= state.next_draw_ts:
            raise LotteryClosedError()
        price = bot.config.lottery_ticket_price
        wallet = await bot.db.ensure_wallet(interaction.user.id, bot.config.economy_welcome_bonus)
        if wallet.balance < price:
            raise InsufficientFundsError(
                balance=wallet.balance, amount=price, action="buy a ticket for"
            )
    except YokaiError as err:
        await reply_error(bot, interaction, err)
        return
    await interaction.response.send_modal(TicketsModal(bot))


class BuyView(discord.ui.View):
    """The always-on Buy tickets button under the lottery panel."""

    def __init__(self, bot: YokaiBot) -> None:
        super().__init__(timeout=None)
        self.bot = bot

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return not await screen(interaction)

    @discord.ui.button(
        label="Buy tickets", style=discord.ButtonStyle.success, custom_id=BUY_CUSTOM_ID
    )
    async def buy_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await handle_buy(self.bot, interaction)
