"""The party lobby message: Join, Leave, Start and Cancel, with a state machine behind them."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Optional, Protocol

import discord

from yokai.errors import (
    LeaderCannotLeaveError,
    NotPartyLeaderError,
    YokaiError,
)
from yokai.rpg.models import MAX_PARTY
from yokai.rpg.party import Lobby, PartyMember
from yokai.ui.embeds import EmbedFactory, send
from yokai.ui.table_common import avatar_url
from yokai.ui.views import BaseView

if TYPE_CHECKING:
    from yokai.bot import YokaiBot

logger = logging.getLogger(__name__)

LOBBY_TIMEOUT_S = 180.0

OPEN, STARTING, CLOSED = "open", "starting", "closed"


class PartyHost(Protocol):
    """What the lobby needs from the cog: the rules and the launch.

    A protocol so the view can be tested with a fake and never imports the cog.
    """

    async def admit(self, user: discord.abc.User) -> tuple[PartyMember, Optional[str]]:
        """Check this user may take a seat (raises a YokaiError).

        Returns the seat and an optional private heads-up (for example worn gear).
        """
        ...

    async def rejected(self, lobby: Lobby) -> dict[int, YokaiError]:
        """Re-check every seated member with fresh data; return those who may no longer go."""
        ...

    async def launch(self, lobby: Lobby) -> discord.Thread:
        """Charge everyone and start the fight (raises a YokaiError and changes nothing)."""
        ...


class PartyLobbyView(BaseView):
    """Open to everyone to join; only the leader starts or cancels. Times out after 3 minutes."""

    def __init__(
        self,
        bot: YokaiBot,
        lobby: Lobby,
        host: PartyHost,
        *,
        timeout: float = LOBBY_TIMEOUT_S,
    ) -> None:
        super().__init__(allowed_user_ids=None, timeout=timeout)
        self.bot = bot
        self.lobby = lobby
        self.host = host
        self.state = OPEN
        self.expires_ts = int(time.time() + timeout)
        self._lock = asyncio.Lock()
        self.note: Optional[str] = None
        self._sync_buttons()

    # ------------------------------------------------------------------ rendering

    def render(self) -> discord.Embed:
        mission = self.lobby.mission
        return EmbedFactory.party_lobby(
            mission.name,
            mission.rank,
            mission.top_level,
            mission.attempt_cost,
            [(f"<@{m.user_id}>", m.rank, m.level) for m in self.lobby.members.values()],
            mission.min_party,
            MAX_PARTY,
            self.expires_ts,
            is_raid=mission.kind == "raid",
            note=self.note,
            bot_avatar_url=avatar_url(self.bot),
        )

    def _sync_buttons(self) -> None:
        open_ = self.state == OPEN
        self.join_button.disabled = not open_ or self.lobby.is_full
        self.leave_button.disabled = not open_
        self.start_button.disabled = not open_
        self.cancel_button.disabled = not open_

    def _closed_embed(self, title: str, text: str) -> discord.Embed:
        return EmbedFactory.info(
            title=title,
            description=text,
            context="Guild",
            bot_avatar_url=avatar_url(self.bot),
        )

    async def _fail(self, interaction: discord.Interaction, err: YokaiError) -> None:
        embed = EmbedFactory.error(
            message=err.message,
            title=getattr(err, "title", "That didn't work"),
            user_hint=getattr(err, "user_hint", None),
            context="Guild",
            bot_avatar_url=avatar_url(self.bot),
        )
        await send(interaction, embed=embed, ephemeral=True)

    async def _redraw(self, interaction: discord.Interaction) -> None:
        self._sync_buttons()
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=self.render(), view=self)
        else:
            await interaction.response.edit_message(embed=self.render(), view=self)

    # ------------------------------------------------------------------ buttons

    @discord.ui.button(label="Join", style=discord.ButtonStyle.success)
    async def join_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        async with self._lock:
            if self.state != OPEN:
                await interaction.response.defer()
                return
            try:
                member, heads_up = await self.host.admit(interaction.user)
                self.lobby.add(member)
            except YokaiError as err:
                await self._fail(interaction, err)
                return
            self.note = None
            await self._redraw(interaction)
        if heads_up:
            embed = EmbedFactory.warning(
                title="Heads up",
                description=heads_up,
                context="Guild",
                bot_avatar_url=avatar_url(self.bot),
            )
            await send(interaction, embed=embed, ephemeral=True)

    @discord.ui.button(label="Leave", style=discord.ButtonStyle.secondary)
    async def leave_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        async with self._lock:
            if self.state != OPEN:
                await interaction.response.defer()
                return
            try:
                if interaction.user.id == self.lobby.leader_id:
                    raise LeaderCannotLeaveError()
                self.lobby.remove(interaction.user.id)
            except YokaiError as err:
                await self._fail(interaction, err)
                return
            await self._redraw(interaction)

    @discord.ui.button(label="Start", style=discord.ButtonStyle.primary)
    async def start_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        async with self._lock:
            if self.state != OPEN:
                await interaction.response.defer()
                return
            try:
                if interaction.user.id != self.lobby.leader_id:
                    raise NotPartyLeaderError()
                self.lobby.check_ready()
            except YokaiError as err:
                await self._fail(interaction, err)
                return
            await interaction.response.defer()
            dropped = await self.host.rejected(self.lobby)
            if dropped:
                await self._drop(interaction, dropped)
                return
            self.state = STARTING
            self._sync_buttons()
            await interaction.edit_original_response(view=self)
            try:
                thread = await self.host.launch(self.lobby)
            except YokaiError as err:
                self.state = OPEN
                await self._redraw(interaction)
                await self._fail(interaction, err)
                return
            self.state = CLOSED
            self.stop()
            names = ", ".join(f"<@{uid}>" for uid in self.lobby.members)
            await interaction.edit_original_response(
                embed=self._closed_embed(
                    "Party set out", f"{names} have entered {thread.mention}."
                ),
                view=None,
            )

    async def _drop(self, interaction: discord.Interaction, dropped: dict[int, YokaiError]) -> None:
        """Remove members who can no longer go. The leader must press Start again."""
        lines = []
        for user_id, err in dropped.items():
            self.lobby.members.pop(user_id, None)
            lines.append(f"<@{user_id}>: {err.message}")
        if self.lobby.leader_id in dropped:
            self.state = CLOSED
            self.stop()
            text = "The leader can no longer take this mission.\n" + "\n".join(lines)
            await interaction.edit_original_response(
                embed=self._closed_embed("Lobby closed", text), view=None
            )
            return
        self.note = "Removed before starting:\n" + "\n".join(lines)
        await self._redraw(interaction)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        async with self._lock:
            if self.state != OPEN:
                await interaction.response.defer()
                return
            if interaction.user.id != self.lobby.leader_id:
                await self._fail(interaction, NotPartyLeaderError())
                return
            self.state = CLOSED
            self.stop()
            await interaction.response.edit_message(
                embed=self._closed_embed(
                    "Lobby cancelled", "The leader closed the lobby. Nobody was charged."
                ),
                view=None,
            )

    async def on_timeout(self) -> None:
        if self.state != OPEN:
            return
        self.state = CLOSED
        if self.message is None:
            return
        try:
            await self.message.edit(
                embed=self._closed_embed(
                    "Lobby expired", "Nobody started it in time. Nobody was charged."
                ),
                view=None,
            )
        except discord.HTTPException as exc:
            logger.debug("Could not close the expired lobby: %s", exc)
