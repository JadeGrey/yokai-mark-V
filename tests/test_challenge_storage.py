"""Challenge storage: migration, slates, atomic progress and exactly-once payout."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from yokai.storage.db import CURRENT_SCHEMA_VERSION, Database

WELCOME = 200


@pytest.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "t.db")
    try:
        await database.initialize()
        yield database
    finally:
        await database.close()


async def _challenge_rows(db: Database, user_id: int) -> int:
    async with db.conn.execute(
        "SELECT COUNT(*) FROM renown_transactions WHERE user_id = ? AND reason LIKE 'challenge:%';",
        (user_id,),
    ) as cursor:
        row = await cursor.fetchone()
    return row[0]


async def test_schema_is_at_least_v8_with_the_challenge_tables(db: Database) -> None:
    assert CURRENT_SCHEMA_VERSION >= 8
    async with db.conn.execute(
        "SELECT name FROM sqlite_master WHERE name LIKE 'challenge_%';"
    ) as cursor:
        names = {row[0] for row in await cursor.fetchall()}
    assert {"challenge_slates", "challenge_progress"} <= names


async def test_a_v7_database_upgrades_and_keeps_wallets(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    first = Database(path)
    try:
        await first.initialize()
        await first.credit_wallet(1, 50, "admin", WELCOME)
        await first.conn.execute("DROP TABLE challenge_slates;")
        await first.conn.execute("DROP TABLE challenge_progress;")
        await first.conn.execute("PRAGMA user_version = 7;")
        await first.conn.commit()
    finally:
        await first.close()

    upgraded = Database(path)
    try:
        await upgraded.initialize()
        wallet = await upgraded.get_wallet(1)
        assert wallet is not None and wallet.balance == WELCOME + 50
        assert await upgraded.get_challenge_slate("daily", "2026-10-13") is None
    finally:
        await upgraded.close()


async def test_first_slate_wins_and_empty_slates_are_allowed(db: Database) -> None:
    assert await db.get_challenge_slate("daily", "k") is None
    assert await db.create_challenge_slate("daily", "k", ["a", "b"]) == ["a", "b"]
    assert await db.create_challenge_slate("daily", "k", ["x"]) == ["a", "b"]
    assert await db.create_challenge_slate("weekly", "k", []) == []
    assert await db.get_challenge_slate("weekly", "k") == []


async def test_counter_progress_caps_and_completes_once(db: Database) -> None:
    first = await db.advance_challenge(
        7, "daily", "k", "t", target=3, reward=40, welcome_bonus=WELCOME, delta=2
    )
    assert (first.progress, first.completed_now) == (2, False)
    done = await db.advance_challenge(
        7, "daily", "k", "t", target=3, reward=40, welcome_bonus=WELCOME, delta=5
    )
    assert (done.progress, done.completed_now) == (3, True)
    again = await db.advance_challenge(
        7, "daily", "k", "t", target=3, reward=40, welcome_bonus=WELCOME, delta=1
    )
    assert (again.progress, again.completed_now) == (3, False)
    wallet = await db.get_wallet(7)
    assert wallet is not None and wallet.balance == WELCOME + 40  # welcome bonus + one reward
    assert await _challenge_rows(db, 7) == 1


async def test_distinct_items_count_once(db: Database) -> None:
    for item in ("a", "a", "b", "a"):
        result = await db.advance_challenge(
            1, "weekly", "k", "t", target=3, reward=10, welcome_bonus=WELCOME, item=item
        )
    assert result.progress == 2
    result = await db.advance_challenge(
        1, "weekly", "k", "t", target=3, reward=10, welcome_bonus=WELCOME, item="c"
    )
    assert result.completed_now


async def test_long_distinct_items_are_deduplicated(db: Database) -> None:
    long_item = "x" * 150
    for _ in range(4):
        result = await db.advance_challenge(
            1, "weekly", "k", "t", target=3, reward=10, welcome_bonus=WELCOME, item=long_item
        )
    assert result.progress == 1
    assert not result.completed_now


async def test_payout_is_exactly_once_under_concurrency(db: Database) -> None:
    results = await asyncio.gather(
        *(
            db.advance_challenge(
                9, "daily", "k", "t", target=5, reward=25, welcome_bonus=WELCOME, delta=1
            )
            for _ in range(20)
        )
    )
    assert sum(r.completed_now for r in results) == 1
    wallet = await db.get_wallet(9)
    assert wallet is not None and wallet.balance == WELCOME + 25
    assert await _challenge_rows(db, 9) == 1


async def test_progress_is_per_user_and_per_period_key(db: Database) -> None:
    await db.advance_challenge(
        1, "daily", "k1", "t", target=9, reward=5, welcome_bonus=WELCOME, delta=2
    )
    await db.advance_challenge(
        2, "daily", "k1", "t", target=9, reward=5, welcome_bonus=WELCOME, delta=1
    )
    await db.advance_challenge(
        1, "daily", "k2", "t", target=9, reward=5, welcome_bonus=WELCOME, delta=4
    )
    assert await db.get_challenge_progress(1, "daily", "k1") == {"t": (2, False)}
    assert await db.get_challenge_progress(2, "daily", "k1") == {"t": (1, False)}
    assert await db.get_challenge_progress(1, "daily", "k2") == {"t": (4, False)}


async def test_replace_keeps_retained_and_completed_drops_incomplete_removed(
    db: Database,
) -> None:
    await db.create_challenge_slate("daily", "k", ["keep", "gone_open", "gone_done"])
    kw = {"target": 2, "reward": 5, "welcome_bonus": WELCOME}
    await db.advance_challenge(1, "daily", "k", "keep", delta=1, **kw)
    await db.advance_challenge(1, "daily", "k", "gone_open", delta=1, **kw)
    await db.advance_challenge(1, "daily", "k", "gone_done", delta=2, **kw)

    assert await db.replace_challenge_slate("daily", "k", ["keep", "new"]) == ["keep", "new"]

    progress = await db.get_challenge_progress(1, "daily", "k")
    assert progress == {"keep": (1, False), "gone_done": (2, True)}
    # Re-adding the paid template cannot be earned a second time.
    redo = await db.advance_challenge(1, "daily", "k", "gone_done", delta=1, **kw)
    assert not redo.completed_now
    with pytest.raises(ValueError):
        await db.replace_challenge_slate("daily", "k", [])


async def test_purge_removes_old_rows_only(db: Database) -> None:
    await db.create_challenge_slate("daily", "old", ["t"])
    await db.advance_challenge(
        1, "daily", "old", "t", target=9, reward=5, welcome_bonus=WELCOME, delta=1
    )
    await db.conn.execute("UPDATE challenge_slates SET created_ts = 100;")
    await db.conn.execute("UPDATE challenge_progress SET updated_ts = 100;")
    await db.conn.commit()
    await db.create_challenge_slate("daily", "new", ["t"])
    await db.advance_challenge(
        1, "daily", "new", "t", target=9, reward=5, welcome_bonus=WELCOME, delta=1
    )

    assert await db.purge_challenges(older_than_ts=1000) == 2
    assert await db.get_challenge_slate("daily", "old") is None
    assert await db.get_challenge_slate("daily", "new") == ["t"]
    assert await db.get_challenge_progress(1, "daily", "old") == {}
    assert await db.get_challenge_progress(1, "daily", "new") == {"t": (1, False)}
