"""Application entrypoint for running Yokai."""

from __future__ import annotations

import logging
import sys

from yokai.bot import YokaiBot
from yokai.config import ConfigError, load_config
from yokai.health import check_health
from yokai.log import setup_logging

logger = logging.getLogger("yokai")


def main() -> int:
    """Load configuration, set up logging, run health checks, and start the bot."""
    # 1. Load configuration
    try:
        config = load_config()
    except ConfigError as err:
        print(f"Configuration error: {err}", file=sys.stderr)
        return 1

    # 2. Configure logging and secret redaction
    secrets_to_redact = [config.discord_token]
    if config.spotify_client_secret:
        secrets_to_redact.append(config.spotify_client_secret)

    setup_logging(
        log_level=config.log_level,
        log_dir="logs",
        secrets=secrets_to_redact,
    )

    logger.info("Initializing Yokai Mark V...")

    # 3. Run startup health diagnostics
    health = check_health(
        ffmpeg_override=config.ffmpeg_path,
        deno_override=config.deno_path,
    )

    for warning in health.warnings:
        logger.warning("Startup notice: %s", warning)

    # 4. Instantiate bot
    bot = YokaiBot(config=config, health_report=health)

    # 5. Execute bot runner
    try:
        # Use log_handler=None to retain our custom logging configuration
        bot.run(config.discord_token, log_handler=None)
    except KeyboardInterrupt:
        logger.info("Termination signal received. Shutting down cleanly.")
    except Exception as exc:
        logger.critical("Fatal error running bot: %s", exc, exc_info=exc)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
