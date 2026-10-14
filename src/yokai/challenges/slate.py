"""Pure slate selection: one template per category, seeded so it is repeatable."""

from __future__ import annotations

import random
from typing import Sequence

from yokai.challenges.models import CATEGORIES, Template


def pick_slate(period: str, period_key: str, pool: Sequence[Template], salt: str = "") -> list[str]:
    """Choose one template id per category for a period. Same inputs, same slate."""
    rng = random.Random(f"{period}:{period_key}:{salt}")
    chosen: list[str] = []
    for category in CATEGORIES:
        options = sorted(
            (t for t in pool if t.period == period and t.category == category),
            key=lambda t: t.id,
        )
        if options:
            chosen.append(rng.choice(options).id)
    return chosen
