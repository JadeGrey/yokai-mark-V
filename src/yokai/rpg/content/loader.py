"""Load and validate the RPG content files (TOML); a bad file stops startup with a clear error."""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType

from yokai.rpg.content.common import ContentError, read_toml
from yokai.rpg.content.parse_core import parse_levels, parse_ranks, parse_tags
from yokai.rpg.content.parse_world import (
    parse_balance,
    parse_enemies,
    parse_items,
    parse_missions,
    parse_skills,
)
from yokai.rpg.content.registry import Content

__all__ = ["ContentError", "load_content"]


def load_content(directory: Path | str) -> Content:
    """Read and validate every content file in `directory`. Raises ContentError on any problem."""
    root = Path(directory)
    levels = parse_levels(read_toml(root, "levels.toml"))
    ranks = parse_ranks(read_toml(root, "ranks.toml"), levels.cap)
    balance = parse_balance(read_toml(root, "balance.toml"))
    skills = parse_skills(read_toml(root, "skills.toml"))
    tags = parse_tags(read_toml(root, "tags.toml"), skills)
    items = parse_items(read_toml(root, "items.toml"), tags, skills, levels.cap)
    enemies = parse_enemies(read_toml(root, "enemies.toml"), skills)
    missions = parse_missions(
        read_toml(root, "missions.toml"), tuple(rank.id for rank in ranks), enemies, levels.cap
    )
    return Content(
        levels=levels,
        ranks=ranks,
        tags=MappingProxyType(tags),
        balance=balance,
        skills=MappingProxyType(skills),
        enemies=MappingProxyType(enemies),
        missions=MappingProxyType(missions),
        items=MappingProxyType(items),
    )
