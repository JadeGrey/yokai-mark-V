"""Period keys and reset times for daily and weekly challenges."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from yokai.challenges import periods

UTC = timezone.utc


def ts(*args: int) -> int:
    return int(datetime(*args, tzinfo=UTC).timestamp())


def test_load_tz_utc_needs_no_tzdata() -> None:
    assert periods.load_tz("UTC") is UTC
    assert periods.load_tz(" utc ") is UTC


def test_load_tz_rejects_unknown_zone_with_a_helpful_message() -> None:
    with pytest.raises(ValueError, match="CHALLENGE_TZ"):
        periods.load_tz("Not/AZone")


def test_daily_key_changes_exactly_at_midnight() -> None:
    assert periods.period_key("daily", ts(2026, 10, 13, 23, 59, 59), UTC) == "2026-10-13"
    assert periods.period_key("daily", ts(2026, 10, 14, 0, 0, 0), UTC) == "2026-10-14"


def test_weekly_key_is_the_iso_week_and_rolls_on_monday() -> None:
    assert periods.period_key("weekly", ts(2026, 10, 13, 12), UTC) == "2026-W42"
    assert periods.period_key("weekly", ts(2026, 10, 18, 23, 59, 59), UTC) == "2026-W42"
    assert periods.period_key("weekly", ts(2026, 10, 19, 0, 0, 0), UTC) == "2026-W43"


def test_weekly_key_across_the_iso_year_boundary() -> None:
    assert periods.period_key("weekly", ts(2027, 1, 1, 12), UTC) == "2026-W53"
    assert periods.period_key("weekly", ts(2027, 1, 4, 12), UTC) == "2027-W01"


def test_unknown_period_is_rejected() -> None:
    with pytest.raises(ValueError):
        periods.period_key("monthly", 0, UTC)
    with pytest.raises(ValueError):
        periods.next_reset_ts("monthly", 0, UTC)


def test_next_daily_reset_is_the_coming_midnight() -> None:
    assert periods.next_reset_ts("daily", ts(2026, 10, 13, 12), UTC) == ts(2026, 10, 14)


def test_next_weekly_reset_is_the_coming_monday_even_on_a_monday() -> None:
    assert periods.next_reset_ts("weekly", ts(2026, 10, 13, 12), UTC) == ts(2026, 10, 19)
    assert periods.next_reset_ts("weekly", ts(2026, 10, 19, 0, 0, 0), UTC) == ts(2026, 10, 26)


def test_named_zone_reset_follows_local_midnight_across_dst() -> None:
    pytest.importorskip("tzdata")
    new_york = periods.load_tz("America/New_York")
    now = int(datetime(2026, 3, 8, 0, 30, tzinfo=new_york).timestamp())  # day DST starts
    reset = periods.next_reset_ts("daily", now, new_york)
    assert datetime.fromtimestamp(reset, new_york) == datetime(2026, 3, 9, 0, 0, tzinfo=new_york)
    assert periods.period_key("daily", now, new_york) == "2026-03-08"
