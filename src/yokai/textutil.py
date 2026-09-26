"""Dependency-free text helpers shared by embeds, errors and the player."""

from __future__ import annotations

import re

_MD_SPECIAL = re.compile(r"([\\*_`~|\[\]<>()])")


def escape_md(text: str) -> str:
    """Escape Discord markdown so user-supplied text (track titles) renders as plain text.

    Backslash-escapes `\\ * _ ` ~ | [ ] ( ) < >`, which neutralises live links, mentions, code
    spans and formatting that a YouTube title could otherwise inject into an embed.
    """
    return _MD_SPECIAL.sub(r"\\\1", text)
