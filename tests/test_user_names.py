"""Tests for the throttled, sanitising, non-blocking user-name cache."""

from __future__ import annotations

import asyncio
import sqlite3
from unittest.mock import AsyncMock, MagicMock

import pytest

from yokai.moderation.names import MAX_NAME_CHARS, UserNameCache, clean_name


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def make_cache(**kwargs: float) -> tuple[UserNameCache, MagicMock, Clock]:
    db = MagicMock()
    db.upsert_user_name = AsyncMock()
    clock = Clock()
    return UserNameCache(db, clock=clock, **kwargs), db, clock


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Sami", "Sami"),
        ("  Sami \n\t Syverson  ", "Sami Syverson"),
        ("evil‮gnp.exe", "evilgnp.exe"),  # right-to-left override removed
        ("zero​width", "zerowidth"),
        ("bell\x07char", "bellchar"),
        ("", ""),
        (None, ""),
        (12345, ""),
        ("x" * 500, "x" * MAX_NAME_CHARS),
    ],
)
def test_clean_name(raw: object, expected: str) -> None:
    assert clean_name(raw) == expected


async def test_the_first_sighting_is_written_once() -> None:
    cache, db, _ = make_cache()
    cache.touch(7, "Sami")
    cache.touch(7, "Sami")
    await cache.wait_idle()
    db.upsert_user_name.assert_awaited_once_with(7, "Sami")


async def test_a_changed_name_is_written_again() -> None:
    cache, db, _ = make_cache()
    cache.touch(7, "Sami")
    await cache.wait_idle()
    cache.touch(7, "Sami the Brave")
    await cache.wait_idle()
    assert db.upsert_user_name.await_count == 2


async def test_the_same_name_is_refreshed_after_the_ttl() -> None:
    cache, db, clock = make_cache(ttl_s=100)
    cache.touch(7, "Sami")
    await cache.wait_idle()
    clock.now += 101
    cache.touch(7, "Sami")
    await cache.wait_idle()
    assert db.upsert_user_name.await_count == 2


async def test_an_empty_name_is_never_written() -> None:
    cache, db, _ = make_cache()
    cache.touch(7, "‮​")
    await cache.wait_idle()
    db.upsert_user_name.assert_not_awaited()


async def test_touch_returns_immediately_even_while_the_database_write_is_stuck() -> None:
    """Review finding: a locked database used to hold the interaction for the 5 s busy timeout,
    past Discord's 3 s deadline. The write now runs in the background."""
    cache, db, _ = make_cache()
    release = asyncio.Event()

    async def stuck(*args: object) -> None:
        await release.wait()

    db.upsert_user_name.side_effect = stuck
    cache.touch(7, "Sami")  # a plain call: nothing to await, so nothing can block
    await asyncio.sleep(0)
    assert db.upsert_user_name.await_count == 1  # the write started...
    assert cache.pending == 1  # ...and is still running in the background
    release.set()
    await cache.wait_idle()
    assert cache.pending == 0


async def test_rapid_repeat_interactions_do_not_start_duplicate_writes() -> None:
    """Autocomplete fires on every keystroke; the first one marks the person as in flight."""
    cache, db, _ = make_cache()
    release = asyncio.Event()

    async def stuck(*args: object) -> None:
        await release.wait()

    db.upsert_user_name.side_effect = stuck
    for _ in range(5):
        cache.touch(7, "Sami")
    await asyncio.sleep(0)
    assert db.upsert_user_name.await_count == 1
    release.set()
    await cache.wait_idle()


@pytest.mark.parametrize(
    "error", [sqlite3.OperationalError("locked"), RuntimeError("x"), ValueError("closed")]
)
async def test_a_failed_write_never_raises_and_is_retried_soon(error: Exception) -> None:
    """Review Focus 5: recording a name must never break a Discord interaction."""
    cache, db, clock = make_cache(retry_s=60)
    db.upsert_user_name.side_effect = error
    cache.touch(7, "Sami")
    await cache.wait_idle()  # must not raise
    cache.touch(7, "Sami")  # inside the retry window: no second attempt
    await cache.wait_idle()
    assert db.upsert_user_name.await_count == 1

    clock.now += 61
    db.upsert_user_name.side_effect = None
    cache.touch(7, "Sami")
    await cache.wait_idle()
    assert db.upsert_user_name.await_count == 2


async def test_an_unexpected_error_in_the_background_write_is_logged_not_lost(
    caplog: pytest.LogCaptureFixture,
) -> None:
    cache, db, _ = make_cache()
    db.upsert_user_name.side_effect = TypeError("unexpected")
    cache.touch(7, "Sami")
    with caplog.at_level("ERROR"):
        await cache.wait_idle()
    assert "unexpected" in caplog.text and cache.pending == 0


async def test_close_cancels_writes_still_in_flight() -> None:
    cache, db, _ = make_cache()

    async def never(*args: object) -> None:
        await asyncio.Event().wait()

    db.upsert_user_name.side_effect = never
    cache.touch(7, "Sami")
    await asyncio.sleep(0)
    await cache.close()
    assert cache.pending == 0
