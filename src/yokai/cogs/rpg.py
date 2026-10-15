"""Guild RPG slash commands: /guild join, board, mission, rank, leaderboard and wiki, and /card."""

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
    PromotionCooldownError,
    PromotionNotReadyError,
    PromotionStaleError,
    ThreadUnavailableError,
    TopRankError,
    YokaiError,
)
from yokai.rpg import guild
from yokai.rpg.content import Content
from yokai.rpg.fights import Entrant, launch_fight, launch_party_fight
from yokai.rpg.gear import compute_loadout, durability_percent
from yokai.rpg.models import SLOTS, Character, ItemInstance, Mission
from yokai.rpg.party import Lobby, PartyMember, check_member, screen_members
from yokai.rpg.runtime import RpgRuntime
from yokai.rpg.stats import format_stats, stats_at_level
from yokai.rpg.store import PromotionRequest
from yokai.rpg.wiki import SECTIONS, WikiSettings, build_wiki
from yokai.textutil import escape_md
from yokai.theme import SEP
from yokai.ui.embeds import EmbedFactory, send
from yokai.ui.party_views import LOBBY_TIMEOUT_S, PartyLobbyView
from yokai.ui.rpg_views import ConfirmView, GuildLeaderboardView, PromotionView
from yokai.ui.wiki_views import WikiView

if TYPE_CHECKING:
    from yokai.bot import YokaiBot

logger = logging.getLogger(__name__)

MAX_AUTOCOMPLETE = 25
WIKI_CHOICES = [app_commands.Choice(name=title, value=key) for key, title, _ in SECTIONS]
SORT_CHOICES = [
    app_commands.Choice(name="Rank", value="rank"),
    app_commands.Choice(name="Level", value="level"),
    app_commands.Choice(name="Battles won", value="wins"),
]


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


class _LobbyHost:
    """The cog's side of a party lobby: eligibility for joiners and the final launch."""

    def __init__(
        self,
        cog: RpgCog,
        channel: discord.TextChannel,
        mission: Mission,
        today: str,
        resets_ts: int,
    ) -> None:
        self.cog = cog
        self.channel = channel
        self.mission = mission
        self.today = today
        self.resets_ts = resets_ts
        self.users: dict[int, discord.abc.User] = {}

    def _rules(self) -> dict:  # type: ignore[type-arg]
        runtime = self.cog._runtime()
        return {
            "today": self.today,
            "base_attempts": self.cog.bot.config.rpg_base_attempts,
            "streak_rules": runtime.content.balance.streak,
            "voice_rules": guild.voice_rules_from_config(self.cog.bot.config),
            "resets_ts": self.resets_ts,
            "fighting": runtime.active_users,
        }

    async def admit(self, user: discord.abc.User) -> tuple[PartyMember, Optional[str]]:
        runtime = self.cog._runtime()
        character = await runtime.store.get_character(user.id)
        if character is None:
            raise NoCharacterError(own=True)
        check_member(runtime.content, character, self.mission, **self._rules())
        self.users[user.id] = user
        worn = self.cog._worn_gear_lines(
            runtime.content, await runtime.gear.equipped_items(user.id)
        )
        heads_up = ("Your gear is worn: " + "; ".join(worn) + ".") if worn else None
        member = PartyMember(user.id, _display_name(user), character.rank, character.level)
        return member, heads_up

    async def rejected(self, lobby: Lobby) -> dict[int, YokaiError]:
        runtime = self.cog._runtime()
        characters = {uid: await runtime.store.get_character(uid) for uid in lobby.members}
        _, rejected = screen_members(runtime.content, characters, self.mission, **self._rules())
        return rejected

    async def launch(self, lobby: Lobby) -> discord.Thread:
        runtime = self.cog._runtime()
        entrants = []
        for user_id, member in lobby.members.items():
            character = await runtime.store.get_character(user_id)
            if character is None:
                raise NoCharacterError(own=False)
            entrants.append(Entrant(self.users[user_id], member.name, character))
        return await launch_party_fight(
            bot=self.cog.bot,
            runtime=runtime,
            channel=self.channel,
            entrants=entrants,
            mission=self.mission,
            today=self.today,
            resets_ts=self.resets_ts,
            base_attempts=self.cog.bot.config.rpg_base_attempts,
            voice_rules=guild.voice_rules_from_config(self.cog.bot.config),
            turn_seconds=self.cog.bot.config.rpg_turn_seconds,
        )


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

    @staticmethod
    def _gear_rows(content: Content, equipped: list[ItemInstance]) -> list[tuple[str, str]]:
        """(slot label, "name · 80%") for every slot, with "empty" where nothing is worn."""
        by_slot = {item.equipped_slot: item for item in equipped}
        rows: list[tuple[str, str]] = []
        for slot in SLOTS:
            item = by_slot.get(slot)
            definition = content.items.get(item.content_id) if item else None
            if item is None:
                rows.append((slot.title(), "empty"))
            elif definition is None:
                rows.append((slot.title(), "retired item"))
            elif item.durability <= 0:
                rows.append((slot.title(), f"{definition.name}{SEP}broken"))
            else:
                percent = durability_percent(item.durability, definition.max_durability)
                rows.append((slot.title(), f"{definition.name}{SEP}{percent}%"))
        return rows

    @staticmethod
    def _worn_gear_lines(content: Content, equipped: list[ItemInstance]) -> list[str]:
        """Equipped items at or below 25% durability, for the warning before a mission."""
        lines: list[str] = []
        for item in equipped:
            definition = content.items.get(item.content_id)
            if definition is None:
                continue
            percent = durability_percent(item.durability, definition.max_durability)
            if percent <= 25:
                state = "broken (0%)" if percent == 0 else f"{percent}%"
                lines.append(f"{definition.name}: {state}")
        return lines

    async def _board_for(self, today: str, runtime: RpgRuntime) -> list[Mission]:
        content = runtime.content
        ids = await runtime.store.get_or_create_board(
            today, lambda: guild.draw_board(content, today)
        )
        return guild.board_missions(content, ids)

    def _attempts_text(self, character: Character, today: str, content: Content) -> str:
        status = guild.attempt_status(
            character,
            today,
            self.bot.config.rpg_base_attempts,
            content.balance.streak,
            guild.voice_rules_from_config(self.bot.config),
        )
        text = f"Attempts: **{status.remaining}/{status.allowance}** left today."
        if status.streak:
            bonus = f" (+{status.bonus} attempts)" if status.bonus else ""
            text += f" Streak: **{status.streak}** day(s){bonus}."
        if status.voice_bonus:
            text += f" Voice time: **+{status.voice_bonus}** attempts today."
        if status.voice_seconds_to_next is not None:
            minutes = max(1, -(-status.voice_seconds_to_next // 60))
            text += f" {minutes} min in a call with someone gives the next one."
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
                        f"Raid: {mission.name}" if mission.kind == "raid" else mission.name,
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
    @app_commands.describe(
        mission="Which mission to run",
        party="Open a lobby so others can join (raids always do)",
    )
    async def mission(
        self, interaction: discord.Interaction, mission: str, party: Optional[bool] = None
    ) -> None:
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
            character,
            today,
            self.bot.config.rpg_base_attempts,
            content.balance.streak,
            guild.voice_rules_from_config(self.bot.config),
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

        if chosen.kind == "raid" or party:
            await self._open_lobby(interaction, channel, chosen, character, today, resets_ts)
            return

        warnings = self._worn_gear_lines(content, await runtime.gear.equipped_items(user.id))
        if warnings:
            view = ConfirmView(
                self.bot,
                user.id,
                lambda confirmed: self._launch(
                    confirmed, channel, chosen, character, today, resets_ts
                ),
            )
            warning = EmbedFactory.gear_warning(warnings, bot_avatar_url=self._avatar_url())
            await send(interaction, embed=warning, view=view, ephemeral=True)
            return
        await self._launch(interaction, channel, chosen, character, today, resets_ts)

    async def _open_lobby(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        mission: Mission,
        character: Character,
        today: str,
        resets_ts: int,
    ) -> None:
        """Post a public lobby with the leader seated; nothing is charged until Start."""
        user = interaction.user
        lobby = Lobby(mission, leader_id=user.id)
        lobby.add(PartyMember(user.id, _display_name(user), character.rank, character.level))
        host = _LobbyHost(self, channel, mission, today, resets_ts)
        host.users[user.id] = user
        view = PartyLobbyView(self.bot, lobby, host)
        view.message = await send(interaction, embed=view.render(), view=view)

    async def _launch(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        mission: Mission,
        character: Character,
        today: str,
        resets_ts: int,
        promotion: Optional[PromotionRequest] = None,
    ) -> None:
        """Spend the attempts and start the fight. Defers first unless the click already did."""
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            thread = await launch_fight(
                bot=self.bot,
                runtime=self._runtime(),
                channel=channel,
                member=interaction.user,
                member_name=_display_name(interaction.user),
                character=character,
                mission=mission,
                today=today,
                resets_ts=resets_ts,
                base_attempts=self.bot.config.rpg_base_attempts,
                voice_rules=guild.voice_rules_from_config(self.bot.config),
                turn_seconds=self.bot.config.rpg_turn_seconds,
                promotion=promotion,
                promotion_cooldown_s=self.bot.config.rpg_promotion_cooldown_hours * 3600,
            )
        except YokaiError as err:
            await self._send_domain_error(interaction, err)
            return
        embed = EmbedFactory.info(
            title="Promotion trial started" if promotion else "Mission started",
            description=(
                f"Your fight against **{escape_md(mission.name)}** is under way in "
                f"{thread.mention}."
            ),
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
            kind = "raid, " if mission.kind == "raid" else ""
            label = f"{mission.name} ({kind}rank {mission.rank}, cost {mission.attempt_cost})"
            choices.append(app_commands.Choice(name=label[:100], value=mission.id))
        return choices[:MAX_AUTOCOMPLETE]

    # ------------------------------------------------------------------ /guild rank

    def _rank_lines(
        self, content: Content, character: Character, status: guild.PromotionStatus
    ) -> list[str]:
        if not status.known_rank:
            return ["Your rank is no longer in the guild's records. Ask an admin to set it."]
        if status.next_rank is None:
            return ["You hold the highest rank in the guild."]
        upcoming = status.next_rank
        trial = content.promotion_mission(upcoming.id)
        lines = [
            f"Next: **Rank {escape_md(upcoming.id)}**, which opens at level {upcoming.min_level}."
        ]
        if trial is not None:
            lines.append(
                f"Trial: *{escape_md(trial.name)}* ({trial.attempt_cost} attempts, "
                f"{len(trial.stages)} stage(s))."
            )
        if not status.level_ok:
            lines.append(f"You need {upcoming.min_level - character.level} more level(s).")
        elif status.cooldown_until is not None:
            lines.append(
                "You are recovering from your last trial. "
                f"You can try again <t:{status.cooldown_until}:R>."
            )
        else:
            hours = self.bot.config.rpg_promotion_cooldown_hours
            lines.append(
                f"You can challenge now. A win promotes you; a loss starts a {hours}-hour cooldown."
            )
        return lines

    @guild_group.command(name="rank", description="See your rank and challenge for the next one.")
    async def rank(self, interaction: discord.Interaction) -> None:
        """The rank screen is private; a Challenge button appears when you can promote."""
        runtime = self._runtime()
        character = await runtime.store.get_character(interaction.user.id)
        if character is None:
            await self._send_domain_error(interaction, NoCharacterError(own=True))
            return
        content = runtime.content
        status = guild.promotion_status(content, character, int(time.time()))
        embed = EmbedFactory.rank_status(
            character.rank,
            character.level,
            self._rank_lines(content, character, status),
            status.ready,
            bot_avatar_url=self._avatar_url(),
        )
        view: Optional[PromotionView] = None
        if status.ready and status.next_rank is not None:
            view = PromotionView(
                self.bot, interaction.user.id, status.next_rank.id, self._start_promotion
            )
        if view is None:
            await send(interaction, embed=embed, ephemeral=True)
        else:
            await send(interaction, embed=embed, view=view, ephemeral=True)

    async def _start_promotion(self, interaction: discord.Interaction) -> None:
        """The Challenge button: re-check everything with fresh data, then start the trial."""
        runtime = self._runtime()
        content = runtime.content
        user = interaction.user
        try:
            character = await runtime.store.get_character(user.id)
            if character is None:
                raise NoCharacterError(own=True)
            status = guild.promotion_status(content, character, int(time.time()))
            if not status.known_rank:
                raise PromotionStaleError()
            upcoming = status.next_rank
            if upcoming is None:
                raise TopRankError()
            if not status.level_ok:
                raise PromotionNotReadyError(upcoming.id, upcoming.min_level, character.level)
            if status.cooldown_until is not None:
                raise PromotionCooldownError(status.cooldown_until)
            trial = content.promotion_mission(upcoming.id)
            if trial is None:
                raise PromotionStaleError()
            today, resets_ts = self._day()
            attempts = guild.attempt_status(
                character,
                today,
                self.bot.config.rpg_base_attempts,
                content.balance.streak,
                guild.voice_rules_from_config(self.bot.config),
            )
            if trial.attempt_cost > attempts.remaining:
                raise NoAttemptsError(attempts.remaining, trial.attempt_cost, resets_ts)
            if user.id in runtime.active_users:
                raise AlreadyFightingError()
            channel = interaction.channel
            if not isinstance(channel, discord.TextChannel):
                raise ThreadUnavailableError()
        except YokaiError as err:
            await self._send_domain_error(interaction, err)
            return
        request = PromotionRequest(character.rank, upcoming.id, upcoming.min_level)
        warnings = self._worn_gear_lines(content, await runtime.gear.equipped_items(user.id))
        if warnings:
            view = ConfirmView(
                self.bot,
                user.id,
                lambda confirmed: self._launch(
                    confirmed, channel, trial, character, today, resets_ts, request
                ),
            )
            warning = EmbedFactory.gear_warning(warnings, bot_avatar_url=self._avatar_url())
            await send(interaction, embed=warning, view=view, ephemeral=True)
            return
        await self._launch(interaction, channel, trial, character, today, resets_ts, request)

    # ------------------------------------------------------------------ /guild leaderboard

    @guild_group.command(name="leaderboard", description="See the guild standings.")
    @app_commands.describe(sort="What to rank by (default: rank)", page="Page to open")
    @app_commands.choices(sort=SORT_CHOICES)
    async def leaderboard(
        self,
        interaction: discord.Interaction,
        sort: Optional[app_commands.Choice[str]] = None,
        page: Optional[int] = None,
    ) -> None:
        """Open to everyone; a dropdown switches the sort and buttons turn the pages."""
        runtime = self._runtime()
        await interaction.response.defer(thinking=True)
        view = GuildLeaderboardView(
            self.bot, runtime, sort.value if sort else "rank", page if page and page > 0 else 1
        )
        embed = await view.build_embed()
        view.message = await send(interaction, embed=embed, view=view)

    # ------------------------------------------------------------------ /guild wiki

    @guild_group.command(name="wiki", description="Read the guild guide: commands, loot and more.")
    @app_commands.describe(section="Jump straight to a section (defaults to the contents)")
    @app_commands.choices(section=WIKI_CHOICES)
    async def wiki(
        self,
        interaction: discord.Interaction,
        section: Optional[app_commands.Choice[str]] = None,
    ) -> None:
        """A private, paginated guide built from the live content files."""
        runtime = self._runtime()
        config = self.bot.config
        built = build_wiki(
            runtime.content,
            WikiSettings(
                base_attempts=config.rpg_base_attempts,
                turn_seconds=int(config.rpg_turn_seconds),
                promotion_cooldown_hours=config.rpg_promotion_cooldown_hours,
                shop_daily_cap=config.rpg_shop_daily_cap,
                timezone=config.rpg_tz,
                lobby_minutes=int(LOBBY_TIMEOUT_S // 60),
                voice_minutes_per_attempt=config.rpg_voice_minutes_per_attempt,
                voice_max_bonus=config.rpg_voice_max_bonus,
            ),
        )
        start = built.first_page.get(section.value, 0) if section else 0
        view = WikiView(self.bot, built, interaction.user.id, start)
        view.message = await send(interaction, embed=view.render(), view=view, ephemeral=True)

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
        equipped = await runtime.gear.equipped_items(target.id)
        loadout = compute_loadout(content, character.level, equipped)
        next_rank, top_rank = _promotion(content, character.rank)
        status = guild.promotion_status(content, character, int(time.time()))
        note: Optional[str] = None
        if status.ready:
            note = "ready: use /guild rank"
        elif status.cooldown_until is not None and status.level_ok:
            note = f"recovering, try again <t:{status.cooldown_until}:R>"
        embed = EmbedFactory.player_card(
            user_mention=f"<@{target.id}>",
            rank=character.rank,
            level=character.level,
            level_cap=content.levels.cap,
            stats=format_stats(loadout.stats),
            wins=character.battles_won,
            losses=character.battles_lost,
            joined_ts=character.created_ts,
            next_rank=next_rank,
            top_rank=top_rank,
            thumbnail_url=_avatar_of(target),
            bot_avatar_url=self._avatar_url(),
            gear=self._gear_rows(content, equipped),
            bonus_lines=loadout.bonus_lines,
            promotion_note=note,
            xp=(
                (character.xp, content.balance.xp.xp_to_next(character.level))
                if character.level < content.levels.cap
                else None
            ),
        )
        await send(interaction, embed=embed)


async def setup(bot: YokaiBot) -> None:
    """Extension setup entrypoint."""
    await bot.add_cog(RpgCog(bot))
