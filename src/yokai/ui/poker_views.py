"""The poker table: action buttons, private hole cards, the Raise form and the hand loop."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Callable

import discord

from yokai.errors import InvalidPokerActionError, LobbyClosedError, NotAtTableError, YokaiError
from yokai.gambling.engines.poker import HandResult, PokerHand, best_hand, describe
from yokai.gambling.engines.pokertable import CashOut, PokerTable
from yokai.gambling.lobby import Lobby
from yokai.moderation.gate import screen
from yokai.ui.embeds import EmbedFactory, send
from yokai.ui.table_common import avatar_url, finish_table, reply_error

if TYPE_CHECKING:
    from yokai.bot import YokaiBot

logger = logging.getLogger(__name__)

RECAP_PAUSE_S = 6.0  # how long a finished hand stays on screen before the next one is dealt

Move = Callable[[PokerHand, int], None]


def parse_chips(text: str) -> int:
    """Turn the text typed into the Raise form into a whole number of chips."""
    cleaned = text.strip().replace(",", "")
    if not cleaned.isdigit():
        raise InvalidPokerActionError(
            "Enter the total you want to raise to as a whole number, like 200."
        )
    return int(cleaned)


def _cards(cards: object) -> list[str]:
    return [str(card) for card in cards]  # type: ignore[attr-defined]


class PokerTableView(discord.ui.View):
    """The buttons under one hand. Only the player whose turn it is can act."""

    def __init__(self, bot: YokaiBot, lobby: Lobby, table: PokerTable, hand: PokerHand) -> None:
        super().__init__(timeout=None)
        self.bot = bot
        self.lobby = lobby
        self.table = table
        self.hand = hand
        self.message: discord.Message | None = None
        self.acted = asyncio.Event()
        self.lock = asyncio.Lock()
        self.closed = False  # set by the runner, under the lock, once the hand is being wrapped up
        self.deadline_ts = 0
        self.reset_deadline()
        self.sync_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return not await screen(interaction)

    def reset_deadline(self) -> None:
        self.deadline_ts = int(self.bot.tables.clock() + self.bot.tables.pick_seconds)

    def build_embed(self) -> discord.Embed:
        """The public table: board, pot and seats. Hole cards are never shown here."""
        hand = self.hand
        seats = []
        for user_id in hand.order:
            if user_id in hand.folded:
                status = "folded"
            elif user_id == hand.current:
                status = "to act"
            elif hand.stacks[user_id] == 0:
                status = "all-in"
            else:
                status = ""
            seats.append(
                (user_id, hand.stacks[user_id], hand.bet[user_id], status, user_id == hand.button)
            )
        return EmbedFactory.poker_table(
            hand_no=self.table.hand_no,
            max_hands=self.table.max_hands,
            street=hand.street.value,
            board=_cards(hand.board),
            pot=hand.pot,
            small_blind=self.table.small_blind,
            big_blind=self.table.big_blind,
            seats=seats,
            deadline_ts=None if hand.is_over else self.deadline_ts,
            bot_avatar_url=avatar_url(self.bot),
        )

    def sync_buttons(self) -> None:
        """Label and enable the buttons for whoever is up now."""
        who = self.hand.current
        acting = (self.fold_button, self.call_button, self.raise_button, self.all_in_button)
        if who is None:
            for button in acting:
                button.disabled = True
            return
        legal = self.hand.legal_actions(who)
        self.fold_button.disabled = False
        self.call_button.disabled = False
        self.call_button.label = "Check" if legal.can_check else f"Call {legal.call_amount:,}"
        can_raise = legal.max_raise_to is not None
        self.raise_button.disabled = not can_raise
        self.all_in_button.disabled = not can_raise

    async def _act(self, interaction: discord.Interaction, move: Move) -> None:
        async with self.lock:
            if self.closed:
                await reply_error(
                    self.bot, interaction, LobbyClosedError("This hand is over.", user_hint=None)
                )
                return
            user_id = interaction.user.id
            try:
                move(self.hand, user_id)
            except YokaiError as err:
                await reply_error(self.bot, interaction, err)
                return
            self.table.record_action(user_id)
            self.reset_deadline()
            self.sync_buttons()
            self.acted.set()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)

    def turn_token(self) -> tuple[object, ...]:
        """Identifies the exact state of the hand a Raise form was opened in."""
        hand = self.hand
        return (hand.current, hand.street, hand.current_bet, hand.pot)

    async def raise_to(
        self, interaction: discord.Interaction, amount: int, token: tuple[object, ...] | None = None
    ) -> None:
        """Raise so the player's total bet this round is `amount` (the Raise form calls this).

        A form carries the `token` of the hand it was opened in; if the hand has moved on since
        (the turn passed, a new street), the raise is refused instead of applied to new facts.
        """

        def move(hand: PokerHand, user_id: int) -> None:
            if token is not None and token != self.turn_token():
                raise InvalidPokerActionError("That raise form is out of date. Press Raise again.")
            hand.raise_to(user_id, amount)

        await self._act(interaction, move)

    @discord.ui.button(label="Fold", style=discord.ButtonStyle.secondary)
    async def fold_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._act(interaction, lambda hand, user_id: hand.fold(user_id))

    @discord.ui.button(label="Call", style=discord.ButtonStyle.success)
    async def call_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        def check_or_call(hand: PokerHand, user_id: int) -> None:
            if hand.legal_actions(user_id).can_check:
                hand.check(user_id)
            else:
                hand.call(user_id)

        await self._act(interaction, check_or_call)

    @discord.ui.button(label="Raise", style=discord.ButtonStyle.primary)
    async def raise_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        try:
            legal = self.hand.legal_actions(interaction.user.id)
        except YokaiError as err:
            await reply_error(self.bot, interaction, err)
            return
        if legal.min_raise_to is None or legal.max_raise_to is None:
            await reply_error(
                self.bot, interaction, InvalidPokerActionError("You can't raise here.")
            )
            return
        await interaction.response.send_modal(
            RaiseModal(self, legal.min_raise_to, legal.max_raise_to)
        )

    @discord.ui.button(label="All-in", style=discord.ButtonStyle.danger)
    async def all_in_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._act(interaction, lambda hand, user_id: hand.all_in(user_id))

    @discord.ui.button(label="My cards", style=discord.ButtonStyle.secondary, row=1)
    async def cards_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        user_id = interaction.user.id
        hole = self.hand.holes.get(user_id)
        if hole is None:
            await reply_error(self.bot, interaction, NotAtTableError())
            return
        lines = [f"Your cards: `{' '.join(_cards(hole))}`"]
        if self.hand.board:
            lines.append(f"Board: `{' '.join(_cards(self.hand.board))}`")
            lines.append(f"You have **{describe(best_hand([*hole, *self.hand.board]))}**.")
        embed = EmbedFactory.info(
            title="Your cards",
            description="\n".join(lines),
            context="Gambling",
            bot_avatar_url=avatar_url(self.bot),
        )
        await send(interaction, embed=embed, ephemeral=True)

    @discord.ui.button(label="Leave", style=discord.ButtonStyle.secondary, row=1)
    async def leave_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        user_id = interaction.user.id
        try:
            self.table.request_leave(user_id)
        except YokaiError as err:
            await reply_error(self.bot, interaction, err)
            return
        if self.hand.current == user_id:
            await self._act(interaction, lambda hand, who: hand.fold(who))
            return
        embed = EmbedFactory.info(
            title="Leaving the table",
            description="You'll be folded and paid out your chips when this hand ends.",
            context="Gambling",
            bot_avatar_url=avatar_url(self.bot),
        )
        await send(interaction, embed=embed, ephemeral=True)


class RaiseModal(discord.ui.Modal):
    """Asks how much to raise to, then hands the number to the table view."""

    def __init__(self, table_view: PokerTableView, min_to: int, max_to: int) -> None:
        super().__init__(title="Raise", timeout=300)
        self.table_view = table_view
        self.token = table_view.turn_token()
        self.amount = discord.ui.TextInput(
            label="Raise to (your total bet this round)",
            placeholder=f"{min_to:,} to {max_to:,}",
            min_length=1,
            max_length=12,
        )
        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            amount = parse_chips(self.amount.value)
        except YokaiError as err:
            await reply_error(self.table_view.bot, interaction, err)
            return
        await self.table_view.raise_to(interaction, amount, self.token)


async def _await_action(view: PokerTableView, seconds: float) -> bool:
    """Wait for a player to act; False if `seconds` pass first. Tests replace this."""
    try:
        await asyncio.wait_for(view.acted.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        return False
    return True


async def _refresh(view: PokerTableView, message: discord.Message) -> None:
    if view.hand.is_over:
        return
    with contextlib.suppress(discord.HTTPException):
        await message.edit(embed=view.build_embed(), view=view)


async def _play_hand(
    bot: YokaiBot,
    table: PokerTable,
    hand: PokerHand,
    view: PokerTableView,
    message: discord.Message,
) -> None:
    """Drive one hand to its end: wait for each player, fold leavers, act for the absent."""
    while not hand.is_over:
        who = hand.current
        assert who is not None
        if who in table.leaving:
            async with view.lock:
                if hand.current == who:
                    hand.fold(who)
                    view.reset_deadline()
                    view.sync_buttons()
            await _refresh(view, message)
            continue
        view.acted.clear()
        if await _await_action(view, bot.tables.pick_seconds):
            continue  # someone acted; the button handler already updated the message
        async with view.lock:
            if view.acted.is_set():
                continue  # a press landed while we waited for the lock
            if hand.current == who:  # still their turn: they ran out of time
                hand.auto_action(who)
                table.record_timeout(who)
                view.reset_deadline()
                view.sync_buttons()
        await _refresh(view, message)
    # A player's last action edits the message from its own handler while holding the lock. Wait
    # for that edit to finish before the recap goes up, or it could land last over the recap.
    async with view.lock:
        view.closed = True
    view.stop()


def _recap_embed(
    bot: YokaiBot, table: PokerTable, hand: PokerHand, result: HandResult, left: list[CashOut]
) -> discord.Embed:
    awards = [
        (pot.amount, list(pot.winners), len(pot.eligible) == 1 and index > 0)
        for index, pot in enumerate(result.pots)
    ]
    shown = {
        user_id: (" ".join(_cards(cards)), describe(result.ranks[user_id]))
        for user_id, cards in result.shown.items()
    }
    return EmbedFactory.poker_hand_result(
        hand_no=table.hand_no,
        max_hands=table.max_hands,
        board=_cards(hand.board) if result.showdown else [],
        awards=awards,
        shown=shown,
        stacks=dict(table.stacks),
        left=[(cash.user_id, cash.chips, cash.reason) for cash in left],
        bot_avatar_url=avatar_url(bot),
    )


async def run_poker(bot: YokaiBot, lobby: Lobby, message: discord.Message) -> None:
    """Run a started poker table: hand after hand until it closes. Any failure refunds the rest."""
    tables = bot.tables
    buy_in = lobby.host_seat.stake
    table = PokerTable(list(lobby.seats), buy_in, tables.poker_max_hands, tables.rng_factory)
    paid: dict[int, int] = {}

    async def cash_out_flagged() -> list[CashOut]:
        """Pay out everyone who busted or is leaving, one by one."""
        leaving = table.take_cash_outs()
        for cash in leaving:
            await tables.cash_out(lobby, cash.user_id, cash.chips)
            paid[cash.user_id] = cash.chips
        return leaving

    async def play() -> discord.Embed:
        while True:
            await cash_out_flagged()  # also picks up a Leave pressed between hands
            if table.is_finished:
                break
            hand = table.start_hand()
            view = PokerTableView(bot, lobby, table, hand)
            view.message = message
            # A failed edit is not worth cancelling the table over: the chips are consistent, and
            # the next edit shows the board again.
            with contextlib.suppress(discord.HTTPException):
                await message.edit(embed=view.build_embed(), view=view)
            await _play_hand(bot, table, hand, view, message)
            result = hand.result()
            table.finish_hand(result)
            left = await cash_out_flagged()
            with contextlib.suppress(discord.HTTPException):
                await message.edit(embed=_recap_embed(bot, table, hand, result, left), view=None)
            await asyncio.sleep(RECAP_PAUSE_S)
        payouts = table.final_payouts()
        await tables.settle_poker(lobby, payouts)
        paid.update(payouts)
        return EmbedFactory.poker_final(
            hands_played=table.hand_no,
            max_hands=table.max_hands,
            buy_in=buy_in,
            results=[(user_id, paid[user_id]) for user_id in lobby.seats],
            bot_avatar_url=avatar_url(bot),
        )

    await finish_table(bot, lobby, message, play)
