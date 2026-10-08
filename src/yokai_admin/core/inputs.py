"""Parsing and cleaning of typed values, shared by every module that takes input."""

from __future__ import annotations

import re

from yokai_admin.errors import PanelError

MIN_REASON = 3
MAX_REASON = 200
# Control characters, zero-width characters and the bidirectional overrides/isolates that can make
# text display in a different order than it was typed.
_UNSAFE = re.compile(r"[\x00-\x1f\x7f-\x9f​-‏ -‮⁦-⁩]")


class InputRefused(PanelError):
    """A request the panel refuses on purpose; the message is shown to the owner."""


def parse_user_id(raw: str) -> int:
    """Turn typed text into a Discord user ID, or refuse with a message that says what to type."""
    text = raw.strip()
    # int() would also accept digits from other scripts, so check for plain ASCII digits first.
    if not (text.isascii() and text.isdigit()):
        raise InputRefused(
            "Enter the Discord user ID as digits only, for example 123456789012345678."
        )
    # A Discord ID has at most 19 digits. Checking the length first also keeps int() from raising
    # on absurdly long input (it refuses more than 4300 digits).
    if len(text) > 19 or not 0 < (value := int(text)) < 2**63:
        raise InputRefused("That is not a valid Discord user ID.")
    return value


def clean_reason(raw: str, *, required: bool) -> str:
    """Normalise a reason or note; refuse when it is too short (if required) or too long."""
    text = " ".join(_UNSAFE.sub(" ", raw).split())
    if required and len(text) < MIN_REASON:
        raise InputRefused(f"Give a short reason, at least {MIN_REASON} characters.")
    if len(text) > MAX_REASON:
        raise InputRefused(f"Keep the reason under {MAX_REASON} characters.")
    return text


def parse_page(raw: str) -> int:
    """A positive page number, or 1 for anything else (empty, text, zero, negative, absurd)."""
    text = raw.strip()
    if not (text.isascii() and text.isdigit()):
        return 1
    if len(text) > 6:  # also keeps int() away from absurdly long input
        return 1
    value = int(text)
    return value if 0 < value <= 100_000 else 1
