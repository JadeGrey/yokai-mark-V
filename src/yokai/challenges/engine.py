"""The challenge engine: turns activity events into progress and pays rewards."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import timezone, tzinfo
from typing import TYPE_CHECKING, Callable, Optional, Sequence

import aiosqlite

from yokai.challenges import periods
from yokai.challenges.models import (
    Completion,
    Event,
    ProgressView,
    RoundPlayed,
    Template,
    TrackListened,
    VoiceMinutes,
)
from yokai.challenges.slate import pick_slate
from yokai.challenges.templates import POOL

if TYPE_CHECKING:
    from yokai.moderation.store import ModerationStore
    from yokai.storage.db import Database

logger = logging.getLogger(__name__)

MAX_SET_TEMPLATES = 6
PURGE_AFTER_S = 30 * 86_400
_ROUND_CACHE_SIZE = 4096


@dataclass(frozen=True, slots=True)
class _Effect:
    """One metric nudge: add `delta`, or add `item` to the distinct set."""

    metric: str
    game: Optional[str]
    delta: int
    item: Optional[str]


class ChallengeEngine:
    """Matches events to the active slates, advances progress and queues completions."""

    def __init__(
        self,
        db: Database,
        moderation: ModerationStore,
        *,
        enabled: bool = True,
        tz: tzinfo = timezone.utc,
        min_wager: int = 10,
        reward_scale: float = 1.0,
        welcome_bonus: int = 200,
        clock: Callable[[], float] = time.time,
        pool: Sequence[Template] = POOL,
    ) -> None:
        self.db = db
        self.moderation = moderation
        self.enabled = enabled
        self.tz = tz
        self.min_wager = min_wager
        self.reward_scale = reward_scale
        self.welcome_bonus = welcome_bonus
        self._clock = clock
        self._pool = tuple(pool)
        self._by_id = {t.id: t for t in self._pool}
        self._slates: dict[str, tuple[str, list[Template]]] = {}
        self._slate_lock = asyncio.Lock()
        self._pending: list[Completion] = []
        self._tasks: set[asyncio.Task[None]] = set()
        self._counted_rounds: OrderedDict[tuple[int, str, str], None] = OrderedDict()

    # ------------------------------------------------------------------ slates

    def _now(self) -> int:
        return int(self._clock())

    def reward_for(self, template: Template) -> int:
        """Renown paid for a template after CHALLENGE_REWARD_SCALE (never below 1)."""
        return max(1, round(template.reward * self.reward_scale))

    def next_reset(self, period: str) -> int:
        return periods.next_reset_ts(period, self._now(), self.tz)

    async def active_slate(self, period: str) -> tuple[str, list[Template]]:
        """The current period key and its templates; creates and stores the slate if missing."""
        key = periods.period_key(period, self._now(), self.tz)
        cached = self._slates.get(period)
        if cached is not None and cached[0] == key:
            return cached
        async with self._slate_lock:
            cached = self._slates.get(period)
            if cached is not None and cached[0] == key:
                return cached
            stored = await self.db.get_challenge_slate(period, key)
            if stored is None:
                stored = await self.db.create_challenge_slate(
                    period, key, pick_slate(period, key, self._pool)
                )
            templates = self._resolve(stored)
            self._slates[period] = (key, templates)
            return key, templates

    def _resolve(self, ids: Sequence[str]) -> list[Template]:
        templates: list[Template] = []
        for template_id in ids:
            template = self._by_id.get(template_id)
            if template is None:
                logger.warning(
                    "Stored challenge %r is no longer in the pool; ignoring it", template_id
                )
                continue
            templates.append(template)
        return templates

    async def reroll(self, period: str) -> list[Template]:
        """Replace the current slate with a fresh random one (marks it overridden)."""
        key = periods.period_key(period, self._now(), self.tz)
        ids = pick_slate(period, key, self._pool, salt=str(self._now()))
        if not ids:
            raise ValueError(f"There are no {period} challenges in the pool.")
        return await self._store_override(period, key, ids)

    async def set_slate(self, period: str, template_ids: Sequence[str]) -> list[Template]:
        """Replace the current slate with chosen templates; raises ValueError with a reason."""
        ids = list(template_ids)
        if not 1 <= len(ids) <= MAX_SET_TEMPLATES:
            raise ValueError(f"Pick between 1 and {MAX_SET_TEMPLATES} templates.")
        if len(set(ids)) != len(ids):
            raise ValueError("Each template can only be listed once.")
        unknown = [i for i in ids if i not in self._by_id]
        if unknown:
            shown = ", ".join(i[:40] for i in unknown[:5])
            raise ValueError(f"Unknown template id(s): {shown}")
        wrong = [i for i in ids if self._by_id[i].period != period]
        if wrong:
            raise ValueError(f"Not {period} templates: {', '.join(wrong)}")
        key = periods.period_key(period, self._now(), self.tz)
        return await self._store_override(period, key, ids)

    async def _store_override(self, period: str, key: str, ids: list[str]) -> list[Template]:
        async with self._slate_lock:
            stored = await self.db.replace_challenge_slate(period, key, ids)
            templates = self._resolve(stored)
            self._slates[period] = (key, templates)
        return templates

    # ------------------------------------------------------------------ events

    def emit(self, event: Event) -> None:
        """Record an event in the background. Never raises, so hosts need no try/except."""
        if not self.enabled:
            return
        try:
            task = asyncio.get_running_loop().create_task(self._record_safely(event))
        except RuntimeError:
            logger.debug("No running loop; dropping %s", type(event).__name__)
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _record_safely(self, event: Event) -> None:
        try:
            await self.record(event)
        except Exception:
            logger.exception("Challenge engine failed on %s", type(event).__name__)

    async def close(self) -> None:
        """Stop accepting events, then let in-flight ones finish (shutdown and tests)."""
        self.enabled = False
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def record(self, event: Event) -> None:
        """Apply one event to every affected user's active challenges."""
        if not self.enabled:
            return
        if isinstance(event, TrackListened):
            for user_id in dict.fromkeys(event.user_ids):
                await self._apply(user_id, self._track_effects(event), event.channel_id)
        elif isinstance(event, RoundPlayed):
            await self._apply(event.user_id, self._round_effects(event), None)
        elif isinstance(event, VoiceMinutes):
            if event.minutes > 0:
                effect = _Effect("voice_minutes", None, event.minutes, None)
                await self._apply(event.user_id, [effect], None)

    @staticmethod
    def _track_effects(event: TrackListened) -> list[_Effect]:
        effects = [_Effect("tracks", None, 0, event.video_id)]
        artist = (event.artist or "").strip().casefold()
        if artist:
            effects.append(_Effect("artists", None, 0, artist[:100]))
        return effects

    def _first_time(self, user_id: int, round_key: str, kind: str) -> bool:
        key = (user_id, round_key, kind)
        if key in self._counted_rounds:
            return False
        self._counted_rounds[key] = None
        while len(self._counted_rounds) > _ROUND_CACHE_SIZE:
            self._counted_rounds.popitem(last=False)
        return True

    def _round_effects(self, event: RoundPlayed) -> list[_Effect]:
        if event.stake < self.min_wager:
            return []
        effects = [_Effect("games", event.game, 0, event.game)]
        if self._first_time(event.user_id, event.round_key, "played"):
            effects.append(_Effect("rounds_played", event.game, 1, None))
        if event.payout > event.stake and self._first_time(event.user_id, event.round_key, "won"):
            effects.append(_Effect("rounds_won", event.game, 1, None))
        return effects

    async def _apply(
        self, user_id: int, effects: Sequence[_Effect], channel_id: Optional[int]
    ) -> None:
        if not effects or await self.moderation.is_blacklisted(user_id):
            return
        for period in periods.PERIODS:
            key, templates = await self.active_slate(period)
            for template in templates:
                for effect in effects:
                    if effect.metric != template.metric:
                        continue
                    if template.game is not None and effect.game not in template.game:
                        continue
                    await self._advance(user_id, period, key, template, effect, channel_id)

    async def _advance(
        self,
        user_id: int,
        period: str,
        key: str,
        template: Template,
        effect: _Effect,
        channel_id: Optional[int],
    ) -> None:
        reward = self.reward_for(template)
        try:
            result = await self.db.advance_challenge(
                user_id,
                period,
                key,
                template.id,
                target=template.target,
                reward=reward,
                welcome_bonus=self.welcome_bonus,
                delta=effect.delta,
                item=effect.item,
            )
        except (aiosqlite.Error, ValueError) as exc:
            logger.error(
                "Could not advance challenge %s for user %d: %s", template.id, user_id, exc
            )
            return
        if result.completed_now:
            self._pending.append(
                Completion(user_id, template.id, template.title, reward, channel_id)
            )

    # ------------------------------------------------------------------ reading

    def drain_completions(self) -> list[Completion]:
        """Hand over (and clear) the completions waiting for a notice."""
        pending, self._pending = self._pending, []
        return pending

    async def view(self, user_id: int, period: str) -> list[ProgressView]:
        """This user's progress on the active slate of a period."""
        key, templates = await self.active_slate(period)
        rows = await self.db.get_challenge_progress(user_id, period, key)
        views: list[ProgressView] = []
        for template in templates:
            progress, completed = rows.get(template.id, (0, False))
            views.append(ProgressView(template, progress, completed, self.reward_for(template)))
        return views

    async def purge_old(self) -> int:
        """Delete challenge rows older than 30 days."""
        return await self.db.purge_challenges(self._now() - PURGE_AFTER_S)
