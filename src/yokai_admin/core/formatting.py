"""Small display helpers shared by modules."""

from __future__ import annotations

from datetime import datetime
from typing import Optional


def describe_age(seconds: int) -> str:
    """Plain-language age such as '1 second', '5 minutes' or '3 days'."""
    if seconds < 60:
        count, unit = seconds, "second"
    elif seconds < 3600:
        count, unit = seconds // 60, "minute"
    elif seconds < 86_400:
        count, unit = seconds // 3600, "hour"
    else:
        count, unit = seconds // 86_400, "day"
    return f"{count} {unit}{'' if count == 1 else 's'}"


def format_when(ts: Optional[int]) -> str:
    """Local date and time of a Unix timestamp, or an en dash when there is none."""
    if ts is None:
        return "–"
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except (OverflowError, OSError, ValueError):
        return "–"


def format_number(value: Optional[int]) -> str:
    """A whole number with thousands separators, or an en dash when there is none."""
    return "\u2013" if value is None else f"{value:,}"


def format_bytes(size: Optional[int]) -> str:
    """A file size in the largest sensible unit (1024 steps), or an en dash when unknown."""
    if size is None:
        return "–"
    value = float(max(size, 0))
    for unit in ("B", "KB", "MB"):
        if value < 1024:
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def format_signed(value: int) -> str:
    """A change shown with its sign: +500, -250, or 0."""
    return f"{value:+,}" if value else "0"
