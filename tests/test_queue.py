"""Tests for MusicQueue data structure, pagination, and loop modes."""

from __future__ import annotations

import pytest

from yokai.music.models import MusicQueue, Track


def _make_track(index: int) -> Track:
    return Track(
        video_id=f"vid_{index}",
        title=f"Track Title {index}",
        artist=f"Artist {index}",
        duration_s=180 + index,
        requester_id=1000 + index,
        origin="link",
    )


def test_queue_add_and_pop() -> None:
    queue = MusicQueue(max_size=3)
    assert queue.is_empty is True
    assert len(queue) == 0

    t1 = _make_track(1)
    t2 = _make_track(2)
    t3 = _make_track(3)
    t4 = _make_track(4)

    assert queue.add(t1) is True
    assert queue.add(t2) is True
    assert queue.add(t3) is True
    # Capacity limit
    assert queue.add(t4) is False
    assert len(queue) == 3

    # Peeking
    assert queue.peek_next() == t1
    assert len(queue) == 3

    # Popping
    assert queue.pop_next() == t1
    assert queue.pop_next() == t2
    assert queue.pop_next() == t3
    assert queue.pop_next() is None
    assert queue.is_empty is True


def test_queue_add_many() -> None:
    queue = MusicQueue(max_size=5)
    tracks = [_make_track(i) for i in range(1, 10)]

    added = queue.add_many(tracks)
    assert added == 5
    assert len(queue) == 5


def test_queue_remove() -> None:
    queue = MusicQueue(max_size=5)
    for i in range(1, 4):
        queue.add(_make_track(i))

    # Remove second track (1-based index)
    removed = queue.remove(2)
    assert removed.video_id == "vid_2"
    assert len(queue) == 2
    assert queue.peek_next().video_id == "vid_1"

    # Out of bounds
    with pytest.raises(IndexError):
        queue.remove(0)
    with pytest.raises(IndexError):
        queue.remove(5)


def test_queue_clear_and_shuffle() -> None:
    queue = MusicQueue(max_size=10)
    for i in range(1, 6):
        queue.add(_make_track(i))

    queue.shuffle()
    assert len(queue) == 5

    count = queue.clear()
    assert count == 5
    assert len(queue) == 0


def test_queue_history_cap() -> None:
    queue = MusicQueue(max_history=3)
    for i in range(1, 6):
        queue.record_history(_make_track(i))

    assert len(queue.history) == 3
    # History contains the most recent 3 items (vid_3, vid_4, vid_5)
    assert [t.video_id for t in queue.history] == ["vid_3", "vid_4", "vid_5"]


def test_queue_pagination() -> None:
    queue = MusicQueue(max_size=25)
    for i in range(1, 26):
        queue.add(_make_track(i))

    # Page 1 (per_page 10)
    items_p1, total_pages, total_tracks = queue.get_page(1, per_page=10)
    assert total_pages == 3
    assert total_tracks == 25
    assert len(items_p1) == 10
    assert items_p1[0][0] == 1  # 1-based pos
    assert items_p1[0][1].video_id == "vid_1"
    assert items_p1[9][0] == 10

    # Page 3
    items_p3, _, _ = queue.get_page(3, per_page=10)
    assert len(items_p3) == 5
    assert items_p3[0][0] == 21

    # Empty queue pagination
    empty_q = MusicQueue()
    e_items, e_pages, e_total = empty_q.get_page(1)
    assert e_items == []
    assert e_pages == 1
    assert e_total == 0
