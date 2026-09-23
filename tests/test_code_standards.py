"""Lint-style test ensuring no feature module sends raw un-embedded text to Discord."""

from __future__ import annotations

import re
from pathlib import Path

# Match raw channel.send("..."), interaction.response.send_message("..."), followup.send("...")
# where an embed is NOT passed
RAW_SEND_PATTERNS = [
    re.compile(r"\.send_message\(\s*[\"']"),
    re.compile(r"\.send\(\s*[\"']"),
]

EXEMPT_FILES = {
    "embeds.py",
    "views.py",
}


def test_no_raw_message_sends_in_feature_code() -> None:
    """Verify that feature modules route messages through ui/embeds.py."""
    src_dir = Path("src/yokai")
    py_files = list(src_dir.rglob("*.py"))
    assert len(py_files) > 0, "No source files found to check"

    violations: list[str] = []

    for path in py_files:
        if path.name in EXEMPT_FILES:
            continue

        text = path.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), start=1):
            # Ignore comments
            stripped = line.strip()
            if stripped.startswith("#"):
                continue

            for pattern in RAW_SEND_PATTERNS:
                if pattern.search(line):
                    violations.append(f"{path}:{i}: {line.strip()}")

    assert not violations, (
        "Found raw text sends bypassing the universal embed system:\n" + "\n".join(violations)
    )
