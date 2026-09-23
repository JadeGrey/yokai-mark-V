"""Tests for theme colors, emojis, and persona quip bank."""

from __future__ import annotations

from yokai.theme import QUIP_VARIANTS, QuipBank


def test_quip_variants_minimum_count() -> None:
    """Every quip key must have at least 3 variants per SPEC.md §3."""
    required_keys = [
        "play_start",
        "queued",
        "skip",
        "search_wait",
        "recs_intro",
        "recs_queued",
        "empty_queue",
        "not_in_vc",
        "error_lead",
        "ping_good",
        "idle_leave",
    ]
    for key in required_keys:
        assert key in QUIP_VARIANTS, f"Missing required quip key: {key}"
        variants = QUIP_VARIANTS[key]
        assert len(variants) >= 3, f"Quip key '{key}' has fewer than 3 variants ({len(variants)})"


def test_quip_bank_no_repeat() -> None:
    """QuipBank must never return the same variant twice consecutively."""
    bank = QuipBank()
    for key in QUIP_VARIANTS:
        last_quip: str | None = None
        for _ in range(60):
            current = bank.get(key)
            assert current, f"Empty quip returned for key '{key}'"
            assert current != last_quip, (
                f"Quip '{current}' was repeated consecutively for key '{key}'"
            )
            last_quip = current
