"""Tests for the queue paginator's embed (now playing header, runtime, pending imports)."""

from __future__ import annotations

import pytest

from yokai.music.models import LoopMode, MusicQueue, Track
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
    view = QueuePaginator(queue=queue, author_id=1, current_track=current)
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
    embed = QueuePaginator(queue=queue, author_id=1, current_track=pending).build_embed()
    assert "**Imported**" in embed.description
    assert "youtube.com" not in embed.description.splitlines()[0]


@pytest.mark.asyncio
async def test_paginator_without_current_track_or_tracks() -> None:
    embed = QueuePaginator(queue=make_queue(0), author_id=1).build_embed()
    assert "The queue is empty." in embed.description
    assert "Now playing" not in embed.description
