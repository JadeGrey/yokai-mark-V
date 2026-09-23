"""Logging setup with secret redaction and rotating file output."""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Sequence

# Regex pattern matching common Discord bot tokens: [24-26 chars].[6 chars].[27-38 chars]
_DISCORD_TOKEN_RE = re.compile(r"([a-zA-Z0-9_-]{24,28}\.[a-zA-Z0-9_-]{6}\.[a-zA-Z0-9_-]{27,38})")


class RedactingFilter(logging.Filter):
    """Logging filter that scrubs sensitive tokens, secrets, and cookies."""

    def __init__(self, secrets: Sequence[str] | None = None) -> None:
        super().__init__()
        # Filter out empty or whitespace secrets
        self._secrets: list[str] = [s for s in (secrets or []) if s and len(s) >= 4]

    def add_secret(self, secret: str) -> None:
        if secret and len(secret) >= 4 and secret not in self._secrets:
            self._secrets.append(secret)

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self._redact(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: self._redact_obj(v) for k, v in record.args.items()}
            elif isinstance(record.args, (list, tuple)):
                record.args = tuple(self._redact_obj(v) for v in record.args)
        return True

    def _redact_obj(self, obj: object) -> object:
        if isinstance(obj, str):
            return self._redact(obj)
        return obj

    def _redact(self, text: str) -> str:
        # Redact specific secrets passed at init
        for secret in self._secrets:
            text = text.replace(secret, "[REDACTED]")
        # Redact token-like patterns
        text = _DISCORD_TOKEN_RE.sub("[REDACTED_TOKEN]", text)
        return text


def setup_logging(
    log_level: str = "INFO",
    log_dir: Path | str = "logs",
    secrets: Sequence[str] | None = None,
) -> RedactingFilter:
    """Configure root and application logging with console and rotating file handlers.

    Returns the RedactingFilter instance so additional secrets can be registered dynamically.
    """
    level = getattr(logging, log_level.upper(), logging.INFO)
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Clear existing handlers to avoid duplicate logs in test runs or reloads
    root_logger.handlers.clear()

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    redacting_filter = RedactingFilter(secrets=secrets)

    # 1. Console Handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(redacting_filter)
    root_logger.addHandler(console_handler)

    # 2. Rotating File Handler
    log_dir_path = Path(log_dir)
    log_dir_path.mkdir(parents=True, exist_ok=True)
    log_file = log_dir_path / "yokai.log"

    file_handler = RotatingFileHandler(
        filename=log_file,
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    file_handler.addFilter(redacting_filter)
    root_logger.addHandler(file_handler)

    # Reduce verbosity of noisy third-party libraries
    logging.getLogger("discord.gateway").setLevel(logging.WARNING)
    logging.getLogger("discord.client").setLevel(logging.INFO)
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)

    return redacting_filter
