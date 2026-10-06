"""Tests for the audit log, the people helper and the formatting helpers."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from tests.admin_helpers import seed_database
from yokai_admin.core.audit import ACTOR, latest_ts, list_entries, modules_seen, record
from yokai_admin.core.db import readonly, write_transaction
from yokai_admin.core.formatting import describe_age, format_when
from yokai_admin.core.people import UserRef, user_refs


def _db(tmp_path: Path, **seed: object) -> Path:
    path = tmp_path / "b.db"
    seed_database(path, **seed)  # type: ignore[arg-type]
    return path


def test_record_stores_every_field_and_returns_the_id(tmp_path: Path) -> None:
    path = _db(tmp_path)
    with write_transaction(path) as conn:
        entry_id = record(
            conn,
            module="access",
            action="blacklist.add",
            target="42",
            detail={"name": "Sami", "previous_reason": None},
            reason="spam",
            now=1000,
        )
    with readonly(path) as conn:
        entries, total = list_entries(conn)
    assert total == 1 and entries[0].id == entry_id
    assert (entries[0].ts, entries[0].actor, entries[0].module) == (1000, ACTOR, "access")
    assert (entries[0].action, entries[0].target) == ("blacklist.add", "42")
    assert entries[0].reason == "spam"
    assert entries[0].detail == {"name": "Sami", "previous_reason": None}


def test_entries_come_newest_first_with_filters_and_paging(tmp_path: Path) -> None:
    path = _db(tmp_path)
    with write_transaction(path) as conn:
        for i in range(30):
            record(
                conn,
                module="access" if i % 2 == 0 else "users",
                action="x",
                target=str(i % 3),
                now=100 + i,
            )
    with readonly(path) as conn:
        everything, total = list_entries(conn, limit=25)
        assert total == 30 and len(everything) == 25 and everything[0].ts == 129
        second_page, _ = list_entries(conn, limit=25, offset=25)
        assert len(second_page) == 5
        only_access, access_total = list_entries(conn, module="access")
        assert access_total == 15 and {e.module for e in only_access} == {"access"}
        one_target, target_total = list_entries(conn, module="access", target="0")
        assert {e.target for e in one_target} == {"0"} and target_total == len(one_target)
        assert modules_seen(conn) == ["access", "users"]
        assert latest_ts(conn) == 129


def test_an_empty_log_has_no_latest_timestamp(tmp_path: Path) -> None:
    with readonly(_db(tmp_path)) as conn:
        assert latest_ts(conn) is None and list_entries(conn) == ([], 0)


def test_malformed_detail_json_never_raises(tmp_path: Path) -> None:
    path = _db(tmp_path)
    raw = sqlite3.connect(path)
    raw.execute(
        "INSERT INTO admin_audit (ts, actor, module, action, detail_json) "
        "VALUES (1, 'panel', 'm', 'a', '{oops');"
    )
    raw.commit()
    raw.close()
    with readonly(path) as conn:
        entries, _ = list_entries(conn)
    assert entries[0].detail == {"raw": "{oops"}


def test_user_refs_resolve_known_and_unknown_people(tmp_path: Path) -> None:
    path = _db(tmp_path, names={1: "Sami", 2: "Alex"})
    with readonly(path) as conn:
        refs = user_refs(conn, [1, 3, 1])
        assert user_refs(conn, []) == {}
    assert refs[1] == UserRef(1, "Sami") and refs[3] == UserRef(3, None)
    assert refs[1].label == "Sami (1)" and refs[3].label == "Unknown user (3)"


def test_user_refs_handles_many_ids(tmp_path: Path) -> None:
    path = _db(tmp_path, names={5: "Five"})
    with readonly(path) as conn:
        refs = user_refs(conn, range(1, 1201))
    assert len(refs) == 1200 and refs[5].name == "Five"


@pytest.mark.parametrize(
    ("seconds", "text"),
    [(0, "0 seconds"), (1, "1 second"), (300, "5 minutes"), (3 * 86400, "3 days")],
)
def test_describe_age_lives_in_formatting(seconds: int, text: str) -> None:
    assert describe_age(seconds) == text


def test_format_when_is_local_time_or_a_dash() -> None:
    ts = 1_790_000_000
    assert format_when(ts) == datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    assert format_when(None) == "–"
