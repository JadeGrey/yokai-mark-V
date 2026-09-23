"""Typed domain errors for Yokai."""

from __future__ import annotations

from typing import Optional


class YokaiError(Exception):
    """Base exception for all domain errors in Yokai."""

    def __init__(self, message: str, user_hint: Optional[str] = None) -> None:
        super().__init__(message)
        self.message = message
        self.user_hint = user_hint


class NotFoundError(YokaiError):
    """Raised when a track, query, or playlist yielded no results or was deleted."""

    def __init__(
        self,
        message: str = "No matching track or video was found.",
        user_hint: Optional[str] = "Check the URL or try searching with artist and title.",
    ) -> None:
        super().__init__(message, user_hint=user_hint)


class LoginRequiredError(YokaiError):
    """Raised when media is age-restricted, private, or members-only."""

    def __init__(
        self,
        message: str = "This content requires a login (age-gated, private, or members-only).",
        user_hint: Optional[str] = "Try a public link or an official audio release.",
    ) -> None:
        super().__init__(message, user_hint=user_hint)


class UnavailableError(YokaiError):
    """Raised when media is region-blocked, taken down, or unavailable."""

    def __init__(
        self,
        message: str = "This track is unavailable or region-restricted.",
        user_hint: Optional[str] = "Try an alternate upload or search query.",
    ) -> None:
        super().__init__(message, user_hint=user_hint)


class BotCheckError(YokaiError):
    """Raised when YouTube issues a bot check, HTTP 403, or SABR format blockage."""

    def __init__(
        self,
        message: str = "YouTube blocked the audio extraction request (bot detection/403).",
        user_hint: Optional[str] = "The bot owner can check /diag or update yt-dlp.",
    ) -> None:
        super().__init__(message, user_hint=user_hint)


class ExtractionTimeoutError(YokaiError):
    """Raised when audio metadata or stream URL extraction timed out."""

    def __init__(
        self,
        message: str = "Extraction timed out while contacting the audio provider.",
        user_hint: Optional[str] = "Please try again in a few moments.",
    ) -> None:
        super().__init__(message, user_hint=user_hint)


class UnsupportedError(YokaiError):
    """Raised when a URL domain, media format, or link type is unsupported."""

    def __init__(
        self,
        message: str = "This link or content type is not supported.",
        user_hint: Optional[str] = "Yokai accepts YouTube and Spotify track/album/playlist links.",
    ) -> None:
        super().__init__(message, user_hint=user_hint)


class SpotifyResolutionError(YokaiError):
    """Raised when Spotify metadata resolution fails across all providers."""

    def __init__(
        self,
        message: str = "Could not resolve metadata for this Spotify link.",
        user_hint: Optional[
            str
        ] = "Try pasting a YouTube link or searching with song title and artist.",
    ) -> None:
        super().__init__(message, user_hint=user_hint)


class VoiceError(YokaiError):
    """Base exception for voice channel connection or playback errors."""


class VoicePermissionError(VoiceError):
    """Raised when the bot lacks necessary voice permissions (Connect or Speak)."""

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

    def __init__(
        self,
        message: str = "You must be in a voice channel to use music commands.",
        user_hint: Optional[str] = "Join a voice channel and try again.",
    ) -> None:
        super().__init__(message, user_hint=user_hint)
