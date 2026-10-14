"""Pure period math for challenges: which day or ISO week it is, and when it resets."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo
from datetime import time as dtime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DAILY = "daily"
WEEKLY = "weekly"
PERIODS = (DAILY, WEEKLY)


def load_tz(name: str) -> tzinfo:
    """Resolve CHALLENGE_TZ. UTC never needs the tz database; named zones do on Windows."""
    cleaned = name.strip()
    if cleaned.upper() == "UTC":
        return timezone.utc
    try:
        return ZoneInfo(cleaned)
    except (ZoneInfoNotFoundError, ValueError, OSError) as err:
        raise ValueError(
            f"CHALLENGE_TZ {name!r} is not a known time zone. On Windows, install the "
            "'tzdata' package for named zones, or set CHALLENGE_TZ=UTC."
        ) from err


def period_key(period: str, now_ts: int, tz: tzinfo) -> str:
    """'2026-10-13' for daily, '2026-W42' (ISO week) for weekly, in the given zone."""
    local = datetime.fromtimestamp(now_ts, tz)
    if period == DAILY:
        return local.date().isoformat()
    if period == WEEKLY:
        iso = local.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    raise ValueError(f"Unknown challenge period: {period!r}")


def next_reset_ts(period: str, now_ts: int, tz: tzinfo) -> int:
    """Unix time of the next local midnight (daily) or Monday midnight (weekly)."""
    local = datetime.fromtimestamp(now_ts, tz)
    if period == DAILY:
        days = 1
    elif period == WEEKLY:
        days = 7 - local.weekday()
    else:
        raise ValueError(f"Unknown challenge period: {period!r}")
    target = datetime.combine(local.date() + timedelta(days=days), dtime.min, tzinfo=tz)
    return int(target.timestamp())
