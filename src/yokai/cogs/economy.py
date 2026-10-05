"""Renown economy slash commands: balance, daily claim, payments, leaderboard, admin tools."""

from __future__ import annotations

import random
import time
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

from yokai.economy import daily
from yokai.errors import DailyOnCooldownError, InsufficientFundsError, InvalidPaymentError
from yokai.ui.embeds import EmbedFactory, send
from yokai.ui.views import LeaderboardPaginator

if TYPE_CHECKING:
    from yokai.bot import YokaiBot


class EconomyCog(commands.Cog, name="Economy"):
    """Renown wallet, daily claims, payments, and the leaderboard."""

    def __init__(self, bot: YokaiBot) -> None:
        self.bot = bot
        self._rng = random.Random()

    def _avatar_url(self) -> Optional[str]:
        return str(self.bot.user.display_avatar.url) if self.bot.user else None

    async def _send_domain_error(self, interaction: discord.Interaction, err: Exception) -> None:
        title = getattr(err, "title", "That didn't work")
        message = getattr(err, "message", str(err))
        hint = getattr(err, "user_hint", None)
        embed = EmbedFactory.error(
            message=message, title=title, user_hint=hint, bot_avatar_url=self._avatar_url()
        )
        await send(interaction, embed=embed, ephemeral=True)

    @app_commands.command(
        name="balance", description="Check your Renown balance, or another member's."
    )
    @app_commands.describe(user="Whose balance to check (defaults to you)")
    async def balance(
        self, interaction: discord.Interaction, user: Optional[discord.Member] = None
    ) -> None:
        """Show a Renown balance; only ever creates a wallet for the caller checking their own."""
        await interaction.response.defer(thinking=True)
        target = user or interaction.user
        if target.id == interaction.user.id:
            wallet = await self.bot.db.ensure_wallet(
                target.id, self.bot.config.economy_welcome_bonus
            )
            bal = wallet.balance
        else:
            wallet = await self.bot.db.get_wallet(target.id)
            bal = wallet.balance if wallet else 0
        embed = EmbedFactory.balance(
            user_mention=f"<@{target.id}>", balance=bal, bot_avatar_url=self._avatar_url()
        )
        await send(interaction, embed=embed)

    @app_commands.command(name="daily", description="Claim your daily Renown reward.")
    async def daily(self, interaction: discord.Interaction) -> None:
        """Claim /daily; a cooldown check runs before deferring so it stays ephemeral."""
        user_id = interaction.user.id
        now = int(time.time())
        wallet = await self.bot.db.get_wallet(user_id)
        remaining = daily.cooldown_remaining_s(wallet.last_daily_ts if wallet else None, now)
        if remaining is not None:
            await self._send_domain_error(interaction, DailyOnCooldownError(remaining))
            return

        await interaction.response.defer(thinking=True)
        result = await self.bot.db.claim_daily(
            user_id,
            base=self.bot.config.economy_daily_base,
            welcome_bonus=self.bot.config.economy_welcome_bonus,
            now=now,
            rng=self._rng,
        )
        embed = EmbedFactory.daily_claim(
            user_mention=f"<@{user_id}>",
            payout=result.payout,
            streak_day=result.streak_day,
            new_balance=result.new_balance,
            bot_avatar_url=self._avatar_url(),
        )
        await send(interaction, embed=embed)

    @app_commands.command(name="pay", description="Send Renown to another member.")
    @app_commands.describe(user="Who to pay", amount="How much Renown to send (whole number)")
    async def pay(
        self, interaction: discord.Interaction, user: discord.Member, amount: int
    ) -> None:
        """All validation happens before defer so a rejection stays ephemeral."""
        sender = interaction.user
        if amount <= 0:
            await self._send_domain_error(
                interaction, InvalidPaymentError("Enter a positive whole number of Renown to send.")
            )
            return
        if user.bot or user.id == sender.id:
            await self._send_domain_error(
                interaction,
                InvalidPaymentError("You can only pay another human member of the server."),
            )
            return

        sender_wallet = await self.bot.db.ensure_wallet(
            sender.id, self.bot.config.economy_welcome_bonus
        )
        if sender_wallet.balance < amount:
            await self._send_domain_error(
                interaction, InsufficientFundsError(balance=sender_wallet.balance, amount=amount)
            )
            return

        await interaction.response.defer(thinking=True)
        await self.bot.db.pay(
            sender.id, user.id, amount, welcome_bonus=self.bot.config.economy_welcome_bonus
        )
        embed = EmbedFactory.pay_sent(
            sender_mention=f"<@{sender.id}>",
            recipient_mention=f"<@{user.id}>",
            amount=amount,
            bot_avatar_url=self._avatar_url(),
        )
        await send(interaction, embed=embed)

    @app_commands.command(
        name="leaderboard", description="Show the top Renown balances on the server."
    )
    @app_commands.describe(page="Page number to jump to (defaults to page 1)")
    async def leaderboard(
        self, interaction: discord.Interaction, page: Optional[int] = None
    ) -> None:
        """Anyone can page through the leaderboard, not just whoever ran the command."""
        await interaction.response.defer(thinking=True)
        initial_page = page if page is not None and page >= 1 else 1
        view = LeaderboardPaginator(
            bot=self.bot,
            author_id=interaction.user.id,
            initial_page=initial_page,
            bot_avatar_url=self._avatar_url(),
        )
        embed = await view.build_embed()
        msg = await send(interaction, embed=embed, view=view)
        if isinstance(msg, discord.Message):
            view.message = msg

    @app_commands.command(
        name="economy-admin", description="Admin-only: grant or deduct a member's Renown."
    )
    @app_commands.describe(user="Member to adjust", amount="Renown to add (negative to deduct)")
    async def economy_admin(
        self, interaction: discord.Interaction, user: discord.Member, amount: int
    ) -> None:
        """Admin-only balance correction, always ephemeral, floored at 0."""
        if not await self.bot.moderation.is_admin(interaction.user.id):
            embed = EmbedFactory.error(
                message="Access denied: this command is restricted to Yokai admins.",
                title="Admins only",
                user_hint="Ask the bot owner if you need a balance corrected.",
                context="Permissions",
                bot_avatar_url=self._avatar_url(),
                include_quip=False,
            )
            await send(interaction, embed=embed, ephemeral=True)
            return

        if user.bot:
            await self._send_domain_error(
                interaction,
                InvalidPaymentError("You can only adjust another human member of the server."),
            )
            return

        await interaction.response.defer(ephemeral=True)
        applied, new_balance = await self.bot.db.admin_adjust_wallet(
            user.id, amount, welcome_bonus=self.bot.config.economy_welcome_bonus
        )
        embed = EmbedFactory.economy_admin(
            target_mention=f"<@{user.id}>",
            delta_applied=applied,
            new_balance=new_balance,
            bot_avatar_url=self._avatar_url(),
        )
        await send(interaction, embed=embed, ephemeral=True)


async def setup(bot: YokaiBot) -> None:
    """Extension setup entrypoint."""
    await bot.add_cog(EconomyCog(bot))
