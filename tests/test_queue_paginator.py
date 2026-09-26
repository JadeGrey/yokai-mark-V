"""Tests for the queue paginator's embed (now playing header, runtime, pending imports)."""

from __future__ import annotations

from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from tests.test_music_cog import make_cog_and_player, make_interaction
from yokai.music.models import LoopMode, MusicQueue, PlayerState, Track
from yokai.ui.views import QueuePaginator


def make_queue(count: int) -> MusicQueue:
    queue = MusicQueue(max_size=100)
    for i in range(count):
        queue.add(Track(video_id=f"vid{i:08d}", title=f"Song {i}", artist="A", duration_s=120))
    return queue


@pytest.mark.asyncio
async def test_paginator_embed_shows_now_playing_runtime_and_loop() -> None:
    queue = make_queue(12)
    queue.loop_mode = LoopMode.QUEUE
    current = Track(video_id="cur00000001", title="Current", duration_s=200)
    view = QueuePaginator(queue=queue, author_id=1, current_track_getter=lambda: current)
    embed = view.build_embed()
    assert "[Current](https://www.youtube.com/watch?v=cur00000001)" in embed.description
    assert "**12** up next" in embed.description
    assert "24m 00s total" in embed.description
    assert "loop queue" in embed.description
    assert embed.footer.text == "Yokai · Page 1/2"


@pytest.mark.asyncio
async def test_paginator_pending_import_current_track_has_no_link() -> None:
    queue = make_queue(1)
    pending = Track(video_id="sp:abc", title="Imported", is_pending_match=True)
    embed = QueuePaginator(
        queue=queue, author_id=1, current_track_getter=lambda: pending
    ).build_embed()
    assert "**Imported**" in embed.description
    assert "youtube.com" not in embed.description.splitlines()[0]


@pytest.mark.asyncio
async def test_paginator_without_current_track_or_tracks() -> None:
    embed = QueuePaginator(queue=make_queue(0), author_id=1).build_embed()
    assert "The queue is empty." in embed.description
    assert "Now playing" not in embed.description


@pytest.mark.asyncio
async def test_paginator_now_playing_follows_the_player() -> None:
    queue = make_queue(12)
    playing: list[Optional[Track]] = [Track(video_id="old00000001", title="Old Song")]
    view = QueuePaginator(queue=queue, author_id=1, current_track_getter=lambda: playing[0])
    assert "Old Song" in view.build_embed().description

    playing[0] = Track(video_id="new00000001", title="New Song")
    embed = view.build_embed()
    assert "New Song" in embed.description
    assert "Old Song" not in embed.description

    playing[0] = None
    assert "Now playing" not in view.build_embed().description


@pytest.mark.asyncio
async def test_paginator_clamps_an_out_of_range_page() -> None:
    view = QueuePaginator(queue=make_queue(12), author_id=1, initial_page=50)
    assert view.current_page == 2
    assert view.build_embed().footer.text == "Yokai · Page 2/2"
    assert view.next_button.disabled is True
    assert view.prev_button.disabled is False


@pytest.mark.asyncio
async def test_paginator_page_is_clamped_again_when_the_queue_shrinks() -> None:
    queue = make_queue(12)
    view = QueuePaginator(queue=queue, author_id=1, initial_page=2)
    queue.clear()
    assert view.build_embed().footer.text == "Yokai · Page 1/1"
    assert view.current_page == 1


@pytest.mark.asyncio
async def test_queue_command_reads_the_live_track_and_keeps_the_message() -> None:
    cog, player = make_cog_and_player(PlayerState.PLAYING)
    player.queue = make_queue(3)
    player.current_track = Track(video_id="old00000001", title="Old Song")
    message = MagicMock(spec=discord.Message)
    with patch("yokai.cogs.music.send", new=AsyncMock(return_value=message)) as send:
        await cog.queue.callback(cog, make_interaction(), page=1)
    view = send.await_args.kwargs["view"]
    assert isinstance(view, QueuePaginator)
    assert view.message is message
    player.current_track = Track(video_id="new00000001", title="New Song")
    assert "New Song" in view.build_embed().description


def test_queue_pages_use_the_theme_page_size() -> None:
    import inspect

    from yokai.theme import LIST_PAGE_SIZE

    # 10 == 10 at runtime either way, so check the source names the token, not a literal.
    assert "per_page: int = LIST_PAGE_SIZE" in inspect.getsource(MusicQueue.get_page)
    items, total_pages, total = make_queue(LIST_PAGE_SIZE + 1).get_page(1)
    assert (len(items), total_pages, total) == (LIST_PAGE_SIZE, 2, LIST_PAGE_SIZE + 1)
