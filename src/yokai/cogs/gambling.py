"""Gambling slash commands: slots, roulette, blackjack, and personal gambling stats."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

from yokai.errors import InsufficientFundsError, InvalidGameChoiceError, YokaiError
from yokai.gambling.engines import roulette as roulette_engine
from yokai.gambling.engines import slots as slots_engine
from yokai.gambling.escrow import escrowed_bet, validate_bet
from yokai.gambling.rng import new_rng
from yokai.theme import Emoji
from yokai.ui.embeds import EmbedFactory, send
from yokai.ui.game_views import SlotsView
from yokai.ui.views import BaseView

if TYPE_CHECKING:
    from yokai.bot import YokaiBot

# Pause between the "spinning" message and the result; one edit keeps us far from rate limits.
SPIN_DELAY_S = 1.2

ROULETTE_CHOICES = [
    app_commands.Choice(name=name, value=name)
    for name in ("red", "black", "odd", "even", "low", "high", "dozen", "column", "straight")
]


class GamblingCog(commands.Cog, name="Gambling"):
    """Solo casino games that spend and win Renown."""

    def __init__(self, bot: YokaiBot) -> None:
        self.bot = bot

    def _avatar_url(self) -> Optional[str]:
        return str(self.bot.user.display_avatar.url) if self.bot.user else None

    def _welcome(self) -> int:
        return self.bot.config.economy_welcome_bonus

    async def _send_domain_error(self, interaction: discord.Interaction, err: Exception) -> None:
        embed = EmbedFactory.error(
            message=getattr(err, "message", str(err)),
            title=getattr(err, "title", "That didn't work"),
            user_hint=getattr(err, "user_hint", None),
            bot_avatar_url=self._avatar_url(),
        )
        await send(interaction, embed=embed, ephemeral=True)

    async def _begin(self, interaction: discord.Interaction, bet: int) -> bool:
        """Validate range, balance and one-game-at-a-time, then claim the player's game slot.

        Runs before any defer, so every rejection is ephemeral. On success the caller owns the
        claim and must release it. Returns False after replying with an error.
        """
        config = self.bot.config
        user_id = interaction.user.id
        try:
            validate_bet(bet, config.gambling_min_bet, config.gambling_max_bet)
            wallet = await self.bot.db.ensure_wallet(user_id, self._welcome())
            if wallet.balance < bet:
                raise InsufficientFundsError(balance=wallet.balance, amount=bet, action="bet")
            self.bot.active_games.claim(user_id)
        except YokaiError as err:
            await self._send_domain_error(interaction, err)
            return False
        return True

    async def _defer(self, interaction: discord.Interaction) -> None:
        # A button replay has already answered the interaction, so only defer when it has not.
        if not interaction.response.is_done():
            await interaction.response.defer(thinking=True)

    async def _reveal(
        self,
        interaction: discord.Interaction,
        pending: Optional[discord.Message],
        embed: discord.Embed,
        view: Optional[BaseView],
    ) -> None:
        """Replace the 'spinning' message with the result (or send it fresh if that failed)."""
        message: Optional[discord.Message]
        if isinstance(pending, discord.Message):
            await pending.edit(embed=embed, view=view)
            message = pending
        else:
            message = await send(interaction, embed=embed, view=view)
        if view is not None and isinstance(message, discord.Message):
            view.message = message

    async def _play_slots(self, interaction: discord.Interaction, bet: int) -> None:
        if not await self._begin(interaction, bet):
            return
        user_id = interaction.user.id
        try:
            async with escrowed_bet(self.bot.db, user_id, bet, "slots", self._welcome()) as bet_id:
                await self._defer(interaction)
                result = slots_engine.spin(new_rng())
                payout = result.payout(bet)
                pending = await send(
                    interaction,
                    embed=EmbedFactory.spinning(
                        title="Slots",
                        description="`[ ? ] [ ? ] [ ? ]`",
                        icon=Emoji.SLOTS,
                        bot_avatar_url=self._avatar_url(),
                    ),
                )
                await asyncio.sleep(SPIN_DELAY_S)
                balance = await self.bot.db.settle_bet(bet_id, payout)
                embed = EmbedFactory.slots_result(
                    user_mention=f"<@{user_id}>",
                    reels=result.reels,
                    stake=bet,
                    payout=payout,
                    new_balance=balance,
                    bot_avatar_url=self._avatar_url(),
                )
                view = SlotsView(user_id, bet, replay=self._play_slots)
                await self._reveal(interaction, pending, embed, view)
        except YokaiError as err:
            await self._send_domain_error(interaction, err)
        finally:
            self.bot.active_games.release(user_id)

    @app_commands.command(name="slots", description="Spin the slots for Renown.")
    @app_commands.describe(bet="Renown to wager (whole number)")
    async def slots(self, interaction: discord.Interaction, bet: int) -> None:
        await self._play_slots(interaction, bet)

    @app_commands.command(name="roulette", description="Bet on a single spin of the wheel.")
    @app_commands.describe(
        bet="Renown to wager (whole number)",
        choice="What to bet on",
        pick="Number 0-36 for straight, or 1-3 for dozen/column",
    )
    @app_commands.choices(choice=ROULETTE_CHOICES)
    async def roulette(
        self,
        interaction: discord.Interaction,
        bet: int,
        choice: app_commands.Choice[str],
        pick: Optional[int] = None,
    ) -> None:
        try:
            wheel_bet = roulette_engine.parse_bet(choice.value, pick)
        except InvalidGameChoiceError as err:
            await self._send_domain_error(interaction, err)
            return
        if not await self._begin(interaction, bet):
            return
        user_id = interaction.user.id
        try:
            async with escrowed_bet(
                self.bot.db, user_id, bet, "roulette", self._welcome()
            ) as bet_id:
                await self._defer(interaction)
                pocket = roulette_engine.spin(new_rng())
                payout = bet * roulette_engine.return_multiplier(wheel_bet, pocket)
                pending = await send(
                    interaction,
                    embed=EmbedFactory.spinning(
                        title="Roulette",
                        description=f"Bet: **{wheel_bet.label}**\nThe wheel is spinning…",
                        icon=Emoji.DICE,
                        bot_avatar_url=self._avatar_url(),
                    ),
                )
                await asyncio.sleep(SPIN_DELAY_S)
                balance = await self.bot.db.settle_bet(bet_id, payout)
                embed = EmbedFactory.roulette_result(
                    user_mention=f"<@{user_id}>",
                    bet_label=wheel_bet.label,
                    pocket=pocket,
                    pocket_colour=roulette_engine.colour(pocket),
                    stake=bet,
                    payout=payout,
                    new_balance=balance,
                    bot_avatar_url=self._avatar_url(),
                )
                await self._reveal(interaction, pending, embed, None)
        except YokaiError as err:
            await self._send_domain_error(interaction, err)
        finally:
            self.bot.active_games.release(user_id)

    @app_commands.command(
        name="gamble-stats", description="Show your (or another member's) gambling record."
    )
    @app_commands.describe(user="Whose record to show (defaults to you)")
    async def gamble_stats(
        self, interaction: discord.Interaction, user: Optional[discord.Member] = None
    ) -> None:
        await interaction.response.defer(thinking=True)
        target = user or interaction.user
        rows = await self.bot.db.get_gamble_stats(target.id)
        embed = EmbedFactory.gamble_stats(
            user_mention=f"<@{target.id}>", rows=rows, bot_avatar_url=self._avatar_url()
        )
        await send(interaction, embed=embed)


async def setup(bot: YokaiBot) -> None:
    """Extension setup entrypoint."""
    await bot.add_cog(GamblingCog(bot))
