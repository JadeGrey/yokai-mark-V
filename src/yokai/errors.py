"""Typed domain errors for Yokai."""

from __future__ import annotations

from typing import Optional

from yokai.textutil import escape_md


class YokaiError(Exception):
    """Base exception for all domain errors in Yokai."""

    title: str = "That didn't work"

    def __init__(self, message: str, user_hint: Optional[str] = None) -> None:
        super().__init__(message)
        self.message = message
        self.user_hint = user_hint


class NotFoundError(YokaiError):
    """Raised when a track, query, or playlist yielded no results or was deleted."""

    title = "Nothing found"

    def __init__(
        self,
        message: str = "No matching track or video was found.",
        user_hint: Optional[str] = "Check the URL or try searching with artist and title.",
    ) -> None:
        super().__init__(message, user_hint=user_hint)


class LoginRequiredError(YokaiError):
    """Raised when media is age-restricted, private, or members-only."""

    title = "Login required"

    def __init__(
        self,
        message: str = "This content requires a login (age-gated, private, or members-only).",
        user_hint: Optional[str] = "Try a public link or an official audio release.",
    ) -> None:
        super().__init__(message, user_hint=user_hint)


class UnavailableError(YokaiError):
    """Raised when media is region-blocked, taken down, or unavailable."""

    title = "Track unavailable"

    def __init__(
        self,
        message: str = "This track is unavailable or region-restricted.",
        user_hint: Optional[str] = "Try an alternate upload or search query.",
    ) -> None:
        super().__init__(message, user_hint=user_hint)


class BotCheckError(YokaiError):
    """Raised when YouTube issues a bot check, HTTP 403, or SABR format blockage."""

    title = "YouTube blocked the request"

    def __init__(
        self,
        message: str = "YouTube blocked the audio extraction request (bot detection/403).",
        user_hint: Optional[str] = "The bot owner can check /diag or update yt-dlp.",
    ) -> None:
        super().__init__(message, user_hint=user_hint)


class ExtractionTimeoutError(YokaiError):
    """Raised when audio metadata or stream URL extraction timed out."""

    title = "Took too long"

    def __init__(
        self,
        message: str = "Extraction timed out while contacting the audio provider.",
        user_hint: Optional[str] = "Please try again in a few moments.",
    ) -> None:
        super().__init__(message, user_hint=user_hint)


class UnsupportedError(YokaiError):
    """Raised when a URL domain, media format, or link type is unsupported."""

    title = "Not supported"

    def __init__(
        self,
        message: str = "This link or content type is not supported.",
        user_hint: Optional[str] = "Yokai accepts YouTube and Spotify track/album/playlist links.",
    ) -> None:
        super().__init__(message, user_hint=user_hint)


class SpotifyResolutionError(YokaiError):
    """Raised when Spotify metadata resolution fails across all providers."""

    title = "Spotify lookup failed"

    def __init__(
        self,
        message: str = "Could not resolve metadata for this Spotify link.",
        user_hint: Optional[
            str
        ] = "Try pasting a YouTube link or searching with song title and artist.",
    ) -> None:
        super().__init__(message, user_hint=user_hint)


class UnmatchedTrackError(YokaiError):
    """Raised when an imported Spotify track has no confident YouTube match."""

    title = "No match found"

    def __init__(self, title: str) -> None:
        super().__init__(
            f"Could not find a confident YouTube match for **{escape_md(title[:120])}**.",
            user_hint="Try searching with the song title and artist, or paste a YouTube link.",
        )


class VoiceError(YokaiError):
    """Base exception for voice channel connection or playback errors."""

    title = "Voice problem"


class VoicePermissionError(VoiceError):
    """Raised when the bot lacks necessary voice permissions (Connect or Speak)."""

    title = "Missing permission"

    def __init__(
        self,
        missing_permission: str,
        channel_name: str,
    ) -> None:
        msg = f"Missing permission '{missing_permission}' in voice channel #{channel_name}."
        hint = f"Please grant Yokai '{missing_permission}' permission in that channel's settings."
        super().__init__(msg, user_hint=hint)
        self.missing_permission = missing_permission
        self.channel_name = channel_name


class VoiceChannelError(VoiceError):
    """Raised when voice state rules are violated (e.g. caller not in VC)."""

    title = "Voice channel needed"

    def __init__(
        self,
        message: str = "You must be in a voice channel to use music commands.",
        user_hint: Optional[str] = "Join a voice channel and try again.",
    ) -> None:
        super().__init__(message, user_hint=user_hint)
