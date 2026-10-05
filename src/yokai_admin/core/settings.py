"""Panel settings: read from the same .env as the bot, but never needs Discord secrets."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from yokai_admin.errors import PanelConfigError

HOST = "127.0.0.1"  # loopback only; deliberately not configurable
DEFAULT_PORT = 8765


@dataclass(frozen=True, slots=True)
class PanelSettings:
    """Where the panel finds the bot's data and which local port it listens on."""

    db_path: Path
    log_dir: Path
    port: int = DEFAULT_PORT


def load_settings(env_path: Path | str | None = None) -> PanelSettings:
    """Load panel settings from the environment, optionally reading an .env file first."""
    if env_path is not None:
        load_dotenv(dotenv_path=env_path)
    else:
        load_dotenv()

    db_path = Path(os.getenv("DB_PATH", "data/yokai.db").strip() or "data/yokai.db")
    log_dir = Path(os.getenv("LOG_DIR", "logs").strip() or "logs")

    raw_port = os.getenv("ADMIN_PORT", str(DEFAULT_PORT)).strip() or str(DEFAULT_PORT)
    try:
        port = int(raw_port)
    except ValueError as err:
        raise PanelConfigError(f"ADMIN_PORT must be a whole number, got {raw_port!r}.") from err
    if not 1024 <= port <= 65535:
        raise PanelConfigError(f"ADMIN_PORT must be between 1024 and 65535, got {port}.")

    return PanelSettings(db_path=db_path, log_dir=log_dir, port=port)
