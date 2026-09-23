"""Startup health and dependency diagnostics."""

from __future__ import annotations

import datetime
import importlib
import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class HealthReport:
    """Startup diagnostic report of external tools and libraries."""

    davey_available: bool = False
    davey_version: Optional[str] = None

    ffmpeg_found: bool = False
    ffmpeg_path: Optional[str] = None
    ffmpeg_version: Optional[str] = None

    deno_found: bool = False
    deno_path: Optional[str] = None
    deno_version: Optional[str] = None

    ytdlp_available: bool = False
    ytdlp_version: Optional[str] = None
    ytdlp_age_days: Optional[int] = None
    ytdlp_outdated: bool = False

    voice_capable: bool = False
    warnings: list[str] = field(default_factory=list)


def _check_binary(
    name: str, custom_path: Optional[Path], version_flag: str
) -> tuple[Optional[str], Optional[str]]:
    """Locate binary and extract its primary version string."""
    binary_path: Optional[str] = None
    if custom_path:
        if custom_path.is_file():
            binary_path = str(custom_path)
    else:
        binary_path = shutil.which(name)

    if not binary_path:
        return None, None

    try:
        proc = subprocess.run(
            [binary_path, version_flag],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        output = (proc.stdout or proc.stderr).strip().splitlines()
        version_str = output[0] if output else "unknown"
        return binary_path, version_str
    except Exception as exc:
        logger.warning("Failed to execute %s (%s): %s", name, binary_path, exc)
        return binary_path, None


def check_health(
    ffmpeg_override: Optional[Path] = None,
    deno_override: Optional[Path] = None,
) -> HealthReport:
    """Run all startup diagnostics and log findings."""
    report = HealthReport()

    # 1. Check davey (required for Discord DAVE E2EE voice)
    try:
        davey = importlib.import_module("davey")
        report.davey_available = True
        report.davey_version = getattr(davey, "__version__", "installed")
        logger.info("DAVE voice encryption: enabled (davey %s)", report.davey_version)
    except ImportError:
        report.davey_available = False
        msg = "davey is not installed. Voice calls will fail under Discord's DAVE E2EE protocol."
        report.warnings.append(msg)
        logger.warning(msg)

    # 2. Check ffmpeg
    ff_path, ff_ver = _check_binary("ffmpeg", ffmpeg_override, "-version")
    if ff_path:
        report.ffmpeg_found = True
        report.ffmpeg_path = ff_path
        report.ffmpeg_version = ff_ver
        logger.info("FFmpeg: found at %s (%s)", ff_path, ff_ver)
    else:
        msg = "FFmpeg was not found on PATH or custom path. Audio playback will be disabled."
        report.warnings.append(msg)
        logger.warning(msg)

    # 3. Check deno (for yt-dlp JS execution / EJS)
    deno_path, deno_ver = _check_binary("deno", deno_override, "--version")
    if deno_path:
        report.deno_found = True
        report.deno_path = deno_path
        report.deno_version = deno_ver
        logger.info("Deno: found at %s (%s)", deno_path, deno_ver)
    else:
        msg = "Deno was not found on PATH. YouTube extraction may fail on ciphered formats."
        report.warnings.append(msg)
        logger.warning(msg)

    # 4. Check yt-dlp
    try:
        import yt_dlp.version

        ver = yt_dlp.version.__version__
        report.ytdlp_available = True
        report.ytdlp_version = ver

        # Parse release age from YYYY.MM.DD
        parts = ver.split(".")[:3]
        if len(parts) >= 3 and all(p.isdigit() for p in parts):
            release_date = datetime.date(int(parts[0]), int(parts[1]), int(parts[2]))
            today = datetime.datetime.now(datetime.timezone.utc).date()
            age_days = (today - release_date).days
            report.ytdlp_age_days = age_days
            if age_days > 30:
                report.ytdlp_outdated = True
                msg = f"yt-dlp is {age_days} days old ({ver}). Consider updating via /diag or pip."
                report.warnings.append(msg)
                logger.warning(msg)
            else:
                logger.info("yt-dlp: up to date (%s, %d days old)", ver, age_days)
        else:
            logger.info("yt-dlp: version %s", ver)
    except Exception as exc:
        msg = f"Failed to inspect yt-dlp: {exc}"
        report.warnings.append(msg)
        logger.warning(msg)

    # Determine voice capability: both davey and ffmpeg are required
    report.voice_capable = report.davey_available and report.ffmpeg_found
    if not report.voice_capable:
        logger.warning("Voice playback is DISABLED due to missing dependencies.")

    return report
