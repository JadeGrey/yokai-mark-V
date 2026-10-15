"""Runs a guild fight in a Discord thread: rounds, timers, away players and settlement."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Optional

import discord

from yokai.errors import AlreadyFightingError, ThreadUnavailableError
from yokai.rpg import guild
from yokai.rpg.combat.builders import build_mission_stages, player_combatant
from yokai.rpg.combat.describe import describe_all
from yokai.rpg.combat.engine import Fight
from yokai.rpg.combat.models import OUTCOME_WON
from yokai.rpg.models import Character, Mission
from yokai.rpg.runtime import RpgRuntime
from yokai.rpg.stats import stats_at_level
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
    ) -> None:
        self.bot = bot
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
            player_combatant(p.user_id, p.name, stats_at_level(content.levels, p.character.level))
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
        rewards = (
            {
                p.user_id: guild.mission_reward(self.content, self.mission, p.character.rank)
                for p in self.participants
            }
            if won
            else {}
        )
        settlements = await self.runtime.store.settle_fight(
            self.fight_id,
            won=won,
            rewards=rewards,
            level_cap=self.content.levels.cap,
            xp_curve=self.content.balance.xp,
            welcome_bonus=self.bot.config.economy_welcome_bonus,
        )
        self._settled = True
        if settlements is None:
            return
        rows = [
            (f"<@{uid}>", s.xp_gained, s.renown_gained, s.levels_gained)
            for uid, s in settlements.items()
        ]
        embed = EmbedFactory.fight_result(
            won,
            self.mission.name,
            fight.round_no - 1,
            rows,
            self.last_log,
            bot_avatar_url=avatar_url(self.bot),
        )
        try:
            await self._edit(embed=embed, view=None)
        except discord.HTTPException as exc:
            logger.warning("Could not show the result of fight %d: %s", self.fight_id, exc)


# ---------------------------------------------------------------------------- launching


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
) -> discord.Thread:
    """Spend the attempts, open a thread and start the fight task.

    Raises NoAttemptsError or AlreadyFightingError (nothing changes), or ThreadUnavailableError
    (the attempts are refunded).
    """
    if member.id in runtime.active_users:
        raise AlreadyFightingError()
    runtime.active_users.add(member.id)
    try:
        fight_id = await runtime.store.begin_fight(
            mission_id=mission.id,
            kind=mission.kind,
            user_ids=[member.id],
            cost=mission.attempt_cost,
            today=today,
            base_attempts=base_attempts,
            streak_rules=runtime.content.balance.streak,
            resets_ts=resets_ts,
        )
    except BaseException:
        runtime.active_users.discard(member.id)
        raise
    try:
        thread = await channel.create_thread(
            name=f"{mission.name} - {member_name}"[:100],
            type=discord.ChannelType.public_thread,
            auto_archive_duration=THREAD_ARCHIVE_MINUTES,
        )
    except (discord.Forbidden, discord.HTTPException) as exc:
        logger.warning("Could not open a fight thread in channel %s: %s", channel.id, exc)
        await runtime.store.abort_fight(fight_id)
        runtime.active_users.discard(member.id)
        raise ThreadUnavailableError() from exc
    with contextlib.suppress(discord.HTTPException):
        await thread.add_user(member)
    await runtime.store.set_fight_thread(fight_id, thread.id)

    runner = FightRunner(
        bot=bot,
        runtime=runtime,
        fight_id=fight_id,
        mission=mission,
        participants=[Participant(member.id, member_name, character)],
        thread=thread,
        turn_seconds=turn_seconds,
    )
    task = asyncio.create_task(runner.run(), name=f"rpg-fight-{fight_id}")
    runtime.tasks.add(task)
    task.add_done_callback(runtime.tasks.discard)
    return thread
