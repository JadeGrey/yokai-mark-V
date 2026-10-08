"""People write side: balance adjustments and daily resets, each ledger-safe and audited."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from yokai_admin.core.audit import record
from yokai_admin.core.db import write_transaction
from yokai_admin.core.inputs import InputRefused, clean_reason, parse_user_id
from yokai_admin.core.people import UserRef, user_refs
from yokai_admin.modules.users.service import DAILY_COOLDOWN_S

ADJUST_CAP = 100_000
_MAX_AMOUNT_DIGITS = 7


def parse_amount(raw: str) -> int:
    """A signed whole number of Renown: optional + or -, then plain ASCII digits."""
    text = raw.strip()
    sign = -1 if text.startswith("-") else 1
    digits = text[1:] if text[:1] in ("+", "-") else text
    if not (digits.isascii() and digits.isdigit()):
        raise InputRefused("Enter the amount as a whole number, such as 500 or -250.")
    if len(digits) > _MAX_AMOUNT_DIGITS:  # also keeps int() away from absurdly long input
        raise InputRefused(f"One adjustment is limited to {ADJUST_CAP:,} Renown.")
    value = sign * int(digits)
    if value == 0:
        raise InputRefused("An adjustment cannot be zero.")
    if abs(value) > ADJUST_CAP:
        raise InputRefused(f"One adjustment is limited to {ADJUST_CAP:,} Renown.")
    return value


@dataclass(frozen=True, slots=True)
class AdjustPreview:
    """What an adjustment would do, shown before anything is written."""

    user: UserRef
    before: int
    requested: int
    applied: int
    after: int

    @property
    def clamped(self) -> bool:
        return self.applied != self.requested


@dataclass(frozen=True, slots=True)
class AdjustResult:
    user: UserRef
    requested: int
    applied: int
    before: int
    after: int


@dataclass(frozen=True, slots=True)
class DailyResetResult:
    user: UserRef
    previous_last_daily_ts: int
    streak: int


def _no_wallet(ref: UserRef) -> InputRefused:
    return InputRefused(
        f"{ref.label} has no wallet yet. They get one the first time they use an economy command."
    )


def _outcome(before: int, amount: int) -> tuple[int, int]:
    """(applied, after) for a requested change; a balance never goes below 0."""
    after = max(0, before + amount)
    return after - before, after


def preview_adjustment(conn: sqlite3.Connection, user_id: int, amount: int) -> AdjustPreview:
    """Work out an adjustment without writing anything (used by the preview step)."""
    ref = user_refs(conn, [user_id])[user_id]
    row = conn.execute("SELECT balance FROM wallets WHERE user_id = ?;", (user_id,)).fetchone()
    if row is None:
        raise _no_wallet(ref)
    before = int(row["balance"])
    applied, after = _outcome(before, amount)
    if applied == 0:
        raise InputRefused(f"{ref.label} already has 0 Renown, so there is nothing to deduct.")
    return AdjustPreview(ref, before, amount, applied, after)


def adjust_balance(
    db_path: Path,
    *,
    raw_id: str,
    raw_amount: str,
    raw_reason: str,
    expected_balance: int,
    now: Optional[int] = None,
) -> AdjustResult:
    """Change a balance. Wallet, ledger row and audit row commit together, and only if the
    balance is still what the owner saw in the preview."""
    user_id = parse_user_id(raw_id)
    amount = parse_amount(raw_amount)
    reason = clean_reason(raw_reason, required=True)
    ts = int(time.time()) if now is None else now
    with write_transaction(db_path) as conn:
        ref = user_refs(conn, [user_id])[user_id]
        row = conn.execute("SELECT balance FROM wallets WHERE user_id = ?;", (user_id,)).fetchone()
        if row is None:
            raise _no_wallet(ref)
        before = int(row["balance"])
        if before != expected_balance:
            raise InputRefused(
                f"The balance changed from {expected_balance:,} to {before:,} after you previewed "
                "this. Check the new balance and try again."
            )
        applied, after = _outcome(before, amount)
        if applied == 0:
            raise InputRefused(f"{ref.label} already has 0 Renown, so there is nothing to deduct.")
        conn.execute(
            "UPDATE wallets SET balance = balance + ? WHERE user_id = ?;", (applied, user_id)
        )
        conn.execute(
            "INSERT INTO renown_transactions (ts, user_id, delta, reason, counterparty_id, "
            "balance_after) VALUES (?, ?, ?, 'admin', NULL, ?);",
            (ts, user_id, applied, after),
        )
        record(
            conn,
            module="users",
            action="balance.adjust",
            target=str(user_id),
            detail={
                "name": ref.name,
                "requested": amount,
                "applied": applied,
                "before": before,
                "after": after,
            },
            reason=reason,
            now=ts,
        )
    return AdjustResult(ref, amount, applied, before, after)


def reset_daily(db_path: Path, *, raw_id: str, now: Optional[int] = None) -> DailyResetResult:
    """Let a person claim /daily now without losing their streak.

    The last claim is back-dated to exactly one cooldown ago. Clearing it to NULL instead would
    make the bot treat the next claim as day 1 and restart the streak.
    """
    user_id = parse_user_id(raw_id)
    ts = int(time.time()) if now is None else now
    with write_transaction(db_path) as conn:
        ref = user_refs(conn, [user_id])[user_id]
        row = conn.execute(
            "SELECT last_daily_ts, daily_streak FROM wallets WHERE user_id = ?;", (user_id,)
        ).fetchone()
        if row is None:
            raise _no_wallet(ref)
        if row["last_daily_ts"] is None:
            raise InputRefused(
                f"{ref.label} has never claimed a daily reward, so there is nothing to reset."
            )
        previous = int(row["last_daily_ts"])
        if ts - previous >= DAILY_COOLDOWN_S:
            raise InputRefused(f"{ref.label} can already claim their daily reward.")
        conn.execute(
            "UPDATE wallets SET last_daily_ts = ? WHERE user_id = ?;",
            (ts - DAILY_COOLDOWN_S, user_id),
        )
        streak = int(row["daily_streak"])
        record(
            conn,
            module="users",
            action="daily.reset",
            target=str(user_id),
            detail={"name": ref.name, "previous_last_daily_ts": previous, "streak": streak},
            now=ts,
        )
    return DailyResetResult(ref, previous, streak)
