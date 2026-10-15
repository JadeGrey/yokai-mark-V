"""Regenerate content/rpg/items.toml from the tags and the level curve.

Usage: python scripts/gen_rpg_items.py [--content content/rpg] [--check]

With --check it only reports whether the committed file is up to date (exit code 1 if not).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from yokai.rpg.content.common import read_toml
from yokai.rpg.content.parse_core import parse_levels, parse_tags
from yokai.rpg.content.parse_world import parse_skills
from yokai.rpg.itemgen import generate_items_toml


def render(content_dir: Path) -> str:
    levels = parse_levels(read_toml(content_dir, "levels.toml"))
    skills = parse_skills(read_toml(content_dir, "skills.toml"))
    tags = parse_tags(read_toml(content_dir, "tags.toml"), skills)
    return generate_items_toml(tags, levels.growth)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--content", default="content/rpg")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    content_dir = Path(args.content)
    text = render(content_dir)
    target = content_dir / "items.toml"
    if args.check:
        current = target.read_text(encoding="utf-8") if target.exists() else ""
        if current.replace("\r\n", "\n") == text:
            print("items.toml is up to date")
            return 0
        print("items.toml is out of date: run python scripts/gen_rpg_items.py")
        return 1
    target.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {target} ({text.count('[[item]]')} items)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
