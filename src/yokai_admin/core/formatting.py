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
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def format_number(value: Optional[int]) -> str:
    """A whole number with thousands separators, or an en dash when there is none."""
    return "\u2013" if value is None else f"{value:,}"


def format_signed(value: int) -> str:
    """A change shown with its sign: +500, -250, or 0."""
    return f"{value:+,}" if value else "0"
