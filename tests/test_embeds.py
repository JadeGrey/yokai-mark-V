"""Tests for universal embed system and clamping rules."""

from __future__ import annotations

import discord

from yokai.ui.embeds import (
    MAX_DESC_LEN,
    MAX_FIELD_NAME_LEN,
    MAX_FIELD_VAL_LEN,
    MAX_FIELDS_COUNT,
    MAX_FOOTER_LEN,
    MAX_TITLE_LEN,
    MAX_TOTAL_LEN,
    EmbedFactory,
    clamp,
)


def _total_embed_length(embed: discord.Embed) -> int:
    total = len(embed.title or "") + len(embed.description or "")
    if embed.footer and embed.footer.text:
        total += len(embed.footer.text)
    if embed.author and embed.author.name:
        total += len(embed.author.name)
    for f in embed.fields:
        total += len(f.name or "") + len(f.value or "")
    return total


def test_clamp_basic() -> None:
    assert clamp("", 10) == ""
    assert clamp(None, 10) == ""
    assert clamp("hello", 10) == "hello"
    assert clamp("hello world", 5) == "hell…"
    assert clamp("a" * 1000, 256).endswith("…")
    assert len(clamp("a" * 1000, 256)) == 256


def test_hostile_embed_limits() -> None:
    """Embeds with hostile 10k-character inputs must not raise and must respect limits."""
    giant_string = "X" * 10000

    # 1. Info embed
    info_embed = EmbedFactory.info(
        title=giant_string,
        description=giant_string,
        context=giant_string,
        quip=giant_string,
    )
    assert len(info_embed.title) <= MAX_TITLE_LEN
    assert len(info_embed.description) <= MAX_DESC_LEN
    assert len(info_embed.footer.text) <= MAX_FOOTER_LEN
    assert _total_embed_length(info_embed) <= MAX_TOTAL_LEN

    # 2. Error embed
    err_embed = EmbedFactory.error(
        message=giant_string,
        user_hint=giant_string,
        context=giant_string,
    )
    assert len(err_embed.title) <= MAX_TITLE_LEN
    assert len(err_embed.description) <= MAX_DESC_LEN
    assert _total_embed_length(err_embed) <= MAX_TOTAL_LEN

    # 3. Queued embed
    queued_embed = EmbedFactory.queued(
        title=giant_string,
        position=999,
        artist=giant_string,
        duration_s=3600,
    )
    assert len(queued_embed.title) <= MAX_TITLE_LEN
    assert _total_embed_length(queued_embed) <= MAX_TOTAL_LEN

    # 4. Queue page embed with 50 tracks
    many_tracks = [(i, giant_string, giant_string, 180) for i in range(1, 51)]
    queue_embed = EmbedFactory.queue_page(
        tracks=many_tracks,
        page=1,
        total_pages=5,
        total_tracks=50,
    )
    assert len(queue_embed.fields) <= MAX_FIELDS_COUNT
    for f in queue_embed.fields:
        assert len(f.name) <= MAX_FIELD_NAME_LEN
        assert len(f.value) <= MAX_FIELD_VAL_LEN

    # 5. Recommendations embed
    rec_tracks = [(giant_string, giant_string, 200) for _ in range(10)]
    rec_embed = EmbedFactory.recommendations(
        tracks=rec_tracks,
        seed_title=giant_string,
    )
    assert len(rec_embed.description) <= MAX_DESC_LEN
    assert _total_embed_length(rec_embed) <= MAX_TOTAL_LEN


def test_error_embed_shape() -> None:
    """Error embed has proper title emoji, clear message, and hint."""
    err_embed = EmbedFactory.error(
        message="Cannot access the requested voice channel.",
        user_hint="Check permissions.",
        include_quip=False,
    )
    assert "❌ Error" in (err_embed.title or "")
    assert "Cannot access the requested voice channel." in (err_embed.description or "")
    assert "💡 **What to try:** Check permissions." in (err_embed.description or "")
