"""Input classification, URL sanitization, and security allowlist enforcement."""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import parse_qs, urlparse

# Strict security allowlist of hostnames
ALLOWED_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "open.spotify.com",
    "spotify.link",
}

# Regex to detect general URLs
_URL_REGEX = re.compile(r"^https?://", re.IGNORECASE)
# Spotify URI pattern: spotify:track:..., spotify:album:..., spotify:playlist:...
_SPOTIFY_URI_REGEX = re.compile(
    r"^spotify:(track|album|playlist|artist|show|episode):([a-zA-Z0-9]+)$"
)


class InputKind(enum.Enum):
    """Classified category of user input."""

    YOUTUBE_TRACK = "youtube_track"
    YOUTUBE_PLAYLIST = "youtube_playlist"
    SPOTIFY_TRACK = "spotify_track"
    SPOTIFY_ALBUM = "spotify_album"
    SPOTIFY_PLAYLIST = "spotify_playlist"
    SEARCH_QUERY = "search_query"
    UNSUPPORTED = "unsupported"


@dataclass(slots=True)
class ClassifiedInput:
    """Result of classifying user input."""

    kind: InputKind
    raw_input: str
    clean_target: str
    extracted_id: Optional[str] = None
    error_message: Optional[str] = None
    user_hint: Optional[str] = None


def classify_input(raw: str) -> ClassifiedInput:
    """Classify free text or URL input according to strict security and domain rules."""
    text = raw.strip()
    if not text:
        return ClassifiedInput(
            kind=InputKind.UNSUPPORTED,
            raw_input=raw,
            clean_target="",
            error_message="Query cannot be empty.",
            user_hint="Provide a search query or a YouTube/Spotify link.",
        )

    # 1. Check for Spotify URI (spotify:track:...)
    uri_match = _SPOTIFY_URI_REGEX.match(text)
    if uri_match:
        entity_type, entity_id = uri_match.group(1), uri_match.group(2)
        if entity_type == "track":
            return ClassifiedInput(InputKind.SPOTIFY_TRACK, text, text, extracted_id=entity_id)
        if entity_type == "album":
            return ClassifiedInput(InputKind.SPOTIFY_ALBUM, text, text, extracted_id=entity_id)
        if entity_type == "playlist":
            return ClassifiedInput(InputKind.SPOTIFY_PLAYLIST, text, text, extracted_id=entity_id)
        return ClassifiedInput(
            kind=InputKind.UNSUPPORTED,
            raw_input=text,
            clean_target=text,
            error_message=f"Spotify {entity_type} links are not supported.",
            user_hint="Yokai accepts Spotify track, album, and playlist links.",
        )

    # 2. Check if input looks like a URL
    if not _URL_REGEX.match(text):
        # Plain text search query, capped to 200 chars
        clean_query = text[:200].strip()
        return ClassifiedInput(
            kind=InputKind.SEARCH_QUERY,
            raw_input=text,
            clean_target=clean_query,
        )

    # Parse URL
    try:
        parsed = urlparse(text)
    except Exception:
        return ClassifiedInput(
            kind=InputKind.UNSUPPORTED,
            raw_input=text,
            clean_target=text,
            error_message="Invalid URL structure.",
            user_hint="Please provide a valid YouTube or Spotify link.",
        )

    hostname = (parsed.hostname or "").lower()
    # Check security allowlist
    if hostname not in ALLOWED_HOSTS:
        return ClassifiedInput(
            kind=InputKind.UNSUPPORTED,
            raw_input=text,
            clean_target=text,
            error_message=f"Links from '{hostname}' are not permitted.",
            user_hint="Yokai only accepts links from YouTube and Spotify.",
        )

    path = parsed.path
    query = parse_qs(parsed.query)

    # 3. Spotify URLs
    if hostname in ("open.spotify.com", "spotify.link"):
        if hostname == "spotify.link":
            # Will be resolved via HTTP redirect in Phase 3
            return ClassifiedInput(
                kind=InputKind.SPOTIFY_TRACK,
                raw_input=text,
                clean_target=text,
            )

        # Normalize path by removing optional locale prefixes like /intl-xx/
        clean_path = re.sub(r"^/intl-[a-z]{2}/", "/", path)

        parts = [p for p in clean_path.split("/") if p]
        if len(parts) >= 2:
            stype, sid = parts[0], parts[1]
            if stype == "track":
                return ClassifiedInput(InputKind.SPOTIFY_TRACK, text, text, extracted_id=sid)
            if stype == "album":
                return ClassifiedInput(InputKind.SPOTIFY_ALBUM, text, text, extracted_id=sid)
            if stype == "playlist":
                return ClassifiedInput(InputKind.SPOTIFY_PLAYLIST, text, text, extracted_id=sid)
            if stype in ("artist", "show", "episode"):
                return ClassifiedInput(
                    kind=InputKind.UNSUPPORTED,
                    raw_input=text,
                    clean_target=text,
                    error_message=f"Spotify {stype} links are not supported.",
                    user_hint="Yokai accepts Spotify track, album, and playlist links.",
                )

        return ClassifiedInput(
            kind=InputKind.UNSUPPORTED,
            raw_input=text,
            clean_target=text,
            error_message="Unsupported Spotify URL format.",
            user_hint="Please paste a track, album, or playlist URL.",
        )

    # 4. YouTube URLs
    # 4a. Shortened youtu.be/<video_id>
    if hostname == "youtu.be":
        video_id = path.lstrip("/").split("/")[0]
        if video_id:
            clean_url = f"https://www.youtube.com/watch?v={video_id}"
            return ClassifiedInput(
                kind=InputKind.YOUTUBE_TRACK,
                raw_input=text,
                clean_target=clean_url,
                extracted_id=video_id,
            )

    # 4b. Explicit Playlist URL: /playlist?list=...
    if path == "/playlist" or "/playlist" in path:
        list_id = query.get("list", [None])[0]
        if not list_id:
            return ClassifiedInput(
                kind=InputKind.UNSUPPORTED,
                raw_input=text,
                clean_target=text,
                error_message="Playlist URL is missing the 'list' parameter.",
                user_hint="Please provide a valid playlist link.",
            )
        if list_id.startswith("RD"):
            return ClassifiedInput(
                kind=InputKind.UNSUPPORTED,
                raw_input=text,
                clean_target=text,
                error_message="Auto-generated YouTube mix playlists (RD...) are not supported.",
                user_hint="Please provide a direct song link or a user-created playlist.",
            )
        clean_url = f"https://www.youtube.com/playlist?list={list_id}"
        return ClassifiedInput(
            kind=InputKind.YOUTUBE_PLAYLIST,
            raw_input=text,
            clean_target=clean_url,
            extracted_id=list_id,
        )

    # 4c. Shorts URL: /shorts/<video_id>
    if "/shorts/" in path:
        parts = path.split("/shorts/")
        if len(parts) >= 2:
            video_id = parts[1].split("/")[0].split("?")[0]
            if video_id:
                clean_url = f"https://www.youtube.com/watch?v={video_id}"
                return ClassifiedInput(
                    kind=InputKind.YOUTUBE_TRACK,
                    raw_input=text,
                    clean_target=clean_url,
                    extracted_id=video_id,
                )

    # 4d. Watch URL: /watch?v=... (ignore attached list= unless it's a playlist endpoint)
    if "v" in query:
        video_id = query["v"][0]
        clean_url = f"https://www.youtube.com/watch?v={video_id}"
        return ClassifiedInput(
            kind=InputKind.YOUTUBE_TRACK,
            raw_input=text,
            clean_target=clean_url,
            extracted_id=video_id,
        )

    return ClassifiedInput(
        kind=InputKind.UNSUPPORTED,
        raw_input=text,
        clean_target=text,
        error_message="Could not recognize a valid YouTube video or playlist from this URL.",
        user_hint="Check the link or try searching with artist and title.",
    )
