"""Tests for universal embed system, clamping rules and the shared layout helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from tests.embed_helpers import emoji_count, total_embed_length
from yokai.theme import COLOR_ERROR, COLOR_SUCCESS, COLOR_WARNING
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
    send,
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
    assert len(queue_embed.description) <= MAX_DESC_LEN
    assert _total_embed_length(queue_embed) <= MAX_TOTAL_LEN

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


def _fields(embed: discord.Embed) -> dict[str, str]:
    return {f.name: f.value for f in embed.fields if f.name != "​"}


def test_now_playing_layout() -> None:
    embed = EmbedFactory.now_playing(
        title="Never Gonna Give You Up",
        artist="Rick Astley",
        duration_s=213,
        position_s=65,
        requester_mention="<@42>",
        thumbnail_url="https://img.example/t.jpg",
        url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        source="search",
    )
    assert embed.title == "▶️ Never Gonna Give You Up"
    assert embed.url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert "**Rick Astley**" in embed.description
    assert "●" in embed.description and "🔘" not in embed.description
    assert "`1:05 / 3:33`" in embed.description
    assert _fields(embed) == {
        "Duration": "`3:33`",
        "Requested by": "<@42>",
        "Source": "Search",
    }
    assert len(embed.fields) == 3  # already a full row, so no padding
    assert embed.thumbnail.url == "https://img.example/t.jpg"
    assert emoji_count(embed) == 1


def test_now_playing_without_optional_data() -> None:
    embed = EmbedFactory.now_playing(
        title="Song", artist=None, duration_s=0, position_s=0, requester_mention="<@1>"
    )
    assert embed.url is None
    assert "`0:00 / --:--`" in embed.description or "0:00 / 0:00" in embed.description
    assert "Source" not in _fields(embed)
    assert emoji_count(embed) == 1


def test_queued_layout_with_eta() -> None:
    embed = EmbedFactory.queued(
        title="Song A",
        position=3,
        artist="Artist",
        duration_s=200,
        url="https://www.youtube.com/watch?v=abc",
        eta_s=440,
        requester_mention="<@7>",
        source="import",
    )
    assert embed.title == "📑 Queued"
    assert "**[Song A](https://www.youtube.com/watch?v=abc)**" in embed.description
    assert "by Artist" in embed.description
    assert _fields(embed) == {
        "Position": "`#3`",
        "Plays in": "`~7m 20s`",
        "Duration": "`3:20`",
        "Requested by": "<@7>",
        "Source": "Spotify import",
    }
    assert emoji_count(embed) == 1


def test_queued_old_call_style_still_works() -> None:
    embed = EmbedFactory.queued(title="Song", position=1, artist="A", duration_s=100)
    assert _fields(embed)["Position"] == "`#1`"
    assert "Plays in" not in _fields(embed)


def test_track_text_is_escaped_in_lists_and_links() -> None:
    nasty = "[free gift](http://evil.example) <@123> **x**"
    queued = EmbedFactory.queued(title=nasty, position=1, artist=nasty, url=None)
    assert "[free gift](" not in queued.description
    now = EmbedFactory.now_playing(
        title=nasty, artist=nasty, duration_s=10, position_s=1, requester_mention="<@1>"
    )
    assert "[free gift](" not in now.description
    page = EmbedFactory.queue_page(
        tracks=[(1, nasty, nasty, 10)], page=1, total_pages=1, total_tracks=1
    )
    assert "[free gift](" not in page.description
    recs = EmbedFactory.recommendations(tracks=[(nasty, nasty, 10)], seed_title=nasty)
    assert "[free gift](" not in recs.description
    assert "Seeded from **\\[free gift\\]\\(" in recs.description
    summary = EmbedFactory.import_summary(
        kind="Playlist", name=nasty, loaded=1, total=None, queued=0, now_playing=nasty
    )
    assert "[free gift](" not in summary.description
    assert "\\[free gift\\]" in summary.description
    assert "[free gift](" not in _fields(summary)["Now playing"]
    assert "\\[free gift\\]" in _fields(summary)["Now playing"]


def test_queue_page_compact_list_with_header() -> None:
    tracks = [(i, f"Song {i}", f"Artist {i}", 180 + i) for i in range(1, 4)]
    embed = EmbedFactory.queue_page(
        tracks=tracks,
        page=1,
        total_pages=2,
        total_tracks=13,
        loop_mode="queue",
        now_playing=("Current Song", "https://www.youtube.com/watch?v=cur", 200),
        total_runtime_s=3900,
    )
    assert embed.title == "📑 Up next"
    assert len(embed.fields) == 0
    lines = embed.description.splitlines()
    assert "**Now playing**" in lines[0] and "[Current Song](" in lines[0]
    assert "**13** up next" in lines[1]
    assert "1h 05m total" in lines[1]
    assert "loop queue" in lines[1]
    assert "` 1.` **Song 1** — Artist 1 · `3:01`" in embed.description
    assert embed.footer.text == "Yokai · Page 1/2"
    assert emoji_count(embed) == 1


def test_queue_page_empty_and_out_of_range_states() -> None:
    empty = EmbedFactory.queue_page(tracks=[], page=1, total_pages=1, total_tracks=0)
    assert "The queue is empty." in empty.description
    assert "**0** up next" in empty.description
    assert emoji_count(empty) <= 1

    beyond = EmbedFactory.queue_page(tracks=[], page=9, total_pages=0, total_tracks=0)
    assert beyond.footer.text == "Yokai · Page 9/1"


def test_queue_page_truncates_long_lists_within_limits() -> None:
    giant = "G" * 10000
    tracks = [(i, giant, giant, 180) for i in range(1, 51)]
    embed = EmbedFactory.queue_page(
        tracks=tracks,
        page=1,
        total_pages=5,
        total_tracks=50,
        now_playing=(giant, "https://www.youtube.com/watch?v=x", 10),
    )
    assert len(embed.description) <= MAX_DESC_LEN
    assert total_embed_length(embed) <= MAX_TOTAL_LEN
    assert "more on this page" in embed.description
    assert len(embed.fields) == 0


def test_recommendations_use_the_shared_list_style() -> None:
    embed = EmbedFactory.recommendations(
        tracks=[("Song A", "Artist A", 221), ("Song B", "Artist B", 0)], seed_title="Seed Song"
    )
    assert embed.title == "📻 Radio recommendations"
    assert "Seeded from **Seed Song**" in embed.description
    assert "` 1.` **Song A** — Artist A · `3:41`" in embed.description
    assert "` 2.` **Song B** — Artist B" in embed.description
    assert emoji_count(embed) == 1


def test_import_summary_layout_and_partial_note() -> None:
    embed = EmbedFactory.import_summary(
        kind="Spotify album",
        name="Discovery",
        loaded=14,
        total=14,
        queued=13,
        now_playing="One More Time",
    )
    assert embed.title == "📑 Spotify album loaded"
    assert embed.description == "**Discovery**"
    assert _fields(embed) == {"Loaded": "`14`", "Queued": "`13`", "Now playing": "One More Time"}

    partial = EmbedFactory.import_summary(
        kind="Spotify playlist", name="Big", loaded=50, total=120, queued=50, partial=True
    )
    assert _fields(partial)["Loaded"] == "`50 of 120`"
    assert "Now playing" not in _fields(partial)

    unnamed = EmbedFactory.import_summary(
        kind="Playlist", name=None, loaded=3, total=None, queued=2, now_playing="Song 0"
    )
    assert "Loaded from the playlist link." in unnamed.description
    assert emoji_count(embed) == 1
    assert emoji_count(unnamed) == 1


def test_music_builders_hostile_limits_and_emoji() -> None:
    giant = "X" * 10000
    embeds = [
        EmbedFactory.now_playing(
            title=giant,
            artist=giant,
            duration_s=10**9,
            position_s=-5,
            requester_mention=giant,
            source=giant,
            url="https://www.youtube.com/watch?v=x",
        ),
        EmbedFactory.queued(
            title=giant,
            position=999,
            artist=giant,
            duration_s=None,
            eta_s=10**9,
            requester_mention=giant,
            source="link",
        ),
        EmbedFactory.recommendations(tracks=[(giant, giant, 200)] * 10, seed_title=giant),
        EmbedFactory.import_summary(
            kind=giant, name=giant, loaded=1, total=2, queued=1, now_playing=giant, partial=True
        ),
    ]
    for embed in embeds:
        assert len(embed.title) <= MAX_TITLE_LEN
        assert len(embed.description) <= MAX_DESC_LEN
        assert total_embed_length(embed) <= MAX_TOTAL_LEN
        assert emoji_count(embed) <= 1


def test_ping_fields_are_grid_aligned_and_status_colored() -> None:
    good = EmbedFactory.ping(
        gateway_ms=40.0, rest_ms=60.0, db_ms=1.0, voice_ms=30.0, resolver_ms=20.0
    )
    assert "System Latencies" in good.title
    assert len(good.fields) % 3 == 0  # 5 real fields + 1 spacer
    assert good.fields[-1].name == "​"
    assert {"Gateway (WS)", "REST API", "Database", "Voice", "Resolver"} <= {
        f.name for f in good.fields
    }
    slow = EmbedFactory.ping(gateway_ms=500.0, rest_ms=None, db_ms=None)
    assert slow.color.value != good.color.value
    assert emoji_count(good) == 1 and emoji_count(slow) == 1


def test_ping_status_ignores_a_slow_resolver() -> None:
    # A first-time YouTube resolve takes about 2 s by nature; it is shown but never counted.
    embed = EmbedFactory.ping(
        gateway_ms=40.0, rest_ms=60.0, db_ms=1.0, voice_ms=30.0, resolver_ms=3000.0
    )
    assert embed.color.value == COLOR_SUCCESS
    assert "Fast. As expected." in embed.description
    resolver = next(f for f in embed.fields if f.name == "Resolver")
    assert "3000.0 ms" in resolver.value


def test_ping_status_tiers_follow_gateway_rest_voice_and_database() -> None:
    assert EmbedFactory.ping(gateway_ms=220.0, rest_ms=50.0, db_ms=1.0).color.value == COLOR_WARNING
    assert EmbedFactory.ping(gateway_ms=500.0, rest_ms=50.0, db_ms=1.0).color.value == COLOR_ERROR
    assert EmbedFactory.ping(gateway_ms=10.0, rest_ms=450.0, db_ms=1.0).color.value == COLOR_ERROR
    assert (
        EmbedFactory.ping(gateway_ms=10.0, rest_ms=10.0, db_ms=1.0, voice_ms=250.0).color.value
        == COLOR_WARNING
    )
    assert (
        EmbedFactory.ping(gateway_ms=10.0, rest_ms=10.0, db_ms=200.0).color.value == COLOR_WARNING
    )


def test_ping_with_only_resolver_data_still_works() -> None:
    embed = EmbedFactory.ping(gateway_ms=None, rest_ms=None, db_ms=None, resolver_ms=1800.0)
    assert embed.color.value == COLOR_SUCCESS
    assert "1800.0 ms" in next(f for f in embed.fields if f.name == "Resolver").value


def test_about_stack_grid_is_aligned_and_notice_is_wide() -> None:
    embed = EmbedFactory.about(
        version="0.1.0",
        uptime_str="1h 2m",
        host_os="Windows 11",
        stack_info={"Python": "3.14", "discord.py": "2.7.1", "yt-dlp": "x", "ytmusicapi": "y"},
        owner_id=42,
    )
    inline = [f for f in embed.fields if f.inline]
    assert len(inline) % 3 == 0
    assert embed.fields[-1].name == "Notice" and embed.fields[-1].inline is False
    assert emoji_count(embed) == 1


def test_diag_status_changes_color_and_emoji() -> None:
    ok = EmbedFactory.diag(diagnostics={"Python": "3.14"}, recent_errors=[])
    warn = EmbedFactory.diag(diagnostics={"Python": "3.14"}, recent_errors=["boom"], status="warn")
    assert "Diagnostic Telemetry" in ok.title
    assert ok.title.startswith("🛸") and warn.title.startswith("⚠️")
    assert ok.color.value != warn.color.value
    assert emoji_count(ok) == 1 and emoji_count(warn) == 1
    inline = [f for f in ok.fields if f.inline]
    assert len(inline) % 3 == 0


def test_usage_embed_still_has_period_and_two_fields() -> None:
    embed = EmbedFactory.usage(
        top_users=[(1, 5, 2)], top_commands=[("play", 9)], period="Last 7 Days"
    )
    assert "Last 7 Days" in embed.description
    assert [f.name for f in embed.fields] == ["Top Operators", "Top Commands"]
    assert emoji_count(embed) == 1


def test_import_summary_note_is_appended_on_its_own_line() -> None:
    embed = EmbedFactory.import_summary(
        kind="Playlist",
        name="Mix",
        loaded=2,
        total=None,
        queued=2,
        note="Queue limit reached: only 2 of 4 tracks fit.",
    )
    assert embed.description == "**Mix**\nQueue limit reached: only 2 of 4 tracks fit."
    unnamed = EmbedFactory.import_summary(
        kind="Playlist", name=None, loaded=2, total=None, queued=2, note="Note."
    )
    assert unnamed.description.splitlines() == ["Loaded from the playlist link.", "Note."]
    plain = EmbedFactory.import_summary(kind="Playlist", name="Mix", loaded=2, total=None, queued=2)
    assert plain.description == "**Mix**"


def _send_interaction(avatar: object) -> MagicMock:
    interaction = MagicMock()
    interaction.response.is_done.return_value = False
    interaction.response.send_message = AsyncMock()
    interaction.original_response = AsyncMock()
    if avatar is None:
        interaction.client.user = None
    else:
        interaction.client.user.display_avatar.url = avatar
    return interaction


@pytest.mark.asyncio
async def test_send_adds_the_bot_icon_to_a_footer_without_one() -> None:
    interaction = _send_interaction("https://cdn.example/bot.png")
    embed = EmbedFactory.success(title="Stopped", description="Done.", context="Controls")
    await send(interaction, embed=embed)
    sent = interaction.response.send_message.await_args.kwargs["embed"]
    assert sent.footer.text == "Yokai · Controls"
    assert sent.footer.icon_url == "https://cdn.example/bot.png"


@pytest.mark.asyncio
async def test_send_keeps_an_existing_footer_icon() -> None:
    interaction = _send_interaction("https://cdn.example/bot.png")
    embed = EmbedFactory.info(
        title="Info", description="x", bot_avatar_url="https://cdn.example/own.png"
    )
    await send(interaction, embed=embed)
    sent = interaction.response.send_message.await_args.kwargs["embed"]
    assert sent.footer.icon_url == "https://cdn.example/own.png"


@pytest.mark.asyncio
async def test_send_without_a_bot_user_leaves_the_footer_alone() -> None:
    for interaction in (_send_interaction(None), _send_interaction(MagicMock())):
        embed = EmbedFactory.info(title="Info", description="x", context="Status")
        await send(interaction, embed=embed)
        sent = interaction.response.send_message.await_args.kwargs["embed"]
        assert sent.footer.text == "Yokai · Status"
        assert sent.footer.icon_url is None
