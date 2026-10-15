"""Guild RPG slash commands: /guild join, board and mission, and /card."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

from yokai.challenges.periods import load_tz
from yokai.errors import (
    AlreadyFightingError,
    MissionNotAvailableError,
    MissionRankError,
    NoAttemptsError,
    NoCharacterError,
    ThreadUnavailableError,
    YokaiError,
)
from yokai.rpg import guild
from yokai.rpg.content import Content
from yokai.rpg.fights import launch_fight
from yokai.rpg.models import Character, Mission
from yokai.rpg.runtime import RpgRuntime
from yokai.rpg.stats import format_stats, stats_at_level
from yokai.ui.embeds import EmbedFactory, send

if TYPE_CHECKING:
    from yokai.bot import YokaiBot

logger = logging.getLogger(__name__)

MAX_AUTOCOMPLETE = 25


def _avatar_of(member: discord.abc.User) -> Optional[str]:
    """The member's avatar URL, or None when it is not a plain string (tests, odd objects)."""
    url = getattr(getattr(member, "display_avatar", None), "url", None)
    return url if isinstance(url, str) else None


def _display_name(user: discord.abc.User) -> str:
    name = getattr(user, "display_name", None) or getattr(user, "name", None)
    return name if isinstance(name, str) and name.strip() else f"Adventurer {user.id % 10000}"


def _promotion(content: Content, rank_id: str) -> tuple[Optional[tuple[str, int]], bool]:
    """(next rank as (id, min level), reached the top). Both empty if the rank is unknown."""
    try:
        upcoming = content.next_rank(rank_id)
    except KeyError:
        logger.warning("A character has rank %r, which is not in the content files.", rank_id)
        return None, False
    if upcoming is None:
        return None, True
    return (upcoming.id, upcoming.min_level), False


class RpgCog(commands.Cog, name="Guild"):
    """The adventurers' guild: join, see the board, take missions, view player cards."""

    guild_group = app_commands.Group(name="guild", description="The adventurers' guild.")

    def __init__(self, bot: YokaiBot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------ helpers

    def _avatar_url(self) -> Optional[str]:
        return str(self.bot.user.display_avatar.url) if self.bot.user else None

    def _runtime(self) -> RpgRuntime:
        runtime = self.bot.rpg
        if runtime is None:
            raise RuntimeError("The Guild cog was loaded without an RPG runtime.")
        return runtime

    def _day(self) -> tuple[str, int]:
        """(today's guild day key, Unix time of the next rollover)."""
        tz = load_tz(self.bot.config.rpg_tz)
        now = int(time.time())
        return guild.day_key(now, tz), guild.next_day_ts(now, tz)

    async def _send_domain_error(self, interaction: discord.Interaction, err: Exception) -> None:
        title = getattr(err, "title", "That didn't work")
        message = getattr(err, "message", str(err))
        hint = getattr(err, "user_hint", None)
        embed = EmbedFactory.error(
            message=message,
            title=title,
            user_hint=hint,
            context="Guild",
            bot_avatar_url=self._avatar_url(),
        )
        await send(interaction, embed=embed, ephemeral=True)

    async def _board_for(self, today: str, runtime: RpgRuntime) -> list[Mission]:
        content = runtime.content
        ids = await runtime.store.get_or_create_board(
            today, lambda: guild.draw_board(content, today)
        )
        return guild.board_missions(content, ids)

    def _attempts_text(self, character: Character, today: str, content: Content) -> str:
        status = guild.attempt_status(
            character, today, self.bot.config.rpg_base_attempts, content.balance.streak
        )
        text = f"Attempts: **{status.remaining}/{status.allowance}** left today."
        if status.streak:
            bonus = f" (+{status.bonus} attempts)" if status.bonus else ""
            text += f" Streak: **{status.streak}** day(s){bonus}."
        return text

    # ------------------------------------------------------------------ /guild join

    @guild_group.command(name="join", description="Join the adventurers' guild.")
    async def join(self, interaction: discord.Interaction) -> None:
        """Joining twice is answered before deferring so it stays ephemeral."""
        runtime = self._runtime()
        user_id = interaction.user.id
        if await runtime.store.get_character(user_id) is not None:
            embed = EmbedFactory.info(
                title="Already on the roster",
                description="You've already joined the guild. Use /card to see your card.",
                context="Guild",
                bot_avatar_url=self._avatar_url(),
            )
            await send(interaction, embed=embed, ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        character, _ = await runtime.store.create_character(user_id, runtime.content.ranks[0].id)
        stats = stats_at_level(runtime.content.levels, character.level)
        embed = EmbedFactory.guild_joined(
            user_mention=f"<@{user_id}>",
            rank=character.rank,
            level=character.level,
            stats=format_stats(stats),
            bot_avatar_url=self._avatar_url(),
        )
        await send(interaction, embed=embed)

    # ------------------------------------------------------------------ /guild board

    @guild_group.command(name="board", description="See today's missions and your attempts.")
    async def board(self, interaction: discord.Interaction) -> None:
        """The board is shown privately: it includes your own attempts and rewards."""
        runtime = self._runtime()
        character = await runtime.store.get_character(interaction.user.id)
        if character is None:
            await self._send_domain_error(interaction, NoCharacterError(own=True))
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        content = runtime.content
        today, resets_ts = self._day()
        missions = await self._board_for(today, runtime)
        groups = []
        for rank in content.ranks:
            rows = []
            is_open = False
            for mission in (m for m in missions if m.rank == rank.id):
                is_open = guild.can_enter(content, character.rank, mission)
                reward = (
                    guild.mission_reward(content, mission, character.rank)
                    if is_open
                    else guild.Reward(0, 0)
                )
                rows.append(
                    (
                        mission.name,
                        mission.attempt_cost,
                        mission.top_level,
                        reward.xp,
                        reward.renown,
                    )
                )
            if rows:
                groups.append((rank.id, is_open, rows))
        embed = EmbedFactory.guild_board(
            groups,
            character.rank,
            self._attempts_text(character, today, content),
            resets_ts,
            bot_avatar_url=self._avatar_url(),
        )
        await send(interaction, embed=embed, ephemeral=True)

    # ------------------------------------------------------------------ /guild mission

    @guild_group.command(name="mission", description="Take a mission from today's board.")
    @app_commands.describe(mission="Which mission to run")
    async def mission(self, interaction: discord.Interaction, mission: str) -> None:
        """Everything that can be refused is checked before deferring, so refusals are private."""
        runtime = self._runtime()
        content = runtime.content
        user = interaction.user
        character = await runtime.store.get_character(user.id)
        if character is None:
            await self._send_domain_error(interaction, NoCharacterError(own=True))
            return

        today, resets_ts = self._day()
        board = await self._board_for(today, runtime)
        chosen = next((m for m in board if m.id == mission), None)
        if chosen is None:
            await self._send_domain_error(interaction, MissionNotAvailableError())
            return
        if not guild.can_enter(content, character.rank, chosen):
            await self._send_domain_error(
                interaction, MissionRankError(chosen.rank, character.rank)
            )
            return
        status = guild.attempt_status(
            character, today, self.bot.config.rpg_base_attempts, content.balance.streak
        )
        if chosen.attempt_cost > status.remaining:
            await self._send_domain_error(
                interaction, NoAttemptsError(status.remaining, chosen.attempt_cost, resets_ts)
            )
            return
        if user.id in runtime.active_users:
            await self._send_domain_error(interaction, AlreadyFightingError())
            return
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await self._send_domain_error(interaction, ThreadUnavailableError())
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            thread = await launch_fight(
                bot=self.bot,
                runtime=runtime,
                channel=channel,
                member=user,
                member_name=_display_name(user),
                character=character,
                mission=chosen,
                today=today,
                resets_ts=resets_ts,
                base_attempts=self.bot.config.rpg_base_attempts,
                turn_seconds=self.bot.config.rpg_turn_seconds,
            )
        except YokaiError as err:
            await self._send_domain_error(interaction, err)
            return
        embed = EmbedFactory.info(
            title="Mission started",
            description=f"Your fight against **{chosen.name}** is under way in {thread.mention}.",
            context="Guild",
            bot_avatar_url=self._avatar_url(),
        )
        await send(interaction, embed=embed, ephemeral=True)

    @mission.autocomplete("mission")
    async def mission_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Today's missions the member can enter, filtered by what they have typed."""
        runtime = self._runtime()
        character = await runtime.store.get_character(interaction.user.id)
        if character is None:
            return []
        content = runtime.content
        today, _ = self._day()
        ids = await runtime.store.get_board(today) or guild.draw_board(content, today)
        needle = current.strip().casefold()
        choices: list[app_commands.Choice[str]] = []
        for mission in guild.board_missions(content, ids):
            if not guild.can_enter(content, character.rank, mission):
                continue
            if needle and needle not in mission.name.casefold():
                continue
            label = f"{mission.name} (rank {mission.rank}, cost {mission.attempt_cost})"
            choices.append(app_commands.Choice(name=label[:100], value=mission.id))
        return choices[:MAX_AUTOCOMPLETE]

    # ------------------------------------------------------------------ /card

    @app_commands.command(name="card", description="Show an adventurer's player card.")
    @app_commands.describe(member="Whose card to show (defaults to you)")
    async def card(
        self, interaction: discord.Interaction, member: Optional[discord.Member] = None
    ) -> None:
        """Anyone can view anyone's card. Missing characters are answered before deferring."""
        runtime = self._runtime()
        target = member or interaction.user
        character = None if target.bot else await runtime.store.get_character(target.id)
        if character is None:
            await self._send_domain_error(
                interaction, NoCharacterError(own=target.id == interaction.user.id)
            )
            return

        await interaction.response.defer(thinking=True)
        content = runtime.content
        next_rank, top_rank = _promotion(content, character.rank)
        embed = EmbedFactory.player_card(
            user_mention=f"<@{target.id}>",
            rank=character.rank,
            level=character.level,
            level_cap=content.levels.cap,
            stats=format_stats(stats_at_level(content.levels, character.level)),
            wins=character.battles_won,
            losses=character.battles_lost,
            joined_ts=character.created_ts,
            next_rank=next_rank,
            top_rank=top_rank,
            thumbnail_url=_avatar_of(target),
            bot_avatar_url=self._avatar_url(),
        )
        await send(interaction, embed=embed)


async def setup(bot: YokaiBot) -> None:
    """Extension setup entrypoint."""
    await bot.add_cog(RpgCog(bot))
