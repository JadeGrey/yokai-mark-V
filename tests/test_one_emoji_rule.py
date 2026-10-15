"""Guards the persona rule: at most one emoji per embed, including hand-written text."""

from __future__ import annotations

import re
from pathlib import Path

from tests.embed_helpers import EMOJI_RE

SRC = Path("src/yokai")
CHECKED = [
    "cogs/meta.py",
    "cogs/music.py",
    "cogs/economy.py",
    "cogs/gambling.py",
    "cogs/admin.py",
    "cogs/tables.py",
    "cogs/lottery.py",
    "cogs/rpg.py",
    "ui/lottery_views.py",
    "ui/table_common.py",
    "ui/table_views.py",
    "ui/duel_views.py",
    "ui/views.py",
    "ui/game_views.py",
    "music/player.py",
    "bot.py",
]


def test_feature_modules_contain_no_emoji_in_embed_text() -> None:
    """Emoji live in theme.Emoji only; feature code must not hand-write them into strings.

    Button labels/emoji arguments (`emoji="..."`) are components, not embed text, and are exempt.
    """
    offenders: list[str] = []
    for relative in CHECKED:
        for number, line in enumerate((SRC / relative).read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or "emoji=" in stripped:
                continue
            if EMOJI_RE.search(line):
                offenders.append(f"{relative}:{number}: {stripped}")
    assert not offenders, "Emoji in feature code:\n" + "\n".join(offenders)


def test_pattern_sanity() -> None:
    assert re.search(EMOJI_RE, "⚠️ text")
    assert not re.search(EMOJI_RE, "plain — text · ● ━")
