"""Tests for the local audio pre-buffer."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, Mock

import pytest

from yokai.music.buffer import AudioBuffer, ffmpeg_download
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


@pytest.mark.asyncio
async def test_unsafe_video_ids_with_newlines_and_spaces(tmp_path: Path) -> None:
    calls: list[str] = []
    buffer = AudioBuffer(tmp_path, 10_000, downloader=make_downloader(calls=calls))
    for bad in ["aaaaaaaa\n", "aaaaaaaa\r\n", " aaaaaaaa", "aaaaaaaa "]:
        assert buffer.path_for(bad) is None
        buffer.schedule(track(bad), StreamInfo(url="http://s/x"))
        assert buffer.get(bad) is None
    await buffer.wait_idle()
    assert calls == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_cancel_with_hanging_downloader_cleans_up_part_file(tmp_path: Path) -> None:
    async def hanging_downloader(url: str, dest: Path) -> None:
        dest.write_bytes(b"partial")
        await asyncio.sleep(10)  # never completes

    buffer = AudioBuffer(tmp_path, 10_000, downloader=hanging_downloader)
    buffer.schedule(track("aaaaaaaa"), StreamInfo(url="http://s/1"))
    await asyncio.sleep(0.05)
    task = buffer._inflight.get("aaaaaaaa")
    assert task is not None
    task.cancel()
    results = await asyncio.gather(task, return_exceptions=True)
    assert len(results) == 1 and isinstance(results[0], asyncio.CancelledError)
    assert task.cancelled()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_download_failure_with_unlink_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def failing_downloader(url: str, dest: Path) -> None:
        dest.write_bytes(b"partial")
        raise OSError("disk full")

    buffer = AudioBuffer(tmp_path, 10_000, downloader=failing_downloader)
    orig_unlink = Path.unlink

    def failing_unlink(self: Path, missing_ok: bool = False) -> None:
        if str(self).endswith(".part"):
            raise PermissionError("file in use")
        return orig_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", failing_unlink)
    buffer.schedule(track("aaaaaaaa"), StreamInfo(url="http://s/1"))
    await buffer.wait_idle()  # must not raise
    assert buffer.get("aaaaaaaa") is None


@pytest.mark.asyncio
async def test_cancel_while_waiting_for_semaphore(tmp_path: Path) -> None:
    downloads_started = asyncio.Event()

    async def slow_downloader(url: str, dest: Path) -> None:
        if "1" in url:
            downloads_started.set()
            await asyncio.sleep(10)
        else:
            downloads_started.wait()
            dest.write_bytes(b"x" * 1000)

    buffer = AudioBuffer(tmp_path, 10_000, downloader=slow_downloader)
    buffer.schedule(track("aaaaaaaa"), StreamInfo(url="http://s/1"))
    await asyncio.sleep(0.05)
    buffer.schedule(track("bbbbbbbb"), StreamInfo(url="http://s/2"))
    task_b = buffer._inflight.get("bbbbbbbb")
    assert task_b is not None
    task_b.cancel()
    results = await asyncio.gather(task_b, return_exceptions=True)
    assert len(results) == 1 and isinstance(results[0], asyncio.CancelledError)
    assert task_b.cancelled()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_ffmpeg_download_arguments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_proc = AsyncMock()
    fake_proc.communicate = AsyncMock(return_value=(b"", b""))
    fake_proc.returncode = 0

    async def fake_create_subprocess_exec(*args, **kwargs):
        fake_proc.args = args
        return fake_proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    url = "http://example.com/stream"
    dest = tmp_path / "output.mka"
    await ffmpeg_download(url, dest, ffmpeg="custom_ffmpeg")
    assert fake_proc.args[0] == "custom_ffmpeg"
    assert "-reconnect" in fake_proc.args
    assert "-reconnect_streamed" in fake_proc.args
    assert "-reconnect_delay_max" in fake_proc.args
    assert "-i" in fake_proc.args
    i_index = fake_proc.args.index("-i")
    assert fake_proc.args[i_index + 1] == url
    assert "-vn" in fake_proc.args
    assert "-c:a" in fake_proc.args
    assert "copy" in fake_proc.args
    assert "-f" in fake_proc.args
    assert "matroska" in fake_proc.args
    assert str(dest) in fake_proc.args


@pytest.mark.asyncio
async def test_ffmpeg_download_non_zero_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_proc = AsyncMock()
    fake_proc.communicate = AsyncMock(return_value=(b"", b"Stream error details"))
    fake_proc.returncode = 1

    async def fake_create_subprocess_exec(*args, **kwargs):
        return fake_proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    url = "http://example.com/stream"
    dest = tmp_path / "output.mka"
    with pytest.raises(RuntimeError, match="ffmpeg exited 1"):
        await ffmpeg_download(url, dest)


@pytest.mark.asyncio
async def test_ffmpeg_download_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_proc = AsyncMock()
    fake_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
    fake_proc.kill = Mock()
    fake_proc.wait = AsyncMock()

    async def fake_create_subprocess_exec(*args, **kwargs):
        return fake_proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("yokai.music.buffer.DOWNLOAD_TIMEOUT_S", 0.001)
    url = "http://example.com/stream"
    dest = tmp_path / "output.mka"
    with pytest.raises(asyncio.TimeoutError):
        await ffmpeg_download(url, dest)
    assert fake_proc.kill.called
    assert fake_proc.wait.called
