"""Presence management state machine for Yokai."""

from __future__ import annotations

import enum
import logging
from typing import Protocol

import discord

logger = logging.getLogger(__name__)


class PresenceState(enum.Enum):
    """Voice-driven presence state."""

    IDLE = "idle"  # Disconnected from voice channels
    ACTIVE = "active"  # Connected to a voice channel


class PresenceClient(Protocol):
    """Protocol matching discord.Client's presence modification interface."""

    async def change_presence(
        self,
        *,
        activity: discord.BaseActivity | None = None,
        status: discord.Status | None = None,
    ) -> None: ...


def get_default_activity() -> discord.Activity:
    """Return standard 'Watching for plant' activity."""
    return discord.Activity(
        type=discord.ActivityType.watching,
        name="for plant",
    )


class PresenceManager:
    """Idempotent state machine managing Yokai's presence and activity.

    Presence updates are strictly rate-limited by Discord gateway (~5 per 20 seconds).
    This manager prevents duplicate state calls and guarantees idempotency.
    """

    def __init__(self, client: PresenceClient) -> None:
        self._client = client
        self._state: PresenceState = PresenceState.IDLE
        self._activity = get_default_activity()

    @property
    def current_state(self) -> PresenceState:
        return self._state

    async def set_active(self) -> bool:
        """Switch presence to online while connected to voice.

        Returns True if presence state changed, False if already active.
        """
        if self._state == PresenceState.ACTIVE:
            return False

        logger.info("Presence transition: IDLE -> ACTIVE (Status: online)")
        self._state = PresenceState.ACTIVE
        try:
            await self._client.change_presence(
                activity=self._activity,
                status=discord.Status.online,
            )
            return True
        except Exception as exc:
            logger.error("Failed to update presence to ACTIVE: %s", exc)
            return False

    async def set_idle(self) -> bool:
        """Switch presence to idle when disconnected from voice.

        Returns True if presence state changed, False if already idle.
        """
        if self._state == PresenceState.IDLE:
            return False

        logger.info("Presence transition: ACTIVE -> IDLE (Status: idle)")
        self._state = PresenceState.IDLE
        try:
            await self._client.change_presence(
                activity=self._activity,
                status=discord.Status.idle,
            )
            return True
        except Exception as exc:
            logger.error("Failed to update presence to IDLE: %s", exc)
            return False
