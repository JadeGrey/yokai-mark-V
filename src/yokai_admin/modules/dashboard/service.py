"""Dashboard data: bot liveness from the heartbeat, database vitals, recent log errors."""

from __future__ import annotations

import logging
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from yokai_admin.core.db import schema_version

logger = logging.getLogger(__name__)

ONLINE_WINDOW_S = 90
MAX_TAIL_BYTES = 256 * 1024
MAX_LINE_CHARS = 400
# The bot formats lines as "%(asctime)s [%(levelname)s] ..."; match the level in that position
# only, so an INFO line that merely contains the text "[ERROR]" is not counted.
_ERROR_LINE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \[(ERROR|CRITICAL)\]")


@dataclass(frozen=True, slots=True)
class BotStatus:
    """Whether the bot is alive according to its heartbeat row."""

    state: Literal["online", "offline"]
    last_beat_ts: Optional[int]
    age_s: Optional[int]
    pid: Optional[int]
    version: Optional[str]


@dataclass(frozen=True, slots=True)
class Overview:
    """Database vitals shown as readouts."""

    db_size_bytes: int
    schema_version: int
    wallets: int
    commands_24h: int


def bot_status(conn: sqlite3.Connection, now: int) -> BotStatus:
    """Read the heartbeat row. Online means a beat no older than `ONLINE_WINDOW_S` seconds."""
    row = conn.execute("SELECT ts, pid, version FROM bot_heartbeat WHERE id = 1;").fetchone()
    if row is None:
        return BotStatus("offline", None, None, None, None)
    age = max(0, now - int(row["ts"]))
    state: Literal["online", "offline"] = "online" if age <= ONLINE_WINDOW_S else "offline"
    return BotStatus(state, int(row["ts"]), age, int(row["pid"]), str(row["version"]))


def _scalar(conn: sqlite3.Connection, sql: str, params: tuple[object, ...] = ()) -> int:
    return int(conn.execute(sql, params).fetchone()[0])


def overview(conn: sqlite3.Connection, db_path: Path, now: int) -> Overview:
    """Collect the database vitals."""
    return Overview(
        db_size_bytes=db_path.stat().st_size,
        schema_version=schema_version(conn),
        wallets=_scalar(conn, "SELECT COUNT(*) FROM wallets;"),
        commands_24h=_scalar(
            conn, "SELECT COUNT(*) FROM command_log WHERE ts >= ?;", (now - 86_400,)
        ),
    )


def recent_errors(log_dir: Path, limit: int = 8) -> list[str]:
    """Return the last `limit` ERROR/CRITICAL lines of the current log (reads only its tail)."""
    log_file = log_dir / "yokai.log"
    try:
        with log_file.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - MAX_TAIL_BYTES))
            data = handle.read()
    except FileNotFoundError:
        return []
    except OSError as exc:
        logger.warning("Could not read the log file %s: %s", log_file, exc)
        return []

    lines = data.decode("utf-8", errors="replace").splitlines()
    if size > MAX_TAIL_BYTES and lines:
        lines = lines[1:]  # the first line of a tail read is usually cut in half
    errors = [line for line in lines if _ERROR_LINE.match(line)]
    return [line[:MAX_LINE_CHARS] for line in errors[-limit:]]


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


def format_bytes(size: int) -> str:
    """Human-readable file size."""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.2f} MB"


def status_note(status: Optional[BotStatus], db_error: Optional[str]) -> str:
    """The sentence under the status word."""
    if db_error:
        return db_error
    if status is None or status.age_s is None:
        return "No heartbeat on record. The bot has not started, or it shut down cleanly."
    return f"Last heartbeat {describe_age(status.age_s)} ago."
