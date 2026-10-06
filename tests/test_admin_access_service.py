"""Tests for the Access rules: validation, the owner guard, atomic writes and the bot's view."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from tests.admin_helpers import seed_database
from yokai.moderation.store import ModerationStore
from yokai.storage.db import Database
from yokai_admin.core.audit import AuditEntry, list_entries
from yokai_admin.core.db import readonly
from yokai_admin.errors import PanelDbError
from yokai_admin.modules.access import service
from yokai_admin.modules.access.service import (
    AccessRefused,
    add_admin,
    add_blacklist,
    clean_reason,
    list_admins,
    list_blacklist,
    parse_user_id,
    remove_admin,
    remove_blacklist,
)

OWNER = 111


def _db(tmp_path: Path, **seed: object) -> Path:
    path = tmp_path / "b.db"
    seed_database(path, **seed)  # type: ignore[arg-type]
    return path


def _rows(path: Path, table: str) -> list[tuple[object, ...]]:
    with readonly(path) as conn:
        return [tuple(r) for r in conn.execute(f"SELECT * FROM {table};")]  # test-only fixed names


def _audit(path: Path) -> list[AuditEntry]:
    with readonly(path) as conn:
        return list_entries(conn)[0]


@pytest.mark.parametrize(
    ("raw", "value"),
    [("123", 123), ("  456  ", 456), ("000789", 789), (str(2**63 - 1), 2**63 - 1)],
)
def test_valid_ids_parse(raw: str, value: int) -> None:
    assert parse_user_id(raw) == value


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "  ",
        "abc",
        "12 34",
        "+5",
        "-5",
        "1e3",
        "0x10",
        "12.5",
        "0",
        "00",
        str(2**63),
        "١٢٣",  # Arabic-Indic digits
        "１２",  # full-width digits
    ],
)
def test_invalid_ids_are_refused(raw: str) -> None:
    """Review Focus 1: int() would accept the non-ASCII digits; the panel must not."""
    with pytest.raises(AccessRefused):
        parse_user_id(raw)


def test_clean_reason_strips_unsafe_characters_and_enforces_length() -> None:
    assert clean_reason("  spam \n\t in‮ chat  ", required=True) == "spam in chat"
    assert clean_reason("", required=False) == ""
    with pytest.raises(AccessRefused, match="reason"):
        clean_reason("ab", required=True)
    with pytest.raises(AccessRefused, match="200"):
        clean_reason("x" * 201, required=False)


def test_adding_to_the_blacklist_writes_the_row_and_the_audit_entry_together(
    tmp_path: Path,
) -> None:
    path = _db(tmp_path, names={42: "Mallory"})
    ref = add_blacklist(
        path, owner_id=OWNER, raw_id="42", raw_reason="spamming  the queue", now=500
    )
    assert ref.label == "Mallory (42)"
    assert _rows(path, "blacklist") == [(42, "spamming the queue", 500, "panel")]
    (entry,) = _audit(path)
    assert (entry.module, entry.action, entry.target) == ("access", "blacklist.add", "42")
    assert entry.reason == "spamming the queue" and entry.detail == {"name": "Mallory"}


@pytest.mark.parametrize("raw", ["111", " 111 ", "000111"])
def test_the_owner_can_never_be_added_to_either_list(tmp_path: Path, raw: str) -> None:
    """Review Focus 1: refused however it is written, and nothing is written at all."""
    path = _db(tmp_path)
    with pytest.raises(AccessRefused, match="owner"):
        add_blacklist(path, owner_id=OWNER, raw_id=raw, raw_reason="because")
    with pytest.raises(AccessRefused, match="owner"):
        add_admin(path, owner_id=OWNER, raw_id=raw, raw_note="")
    assert _rows(path, "blacklist") == [] and _rows(path, "admins") == [] and _audit(path) == []


def test_duplicates_and_missing_entries_are_refused(tmp_path: Path) -> None:
    path = _db(tmp_path, blacklist=[(5, "spam")], admins=[7])
    with pytest.raises(AccessRefused, match="already on the blacklist"):
        add_blacklist(path, owner_id=OWNER, raw_id="5", raw_reason="again")
    with pytest.raises(AccessRefused, match="already an admin"):
        add_admin(path, owner_id=OWNER, raw_id="7", raw_note="")
    with pytest.raises(AccessRefused, match="not on the blacklist"):
        remove_blacklist(path, raw_id="99")
    with pytest.raises(AccessRefused, match="not an admin"):
        remove_admin(path, raw_id="99")
    assert _audit(path) == []


def test_a_blacklisted_person_cannot_be_made_an_admin(tmp_path: Path) -> None:
    path = _db(tmp_path, blacklist=[(5, "spam")])
    with pytest.raises(AccessRefused, match="Remove them from the blacklist"):
        add_admin(path, owner_id=OWNER, raw_id="5", raw_note="")
    assert _rows(path, "admins") == []


def test_blacklisting_an_admin_keeps_the_admin_row_and_flags_both_lists(tmp_path: Path) -> None:
    path = _db(tmp_path, admins=[7])
    add_blacklist(path, owner_id=OWNER, raw_id="7", raw_reason="compromised account")
    assert _rows(path, "admins")[0][0] == 7  # the bot lets the blacklist win
    with readonly(path) as conn:
        (blacklisted,) = list_blacklist(conn)
        (admin,) = list_admins(conn)
    assert "admin list" in (blacklisted.flag or "")
    assert "blacklisted" in (admin.flag or "").lower()


def test_removals_record_what_was_removed(tmp_path: Path) -> None:
    path = _db(tmp_path, blacklist=[(5, "spam")], admins=[7], names={5: "Mallory", 7: "Alex"})
    remove_blacklist(path, raw_id="5", now=900)
    remove_admin(path, raw_id="7", now=901)
    assert _rows(path, "blacklist") == [] and _rows(path, "admins") == []
    newest, oldest = _audit(path)
    assert oldest.action == "blacklist.remove"
    assert oldest.detail == {"name": "Mallory", "previous_reason": "spam"}
    assert (newest.action, newest.detail) == ("admin.remove", {"name": "Alex"})


def test_an_admin_note_goes_to_the_audit_log_only(tmp_path: Path) -> None:
    path = _db(tmp_path)
    add_admin(path, owner_id=OWNER, raw_id="7", raw_note="helps with moderation")
    assert _rows(path, "admins")[0][0] == 7
    (entry,) = _audit(path)
    assert (entry.action, entry.reason) == ("admin.add", "helps with moderation")


def test_a_failing_audit_write_rolls_the_change_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review Focus 2: no change without its audit row."""
    path = _db(tmp_path)

    def broken_record(*args: object, **kwargs: object) -> int:
        raise RuntimeError("audit table is broken")

    monkeypatch.setattr(service, "record", broken_record)
    with pytest.raises(RuntimeError):
        add_blacklist(path, owner_id=OWNER, raw_id="42", raw_reason="spamming")
    assert _rows(path, "blacklist") == []


def test_two_simultaneous_adds_leave_one_row_and_one_audit_entry(tmp_path: Path) -> None:
    """Review Focus 2: BEGIN IMMEDIATE serialises the writers; the loser is refused cleanly."""
    path = _db(tmp_path)
    outcomes: list[str] = []
    barrier = threading.Barrier(2)

    def attempt() -> None:
        barrier.wait()
        try:
            add_blacklist(path, owner_id=OWNER, raw_id="42", raw_reason="spamming")
            outcomes.append("added")
        except AccessRefused:
            outcomes.append("refused")

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
    assert sorted(outcomes) == ["added", "refused"]
    assert len(_rows(path, "blacklist")) == 1 and len(_audit(path)) == 1


def test_writes_to_an_old_schema_are_refused(tmp_path: Path) -> None:
    old = tmp_path / "old.db"
    raw = sqlite3.connect(old)
    raw.execute("PRAGMA user_version = 4;")
    raw.commit()
    raw.close()
    with pytest.raises(PanelDbError):
        add_blacklist(old, owner_id=OWNER, raw_id="42", raw_reason="spamming")


async def test_the_bot_sees_the_blacklist_change_made_by_the_panel(tmp_path: Path) -> None:
    """Review Focus 5: a ModerationStore on the same file picks the change up."""
    path = tmp_path / "b.db"
    bot_db = Database(path)
    await bot_db.initialize()
    try:
        add_blacklist(path, owner_id=OWNER, raw_id="42", raw_reason="spamming")
        add_admin(path, owner_id=OWNER, raw_id="7", raw_note="")
        store = ModerationStore(bot_db, owner_id=OWNER, ttl_s=0)
        assert await store.is_blacklisted(42) is True and await store.is_admin(7) is True
        remove_blacklist(path, raw_id="42")
        assert await store.is_blacklisted(42) is False
    finally:
        await bot_db.close()
