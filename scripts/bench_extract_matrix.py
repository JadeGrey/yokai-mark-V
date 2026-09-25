"""Find the fastest yt-dlp option profile that still yields playable audio.

Measurements on the owner's PC showed ~1.26 s of a 1.4 s stream extraction is yt-dlp
downloading the YouTube watch page. This script times every player client, with and
without `player_skip=webpage`, and checks each resulting URL actually plays in FFmpeg.
It sends about 50 requests to YouTube, so run it sparingly. Run from the repo root
(PowerShell):

    .\\.venv\\Scripts\\python.exe scripts\\bench_extract_matrix.py --runs 2
    .\\.venv\\Scripts\\python.exe scripts\\bench_extract_matrix.py --breakdown
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

import yt_dlp

from yokai.health import check_health

VIDEO_ID = "lYBUbBu4W08"


def out(text: str = "") -> None:
    sys.stdout.write(text + "\n")


def client_names() -> list[str]:
    try:
        from yt_dlp.extractor.youtube import _base

        return sorted(_base.INNERTUBE_CLIENTS)
    except (ImportError, AttributeError):
        return [
            "android_vr",
            "ios",
            "mweb",
            "tv",
            "web",
            "web_safari",
            "web_music",
        ]


def profile_args(client: Optional[str], skip: Optional[list[str]]) -> Optional[dict[str, Any]]:
    youtube: dict[str, list[str]] = {}
    if client:
        youtube["player_client"] = [client]
    if skip:
        youtube["player_skip"] = skip
    return {"youtube": youtube} if youtube else None


def base_opts(deno_path: Optional[str]) -> dict[str, Any]:
    return {
        "format": "bestaudio/best",
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 15,
        "noplaylist": True,
        "js_runtimes": {"deno": {"path": deno_path} if deno_path else {}},
    }


def extract_once(
    deno_path: Optional[str], extractor_args: Optional[dict[str, Any]]
) -> tuple[float, dict[str, Any]]:
    opts = base_opts(deno_path)
    if extractor_args:
        opts["extractor_args"] = extractor_args
    started = time.perf_counter()
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={VIDEO_ID}", download=False)
    return (time.perf_counter() - started) * 1000.0, info or {}


def plays_without_headers(url: str, ffmpeg: str) -> bool:
    """True if FFmpeg can read 2 s of audio from the URL with no special headers."""
    try:
        proc = subprocess.run(
            [
                ffmpeg,
                "-nostdin",
                "-loglevel",
                "error",
                "-t",
                "2",
                "-i",
                url,
                "-vn",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            timeout=25,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def evaluate(
    label: str,
    extractor_args: Optional[dict[str, Any]],
    runs: int,
    deno_path: Optional[str],
    ffmpeg: str,
    pause: float,
) -> dict[str, Any]:
    times: list[float] = []
    info: dict[str, Any] = {}
    error: Optional[str] = None
    for _ in range(runs):
        try:
            elapsed, result = extract_once(deno_path, extractor_args)
        except Exception as exc:  # harness: record and continue
            error = (str(exc).splitlines() or [type(exc).__name__])[0][:120]
            continue
        if result.get("url"):
            times.append(elapsed)
            info = result
        else:
            error = "no direct url"
        time.sleep(pause)
    plays = bool(times) and plays_without_headers(str(info["url"]), ffmpeg)
    vcodec = info.get("vcodec")
    return {
        "profile": label,
        "extractor_args": extractor_args,
        "ok_runs": len(times),
        "runs": runs,
        "median_ms": statistics.median(times) if times else None,
        "format_id": info.get("format_id"),
        "ext": info.get("ext"),
        "acodec": info.get("acodec"),
        "audio_only": vcodec in (None, "none") if info else False,
        "plays_without_headers": plays,
        "error": error,
    }


def breakdown(deno_path: Optional[str]) -> None:
    """Print timestamped yt-dlp phases for one default extraction."""
    lines: list[tuple[float, str]] = []
    started = time.perf_counter()

    class Logger:
        def debug(self, message: str) -> None:
            lines.append((time.perf_counter() - started, message[:110]))

        def info(self, message: str) -> None:
            return None

        warning = error = info

    opts = base_opts(deno_path)
    opts["logger"] = Logger()
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.extract_info(f"https://www.youtube.com/watch?v={VIDEO_ID}", download=False)
    for stamp, message in lines:
        out(f"{stamp:6.2f}s  {message}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument("--pause", type=float, default=1.0)
    parser.add_argument("--breakdown", action="store_true")
    parser.add_argument("--out", default="docs/perf/extract-matrix.json")
    args = parser.parse_args()

    health = check_health()
    if not health.ffmpeg_found or not health.ffmpeg_path:
        out("FFmpeg not found.")
        return 2

    if args.breakdown:
        breakdown(health.deno_path)
        return 0

    profiles: list[tuple[str, Optional[dict[str, Any]]]] = [("default", None)]
    for client in client_names():
        profiles.append((f"client={client}", profile_args(client, None)))
        profiles.append((f"client={client}+skip_webpage", profile_args(client, ["webpage"])))
    profiles.append(("default+skip_webpage", profile_args(None, ["webpage"])))

    rows: list[dict[str, Any]] = []
    for label, extractor_args in profiles:
        row = evaluate(
            label, extractor_args, args.runs, health.deno_path, health.ffmpeg_path, args.pause
        )
        rows.append(row)
        median = f"{row['median_ms']:.0f}ms" if row["median_ms"] is not None else "-"
        out(f"{label:<34}{median:>9}  plays={row['plays_without_headers']}  {row['error'] or ''}")

    usable = [r for r in rows if r["plays_without_headers"] and r["median_ms"] is not None]
    usable.sort(key=lambda r: r["median_ms"])
    out("\nFastest usable profiles (extraction succeeded AND FFmpeg can play the URL):")
    for row in usable[:5]:
        kind = "audio-only" if row["audio_only"] else "muxed"
        out(f"  {row['profile']:<34}{row['median_ms']:>7.0f}ms  fmt={row['format_id']} ({kind})")

    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    out(f"wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
