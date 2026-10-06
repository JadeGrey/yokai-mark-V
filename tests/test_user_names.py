"""Tests for the throttled, sanitising user-name cache."""

from __future__ import annotations

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
    await cache.touch(7, "Sami")
    await cache.touch(7, "Sami")
    db.upsert_user_name.assert_awaited_once_with(7, "Sami")


async def test_a_changed_name_is_written_again() -> None:
    cache, db, _ = make_cache()
    await cache.touch(7, "Sami")
    await cache.touch(7, "Sami the Brave")
    assert db.upsert_user_name.await_count == 2


async def test_the_same_name_is_refreshed_after_the_ttl() -> None:
    cache, db, clock = make_cache(ttl_s=100)
    await cache.touch(7, "Sami")
    clock.now += 101
    await cache.touch(7, "Sami")
    assert db.upsert_user_name.await_count == 2


async def test_an_empty_name_is_never_written() -> None:
    cache, db, _ = make_cache()
    await cache.touch(7, "‮​")
    db.upsert_user_name.assert_not_awaited()


@pytest.mark.parametrize(
    "error", [sqlite3.OperationalError("locked"), RuntimeError("x"), ValueError("closed")]
)
async def test_a_failed_write_never_raises_and_is_retried_soon(error: Exception) -> None:
    """Review Focus 5: recording a name must never break a Discord interaction."""
    cache, db, clock = make_cache(retry_s=60)
    db.upsert_user_name.side_effect = error
    await cache.touch(7, "Sami")  # must not raise
    await cache.touch(7, "Sami")  # inside the retry window: no second attempt
    assert db.upsert_user_name.await_count == 1

    clock.now += 61
    db.upsert_user_name.side_effect = None
    await cache.touch(7, "Sami")
    assert db.upsert_user_name.await_count == 2
