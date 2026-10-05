"""Shared fake for the bot's ModerationStore in cog and tracker tests."""

from __future__ import annotations

from typing import Iterable
from unittest.mock import AsyncMock, MagicMock


def fake_moderation(
    owner_id: int,
    admins: Iterable[int] = (),
    blacklisted: Iterable[int] = (),
) -> MagicMock:
    """A stand-in with the same async surface and rules as `ModerationStore`."""
    admin_ids = set(admins)
    black_ids = set(blacklisted)
    store = MagicMock()
    store.is_admin = AsyncMock(
        side_effect=lambda uid: uid == owner_id or (uid in admin_ids and uid not in black_ids)
    )
    store.is_blacklisted = AsyncMock(side_effect=lambda uid: uid != owner_id and uid in black_ids)
    return store
