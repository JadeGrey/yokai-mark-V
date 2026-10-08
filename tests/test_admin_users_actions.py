"""Tests for the People write side: parsing, clamping, atomicity, stale previews and races."""

from __future__ import annotations

import random
import sqlite3
import threading
from pathlib import Path

import pytest

from tests.admin_helpers import seed_activity, seed_database
from yokai.storage.db import Database
from yokai_admin.core import db as panel_db
from yokai_admin.core.audit import AuditEntry, list_entries
from yokai_admin.core.db import readonly
from yokai_admin.core.inputs import InputRefused
from yokai_admin.errors import PanelDbError
from yokai_admin.modules.users import actions
from yokai_admin.modules.users.actions import (
    ADJUST_CAP,
    adjust_balance,
    parse_amount,
    preview_adjustment,
    reset_daily,
)

NOW = 2_000_000


def _db(tmp_path: Path, **seed: object) -> Path:
    path = tmp_path / "b.db"
    seed_database(path, **seed)  # type: ignore[arg-type]
    return path


def _balance(path: Path, user_id: int) -> int:
    with readonly(path) as conn:
        row = conn.execute("SELECT balance FROM wallets WHERE user_id = ?;", (user_id,)).fetchone()
        return int(row[0])


def _ledger(path: Path, user_id: int) -> list[tuple[int, str, int]]:
    with readonly(path) as conn:
        rows = conn.execute(
            "SELECT delta, reason, balance_after FROM renown_transactions "
            "WHERE user_id = ? ORDER BY id;",
            (user_id,),
        )
        return [(int(r[0]), str(r[1]), int(r[2])) for r in rows]


def _ledger_is_true(path: Path, user_id: int) -> bool:
    ledger = _ledger(path, user_id)
    return _balance(path, user_id) == sum(d for d, _, _ in ledger) == ledger[-1][2]


def _audit(path: Path) -> list[AuditEntry]:
    with readonly(path) as conn:
        return list_entries(conn)[0]


@pytest.mark.parametrize(
    ("raw", "value"),
    [
        ("500", 500),
        ("+500", 500),
        ("-250", -250),
        (" 42 ", 42),
        ("000123", 123),
        (str(ADJUST_CAP), ADJUST_CAP),
        (f"-{ADJUST_CAP}", -ADJUST_CAP),
    ],
)
def test_valid_amounts_parse(raw: str, value: int) -> None:
    assert parse_amount(raw) == value


@pytest.mark.parametrize(
    "raw",
    [
        "",
        " ",
        "0",
        "+0",
        "-0",
        "abc",
        "1e3",
        "1,000",
        "1 000",
        "12.5",
        "--5",
        "+-5",
        "−5",  # Unicode minus sign
        "٥٠٠",  # Arabic-Indic digits
        "５００",  # full-width digits
        str(ADJUST_CAP + 1),
        f"-{ADJUST_CAP + 1}",
        "9" * 8,
        "9" * 5000,
    ],
)
def test_invalid_amounts_are_refused(raw: str) -> None:
    """Review Focus 3: Unicode minus, other-script digits, separators, zero, over the cap, huge."""
    with pytest.raises(InputRefused):
        parse_amount(raw)


def test_the_cap_message_names_the_limit() -> None:
    with pytest.raises(InputRefused, match="100,000"):
        parse_amount("100001")


def test_a_positive_adjustment_changes_wallet_ledger_and_audit_log_together(
    tmp_path: Path,
) -> None:
    path = _db(tmp_path, credits={1: 300}, names={1: "Alex"})  # balance 500
    result = adjust_balance(
        path,
        raw_id="1",
        raw_amount="+250",
        raw_reason="  prize for the event ",
        expected_balance=500,
        now=NOW,
    )
    assert (result.before, result.applied, result.after, result.user.name) == (
        500,
        250,
        750,
        "Alex",
    )
    assert _balance(path, 1) == 750 and _ledger_is_true(path, 1)
    assert _ledger(path, 1)[-1] == (250, "admin", 750)
    (entry,) = _audit(path)
    assert (entry.module, entry.action, entry.target) == ("users", "balance.adjust", "1")
    assert entry.reason == "prize for the event"
    assert entry.detail == {
        "name": "Alex",
        "requested": 250,
        "applied": 250,
        "before": 500,
        "after": 750,
    }


def test_a_deduction_larger_than_the_balance_ends_at_zero_and_records_the_applied_amount(
    tmp_path: Path,
) -> None:
    """Review Focus 4."""
    path = _db(tmp_path, credits={1: 300})  # 500
    result = adjust_balance(
        path, raw_id="1", raw_amount="-900", raw_reason="reset after exploit", expected_balance=500
    )
    assert (result.requested, result.applied, result.after) == (-900, -500, 0)
    assert _balance(path, 1) == 0 and _ledger_is_true(path, 1)
    assert _ledger(path, 1)[-1] == (-500, "admin", 0)
    detail = _audit(path)[0].detail
    assert detail["requested"] == -900 and detail["applied"] == -500


def test_deducting_from_an_empty_wallet_is_refused_and_writes_nothing(tmp_path: Path) -> None:
    path = _db(tmp_path, credits={1: 300})
    adjust_balance(path, raw_id="1", raw_amount="-500", raw_reason="to zero", expected_balance=500)
    before = (_balance(path, 1), len(_ledger(path, 1)), len(_audit(path)))
    with pytest.raises(InputRefused, match="nothing to deduct"):
        adjust_balance(path, raw_id="1", raw_amount="-1", raw_reason="again", expected_balance=0)
    assert (_balance(path, 1), len(_ledger(path, 1)), len(_audit(path))) == before


def test_a_reason_is_required_and_cleaned(tmp_path: Path) -> None:
    path = _db(tmp_path, credits={1: 300})
    with pytest.raises(InputRefused, match="reason"):
        adjust_balance(path, raw_id="1", raw_amount="5", raw_reason="x", expected_balance=500)
    assert _balance(path, 1) == 500


def test_a_person_without_a_wallet_is_refused_with_an_explanation(tmp_path: Path) -> None:
    path = _db(tmp_path, names={3: "Riley"})
    with pytest.raises(InputRefused, match="no wallet"):
        adjust_balance(path, raw_id="3", raw_amount="5", raw_reason="gift", expected_balance=0)
    with readonly(path) as conn:
        with pytest.raises(InputRefused, match="no wallet"):
            preview_adjustment(conn, 3, 5)


def test_a_stale_preview_is_refused_and_changes_nothing(tmp_path: Path) -> None:
    """The confirm carries the balance the preview saw; if it moved, nothing is written."""
    path = _db(tmp_path, credits={1: 300})
    with pytest.raises(InputRefused, match="changed from 400 to 500"):
        adjust_balance(path, raw_id="1", raw_amount="5", raw_reason="gift", expected_balance=400)
    assert _balance(path, 1) == 500 and _audit(path) == []


def test_two_confirms_from_the_same_preview_apply_exactly_once(tmp_path: Path) -> None:
    """Review Focus 2: BEGIN IMMEDIATE plus the expected-balance check make a double click safe."""
    path = _db(tmp_path, credits={1: 300})
    outcomes: list[str] = []
    barrier = threading.Barrier(2)

    def confirm() -> None:
        barrier.wait()
        try:
            adjust_balance(
                path,
                raw_id="1",
                raw_amount="+100",
                raw_reason="double click",
                expected_balance=500,
            )
            outcomes.append("applied")
        except InputRefused:
            outcomes.append("refused")

    threads = [threading.Thread(target=confirm) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
    assert sorted(outcomes) == ["applied", "refused"]
    assert _balance(path, 1) == 600 and _ledger_is_true(path, 1) and len(_audit(path)) == 1


def test_a_failing_audit_write_rolls_back_the_wallet_and_the_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review Focus 1."""
    path = _db(tmp_path, credits={1: 300})
    ledger_before = _ledger(path, 1)

    def broken(*args: object, **kwargs: object) -> int:
        raise RuntimeError("audit table is broken")

    monkeypatch.setattr(actions, "record", broken)
    with pytest.raises(RuntimeError):
        adjust_balance(path, raw_id="1", raw_amount="+100", raw_reason="gift", expected_balance=500)
    assert _balance(path, 1) == 500 and _ledger(path, 1) == ledger_before


def test_an_old_schema_and_a_busy_database_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = tmp_path / "old.db"
    raw = sqlite3.connect(old)
    raw.execute("PRAGMA user_version = 4;")
    raw.commit()
    raw.close()
    with pytest.raises(PanelDbError):
        adjust_balance(old, raw_id="1", raw_amount="5", raw_reason="gift", expected_balance=0)

    busy = tmp_path / "busy"
    busy.mkdir()
    path = _db(busy, credits={1: 300})
    monkeypatch.setattr(panel_db, "WRITE_BUSY_TIMEOUT_S", 0.2)
    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE;")
    try:
        with pytest.raises(PanelDbError, match="busy"):
            adjust_balance(
                path, raw_id="1", raw_amount="5", raw_reason="gift", expected_balance=500
            )
    finally:
        holder.execute("ROLLBACK;")
        holder.close()


async def test_panel_and_bot_adjustments_keep_one_consistent_ledger(tmp_path: Path) -> None:
    """The bot's /economy-admin path and the panel, in turn, on one file."""
    path = tmp_path / "b.db"
    bot_db = Database(path)
    await bot_db.initialize()
    try:
        await bot_db.ensure_wallet(1, 200)
        await bot_db.credit_wallet(1, 300, "daily", 200)
        adjust_balance(
            path, raw_id="1", raw_amount="+100", raw_reason="panel gift", expected_balance=500
        )
        await bot_db.admin_adjust_wallet(1, -50, 200)
        adjust_balance(
            path, raw_id="1", raw_amount="-1000", raw_reason="panel wipe", expected_balance=550
        )
        wallet = await bot_db.get_wallet(1)
    finally:
        await bot_db.close()
    assert wallet is not None and wallet.balance == 0 and _ledger_is_true(path, 1)


def test_the_preview_reports_clamping_without_writing(tmp_path: Path) -> None:
    path = _db(tmp_path, credits={1: 300}, names={1: "Alex"})
    with readonly(path) as conn:
        clamped = preview_adjustment(conn, 1, -900)
        plain = preview_adjustment(conn, 1, 100)
    assert (clamped.before, clamped.requested, clamped.applied) == (500, -900, -500)
    assert (clamped.after, clamped.clamped) == (0, True)
    assert (plain.after, plain.clamped) == (600, False)
    assert _balance(path, 1) == 500 and _audit(path) == []


def test_daily_reset_back_dates_the_last_claim_by_a_day_and_keeps_the_streak(
    tmp_path: Path,
) -> None:
    path = _db(tmp_path, credits={1: 300}, names={1: "Alex"})
    seed_activity(path, daily={1: (NOW - 3_600, 4)})
    result = reset_daily(path, raw_id="1", now=NOW)
    assert (result.previous_last_daily_ts, result.streak, result.user.name) == (
        NOW - 3_600,
        4,
        "Alex",
    )
    with readonly(path) as conn:
        row = conn.execute(
            "SELECT last_daily_ts, daily_streak, balance FROM wallets WHERE user_id = 1;"
        ).fetchone()
    assert (row[0], row[1], row[2]) == (
        NOW - 86_400,
        4,
        500,
    )  # claimable, streak kept, balance kept
    (entry,) = _audit(path)
    assert (entry.action, entry.target) == ("daily.reset", "1")
    assert entry.detail == {"name": "Alex", "previous_last_daily_ts": NOW - 3_600, "streak": 4}


def test_a_reset_that_would_do_nothing_is_refused(tmp_path: Path) -> None:
    path = _db(tmp_path, credits={1: 300, 2: 300, 3: 300}, names={4: "Riley"})
    seed_activity(path, daily={1: (None, 0), 2: (NOW - 90_000, 3)})
    with pytest.raises(InputRefused, match="never claimed"):
        reset_daily(path, raw_id="1", now=NOW)
    with pytest.raises(InputRefused, match="already claim"):
        reset_daily(path, raw_id="2", now=NOW)
    with pytest.raises(InputRefused, match="no wallet"):
        reset_daily(path, raw_id="4", now=NOW)
    assert _audit(path) == []


async def test_after_a_panel_reset_the_bot_lets_the_person_claim_and_continue_the_streak(
    tmp_path: Path,
) -> None:
    path = tmp_path / "b.db"
    bot_db = Database(path)
    await bot_db.initialize()
    try:
        await bot_db.ensure_wallet(1, 200)
        await bot_db.claim_daily(1, 100, 200, now=NOW - 7_200, rng=random.Random(1))  # day 1
        reset_daily(path, raw_id="1", now=NOW)
        claim = await bot_db.claim_daily(1, 100, 200, now=NOW, rng=random.Random(1))
    finally:
        await bot_db.close()
    assert claim.streak_day == 2  # the streak continued instead of restarting
    assert _ledger_is_true(path, 1)
