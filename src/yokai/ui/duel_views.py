"""The duel's picking phase: a private number prompt, then the reveal."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING

import discord

from yokai.errors import NotAtTableError, YokaiError
from yokai.gambling.engines.duel import NUMBER_MAX, NUMBER_MIN, DuelRound, draw_target
from yokai.gambling.lobby import Lobby
from yokai.moderation.gate import screen
from yokai.ui.embeds import EmbedFactory, send
from yokai.ui.table_common import avatar_url, reply_error, tell_cancelled

if TYPE_CHECKING:
    from yokai.bot import YokaiBot

logger = logging.getLogger(__name__)


class DuelView(discord.ui.View):
    """The table message while players pick; the pick prompt itself is private to each player."""

    def __init__(self, bot: YokaiBot, lobby: Lobby, duel: DuelRound, deadline_ts: int) -> None:
        super().__init__(timeout=None)
        self.bot = bot
        self.lobby = lobby
        self.duel = duel
        self.deadline_ts = deadline_ts
        self.message: "discord.Message | None" = None
        self.all_picked = asyncio.Event()
        self.edit_lock = asyncio.Lock()  # serialises edits of the public table message
        self.closed = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return not await screen(interaction)

    def build_embed(self) -> discord.Embed:
        return EmbedFactory.duel_picking(
            seat_ids=list(self.lobby.seats),
            stake=self.lobby.host_seat.stake,
            pot=self.lobby.pot,
            rake_percent=self.bot.tables.rake_percent,
            deadline_ts=self.deadline_ts,
            picked=self.duel.picked_ids,
            bot_avatar_url=avatar_url(self.bot),
        )

    async def after_pick(self) -> None:
        """Run once a pick is locked in and answered: wake the runner, or show who is ready."""
        if self.duel.ready:
            self.all_picked.set()  # the result replaces the message, so it needs no refresh
            return
        async with self.edit_lock:
            if self.closed or self.message is None:
                return
            try:
                await self.message.edit(embed=self.build_embed(), view=self)
            except discord.HTTPException as exc:
                logger.warning("Could not refresh the duel message: %s", exc)

    @discord.ui.button(label="Pick your number", style=discord.ButtonStyle.primary)
    async def pick_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        user_id = interaction.user.id
        if user_id not in self.lobby.seats:
            await reply_error(self.bot, interaction, NotAtTableError())
            return
        if self.duel.has_picked(user_id):
            embed = EmbedFactory.info(
                title="Already locked in",
                description="You already picked. Picks can't be changed.",
                context="Gambling",
                bot_avatar_url=avatar_url(self.bot),
            )
            await send(interaction, embed=embed, ephemeral=True)
            return
        prompt = EmbedFactory.info(
            title="Pick your number",
            description=(
                f"Choose a number from **{NUMBER_MIN}** to **{NUMBER_MAX}**. "
                "It stays secret until the reveal, and you can't change it."
            ),
            context="Gambling",
            bot_avatar_url=avatar_url(self.bot),
        )
        picker = discord.ui.View(timeout=self.bot.tables.pick_seconds)
        picker.add_item(PickSelect(self))
        await send(interaction, embed=prompt, view=picker, ephemeral=True)


class PickSelect(discord.ui.Select):
    """The 1 to 10 dropdown inside a player's private prompt."""

    def __init__(self, duel_view: DuelView) -> None:
        super().__init__(
            placeholder="Choose your number",
            options=[
                discord.SelectOption(label=str(n), value=str(n))
                for n in range(NUMBER_MIN, NUMBER_MAX + 1)
            ],
        )
        self.duel_view = duel_view

    async def callback(self, interaction: discord.Interaction) -> None:
        number = int(self.values[0])
        try:
            self.duel_view.duel.submit(interaction.user.id, number)
        except YokaiError as err:
            await reply_error(self.duel_view.bot, interaction, err)
            return
        locked = EmbedFactory.info(
            title="Locked in",
            description=f"You picked **{number}**. Wait for the reveal.",
            context="Gambling",
            bot_avatar_url=avatar_url(self.duel_view.bot),
        )
        await interaction.response.edit_message(embed=locked, view=None)
        await self.duel_view.after_pick()


async def run_duel(bot: YokaiBot, lobby: Lobby, message: discord.Message) -> None:
    """Run a started duel: collect picks, resolve, reveal. Any failure refunds both stakes."""
    tables = bot.tables
    duel = DuelRound(list(lobby.seats), draw_target(tables.rng_factory()))
    deadline_ts = int(tables.clock() + tables.pick_seconds)
    view = DuelView(bot, lobby, duel, deadline_ts)
    view.message = message
    try:
        await message.edit(embed=view.build_embed(), view=view)
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(view.all_picked.wait(), timeout=tables.pick_seconds)
        # A 'locked in' refresh of the table message may still be in flight; let it finish so it
        # cannot land after the result and bring back a stale board with a live button.
        async with view.edit_lock:
            view.closed = True
        result = await tables.resolve_duel(lobby, duel)
    except BaseException as exc:
        await tables.abort(lobby)
        view.stop()
        if isinstance(exc, Exception):
            await tell_cancelled(bot, message)
        raise
    view.stop()
    final = EmbedFactory.duel_result(
        picks=result.outcome.picks,
        target=result.outcome.target,
        winner_id=result.outcome.winner_id,
        prize=result.prize,
        rake=result.rake,
        bot_avatar_url=avatar_url(bot),
    )
    try:
        await message.edit(embed=final, view=None)
    except discord.HTTPException as exc:
        logger.warning("Could not show the duel result (stakes are already settled): %s", exc)
