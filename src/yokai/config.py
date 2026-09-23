"""Configuration loading and validation for Yokai."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True, slots=True)
class Config:
    """Runtime configuration for Yokai."""

    # Required credentials & server IDs
    discord_token: str
    guild_id: int
    owner_id: int

    # Logging & Storage
    log_level: str = "INFO"
    db_path: Path = Path("data/yokai.db")

    # Spotify Provider Settings
    spotify_client_id: Optional[str] = None
    spotify_client_secret: Optional[str] = None
    spotify_scraper_enabled: bool = True

    # YouTube / yt-dlp Settings
    ytdlp_cookies_file: Optional[Path] = None
    ffmpeg_path: Optional[Path] = None
    deno_path: Optional[Path] = None

    # Playback & Queue Limits
    max_queue_size: int = 200
    max_playlist_tracks: int = 100
    max_track_seconds: int = 3600
    idle_disconnect_seconds: int = 300
    alone_disconnect_seconds: int = 120
    recommend_count: int = 8


def _parse_bool(value: str | None, default: bool = True) -> bool:
    if value is None:
        return default
    val = value.strip().lower()
    if val in ("true", "1", "yes", "on"):
        return True
    if val in ("false", "0", "no", "off"):
        return False
    return default


def _parse_int(name: str, value: str | None, default: int | None = None) -> int:
    if value is None or not value.strip():
        if default is not None:
            return default
        raise ConfigError(f"Missing required integer setting: {name}")
    try:
        return int(value.strip())
    except ValueError as err:
        raise ConfigError(f"Invalid integer for {name}: {value!r}") from err


def _parse_optional_path(value: str | None) -> Optional[Path]:
    if value and value.strip():
        return Path(value.strip())
    return None


def load_config(env_path: Path | str | None = None) -> Config:
    """Load configuration from environment variables, optionally reading an .env file.

    Raises:
        ConfigError: If required configuration variables are missing or malformed.
    """
    if env_path is not None:
        load_dotenv(dotenv_path=env_path)
    else:
        load_dotenv()

    # Required settings
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token:
        raise ConfigError("DISCORD_TOKEN is required. Please set it in your .env file.")

    guild_id_str = os.getenv("GUILD_ID")
    if not guild_id_str:
        raise ConfigError("GUILD_ID is required. Set your server ID in .env.")
    guild_id = _parse_int("GUILD_ID", guild_id_str)

    owner_id_str = os.getenv("OWNER_ID")
    if not owner_id_str:
        raise ConfigError("OWNER_ID is required. Set your user ID in .env.")
    owner_id = _parse_int("OWNER_ID", owner_id_str)

    # Optional paths and strings
    log_level = os.getenv("LOG_LEVEL", "INFO").strip().upper()
    db_path = Path(os.getenv("DB_PATH", "data/yokai.db").strip())

    spotify_id = os.getenv("SPOTIFY_CLIENT_ID", "").strip() or None
    spotify_secret = os.getenv("SPOTIFY_CLIENT_SECRET", "").strip() or None
    spotify_scraper = _parse_bool(os.getenv("SPOTIFY_SCRAPER_ENABLED"), default=True)

    ytdlp_cookies = _parse_optional_path(os.getenv("YTDLP_COOKIES_FILE"))
    ffmpeg_path = _parse_optional_path(os.getenv("FFMPEG_PATH"))
    deno_path = _parse_optional_path(os.getenv("DENO_PATH"))

    # Numeric limits
    max_queue_size = _parse_int("MAX_QUEUE_SIZE", os.getenv("MAX_QUEUE_SIZE"), 200)
    max_playlist_tracks = _parse_int("MAX_PLAYLIST_TRACKS", os.getenv("MAX_PLAYLIST_TRACKS"), 100)
    max_track_seconds = _parse_int("MAX_TRACK_SECONDS", os.getenv("MAX_TRACK_SECONDS"), 3600)
    idle_disconnect = _parse_int(
        "IDLE_DISCONNECT_SECONDS", os.getenv("IDLE_DISCONNECT_SECONDS"), 300
    )
    alone_disconnect = _parse_int(
        "ALONE_DISCONNECT_SECONDS", os.getenv("ALONE_DISCONNECT_SECONDS"), 120
    )
    recommend_count = _parse_int("RECOMMEND_COUNT", os.getenv("RECOMMEND_COUNT"), 8)

    return Config(
        discord_token=token,
        guild_id=guild_id,
        owner_id=owner_id,
        log_level=log_level,
        db_path=db_path,
        spotify_client_id=spotify_id,
        spotify_client_secret=spotify_secret,
        spotify_scraper_enabled=spotify_scraper,
        ytdlp_cookies_file=ytdlp_cookies,
        ffmpeg_path=ffmpeg_path,
        deno_path=deno_path,
        max_queue_size=max_queue_size,
        max_playlist_tracks=max_playlist_tracks,
        max_track_seconds=max_track_seconds,
        idle_disconnect_seconds=idle_disconnect,
        alone_disconnect_seconds=alone_disconnect,
        recommend_count=recommend_count,
    )
