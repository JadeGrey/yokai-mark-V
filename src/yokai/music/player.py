"""Guild audio player state machine, voice streaming, prefetching, and lifecycle management."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Optional

import discord

from yokai.errors import VoiceError
from yokai.music.models import LoopMode, MusicQueue, PlayerState, StreamInfo, Track
from yokai.music.resolvers.base import Resolver

if TYPE_CHECKING:
    from yokai.bot import YokaiBot

logger = logging.getLogger(__name__)


class GuildPlayer:
    """Manages audio playback, voice connection, queue, and timers for a single guild."""

    def __init__(
        self,
        guild_id: int,
        bot: YokaiBot,
        resolver: Resolver,
    ) -> None:
        self.guild_id = guild_id
        self.bot = bot
        self.resolver = resolver
        self.queue = MusicQueue(max_size=bot.config.max_queue_size)

        self.state: PlayerState = PlayerState.IDLE
        self.current_track: Optional[Track] = None
        self.voice_client: Optional[discord.VoiceClient] = None
        self.text_channel: Optional[discord.abc.Messageable] = None

        # Playback volume (0.0 to 1.0)
        self.volume: float = 0.5
        self._current_source: Optional[discord.PCMVolumeTransformer] = None

        # Playback elapsed timing
        self._track_start_time: float = 0.0
        self._paused_duration: float = 0.0
        self._pause_start_time: float = 0.0

        # Prefetch cache: (video_id, StreamInfo)
        self._prefetched: Optional[tuple[str, StreamInfo]] = None
        self._prefetch_task: Optional[asyncio.Task[None]] = None

        # Retry tracking
        self._retry_attempted: bool = False

        # Disconnect timers
        self._alone_timer: Optional[asyncio.TimerHandle] = None
        self._idle_timer: Optional[asyncio.TimerHandle] = None

        # Escalation flag to notify text channel only once per failure burst
        self._escalated: bool = False

        # Concurrency lock ensuring serialized queue transitions
        self._play_lock: asyncio.Lock = asyncio.Lock()

    @property
    def is_playing(self) -> bool:
        return self.state == PlayerState.PLAYING and self.voice_client is not None

    @property
    def is_paused(self) -> bool:
        return self.state == PlayerState.PAUSED

    @property
    def elapsed_seconds(self) -> int:
        """Calculate elapsed playback time in seconds, accounting for pause intervals."""
        if self.state in (PlayerState.IDLE, PlayerState.LOADING):
            return 0
        now = time.monotonic()
        if self.state == PlayerState.PAUSED:
            elapsed = self._pause_start_time - self._track_start_time - self._paused_duration
        else:
            elapsed = now - self._track_start_time - self._paused_duration
        return max(0, int(elapsed))

    async def connect(self, channel: discord.VoiceChannel) -> discord.VoiceClient:
        """Connect to the specified voice channel or move to it if already connected."""
        # Cancel any pending disconnect timers
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
            self.voice_client = await channel.connect(reconnect=True, timeout=20.0)
            await self.bot.presence.set_active()
            return self.voice_client
        except Exception as exc:
            logger.error("Failed to connect to voice channel #%s: %s", channel.name, exc)
            raise VoiceError(f"Could not connect to voice channel #{channel.name}: {exc}") from exc

    async def play_next(self) -> None:
        """Advance queue and start playback of the next track with concurrency protection."""
        async with self._play_lock:
            await self._play_next_inner()

    async def _play_next_inner(self) -> None:
        """Internal queue advancement without re-acquiring _play_lock."""
        self._cancel_idle_timer()

        if not self.voice_client or not self.voice_client.is_connected():
            logger.warning("play_next called without active voice connection.")
            self.state = PlayerState.IDLE
            self._start_idle_timer()
            return

        track = self.queue.pop_next()
        if not track:
            logger.info("Queue is empty. Player entering IDLE state.")
            self.state = PlayerState.IDLE
            self.current_track = None
            self._start_idle_timer()
            return

        self.current_track = track
        self.state = PlayerState.LOADING
        self._retry_attempted = False

        await self._start_playback(track)

    async def _start_playback(self, track: Track) -> None:
        """Resolve stream info and invoke FFmpeg playback."""
        # 0. Lazy match Spotify tracks if pending
        if track.is_pending_match and track.spotify_meta:
            matcher = getattr(self.bot, "matcher", None)
            if matcher:
                try:
                    matched = await matcher.match_track(track.spotify_meta, track.requester_id)
                    if matched:
                        track.video_id = matched.video_id
                        track.title = matched.title
                        track.artist = matched.artist
                        track.duration_s = matched.duration_s
                        track.thumbnail_url = matched.thumbnail_url
                        track.origin = "import"
                        track.is_pending_match = False
                    else:
                        logger.warning("Unmatched Spotify track: %s", track.spotify_meta.title)
                        if self.text_channel:
                            from yokai.ui.embeds import EmbedFactory

                            warn_embed = EmbedFactory.warning(
                                title="Track Skipped (Unmatched)",
                                description=(
                                    "Could not find a confident YouTube match for "
                                    f"**{track.spotify_meta.title}**."
                                ),
                                context="Auto-Skip",
                            )
                            try:
                                await self.text_channel.send(embed=warn_embed)
                            except Exception:
                                pass
                        await self._play_next_inner()
                        return
                except Exception as exc:
                    logger.error("Error matching track %s: %s", track.spotify_meta.title, exc)
                    await self._play_next_inner()
                    return

        stream_info: Optional[StreamInfo] = None

        # 1. Check if prefetched
        if self._prefetched and self._prefetched[0] == track.video_id:
            logger.debug("Prefetch cache hit for track %s", track.video_id)
            stream_info = self._prefetched[1]
            self._prefetched = None
        else:
            try:
                stream_info = await self.resolver.get_stream(track)
            except Exception as exc:
                logger.error("Failed to get stream for track %s: %s", track.video_id, exc)
                await self._handle_stream_failure(track, exc)
                return

        # 2. Configure FFmpeg source
        try:
            ffmpeg_exec = str(self.bot.config.ffmpeg_path) if self.bot.config.ffmpeg_path else None
            before_args = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"

            ffmpeg_source = discord.FFmpegPCMAudio(
                source=stream_info.url,
                executable=ffmpeg_exec or "ffmpeg",
                before_options=before_args,
                options="-vn",
            )
            volume_transformer = discord.PCMVolumeTransformer(ffmpeg_source, volume=self.volume)
            self._current_source = volume_transformer

            # Reset timing counters
            self._track_start_time = time.monotonic()
            self._paused_duration = 0.0
            self._pause_start_time = 0.0

            def _after(err: Optional[Exception]) -> None:
                asyncio.run_coroutine_threadsafe(
                    self._on_track_end(track, err),
                    self.bot.loop,
                )

            if self.voice_client and self.voice_client.is_connected():
                self.voice_client.play(volume_transformer, after=_after)
                self.state = PlayerState.PLAYING
                logger.info("Playback started: %s (%s)", track.title, track.video_id)
                self._trigger_prefetch()
            else:
                logger.warning("Voice client disconnected right before play()")
                self.state = PlayerState.IDLE
                self._start_idle_timer()

        except Exception as exc:
            logger.error("FFmpeg error starting playback for %s: %s", track.video_id, exc)
            await self._handle_stream_failure(track, exc)

    async def _handle_stream_failure(self, track: Track, exc: Exception) -> None:
        """Retry playback once with fresh stream URL or advance to next track."""
        if not self._retry_attempted:
            logger.info("Retrying playback once for track %s...", track.video_id)
            self._retry_attempted = True
            self._prefetched = None
            try:
                fresh_stream = await self.resolver.get_stream(track)
                self._prefetched = (track.video_id, fresh_stream)
                await self._start_playback(track)
                return
            except Exception as retry_exc:
                logger.error("Retry failed for track %s: %s", track.video_id, retry_exc)

        # Record error outcome
        await self._record_event(track, outcome="error", listened_s=0)

        # Notify escalation if YouTube health is degraded
        if getattr(self.resolver, "is_degraded", False) and not self._escalated:
            self._escalated = True
            if self.text_channel:
                from yokai.ui.embeds import EmbedFactory

                warn_embed = EmbedFactory.warning(
                    title="YouTube Connection Degraded",
                    description=(
                        "YouTube is giving me trouble resolving audio formats. "
                        "The owner can check `/diag`."
                    ),
                    context="Health Notice",
                )
                try:
                    await self.text_channel.send(embed=warn_embed)
                except Exception as send_err:
                    logger.debug("Could not send escalation warning: %s", send_err)

        # Move to next track
        await self._play_next_inner()

    def maybe_prefetch(self) -> None:
        """Trigger prefetching of the next track if playback is currently active."""
        if self.is_playing:
            self._trigger_prefetch()

    def _trigger_prefetch(self) -> None:
        """Schedule prefetching of the next track's stream URL in the background."""
        next_track = self.queue.peek_next()
        if not next_track or (self._prefetched and self._prefetched[0] == next_track.video_id):
            return

        async def _do_prefetch(track: Track) -> None:
            try:
                if track.is_pending_match and track.spotify_meta:
                    matcher = getattr(self.bot, "matcher", None)
                    if matcher:
                        matched = await matcher.match_track(track.spotify_meta, track.requester_id)
                        if matched:
                            track.video_id = matched.video_id
                            track.title = matched.title
                            track.artist = matched.artist
                            track.duration_s = matched.duration_s
                            track.thumbnail_url = matched.thumbnail_url
                            track.origin = "import"
                            track.is_pending_match = False
                info = await self.resolver.get_stream(track)
                self._prefetched = (track.video_id, info)
                logger.debug("Prefetched stream URL for %s", track.video_id)
            except Exception as exc:
                logger.debug("Prefetch failed for %s: %s", track.video_id, exc)

        if self._prefetch_task and not self._prefetch_task.done():
            self._prefetch_task.cancel()

        self._prefetch_task = asyncio.create_task(_do_prefetch(next_track))

    async def _on_track_end(self, track: Track, error: Optional[Exception]) -> None:
        """Handle track completion, determine outcome, update loops, and advance."""
        listened = self.elapsed_seconds

        if error:
            logger.error("Track %s ended with error: %s", track.video_id, error)
            # If error happened early in track, attempt retry once
            if listened < 5 and not self._retry_attempted:
                await self._handle_stream_failure(track, error)
                return
            outcome = "error"
        else:
            # Check completed threshold (90% or natural end)
            if track.duration_s > 0 and listened >= int(0.9 * track.duration_s):
                outcome = "completed"
            elif self.state == PlayerState.IDLE:
                outcome = "stopped"
            else:
                outcome = "completed"

        await self._record_event(track, outcome=outcome, listened_s=listened)
        self.queue.record_history(track)

        # Handle loop modes
        if self.queue.loop_mode == LoopMode.TRACK:
            self.queue.add(track)  # Re-enqueue
        elif self.queue.loop_mode == LoopMode.QUEUE:
            self.queue.add(track)

        # Continue queue
        await self.play_next()

    async def _record_event(self, track: Track, outcome: str, listened_s: int) -> None:
        """Log playback event to SQLite play_events table (YouTube-derived fields only)."""
        # Strictly guard against persisting Spotify-derived fields or dummy IDs into analytics
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
        """Skip current playing track and return it."""
        if not self.current_track:
            return None
        skipped = self.current_track
        listened = self.elapsed_seconds
        await self._record_event(skipped, outcome="skipped", listened_s=listened)

        if self.voice_client and self.voice_client.is_playing():
            # Stopping voice_client triggers _after callback
            self.voice_client.stop()
        else:
            await self.play_next()

        return skipped

    async def stop(self) -> None:
        """Stop playback, clear queue, disconnect voice client, and reset presence."""
        logger.info("Stopping player and clearing queue.")
        self.queue.clear()
        self.current_track = None
        self.state = PlayerState.IDLE

        self._cancel_alone_timer()
        self._cancel_idle_timer()

        if self.voice_client:
            if self.voice_client.is_playing() or self.voice_client.is_paused():
                self.voice_client.stop()
            if self.voice_client.is_connected():
                await self.voice_client.disconnect(force=True)
            self.voice_client = None

        await self.bot.presence.set_idle()

    def set_volume(self, level: int) -> int:
        """Set volume level (0-100) and adjust active transformer."""
        clamped = min(max(level, 0), 100)
        self.volume = clamped / 100.0
        if self._current_source:
            self._current_source.volume = self.volume
        return clamped

    def on_voice_member_update(self) -> None:
        """Evaluate channel occupancy to start or cancel alone disconnect timer."""
        if not self.voice_client or not self.voice_client.is_connected():
            return

        channel = self.voice_client.channel
        if not isinstance(channel, discord.VoiceChannel):
            return

        # Count non-bot members
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
                self.voice_client.channel.name,
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
