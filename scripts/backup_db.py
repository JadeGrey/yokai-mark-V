"""Make a verified backup of the bot's database, prune old ones, optionally copy it elsewhere.

Meant for a nightly timer (see deploy/linux/). It reuses the admin panel's backup code, so the
copy is made with SQLite's online backup API (safe while the bot runs) and is integrity-checked
before it is kept. Backups land in BACKUP_DIR (default: a `backups` folder beside the database).

    python scripts/backup_db.py                      # back up, keep the newest 14
    python scripts/backup_db.py --keep 30
    python scripts/backup_db.py --copy-to /mnt/usb/yokai-backups
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
import time
from pathlib import Path
from typing import Optional, Sequence

from yokai_admin.core.settings import load_settings
from yokai_admin.errors import PanelConfigError, PanelDbError
from yokai_admin.modules.database.backups import list_backups, make_backup

logger = logging.getLogger("yokai.backup")

DEFAULT_KEEP = 14


def prune(backup_dir: Path, keep: int) -> list[Path]:
    """Delete all but the newest `keep` regular backups. Safety copies are never touched."""
    regular = [b for b in list_backups(backup_dir) if not b.safety]  # newest first
    removed: list[Path] = []
    for old in regular[keep:]:
        old.path.unlink(missing_ok=True)
        removed.append(old.path)
    return removed


def run(
    *,
    keep: int = DEFAULT_KEEP,
    copy_to: Optional[Path] = None,
    env_path: Optional[Path] = None,
    now: Optional[int] = None,
) -> Path:
    """Back up, prune and (optionally) copy. Returns the new backup's path."""
    if keep < 1:
        raise ValueError("--keep must be at least 1.")
    settings = load_settings(env_path)
    if not settings.db_path.exists():
        raise PanelDbError(f"The database was not found at {settings.db_path}.")
    backup_dir = settings.effective_backup_dir
    made = make_backup(settings.db_path, backup_dir, now=int(time.time()) if now is None else now)
    logger.info("Backed up %s to %s (%d bytes).", settings.db_path, made.path, made.size)
    for gone in prune(backup_dir, keep):
        logger.info("Removed old backup %s.", gone.name)
    if copy_to is not None:
        copy_to.mkdir(parents=True, exist_ok=True)
        target = copy_to / made.name
        shutil.copy2(made.path, target)
        logger.info("Copied the backup to %s.", target)
    return made.path


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Back up the Yokai database.")
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP, help="how many to keep")
    parser.add_argument("--copy-to", type=Path, help="also copy the new backup into this folder")
    parser.add_argument("--env", type=Path, help="path to the .env file (default: ./.env)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        run(keep=args.keep, copy_to=args.copy_to, env_path=args.env)
    except (PanelConfigError, PanelDbError, ValueError, OSError) as exc:
        logger.error("Backup failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
