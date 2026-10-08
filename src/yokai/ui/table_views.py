"""The persistent Play embed's button, the stake form, and the lobby of an open table."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Awaitable, Callable, Optional

import aiosqlite
import discord

from yokai.errors import (
    ChannelNotConfiguredError,
    InsufficientFundsError,
    YokaiError,
)
from yokai.gambling.lobby import Lobby
from yokai.gambling.tables import BeginOutcome
from yokai.moderation.gate import screen
from yokai.ui.bjtable_views import run_bjtable
from yokai.ui.crash_views import run_crash
from yokai.ui.duel_views import run_duel
from yokai.ui.embeds import EmbedFactory
from yokai.ui.jackpot_views import run_jackpot
from yokai.ui.table_common import (
    NO_MENTIONS,
    avatar_url,
    delete_message_quietly,
    dismiss,
    parse_stake,
    reply_error,
    tell_cancelled,
)

if TYPE_CHECKING:
    from yokai.bot import YokaiBot

logger = logging.getLogger(__name__)

PLAY_CUSTOM_ID = "yokai:table:play"

StakeCallback = Callable[[discord.Interaction, int], Awaitable[None]]
GameRunner = Callable[["YokaiBot", Lobby, discord.Message], Awaitable[None]]

# One entry per table game: what runs once the table has started.
GAME_RUNNERS: dict[str, GameRunner] = {
    "duel": run_duel,
    "jackpot": run_jackpot,
    "crash": run_crash,
    "bjtable": run_bjtable,
}


class StakeModal(discord.ui.Modal):
    """Asks for a stake, then hands the number to `on_stake`."""

    stake = discord.ui.TextInput(
        label="Your stake (Renown)", placeholder="e.g. 100", min_length=1, max_length=12
    )

    def __init__(self, bot: YokaiBot, title: str, on_stake: StakeCallback) -> None:
        super().__init__(title=title, timeout=300)
        self.bot = bot
        self._on_stake = on_stake

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            await self._on_stake(interaction, parse_stake(self.stake.value))
        except YokaiError as err:
            await reply_error(self.bot, interaction, err)
        except (discord.HTTPException, aiosqlite.Error) as exc:
            logger.error("The stake form failed: %s", exc, exc_info=exc)
            await reply_error(
                self.bot,
                interaction,
                YokaiError(
                    "Something went wrong on my end, and any stake was returned.",
                    user_hint="Try again in a moment.",
                ),
            )


async def handle_play(bot: YokaiBot, interaction: discord.Interaction) -> None:
    """Play was pressed: check the channel, the table and the member, then ask for a stake."""
    channel_id = interaction.channel_id
    user_id = interaction.user.id
    try:
        row = await bot.db.get_multiplayer_channel(channel_id) if channel_id else None
        if row is None or channel_id is None:
            raise ChannelNotConfiguredError()
        bot.tables.check_can_open(channel_id, user_id)
        wallet = await bot.db.ensure_wallet(user_id, bot.config.economy_welcome_bonus)
        minimum = bot.config.gambling_min_bet
        if wallet.balance < minimum:
            raise InsufficientFundsError(balance=wallet.balance, amount=minimum, action="bet")
        spec = bot.tables.spec(row.game)
    except YokaiError as err:
        await reply_error(bot, interaction, err)
        return

    async def open_table(inter: discord.Interaction, amount: int) -> None:
        holder: dict[str, LobbyView] = {}

        async def announce(lobby: Lobby) -> int:
            channel = inter.channel
            assert channel is not None
            current = await bot.db.get_multiplayer_channel(lobby.channel_id)
            if current is not None and current.table_message_id:
                await delete_message_quietly(channel, current.table_message_id)
            view = LobbyView(bot, lobby)
            message = await channel.send(  # type: ignore[union-attr]
                embed=view.build_embed(), view=view, allowed_mentions=NO_MENTIONS
            )
            view.message = message
            holder["view"] = view
            return message.id

        try:
            await bot.tables.open_table(channel_id, row.game, user_id, amount, announce)
        except BaseException:
            # The stake is refunded by open_table; take down a lobby message that was already up.
            posted = holder.get("view")
            if posted is not None and posted.message is not None and inter.channel is not None:
                await delete_message_quietly(inter.channel, posted.message.id)
            raise
        holder["view"].start()
        await dismiss(inter)

    await interaction.response.send_modal(StakeModal(bot, f"Open a {spec.label} table", open_table))


class PlayView(discord.ui.View):
    """The always-on Play button under a table channel's embed."""

    def __init__(self, bot: YokaiBot) -> None:
        super().__init__(timeout=None)
        self.bot = bot

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return not await screen(interaction)

    @discord.ui.button(label="Play", style=discord.ButtonStyle.success, custom_id=PLAY_CUSTOM_ID)
    async def play_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await handle_play(self.bot, interaction)


class LobbyView(discord.ui.View):
    """Join / Leave / (Start) / Cancel for one open table; ends when the game starts or closes."""

    def __init__(self, bot: YokaiBot, lobby: Lobby) -> None:
        super().__init__(timeout=None)
        self.bot = bot
        self.lobby = lobby
        self.message: Optional[discord.Message] = None
        self._window: Optional[asyncio.Task[None]] = None
        if lobby.spec.min_players == lobby.spec.max_players:
            self.remove_item(self.start_button)  # nothing to start early on a full-or-nothing game

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return not await screen(interaction)

    def build_embed(self) -> discord.Embed:
        lobby = self.lobby
        return EmbedFactory.table_lobby(
            game_label=lobby.spec.label,
            seats=[(seat.user_id, seat.stake) for seat in lobby.seats.values()],
            min_players=lobby.spec.min_players,
            max_players=lobby.spec.max_players,
            closes_ts=int(lobby.closes_at),
            pot=lobby.pot,
            bot_avatar_url=avatar_url(self.bot),
        )

    def start(self) -> None:
        """Begin counting down the join window."""
        self._window = asyncio.create_task(self._run_window())

    # ---- window and start ----------------------------------------------------------------

    async def _run_window(self) -> None:
        try:
            await asyncio.sleep(max(0.0, self.lobby.closes_at - self.bot.tables.clock()))
            await self._begin()
        except Exception:
            # Nothing awaits this task, so log here and make sure nobody's stake is left behind.
            logger.exception("The join window of table %s failed", self.lobby.id)
            await self._cancel_after_failure()

    async def _cancel_after_failure(self) -> None:
        await self.bot.tables.abort(self.lobby)
        self.stop()
        await self._show(
            EmbedFactory.table_notice(
                "Table cancelled",
                "Something went wrong. Every stake was refunded.",
                bot_avatar_url=avatar_url(self.bot),
            )
        )

    def _stop_window(self) -> None:
        task, self._window = self._window, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    async def _begin(self) -> None:
        await self._after_begin(await self.bot.tables.begin(self.lobby))

    async def _after_begin(self, outcome: BeginOutcome) -> None:
        if outcome is BeginOutcome.IGNORED:
            return
        self._stop_window()
        self.stop()
        if outcome is BeginOutcome.CANCELLED:
            await self._show(
                EmbedFactory.table_notice(
                    "Table cancelled",
                    "Not enough players joined in time. Every stake was refunded.",
                    bot_avatar_url=avatar_url(self.bot),
                )
            )
            return
        assert self.message is not None
        runner = GAME_RUNNERS.get(self.lobby.spec.name)
        if runner is None:
            # A playable game without a runner is a bug; never leave the stakes escrowed.
            logger.error("No runner is registered for %s", self.lobby.spec.name)
            await self.bot.tables.abort(self.lobby)
            await tell_cancelled(self.bot, self.message)
            return
        await runner(self.bot, self.lobby, self.message)

    async def _show(self, embed: discord.Embed) -> None:
        """Replace the table message with `embed` and no buttons."""
        if self.message is None:
            return
        try:
            await self.message.edit(embed=embed, view=None)
        except discord.HTTPException as exc:
            logger.warning("Could not update table message: %s", exc)

    async def _update_message(self) -> None:
        if self.message is None:
            return
        try:
            await self.message.edit(embed=self.build_embed(), view=self)
        except discord.HTTPException as exc:
            logger.warning("Could not refresh table message: %s", exc)

    # ---- buttons -------------------------------------------------------------------------

    async def _do_join(
        self, interaction: discord.Interaction, stake: Optional[int], *, from_modal: bool
    ) -> None:
        try:
            await self.bot.tables.join(self.lobby, interaction.user.id, stake)
        except YokaiError as err:
            await reply_error(self.bot, interaction, err)
            return
        if from_modal:
            await dismiss(interaction)
        await self._update_message()
        if self.lobby.is_full:
            await self._after_begin(await self.bot.tables.begin(self.lobby))

    @discord.ui.button(label="Join", style=discord.ButtonStyle.success)
    async def join_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        spec = self.lobby.spec
        if spec.equal_stakes:
            await interaction.response.defer()
            await self._do_join(interaction, None, from_modal=False)
            return

        async def on_stake(inter: discord.Interaction, amount: int) -> None:
            await self._do_join(inter, amount, from_modal=True)

        await interaction.response.send_modal(
            StakeModal(self.bot, f"Join the {spec.label} table", on_stake)
        )

    @discord.ui.button(label="Leave", style=discord.ButtonStyle.secondary)
    async def leave_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.defer()
        try:
            await self.bot.tables.leave(self.lobby, interaction.user.id)
        except YokaiError as err:
            await reply_error(self.bot, interaction, err)
            return
        await self._update_message()

    @discord.ui.button(label="Start", style=discord.ButtonStyle.primary)
    async def start_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.defer()
        try:
            outcome = await self.bot.tables.start_early(self.lobby, interaction.user.id)
        except YokaiError as err:
            await reply_error(self.bot, interaction, err)
            return
        await self._after_begin(outcome)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.defer()
        is_admin = await self.bot.moderation.is_admin(interaction.user.id)
        try:
            await self.bot.tables.cancel(self.lobby, interaction.user.id, is_admin=is_admin)
        except YokaiError as err:
            await reply_error(self.bot, interaction, err)
            return
        self._stop_window()
        self.stop()
        await self._show(
            EmbedFactory.table_notice(
                "Table cancelled",
                f"<@{interaction.user.id}> cancelled the table. Every stake was refunded.",
                bot_avatar_url=avatar_url(self.bot),
            )
        )
