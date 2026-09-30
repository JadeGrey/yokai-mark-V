"""Tests for the music-command Renown reward rate limiter (Review Focus: boundary behavior)."""

from __future__ import annotations

from yokai.economy.ratelimit import CommandRewardLimiter


def test_allows_exactly_the_limit_then_blocks_the_next_call() -> None:
    limiter = CommandRewardLimiter(limit=5, window_s=60.0)
    results = [limiter.try_consume(user_id=1, now=100.0) for _ in range(6)]
    assert results == [True, True, True, True, True, False]


def test_tracks_users_independently() -> None:
    limiter = CommandRewardLimiter(limit=1, window_s=60.0)
    assert limiter.try_consume(user_id=1, now=0.0) is True
    assert limiter.try_consume(user_id=2, now=0.0) is True
    assert limiter.try_consume(user_id=1, now=0.0) is False


def test_window_expiry_allows_new_calls() -> None:
    limiter = CommandRewardLimiter(limit=2, window_s=60.0)
    assert limiter.try_consume(user_id=1, now=0.0) is True
    assert limiter.try_consume(user_id=1, now=1.0) is True
    assert limiter.try_consume(user_id=1, now=2.0) is False
    assert limiter.try_consume(user_id=1, now=61.0) is True
