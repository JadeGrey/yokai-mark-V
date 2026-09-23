"""Tests for presence management and idempotency."""

from __future__ import annotations

import discord
import pytest

from yokai.presence import PresenceManager, PresenceState


class FakePresenceClient:
    """Mock client tracking change_presence calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[discord.BaseActivity | None, discord.Status | None]] = []

    async def change_presence(
        self,
        *,
        activity: discord.BaseActivity | None = None,
        status: discord.Status | None = None,
    ) -> None:
        self.calls.append((activity, status))


@pytest.mark.asyncio
async def test_presence_manager_idempotency() -> None:
    client = FakePresenceClient()
    manager = PresenceManager(client)

    # Initial state is IDLE
    assert manager.current_state == PresenceState.IDLE
    assert len(client.calls) == 0

    # Redundant set_idle call
    assert await manager.set_idle() is False
    assert len(client.calls) == 0
    assert manager.current_state == PresenceState.IDLE

    # Transition to ACTIVE
    assert await manager.set_active() is True
    assert len(client.calls) == 1
    assert manager.current_state == PresenceState.ACTIVE
    assert client.calls[0][1] == discord.Status.online

    # Redundant set_active call
    assert await manager.set_active() is False
    assert len(client.calls) == 1

    # Transition back to IDLE
    assert await manager.set_idle() is True
    assert len(client.calls) == 2
    assert manager.current_state == PresenceState.IDLE
    assert client.calls[1][1] == discord.Status.idle

    # Redundant set_idle call
    assert await manager.set_idle() is False
    assert len(client.calls) == 2
