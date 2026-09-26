"""Tests for universal embed system, clamping rules and the shared layout helpers."""

from __future__ import annotations

import discord
import pytest

from tests.embed_helpers import emoji_count, total_embed_length
from yokai.ui.embeds import (
    MAX_DESC_LEN,
    MAX_FIELD_NAME_LEN,
    MAX_FIELD_VAL_LEN,
    MAX_FIELDS_COUNT,
    MAX_FOOTER_LEN,
    MAX_TITLE_LEN,
    MAX_TOTAL_LEN,
    EmbedFactory,
    _grid,
    _link,
    _list_line,
    _render_progress_bar,
    clamp,
    escape_md,
    format_runtime,
)


def _total_embed_length(embed: discord.Embed) -> int:
    return total_embed_length(embed)


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
    """Fixed shape: specific title, plain explanation, 'Try' field, quip in the footer."""
    err_embed = EmbedFactory.error(
        message="Cannot access the requested voice channel.",
        user_hint="Check permissions.",
        title="Missing permission",
    )
    assert err_embed.title == "❌ Missing permission"
    assert err_embed.description == "Cannot access the requested voice channel."
    assert [(f.name, f.value, f.inline) for f in err_embed.fields] == [
        ("Try", "Check permissions.", False)
    ]
    assert "💡" not in (err_embed.description or "")
    assert err_embed.footer.text.startswith("Yokai · System")
    # the persona quip is present but never mixed into the explanation
    assert err_embed.footer.text != "Yokai · System"
    assert "slipped" not in (err_embed.description or "").lower()


def test_error_embed_without_hint_or_quip_is_plain() -> None:
    err_embed = EmbedFactory.error(message="Boom.", include_quip=False)
    assert err_embed.title == "❌ That didn't work"
    assert err_embed.description == "Boom."
    assert len(err_embed.fields) == 0
    assert err_embed.footer.text == "Yokai · System"


def test_titles_are_never_generic() -> None:
    for embed in (
        EmbedFactory.error(message="x"),
        EmbedFactory.success(title="Skipped", description="x"),
        EmbedFactory.warning(title="Queue too short", description="x"),
    ):
        assert (embed.title or "").split(" ", 1)[-1] not in ("Error", "Success", "Warning")


def test_at_most_one_emoji_per_embed() -> None:
    hostile = "X" * 5000
    embeds = [
        EmbedFactory.info(title="Nothing playing", description="Idle."),
        EmbedFactory.success(title="Skipped", description="Skipped **Song**."),
        EmbedFactory.warning(title="Queue too short", description="Need two tracks."),
        EmbedFactory.error(message="Boom.", user_hint="Try again.", title="Broke"),
        EmbedFactory.error(message=hostile, user_hint=hostile, context=hostile),
    ]
    for embed in embeds:
        assert emoji_count(embed) <= 1, embed.to_dict()


def test_escape_md_neutralises_links_and_mentions() -> None:
    nasty = "[free gift](http://evil.example) **bold** `code` <@123> @everyone _it_ ~x~ |s|"
    escaped = escape_md(nasty)
    assert "[free gift](" not in escaped
    assert "\\[free gift\\]" in escaped
    assert "<@123>" not in escaped
    assert "\\*\\*bold\\*\\*" in escaped
    assert "\\`code\\`" in escaped
    assert escape_md("plain title") == "plain title"
    assert escape_md("") == ""


def test_link_escapes_text_and_omits_missing_url() -> None:
    assert _link("Song", "https://youtu.be/x") == "**[Song](https://youtu.be/x)**"
    assert _link("Song", None) == "**Song**"
    linked = _link("a](http://evil) b", "https://youtu.be/x")
    assert "](http://evil)" not in linked


def test_format_runtime() -> None:
    assert format_runtime(45) == "45s"
    assert format_runtime(440) == "7m 20s"
    assert format_runtime(65) == "1m 05s"
    assert format_runtime(4320) == "1h 12m"
    assert format_runtime(None) == "unknown"
    assert format_runtime(-5) == "unknown"


def test_progress_bar_shape_and_no_emoji() -> None:
    for current, total in [(0, 200), (100, 200), (200, 200), (500, 200), (0, 0), (5, -1)]:
        bar = _render_progress_bar(current, total)
        assert len(bar) == 14
        assert bar.count("●") == 1
        assert "🔘" not in bar
    assert _render_progress_bar(0, 200).startswith("●")
    assert _render_progress_bar(200, 200).endswith("●")
    assert _render_progress_bar(100, 200).index("●") == 7


def test_grid_pads_partial_rows_of_inline_fields() -> None:
    five = [(f"F{i}", "v", True) for i in range(5)]
    padded = _grid(five)
    assert len(padded) == 6
    assert padded[-1][0] == "​" and padded[-1][2] is True

    mixed = [("A", "1", True), ("B", "2", True), ("Wide", "3", False)]
    padded = _grid(mixed)
    assert [f[0] for f in padded] == ["A", "B", "​", "Wide"]

    assert _grid([]) == []
    full = [(f"F{i}", "v", True) for i in range(3)]
    assert _grid(full) == full


def test_list_line_format_and_clamping() -> None:
    assert _list_line(1, "Song", "Artist", 221) == "` 1.` **Song** — Artist · `3:41`"
    assert _list_line(12, "Song", None, 0) == "`12.` **Song**"
    long_line = _list_line(3, "T" * 500, "A" * 500, 100)
    assert len(long_line) < 200
    assert "\\[" in _list_line(1, "[x](y)", None, None)


def test_hostile_fields_respect_limits() -> None:
    giant = "X" * 10000
    embed = EmbedFactory.info(
        title=giant,
        description=giant,
        context=giant,
        fields=[(giant, giant, True)] * 40,
    )
    assert len(embed.fields) <= MAX_FIELDS_COUNT
    assert total_embed_length(embed) <= MAX_TOTAL_LEN
    for field in embed.fields:
        assert len(field.name) <= MAX_FIELD_NAME_LEN
        assert len(field.value) <= MAX_FIELD_VAL_LEN


@pytest.mark.parametrize("url", ["https://www.youtube.com/watch?v=abc", None])
def test_base_embed_accepts_url(url: str | None) -> None:
    embed = EmbedFactory._create_base(
        title="T", description="D", color=0x00E5FF, url=url, fields=[("A", "1", True)]
    )
    assert (embed.url or None) == url
