"""Parsers for levels.toml, ranks.toml and tags.toml."""

from __future__ import annotations

from typing import Any

from yokai.rpg.content.common import (
    ContentError,
    entries,
    int_value,
    number_value,
    stats_value,
    text_value,
)
from yokai.rpg.models import STAT_NAMES, LevelTable, Rank, Tag

MAX_LEANS = 3


def parse_levels(raw: dict[str, Any]) -> LevelTable:
    cap = int_value(raw.get("cap"), "levels.toml cap", 1)
    base = stats_value(raw.get("base"), "levels.toml [base]", 1)
    growth = stats_value(raw.get("growth"), "levels.toml [growth]", 0)
    return LevelTable(cap=cap, base=base, growth=growth)


def parse_ranks(raw: dict[str, Any], cap: int) -> tuple[Rank, ...]:
    ranks: list[Rank] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries(raw, "rank", "ranks.toml"), start=1):
        where = f"ranks.toml rank #{index}"
        rank_id = text_value(entry.get("id"), f"{where} id")
        if rank_id in seen:
            raise ContentError(f"ranks.toml has a duplicate rank id: {rank_id}")
        seen.add(rank_id)
        min_level = int_value(entry.get("min_level"), f"{where} min_level", 1)
        if ranks and min_level <= ranks[-1].min_level:
            raise ContentError(f"{where} min_level must be higher than the previous rank's")
        xp_share = number_value(entry.get("xp_share"), f"{where} xp_share", 0.001, 1.0)
        enemy_power = int_value(entry.get("enemy_power", 100), f"{where} enemy_power", 10, 400)
        ranks.append(
            Rank(id=rank_id, min_level=min_level, xp_share=xp_share, enemy_power=enemy_power)
        )
    if ranks[0].min_level != 1:
        raise ContentError("ranks.toml: the first rank must start at level 1")
    if ranks[-1].min_level > cap:
        raise ContentError("ranks.toml: the last rank's min_level is above the level cap")
    return tuple(ranks)


def _parse_leans(value: object, where: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ContentError(f"{where} leans must be a list of stat names")
    if not 1 <= len(value) <= MAX_LEANS:
        raise ContentError(f"{where} leans must name 1 to {MAX_LEANS} stats")
    for stat in value:
        if stat not in STAT_NAMES:
            raise ContentError(f"{where} leans has an unknown stat: {stat!r}")
    return tuple(value)


def parse_tags(raw: dict[str, Any]) -> dict[str, Tag]:
    tags: dict[str, Tag] = {}
    names: set[str] = set()
    for index, entry in enumerate(entries(raw, "tag", "tags.toml"), start=1):
        where = f"tags.toml tag #{index}"
        tag_id = text_value(entry.get("id"), f"{where} id")
        if tag_id in tags:
            raise ContentError(f"tags.toml has a duplicate tag id: {tag_id}")
        name = text_value(entry.get("name"), f"{where} name")
        if name.casefold() in names:
            raise ContentError(f"tags.toml has a duplicate tag name: {name}")
        names.add(name.casefold())
        tags[tag_id] = Tag(
            id=tag_id,
            name=name,
            role=text_value(entry.get("role"), f"{where} role"),
            leans=_parse_leans(entry.get("leans"), where),
            description=text_value(entry.get("description"), f"{where} description"),
        )
    return tags
