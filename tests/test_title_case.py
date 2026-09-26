"""Every literal embed title uses sentence case, so the bot has one title convention."""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "yokai"
TITLE_LITERAL = re.compile(r'\btitle\s*=\s*"([^"]+)"')
PROPER_NOUNS = {"YouTube", "Spotify", "Yokai", "DAVE"}


def _title_case_offenders() -> list[str]:
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        for title in TITLE_LITERAL.findall(path.read_text(encoding="utf-8")):
            later_words = title.split()[1:]
            if any(w[:1].isupper() and w not in PROPER_NOUNS for w in later_words):
                offenders.append(f"{path.relative_to(SRC)}: {title}")
    return offenders


def test_literal_titles_are_sentence_case() -> None:
    assert _title_case_offenders() == []


def test_the_scan_sees_the_titles() -> None:
    """Guard the guard: the pattern must actually find the literal titles."""
    found = [
        t for p in SRC.rglob("*.py") for t in TITLE_LITERAL.findall(p.read_text(encoding="utf-8"))
    ]
    assert "Unsupported link" in found
    assert len(found) > 20
