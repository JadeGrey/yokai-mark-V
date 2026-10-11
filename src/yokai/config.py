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
    log_dir: Path = Path("logs")

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

    # Audio pre-buffer (0 MB disables it)
    audio_buffer_mb: int = 500
    audio_buffer_dir: Path = Path("data/cache")

    # Economy: Renown wallet, earning, and rate limits
    economy_daily_base: int = 100
    economy_welcome_bonus: int = 200
    economy_voice_interval_seconds: int = 300
    economy_voice_reward: int = 5
    economy_command_reward: int = 5
    economy_command_rate_limit: int = 5

    # Gambling: stake limits
    gambling_min_bet: int = 10
    gambling_max_bet: int = 5000

    # Gambling: multiplayer tables
    gambling_duel_rake_percent: int = 2
    gambling_lobby_join_seconds: int = 60
    gambling_turn_seconds: int = 45
    gambling_jackpot_rake_percent: int = 2
    gambling_crash_max_multiplier: int = 100
    gambling_poker_max_hands: int = 30

    # Lottery
    lottery_ticket_price: int = 100
    lottery_number_range: int = 20
    lottery_max_tickets_per_user: int = 10
    lottery_draw_interval_hours: int = 168


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
    log_dir = Path(os.getenv("LOG_DIR", "logs").strip() or "logs")

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
    audio_buffer_mb = max(0, _parse_int("AUDIO_BUFFER_MB", os.getenv("AUDIO_BUFFER_MB"), 500))
    audio_buffer_dir = Path(os.getenv("AUDIO_BUFFER_DIR", "data/cache").strip() or "data/cache")

    # Economy: Renown wallet, earning, and rate limits
    economy_daily_base = _parse_int("ECONOMY_DAILY_BASE", os.getenv("ECONOMY_DAILY_BASE"), 100)
    economy_welcome_bonus = max(
        0,
        _parse_int("ECONOMY_WELCOME_BONUS", os.getenv("ECONOMY_WELCOME_BONUS"), 200),
    )
    # Floored at 30s: 0 (or a very small value) would make the voice tracker busy-loop.
    economy_voice_interval_seconds = max(
        30,
        _parse_int(
            "ECONOMY_VOICE_INTERVAL_SECONDS", os.getenv("ECONOMY_VOICE_INTERVAL_SECONDS"), 300
        ),
    )
    # 0 is a valid "feature disabled" value; callers must skip crediting rather than call
    # credit_wallet with a non-positive amount (which raises ValueError).
    economy_voice_reward = max(
        0, _parse_int("ECONOMY_VOICE_REWARD", os.getenv("ECONOMY_VOICE_REWARD"), 5)
    )
    economy_command_reward = max(
        0, _parse_int("ECONOMY_COMMAND_REWARD", os.getenv("ECONOMY_COMMAND_REWARD"), 5)
    )
    economy_command_rate_limit = _parse_int(
        "ECONOMY_COMMAND_RATE_LIMIT", os.getenv("ECONOMY_COMMAND_RATE_LIMIT"), 5
    )

    # Gambling: stake limits (a bet of 0 or less is never valid, so the floor is 1)
    gambling_min_bet = max(1, _parse_int("GAMBLING_MIN_BET", os.getenv("GAMBLING_MIN_BET"), 10))
    gambling_max_bet = _parse_int("GAMBLING_MAX_BET", os.getenv("GAMBLING_MAX_BET"), 5000)
    if gambling_max_bet < gambling_min_bet:
        raise ConfigError("GAMBLING_MAX_BET must be greater than or equal to GAMBLING_MIN_BET.")

    gambling_duel_rake_percent = min(
        50,
        max(
            0,
            _parse_int("GAMBLING_DUEL_RAKE_PERCENT", os.getenv("GAMBLING_DUEL_RAKE_PERCENT"), 2),
        ),
    )
    gambling_lobby_join_seconds = max(
        5,
        _parse_int("GAMBLING_LOBBY_JOIN_SECONDS", os.getenv("GAMBLING_LOBBY_JOIN_SECONDS"), 60),
    )
    gambling_turn_seconds = max(
        5, _parse_int("GAMBLING_TURN_SECONDS", os.getenv("GAMBLING_TURN_SECONDS"), 45)
    )
    gambling_jackpot_rake_percent = min(
        50,
        max(
            0,
            _parse_int(
                "GAMBLING_JACKPOT_RAKE_PERCENT", os.getenv("GAMBLING_JACKPOT_RAKE_PERCENT"), 2
            ),
        ),
    )
    gambling_crash_max_multiplier = max(
        2,
        _parse_int(
            "GAMBLING_CRASH_MAX_MULTIPLIER", os.getenv("GAMBLING_CRASH_MAX_MULTIPLIER"), 100
        ),
    )
    gambling_poker_max_hands = max(
        1, _parse_int("GAMBLING_POKER_MAX_HANDS", os.getenv("GAMBLING_POKER_MAX_HANDS"), 30)
    )
    lottery_ticket_price = max(
        1, _parse_int("LOTTERY_TICKET_PRICE", os.getenv("LOTTERY_TICKET_PRICE"), 100)
    )
    lottery_number_range = min(
        100, max(2, _parse_int("LOTTERY_NUMBER_RANGE", os.getenv("LOTTERY_NUMBER_RANGE"), 20))
    )
    lottery_max_tickets_per_user = max(
        1,
        _parse_int("LOTTERY_MAX_TICKETS_PER_USER", os.getenv("LOTTERY_MAX_TICKETS_PER_USER"), 10),
    )
    lottery_draw_interval_hours = max(
        1,
        _parse_int("LOTTERY_DRAW_INTERVAL_HOURS", os.getenv("LOTTERY_DRAW_INTERVAL_HOURS"), 168),
    )

    return Config(
        discord_token=token,
        guild_id=guild_id,
        owner_id=owner_id,
        log_level=log_level,
        db_path=db_path,
        log_dir=log_dir,
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
        audio_buffer_mb=audio_buffer_mb,
        audio_buffer_dir=audio_buffer_dir,
        economy_daily_base=economy_daily_base,
        economy_welcome_bonus=economy_welcome_bonus,
        economy_voice_interval_seconds=economy_voice_interval_seconds,
        economy_voice_reward=economy_voice_reward,
        economy_command_reward=economy_command_reward,
        economy_command_rate_limit=economy_command_rate_limit,
        gambling_min_bet=gambling_min_bet,
        gambling_max_bet=gambling_max_bet,
        gambling_duel_rake_percent=gambling_duel_rake_percent,
        gambling_lobby_join_seconds=gambling_lobby_join_seconds,
        gambling_turn_seconds=gambling_turn_seconds,
        gambling_jackpot_rake_percent=gambling_jackpot_rake_percent,
        gambling_crash_max_multiplier=gambling_crash_max_multiplier,
        gambling_poker_max_hands=gambling_poker_max_hands,
        lottery_ticket_price=lottery_ticket_price,
        lottery_number_range=lottery_number_range,
        lottery_max_tickets_per_user=lottery_max_tickets_per_user,
        lottery_draw_interval_hours=lottery_draw_interval_hours,
    )
