"""Shared pieces for challenge tests: a tiny pool, a movable clock and an engine factory."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from tests.moderation_helpers import fake_moderation
from yokai.challenges.engine import ChallengeEngine
from yokai.challenges.models import Template
from yokai.storage.db import Database

NOW = int(datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc).timestamp())  # Tuesday, W42
DAY = 86_400


class FakeClock:
    def __init__(self, now: int = NOW) -> None:
        self.now = now

    def __call__(self) -> float:
        return float(self.now)


def tpl(
    id: str,
    metric: str,
    target: int,
    reward: int,
    *,
    period: str = "daily",
    category: str = "music",
    game: Optional[frozenset[str]] = None,
) -> Template:
    return Template(id, period, category, metric, target, reward, f"title {id}", game)


def make_engine(
    db: Database,
    *pool: Template,
    blacklisted: tuple[int, ...] = (),
    clock: Optional[FakeClock] = None,
    **kwargs,
) -> ChallengeEngine:
    kwargs.setdefault("welcome_bonus", 200)
    return ChallengeEngine(
        db,
        fake_moderation(owner_id=0, blacklisted=blacklisted),
        clock=clock or FakeClock(),
        pool=pool,
        **kwargs,
    )
