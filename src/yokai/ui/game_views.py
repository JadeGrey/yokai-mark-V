"""Interactive views for the gambling games."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Awaitable, Callable, Optional

import discord

from yokai.errors import BetNotOpenError, InsufficientFundsError
from yokai.gambling.engines.blackjack import BlackjackGame, Outcome, hand_value, payout_for
from yokai.ui.embeds import EmbedFactory
from yokai.ui.views import BaseView

if TYPE_CHECKING:
    from yokai.storage.db import Database

logger = logging.getLogger(__name__)

ReplayFn = Callable[[discord.Interaction, int], Awaitable[None]]


class SlotsView(BaseView):
    """A single-use 'Spin again' button that replays the same stake for the same player."""

    def __init__(self, user_id: int, stake: int, replay: ReplayFn, timeout: float = 120.0) -> None:
        super().__init__(allowed_user_ids=[user_id], timeout=timeout)
        self._stake = stake
        self._replay = replay

    @discord.ui.button(label="Spin again", style=discord.ButtonStyle.primary)
    async def spin_again(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.disable_all_items()
        await interaction.response.edit_message(view=self)
        await self._replay(interaction, self._stake)


def _verdict(game: BlackjackGame) -> str:
    if game.player_busted:
        return "bust"
    if game.outcome is Outcome.BLACKJACK:
        return "blackjack"
    if game.outcome is Outcome.WIN:
        return "win"
    if game.outcome is Outcome.PUSH:
        return "push"
    return "lose"


class BlackjackView(BaseView):
    """Hit / Stand / Double buttons for one blackjack hand, owning the hand's escrowed bets."""

    def __init__(
        self,
        *,
        game: BlackjackGame,
        db: Database,
        user_id: int,
        user_mention: str,
        stake: int,
        bet_id: int,
        group_id: str,
        welcome_bonus: int,
        can_double: bool,
        release: Callable[[], None],
        bot_avatar_url: Optional[str] = None,
        timeout: float = 180.0,
    ) -> None:
        super().__init__(allowed_user_ids=[user_id], timeout=timeout)
        self.game = game
        self.db = db
        self.user_id = user_id
        self.user_mention = user_mention
        self.stake = stake
        self.bet_id = bet_id
        self.group_id = group_id
        self.welcome_bonus = welcome_bonus
        self.bot_avatar_url = bot_avatar_url
        self._release = release
        self._double_bet_id: Optional[int] = None
        self._lock = asyncio.Lock()
        self._closed = False
        self._result: Optional[tuple[int, int, int]] = None  # (total stake, payout, balance)
        self.double_button.disabled = not can_double

    @property
    def finished(self) -> bool:
        """True once the hand has been settled."""
        return self._result is not None

    def _open_bet_ids(self) -> list[int]:
        if self._double_bet_id is None:
            return [self.bet_id]
        return [self.bet_id, self._double_bet_id]

    def _close(self) -> None:
        self._closed = True
        self.disable_all_items()
        self.stop()
        self._release()

    async def _refund_all(self) -> None:
        for bet_id in self._open_bet_ids():
            with contextlib.suppress(BetNotOpenError):
                await self.db.refund_bet(bet_id)

    def build_embed(self) -> discord.Embed:
        game = self.game
        player_cards = [str(card) for card in game.player]
        player_total = hand_value(game.player)[0]
        if self._result is None:
            return EmbedFactory.blackjack_hand(
                user_mention=self.user_mention,
                player_cards=player_cards,
                player_total=player_total,
                dealer_cards=[str(game.dealer[0]), "??"],
                dealer_total=None,
                stake=self.stake * len(self._open_bet_ids()),
                bot_avatar_url=self.bot_avatar_url,
            )
        total_stake, total_payout, balance = self._result
        return EmbedFactory.blackjack_hand(
            user_mention=self.user_mention,
            player_cards=player_cards,
            player_total=player_total,
            dealer_cards=[str(card) for card in game.dealer],
            dealer_total=hand_value(game.dealer)[0],
            stake=total_stake,
            verdict=_verdict(game),
            payout=total_payout,
            new_balance=balance,
            bot_avatar_url=self.bot_avatar_url,
        )

    async def _finish(self) -> discord.Embed:
        """Settle every bet of the finished hand, free the player, and return the result embed."""
        outcome = self.game.outcome
        assert outcome is not None
        per_bet = payout_for(outcome, self.stake)
        bet_ids = self._open_bet_ids()
        try:
            balance = 0
            for bet_id in bet_ids:
                balance = await self.db.settle_bet(bet_id, per_bet)
        except Exception:
            try:
                await self._refund_all()
            finally:
                self._close()
            raise
        self._result = (self.stake * len(bet_ids), per_bet * len(bet_ids), balance)
        self._close()
        return self.build_embed()

    async def start(self) -> discord.Embed:
        """First render; settles at once when the deal was a natural."""
        async with self._lock:
            if self.game.finished:
                return await self._finish()
            return self.build_embed()

    async def abort(self) -> None:
        """Refund the hand's bets and free the player (used when sending the hand failed)."""
        async with self._lock:
            if self._closed:
                return
            try:
                await self._refund_all()
            finally:
                self._close()

    async def _ack_if_closed(self, interaction: discord.Interaction) -> bool:
        """Swallow a press that arrives after the hand ended (e.g. a double click)."""
        if not self._closed:
            return False
        if not interaction.response.is_done():
            await interaction.response.defer()
        return True

    async def _advance(self, interaction: discord.Interaction) -> None:
        if self.game.finished:
            embed = await self._finish()
        else:
            self.double_button.disabled = self.double_button.disabled or not self.game.can_double
            embed = self.build_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Hit", style=discord.ButtonStyle.primary)
    async def hit_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        async with self._lock:
            if await self._ack_if_closed(interaction):
                return
            self.game.hit()
            self.double_button.disabled = True
            await self._advance(interaction)

    @discord.ui.button(label="Stand", style=discord.ButtonStyle.secondary)
    async def stand_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        async with self._lock:
            if await self._ack_if_closed(interaction):
                return
            self.game.stand()
            await self._advance(interaction)

    @discord.ui.button(label="Double", style=discord.ButtonStyle.success)
    async def double_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        async with self._lock:
            if await self._ack_if_closed(interaction):
                return
            if not self.game.can_double:
                if not interaction.response.is_done():
                    await interaction.response.defer()
                return
            try:
                self._double_bet_id = await self.db.place_bet(
                    self.user_id,
                    self.stake,
                    "blackjack",
                    self.welcome_bonus,
                    group_id=self.group_id,
                )
            except InsufficientFundsError as err:
                self.double_button.disabled = True
                embed = EmbedFactory.error(
                    message=err.message,
                    title=err.title,
                    user_hint=err.user_hint,
                    bot_avatar_url=self.bot_avatar_url,
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
            self.game.double()
            await self._advance(interaction)

    async def on_timeout(self) -> None:
        """Walking away must not forfeit the hand: auto-stand, settle, and disable the buttons."""
        async with self._lock:
            if self._closed:
                return
            if not self.game.finished:
                self.game.stand()
            embed = await self._finish()
        if self.message:
            try:
                await self.message.edit(embed=embed, view=self)
            except discord.HTTPException as exc:
                logger.debug("Could not update blackjack message on timeout: %s", exc)
