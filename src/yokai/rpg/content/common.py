"""Shared helpers for reading and validating the RPG content files."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from yokai.rpg.models import STAT_NAMES, Stats


class ContentError(Exception):
    """Raised when a content file is missing or invalid."""


def read_toml(directory: Path, name: str) -> dict[str, Any]:
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


def int_value(value: object, where: str, minimum: int, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContentError(f"{where} must be a whole number")
    if value < minimum:
        raise ContentError(f"{where} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise ContentError(f"{where} must be at most {maximum}")
    return value


def number_value(value: object, where: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContentError(f"{where} must be a number")
    if not minimum <= value <= maximum:
        raise ContentError(f"{where} must be between {minimum} and {maximum}")
    return float(value)


def text_value(value: object, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContentError(f"{where} must be non-empty text")
    return value.strip()


def table_value(value: object, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContentError(f"{where} must be a table")
    return value


def stats_value(table: object, where: str, hp_minimum: int, minimum: int = 0) -> Stats:
    data = table_value(table, where)
    unknown = sorted(set(data) - set(STAT_NAMES))
    if unknown:
        raise ContentError(f"{where} has unknown stat(s): {', '.join(unknown)}")
    missing = [name for name in STAT_NAMES if name not in data]
    if missing:
        raise ContentError(f"{where} is missing stat(s): {', '.join(missing)}")
    values = {
        name: int_value(data[name], f"{where}.{name}", hp_minimum if name == "hp" else minimum)
        for name in STAT_NAMES
    }
    return Stats(**values)


def partial_stats_value(table: object, where: str, minimum: int, maximum: int) -> Stats:
    """Stats where any stat may be left out (it counts as 0). Values may be negative."""
    data = table_value(table, where)
    unknown = sorted(set(data) - set(STAT_NAMES))
    if unknown:
        raise ContentError(f"{where} has unknown stat(s): {', '.join(unknown)}")
    values = {
        name: int_value(data[name], f"{where}.{name}", minimum, maximum)
        for name in STAT_NAMES
        if name in data
    }
    return Stats(**values)


def entries(raw: dict[str, Any], key: str, file_name: str) -> list[dict[str, Any]]:
    found = raw.get(key)
    if not isinstance(found, list) or not found:
        raise ContentError(f"{file_name} needs at least one [[{key}]] entry")
    for index, entry in enumerate(found, start=1):
        if not isinstance(entry, dict):
            raise ContentError(f"{file_name} {key} #{index} must be a table")
    return found
