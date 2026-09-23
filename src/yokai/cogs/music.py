"""Music playback slash commands and voice interactions."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

from yokai.errors import VoiceChannelError, VoicePermissionError
from yokai.music.classifier import InputKind, classify_input
from yokai.music.models import LoopMode, Track
from yokai.music.spotify.urls import parse_spotify_uri_or_url, resolve_spotify_link
from yokai.theme import quip_bank
from yokai.ui.embeds import EmbedFactory, send
from yokai.ui.views import QueuePaginator

if TYPE_CHECKING:
    from yokai.bot import YokaiBot
    from yokai.music.player import GuildPlayer

logger = logging.getLogger(__name__)


def _validate_voice_state(
    interaction: discord.Interaction, player: GuildPlayer
) -> discord.VoiceChannel:
    """Validate caller voice presence and bot channel permissions.

    Raises:
        VoiceChannelError: If caller is not in VC or bot is busy in another active VC.
        VoicePermissionError: If bot lacks Connect or Speak permissions.
    """
    user = interaction.user
    if not isinstance(user, discord.Member) or not user.voice or not user.voice.channel:
        raise VoiceChannelError(
            "You must be in a voice channel to use music commands.",
            user_hint="Join a voice channel and try again.",
        )

    caller_channel = user.voice.channel
    if not isinstance(caller_channel, discord.VoiceChannel):
        raise VoiceChannelError("Stage channels are not currently supported.")

    # Check bot permissions in target channel
    guild = interaction.guild
    if guild and guild.me:
        perms = caller_channel.permissions_for(guild.me)
        if not perms.connect:
            raise VoicePermissionError("Connect", caller_channel.name)
        if not perms.speak:
            raise VoicePermissionError("Speak", caller_channel.name)

    # Check if bot is already active in a different VC with listeners
    if (
        player.voice_client
        and player.voice_client.is_connected()
        and player.voice_client.channel
        and player.voice_client.channel.id != caller_channel.id
    ):
        active_vc = player.voice_client.channel
        listeners = [m for m in active_vc.members if not m.bot]
        if listeners:
            raise VoiceChannelError(
                f"Yokai is currently streaming in #{active_vc.name} for other listeners.",
                user_hint=f"Join #{active_vc.name} or wait until the channel is free.",
            )

    return caller_channel


class MusicCog(commands.Cog, name="Music"):
    """Playback, queue controls, and audio commands."""

    def __init__(self, bot: YokaiBot) -> None:
        self.bot = bot

    def _player(self, interaction: discord.Interaction) -> GuildPlayer:
        if not interaction.guild_id:
            raise VoiceChannelError("Music commands can only be executed in a server.")
        return self.bot.get_player(interaction.guild_id)

    @app_commands.command(
        name="play",
        description="Play audio from a YouTube link, playlist, or search query.",
    )
    @app_commands.describe(query="A YouTube URL, playlist link, or search keywords")
    async def play(self, interaction: discord.Interaction, query: str) -> None:
        """Resolve query or URL, connect to voice channel, and enqueue or play track."""
        player = self._player(interaction)
        caller_vc = _validate_voice_state(interaction, player)

        await interaction.response.defer(thinking=True)
        avatar_url = str(self.bot.user.display_avatar.url) if self.bot.user else None

        # 1. Connect to caller's voice channel
        await player.connect(caller_vc)
        player.text_channel = interaction.channel

        # 2. Classify input
        classified = classify_input(query)

        if classified.kind == InputKind.UNSUPPORTED:
            err_embed = EmbedFactory.error(
                message=classified.error_message or "Unsupported input.",
                user_hint=classified.user_hint,
                bot_avatar_url=avatar_url,
            )
            await send(interaction, embed=err_embed, ephemeral=True)
            return

        # 3. Resolve target
        tracks_to_queue: list[Track] = []
        is_playlist = False
        collection_info: Optional[tuple[str, str, int, int, bool]] = None

        if classified.kind == InputKind.SEARCH_QUERY:
            single = await player.resolver.resolve_query(
                classified.clean_target, interaction.user.id
            )
            tracks_to_queue = [single]
        elif classified.kind in (InputKind.YOUTUBE_TRACK, InputKind.YOUTUBE_PLAYLIST):
            result, is_pl = await player.resolver.resolve_url(
                classified.clean_target,
                interaction.user.id,
                max_tracks=self.bot.config.max_playlist_tracks,
            )
            is_playlist = is_pl
            tracks_to_queue = result if isinstance(result, list) else [result]
        elif classified.kind in (
            InputKind.SPOTIFY_TRACK,
            InputKind.SPOTIFY_ALBUM,
            InputKind.SPOTIFY_PLAYLIST,
        ):
            target = classified.clean_target
            if "spotify.link" in target:
                target = await resolve_spotify_link(target)

            entity_type, entity_id = parse_spotify_uri_or_url(target)

            if entity_type == "track":
                meta = await self.bot.spotify.resolve_track(entity_id)
                matched = await self.bot.matcher.match_track(meta, interaction.user.id)
                if not matched:
                    err_embed = EmbedFactory.error(
                        message=f"Could not find a confident YouTube match for '{meta.title}'.",
                        user_hint="Try searching with YouTube song title and artist.",
                        bot_avatar_url=avatar_url,
                    )
                    await send(interaction, embed=err_embed)
                    return
                tracks_to_queue = [matched]
                is_playlist = False
            else:
                is_playlist = True
                if entity_type == "album":
                    collection = await self.bot.spotify.resolve_album(entity_id)
                else:
                    collection = await self.bot.spotify.resolve_playlist(
                        entity_id, max_tracks=self.bot.config.max_playlist_tracks
                    )

                if not collection.tracks:
                    err_embed = EmbedFactory.error(
                        message=f"No playable tracks found in Spotify {entity_type}.",
                        user_hint="Check that the link is public and contains audio tracks.",
                        bot_avatar_url=avatar_url,
                    )
                    await send(interaction, embed=err_embed)
                    return

                collection_info = (
                    entity_type,
                    collection.name,
                    collection.count,
                    collection.total,
                    collection.partial,
                )
                tracks_to_queue = [
                    Track(
                        video_id=f"sp:{t.spotify_id}",
                        title=t.title,
                        artist=t.artist_summary,
                        duration_s=t.duration_s,
                        thumbnail_url=t.thumbnail_url,
                        requester_id=interaction.user.id,
                        origin="import",
                        spotify_id=t.spotify_id,
                        is_pending_match=True,
                        spotify_meta=t,
                    )
                    for t in collection.tracks
                ]

        if not tracks_to_queue:
            err_embed = EmbedFactory.error(
                message="No playable audio tracks were found.",
                user_hint="Check the link or search terms.",
                bot_avatar_url=avatar_url,
            )
            await send(interaction, embed=err_embed)
            return

        # 4. Enqueue and start playback
        if not is_playlist:
            track = tracks_to_queue[0]
            if not player.is_playing and not player.is_paused:
                player.queue.add(track)
                await player.play_next()
                embed = EmbedFactory.now_playing(
                    title=track.title,
                    artist=track.artist,
                    duration_s=track.duration_s,
                    position_s=0,
                    requester_mention=f"<@{interaction.user.id}>",
                    thumbnail_url=track.thumbnail_url,
                    bot_avatar_url=avatar_url,
                )
            else:
                added = player.queue.add(track)
                if not added:
                    embed = EmbedFactory.error(
                        message=f"Queue limit reached ({self.bot.config.max_queue_size} tracks).",
                        user_hint="Wait for tracks to finish or remove items.",
                        bot_avatar_url=avatar_url,
                    )
                else:
                    player.maybe_prefetch()
                    pos = len(player.queue)
                    embed = EmbedFactory.queued(
                        title=track.title,
                        position=pos,
                        artist=track.artist,
                        duration_s=track.duration_s,
                        bot_avatar_url=avatar_url,
                    )
            await send(interaction, embed=embed)
        else:
            # Playlist queueing: start first immediately if idle
            first = tracks_to_queue[0]
            rest = tracks_to_queue[1:]
            if not player.is_playing and not player.is_paused:
                player.queue.add(first)
                await player.play_next()
                added_rest = player.queue.add_many(rest)
                player.maybe_prefetch()
                total_loaded = 1 + added_rest
                msg = (
                    f"Started playback with **{first.title}** "
                    f"and queued **{added_rest}** additional tracks."
                )
            else:
                total_loaded = player.queue.add_many(tracks_to_queue)
                player.maybe_prefetch()
                msg = f"Queued **{total_loaded}** tracks from playlist."

            if collection_info:
                c_type, c_name, c_count, c_total, c_partial = collection_info
                partial_note = f" (loaded {c_count} of {c_total})" if c_partial else ""
                embed = EmbedFactory.success(
                    title=f"Spotify {c_type.title()} Loaded ({c_count} tracks)",
                    description=f"Loaded **{c_name}**{partial_note}.\n{msg}",
                    context="Spotify Import",
                    bot_avatar_url=avatar_url,
                )
            else:
                embed = EmbedFactory.success(
                    title=f"Playlist Loaded ({total_loaded} tracks)",
                    description=msg,
                    context="Playlist",
                    bot_avatar_url=avatar_url,
                )
            await send(interaction, embed=embed)

    @app_commands.command(name="pause", description="Pause current audio playback.")
    async def pause(self, interaction: discord.Interaction) -> None:
        """Pause currently playing audio."""
        player = self._player(interaction)
        _validate_voice_state(interaction, player)

        if not player.is_playing:
            embed = EmbedFactory.warning(
                title="Playback Idle",
                description="Nothing is currently playing to pause.",
                context="Controls",
            )
            await send(interaction, embed=embed, ephemeral=True)
            return

        player.pause()
        embed = EmbedFactory.success(
            title="Playback Paused",
            description="Use `/resume` to continue audio playback.",
            context="Controls",
        )
        await send(interaction, embed=embed)

    @app_commands.command(name="resume", description="Resume paused audio playback.")
    async def resume(self, interaction: discord.Interaction) -> None:
        """Resume paused audio."""
        player = self._player(interaction)
        _validate_voice_state(interaction, player)

        if not player.is_paused:
            embed = EmbedFactory.warning(
                title="Playback Active",
                description="Playback is not currently paused.",
                context="Controls",
            )
            await send(interaction, embed=embed, ephemeral=True)
            return

        player.resume()
        embed = EmbedFactory.success(
            title="Playback Resumed",
            description="Audio stream continued.",
            context="Controls",
        )
        await send(interaction, embed=embed)

    @app_commands.command(name="skip", description="Skip the current track.")
    async def skip(self, interaction: discord.Interaction) -> None:
        """Skip currently playing track."""
        player = self._player(interaction)
        _validate_voice_state(interaction, player)

        if not player.current_track:
            embed = EmbedFactory.warning(
                title="No Active Track",
                description="There is no track currently playing to skip.",
                context="Controls",
            )
            await send(interaction, embed=embed, ephemeral=True)
            return

        skipped = await player.skip()
        quip = quip_bank.get("skip")
        title = skipped.title if skipped else "Track"
        embed = EmbedFactory.success(
            title="Skipped",
            description=f"Skipped **{title}**.",
            context="Controls",
            quip=quip,
        )
        await send(interaction, embed=embed)

    @app_commands.command(name="stop", description="Stop playback, clear queue, and leave voice.")
    async def stop(self, interaction: discord.Interaction) -> None:
        """Stop audio, clear upcoming tracks, disconnect from voice, and reset presence."""
        player = self._player(interaction)
        _validate_voice_state(interaction, player)

        await player.stop()
        embed = EmbedFactory.success(
            title="Stopped",
            description="Playback stopped, queue cleared, and disconnected from voice.",
            context="Controls",
        )
        await send(interaction, embed=embed)

    @app_commands.command(name="queue", description="Display upcoming tracks in the queue.")
    @app_commands.describe(page="Page number to inspect")
    async def queue(self, interaction: discord.Interaction, page: int = 1) -> None:
        """Show interactive paginated queue."""
        player = self._player(interaction)
        avatar_url = str(self.bot.user.display_avatar.url) if self.bot.user else None

        paginator = QueuePaginator(
            queue=player.queue,
            author_id=interaction.user.id,
            initial_page=max(1, page),
            bot_avatar_url=avatar_url,
        )
        embed = paginator.build_embed()
        await send(interaction, embed=embed, view=paginator)

    @app_commands.command(
        name="nowplaying", description="Show details of the currently playing track."
    )
    async def nowplaying(self, interaction: discord.Interaction) -> None:
        """Display track progress, duration, artist, and requester."""
        player = self._player(interaction)
        avatar_url = str(self.bot.user.display_avatar.url) if self.bot.user else None

        if not player.current_track:
            embed = EmbedFactory.info(
                title="Nothing Playing",
                description="The player is currently idle.",
                context="Status",
                bot_avatar_url=avatar_url,
            )
            await send(interaction, embed=embed)
            return

        track = player.current_track
        embed = EmbedFactory.now_playing(
            title=track.title,
            artist=track.artist,
            duration_s=track.duration_s,
            position_s=player.elapsed_seconds,
            requester_mention=f"<@{track.requester_id}>",
            thumbnail_url=track.thumbnail_url,
            bot_avatar_url=avatar_url,
        )
        await send(interaction, embed=embed)

    @app_commands.command(name="remove", description="Remove a track from the queue by position.")
    @app_commands.describe(position="Position number in the queue (e.g. 1)")
    async def remove(self, interaction: discord.Interaction, position: int) -> None:
        """Remove a specific track from upcoming queue."""
        player = self._player(interaction)
        _validate_voice_state(interaction, player)

        try:
            removed = player.queue.remove(position)
            embed = EmbedFactory.success(
                title="Track Removed",
                description=f"Removed **#{position}**: {removed.title}",
                context="Queue",
            )
            await send(interaction, embed=embed)
        except IndexError as err:
            err_embed = EmbedFactory.error(
                message=str(err),
                user_hint="Check `/queue` to see valid track positions.",
            )
            await send(interaction, embed=err_embed, ephemeral=True)

    @app_commands.command(name="clear", description="Clear all upcoming tracks from the queue.")
    async def clear(self, interaction: discord.Interaction) -> None:
        """Clear upcoming queue."""
        player = self._player(interaction)
        _validate_voice_state(interaction, player)

        count = player.queue.clear()
        embed = EmbedFactory.success(
            title="Queue Cleared",
            description=f"Removed **{count}** upcoming tracks from the queue.",
            context="Queue",
        )
        await send(interaction, embed=embed)

    @app_commands.command(name="shuffle", description="Shuffle upcoming tracks in the queue.")
    async def shuffle(self, interaction: discord.Interaction) -> None:
        """Shuffle upcoming queue."""
        player = self._player(interaction)
        _validate_voice_state(interaction, player)

        if len(player.queue) < 2:
            embed = EmbedFactory.warning(
                title="Queue Too Short",
                description="Need at least 2 tracks in the queue to shuffle.",
                context="Queue",
            )
            await send(interaction, embed=embed, ephemeral=True)
            return

        player.queue.shuffle()
        embed = EmbedFactory.success(
            title="Queue Shuffled",
            description=f"Randomized **{len(player.queue)}** upcoming tracks.",
            context="Queue",
        )
        await send(interaction, embed=embed)

    @app_commands.command(name="loop", description="Configure loop mode.")
    @app_commands.describe(mode="Loop behavior: off, track, or queue")
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="off", value="off"),
            app_commands.Choice(name="track", value="track"),
            app_commands.Choice(name="queue", value="queue"),
        ]
    )
    async def loop(self, interaction: discord.Interaction, mode: app_commands.Choice[str]) -> None:
        """Set loop mode."""
        player = self._player(interaction)
        _validate_voice_state(interaction, player)

        player.queue.loop_mode = LoopMode(mode.value)
        embed = EmbedFactory.success(
            title="Loop Mode Updated",
            description=f"Loop mode set to **{mode.name}**.",
            context="Queue",
        )
        await send(interaction, embed=embed)

    @app_commands.command(name="volume", description="Adjust playback volume (0-100).")
    @app_commands.describe(level="Volume level from 0 to 100")
    async def volume(self, interaction: discord.Interaction, level: int) -> None:
        """Adjust volume level."""
        player = self._player(interaction)
        _validate_voice_state(interaction, player)

        actual_level = player.set_volume(level)
        embed = EmbedFactory.success(
            title="Volume Adjusted",
            description=f"Volume set to **{actual_level}%**.",
            context="Audio",
        )
        await send(interaction, embed=embed)


async def setup(bot: YokaiBot) -> None:
    """Extension setup entrypoint."""
    await bot.add_cog(MusicCog(bot))
