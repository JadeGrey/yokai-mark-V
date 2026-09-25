"""Guild audio player: generation-tagged state machine, watchdog and gapless pre-spawn.

How races are avoided (see docs/superpowers/plans/2026-09-24-music-core-plan.md, Task 9):

* Every playback intent (start, skip, stop, resume) increments `_generation`. The voice
  `after` callback carries the generation it was created in, and a callback from an older
  generation is ignored, so our own `voice_client.stop()` can never advance the queue twice.
* At most one `_advance_task` is alive. It is cancellable, so skip and stop always work even
  when a stream lookup is stuck. There is no lock.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Coroutine, Optional

import discord

from yokai.errors import UnmatchedTrackError, VoiceError, YokaiError
from yokai.music.buffer import AudioBuffer, redact_urls
from yokai.music.models import LoopMode, MusicQueue, PlayerState, StreamInfo, Track
from yokai.music.resolvers.base import Resolver
from yokai.music.timing import StageTimer

if TYPE_CHECKING:
    from yokai.bot import YokaiBot

logger = logging.getLogger(__name__)

STREAM_ATTEMPT_TIMEOUT_S = 20.0  # one stream lookup; a track gets two attempts
LOAD_WATCHDOG_S = 60.0  # backstop: LOADING this long means something is wedged
WATCHDOG_INTERVAL_S = 2.0
WATCHDOG_SILENT_TICKS = 2  # consecutive "PLAYING but voice idle" ticks before recovery
PREBUILD_LEAD_S = 8  # start the next FFmpeg this many seconds before the track ends
PREMATURE_END_MARGIN_S = 10  # ending this far before the known duration counts as cut off
MAX_RESUME_ATTEMPTS = 2
RESUMED_END_GRACE_S = 5  # a resumed source ending this soon after its offset = real end
INSTANT_END_FLOOR_S = 5  # a clean end before this many seconds of a track = dead stream
INSTANT_END_MIN_DURATION_S = 30  # ...but only for tracks at least this long
PREFETCH_DEPTH = 2  # how many upcoming tracks get their stream URL and buffer file ready
STALL_GRACE_S = 30  # PLAYING this long past the known duration = a stalled read
# -rw_timeout (microseconds): give up on a network read that stalls for 15 s (dead TCP link)
FFMPEG_BEFORE_OPTIONS = (
    "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -rw_timeout 15000000"
)


@dataclass(slots=True)
class _Prepared:
    """An FFmpeg-backed source built ahead of time for a specific queued track."""

    track: Track
    source: discord.PCMVolumeTransformer
    from_file: bool = False


class GuildPlayer:
    """Manages audio playback, voice connection, queue, and timers for a single guild."""

    def __init__(
        self,
        guild_id: int,
        bot: YokaiBot,
        resolver: Resolver,
        buffer: Optional[AudioBuffer] = None,
    ) -> None:
        self.guild_id = guild_id
        self.bot = bot
        self.resolver = resolver
        self.buffer = buffer
        self.queue = MusicQueue(max_size=bot.config.max_queue_size)

        self.state: PlayerState = PlayerState.IDLE
        self.current_track: Optional[Track] = None
        self.voice_client: Optional[discord.VoiceClient] = None
        self.text_channel: Optional[discord.abc.Messageable] = None

        # Playback volume (0.0 to 1.0)
        self.volume: float = 0.5
        self._current_source: Optional[discord.PCMVolumeTransformer] = None
        self._current_from_file: bool = False  # current source reads a local buffer file

        # Elapsed timing (monotonic clock minus paused time, plus a resume offset)
        self._track_start_time: float = 0.0
        self._paused_duration: float = 0.0
        self._pause_start_time: float = 0.0
        self._track_offset: int = 0

        # Concurrency control
        self._generation: int = 0
        self._advance_task: Optional[asyncio.Task[None]] = None
        self._watchdog_task: Optional[asyncio.Task[None]] = None
        self._background: set[asyncio.Task[Any]] = set()
        self._loading_since: float = 0.0
        self._silent_ticks: int = 0
        self._resume_attempts: int = 0
        self._last_finish_at: Optional[float] = None
        self._failure_notified: bool = False

        # Gapless: an FFmpeg source already started for the head of the queue
        self._prepared: Optional[_Prepared] = None
        self._prebuilding: bool = False

        # Prefetch cache: (video_id, StreamInfo) for the head of the queue
        self._prefetched: Optional[tuple[str, StreamInfo]] = None
        self._prefetch_task: Optional[asyncio.Task[None]] = None

        # Disconnect timers
        self._alone_timer: Optional[asyncio.TimerHandle] = None
        self._idle_timer: Optional[asyncio.TimerHandle] = None

    # ------------------------------------------------------------------ properties

    @property
    def is_playing(self) -> bool:
        return self.state == PlayerState.PLAYING and self.voice_client is not None

    @property
    def is_paused(self) -> bool:
        return self.state == PlayerState.PAUSED

    @property
    def elapsed_seconds(self) -> int:
        """Elapsed playback time in seconds, accounting for pauses and resume offsets."""
        if self.state in (PlayerState.IDLE, PlayerState.LOADING):
            return 0
        now = self._pause_start_time if self.state == PlayerState.PAUSED else time.monotonic()
        elapsed = now - self._track_start_time - self._paused_duration
        return max(0, int(elapsed) + self._track_offset)

    # ------------------------------------------------------------------ connection

    async def connect(self, channel: discord.VoiceChannel) -> discord.VoiceClient:
        """Connect to the specified voice channel or move to it if already connected."""
        self._cancel_alone_timer()
        self._cancel_idle_timer()

        if self.voice_client is not None and self.voice_client.is_connected():
            if self.voice_client.channel.id != channel.id:
                logger.info(
                    "Moving voice connection from #%s to #%s",
                    self.voice_client.channel.name,
                    channel.name,
                )
                await self.voice_client.move_to(channel)
            return self.voice_client

        logger.info("Connecting to voice channel #%s (%d)", channel.name, channel.id)
        try:
            with StageTimer("voice_connect", channel=channel.id):
                self.voice_client = await channel.connect(reconnect=True, timeout=20.0)
            await self.bot.presence.set_active()
        except Exception as exc:
            logger.error("Failed to connect to voice channel #%s: %s", channel.name, exc)
            raise VoiceError(f"Could not connect to voice channel #{channel.name}: {exc}") from exc

        self._ensure_watchdog()
        # Leave again if nothing ever gets queued; starting playback cancels this timer.
        self._start_idle_timer()
        return self.voice_client

    # --------------------------------------------------------------------- helpers

    def _spawn(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        """Run a coroutine in the background, keeping a reference until it finishes."""
        task = asyncio.get_running_loop().create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        return task

    @staticmethod
    def _discard_source(source: discord.AudioSource) -> None:
        try:
            source.cleanup()
        except Exception as exc:
            logger.debug("Error cleaning up audio source: %s", exc)

    # ------------------------------------------------------------ starting playback

    async def play_next(self) -> None:
        """Start playback if idle, or wait for the advance already in flight."""
        task = self._advance_task
        if task is not None and not task.done():
            await asyncio.wait({task})
            return
        if self.state in (PlayerState.PLAYING, PlayerState.PAUSED):
            return
        await asyncio.wait({self._begin_advance()})

    def _pop_next_track(self) -> Optional[Track]:
        """Pop the next track and make it current (synchronously, so state is never stale)."""
        track = self.queue.pop_next()
        self.current_track = track
        self._resume_attempts = 0
        if track is not None:
            self.state = PlayerState.LOADING
            self._loading_since = time.monotonic()
            self._silent_ticks = 0
        return track

    def _begin_advance(self, resume: Optional[tuple[Track, int]] = None) -> asyncio.Task[None]:
        """Invalidate in-flight work and start the single worker that plays the next track."""
        self._generation += 1
        generation = self._generation
        if self._advance_task is not None and not self._advance_task.done():
            self._advance_task.cancel()
        self._cancel_idle_timer()

        if resume is not None:
            track: Optional[Track] = resume[0]
            start_s = resume[1]
        else:
            track = self._pop_next_track()
            start_s = 0
            if track is None:
                self._enter_idle()

        self._advance_task = asyncio.get_running_loop().create_task(
            self._advance_worker(generation, track, start_s)
        )
        return self._advance_task

    def _enter_idle(self) -> None:
        logger.info("Queue is empty. Player entering IDLE state.")
        self.state = PlayerState.IDLE
        self.current_track = None
        self._current_source = None
        self._failure_notified = False  # the failure burst (if any) ends here
        self._start_idle_timer()

    async def _advance_worker(self, generation: int, track: Optional[Track], start_s: int) -> None:
        """Load `track` (or, on failure, the following ones) and start playing it."""
        source: Optional[discord.PCMVolumeTransformer] = None
        from_file = False
        try:
            while track is not None and generation == self._generation:
                try:
                    with StageTimer("track_load", video=track.video_id, resume_s=start_s):
                        prepared = self._take_prepared(track) if start_s == 0 else None
                        if prepared is not None:
                            source, from_file = prepared.source, prepared.from_file
                        else:
                            source, from_file = await self._prepare_source(track, start_s)
                    if generation != self._generation:
                        return  # superseded while loading; `finally` discards the source
                    ready, source = source, None
                    # Raises only if the voice client refuses to play (it cleans up `ready`).
                    self._play_source(generation, track, ready, start_s, from_file)
                    return
                except Exception as exc:
                    if generation != self._generation:
                        return
                    await self._on_load_failed(track, exc)
                    if generation != self._generation:
                        return
                    track, start_s = self._pop_next_track(), 0
                    if track is None:
                        self._enter_idle()
                    continue
        finally:
            if source is not None:
                self._discard_source(source)

    async def _ensure_matched(self, track: Track) -> None:
        """Lazily match an imported Spotify track to a YouTube video just before it is needed."""
        if not (track.is_pending_match and track.spotify_meta):
            return
        matcher = getattr(self.bot, "matcher", None)
        matched = (
            await matcher.match_track(track.spotify_meta, track.requester_id) if matcher else None
        )
        if matched is None:
            raise UnmatchedTrackError(track.spotify_meta.title)
        track.video_id = matched.video_id
        track.title = matched.title
        track.artist = matched.artist
        track.duration_s = matched.duration_s
        track.thumbnail_url = matched.thumbnail_url
        track.origin = "import"
        track.is_pending_match = False

    async def _stream_target(self, track: Track, use_buffer: bool = True) -> tuple[str, bool]:
        """Return (FFmpeg input, is_local_file): buffer file, prefetched URL, or fresh URL."""
        if use_buffer and self.buffer is not None:
            path = self.buffer.get(track.video_id)
            if path is not None:
                return str(path), True
        prefetched = self._prefetched
        if prefetched is not None and prefetched[0] == track.video_id:
            self._prefetched = None
            expires_at = prefetched[1].expires_at
            if expires_at is None or expires_at > time.time():
                return prefetched[1].url, False
        info = await asyncio.wait_for(
            self.resolver.get_stream(track), timeout=STREAM_ATTEMPT_TIMEOUT_S
        )
        return info.url, False

    def _build_source(
        self, target: str, is_file: bool, start_s: int
    ) -> discord.PCMVolumeTransformer:
        ffmpeg_exec = str(self.bot.config.ffmpeg_path) if self.bot.config.ffmpeg_path else "ffmpeg"
        before = "" if is_file else FFMPEG_BEFORE_OPTIONS
        if start_s > 0:
            before = f"{before} -ss {start_s}".strip()
        source = discord.FFmpegPCMAudio(
            target,
            executable=ffmpeg_exec,
            before_options=before or None,
            options="-vn",
        )
        return discord.PCMVolumeTransformer(source, volume=self.volume)

    async def _prepare_source(
        self, track: Track, start_s: int
    ) -> tuple[discord.PCMVolumeTransformer, bool]:
        """Match if needed, find a stream, start FFmpeg. Two attempts, each time-limited.

        Returns (source, from_file). A resume (`start_s > 0`) or a retry never uses the local
        buffer file: a file that ended early is likely truncated, so seeking in it is useless.
        """
        await self._ensure_matched(track)
        error: Exception = RuntimeError("no stream attempt was made")
        for attempt in (1, 2):
            try:
                use_buffer = start_s == 0 and attempt == 1
                target, is_file = await self._stream_target(track, use_buffer=use_buffer)
                return self._build_source(target, is_file, start_s), is_file
            except Exception as exc:
                error = exc
                logger.warning("Stream attempt %d failed for %s: %s", attempt, track.video_id, exc)
                self._prefetched = None
                self.resolver.invalidate_stream(track.video_id)
        raise error

    def _take_prepared(self, track: Track) -> Optional[_Prepared]:
        prepared, self._prepared = self._prepared, None
        if prepared is None:
            return None
        if prepared.track is track:
            return prepared
        self._discard_source(prepared.source)
        return None

    def _play_source(
        self,
        generation: int,
        track: Track,
        source: discord.PCMVolumeTransformer,
        start_s: int,
        from_file: bool = False,
    ) -> None:
        vc = self.voice_client
        if vc is None or not vc.is_connected():
            logger.warning("Voice client disconnected right before play()")
            self._discard_source(source)
            self._enter_idle()
            return

        loop = asyncio.get_running_loop()

        def _after(error: Optional[Exception]) -> None:
            # discord.py's AudioPlayer only checks `_current_error` on the source it was given
            # (our PCMVolumeTransformer), so FFmpeg failures arrive here as None. The wrapped
            # FFmpegPCMAudio sets its own `_current_error` inside read() when it hits EOF and
            # FFmpeg has already exited non-zero, i.e. BEFORE this callback runs; cleanup()
            # runs after this callback and sets `_stopped` first, so it never sets it later.
            # This runs on the audio thread, so reading it here does not touch the event loop.
            # If FFmpeg had not been reaped yet at EOF the error is lost; the instant-end floor
            # and the premature-end check in `_on_source_finished` cover that case.
            if error is None:
                error = getattr(getattr(source, "original", None), "_current_error", None)
            try:
                loop.call_soon_threadsafe(self._on_source_finished, generation, track, error)
            except RuntimeError:
                pass  # event loop already closed (shutdown)

        self._current_source = source
        self._current_from_file = from_file
        self._track_start_time = time.monotonic()
        self._paused_duration = 0.0
        self._pause_start_time = 0.0
        self._track_offset = start_s
        try:
            vc.play(source, after=_after)
        except Exception:
            # e.g. discord.ClientException; never leak the FFmpeg process we just started
            self._current_source = None
            self._current_from_file = False
            self._discard_source(source)
            raise
        self.state = PlayerState.PLAYING
        self._silent_ticks = 0
        self._failure_notified = False

        if self._last_finish_at is not None:
            gap_ms = (time.perf_counter() - self._last_finish_at) * 1000.0
            logger.info("stage=track_gap ms=%.0f status=ok video=%s", gap_ms, track.video_id)
            self._last_finish_at = None
        logger.info("Playback started: %s (%s)", track.title, track.video_id)
        self._trigger_prefetch()

    # ----------------------------------------------------------- track end handling

    def _retire_current(self, outcome: str, listened: Optional[int] = None) -> Optional[Track]:
        """Record how the current track ended and apply loop rules. Synchronous."""
        track = self.current_track
        if track is None:
            return None
        if listened is None:
            listened = self.elapsed_seconds
        self.current_track = None
        self._current_source = None
        self._spawn(self._record_event(track, outcome=outcome, listened_s=listened))
        self.queue.record_history(track)

        mode = self.queue.loop_mode
        if mode == LoopMode.TRACK and outcome == "completed":
            self.queue.push_front(track)
        elif mode == LoopMode.QUEUE and outcome in ("completed", "skipped"):
            self.queue.add(track)
        return track

    def _on_source_finished(
        self, generation: int, track: Track, error: Optional[BaseException]
    ) -> None:
        """Voice `after` callback, delivered on the event loop. Ignores stale generations."""
        if generation != self._generation or self.current_track is not track:
            return

        listened = self.elapsed_seconds
        if (
            error is None
            and track.duration_s >= INSTANT_END_MIN_DURATION_S
            and listened < INSTANT_END_FLOOR_S
        ):
            # FFmpeg can exit 0 after a dead connection; a long track that "ends" in its first
            # seconds is a failed stream, not a finished song (and must never loop forever).
            error = RuntimeError("stream ended immediately")
        premature = (
            error is None
            and track.duration_s > 0
            and listened < track.duration_s - PREMATURE_END_MARGIN_S
        )
        if premature and self._track_offset > 0:
            # A resumed source that ends almost at once means the stream really ends here
            # (the known duration was too long), so it is finished, not cut off.
            premature = listened - self._track_offset >= RESUMED_END_GRACE_S
        if error is not None:
            # FFmpeg errors include its stderr, which can contain the signed stream URL.
            logger.error("Track %s ended with error: %s", track.video_id, redact_urls(str(error)))
        if (error is not None or premature) and self._current_from_file:
            # The network cannot cut off a local file, so the file itself is bad (truncated).
            self._current_from_file = False
            if self.buffer is not None:
                self.buffer.discard(track.video_id)

        if (error is not None or premature) and self._resume_attempts < MAX_RESUME_ATTEMPTS:
            self._resume_attempts += 1
            logger.warning(
                "Track %s ended early at %ds of %ds; resuming (attempt %d).",
                track.video_id,
                listened,
                track.duration_s,
                self._resume_attempts,
            )
            self.resolver.invalidate_stream(track.video_id)
            self._generation += 1
            generation = self._generation
            self.state = PlayerState.LOADING
            self._loading_since = time.monotonic()
            if self._advance_task is not None and not self._advance_task.done():
                self._advance_task.cancel()
            self._advance_task = asyncio.get_running_loop().create_task(
                self._advance_worker(generation, track, listened)
            )
            return

        self._last_finish_at = time.perf_counter()
        # A clean end is never an error, even when resume attempts ran out.
        outcome = "error" if error is not None else "completed"
        self._retire_current(outcome, listened=listened)
        if error is not None and self._claim_failure_notice():
            # Claimed synchronously, so going idle below correctly ends this burst.
            self._spawn(self._send_failure_notice(track, error))
        self._begin_advance()

    async def _on_load_failed(self, track: Track, exc: BaseException) -> None:
        logger.error("Could not load %s (%s): %s", track.title, track.video_id, exc)
        self._retire_current("error", listened=0)
        await self._notify_failure(track, exc)

    async def _notify_failure(self, track: Track, exc: BaseException) -> None:
        """Tell the channel once per failure burst (a successful track resets the burst)."""
        if self._claim_failure_notice():
            await self._send_failure_notice(track, exc)

    def _claim_failure_notice(self) -> bool:
        """Return True (and mark the burst notified) if a notice should be sent now."""
        if self._failure_notified or self.text_channel is None:
            return False
        self._failure_notified = True
        return True

    async def _send_failure_notice(self, track: Track, exc: BaseException) -> None:
        if self.text_channel is None:
            return

        from yokai.ui.embeds import EmbedFactory

        reason = (
            exc.message if isinstance(exc, YokaiError) else "The audio stream could not be loaded."
        )
        description = f"Skipping **{track.title}**. {reason}"
        if getattr(self.resolver, "is_degraded", False) is True:
            description += "\nYouTube is giving me trouble. The owner can check `/diag`."
        embed = EmbedFactory.warning(
            title="Track Skipped", description=description, context="Playback"
        )
        try:
            await self.text_channel.send(embed=embed)
        except discord.HTTPException as send_err:
            logger.debug("Could not send failure notice: %s", send_err)

    async def _record_event(self, track: Track, outcome: str, listened_s: int) -> None:
        """Log playback event to SQLite play_events table (YouTube-derived fields only)."""
        if track.is_pending_match or track.video_id.startswith("sp:"):
            logger.debug(
                "Skipping play_events logging for unmatched/Spotify entity %s", track.video_id
            )
            return
        try:
            await self.bot.db.record_play_event(
                user_id=track.requester_id,
                video_id=track.video_id,
                title=track.title,
                artist=track.artist,
                duration_s=track.duration_s,
                origin=track.origin,
                outcome=outcome,
                listened_s=listened_s,
            )
        except Exception as exc:
            logger.error("Failed to record play event for %s: %s", track.video_id, exc)

    # ------------------------------------------------------------ prefetch / buffer

    def warm_stream(self, track: Track) -> None:
        """Start extracting a track's stream URL now so playback can join the work in flight."""
        if track.is_pending_match:
            return

        async def _warm() -> None:
            try:
                await self.resolver.get_stream(track)
            except Exception as exc:
                logger.debug("Stream warm-up failed for %s: %s", track.video_id, exc)

        self._spawn(_warm())

    def maybe_prefetch(self) -> None:
        """Trigger prefetching of upcoming tracks if playback is currently active."""
        if self.is_playing:
            self._trigger_prefetch()

    def _trigger_prefetch(self) -> None:
        upcoming = list(self.queue.upcoming[:PREFETCH_DEPTH])
        if not upcoming:
            return
        if self._prefetch_task is not None and not self._prefetch_task.done():
            self._prefetch_task.cancel()
        self._prefetch_task = asyncio.get_running_loop().create_task(self._do_prefetch(upcoming))

    async def _do_prefetch(self, tracks: list[Track]) -> None:
        if self.buffer is not None:
            keep = {t.video_id for t in tracks}
            if self.current_track is not None:
                keep.add(self.current_track.video_id)
            self.buffer.protect(keep)
        for index, track in enumerate(tracks):
            try:
                await self._ensure_matched(track)
                info = await self.resolver.get_stream(track, background=True)
            except Exception as exc:
                logger.debug("Prefetch failed for %s: %s", track.video_id, exc)
                continue
            if index == 0:
                self._prefetched = (track.video_id, info)
            if self.buffer is not None:
                self.buffer.schedule(track, info)
            logger.debug("Prefetched %s", track.video_id)

    def _maybe_prebuild_next(self) -> None:
        """Start the next track's FFmpeg process shortly before the current one ends."""
        track = self.current_track
        if (
            track is None
            or track.duration_s <= 0
            or self._prebuilding
            or self._prepared is not None
            or self.queue.loop_mode == LoopMode.TRACK
        ):
            return
        if track.duration_s - self.elapsed_seconds > PREBUILD_LEAD_S:
            return
        upcoming = self.queue.peek_next()
        if upcoming is None:
            return
        self._prebuilding = True
        self._spawn(self._prebuild_next(upcoming, self._generation))

    async def _prebuild_next(self, upcoming: Track, generation: int) -> None:
        try:
            source, from_file = await self._prepare_source(upcoming, 0)
        except Exception as exc:
            logger.debug("Pre-build of %s failed: %s", upcoming.video_id, exc)
            return
        finally:
            self._prebuilding = False
        if (
            generation != self._generation
            or self.queue.peek_next() is not upcoming
            or self._prepared is not None
        ):
            self._discard_source(source)
            return
        self._prepared = _Prepared(upcoming, source, from_file)

    # --------------------------------------------------------------------- watchdog

    def _ensure_watchdog(self) -> None:
        if self._watchdog_task is None or self._watchdog_task.done():
            self._watchdog_task = asyncio.get_running_loop().create_task(self._watchdog_loop())

    async def _watchdog_loop(self) -> None:
        while True:
            await asyncio.sleep(WATCHDOG_INTERVAL_S)
            try:
                self._watchdog_tick(time.monotonic())
            except Exception:
                logger.exception("Player watchdog tick failed")

    def _watchdog_tick(self, now: float) -> None:
        """One synchronous health check: stuck loads, silent voice, stale/pre-built sources."""
        if self.state == PlayerState.LOADING and now - self._loading_since > LOAD_WATCHDOG_S:
            logger.error("Loading exceeded %.0fs; abandoning the current track.", LOAD_WATCHDOG_S)
            self._retire_current("error", listened=0)
            self._begin_advance()
            return

        if self._prepared is not None and self._prepared.track is not self.queue.peek_next():
            stale, self._prepared = self._prepared, None
            self._discard_source(stale.source)

        vc = self.voice_client
        track = self.current_track
        if self.state != PlayerState.PLAYING or vc is None or track is None:
            self._silent_ticks = 0
            return

        if not vc.is_playing() and not vc.is_paused():
            self._silent_ticks += 1
            if self._silent_ticks >= WATCHDOG_SILENT_TICKS:
                self._silent_ticks = 0
                logger.warning("Voice went silent during %s; recovering.", track.video_id)
                self._on_source_finished(
                    self._generation, track, RuntimeError("voice client went silent")
                )
            return

        self._silent_ticks = 0
        if track.duration_s > 0 and self.elapsed_seconds > track.duration_s + STALL_GRACE_S:
            # FFmpeg is blocked on a dead read (the voice client still says "playing"). The
            # track did play through, so retire it and move on; never resume past the end.
            logger.warning("Playback of %s stalled past its end; moving on.", track.video_id)
            self._retire_current("completed")
            vc.stop()  # its `after` callback is stale by the time it runs
            self._begin_advance()
            return
        self._maybe_prebuild_next()

    # --------------------------------------------------------------------- controls

    def pause(self) -> bool:
        """Pause playback."""
        if self.state == PlayerState.PLAYING and self.voice_client:
            self.voice_client.pause()
            self.state = PlayerState.PAUSED
            self._pause_start_time = time.monotonic()
            return True
        return False

    def resume(self) -> bool:
        """Resume playback."""
        if self.state == PlayerState.PAUSED and self.voice_client:
            self.voice_client.resume()
            self.state = PlayerState.PLAYING
            self._paused_duration += time.monotonic() - self._pause_start_time
            return True
        return False

    async def skip(self) -> Optional[Track]:
        """Skip the current track (even one that is still loading) and return it."""
        skipped = self._retire_current("skipped")
        if skipped is None:
            return None
        vc = self.voice_client
        if vc is not None and (vc.is_playing() or vc.is_paused()):
            vc.stop()  # its `after` callback is stale by the time it runs: _begin_advance bumps
        self._begin_advance()
        return skipped

    async def stop(self) -> None:
        """Stop playback, clear the queue, disconnect voice, clear the buffer, reset presence."""
        logger.info("Stopping player and clearing queue.")
        self._generation += 1  # every pending callback and worker is now stale
        if self._advance_task is not None and not self._advance_task.done():
            self._advance_task.cancel()
        self._advance_task = None

        track = self.current_track
        if track is not None:
            self._spawn(
                self._record_event(track, outcome="stopped", listened_s=self.elapsed_seconds)
            )
        self.current_track = None
        self._current_source = None
        self.queue.clear()
        self.state = PlayerState.IDLE
        self._failure_notified = False

        self._prefetched = None
        if self._prefetch_task is not None and not self._prefetch_task.done():
            self._prefetch_task.cancel()
        prepared, self._prepared = self._prepared, None
        if prepared is not None:
            self._discard_source(prepared.source)
        self._prebuilding = False

        self._cancel_alone_timer()
        self._cancel_idle_timer()
        if self._watchdog_task is not None and not self._watchdog_task.done():
            self._watchdog_task.cancel()
        self._watchdog_task = None

        vc, self.voice_client = self.voice_client, None
        if vc is not None:
            if vc.is_playing() or vc.is_paused():
                vc.stop()
            if vc.is_connected():
                await vc.disconnect(force=True)

        if self.buffer is not None:
            await self.buffer.clear()
        await self.bot.presence.set_idle()

    def set_volume(self, level: int) -> int:
        """Set volume level (0-100) and adjust active and pre-built transformers."""
        clamped = min(max(level, 0), 100)
        self.volume = clamped / 100.0
        if self._current_source:
            self._current_source.volume = self.volume
        if self._prepared is not None:
            self._prepared.source.volume = self.volume
        return clamped

    # ------------------------------------------------------------ voice occupancy

    def on_voice_member_update(self) -> None:
        """Evaluate channel occupancy to start or cancel alone disconnect timer."""
        if not self.voice_client or not self.voice_client.is_connected():
            return

        channel = self.voice_client.channel
        if not isinstance(channel, discord.VoiceChannel):
            return

        humans = [m for m in channel.members if not m.bot]
        if not humans:
            self._start_alone_timer()
        else:
            self._cancel_alone_timer()

    def _start_alone_timer(self) -> None:
        """Start alone disconnect timer if not already running."""
        if self._alone_timer is None:
            delay = float(self.bot.config.alone_disconnect_seconds)
            logger.info(
                "Alone in voice channel #%s. Disconnect timer set (%ds).",
                self.voice_client.channel.name if self.voice_client else "?",
                delay,
            )
            self._alone_timer = self.bot.loop.call_later(
                delay,
                lambda: asyncio.create_task(self._on_alone_timeout()),
            )

    def _cancel_alone_timer(self) -> None:
        if self._alone_timer:
            self._alone_timer.cancel()
            self._alone_timer = None

    async def _on_alone_timeout(self) -> None:
        logger.info("Alone timer expired. Auto-leaving voice channel.")
        self._alone_timer = None
        await self.stop()

    def _start_idle_timer(self) -> None:
        """Start idle disconnect timer if queue is empty."""
        if self._idle_timer is None and self.queue.is_empty:
            delay = float(self.bot.config.idle_disconnect_seconds)
            logger.info("Player idle with empty queue. Disconnect timer set (%ds).", delay)
            self._idle_timer = self.bot.loop.call_later(
                delay,
                lambda: asyncio.create_task(self._on_idle_timeout()),
            )

    def _cancel_idle_timer(self) -> None:
        if self._idle_timer:
            self._idle_timer.cancel()
            self._idle_timer = None

    async def _on_idle_timeout(self) -> None:
        logger.info("Idle timer expired. Auto-leaving voice channel.")
        self._idle_timer = None
        await self.stop()
