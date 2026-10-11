"""Local audio pre-buffer: upcoming tracks are downloaded so track changes are gapless.

While track N plays, the next one or two tracks are copied (not re-encoded) into `<video_id>.mka`
files. The player then hands FFmpeg a local file, which starts in a fraction of a second and never
stalls on the network. A buffer failure never blocks playback: the player falls back to the URL.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import os
import re
from pathlib import Path
from typing import Awaitable, Callable, Iterable, Optional

from yokai.music.models import StreamInfo, Track

logger = logging.getLogger(__name__)

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{6,32}$")
_URL = re.compile(r"https?://\S+")
# FFmpeg prints this once for every YouTube opus/webm stream copy, yet exits 0 and writes a
# complete, valid file: it is parser noise, not a failure. Addresses vary per run.
_BENIGN_OPUS_LINE = re.compile(r"^\[opus @ [0-9a-fA-Fx]+\] Error parsing Opus packet header\.\s*$")
DOWNLOAD_TIMEOUT_S = 120.0
RETRY_DELAY_S = 2.0  # pause before the single retry of a failed pre-buffer download

Downloader = Callable[[str, Path], Awaitable[None]]


def redact_urls(text: str) -> str:
    """Replace URLs (stream URLs carry the public IP and a signature) with `<url>`."""
    return _URL.sub("<url>", text)


def _significant_stderr(text: str) -> str:
    """Return `text` without the known-benign opus line and blank lines ('' = nothing left)."""
    lines = (line.strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line and not _BENIGN_OPUS_LINE.match(line))


async def ffmpeg_download(url: str, dest: Path, ffmpeg: str = "ffmpeg") -> None:
    """Copy the audio stream at `url` into `dest` (Matroska container) without re-encoding."""
    proc = await asyncio.create_subprocess_exec(
        ffmpeg,
        "-nostdin",
        "-loglevel",
        "error",
        "-y",
        "-reconnect",
        "1",
        "-reconnect_streamed",
        "1",
        "-reconnect_delay_max",
        "5",
        "-rw_timeout",
        "15000000",  # microseconds: give up on a read that stalls for 15 s
        "-i",
        url,
        "-vn",
        "-c:a",
        "copy",
        "-f",
        "matroska",
        str(dest),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=DOWNLOAD_TIMEOUT_S)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        proc.kill()
        await proc.wait()
        raise
    # Stream URLs carry the public IP and a signature; keep them out of error messages.
    detail = redact_urls(stderr.decode(errors="replace")).strip()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg exited {proc.returncode}: {detail[:200]}")
    significant = _significant_stderr(detail)
    if significant:
        # With `-loglevel error`, FFmpeg can still exit 0 after a mid-stream error ("File ended
        # prematurely", "Stream ends prematurely", ...), leaving a truncated or damaged copy. The
        # one benign opus parse line is ignored; any other output is treated as a failure.
        raise RuntimeError(f"ffmpeg reported errors: {significant[:200]}")


class AudioBuffer:
    """Bounded on-disk cache of upcoming tracks, evicted least-recently-used first."""

    def __init__(
        self,
        directory: Path,
        max_bytes: int,
        downloader: Optional[Downloader] = None,
        ffmpeg: str = "ffmpeg",
    ) -> None:
        self.directory = directory
        self.max_bytes = max_bytes
        self._downloader: Downloader = downloader or functools.partial(
            ffmpeg_download, ffmpeg=ffmpeg
        )
        self._semaphore = asyncio.Semaphore(1)
        self._inflight: dict[str, asyncio.Task[Optional[Path]]] = {}
        self._protected: set[str] = set()
        self._discarded: set[str] = set()  # discarded files that could not be deleted yet

    @property
    def enabled(self) -> bool:
        return self.max_bytes > 0

    async def start(self) -> None:
        """Delete audio files left by a previous run and make sure the directory exists."""
        await asyncio.to_thread(self._reset_directory)

    def _reset_directory(self) -> None:
        """Delete only our own files (`*.mka`, `*.part`); anything else in the folder stays."""
        self.directory.mkdir(parents=True, exist_ok=True)
        for pattern in ("*.mka", "*.part"):
            for path in self.directory.glob(pattern):
                if path.is_file():
                    self._unlink_quietly(path)

    def _unlink_quietly(self, path: Path) -> None:
        """Delete a file, logging but not raising OSError (e.g., permission errors on Windows)."""
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.debug("Failed to unlink %s: %s", path, exc)

    def path_for(self, video_id: str) -> Optional[Path]:
        """Destination path for a video, or None if the ID is not a safe YouTube-style ID."""
        if not _SAFE_ID.fullmatch(video_id):
            return None
        return self.directory / f"{video_id}.mka"

    def get(self, video_id: str) -> Optional[Path]:
        """Return the finished file for a video (and mark it recently used), or None."""
        path = self.path_for(video_id)
        if path is None:
            return None
        if video_id in self._discarded:
            self._unlink_quietly(path)
            if not path.exists():
                self._discarded.discard(video_id)
            return None
        if not path.is_file():
            return None
        try:
            os.utime(path)
        except OSError:
            return None
        return path

    def discard(self, video_id: str) -> None:
        """Delete a video's finished file (e.g., it ended early, so it may be truncated).

        On Windows the file can still be open by the FFmpeg process that just played it; then
        it is remembered as discarded, never served again, and deleted on a later `get`.
        """
        path = self.path_for(video_id)
        if path is None:
            return
        self._unlink_quietly(path)
        if path.exists():
            self._discarded.add(video_id)

    def protect(self, video_ids: Iterable[str]) -> None:
        """Never evict these videos (the current and upcoming tracks)."""
        self._protected = set(video_ids)

    def schedule(self, track: Track, stream: StreamInfo) -> None:
        """Start downloading a track in the background. Idempotent. Needs a running loop."""
        if not self.enabled:
            return
        video_id = track.video_id
        if self.path_for(video_id) is None or video_id in self._inflight:
            return
        if self.get(video_id) is not None:
            return
        task = asyncio.get_running_loop().create_task(self._download(video_id, stream.url))
        self._inflight[video_id] = task
        task.add_done_callback(lambda _t, vid=video_id: self._inflight.pop(vid, None))

    async def _download(self, video_id: str, url: str) -> Optional[Path]:
        dest = self.path_for(video_id)
        if dest is None:
            return None
        part = dest.with_suffix(".part")
        async with self._semaphore:
            for attempt in (1, 2):
                try:
                    self.directory.mkdir(parents=True, exist_ok=True)
                    await self._downloader(url, part)
                    os.replace(part, dest)
                    self._discarded.discard(video_id)  # the old file was replaced by a fresh one
                    break
                except asyncio.CancelledError:
                    self._unlink_quietly(part)
                    raise
                except Exception as exc:  # any failure just means "no buffer for this track"
                    # str() of a timeout is empty, so always name the exception type too.
                    logger.warning(
                        "Audio pre-buffer failed for %s (attempt %d of 2): %s: %s",
                        video_id,
                        attempt,
                        type(exc).__name__,
                        exc,
                    )
                    self._unlink_quietly(part)
                    if attempt == 2:
                        return None
                    await asyncio.sleep(RETRY_DELAY_S)
        self._evict()
        return dest

    def _evict(self) -> None:
        """Delete least-recently-used finished files until the cache fits `max_bytes`."""
        try:
            files = [p for p in self.directory.glob("*.mka") if p.is_file()]
            sizes = {p: p.stat().st_size for p in files}
            total = sum(sizes.values())
            for path in sorted(files, key=lambda p: p.stat().st_mtime_ns):
                if total <= self.max_bytes:
                    break
                if path.stem in self._protected:
                    continue
                path.unlink(missing_ok=True)
                total -= sizes[path]
        except OSError as exc:
            logger.warning("Audio buffer eviction failed: %s", exc)

    async def wait_idle(self) -> None:
        """Wait until no downloads are in flight (used by tests and the harness)."""
        while self._inflight:
            await asyncio.gather(*list(self._inflight.values()), return_exceptions=True)
            await asyncio.sleep(0)

    async def clear(self) -> None:
        """Cancel downloads and delete every buffered file (called on /stop)."""
        tasks = list(self._inflight.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._inflight.clear()
        await asyncio.to_thread(self._reset_directory)
        self._discarded.clear()
