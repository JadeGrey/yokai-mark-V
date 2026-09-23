"""In-process yt-dlp audio resolver with semaphore concurrency and error mapping."""

from __future__ import annotations

import asyncio
import collections
import logging
import time
from pathlib import Path
from typing import Any, Optional

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

logger = logging.getLogger(__name__)


class YtDlpResolver(Resolver):
    """In-process audio resolver utilizing yt-dlp and external Deno JS runtime."""

    def __init__(
        self,
        deno_path: Optional[Path] = None,
        cookies_file: Optional[Path] = None,
        max_track_seconds: int = 3600,
        concurrency_limit: int = 2,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.deno_path = deno_path
        self.cookies_file = cookies_file
        self.max_track_seconds = max_track_seconds
        self.timeout_seconds = timeout_seconds
        self._semaphore = asyncio.Semaphore(concurrency_limit)

        # Health telemetry & error ring buffer
        self.consecutive_failures = 0
        self.is_degraded = False
        self.error_ring_buffer: collections.deque[str] = collections.deque(maxlen=20)

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

    def _map_and_record_error(self, exc: Exception, context: str) -> YokaiError:
        """Map raw yt-dlp exceptions to typed domain errors and update health state."""
        self.consecutive_failures += 1
        if self.consecutive_failures >= 3:
            if not self.is_degraded:
                logger.warning(
                    "YouTube resolver marked DEGRADED after %d consecutive failures.",
                    self.consecutive_failures,
                )
            self.is_degraded = True

        err_msg = str(exc)
        sanitized = err_msg.splitlines()[0] if err_msg else type(exc).__name__
        self.error_ring_buffer.append(f"{context}: {sanitized}")

        err_lower = err_msg.lower()

        if "sign in to confirm your age" in err_lower or "confirm your age" in err_lower:
            return LoginRequiredError("This video is age-restricted and requires authentication.")
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

    async def resolve_query(self, query: str, requester_id: int) -> Track:
        """Search YouTube for a query using ytsearch5 and return the first valid non-live track."""
        async with self._semaphore:
            opts = self._get_base_opts()
            opts["noplaylist"] = True

            search_term = f"ytsearch5:{query}"

            def _search() -> dict[str, Any]:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    return ydl.extract_info(search_term, download=False) or {}

            try:
                data = await asyncio.wait_for(
                    asyncio.to_thread(_search),
                    timeout=self.timeout_seconds,
                )
            except asyncio.TimeoutError as err:
                mapped = ExtractionTimeoutError()
                self._map_and_record_error(mapped, f"search '{query}'")
                raise mapped from err
            except Exception as exc:
                mapped = self._map_and_record_error(exc, f"search '{query}'")
                raise mapped from exc

            entries = data.get("entries") or []
            for entry in entries:
                if not entry:
                    continue
                # Skip live streams
                if entry.get("is_live"):
                    continue
                duration = int(entry.get("duration") or 0)
                if duration <= 0 or duration > self.max_track_seconds:
                    continue

                vid = entry.get("id")
                title = entry.get("title") or "Unknown Title"
                artist = entry.get("uploader") or entry.get("channel") or entry.get("artist")
                thumb = entry.get("thumbnail")

                self._record_success()
                return Track(
                    video_id=vid,
                    title=title,
                    artist=artist,
                    duration_s=duration,
                    thumbnail_url=thumb,
                    requester_id=requester_id,
                    origin="search",
                )

            raise NotFoundError(
                f"No suitable non-live tracks found for search '{query}'.",
                user_hint="Try searching with a different title or direct link.",
            )

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

            def _extract() -> dict[str, Any]:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    return ydl.extract_info(url, download=False) or {}

            try:
                data = await asyncio.wait_for(
                    asyncio.to_thread(_extract),
                    timeout=self.timeout_seconds,
                )
            except asyncio.TimeoutError as err:
                mapped = ExtractionTimeoutError()
                self._map_and_record_error(mapped, f"url '{url}'")
                raise mapped from err
            except Exception as exc:
                mapped = self._map_and_record_error(exc, f"url '{url}'")
                raise mapped from exc

            # Check if result is a playlist
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
                    title = entry.get("title") or "Unknown Title"
                    artist = entry.get("uploader") or entry.get("channel")
                    thumb = entry.get("thumbnail")

                    tracks.append(
                        Track(
                            video_id=vid,
                            title=title,
                            artist=artist,
                            duration_s=duration,
                            thumbnail_url=thumb,
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
                return tracks, True

            # Single track
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

            title = data.get("title") or "Unknown Title"
            artist = data.get("uploader") or data.get("channel") or data.get("artist")
            thumb = data.get("thumbnail")

            self._record_success()
            return (
                Track(
                    video_id=vid,
                    title=title,
                    artist=artist,
                    duration_s=duration,
                    thumbnail_url=thumb,
                    requester_id=requester_id,
                    origin="link",
                ),
                False,
            )

    async def get_stream(self, track: Track) -> StreamInfo:
        """Fetch fresh playback stream URL and HTTP headers for FFmpeg."""
        async with self._semaphore:
            opts = self._get_base_opts()
            opts["noplaylist"] = True

            def _get_info() -> dict[str, Any]:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    return ydl.extract_info(track.url, download=False) or {}

            try:
                data = await asyncio.wait_for(
                    asyncio.to_thread(_get_info),
                    timeout=self.timeout_seconds,
                )
            except asyncio.TimeoutError as err:
                mapped = ExtractionTimeoutError()
                self._map_and_record_error(mapped, f"stream '{track.video_id}'")
                raise mapped from err
            except Exception as exc:
                mapped = self._map_and_record_error(exc, f"stream '{track.video_id}'")
                raise mapped from exc

            stream_url = data.get("url")
            if not stream_url:
                raise BotCheckError(
                    "Failed to acquire direct audio stream format from YouTube.",
                    user_hint="YouTube may have throttled or format-restricted this track.",
                )

            headers = data.get("http_headers") or {}
            self._record_success()
            return StreamInfo(
                url=stream_url,
                http_headers=headers,
                expires_at=time.time() + 3600,  # rough 1 hour validity hint
            )
