"""Live latency harness for the Yokai music pipeline.

Hits real YouTube, so it is NOT part of pytest. Run from the repo root (PowerShell):

    .\\.venv\\Scripts\\python.exe scripts\\bench_music.py --runs 5 --label baseline

Scenarios (each reports p50 and p95 in milliseconds):
    search      free-text query -> Track
    stream      Track -> stream URL (fresh resolver, so nothing is cached)
    ffmpeg      stream URL -> first 20 ms PCM frame out of FFmpeg
    cold_play   the three above in sequence (what /play feels like on a cold start)
    warm_play   the same query and track again on the same resolver (search and stream cached)
    buffer_download / buffered_first_frame
                stream URL -> file in a temp AudioBuffer, then file -> first PCM frame
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import discord
import yt_dlp.version

from yokai.health import check_health
from yokai.music.buffer import AudioBuffer
from yokai.music.models import Track
from yokai.music.resolvers.ytdlp import YtDlpResolver
from yokai.music.search import YtMusicSearcher
from yokai.music.timing import percentile

QUERIES = [
    "never gonna give you up",
    "daft punk one more time",
    "queen bohemian rhapsody",
    "adele hello",
    "nirvana smells like teen spirit",
    "dua lipa levitating",
    "the weeknd blinding lights",
]
COLD_PLAY_TARGET_MS = 1500.0
FFMPEG_BEFORE = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"


def out(text: str = "") -> None:
    sys.stdout.write(text + "\n")


def build_resolver(deno_path: Optional[str], searcher: YtMusicSearcher) -> YtDlpResolver:
    """Fresh resolver so no stream cache carries over between runs (same wiring as bot.py).

    The searcher is shared and warmed once, like the bot does in setup_hook, so ytmusicapi's slow
    first call is not counted in every cold play.
    """
    return YtDlpResolver(deno_path=Path(deno_path) if deno_path else None, searcher=searcher)


async def first_pcm_frame_ms(source_arg: str, ffmpeg: str, is_file: bool = False) -> float:
    """Time from spawning FFmpeg to receiving the first 20 ms PCM frame."""
    started = time.perf_counter()
    source = discord.FFmpegPCMAudio(
        source_arg,
        executable=ffmpeg,
        before_options=None if is_file else FFMPEG_BEFORE,
        options="-vn",
    )
    try:
        frame = await asyncio.to_thread(source.read)
    finally:
        source.cleanup()
    if not frame:
        raise RuntimeError("FFmpeg produced no audio")
    return (time.perf_counter() - started) * 1000.0


async def buffered_once(resolver: YtDlpResolver, track: Track, ffmpeg: str) -> dict[str, float]:
    """Download a track into a temp AudioBuffer, then time FFmpeg's first frame from the file."""
    stream = await resolver.get_stream(track)
    with tempfile.TemporaryDirectory() as tmp:
        buffer = AudioBuffer(Path(tmp) / "cache", 500 * 1024 * 1024, ffmpeg=ffmpeg)
        await buffer.start()
        started = time.perf_counter()
        buffer.schedule(track, stream)
        await buffer.wait_idle()
        download_ms = (time.perf_counter() - started) * 1000.0
        path = buffer.get(track.video_id)
        if path is None:
            raise RuntimeError("buffer download failed")
        frame_ms = await first_pcm_frame_ms(str(path), ffmpeg, is_file=True)
        await buffer.clear()
    return {"buffer_download": download_ms, "buffered_first_frame": frame_ms}


async def run_once(
    query: str, ffmpeg: str, deno_path: Optional[str], searcher: YtMusicSearcher
) -> dict[str, float]:
    resolver = build_resolver(deno_path, searcher)
    started = time.perf_counter()
    track = await resolver.resolve_query(query, requester_id=0)
    searched = time.perf_counter()
    stream = await resolver.get_stream(track)
    streamed = time.perf_counter()
    frame_ms = await first_pcm_frame_ms(stream.url, ffmpeg)
    cold_total = (time.perf_counter() - started) * 1000.0

    warm_started = time.perf_counter()
    warm_track = await resolver.resolve_query(query, requester_id=0)  # search cache hit
    await resolver.get_stream(warm_track)  # stream cache hit
    warm_ms = (time.perf_counter() - warm_started) * 1000.0

    buffered = await buffered_once(resolver, track, ffmpeg)
    return {
        "search": (searched - started) * 1000.0,
        "stream": (streamed - searched) * 1000.0,
        "ffmpeg": frame_ms,
        "cold_play": cold_total,
        "warm_play": warm_ms,
        **buffered,
    }


def summarise(samples: dict[str, list[float]]) -> dict[str, dict[str, float]]:
    return {
        name: {
            "p50": percentile(values, 50),
            "p95": percentile(values, 95),
            "min": min(values),
            "max": max(values),
            "n": float(len(values)),
        }
        for name, values in samples.items()
        if values
    }


def git_commit() -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return proc.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


async def main_async(args: argparse.Namespace) -> int:
    health = check_health()
    if not health.ffmpeg_found or not health.ffmpeg_path:
        out("FFmpeg not found. Install it or put it on PATH.")
        return 2

    searcher = YtMusicSearcher()
    # Warm once like the bot's setup_hook; the searcher's query cache persists across runs.
    await searcher.warm_up()

    samples: dict[str, list[float]] = {}
    failures = 0
    for index in range(args.runs):
        query = QUERIES[index % len(QUERIES)]
        try:
            result = await run_once(query, health.ffmpeg_path, health.deno_path, searcher)
        except Exception as exc:  # harness: report and keep measuring
            failures += 1
            out(f"run {index + 1} ({query!r}) failed: {type(exc).__name__}: {exc}")
            continue
        for name, value in result.items():
            samples.setdefault(name, []).append(value)
        out(f"run {index + 1}: " + "  ".join(f"{k}={v:.0f}ms" for k, v in result.items()))
        await asyncio.sleep(args.pause)

    summary = summarise(samples)
    out()
    out(f"{'scenario':<12}{'p50':>10}{'p95':>10}{'min':>10}{'max':>10}{'n':>5}")
    for name, stats in summary.items():
        out(
            f"{name:<12}{stats['p50']:>10.0f}{stats['p95']:>10.0f}"
            f"{stats['min']:>10.0f}{stats['max']:>10.0f}{stats['n']:>5.0f}"
        )
    if "cold_play" in summary:
        verdict = "PASS" if summary["cold_play"]["p50"] <= COLD_PLAY_TARGET_MS else "FAIL"
        out(f"\ncold_play p50 target <= {COLD_PLAY_TARGET_MS:.0f} ms: {verdict}")

    if args.compare:
        baseline = json.loads(Path(args.compare).read_text(encoding="utf-8"))["summary"]
        out(f"\n{'scenario':<12}{'baseline p50':>14}{'now p50':>10}{'change':>10}")
        for name, stats in summary.items():
            if name in baseline:
                before = baseline[name]["p50"]
                delta = (stats["p50"] - before) / before * 100.0 if before else 0.0
                out(f"{name:<12}{before:>14.0f}{stats['p50']:>10.0f}{delta:>9.0f}%")

    report = {
        "label": args.label,
        "when": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "yt_dlp": yt_dlp.version.__version__,
        "commit": git_commit(),
        "runs": args.runs,
        "failures": failures,
        "samples": samples,
        "summary": summary,
    }
    target = Path(args.out) if args.out else Path("docs/perf") / f"{args.label}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    out(f"wrote {target}")
    return 0 if samples else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--label", default="run")
    parser.add_argument("--pause", type=float, default=1.0, help="seconds between runs")
    parser.add_argument("--out", default=None, help="output JSON path")
    parser.add_argument("--compare", default=None, help="baseline JSON to diff against")
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
