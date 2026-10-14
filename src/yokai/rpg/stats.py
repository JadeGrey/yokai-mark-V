"""Pure stat math for the Guild RPG."""

from __future__ import annotations

from yokai.rpg.models import STAT_LABELS, STAT_NAMES, LevelTable, Stats


def stats_at_level(table: LevelTable, level: int) -> Stats:
    """Base stats for a level. Levels below 1 or above the cap are clamped, never an error."""
    clamped = max(1, min(level, table.cap))
    return table.base + table.growth.scaled(clamped - 1)


def format_stats(stats: Stats) -> list[tuple[str, str]]:
    """(label, display value) for each stat in display order; Crit is shown as a percentage."""
    pairs: list[tuple[str, str]] = []
    for name in STAT_NAMES:
        value = getattr(stats, name)
        pairs.append((STAT_LABELS[name], f"{value}%" if name == "crit" else f"{value:,}"))
    return pairs
