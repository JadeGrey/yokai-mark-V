"""In-process yt-dlp audio resolver: priority concurrency, single-flight and error mapping."""

from __future__ import annotations

import asyncio
import collections
import logging
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import yt_dlp

from yokai.errors import (
    BotCheckError,
    ExtractionTimeoutError,
    LoginRequiredError,
    NotFoundError,
    UnavailableError,
    UnsupportedError,
    YokaiError,
)
from yokai.music.models import StreamInfo, Track
from yokai.music.resolvers.base import Resolver
from yokai.music.timing import StageTimer

if TYPE_CHECKING:
    from yokai.music.search import YtMusicSearcher

logger = logging.getLogger(__name__)

# yt-dlp `extractor_args` tried first when fetching a stream URL; None means "defaults only".
# The default profile is always kept as the fallback. Set this from docs/perf/phase1-findings.md
# only when the measured fastest usable profile beats the default by 500 ms or more.
FAST_STREAM_EXTRACTOR_ARGS: Optional[dict[str, Any]] = None

STREAM_EXPIRY_SAFETY_S = 120
DEFAULT_STREAM_TTL_S = 1800
STREAM_CACHE_MAX = 256
_EXPIRE_RE = re.compile(r"[?&/]expire[=/](\d+)")
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


def stream_expiry(url: str, now: Optional[float] = None) -> float:
    """Epoch seconds after which a stream URL should be treated as expired."""
    current = time.time() if now is None else now
    match = _EXPIRE_RE.search(url)
    if match:
        return float(match.group(1)) - STREAM_EXPIRY_SAFETY_S
    return current + DEFAULT_STREAM_TTL_S


def pick_thumbnail(entry: dict[str, Any]) -> Optional[str]:
    """Best thumbnail URL of a yt-dlp entry: `thumbnail`, else the last of `thumbnails`."""
    thumb = entry.get("thumbnail")
    if thumb:
        return str(thumb)
    thumbs = entry.get("thumbnails")
    if isinstance(thumbs, list) and thumbs and isinstance(thumbs[-1], dict):
        url = thumbs[-1].get("url")
        return str(url) if url else None
    return None


def _swallow_task_exception(task: asyncio.Task[Any]) -> None:
    """Mark a shared extraction's exception as retrieved so asyncio does not warn about it."""
    if not task.cancelled():
        task.exception()


class YtDlpResolver(Resolver):
    """In-process audio resolver utilizing yt-dlp and external Deno JS runtime."""

    def __init__(
        self,
        deno_path: Optional[Path] = None,
        cookies_file: Optional[Path] = None,
        max_track_seconds: int = 3600,
        concurrency_limit: int = 2,
        timeout_seconds: float = 30.0,
        searcher: Optional[YtMusicSearcher] = None,
    ) -> None:
        self.deno_path = deno_path
        self.cookies_file = cookies_file
        self.max_track_seconds = max_track_seconds
        self.timeout_seconds = timeout_seconds
        self.searcher = searcher
        # Foreground work (a track someone is waiting for) and background work (prefetch) use
        # separate slots so speculative extractions can never starve playback.
        self._semaphore = asyncio.Semaphore(concurrency_limit)
        self._background_semaphore = asyncio.Semaphore(1)

        # Health telemetry & error ring buffer
        self.consecutive_failures = 0
        self.is_degraded = False
        self.error_ring_buffer: collections.deque[str] = collections.deque(maxlen=20)
        self.resolve_latencies: collections.deque[float] = collections.deque(maxlen=10)

        # video_id -> (StreamInfo, expires_at epoch seconds)
        self._stream_cache: dict[str, tuple[StreamInfo, float]] = {}
        # video_id -> shared extraction task (single-flight)
        self._inflight: dict[str, asyncio.Task[StreamInfo]] = {}

    def _get_base_opts(self) -> dict[str, Any]:
        """Construct standard yt-dlp execution options."""
        js_config: dict[str, Any] = {}
        if self.deno_path and self.deno_path.is_file():
            js_config["path"] = str(self.deno_path)

        opts: dict[str, Any] = {
            "format": "bestaudio/best",
            "skip_download": True,
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": 15,
            "js_runtimes": {"deno": js_config},
        }

        if self.cookies_file and self.cookies_file.is_file():
            opts["cookiefile"] = str(self.cookies_file)

        return opts

    @staticmethod
    def _extract_sync(opts: dict[str, Any], target: str) -> dict[str, Any]:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(target, download=False) or {}

    def _note_failure(self, context: str, message: str, counts_toward_health: bool = True) -> None:
        if counts_toward_health:
            self.consecutive_failures += 1
        if self.consecutive_failures >= 3:
            if not self.is_degraded:
                logger.warning(
                    "YouTube resolver marked DEGRADED after %d consecutive failures.",
                    self.consecutive_failures,
                )
            self.is_degraded = True
        sanitized = message.splitlines()[0] if message else "error"
        self.error_ring_buffer.append(f"{context}: {sanitized}")

    def _map_and_record_error(self, exc: Exception, context: str) -> YokaiError:
        """Map raw yt-dlp exceptions to typed domain errors and update health state."""
        err_msg = _ANSI_ESCAPE.sub("", str(exc))
        mapped = exc if isinstance(exc, YokaiError) else self._classify(err_msg, exc)
        # Age gates, private or region-blocked videos are about one video, not YouTube's health.
        per_video = isinstance(mapped, (LoginRequiredError, UnavailableError, NotFoundError))
        self._note_failure(
            context, err_msg or type(exc).__name__, counts_toward_health=not per_video
        )
        return mapped

    @staticmethod
    def _classify(err_msg: str, exc: Exception) -> YokaiError:

        err_lower = err_msg.lower()
        sanitized = err_msg.splitlines()[0] if err_msg else type(exc).__name__

        if "confirm your age" in err_lower or "age-restricted" in err_lower:
            return LoginRequiredError(
                "This video is age-restricted and requires authentication.",
                user_hint="The owner can enable it by setting `YTDLP_COOKIES_FILE`.",
            )
        if "private video" in err_lower or "members-only" in err_lower:
            return LoginRequiredError("This video is private or members-only.")
        if (
            "not available in your country" in err_lower
            or "available in your country" in err_lower
            or "blocked in your country" in err_lower
            or "geo-blocked" in err_lower
            or "geo-restricted" in err_lower
        ):
            return UnavailableError("This video is region-blocked or unavailable.")
        if "video unavailable" in err_lower:
            return NotFoundError("This video is unavailable or has been removed.")
        if "403" in err_lower or "bot detection" in err_lower or "sabr" in err_lower:
            return BotCheckError("YouTube blocked the request (HTTP 403 / bot detection).")

        return YokaiError(f"Audio extraction failed: {sanitized}")

    def _record_success(self) -> None:
        """Reset consecutive failure counter on successful extraction."""
        if self.consecutive_failures > 0:
            logger.info("YouTube resolver recovered after %d failures.", self.consecutive_failures)
        self.consecutive_failures = 0
        self.is_degraded = False

    # ------------------------------------------------------------------ search

    async def resolve_query(self, query: str, requester_id: int) -> Track:
        """Resolve free text to the first playable, non-live track.

        Uses the fast YouTube Music searcher when configured and falls back to a flat yt-dlp
        search (about 1.5 s) if it finds nothing.
        """
        started = time.perf_counter()
        if self.searcher is not None:
            found = await self.searcher.search(query, requester_id, limit=5)
            if found:
                self.resolve_latencies.append((time.perf_counter() - started) * 1000.0)
                return found[0]

        try:
            entries = await self._search_flat(query, limit=5)
        except asyncio.TimeoutError as err:
            mapped = ExtractionTimeoutError()
            self._map_and_record_error(mapped, f"search '{query}'")
            raise mapped from err
        except Exception as exc:
            mapped = self._map_and_record_error(exc, f"search '{query}'")
            raise mapped from exc

        for entry in entries:
            if entry.get("is_live") or entry.get("live_status") in ("is_live", "is_upcoming"):
                continue
            duration = int(entry.get("duration") or 0)
            if duration <= 0 or duration > self.max_track_seconds:
                continue
            video_id = entry.get("id")
            if not video_id:
                continue

            self._record_success()
            self.resolve_latencies.append((time.perf_counter() - started) * 1000.0)
            return Track(
                video_id=str(video_id),
                title=entry.get("title") or "Unknown Title",
                artist=entry.get("uploader") or entry.get("channel") or entry.get("artist"),
                duration_s=duration,
                thumbnail_url=pick_thumbnail(entry),
                requester_id=requester_id,
                origin="search",
            )

        raise NotFoundError(
            f"No suitable non-live tracks found for search '{query}'.",
            user_hint="Try searching with a different title or direct link.",
        )

    async def _search_flat(self, query: str, limit: int) -> list[dict[str, Any]]:
        """yt-dlp search without extracting each result (10 s -> about 1.5 s)."""
        async with self._semaphore:
            opts = self._get_base_opts()
            opts["noplaylist"] = True
            opts["extract_flat"] = "in_playlist"
            with StageTimer("search_ytdlp_flat", limit=limit):
                data = await asyncio.wait_for(
                    asyncio.to_thread(self._extract_sync, opts, f"ytsearch{limit}:{query}"),
                    timeout=self.timeout_seconds,
                )
        entries = data.get("entries") or []
        return [e for e in entries if isinstance(e, dict)]

    async def search_candidates(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Query yt-dlp for search candidates with metadata for matching."""
        try:
            return await self._search_flat(query, limit)
        except Exception as exc:
            logger.debug("yt-dlp candidate search failed for %s: %s", query, exc)
            return []

    # --------------------------------------------------------------- resolve_url

    async def resolve_url(
        self, url: str, requester_id: int, max_tracks: int = 100
    ) -> tuple[Track | list[Track], bool]:
        """Resolve a direct YouTube watch or playlist URL."""
        async with self._semaphore:
            is_playlist_url = "/playlist" in url or "list=" in url

            opts = self._get_base_opts()
            if is_playlist_url:
                opts["extract_flat"] = "in_playlist"
            else:
                opts["noplaylist"] = True

            start_time = time.perf_counter()
            try:
                data = await asyncio.wait_for(
                    asyncio.to_thread(self._extract_sync, opts, url),
                    timeout=self.timeout_seconds,
                )
            except asyncio.TimeoutError as err:
                mapped = ExtractionTimeoutError()
                self._map_and_record_error(mapped, f"url '{url}'")
                raise mapped from err
            except Exception as exc:
                mapped = self._map_and_record_error(exc, f"url '{url}'")
                raise mapped from exc

            entries = data.get("entries")
            if entries is not None:
                tracks: list[Track] = []
                for entry in entries:
                    if not entry:
                        continue
                    if entry.get("is_live"):
                        continue
                    duration = int(entry.get("duration") or 0)
                    if duration > self.max_track_seconds:
                        continue

                    vid = entry.get("id")
                    if not vid:
                        continue
                    tracks.append(
                        Track(
                            video_id=vid,
                            title=entry.get("title") or "Unknown Title",
                            artist=entry.get("uploader") or entry.get("channel"),
                            duration_s=duration,
                            thumbnail_url=pick_thumbnail(entry),
                            requester_id=requester_id,
                            origin="link",
                        )
                    )
                    if len(tracks) >= max_tracks:
                        break

                if not tracks:
                    raise NotFoundError(
                        "The playlist contains no playable tracks or is empty.",
                        user_hint="Check if the playlist is public.",
                    )

                self._record_success()
                self.resolve_latencies.append((time.perf_counter() - start_time) * 1000.0)
                return tracks, True

            vid = data.get("id")
            if not vid:
                raise NotFoundError("Could not resolve video metadata.")

            if data.get("is_live"):
                raise UnsupportedError(
                    "Live streams are not currently supported.",
                    user_hint="Yokai only streams recorded songs and video uploads.",
                )

            duration = int(data.get("duration") or 0)
            if duration > self.max_track_seconds:
                raise UnsupportedError(
                    f"Track exceeds maximum allowed duration ({self.max_track_seconds // 60}m).",
                    user_hint="Choose a track shorter than 1 hour.",
                )

            # The single-video extraction already contains the stream URL: keep it so the
            # player does not extract the same video a second time.
            stream_url = data.get("url")
            if stream_url:
                self._remember_stream(
                    vid, StreamInfo(url=stream_url, http_headers=data.get("http_headers") or {})
                )

            self._record_success()
            self.resolve_latencies.append((time.perf_counter() - start_time) * 1000.0)
            return (
                Track(
                    video_id=vid,
                    title=data.get("title") or "Unknown Title",
                    artist=data.get("uploader") or data.get("channel") or data.get("artist"),
                    duration_s=duration,
                    thumbnail_url=data.get("thumbnail"),
                    requester_id=requester_id,
                    origin="link",
                ),
                False,
            )

    # ------------------------------------------------------------------ streams

    def _cached_stream(self, video_id: str) -> Optional[StreamInfo]:
        entry = self._stream_cache.get(video_id)
        if entry is None:
            return None
        info, expires_at = entry
        if time.time() < expires_at:
            return info
        self._stream_cache.pop(video_id, None)
        return None

    def _remember_stream(self, video_id: str, info: StreamInfo) -> StreamInfo:
        info.expires_at = stream_expiry(info.url)
        if len(self._stream_cache) >= STREAM_CACHE_MAX:
            now = time.time()
            for key in [k for k, (_i, exp) in self._stream_cache.items() if exp <= now]:
                self._stream_cache.pop(key, None)
            while len(self._stream_cache) >= STREAM_CACHE_MAX:
                self._stream_cache.pop(next(iter(self._stream_cache)))
        self._stream_cache[video_id] = (info, info.expires_at)
        return info

    def invalidate_stream(self, video_id: str) -> None:
        """Forget a cached stream URL (called before a retry)."""
        self._stream_cache.pop(video_id, None)

    @staticmethod
    def _stream_profiles() -> list[Optional[dict[str, Any]]]:
        if FAST_STREAM_EXTRACTOR_ARGS:
            return [FAST_STREAM_EXTRACTOR_ARGS, None]
        return [None]

    async def get_stream(self, track: Track, *, background: bool = False) -> StreamInfo:
        """Fetch a playable stream URL, sharing one extraction between all callers."""
        cached = self._cached_stream(track.video_id)
        if cached is not None:
            logger.debug("Stream cache hit for track %s", track.video_id)
            return cached

        task = self._inflight.get(track.video_id)
        if task is None:
            task = asyncio.get_running_loop().create_task(self._extract_stream(track, background))
            self._inflight[track.video_id] = task
            task.add_done_callback(_swallow_task_exception)
            task.add_done_callback(lambda _t, vid=track.video_id: self._inflight.pop(vid, None))
        # shield: one caller timing out or being cancelled must not kill the shared extraction
        return await asyncio.shield(task)

    async def _extract_stream(self, track: Track, background: bool) -> StreamInfo:
        semaphore = self._background_semaphore if background else self._semaphore
        async with semaphore:
            cached = self._cached_stream(track.video_id)
            if cached is not None:
                return cached

            failure: Optional[Exception] = None
            data: dict[str, Any] = {}
            with StageTimer("stream_extract", video=track.video_id, background=background):
                for profile in self._stream_profiles():
                    opts = self._get_base_opts()
                    opts["noplaylist"] = True
                    if profile is not None:
                        opts["extractor_args"] = profile
                    try:
                        data = await asyncio.wait_for(
                            asyncio.to_thread(self._extract_sync, opts, track.url),
                            timeout=self.timeout_seconds,
                        )
                    except asyncio.TimeoutError:
                        failure = ExtractionTimeoutError()
                        continue
                    except Exception as exc:
                        failure = exc
                        continue
                    if data.get("url"):
                        failure = None
                        break
                    failure = BotCheckError(
                        "Failed to acquire direct audio stream format from YouTube.",
                        user_hint="YouTube may have throttled or format-restricted this track.",
                    )

            if failure is not None:
                mapped = self._map_and_record_error(failure, f"stream '{track.video_id}'")
                if mapped is failure:
                    raise mapped
                raise mapped from failure

            self._record_success()
            info = StreamInfo(url=data["url"], http_headers=data.get("http_headers") or {})
            return self._remember_stream(track.video_id, info)

    def get_average_latency(self) -> Optional[float]:
        """Return rolling average resolution latency in milliseconds, or None if no data."""
        if not self.resolve_latencies:
            return None
        return sum(self.resolve_latencies) / len(self.resolve_latencies)
