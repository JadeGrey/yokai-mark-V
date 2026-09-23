"""Abstract base interface for music recommender engines."""

from __future__ import annotations

import abc
from typing import Optional

from yokai.music.models import Track
from yokai.music.recommend.models import Recommendation


class Recommender(abc.ABC):
    """Abstract interface for track recommendation systems."""

    @abc.abstractmethod
    async def recommend(
        self,
        seed: Track,
        count: int = 8,
        exclude: Optional[set[str]] = None,
    ) -> Recommendation:
        """Generate track recommendations for a seed track while excluding specific video IDs."""
