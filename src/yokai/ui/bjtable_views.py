"""Blackjack table: one shared dealer, players act in join order with a timer per decision."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Mapping, Optional

import discord

from yokai.errors import LobbyClosedError, YokaiError
from yokai.gambling.engines.bjtable import BlackjackTable
from yokai.gambling.engines.blackjack import Outcome, build_shoe, hand_value
from yokai.gambling.lobby import Lobby
from yokai.moderation.gate import screen
from yokai.ui.embeds import EmbedFactory
from yokai.ui.table_common import avatar_url, finish_table, reply_error

if TYPE_CHECKING:
    from yokai.bot import YokaiBot

logger = logging.getLogger(__name__)

_STATUS_TEXT = {
    "blackjack": "blackjack",
    "bust": "bust",
    "21": "21",
    "stand": "stands",
}


class BlackjackTableView(discord.ui.View):
    """Hit / Stand for whoever is up; everyone else is told it is not their turn."""

    def __init__(self, bot: YokaiBot, lobby: Lobby, table: BlackjackTable) -> None:
        super().__init__(timeout=None)
        self.bot = bot
        self.lobby = lobby
        self.table = table
        self.message: Optional[discord.Message] = None
        self.acted = asyncio.Event()
        self.lock = asyncio.Lock()
        self.closed = False  # set by the runner, under the lock, once it is about to settle
        self.deadline_ts = 0
        self.reset_deadline()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return not await screen(interaction)

    def reset_deadline(self) -> None:
        self.deadline_ts = int(self.bot.tables.clock() + self.bot.tables.pick_seconds)

    def build_embed(self, results: Optional[Mapping[int, str]] = None) -> discord.Embed:
        """The table so far; with `results` (per-player text) it is the final embed."""
        table = self.table
        finished = results is not None
        seats = []
        for user_id in table.order:
            seat = self.lobby.seats[user_id]
            if results is not None:
                status = results[user_id]
            elif user_id == table.current:
                status = "to act"
            else:
                state = table.status_of(user_id)
                status = _STATUS_TEXT.get(state, "waiting")
            hand = table.hands[user_id]
            seats.append((user_id, seat.stake, [str(c) for c in hand], hand_value(hand)[0], status))
        dealer_total = hand_value(table.dealer)[0] if finished else None
        dealer_cards = [str(c) for c in table.dealer] if finished else [str(table.dealer[0]), "??"]
        return EmbedFactory.bjtable_hand(
            seats,
            dealer_cards,
            dealer_total,
            None if finished else self.deadline_ts,
            finished,
            bot_avatar_url=avatar_url(self.bot),
        )

    async def _act(self, interaction: discord.Interaction, move: str) -> None:
        async with self.lock:
            if self.closed:
                await reply_error(
                    self.bot,
                    interaction,
                    LobbyClosedError("The round is over.", user_hint=None),
                )
                return
            try:
                getattr(self.table, move)(interaction.user.id)
            except YokaiError as err:
                await reply_error(self.bot, interaction, err)
                return
            self.reset_deadline()
            self.acted.set()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Hit", style=discord.ButtonStyle.primary)
    async def hit_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._act(interaction, "hit")

    @discord.ui.button(label="Stand", style=discord.ButtonStyle.secondary)
    async def stand_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._act(interaction, "stand")


async def _await_action(view: BlackjackTableView, seconds: float) -> bool:
    """Wait for a player to act; False if `seconds` pass first. Tests replace this."""
    try:
        await asyncio.wait_for(view.acted.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        return False
    return True


def _result_text(outcome: Outcome, stake: int, payout: int) -> str:
    net = payout - stake
    label = {
        Outcome.BLACKJACK: "blackjack",
        Outcome.WIN: "win",
        Outcome.PUSH: "push",
        Outcome.LOSE: "lose",
    }[outcome]
    sign = f"+{net:,}" if net > 0 else f"{net:,}"
    return f"{label}: {sign}"


async def run_bjtable(bot: YokaiBot, lobby: Lobby, message: discord.Message) -> None:
    """Run a started table. Any failure refunds every stake and tells the players."""
    tables = bot.tables
    table = BlackjackTable(build_shoe(tables.rng_factory()), list(lobby.seats))
    view = BlackjackTableView(bot, lobby, table)
    view.message = message

    async def play() -> discord.Embed:
        await message.edit(embed=view.build_embed(), view=view)
        while True:
            who = table.current
            if who is None:
                break
            view.acted.clear()
            if await _await_action(view, tables.pick_seconds):
                continue  # someone acted; the button handler already updated the message
            async with view.lock:
                if view.acted.is_set():
                    continue  # a press landed while we waited for the lock: their turn goes on
                if table.current == who:  # still their turn: they ran out of time
                    table.stand(who)
                    view.reset_deadline()
                    with contextlib.suppress(discord.HTTPException):
                        await message.edit(embed=view.build_embed(), view=view)
        # A player's last action edits the message from its own handler while holding the lock.
        # Wait for that edit to finish before the result goes up, or a slow in-play edit can land
        # last and leave a frozen board with live buttons over the settled result.
        async with view.lock:
            view.closed = True
        view.stop()
        table.play_dealer()
        stakes = {user_id: seat.stake for user_id, seat in lobby.seats.items()}
        payouts = table.payouts(stakes)
        await tables.settle(lobby, payouts)
        results = {
            user_id: _result_text(outcome, stakes[user_id], payouts[user_id])
            for user_id, outcome in table.outcomes().items()
        }
        return view.build_embed(results)

    await finish_table(bot, lobby, message, play)
