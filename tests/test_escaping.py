"""Track titles interpolated outside the embed builders must be markdown-escaped."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.test_music_cog import make_cog_and_player, make_interaction
from tests.test_player_reliability import attach_channel, make_player, make_track
from yokai.errors import BotCheckError, UnmatchedTrackError
from yokai.music.models import PlayerState, Track
from yokai.textutil import escape_md

NASTY = "[x](http://e) *b* `c`"
ESCAPED = "\\[x\\]\\(http://e\\) \\*b\\* \\`c\\`"


def assert_escaped(text: str) -> None:
    assert "](http://e)" not in text
    assert ESCAPED in text


def test_textutil_escape_md_is_the_embeds_escape_md() -> None:
    from yokai.ui import embeds

    assert embeds.escape_md is escape_md
    assert escape_md(NASTY) == ESCAPED


@pytest.mark.asyncio
async def test_skip_escapes_the_skipped_title() -> None:
    cog, player = make_cog_and_player(PlayerState.PLAYING)
    track = Track(video_id="dQw4w9WgXcQ", title=NASTY)
    player.current_track = track
    player.skip = AsyncMock(return_value=track)
    with patch("yokai.cogs.music.send", new=AsyncMock()) as send:
        await cog.skip.callback(cog, make_interaction())
    assert_escaped(send.await_args.kwargs["embed"].description)


@pytest.mark.asyncio
async def test_remove_escapes_the_removed_title() -> None:
    cog, player = make_cog_and_player(PlayerState.PLAYING)
    player.queue.add(Track(video_id="dQw4w9WgXcQ", title=NASTY))
    with patch("yokai.cogs.music.send", new=AsyncMock()) as send:
        await cog.remove.callback(cog, make_interaction(), position=1)
    assert_escaped(send.await_args.kwargs["embed"].description)


@pytest.mark.asyncio
async def test_recommend_empty_result_escapes_the_seed_title() -> None:
    cog, player = make_cog_and_player(PlayerState.PLAYING)
    player.current_track = Track(video_id="dQw4w9WgXcQ", title=NASTY)
    cog.bot.db.get_recent_played_video_ids = AsyncMock(return_value=[])
    cog.bot.recommender.recommend = AsyncMock(return_value=MagicMock(tracks=[]))
    with patch("yokai.cogs.music.send", new=AsyncMock()) as send:
        await cog.recommend.callback(cog, make_interaction(), seed=None)
    assert_escaped(send.await_args.kwargs["embed"].description)


@pytest.mark.asyncio
async def test_spotify_track_miss_escapes_the_title() -> None:
    cog, _player = make_cog_and_player(PlayerState.IDLE)
    meta = MagicMock()
    meta.title = NASTY
    cog.bot.spotify.resolve_track = AsyncMock(return_value=meta)
    cog.bot.matcher.match_track = AsyncMock(return_value=None)
    with patch("yokai.cogs.music.send", new=AsyncMock()) as send:
        await cog.play.callback(
            cog, make_interaction(), query="https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC"
        )
    assert_escaped(send.await_args.kwargs["embed"].description)


def test_unmatched_track_error_message_escapes_the_title() -> None:
    err = UnmatchedTrackError(NASTY)
    assert_escaped(err.message)
    assert f"**{ESCAPED}**" in err.message


async def test_failure_notice_escapes_the_title() -> None:
    player, _vc, _bot = make_player()
    send = attach_channel(player)
    track = make_track("bad")
    track.title = NASTY
    await player._notify_failure(track, BotCheckError())
    description = send.await_args.kwargs["embed"].description
    assert_escaped(description)
    assert description.startswith(f"Skipping **{ESCAPED}**. ")


async def test_failure_notice_for_an_unmatched_track_names_it_once() -> None:
    player, _vc, _bot = make_player()
    send = attach_channel(player)
    track = make_track("sp:abc")
    track.title = NASTY
    await player._notify_failure(track, UnmatchedTrackError(NASTY))
    description = send.await_args.kwargs["embed"].description
    assert_escaped(description)
    assert description.count(ESCAPED) == 1
    assert description.startswith(UnmatchedTrackError(NASTY).message)
