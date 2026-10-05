"""Tests for the Dashboard's data functions, against real databases and log files."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.admin_helpers import seed_database
from yokai_admin.core.db import readonly
from yokai_admin.modules.dashboard.service import (
    ONLINE_WINDOW_S,
    BotStatus,
    bot_status,
    describe_age,
    format_bytes,
    overview,
    recent_errors,
    status_note,
)

NOW = 1_000_000


def test_a_fresh_heartbeat_is_online(tmp_path: Path) -> None:
    seed_database(tmp_path / "b.db", heartbeat_ts=NOW - 30)
    with readonly(tmp_path / "b.db") as conn:
        status = bot_status(conn, NOW)
    assert status == BotStatus("online", NOW - 30, 30, 4242, "0.1.0")


@pytest.mark.parametrize(
    ("age", "state"), [(ONLINE_WINDOW_S, "online"), (ONLINE_WINDOW_S + 1, "offline")]
)
def test_the_online_window_boundary_is_ninety_seconds(tmp_path: Path, age: int, state: str) -> None:
    """Review Focus 6: exactly 90 s is still online, 91 s is not."""
    seed_database(tmp_path / "b.db", heartbeat_ts=NOW - age)
    with readonly(tmp_path / "b.db") as conn:
        assert bot_status(conn, NOW).state == state


def test_a_heartbeat_from_the_future_does_not_give_a_negative_age(tmp_path: Path) -> None:
    seed_database(tmp_path / "b.db", heartbeat_ts=NOW + 500)
    with readonly(tmp_path / "b.db") as conn:
        status = bot_status(conn, NOW)
    assert status.age_s == 0 and status.state == "online"


def test_no_heartbeat_row_means_offline_with_no_details(tmp_path: Path) -> None:
    seed_database(tmp_path / "b.db")
    with readonly(tmp_path / "b.db") as conn:
        assert bot_status(conn, NOW) == BotStatus("offline", None, None, None, None)


def test_overview_counts_wallets_and_recent_commands(tmp_path: Path) -> None:
    seed_database(
        tmp_path / "b.db",
        wallets=3,
        commands=[NOW - 10, NOW - 86_399, NOW - 86_401],  # two inside 24 h, one outside
    )
    with readonly(tmp_path / "b.db") as conn:
        data = overview(conn, tmp_path / "b.db", NOW)
    assert (data.wallets, data.commands_24h, data.schema_version) == (3, 2, 4)
    assert data.db_size_bytes > 0


def test_recent_errors_keeps_only_error_and_critical_levels_in_order(tmp_path: Path) -> None:
    (tmp_path / "yokai.log").write_text(
        "2026-10-05 10:00:00 [INFO] yokai: started\n"
        "2026-10-05 10:00:01 [ERROR] yokai.bot: first failure\n"
        "2026-10-05 10:00:02 [WARNING] yokai: careful\n"
        "2026-10-05 10:00:03 [CRITICAL] yokai: second failure\n",
        encoding="utf-8",
    )
    assert recent_errors(tmp_path) == [
        "2026-10-05 10:00:01 [ERROR] yokai.bot: first failure",
        "2026-10-05 10:00:03 [CRITICAL] yokai: second failure",
    ]


def test_an_info_line_that_merely_mentions_error_is_not_an_error(tmp_path: Path) -> None:
    (tmp_path / "yokai.log").write_text(
        "2026-10-05 10:00:00 [INFO] yokai: saw [ERROR] in a user's track title\n",
        encoding="utf-8",
    )
    assert recent_errors(tmp_path) == []


def test_recent_errors_respects_the_limit_and_truncates_long_lines(tmp_path: Path) -> None:
    lines = [f"2026-10-05 10:00:{i:02d} [ERROR] yokai: failure {i}" for i in range(12)]
    lines.append("2026-10-05 10:01:00 [ERROR] yokai: " + "x" * 1000)
    (tmp_path / "yokai.log").write_text("\n".join(lines), encoding="utf-8")
    found = recent_errors(tmp_path, limit=3)
    assert len(found) == 3
    assert found[0].endswith("failure 10") and found[1].endswith("failure 11")
    assert len(found[2]) == 400


def test_a_missing_log_is_an_empty_list(tmp_path: Path) -> None:
    assert recent_errors(tmp_path / "no-such-dir") == []


def test_a_huge_log_is_read_from_the_tail_and_bad_bytes_do_not_crash(tmp_path: Path) -> None:
    filler = "2026-10-05 10:00:00 [INFO] yokai: filler line\n" * 20_000  # about 900 KB
    final = b"2026-10-05 11:00:00 [ERROR] yokai: caf\xe9 broke\n"
    (tmp_path / "yokai.log").write_bytes(filler.encode() + final)
    found = recent_errors(tmp_path)
    assert len(found) == 1 and found[0].startswith("2026-10-05 11:00:00 [ERROR]")


@pytest.mark.parametrize(
    ("seconds", "text"),
    [
        (0, "0 seconds"),
        (1, "1 second"),
        (59, "59 seconds"),
        (60, "1 minute"),
        (300, "5 minutes"),
        (7200, "2 hours"),
        (3 * 86_400, "3 days"),
    ],
)
def test_describe_age(seconds: int, text: str) -> None:
    assert describe_age(seconds) == text


@pytest.mark.parametrize(
    ("size", "text"), [(512, "512 B"), (2048, "2.0 KB"), (5 * 1024 * 1024, "5.00 MB")]
)
def test_format_bytes(size: int, text: str) -> None:
    assert format_bytes(size) == text


def test_status_note_covers_each_state() -> None:
    online = BotStatus("online", NOW - 12, 12, 1, "0.1.0")
    offline = BotStatus("offline", NOW - 300, 300, 1, "0.1.0")
    never = BotStatus("offline", None, None, None, None)
    assert status_note(online, None) == "Last heartbeat 12 seconds ago."
    assert status_note(offline, None) == "Last heartbeat 5 minutes ago."
    assert "No heartbeat on record" in status_note(never, None)
    assert status_note(None, "Database not found.") == "Database not found."
