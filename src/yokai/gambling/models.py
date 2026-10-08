"""Plain data shared by the multiplayer table code."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class MultiplayerChannel:
    """A channel dedicated to one multiplayer game."""

    channel_id: int
    game: str
    play_message_id: Optional[int]
    table_message_id: Optional[int]
    table_active: bool
