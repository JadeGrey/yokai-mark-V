"""Runs a guild fight in a Discord thread: rounds, timers, away players and settlement."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Optional

import discord

from yokai.errors import AlreadyFightingError, ThreadUnavailableError
from yokai.rpg import guild
from yokai.rpg.combat.builders import build_mission_stages, player_combatant
from yokai.rpg.combat.describe import describe_all
from yokai.rpg.combat.engine import Fight
from yokai.rpg.combat.models import OUTCOME_WON
from yokai.rpg.gear import Loadout, compute_loadout, durability_percent, roll_drop, roll_stats
from yokai.rpg.gear_store import GearSettlement, ItemDrop
from yokai.rpg.models import Character, Mission
from yokai.rpg.runtime import RpgRuntime
from yokai.rpg.stats import stats_at_level
from yokai.rpg.store import PromotionRequest
from yokai.theme import SEP
from yokai.ui.embeds import EmbedFactory
from yokai.ui.rpg_views import FightView
from yokai.ui.table_common import NO_MENTIONS, avatar_url

if TYPE_CHECKING:
    from yokai.bot import YokaiBot

logger = logging.getLogger(__name__)

AWAY_AFTER_MISSED_ROUNDS = 2
THREAD_ARCHIVE_MINUTES = 60


@dataclass(slots=True)
class Participant:
    """A member in a fight."""

    user_id: int
    name: str
    character: Character
    loadout: Optional[Loadout] = None


class FightRunner:
    """Drives one `Fight` from a thread: posts the live message, waits for choices, settles."""

    def __init__(
        self,
        *,
        bot: YokaiBot,
        runtime: RpgRuntime,
        fight_id: int,
        mission: Mission,
        participants: list[Participant],
        thread: discord.Thread,
        turn_seconds: float,
        rng: Optional[random.Random] = None,
        clock: Callable[[], float] = time.time,
        idle_delay: float = 1.5,
        archive_delay: float = 20.0,
        promotion_to: Optional[str] = None,
        promotion_cooldown_s: int = 0,
    ) -> None:
        self.bot = bot
        self.promotion_to = promotion_to
        self.promotion_cooldown_s = promotion_cooldown_s
        self.runtime = runtime
        self.fight_id = fight_id
        self.mission = mission
        self.participants = participants
        self.thread = thread
        self.turn_seconds = turn_seconds
        self.rng = rng or random.Random()
        self.clock = clock
        self.idle_delay = idle_delay
        self.archive_delay = archive_delay
        content = runtime.content
        self.content = content
        combatants = [
            player_combatant(
                p.user_id,
                p.name,
                p.loadout.stats if p.loadout else stats_at_level(content.levels, p.character.level),
                p.loadout.skills if p.loadout else (),
            )
            for p in participants
        ]
        stages = build_mission_stages(content, mission, len(participants))
        self.fight = Fight(combatants, stages, content.skills, content.balance.combat, self.rng)
        self.missed: dict[int, int] = {p.user_id: 0 for p in participants}
        self.last_log: list[str] = []
        self.message: Optional[discord.Message] = None
        self.view: Optional[FightView] = None
        self._edit_lock = asyncio.Lock()
        self._settled = False

    # ------------------------------------------------------------------ the run

    async def run(self) -> None:
        """Play the fight to the end. Any failure before settlement refunds the attempts."""
        try:
            while not self.fight.finished:
                await self._play_round()
            await self._finish()
        except asyncio.CancelledError:
            await self._abort("The fight was interrupted.")
            raise
        except Exception:
            logger.exception("Guild fight %d failed", self.fight_id)
            await self._abort("Something went wrong, so the fight was cancelled.")
        finally:
            self._release()
        await self._archive_later()

    def _release(self) -> None:
        for participant in self.participants:
            self.runtime.active_users.discard(participant.user_id)

    async def _abort(self, reason: str) -> None:
        """Cancel the fight and refund the attempts, unless it was already settled."""
        if self._settled:
            return
        self._settled = True
        try:
            await asyncio.shield(self.runtime.store.abort_fight(self.fight_id))
        except Exception:
            logger.exception("Could not refund the attempts of fight %d", self.fight_id)
        notice = EmbedFactory.warning(
            title="Fight cancelled",
            description=f"{reason} Your attempts were refunded.",
            context="Guild",
            bot_avatar_url=avatar_url(self.bot),
        )
        try:
            await self._edit(embed=notice, view=None)
        except Exception as exc:  # best effort: the refund above is what matters
            logger.warning(
                "Could not tell the thread fight %d was cancelled: %s", self.fight_id, exc
            )

    async def _archive_later(self) -> None:
        await asyncio.sleep(self.archive_delay)
        with contextlib.suppress(discord.HTTPException):
            await self.thread.edit(archived=True, locked=True)

    # ------------------------------------------------------------------ one round

    async def _play_round(self) -> None:
        fight = self.fight
        waiting_for = {
            p.user_id
            for p in fight.awaiting()
            if p.user_id is not None and self.missed[p.user_id] < AWAY_AFTER_MISSED_ROUNDS
        }
        view = FightView(
            bot=self.bot,
            fight=fight,
            player_ids={p.user_id for p in self.participants},
            waiting_for=waiting_for,
            refresh=self._refresh,
        )
        self.view = view
        deadline = int(self.clock() + self.turn_seconds) if waiting_for else None
        await self._edit(embed=self._round_embed(deadline), view=view)

        timed_out = False
        if waiting_for:
            try:
                await asyncio.wait_for(view.all_chosen.wait(), self.turn_seconds)
            except asyncio.TimeoutError:
                timed_out = True
        else:
            await asyncio.sleep(self.idle_delay)
        view.close()

        for participant in self.participants:
            combatant = self._combatant(participant.user_id)
            if combatant is None or not combatant.alive:
                continue
            if fight.has_chosen(combatant.id):
                self.missed[participant.user_id] = 0
            elif timed_out and participant.user_id in waiting_for:
                self.missed[participant.user_id] += 1
        events = fight.resolve_round()
        self.last_log = describe_all(events)

    def _combatant(self, user_id: int):  # type: ignore[no-untyped-def]
        return next((p for p in self.fight.players if p.user_id == user_id), None)

    async def _refresh(self) -> None:
        """Re-draw the live message (for example to show who has locked in)."""
        if self.view is None or self.view.closed:
            return
        deadline = int(self.clock() + self.turn_seconds)
        with contextlib.suppress(discord.HTTPException):
            await self._edit(embed=self._round_embed(deadline), view=self.view)

    # ------------------------------------------------------------------ the message

    def _round_embed(self, deadline_ts: Optional[int]) -> discord.Embed:
        fight = self.fight
        players = []
        for participant in self.participants:
            combatant = self._combatant(participant.user_id)
            if combatant is None:
                continue
            if not combatant.alive:
                state = "down"
            elif fight.has_chosen(combatant.id):
                state = "ready"
            elif self.missed[participant.user_id] >= AWAY_AFTER_MISSED_ROUNDS:
                state = "away"
            else:
                state = "waiting"
            players.append(
                (
                    combatant.name,
                    combatant.hp,
                    combatant.max_hp,
                    [f"{s.name} {s.remaining}" for s in combatant.statuses],
                    state,
                )
            )
        foes = [
            (f.name, f.hp, f.max_hp, [f"{s.name} {s.remaining}" for s in f.statuses])
            for f in fight.foes
        ]
        return EmbedFactory.fight_round(
            self.mission.name,
            f"Stage {fight.stage_index + 1} of {fight.stage_count}",
            fight.round_no,
            players,
            foes,
            self.last_log,
            deadline_ts=deadline_ts,
            bot_avatar_url=avatar_url(self.bot),
        )

    async def _edit(self, *, embed: discord.Embed, view: Optional[discord.ui.View]) -> None:
        """Post the live message on first use, then edit it. Edits are serialised."""
        async with self._edit_lock:
            if self.message is None:
                self.message = await self.thread.send(
                    embed=embed,
                    view=view if view is not None else discord.utils.MISSING,
                    allowed_mentions=NO_MENTIONS,
                )
            else:
                await self.message.edit(embed=embed, view=view)

    # ------------------------------------------------------------------ the end

    async def _finish(self) -> None:
        fight = self.fight
        won = fight.outcome == OUTCOME_WON
        rewards = {p.user_id: self._reward_for(p) for p in self.participants} if won else {}
        settlements = await self.runtime.store.settle_fight(
            self.fight_id,
            won=won,
            rewards=rewards,
            level_cap=self.content.levels.cap,
            xp_curve=self.content.balance.xp,
            welcome_bonus=self.bot.config.economy_welcome_bonus,
            gear=self._gear_settlement(won),
            promotion_cooldown_s=self.promotion_cooldown_s,
        )
        self._settled = True
        if settlements is None:
            return
        rows = [
            (f"<@{uid}>", s.xp_gained, s.renown_gained, s.levels_gained)
            for uid, s in settlements.items()
        ]
        loot_lines, gear_lines = self._gear_report(settlements)
        cooldowns = [s.cooldown_until for s in settlements.values() if s.cooldown_until]
        embed = EmbedFactory.fight_result(
            won,
            self.mission.name,
            fight.round_no - 1,
            rows,
            self.last_log,
            loot_lines=loot_lines,
            gear_lines=gear_lines,
            bot_avatar_url=avatar_url(self.bot),
            promotion_to=self.promotion_to,
            cooldown_ts=cooldowns[0] if cooldowns else None,
        )
        try:
            await self._edit(embed=embed, view=None)
        except discord.HTTPException as exc:
            logger.warning("Could not show the result of fight %d: %s", self.fight_id, exc)

    def _reward_for(self, participant: Participant) -> guild.Reward:
        """Mission rewards; a promotion trial pays Renown only (no XP)."""
        reward = guild.mission_reward(self.content, self.mission, participant.character.rank)
        if self.promotion_to is not None:
            return guild.Reward(0, reward.renown)
        return reward

    def _gear_settlement(self, won: bool) -> GearSettlement:
        """Wear for everyone, plus one drop roll per member on a win (not for a promotion)."""
        rules = self.content.balance.gear
        drops: dict[int, list[ItemDrop]] = {}
        if won and self.promotion_to is None:
            for participant in self.participants:
                definition = roll_drop(self.content, self.mission, self.rng)
                if definition is not None:
                    drops[participant.user_id] = [
                        ItemDrop(
                            definition.id,
                            roll_stats(definition, rules, self.rng),
                            definition.max_durability,
                        )
                    ]
        return GearSettlement(items=self.content.items, rules=rules, drops=drops)

    def _gear_report(self, settlements: dict) -> tuple[list[str], list[str]]:  # type: ignore[type-arg]
        """Loot lines and gear-warning lines for the result embed."""
        items = self.content.items
        loot: list[str] = []
        warnings: list[str] = []
        for user_id, settled in settlements.items():
            mention = f"<@{user_id}>"
            for drop in settled.drops:
                definition = items.get(drop.content_id)
                name = definition.name if definition else drop.content_id
                rarity = definition.rarity if definition else "?"
                if drop.sold_for is not None:
                    loot.append(
                        f"{mention}{SEP}{name} ({rarity}) sold for {drop.sold_for:,} Renown "
                        "(inventory full)"
                    )
                else:
                    loot.append(f"{mention}{SEP}**{name}** ({rarity})")
            for wear in settled.wear:
                if not wear.crossed:
                    continue
                definition = items.get(wear.content_id)
                name = definition.name if definition else wear.content_id
                if wear.after <= 0:
                    warnings.append(f"{mention}{SEP}{name} is broken (0%) and counts for nothing")
                else:
                    percent = durability_percent(wear.after, wear.max_durability)
                    warnings.append(f"{mention}{SEP}{name} is at {percent}%")
        return loot, warnings


# ---------------------------------------------------------------------------- launching


@dataclass(frozen=True, slots=True)
class Entrant:
    """A member about to fight: the Discord user, the name to show and their character."""

    user: discord.abc.User
    name: str
    character: Character


async def launch_fight(
    *,
    bot: YokaiBot,
    runtime: RpgRuntime,
    channel: discord.TextChannel,
    member: discord.abc.User,
    member_name: str,
    character: Character,
    mission: Mission,
    today: str,
    resets_ts: int,
    base_attempts: int,
    turn_seconds: float,
    promotion: Optional[PromotionRequest] = None,
    promotion_cooldown_s: int = 0,
) -> discord.Thread:
    """Spend the attempts, open a thread and start a solo fight task.

    With `promotion` the fight is a rank trial: the request is validated atomically with the
    attempt spend, and settlement promotes or starts the cooldown.

    Raises NoAttemptsError or AlreadyFightingError (nothing changes), or ThreadUnavailableError
    (the attempts are refunded).
    """
    return await _start_fight(
        bot=bot,
        runtime=runtime,
        channel=channel,
        entrants=[Entrant(member, member_name, character)],
        thread_name=f"{mission.name} - {member_name}",
        mission=mission,
        today=today,
        resets_ts=resets_ts,
        base_attempts=base_attempts,
        turn_seconds=turn_seconds,
        promotion=promotion,
        promotion_cooldown_s=promotion_cooldown_s,
    )


async def launch_party_fight(
    *,
    bot: YokaiBot,
    runtime: RpgRuntime,
    channel: discord.TextChannel,
    entrants: Sequence[Entrant],
    mission: Mission,
    today: str,
    resets_ts: int,
    base_attempts: int,
    turn_seconds: float,
) -> discord.Thread:
    """Charge every member's attempts at once, open one thread and start the shared fight.

    The first entrant is the leader. It is all or nothing: if any member cannot pay (or is
    already fighting) nothing changes and the matching error is raised; if the thread cannot
    be created everyone is refunded.
    """
    leader = entrants[0]
    return await _start_fight(
        bot=bot,
        runtime=runtime,
        channel=channel,
        entrants=entrants,
        thread_name=f"{mission.name} - {leader.name}'s party",
        mission=mission,
        today=today,
        resets_ts=resets_ts,
        base_attempts=base_attempts,
        turn_seconds=turn_seconds,
    )


async def _start_fight(
    *,
    bot: YokaiBot,
    runtime: RpgRuntime,
    channel: discord.TextChannel,
    entrants: Sequence[Entrant],
    thread_name: str,
    mission: Mission,
    today: str,
    resets_ts: int,
    base_attempts: int,
    turn_seconds: float,
    promotion: Optional[PromotionRequest] = None,
    promotion_cooldown_s: int = 0,
) -> discord.Thread:
    user_ids = [entrant.user.id for entrant in entrants]
    if len(set(user_ids)) != len(user_ids):
        raise ValueError("A fight cannot list the same member twice.")
    if any(user_id in runtime.active_users for user_id in user_ids):
        raise AlreadyFightingError()
    runtime.active_users.update(user_ids)
    try:
        fight_id = await runtime.store.begin_fight(
            mission_id=mission.id,
            kind=mission.kind,
            user_ids=user_ids,
            cost=mission.attempt_cost,
            today=today,
            base_attempts=base_attempts,
            streak_rules=runtime.content.balance.streak,
            resets_ts=resets_ts,
            promotion=promotion,
        )
    except BaseException:
        runtime.active_users.difference_update(user_ids)
        raise
    try:
        thread = await channel.create_thread(
            name=thread_name[:100],
            type=discord.ChannelType.public_thread,
            auto_archive_duration=THREAD_ARCHIVE_MINUTES,
        )
    except (discord.Forbidden, discord.HTTPException) as exc:
        logger.warning("Could not open a fight thread in channel %s: %s", channel.id, exc)
        await runtime.store.abort_fight(fight_id)
        runtime.active_users.difference_update(user_ids)
        raise ThreadUnavailableError() from exc
    for entrant in entrants:
        with contextlib.suppress(discord.HTTPException):
            await thread.add_user(entrant.user)
    await runtime.store.set_fight_thread(fight_id, thread.id)

    participants = []
    for entrant in entrants:
        equipped = await runtime.gear.equipped_items(entrant.user.id)
        loadout = compute_loadout(runtime.content, entrant.character.level, equipped)
        participants.append(Participant(entrant.user.id, entrant.name, entrant.character, loadout))
    runner = FightRunner(
        bot=bot,
        runtime=runtime,
        fight_id=fight_id,
        mission=mission,
        participants=participants,
        thread=thread,
        turn_seconds=turn_seconds,
        promotion_to=promotion.to_rank if promotion else None,
        promotion_cooldown_s=promotion_cooldown_s,
    )
    task = asyncio.create_task(runner.run(), name=f"rpg-fight-{fight_id}")
    runtime.tasks.add(task)
    task.add_done_callback(runtime.tasks.discard)
    return thread
