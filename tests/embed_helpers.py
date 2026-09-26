"""Shared helpers for embed tests: emoji counting and total-length measurement."""

from __future__ import annotations

import re

import discord

# Emoji blocks plus the geometric shapes that only become emoji with a variation selector.
EMOJI_RE = re.compile("[\U0001f300-\U0001faff☀-➿⌀-⏿⬀-⯿]️?|[■-◿]️")


def all_embed_text(embed: discord.Embed) -> str:
    data = embed.to_dict()
    parts: list[str] = [data.get("title", ""), data.get("description", "")]
    footer = data.get("footer") or {}
    parts.append(footer.get("text", ""))
    author = data.get("author") or {}
    parts.append(author.get("name", ""))
    for field in data.get("fields", []):
        parts.extend([field.get("name", ""), field.get("value", "")])
    return "\n".join(parts)


def emoji_count(embed: discord.Embed) -> int:
    return len(EMOJI_RE.findall(all_embed_text(embed)))


def total_embed_length(embed: discord.Embed) -> int:
    data = embed.to_dict()
    total = len(data.get("title", "")) + len(data.get("description", ""))
    total += len((data.get("footer") or {}).get("text", ""))
    total += len((data.get("author") or {}).get("name", ""))
    for field in data.get("fields", []):
        total += len(field.get("name", "")) + len(field.get("value", ""))
    return total
