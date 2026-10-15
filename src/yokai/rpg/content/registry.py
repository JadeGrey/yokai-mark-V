"""Read-only lookups over the loaded RPG content."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional

from yokai.rpg.models import (
    PROMOTION_KIND,
    Balance,
    EnemyDef,
    ItemDef,
    LevelTable,
    Mission,
    Rank,
    SkillDef,
    Tag,
)


@dataclass(frozen=True, slots=True)
class Content:
    """Everything the RPG reads from `content/rpg/`, validated and immutable."""

    levels: LevelTable
    ranks: tuple[Rank, ...]
    tags: Mapping[str, Tag]
    balance: Balance
    skills: Mapping[str, SkillDef]
    enemies: Mapping[str, EnemyDef]
    missions: Mapping[str, Mission]
    items: Mapping[str, ItemDef]

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

    def rank(self, rank_id: str) -> Rank:
        """The rank with this id. Raises KeyError if unknown."""
        return self.ranks[self.rank_index(rank_id)]

    def missions_for_rank(self, rank_id: str) -> list[Mission]:
        """Missions of exactly this rank, in file order."""
        return [m for m in self.missions.values() if m.rank == rank_id]

    def promotion_mission(self, rank_id: str) -> Optional[Mission]:
        """The trial for entering a rank, as a mission the runner can play (None for rank 1)."""
        rank = self.rank(rank_id)
        trial = rank.promotion
        if trial is None:
            return None
        return Mission(
            id=f"promotion_{rank.id}",
            name=trial.name,
            rank=rank.id,
            kind=PROMOTION_KIND,
            attempt_cost=trial.attempt_cost,
            stages=trial.stages,
            power=trial.power,
        )

    def item_tiers(self) -> list[int]:
        """The distinct level requirements of all items, ascending (one per gear tier)."""
        return sorted({item.req_level for item in self.items.values()})
