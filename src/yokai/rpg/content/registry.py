"""Read-only lookups over the loaded RPG content."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional

from yokai.rpg.models import LevelTable, Rank, Tag


@dataclass(frozen=True, slots=True)
class Content:
    """Everything the RPG reads from `content/rpg/`, validated and immutable."""

    levels: LevelTable
    ranks: tuple[Rank, ...]
    tags: Mapping[str, Tag]

    def rank_index(self, rank_id: str) -> int:
        """Position of a rank in the ladder (0 is the lowest). Raises KeyError if unknown."""
        for index, rank in enumerate(self.ranks):
            if rank.id == rank_id:
                return index
        raise KeyError(rank_id)

    def next_rank(self, rank_id: str) -> Optional[Rank]:
        """The rank above `rank_id`, or None at the top. Raises KeyError if unknown."""
        index = self.rank_index(rank_id)
        return self.ranks[index + 1] if index + 1 < len(self.ranks) else None
