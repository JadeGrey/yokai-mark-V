"""Tests for the ModerationStore cache, owner rules and failure fallback."""

from __future__ import annotations

import asyncio
import sqlite3
from unittest.mock import AsyncMock, MagicMock

from yokai.moderation.store import ModerationStore

OWNER = 1


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def make_store(
    black: frozenset[int] = frozenset(),
    admins: frozenset[int] = frozenset(),
    ttl_s: float = 5.0,
    refresh_timeout_s: float = 1.0,
) -> tuple[ModerationStore, MagicMock, FakeClock]:
    db = MagicMock()
    db.get_moderation_lists = AsyncMock(return_value=(black, admins))
    clock = FakeClock()
    store = ModerationStore(
        db, owner_id=OWNER, ttl_s=ttl_s, clock=clock, refresh_timeout_s=refresh_timeout_s
    )
    return store, db, clock


async def test_blacklisted_user_is_reported() -> None:
    store, _, _ = make_store(black=frozenset({5}))
    assert await store.is_blacklisted(5) is True
    assert await store.is_blacklisted(6) is False


async def test_admin_user_is_reported() -> None:
    store, _, _ = make_store(admins=frozenset({7}))
    assert await store.is_admin(7) is True
    assert await store.is_admin(8) is False


async def test_owner_is_never_blacklisted_and_always_admin() -> None:
    """Review Focus 2: the owner in the blacklist table is still unblockable and admin."""
    store, db, _ = make_store(black=frozenset({OWNER}))
    assert await store.is_blacklisted(OWNER) is False
    assert await store.is_admin(OWNER) is True
    db.get_moderation_lists.assert_not_awaited()  # owner rules need no DB read


async def test_a_user_in_both_lists_is_not_an_admin() -> None:
    """Review Focus 5: blacklist wins over adminlist."""
    store, _, _ = make_store(black=frozenset({9}), admins=frozenset({9}))
    assert await store.is_blacklisted(9) is True
    assert await store.is_admin(9) is False


async def test_reads_are_cached_within_the_ttl_and_refreshed_after() -> None:
    store, db, clock = make_store(black=frozenset({5}))
    await store.is_blacklisted(5)
    await store.is_blacklisted(5)
    assert db.get_moderation_lists.await_count == 1

    clock.now += 5.1
    db.get_moderation_lists.return_value = (frozenset(), frozenset())
    assert await store.is_blacklisted(5) is False
    assert db.get_moderation_lists.await_count == 2


async def test_concurrent_checks_share_one_refresh() -> None:
    store, db, _ = make_store(black=frozenset({5}))
    results = await asyncio.gather(*(store.is_blacklisted(5) for _ in range(20)))
    assert all(results)
    assert db.get_moderation_lists.await_count == 1


async def test_failed_refresh_keeps_the_last_good_copy() -> None:
    """Review Focus 4: a DB hiccup must not unblock users or crash commands."""
    store, db, clock = make_store(black=frozenset({5}))
    assert await store.is_blacklisted(5) is True

    clock.now += 6
    db.get_moderation_lists.side_effect = sqlite3.OperationalError("database is locked")
    assert await store.is_blacklisted(5) is True


async def test_failed_first_load_fails_open_and_does_not_hammer_the_db() -> None:
    """Review Focus 4: no copy yet means nobody is blocked; retry only after one TTL."""
    store, db, clock = make_store()
    db.get_moderation_lists.side_effect = RuntimeError("Database connection is not open.")

    assert await store.is_blacklisted(5) is False
    assert await store.is_admin(7) is False
    assert db.get_moderation_lists.await_count == 1  # second call reused the failed attempt

    clock.now += 5.1
    db.get_moderation_lists.side_effect = None
    db.get_moderation_lists.return_value = (frozenset({5}), frozenset())
    assert await store.is_blacklisted(5) is True


async def test_a_closed_connection_valueerror_is_treated_like_a_failed_read() -> None:
    """aiosqlite raises ValueError (not sqlite3.Error) once its connection is closed."""
    store, db, _ = make_store(black=frozenset({5}))
    assert await store.is_blacklisted(5) is True

    store._loaded_at = None  # force a refresh
    db.get_moderation_lists.side_effect = ValueError("no active connection")
    assert await store.is_blacklisted(5) is True  # last good copy, no exception


async def test_a_hung_refresh_times_out_and_keeps_the_last_copy() -> None:
    """A slow DB must not hold an interaction past Discord's 3 s acknowledgement window."""
    store, db, clock = make_store(black=frozenset({5}), refresh_timeout_s=0.05)
    assert await store.is_blacklisted(5) is True

    async def hang() -> tuple[frozenset[int], frozenset[int]]:
        await asyncio.sleep(30)
        return frozenset(), frozenset()

    clock.now += 6
    db.get_moderation_lists.side_effect = hang
    assert await asyncio.wait_for(store.is_blacklisted(5), timeout=2) is True
