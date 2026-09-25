"""Tests for the local audio pre-buffer."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Optional

import pytest

from yokai.music.buffer import AudioBuffer
from yokai.music.models import StreamInfo, Track


def make_downloader(size: int = 1000, delay: float = 0.0, calls: Optional[list[str]] = None):
    async def download(url: str, dest: Path) -> None:
        if calls is not None:
            calls.append(url)
        if delay:
            await asyncio.sleep(delay)
        dest.write_bytes(b"x" * size)

    return download


def track(video_id: str) -> Track:
    return Track(video_id=video_id, title="T", duration_s=100)


@pytest.mark.asyncio
async def test_schedule_downloads_and_get_returns_finished_file(tmp_path: Path) -> None:
    calls: list[str] = []
    buffer = AudioBuffer(tmp_path / "cache", 10_000, downloader=make_downloader(calls=calls))
    buffer.schedule(track("aaaaaaaa"), StreamInfo(url="http://s/1"))
    buffer.schedule(track("aaaaaaaa"), StreamInfo(url="http://s/1"))  # single-flight
    await buffer.wait_idle()
    path = buffer.get("aaaaaaaa")
    assert path is not None and path.read_bytes() == b"x" * 1000
    assert calls == ["http://s/1"]
    assert not list((tmp_path / "cache").glob("*.part"))


@pytest.mark.asyncio
async def test_get_ignores_unfinished_downloads(tmp_path: Path) -> None:
    buffer = AudioBuffer(tmp_path, 10_000, downloader=make_downloader(delay=0.2))
    buffer.schedule(track("aaaaaaaa"), StreamInfo(url="http://s/1"))
    await asyncio.sleep(0.05)
    assert buffer.get("aaaaaaaa") is None
    await buffer.wait_idle()
    assert buffer.get("aaaaaaaa") is not None


@pytest.mark.asyncio
async def test_unsafe_video_ids_are_never_buffered(tmp_path: Path) -> None:
    calls: list[str] = []
    buffer = AudioBuffer(tmp_path, 10_000, downloader=make_downloader(calls=calls))
    for bad in ["sp:abc123", "../evil", "..\\evil", "", "a" * 200, "has space", "ab"]:
        buffer.schedule(track(bad), StreamInfo(url="http://s/x"))
        assert buffer.get(bad) is None
    await buffer.wait_idle()
    assert calls == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_download_failure_leaves_no_files(tmp_path: Path) -> None:
    async def failing(url: str, dest: Path) -> None:
        dest.write_bytes(b"partial")
        raise OSError("disk full")

    buffer = AudioBuffer(tmp_path, 10_000, downloader=failing)
    buffer.schedule(track("aaaaaaaa"), StreamInfo(url="http://s/1"))
    await buffer.wait_idle()  # must not raise
    assert buffer.get("aaaaaaaa") is None
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_disabled_buffer_does_nothing(tmp_path: Path) -> None:
    calls: list[str] = []
    buffer = AudioBuffer(tmp_path, 0, downloader=make_downloader(calls=calls))
    assert buffer.enabled is False
    buffer.schedule(track("aaaaaaaa"), StreamInfo(url="http://s/1"))
    await buffer.wait_idle()
    assert calls == []


def _write(path: Path, size: int, mtime: float) -> None:
    path.write_bytes(b"x" * size)
    os.utime(path, (mtime, mtime))


@pytest.mark.asyncio
async def test_eviction_removes_oldest_first(tmp_path: Path) -> None:
    buffer = AudioBuffer(tmp_path, 2500)
    _write(tmp_path / "aaaaaaaa.mka", 1000, 100)
    _write(tmp_path / "bbbbbbbb.mka", 1000, 200)
    _write(tmp_path / "cccccccc.mka", 1000, 300)
    buffer._evict()
    assert sorted(p.name for p in tmp_path.glob("*.mka")) == ["bbbbbbbb.mka", "cccccccc.mka"]


@pytest.mark.asyncio
async def test_eviction_skips_protected_ids(tmp_path: Path) -> None:
    buffer = AudioBuffer(tmp_path, 2500)
    _write(tmp_path / "aaaaaaaa.mka", 1000, 100)
    _write(tmp_path / "bbbbbbbb.mka", 1000, 200)
    _write(tmp_path / "cccccccc.mka", 1000, 300)
    buffer.protect({"aaaaaaaa"})
    buffer._evict()
    assert sorted(p.name for p in tmp_path.glob("*.mka")) == ["aaaaaaaa.mka", "cccccccc.mka"]


@pytest.mark.asyncio
async def test_start_and_clear_wipe_the_directory(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "stale.mka").write_bytes(b"old")
    buffer = AudioBuffer(cache, 10_000, downloader=make_downloader(delay=5.0))
    await buffer.start()
    assert list(cache.iterdir()) == []

    buffer.schedule(track("aaaaaaaa"), StreamInfo(url="http://s/1"))
    await asyncio.sleep(0.05)
    await buffer.clear()  # cancels the in-flight download
    assert list(cache.iterdir()) == []
    assert buffer.get("aaaaaaaa") is None
