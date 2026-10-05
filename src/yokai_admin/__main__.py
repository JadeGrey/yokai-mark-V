"""Run the admin panel: `python -m yokai_admin`."""

from __future__ import annotations

import logging
import sys

from yokai_admin.core.settings import load_settings
from yokai_admin.errors import PanelConfigError, PanelDbError
from yokai_admin.launch import preflight, run

logger = logging.getLogger("yokai_admin")


def main() -> int:
    """Load settings, check the database, then serve the panel on 127.0.0.1."""
    # Console only, on purpose: the single-use launch link is printed here and must not be
    # written to a log file.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    try:
        settings = load_settings()
        preflight(settings)
    except (PanelConfigError, PanelDbError) as exc:
        logger.error("%s", exc)
        return 1
    return run(settings)


if __name__ == "__main__":
    sys.exit(main())
