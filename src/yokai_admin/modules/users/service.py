"""People read side: the list with search, and the aggregates shown on a profile."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional

from yokai_admin.core.people import UserRef, user_refs

# Mirrors yokai.economy.daily.DAILY_COOLDOWN_S (the panel does not import the bot package);
# tests/test_admin_users_service.py fails if the two ever differ.
DAILY_COOLDOWN_S = 86_400
PAGE_SIZE = 25
TRANSACTION_LIMIT = 25
TOP_LIMIT = 5
_LABELS = {
    "daily": "Daily reward",
    "welcome": "Welcome bonus",
    "voice": "Voice time",
    "command": "Music command",
    "admin": "Adjusted by an admin",
    "pay_sent": "Sent to",
    "pay_received": "Received from",
}
_GAME_LABELS = {
    "bet": "Bet",
    "win": "Win",
    "push": "Push (stake returned)",
    "payout": "Payout",
    "refund": "Refund",
}


def reason_label(reason: str) -> str:
    """Plain words for a ledger reason; unknown reasons are shown as written."""
    if reason in _LABELS:
        return _LABELS[reason]
    prefix, separator, game = reason.partition(":")
    if separator and prefix in _GAME_LABELS and game:
        return f"{_GAME_LABELS[prefix]} ({game})"
    return reason


@dataclass(frozen=True, slots=True)
class Person:
    """One row of the people list."""

    user: UserRef
    balance: Optional[int]
    last_seen_ts: Optional[int]


def _like(fragment: str) -> str:
    """A LIKE pattern that matches the fragment literally (the escape character is backslash)."""
    escaped = fragment.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def list_people(
    conn: sqlite3.Connection, *, query: str, limit: int, offset: int
) -> tuple[list[Person], int]:
    """Everyone with a wallet or a remembered name, most recently active first."""
    base = (
        "FROM (SELECT user_id FROM wallets UNION SELECT user_id FROM user_names) p "
        "LEFT JOIN user_names n ON n.user_id = p.user_id "
        "LEFT JOIN wallets w ON w.user_id = p.user_id"
    )
    where = ""
    params: list[object] = []
    if query:
        pattern = _like(query)
        name_match = "n.display_name LIKE ? ESCAPE '\\'"
        if query.isascii() and query.isdigit():
            where = f" WHERE ({name_match} OR CAST(p.user_id AS TEXT) LIKE ? ESCAPE '\\')"
            params = [pattern, pattern]
        else:
            where = f" WHERE {name_match}"
            params = [pattern]
    total = int(conn.execute(f"SELECT COUNT(*) {base}{where};", params).fetchone()[0])
    rows = conn.execute(
        "SELECT p.user_id AS user_id, n.display_name AS name, w.balance AS balance, "
        "(SELECT MAX(c.ts) FROM command_log c WHERE c.user_id = p.user_id) AS last_seen "
        f"{base}{where} "
        "ORDER BY last_seen IS NULL, last_seen DESC, w.balance IS NULL, w.balance DESC, p.user_id "
        "LIMIT ? OFFSET ?;",
        [*params, limit, offset],
    ).fetchall()
    people = [
        Person(
            UserRef(int(r["user_id"]), None if r["name"] is None else str(r["name"])),
            None if r["balance"] is None else int(r["balance"]),
            None if r["last_seen"] is None else int(r["last_seen"]),
        )
        for r in rows
    ]
    return people, total


@dataclass(frozen=True, slots=True)
class WalletView:
    balance: int
    last_daily_ts: Optional[int]
    daily_streak: int
    created_ts: int
    daily_ready_in: Optional[int]  # seconds until /daily works; None means it works now


@dataclass(frozen=True, slots=True)
class Txn:
    ts: int
    delta: int
    reason: str
    label: str
    balance_after: int
    counterparty: Optional[UserRef]


@dataclass(frozen=True, slots=True)
class GameStat:
    game: str
    plays: int
    wagered: int
    returned: int

    @property
    def net(self) -> int:
        return self.returned - self.wagered


@dataclass(frozen=True, slots=True)
class TrackStat:
    title: str
    artist: Optional[str]
    plays: int


@dataclass(frozen=True, slots=True)
class CommandStat:
    command: str
    count: int


@dataclass(frozen=True, slots=True)
class Profile:
    user: UserRef
    wallet: Optional[WalletView]
    transactions: list[Txn]
    games: list[GameStat]
    tracks: list[TrackStat]
    commands: list[CommandStat]
    commands_total: int
    first_seen_ts: Optional[int]
    last_seen_ts: Optional[int]


def _daily_ready_in(last_daily_ts: Optional[int], now: int) -> Optional[int]:
    if last_daily_ts is None:
        return None
    elapsed = now - last_daily_ts
    return None if elapsed >= DAILY_COOLDOWN_S else DAILY_COOLDOWN_S - elapsed


def load_profile(conn: sqlite3.Connection, user_id: int, now: int) -> Optional[Profile]:
    """Everything shown on a person's page, or None if the bot knows nothing about the ID."""
    user = user_refs(conn, [user_id])[user_id]
    wallet_row = conn.execute(
        "SELECT balance, last_daily_ts, daily_streak, created_ts FROM wallets WHERE user_id = ?;",
        (user_id,),
    ).fetchone()
    count, first, last = conn.execute(
        "SELECT COUNT(*), MIN(ts), MAX(ts) FROM command_log WHERE user_id = ?;", (user_id,)
    ).fetchone()
    if wallet_row is None and user.name is None and int(count) == 0:
        return None

    wallet: Optional[WalletView] = None
    if wallet_row is not None:
        raw_daily = wallet_row["last_daily_ts"]
        last_daily = None if raw_daily is None else int(raw_daily)
        wallet = WalletView(
            balance=int(wallet_row["balance"]),
            last_daily_ts=last_daily,
            daily_streak=int(wallet_row["daily_streak"]),
            created_ts=int(wallet_row["created_ts"]),
            daily_ready_in=_daily_ready_in(last_daily, now),
        )

    txn_rows = conn.execute(
        "SELECT ts, delta, reason, counterparty_id, balance_after FROM renown_transactions "
        "WHERE user_id = ? ORDER BY id DESC LIMIT ?;",
        (user_id, TRANSACTION_LIMIT),
    ).fetchall()
    others = user_refs(
        conn, [int(r["counterparty_id"]) for r in txn_rows if r["counterparty_id"] is not None]
    )
    transactions = [
        Txn(
            ts=int(r["ts"]),
            delta=int(r["delta"]),
            reason=str(r["reason"]),
            label=reason_label(str(r["reason"])),
            balance_after=int(r["balance_after"]),
            counterparty=(
                others.get(int(r["counterparty_id"])) if r["counterparty_id"] is not None else None
            ),
        )
        for r in txn_rows
    ]

    games = [
        GameStat(str(r[0]), int(r[1]), int(r[2]), int(r[3]))
        for r in conn.execute(
            "SELECT game, COUNT(DISTINCT COALESCE(group_id, id)), SUM(stake), SUM(payout) "
            "FROM gambling_bets WHERE user_id = ? AND status = 'settled' "
            "GROUP BY game ORDER BY game;",
            (user_id,),
        )
    ]
    tracks = [
        TrackStat(
            str(r["title"]), None if r["artist"] is None else str(r["artist"]), int(r["plays"])
        )
        for r in conn.execute(
            "SELECT MAX(title) AS title, MAX(artist) AS artist, COUNT(*) AS plays "
            "FROM play_events WHERE user_id = ? GROUP BY video_id "
            "ORDER BY plays DESC, MAX(ts) DESC LIMIT ?;",
            (user_id, TOP_LIMIT),
        )
    ]
    commands = [
        CommandStat(str(r[0]), int(r[1]))
        for r in conn.execute(
            "SELECT command, COUNT(*) AS n FROM command_log WHERE user_id = ? "
            "GROUP BY command ORDER BY n DESC, command LIMIT ?;",
            (user_id, TOP_LIMIT),
        )
    ]
    return Profile(
        user=user,
        wallet=wallet,
        transactions=transactions,
        games=games,
        tracks=tracks,
        commands=commands,
        commands_total=int(count),
        first_seen_ts=None if first is None else int(first),
        last_seen_ts=None if last is None else int(last),
    )
