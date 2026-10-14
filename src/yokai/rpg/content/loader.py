"""Load and validate the RPG content files (TOML); a bad file stops startup with a clear error."""

from __future__ import annotations

import tomllib
from pathlib import Path
from types import MappingProxyType
from typing import Any

from yokai.rpg.content.registry import Content
from yokai.rpg.models import STAT_NAMES, LevelTable, Rank, Stats, Tag

MAX_LEANS = 3


class ContentError(Exception):
    """Raised when a content file is missing or invalid."""


def _read(directory: Path, name: str) -> dict[str, Any]:
    path = directory / name
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError as err:
        raise ContentError(f"Missing content file: {path}") from err
    except tomllib.TOMLDecodeError as err:
        raise ContentError(f"{name} is not valid TOML: {err}") from err
    except OSError as err:
        raise ContentError(f"Could not read content file {path}: {err}") from err


def _int(value: object, where: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContentError(f"{where} must be a whole number")
    if value < minimum:
        raise ContentError(f"{where} must be at least {minimum}")
    return value


def _text(value: object, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContentError(f"{where} must be non-empty text")
    return value.strip()


def _stats(table: object, where: str, hp_minimum: int) -> Stats:
    if not isinstance(table, dict):
        raise ContentError(f"{where} must be a table")
    unknown = sorted(set(table) - set(STAT_NAMES))
    if unknown:
        raise ContentError(f"{where} has unknown stat(s): {', '.join(unknown)}")
    missing = [name for name in STAT_NAMES if name not in table]
    if missing:
        raise ContentError(f"{where} is missing stat(s): {', '.join(missing)}")
    values = {
        name: _int(table[name], f"{where}.{name}", hp_minimum if name == "hp" else 0)
        for name in STAT_NAMES
    }
    return Stats(**values)


def _parse_levels(raw: dict[str, Any]) -> LevelTable:
    cap = _int(raw.get("cap"), "levels.toml cap", 1)
    base = _stats(raw.get("base"), "levels.toml [base]", 1)
    growth = _stats(raw.get("growth"), "levels.toml [growth]", 0)
    return LevelTable(cap=cap, base=base, growth=growth)


def _entries(raw: dict[str, Any], key: str, file_name: str) -> list[dict[str, Any]]:
    entries = raw.get(key)
    if not isinstance(entries, list) or not entries:
        raise ContentError(f"{file_name} needs at least one [[{key}]] entry")
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise ContentError(f"{file_name} {key} #{index} must be a table")
    return entries


def _parse_ranks(raw: dict[str, Any], cap: int) -> tuple[Rank, ...]:
    ranks: list[Rank] = []
    seen: set[str] = set()
    for index, entry in enumerate(_entries(raw, "rank", "ranks.toml"), start=1):
        where = f"ranks.toml rank #{index}"
        rank_id = _text(entry.get("id"), f"{where} id")
        if rank_id in seen:
            raise ContentError(f"ranks.toml has a duplicate rank id: {rank_id}")
        seen.add(rank_id)
        min_level = _int(entry.get("min_level"), f"{where} min_level", 1)
        if ranks and min_level <= ranks[-1].min_level:
            raise ContentError(f"{where} min_level must be higher than the previous rank's")
        ranks.append(Rank(id=rank_id, min_level=min_level))
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


def _parse_tags(raw: dict[str, Any]) -> dict[str, Tag]:
    tags: dict[str, Tag] = {}
    names: set[str] = set()
    for index, entry in enumerate(_entries(raw, "tag", "tags.toml"), start=1):
        where = f"tags.toml tag #{index}"
        tag_id = _text(entry.get("id"), f"{where} id")
        if tag_id in tags:
            raise ContentError(f"tags.toml has a duplicate tag id: {tag_id}")
        name = _text(entry.get("name"), f"{where} name")
        if name.casefold() in names:
            raise ContentError(f"tags.toml has a duplicate tag name: {name}")
        names.add(name.casefold())
        tags[tag_id] = Tag(
            id=tag_id,
            name=name,
            role=_text(entry.get("role"), f"{where} role"),
            leans=_parse_leans(entry.get("leans"), where),
            description=_text(entry.get("description"), f"{where} description"),
        )
    return tags


def load_content(directory: Path | str) -> Content:
    """Read and validate every content file in `directory`. Raises ContentError on any problem."""
    root = Path(directory)
    levels = _parse_levels(_read(root, "levels.toml"))
    ranks = _parse_ranks(_read(root, "ranks.toml"), levels.cap)
    tags = _parse_tags(_read(root, "tags.toml"))
    return Content(levels=levels, ranks=ranks, tags=MappingProxyType(tags))
