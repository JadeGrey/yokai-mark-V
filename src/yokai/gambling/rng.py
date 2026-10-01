"""Randomness source for games: cryptographic in production, seedable in tests."""

from __future__ import annotations

import random


def new_rng() -> random.Random:
    """Return the production RNG. Tests pass their own seeded `random.Random` instead."""
    return random.SystemRandom()
