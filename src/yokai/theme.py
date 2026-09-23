"""Visual theme, colors, emojis, and line bank for Yokai."""

from __future__ import annotations

import random
from typing import Final, Mapping, Sequence

# Embed accent colors
COLOR_BRAND: Final[int] = 0x00E5FF  # Echo/Yokai cyan accent
COLOR_SUCCESS: Final[int] = 0x00E676  # Vibrant green
COLOR_WARNING: Final[int] = 0xFFD600  # Amber
COLOR_ERROR: Final[int] = 0xFF1744  # Sharp red
COLOR_IDLE: Final[int] = 0x78909C  # Muted slate/grey


# Fixed emoji set (at most one emoji per embed)
class Emoji:
    PLAY: Final[str] = "▶️"
    PAUSE: Final[str] = "⏸️"
    QUEUE: Final[str] = "📑"
    SEARCH: Final[str] = "🔎"
    CHECK: Final[str] = "✅"
    WARN: Final[str] = "⚠️"
    CROSS: Final[str] = "❌"
    DRONE: Final[str] = "🛸"
    RADIO: Final[str] = "📻"
    PING: Final[str] = "📡"
    STOP: Final[str] = "⏹️"
    SKIP: Final[str] = "⏭️"


# Persona Line Bank (at least 3 variants per key)
QUIP_VARIANTS: Final[Mapping[str, Sequence[str]]] = {
    "play_start": (
        "Locked in.",
        "On it.",
        "Here we go.",
        "Rolling audio.",
    ),
    "queued": (
        "Queued. Obviously.",
        "Added to the lineup.",
        "Noted.",
        "Slotted into the queue.",
    ),
    "skip": (
        "Skipped. Bold call.",
        "Gone. Next.",
        "Moving on.",
    ),
    "search_wait": (
        "Scanning…",
        "Looking around…",
        "Scouring the feeds…",
    ),
    "recs_intro": (
        "Recon complete. Try these.",
        "Scanned the area. These are worth your time.",
        "Target identified. Give these a spin.",
    ),
    "recs_queued": (
        "All of them. Good instinct.",
        "Sent to the queue.",
        "Stacked up. Ready.",
    ),
    "empty_queue": (
        "Nothing in the queue. Feed me something.",
        "Queue is clear. Pick a track.",
        "Dead air. Drop a link.",
    ),
    "not_in_vc": (
        "Join a voice channel first, then we'll talk.",
        "Need you on the wire. Hop into voice.",
        "Can't transmit to thin air. Join a VC.",
    ),
    "error_lead": (
        "That one slipped past me.",
        "Didn't go to plan.",
        "Glitch in the feed.",
    ),
    "ping_good": (
        "Fast. As expected.",
        "Signal clear. Zero lag.",
        "On the wire without hesitation.",
    ),
    "idle_leave": (
        "Nothing to watch. Heading out.",
        "Standing down. Catch you later.",
        "Channel is quiet. Disengaging.",
    ),
}


class QuipBank:
    """Manages randomized quip selection ensuring no immediate repetition."""

    def __init__(self, variants: Mapping[str, Sequence[str]] = QUIP_VARIANTS) -> None:
        self._variants = {k: list(v) for k, v in variants.items()}
        self._last_selected: dict[str, str] = {}

    def get(self, key: str) -> str:
        """Return a quip for the key, never repeating the immediately preceding one."""
        options = self._variants.get(key)
        if not options:
            return ""

        if len(options) == 1:
            return options[0]

        last = self._last_selected.get(key)
        pool = [opt for opt in options if opt != last]
        selected = random.choice(pool)
        self._last_selected[key] = selected
        return selected


# Global shared instance
quip_bank = QuipBank()
