# Yokai Music Core Implementation Plan (Phases 1-2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure today's music latency, then make `/play` fast, tracks gapless, and `/skip` and `/stop` always work.

**Architecture:** Phase 1 builds a live timing harness and reproduces the reported bugs as failing tests. Phase 2 swaps free-text search to `ytmusicapi` (0.4s instead of yt-dlp's 10.8s), makes the yt-dlp resolver deduplicate and prioritise work, adds a local audio pre-buffer, and rebuilds `GuildPlayer` around generation-tagged callbacks so stale voice callbacks can never advance or corrupt the queue.

**Tech Stack:** Python 3.12+, discord.py 2.7.1 with `davey`, yt-dlp 2026.8.19 (in-process), `ytmusicapi` 1.12.3, FFmpeg, pytest + pytest-asyncio, ruff. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-24-yokai-overhaul-design.md` (sections 3, 6 and 7). Phases 3-5 (restyle, features, cleanup) get their own plans after this one is built and reviewed, because they depend on interfaces created here.

**Deliberate refinement of the spec:** section 3.3 says "one playback loop per guild with events". This plan meets the same guarantees with a simpler mechanism: every playback intent carries a generation number, a voice callback from an older generation is ignored, and exactly one cancellable "advance" task loads the next track. There is no `_play_lock`. Skip and stop cancel the in-flight load and bump the generation.

**Measured findings this plan is built on** (probed 2026-09-24 on the owner's PC, `docs/perf/` will hold the formal numbers):
- yt-dlp `ytsearch5` takes about 10.8s (it fully extracts all 5 results). Flat search takes 1.3-1.6s. `ytmusicapi` search takes 0.43s warm and 1.56s on its first call.
- Stream extraction for one video takes about 1.4s. About 1.26s of that is yt-dlp downloading the YouTube watch page. Reusing a warm `YoutubeDL` instance does not help. Restricting `player_client` did not help either (most clients fail, `android_vr` and `android` return a muxed mp4 at the same speed).
- Today's cold `/play` is therefore about 12s (search) plus 1.4s (stream) plus FFmpeg start. With `ytmusicapi` search the projection is about 0.4 + 1.4 + 0.3 = 2.1s. **Reaching 1.5s cold needs the watch-page cost removed**, which Task 3's matrix tests (`player_skip=webpage` combined with clients that do not need it). Task 11 ends with an explicit decision gate if the target is still missed.

## Global Constraints

- Python 3.12+, `discord.py>=2.7.1` with voice extras plus `davey`. No new dependencies (AGENTS.md: ask first). Stdlib, `discord.py`, `yt-dlp`, `ytmusicapi`, `httpx`, `aiosqlite` only.
- Every source file starts with `from __future__ import annotations`, is fully type-hinted, uses `logging` (never `print` in `src/`), and has no bare `except`.
- Never block the event loop. yt-dlp, ytmusicapi and the Spotify scraper are synchronous: call them through `asyncio.to_thread` with a timeout and a concurrency limit.
- Every external integration sits behind an interface (`Resolver`, `SpotifyProvider`, `Recommender`) so it can be faked in tests. Default `pytest` makes no network calls.
- Configuration comes from environment variables loaded in `config.py`; document every variable in `.env.example`.
- Never store stream URLs in the queue: queue entries are lightweight `Track` records (spec section 7.1).
- Security: accept links only from `youtube.com`, `youtu.be`, `music.youtube.com`, `open.spotify.com`, `spotify.link`. Free-text queries become `ytsearch` queries, never shell strings, never `shell=True`. Video IDs are never used as file paths without validation.
- Every user-visible message goes through `EmbedFactory` (`ui/embeds.py`); `tests/test_code_standards.py` greps for raw sends. Error embeds are ephemeral.
- Do not use Spotify-derived data in analytics tables (`play_events` stores YouTube-derived fields only).
- Ruff: line length 100, rules `E, F, I, W`. Run `ruff format` on files you create or change. If `ruff check` reports only import-order problems (`I001`), fix them with `ruff check --fix <file>`; for any other rule, fix the code by hand.
- Work on `main`. One commit per task, message ends with the trailer `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`. Never commit `.env`, tokens, databases, cookies, or anything under `data/`.
- After each phase: stop, summarise, list anything unverified, wait for the owner's review (AGENTS.md).
- Test command (from repo root): `.venv/Scripts/python.exe -m pytest -q`. Lint: `.venv/Scripts/python.exe -m ruff check .` and `.venv/Scripts/python.exe -m ruff format --check .`. Baseline before this plan: 76 tests pass, ruff clean, tag `checkpoint/pre-overhaul` at `b422747`.

## Review Focus

Failure modes the spec implies but that no single task would obviously test. Each has a named test in the owning task.

1. **Mashed controls.** `/skip` pressed twice quickly, or two users running `/play` at the same instant while idle, must not double-advance, lose a track, or start two loads. (Task 9: `test_double_skip_advances_twice_and_goes_idle`, `test_concurrent_play_next_starts_one_worker`, `test_stale_voice_callback_is_ignored`.)
2. **A stream that dies instantly.** A URL that returns 403 or an FFmpeg process that exits at 0s must not loop forever: resume attempts are capped at 2, then the track is recorded as an error and the queue moves on. Loop-track mode must not re-queue a failing track. (Task 9: `test_resume_attempts_are_bounded`.)
3. **Hostile or non-YouTube video IDs.** `sp:...` placeholder IDs, `../evil`, empty strings and absurdly long strings must never become a file path in the buffer directory. (Task 8: `test_unsafe_video_ids_are_never_buffered`.)
4. **Full or unwritable disk.** If the buffer download fails (disk full, permission denied), playback continues from the remote URL and no exception reaches the user. (Task 8: `test_download_failure_leaves_no_files`; Task 9: `test_buffer_failure_does_not_block_playback`.)
5. **Search that returns nothing usable, or ytmusicapi failing.** Only live/too-long/no-duration results, an empty result list, or an exception from ytmusicapi must fall through to the yt-dlp flat search and end in a friendly `NotFoundError`, never a crash or a hang. (Task 7: `test_resolver_falls_back_when_search_returns_nothing`, `test_search_swallows_client_errors`.)

Also covered: `/clear` while a pre-built FFmpeg source is waiting must clean the process up (Task 9: `test_stale_prepared_source_is_discarded`).

---

# PHASE 1: Baseline and reproduction

### Task 1: Timing helpers

**Files:**
- Create: `src/yokai/music/timing.py`
- Test: `tests/test_timing.py`

**Interfaces:**
- Produces: `StageTimer(stage: str, **fields: object)` context manager with `.elapsed_ms: float`, logging one line on `yokai.timing` (`stage=<name> ms=<n> status=ok|error <fields>`); `percentile(values: Sequence[float], pct: float) -> float` (nearest-rank, `0.0` for empty input).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_timing.py`:

```python
"""Tests for stage timing and percentile helpers."""

from __future__ import annotations

import logging
import time

import pytest

from yokai.music.timing import StageTimer, percentile


def test_percentile_nearest_rank() -> None:
    values = [float(n) for n in range(1, 11)]
    assert percentile(values, 50) == 5.0
    assert percentile(values, 95) == 10.0
    assert percentile(values, 100) == 10.0
    assert percentile([7.0], 95) == 7.0


def test_percentile_empty_is_zero() -> None:
    assert percentile([], 50) == 0.0


def test_percentile_is_order_independent() -> None:
    assert percentile([9.0, 1.0, 5.0, 3.0, 7.0], 50) == 5.0


def test_stage_timer_measures_and_logs(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="yokai.timing"):
        with StageTimer("extract", video="abc123") as timer:
            time.sleep(0.02)
    assert timer.elapsed_ms >= 15.0
    assert "stage=extract" in caplog.text
    assert "status=ok" in caplog.text
    assert "video=abc123" in caplog.text


def test_stage_timer_marks_errors_and_does_not_swallow(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="yokai.timing"):
        with pytest.raises(ValueError):
            with StageTimer("search"):
                raise ValueError("boom")
    assert "status=error" in caplog.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_timing.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'yokai.music.timing'`)

- [ ] **Step 3: Write the implementation**

Create `src/yokai/music/timing.py`:

```python
"""Lightweight stage timing used for latency logs and the benchmark harness."""

from __future__ import annotations

import logging
import math
import time
from types import TracebackType
from typing import Optional, Sequence

logger = logging.getLogger("yokai.timing")


class StageTimer:
    """Context manager that logs how long a named stage took, in milliseconds.

    Usage:
        with StageTimer("stream_extract", video=track.video_id) as timer:
            ...
        timer.elapsed_ms
    """

    def __init__(self, stage: str, **fields: object) -> None:
        self.stage = stage
        self.fields = fields
        self.elapsed_ms: float = 0.0
        self._start: float = 0.0

    def __enter__(self) -> StageTimer:
        self._start = time.perf_counter()
        return self

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        self.elapsed_ms = (time.perf_counter() - self._start) * 1000.0
        extra = " ".join(f"{key}={value}" for key, value in self.fields.items())
        status = "error" if exc_type is not None else "ok"
        logger.info("stage=%s ms=%.0f status=%s %s", self.stage, self.elapsed_ms, status, extra)


def percentile(values: Sequence[float], pct: float) -> float:
    """Nearest-rank percentile; returns 0.0 for empty input."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100.0 * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]
```

- [ ] **Step 4: Run tests and lint**

Run: `.venv/Scripts/python.exe -m pytest tests/test_timing.py -q && .venv/Scripts/python.exe -m ruff check src/yokai/music/timing.py tests/test_timing.py && .venv/Scripts/python.exe -m ruff format src/yokai/music/timing.py tests/test_timing.py`
Expected: 5 passed, `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add src/yokai/music/timing.py tests/test_timing.py
git commit -m "feat(perf): add StageTimer and percentile helpers" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: Reproduce the reported player bugs (strict xfail tests)

The owner reported "songs stop playing and `/skip` does nothing". Code reading found three concrete bugs in the current `GuildPlayer`: (a) `skip()` waits on `_play_lock`, which a stuck stream load holds; (b) `skip()` records a `skipped` play event and then `_on_track_end` records a second one; (c) `stop()` under loop-track mode lets the track-end callback re-add the track to the cleared queue. These tests fail today (marked `xfail(strict=True)` so the suite stays green); Task 9 removes the markers.

**Files:**
- Create: `tests/test_player_reliability.py`

**Interfaces:**
- Consumes: `tests/test_player.py` (`DummyAudioSource`, `FakeResolver`, `FakeVoiceClient`), `yokai.music.player.GuildPlayer`.
- Produces: helpers `make_bot()`, `make_track(video_id, duration_s=180)`, `HangingResolver`. Task 9 replaces this whole file with a superset.

- [ ] **Step 1: Write the tests**

Create `tests/test_player_reliability.py`:

```python
"""Reliability tests for GuildPlayer (bug reproductions for the Phase 1 baseline)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.test_player import DummyAudioSource, FakeResolver, FakeVoiceClient
from yokai.music.models import LoopMode, PlayerState, StreamInfo, Track
from yokai.music.player import GuildPlayer


def make_bot() -> MagicMock:
    bot = MagicMock()
    bot.config.max_queue_size = 50
    bot.config.ffmpeg_path = None
    bot.config.alone_disconnect_seconds = 120
    bot.config.idle_disconnect_seconds = 300
    bot.loop = asyncio.get_running_loop()
    bot.presence = AsyncMock()
    bot.db = AsyncMock()
    return bot


def make_track(video_id: str, duration_s: int = 180) -> Track:
    return Track(video_id=video_id, title=f"Title {video_id}", duration_s=duration_s, requester_id=1)


class HangingResolver(FakeResolver):
    """Resolver whose stream lookups never finish (simulates a stuck yt-dlp call)."""

    async def get_stream(self, track: Track, *, background: bool = False) -> StreamInfo:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


@pytest.mark.xfail(strict=True, reason="Phase 2: skip must work while a stream load is stuck")
async def test_skip_works_while_stream_load_is_stuck() -> None:
    player = GuildPlayer(guild_id=1, bot=make_bot(), resolver=HangingResolver())
    player.voice_client = FakeVoiceClient()
    player.queue.add(make_track("stuck"))
    player.queue.add(make_track("next"))

    load = asyncio.create_task(player.play_next())
    try:
        await asyncio.sleep(0.05)
        assert player.state == PlayerState.LOADING
        skipped = await asyncio.wait_for(player.skip(), timeout=1.0)
        assert skipped is not None
        assert skipped.video_id == "stuck"
    finally:
        load.cancel()


@pytest.mark.xfail(strict=True, reason="Phase 2: skip records exactly one play event")
async def test_skip_records_a_single_play_event() -> None:
    bot = make_bot()
    player = GuildPlayer(guild_id=1, bot=bot, resolver=FakeResolver())
    player.voice_client = FakeVoiceClient()
    player.queue.add(make_track("t1"))
    player.queue.add(make_track("t2"))

    with patch("discord.FFmpegPCMAudio", side_effect=lambda *a, **k: DummyAudioSource()):
        await player.play_next()
        await player.skip()
        await asyncio.sleep(0.05)

    events = [
        call.kwargs["outcome"]
        for call in bot.db.record_play_event.await_args_list
        if call.kwargs["video_id"] == "t1"
    ]
    assert events == ["skipped"]


@pytest.mark.xfail(strict=True, reason="Phase 2: stop must leave the queue empty in loop mode")
async def test_stop_leaves_queue_empty_even_with_loop_track() -> None:
    player = GuildPlayer(guild_id=1, bot=make_bot(), resolver=FakeResolver())
    player.voice_client = FakeVoiceClient()
    player.queue.loop_mode = LoopMode.TRACK
    player.queue.add(make_track("t1"))

    with patch("discord.FFmpegPCMAudio", side_effect=lambda *a, **k: DummyAudioSource()):
        await player.play_next()
        await player.stop()
        await asyncio.sleep(0.05)

    assert len(player.queue) == 0
```

- [ ] **Step 2: Update `FakeResolver` so the new keyword and hook exist**

In `tests/test_player.py`, replace the whole `FakeResolver` class with:

```python
class FakeResolver(Resolver):
    """Test fake for Resolver interface."""

    def __init__(self) -> None:
        self.stream_call_count = 0
        self.streamed_ids: list[str] = []
        self.background_calls = 0
        self.invalidated: list[str] = []

    async def resolve_query(self, query: str, requester_id: int) -> Track:
        return Track(
            video_id="fake_q",
            title="Query Result",
            duration_s=180,
            requester_id=requester_id,
        )

    async def resolve_url(
        self, url: str, requester_id: int, max_tracks: int = 100
    ) -> tuple[Track | list[Track], bool]:
        return (
            Track(
                video_id="fake_u",
                title="URL Result",
                duration_s=200,
                requester_id=requester_id,
            ),
            False,
        )

    async def get_stream(self, track: Track, *, background: bool = False) -> StreamInfo:
        self.stream_call_count += 1
        self.streamed_ids.append(track.video_id)
        if background:
            self.background_calls += 1
        return StreamInfo(url=f"http://stream.test/{track.video_id}")

    def invalidate_stream(self, video_id: str) -> None:
        self.invalidated.append(video_id)
```

(`Resolver.invalidate_stream` does not exist yet; defining it on the fake is harmless. Task 6 adds it to the ABC.)

- [ ] **Step 3: Run the suite and confirm the three tests xfail**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: `76 passed, 3 xfailed` (plus the 5 tests from Task 1 = `81 passed, 3 xfailed`). Strict xfail means an unexpected pass would fail the run, so each test really reproduces its bug.

- [ ] **Step 4: Lint and commit**

Run: `.venv/Scripts/python.exe -m ruff format tests/test_player_reliability.py tests/test_player.py && .venv/Scripts/python.exe -m ruff check .`
Expected: `All checks passed!`

```bash
git add tests/test_player_reliability.py tests/test_player.py
git commit -m "test(player): reproduce skip/stop bugs as strict xfails" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: Live benchmark harness and extraction matrix

Two scripts that hit real YouTube. They are not part of pytest.

**Files:**
- Create: `scripts/bench_music.py`
- Create: `scripts/bench_extract_matrix.py`

**Interfaces:**
- Consumes: `yokai.music.resolvers.ytdlp.YtDlpResolver` (`resolve_query`, `get_stream`), `yokai.health.check_health`, `yokai.music.timing.percentile`.
- Produces: `docs/perf/<label>.json` from `bench_music.py` (`samples`, `summary` with p50/p95/min/max per scenario) and `docs/perf/extract-matrix.json` from the matrix script (one row per option profile: `median_ms`, `format_id`, `audio_only`, `plays_without_headers`, `error`). Task 11 extends `bench_music.py`.

- [ ] **Step 1: Write `scripts/bench_music.py`**

```python
"""Live latency harness for the Yokai music pipeline.

Hits real YouTube, so it is NOT part of pytest. Run from the repo root (PowerShell):

    .\\.venv\\Scripts\\python.exe scripts\\bench_music.py --runs 5 --label baseline

Scenarios (each reports p50 and p95 in milliseconds):
    search      free-text query -> Track
    stream      Track -> stream URL (fresh resolver, so nothing is cached)
    ffmpeg      stream URL -> first 20 ms PCM frame out of FFmpeg
    cold_play   the three above in sequence (what /play feels like on a cold start)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import discord
import yt_dlp.version

from yokai.health import check_health
from yokai.music.resolvers.ytdlp import YtDlpResolver
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


def build_resolver(deno_path: Optional[str]) -> YtDlpResolver:
    """Fresh resolver so no stream cache carries over between runs."""
    return YtDlpResolver(deno_path=Path(deno_path) if deno_path else None)


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


async def run_once(query: str, ffmpeg: str, deno_path: Optional[str]) -> dict[str, float]:
    resolver = build_resolver(deno_path)
    started = time.perf_counter()
    track = await resolver.resolve_query(query, requester_id=0)
    searched = time.perf_counter()
    stream = await resolver.get_stream(track)
    streamed = time.perf_counter()
    frame_ms = await first_pcm_frame_ms(stream.url, ffmpeg)
    return {
        "search": (searched - started) * 1000.0,
        "stream": (streamed - searched) * 1000.0,
        "ffmpeg": frame_ms,
        "cold_play": (time.perf_counter() - started) * 1000.0,
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

    samples: dict[str, list[float]] = {}
    failures = 0
    for index in range(args.runs):
        query = QUERIES[index % len(QUERIES)]
        try:
            result = await run_once(query, health.ffmpeg_path, health.deno_path)
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
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Write `scripts/bench_extract_matrix.py`**

```python
"""Find the fastest yt-dlp option profile that still yields playable audio.

Measurements on the owner's PC showed ~1.26 s of a 1.4 s stream extraction is yt-dlp downloading
the YouTube watch page. This script times every player client, with and without
`player_skip=webpage`, and checks each resulting URL actually plays in FFmpeg. It sends about
50 requests to YouTube, so run it sparingly. Run from the repo root (PowerShell):

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
        return ["android_vr", "ios", "mweb", "tv", "web", "web_safari", "web_music"]


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
            [ffmpeg, "-nostdin", "-loglevel", "error", "-t", "2", "-i", url, "-vn", "-f", "null", "-"],
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
        row = evaluate(label, extractor_args, args.runs, health.deno_path, health.ffmpeg_path, args.pause)
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
```

- [ ] **Step 3: Verify both scripts parse and lint**

Run: `.venv/Scripts/python.exe -m ruff format scripts && .venv/Scripts/python.exe -m ruff check scripts && .venv/Scripts/python.exe scripts/bench_music.py --help && .venv/Scripts/python.exe scripts/bench_extract_matrix.py --help`
Expected: ruff clean; both `--help` outputs print usage without importing errors.

- [ ] **Step 4: Commit**

```bash
git add scripts
git commit -m "feat(perf): add live latency harness and yt-dlp extraction matrix" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: Capture the baseline, record findings, checkpoint, stop for review

**Files:**
- Create: `docs/perf/baseline.json` (generated)
- Create: `docs/perf/extract-matrix.json` (generated)
- Create: `docs/perf/phase1-findings.md`

- [ ] **Step 1: Run the baseline (takes about 2 minutes)**

Run: `.venv/Scripts/python.exe scripts/bench_music.py --runs 5 --label baseline`
Expected: five `run N:` lines and a table with `search`, `stream`, `ffmpeg`, `cold_play` rows. From earlier probes `search` p50 should be around 10000ms. If runs fail with a bot-check error, wait 10 minutes and retry rather than continuing.

- [ ] **Step 2: Run the extraction breakdown and matrix (takes about 3 minutes)**

Run: `.venv/Scripts/python.exe scripts/bench_extract_matrix.py --breakdown`
Expected: timestamped lines; the gap between `Downloading webpage` and the next line is the watch-page cost (about 1.2s in earlier probes).

Run: `.venv/Scripts/python.exe scripts/bench_extract_matrix.py --runs 2`
Expected: one line per profile, then "Fastest usable profiles". This writes `docs/perf/extract-matrix.json`.

- [ ] **Step 3: Write `docs/perf/phase1-findings.md`**

Fill every field from the command output above (no field may be left blank):

```markdown
# Phase 1 findings (measured <date> on <commit from baseline.json>)

## Baseline latency (docs/perf/baseline.json, n = <runs>, yt-dlp <version>)
| scenario | p50 ms | p95 ms |
|---|---|---|
| search | <n> | <n> |
| stream | <n> | <n> |
| ffmpeg | <n> | <n> |
| cold_play | <n> | <n> |

## Where the stream extraction time goes (`--breakdown`)
<paste the timestamped lines>

## Fastest usable extraction profile (docs/perf/extract-matrix.json)
- Profile: <label>, median <n> ms, format <id> (<audio-only|muxed>), plays without headers: <yes/no>
- Default profile median for comparison: <n> ms
- Decision for Task 6: <"set FAST_STREAM_EXTRACTOR_ARGS to <extractor_args json>" if the fastest usable profile is at least 500 ms faster than default, otherwise "leave FAST_STREAM_EXTRACTOR_ARGS = None">

## Reproduced bugs
Three strict-xfail tests in tests/test_player_reliability.py reproduce: skip hangs while a load is stuck, skip records two play events, stop leaves the queue non-empty in loop-track mode.
```

- [ ] **Step 4: Verify, commit, tag**

Run: `.venv/Scripts/python.exe -m pytest -q && .venv/Scripts/python.exe -m ruff check . && .venv/Scripts/python.exe -m ruff format --check .`
Expected: `81 passed, 3 xfailed`, ruff clean.

```bash
git add docs/perf
git commit -m "docs(perf): record Phase 1 baseline and extraction matrix" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
git tag -a checkpoint/phase1-baseline -m "Phase 1 complete: harness, baseline numbers, bug reproductions"
```

- [ ] **Step 5: STOP for owner review**

Report to the owner: the baseline table, the fastest usable extraction profile and the Task 6 decision, the three reproduced bugs, and anything unverified (real Discord voice behaviour cannot be measured by the harness). Do not start Phase 2 until the owner approves.

---

# PHASE 2: Music core

### Task 5: Foundations (config, error type, queue primitive)

**Files:**
- Modify: `src/yokai/config.py`
- Modify: `.env.example`
- Modify: `src/yokai/errors.py`
- Modify: `src/yokai/music/models.py`
- Test: `tests/test_config.py`, `tests/test_queue.py`, `tests/test_errors.py`

**Interfaces:**
- Produces: `Config.audio_buffer_mb: int = 500` (0 disables), `Config.audio_buffer_dir: Path = Path("data/cache")`; `UnmatchedTrackError(title: str)` (a `YokaiError`, `.message` = `Could not find a confident YouTube match for **<title>**.`); `MusicQueue.push_front(track: Track) -> None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
def test_audio_buffer_defaults_and_overrides() -> None:
    base = {"DISCORD_TOKEN": "t.o.k", "GUILD_ID": "1", "OWNER_ID": "2"}
    with patch.dict(os.environ, base, clear=True):
        cfg = load_config(env_path=Path("non_existent_env"))
        assert cfg.audio_buffer_mb == 500
        assert cfg.audio_buffer_dir == Path("data/cache")

    custom = {**base, "AUDIO_BUFFER_MB": "128", "AUDIO_BUFFER_DIR": "somewhere/else"}
    with patch.dict(os.environ, custom, clear=True):
        cfg = load_config(env_path=Path("non_existent_env"))
        assert cfg.audio_buffer_mb == 128
        assert cfg.audio_buffer_dir == Path("somewhere/else")


def test_negative_audio_buffer_means_disabled() -> None:
    env = {"DISCORD_TOKEN": "t.o.k", "GUILD_ID": "1", "OWNER_ID": "2", "AUDIO_BUFFER_MB": "-5"}
    with patch.dict(os.environ, env, clear=True):
        assert load_config(env_path=Path("non_existent_env")).audio_buffer_mb == 0
```

Append to `tests/test_queue.py`:

```python
def test_push_front_replays_before_other_tracks() -> None:
    queue = MusicQueue(max_size=3)
    first = _make_track(1)
    second = _make_track(2)
    queue.add(second)
    queue.push_front(first)
    assert queue.peek_next() is first
    assert [t.video_id for t in queue.upcoming] == ["vid_1", "vid_2"]


def test_push_front_ignores_size_cap() -> None:
    queue = MusicQueue(max_size=1)
    queue.add(_make_track(1))
    queue.push_front(_make_track(2))
    assert len(queue) == 2
```

Create `tests/test_errors.py`:

```python
"""Tests for domain error types."""

from __future__ import annotations

from yokai.errors import UnmatchedTrackError, YokaiError


def test_unmatched_track_error_carries_title_and_hint() -> None:
    err = UnmatchedTrackError("Song X")
    assert isinstance(err, YokaiError)
    assert "Song X" in err.message
    assert err.user_hint
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_config.py tests/test_queue.py tests/test_errors.py -q`
Expected: FAIL (missing attributes/imports).

- [ ] **Step 3: Implement**

In `src/yokai/config.py`, add after `recommend_count: int = 8` in the dataclass:

```python

    # Audio pre-buffer (0 MB disables it)
    audio_buffer_mb: int = 500
    audio_buffer_dir: Path = Path("data/cache")
```

In `load_config`, after the `recommend_count = ...` line add:

```python
    audio_buffer_mb = max(0, _parse_int("AUDIO_BUFFER_MB", os.getenv("AUDIO_BUFFER_MB"), 500))
    audio_buffer_dir = Path(os.getenv("AUDIO_BUFFER_DIR", "data/cache").strip() or "data/cache")
```

and in the `Config(...)` call add, after `recommend_count=recommend_count,`:

```python
        audio_buffer_mb=audio_buffer_mb,
        audio_buffer_dir=audio_buffer_dir,
```

Append to `.env.example`:

```
# Audio pre-buffer: upcoming tracks are downloaded here so track changes are gapless.
# AUDIO_BUFFER_MB=0 disables it. The folder is wiped on startup and on /stop.
AUDIO_BUFFER_MB=500
AUDIO_BUFFER_DIR=data/cache
```

In `src/yokai/errors.py`, add after `SpotifyResolutionError`:

```python
class UnmatchedTrackError(YokaiError):
    """Raised when an imported Spotify track has no confident YouTube match."""

    def __init__(self, title: str) -> None:
        super().__init__(
            f"Could not find a confident YouTube match for **{title}**.",
            user_hint="Try searching with the song title and artist, or paste a YouTube link.",
        )
```

In `src/yokai/music/models.py`, add to `MusicQueue` right after `add_many`:

```python
    def push_front(self, track: Track) -> None:
        """Put a track at the head of the queue, ignoring the size cap.

        Used to replay the current track in loop-track mode.
        """
        self._upcoming.insert(0, track)
```

- [ ] **Step 4: Verify and commit**

Run: `.venv/Scripts/python.exe -m pytest -q && .venv/Scripts/python.exe -m ruff format src tests && .venv/Scripts/python.exe -m ruff check .`
Expected: all pass (`3 xfailed` still), ruff clean.

```bash
git add src tests .env.example
git commit -m "feat(music): add audio buffer config, UnmatchedTrackError, push_front" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 6: Resolver: flat-search fallback, single-flight, priority, real expiry

**Files:**
- Modify: `src/yokai/music/resolvers/base.py`
- Replace: `src/yokai/music/resolvers/ytdlp.py` (full new content below)
- Test: `tests/test_resolver.py`

**Interfaces:**
- Consumes: `yokai.music.timing.StageTimer`; `yokai.music.search.YtMusicSearcher` (Task 7, type-only import, optional constructor argument).
- Produces:
  - `Resolver.get_stream(track: Track, *, background: bool = False) -> StreamInfo`
  - `Resolver.invalidate_stream(video_id: str) -> None` (default no-op)
  - `YtDlpResolver(deno_path=None, cookies_file=None, max_track_seconds=3600, concurrency_limit=2, timeout_seconds=30.0, searcher=None)`; `.searcher` attribute
  - `stream_expiry(url: str, now: float | None = None) -> float`
  - module constant `FAST_STREAM_EXTRACTOR_ARGS: dict | None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_resolver.py` (add `import asyncio`, `import threading`, `import time` and `from yokai.music.resolvers import ytdlp as ytdlp_module` and `from yokai.music.resolvers.ytdlp import stream_expiry` to the imports at the top):

```python
def test_stream_expiry_reads_expire_param() -> None:
    url = "https://rr1.googlevideo.com/videoplayback?expire=1790321938&id=abc"
    assert stream_expiry(url, now=1000.0) == 1790321938 - 120.0


def test_stream_expiry_falls_back_to_ttl() -> None:
    assert stream_expiry("https://example.test/audio", now=1000.0) == 1000.0 + 1800.0


@pytest.mark.asyncio
async def test_get_stream_is_single_flight() -> None:
    calls = 0

    def slow_extract(*_args: object, **_kwargs: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        time.sleep(0.05)
        return {"url": "https://googlevideo.com/one"}

    resolver = YtDlpResolver()
    track = Track(video_id="vid_sf", title="T", duration_s=100)
    with patch.object(yt_dlp.YoutubeDL, "extract_info", side_effect=slow_extract):
        results = await asyncio.gather(
            resolver.get_stream(track),
            resolver.get_stream(track, background=True),
            resolver.get_stream(track),
        )
    assert calls == 1
    assert {r.url for r in results} == {"https://googlevideo.com/one"}


@pytest.mark.asyncio
async def test_expired_cache_entry_is_refetched() -> None:
    resolver = YtDlpResolver()
    track = Track(video_id="vid_exp", title="T", duration_s=100)
    from yokai.music.models import StreamInfo

    stale = StreamInfo(url="https://old.test/a")
    resolver._stream_cache["vid_exp"] = (stale, time.time() - 5)
    with patch.object(
        yt_dlp.YoutubeDL, "extract_info", return_value={"url": "https://new.test/b"}
    ) as mock_extract:
        info = await resolver.get_stream(track)
    assert info.url == "https://new.test/b"
    assert mock_extract.call_count == 1


@pytest.mark.asyncio
async def test_invalidate_stream_drops_cache() -> None:
    resolver = YtDlpResolver()
    track = Track(video_id="vid_inv", title="T", duration_s=100)
    with patch.object(yt_dlp.YoutubeDL, "extract_info", return_value={"url": "https://a.test/1"}):
        await resolver.get_stream(track)
    resolver.invalidate_stream("vid_inv")
    with patch.object(yt_dlp.YoutubeDL, "extract_info", return_value={"url": "https://a.test/2"}):
        assert (await resolver.get_stream(track)).url == "https://a.test/2"


@pytest.mark.asyncio
async def test_background_work_does_not_starve_foreground() -> None:
    release = threading.Event()

    def extract(_self: object, target: str, download: bool = False) -> dict[str, object]:
        if "vid_bg" in target:
            release.wait(timeout=5)
        return {"url": f"https://googlevideo.com/{target[-6:]}"}

    resolver = YtDlpResolver(concurrency_limit=2)
    with patch.object(yt_dlp.YoutubeDL, "extract_info", new=extract):
        # Both background lookups are patched too (no real network): the first blocks inside
        # extract(), the second waits for the single background slot.
        background = asyncio.create_task(
            resolver.get_stream(
                Track(video_id="vid_bg", title="B", duration_s=100), background=True
            )
        )
        queued_background = asyncio.create_task(
            resolver.get_stream(
                Track(video_id="vid_bg2", title="B2", duration_s=100), background=True
            )
        )
        await asyncio.sleep(0.05)
        try:
            foreground = await asyncio.wait_for(
                resolver.get_stream(Track(video_id="vid_fg", title="F", duration_s=100)),
                timeout=2.0,
            )
            assert foreground.url.startswith("https://googlevideo.com/")
        finally:
            release.set()
            await asyncio.gather(background, queued_background, return_exceptions=True)


@pytest.mark.asyncio
async def test_flat_search_fallback_uses_flat_extraction() -> None:
    created: list[dict[str, object]] = []

    class FakeYDL:
        def __init__(self, opts: dict[str, object]) -> None:
            created.append(opts)

        def __enter__(self) -> FakeYDL:
            return self

        def __exit__(self, *_exc: object) -> bool:
            return False

        def extract_info(self, target: str, download: bool = False) -> dict[str, object]:
            return {
                "entries": [
                    {"id": "flat_vid", "title": "Flat Song", "uploader": "A", "duration": 200}
                ]
            }

    resolver = YtDlpResolver()
    with patch.object(yt_dlp, "YoutubeDL", FakeYDL):
        track = await resolver.resolve_query("some song", requester_id=5)
    assert track.video_id == "flat_vid"
    assert created[0]["extract_flat"] == "in_playlist"


@pytest.mark.asyncio
async def test_fast_profile_is_tried_first_then_default(monkeypatch: pytest.MonkeyPatch) -> None:
    fast = {"youtube": {"player_client": ["android_vr"]}}
    monkeypatch.setattr(ytdlp_module, "FAST_STREAM_EXTRACTOR_ARGS", fast)
    seen: list[object] = []

    class FakeYDL:
        def __init__(self, opts: dict[str, object]) -> None:
            self.opts = opts

        def __enter__(self) -> FakeYDL:
            return self

        def __exit__(self, *_exc: object) -> bool:
            return False

        def extract_info(self, target: str, download: bool = False) -> dict[str, object]:
            seen.append(self.opts.get("extractor_args"))
            if self.opts.get("extractor_args"):
                raise RuntimeError("fast profile rejected")
            return {"url": "https://googlevideo.com/default"}

    resolver = YtDlpResolver()
    with patch.object(yt_dlp, "YoutubeDL", FakeYDL):
        info = await resolver.get_stream(Track(video_id="vid_fast", title="T", duration_s=100))
    assert info.url == "https://googlevideo.com/default"
    assert seen == [fast, None]
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_resolver.py -q`
Expected: FAIL on the new tests (`stream_expiry` import error first).

- [ ] **Step 3: Update the `Resolver` interface**

Replace `get_stream` in `src/yokai/music/resolvers/base.py` and add `invalidate_stream`:

```python
    @abstractmethod
    async def get_stream(self, track: Track, *, background: bool = False) -> StreamInfo:
        """Fetch fresh direct playback audio stream URL and HTTP headers for FFmpeg.

        `background=True` marks speculative work (prefetch) so it never starves playback.
        """
        ...

    def invalidate_stream(self, video_id: str) -> None:
        """Forget any cached stream URL for a video (used before a retry). Default: no-op."""
        return None
```

- [ ] **Step 4: Replace `src/yokai/music/resolvers/ytdlp.py`**

```python
"""In-process yt-dlp audio resolver: priority concurrency, single-flight and error mapping."""

from __future__ import annotations

import asyncio
import collections
import logging
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import yt_dlp

from yokai.errors import (
    BotCheckError,
    ExtractionTimeoutError,
    LoginRequiredError,
    NotFoundError,
    UnavailableError,
    UnsupportedError,
    YokaiError,
)
from yokai.music.models import StreamInfo, Track
from yokai.music.resolvers.base import Resolver
from yokai.music.timing import StageTimer

if TYPE_CHECKING:
    from yokai.music.search import YtMusicSearcher

logger = logging.getLogger(__name__)

# yt-dlp `extractor_args` tried first when fetching a stream URL; None means "defaults only".
# The default profile is always kept as the fallback. Set this from docs/perf/phase1-findings.md
# only when the measured fastest usable profile beats the default by 500 ms or more.
FAST_STREAM_EXTRACTOR_ARGS: Optional[dict[str, Any]] = None

STREAM_EXPIRY_SAFETY_S = 120
DEFAULT_STREAM_TTL_S = 1800
STREAM_CACHE_MAX = 256
_EXPIRE_RE = re.compile(r"[?&/]expire[=/](\d+)")


def stream_expiry(url: str, now: Optional[float] = None) -> float:
    """Epoch seconds after which a stream URL should be treated as expired."""
    current = time.time() if now is None else now
    match = _EXPIRE_RE.search(url)
    if match:
        return float(match.group(1)) - STREAM_EXPIRY_SAFETY_S
    return current + DEFAULT_STREAM_TTL_S


def _pick_thumbnail(entry: dict[str, Any]) -> Optional[str]:
    thumb = entry.get("thumbnail")
    if thumb:
        return str(thumb)
    thumbs = entry.get("thumbnails")
    if isinstance(thumbs, list) and thumbs and isinstance(thumbs[-1], dict):
        url = thumbs[-1].get("url")
        return str(url) if url else None
    return None


def _swallow_task_exception(task: asyncio.Task[Any]) -> None:
    """Mark a shared extraction's exception as retrieved so asyncio does not warn about it."""
    if not task.cancelled():
        task.exception()


class YtDlpResolver(Resolver):
    """In-process audio resolver utilizing yt-dlp and external Deno JS runtime."""

    def __init__(
        self,
        deno_path: Optional[Path] = None,
        cookies_file: Optional[Path] = None,
        max_track_seconds: int = 3600,
        concurrency_limit: int = 2,
        timeout_seconds: float = 30.0,
        searcher: Optional[YtMusicSearcher] = None,
    ) -> None:
        self.deno_path = deno_path
        self.cookies_file = cookies_file
        self.max_track_seconds = max_track_seconds
        self.timeout_seconds = timeout_seconds
        self.searcher = searcher
        # Foreground work (a track someone is waiting for) and background work (prefetch) use
        # separate slots so speculative extractions can never starve playback.
        self._semaphore = asyncio.Semaphore(concurrency_limit)
        self._background_semaphore = asyncio.Semaphore(1)

        # Health telemetry & error ring buffer
        self.consecutive_failures = 0
        self.is_degraded = False
        self.error_ring_buffer: collections.deque[str] = collections.deque(maxlen=20)
        self.resolve_latencies: collections.deque[float] = collections.deque(maxlen=10)

        # video_id -> (StreamInfo, expires_at epoch seconds)
        self._stream_cache: dict[str, tuple[StreamInfo, float]] = {}
        # video_id -> shared extraction task (single-flight)
        self._inflight: dict[str, asyncio.Task[StreamInfo]] = {}

    def _get_base_opts(self) -> dict[str, Any]:
        """Construct standard yt-dlp execution options."""
        js_config: dict[str, Any] = {}
        if self.deno_path and self.deno_path.is_file():
            js_config["path"] = str(self.deno_path)

        opts: dict[str, Any] = {
            "format": "bestaudio/best",
            "skip_download": True,
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": 15,
            "js_runtimes": {"deno": js_config},
        }

        if self.cookies_file and self.cookies_file.is_file():
            opts["cookiefile"] = str(self.cookies_file)

        return opts

    @staticmethod
    def _extract_sync(opts: dict[str, Any], target: str) -> dict[str, Any]:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(target, download=False) or {}

    def _note_failure(self, context: str, message: str) -> None:
        self.consecutive_failures += 1
        if self.consecutive_failures >= 3:
            if not self.is_degraded:
                logger.warning(
                    "YouTube resolver marked DEGRADED after %d consecutive failures.",
                    self.consecutive_failures,
                )
            self.is_degraded = True
        sanitized = message.splitlines()[0] if message else "error"
        self.error_ring_buffer.append(f"{context}: {sanitized}")

    def _map_and_record_error(self, exc: Exception, context: str) -> YokaiError:
        """Map raw yt-dlp exceptions to typed domain errors and update health state."""
        err_msg = str(exc)
        self._note_failure(context, err_msg or type(exc).__name__)
        if isinstance(exc, YokaiError):
            return exc

        err_lower = err_msg.lower()
        sanitized = err_msg.splitlines()[0] if err_msg else type(exc).__name__

        if "sign in to confirm your age" in err_lower or "confirm your age" in err_lower:
            return LoginRequiredError("This video is age-restricted and requires authentication.")
        if "private video" in err_lower or "members-only" in err_lower:
            return LoginRequiredError("This video is private or members-only.")
        if (
            "not available in your country" in err_lower
            or "available in your country" in err_lower
            or "blocked in your country" in err_lower
            or "geo-blocked" in err_lower
            or "geo-restricted" in err_lower
        ):
            return UnavailableError("This video is region-blocked or unavailable.")
        if "video unavailable" in err_lower:
            return NotFoundError("This video is unavailable or has been removed.")
        if "403" in err_lower or "bot detection" in err_lower or "sabr" in err_lower:
            return BotCheckError("YouTube blocked the request (HTTP 403 / bot detection).")

        return YokaiError(f"Audio extraction failed: {sanitized}")

    def _record_success(self) -> None:
        """Reset consecutive failure counter on successful extraction."""
        if self.consecutive_failures > 0:
            logger.info("YouTube resolver recovered after %d failures.", self.consecutive_failures)
        self.consecutive_failures = 0
        self.is_degraded = False

    # ------------------------------------------------------------------ search

    async def resolve_query(self, query: str, requester_id: int) -> Track:
        """Resolve free text to the first playable, non-live track.

        Uses the fast YouTube Music searcher when configured and falls back to a flat yt-dlp
        search (about 1.5 s) if it finds nothing.
        """
        started = time.perf_counter()
        if self.searcher is not None:
            found = await self.searcher.search(query, requester_id, limit=5)
            if found:
                self.resolve_latencies.append((time.perf_counter() - started) * 1000.0)
                return found[0]

        try:
            entries = await self._search_flat(query, limit=5)
        except asyncio.TimeoutError as err:
            mapped = ExtractionTimeoutError()
            self._map_and_record_error(mapped, f"search '{query}'")
            raise mapped from err
        except Exception as exc:
            mapped = self._map_and_record_error(exc, f"search '{query}'")
            raise mapped from exc

        for entry in entries:
            if entry.get("is_live") or entry.get("live_status") in ("is_live", "is_upcoming"):
                continue
            duration = int(entry.get("duration") or 0)
            if duration <= 0 or duration > self.max_track_seconds:
                continue
            video_id = entry.get("id")
            if not video_id:
                continue

            self._record_success()
            self.resolve_latencies.append((time.perf_counter() - started) * 1000.0)
            return Track(
                video_id=str(video_id),
                title=entry.get("title") or "Unknown Title",
                artist=entry.get("uploader") or entry.get("channel") or entry.get("artist"),
                duration_s=duration,
                thumbnail_url=_pick_thumbnail(entry),
                requester_id=requester_id,
                origin="search",
            )

        raise NotFoundError(
            f"No suitable non-live tracks found for search '{query}'.",
            user_hint="Try searching with a different title or direct link.",
        )

    async def _search_flat(self, query: str, limit: int) -> list[dict[str, Any]]:
        """yt-dlp search without extracting each result (10 s -> about 1.5 s)."""
        async with self._semaphore:
            opts = self._get_base_opts()
            opts["noplaylist"] = True
            opts["extract_flat"] = "in_playlist"
            with StageTimer("search_ytdlp_flat", limit=limit):
                data = await asyncio.wait_for(
                    asyncio.to_thread(self._extract_sync, opts, f"ytsearch{limit}:{query}"),
                    timeout=self.timeout_seconds,
                )
        entries = data.get("entries") or []
        return [e for e in entries if isinstance(e, dict)]

    async def search_candidates(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Query yt-dlp for search candidates with metadata for matching."""
        try:
            return await self._search_flat(query, limit)
        except Exception as exc:
            logger.debug("yt-dlp candidate search failed for %s: %s", query, exc)
            return []

    # --------------------------------------------------------------- resolve_url

    async def resolve_url(
        self, url: str, requester_id: int, max_tracks: int = 100
    ) -> tuple[Track | list[Track], bool]:
        """Resolve a direct YouTube watch or playlist URL."""
        async with self._semaphore:
            is_playlist_url = "/playlist" in url or "list=" in url

            opts = self._get_base_opts()
            if is_playlist_url:
                opts["extract_flat"] = "in_playlist"
            else:
                opts["noplaylist"] = True

            start_time = time.perf_counter()
            try:
                data = await asyncio.wait_for(
                    asyncio.to_thread(self._extract_sync, opts, url),
                    timeout=self.timeout_seconds,
                )
            except asyncio.TimeoutError as err:
                mapped = ExtractionTimeoutError()
                self._map_and_record_error(mapped, f"url '{url}'")
                raise mapped from err
            except Exception as exc:
                mapped = self._map_and_record_error(exc, f"url '{url}'")
                raise mapped from exc

            entries = data.get("entries")
            if entries is not None:
                tracks: list[Track] = []
                for entry in entries:
                    if not entry:
                        continue
                    if entry.get("is_live"):
                        continue
                    duration = int(entry.get("duration") or 0)
                    if duration > self.max_track_seconds:
                        continue

                    vid = entry.get("id")
                    if not vid:
                        continue
                    tracks.append(
                        Track(
                            video_id=vid,
                            title=entry.get("title") or "Unknown Title",
                            artist=entry.get("uploader") or entry.get("channel"),
                            duration_s=duration,
                            thumbnail_url=_pick_thumbnail(entry),
                            requester_id=requester_id,
                            origin="link",
                        )
                    )
                    if len(tracks) >= max_tracks:
                        break

                if not tracks:
                    raise NotFoundError(
                        "The playlist contains no playable tracks or is empty.",
                        user_hint="Check if the playlist is public.",
                    )

                self._record_success()
                self.resolve_latencies.append((time.perf_counter() - start_time) * 1000.0)
                return tracks, True

            vid = data.get("id")
            if not vid:
                raise NotFoundError("Could not resolve video metadata.")

            if data.get("is_live"):
                raise UnsupportedError(
                    "Live streams are not currently supported.",
                    user_hint="Yokai only streams recorded songs and video uploads.",
                )

            duration = int(data.get("duration") or 0)
            if duration > self.max_track_seconds:
                raise UnsupportedError(
                    f"Track exceeds maximum allowed duration ({self.max_track_seconds // 60}m).",
                    user_hint="Choose a track shorter than 1 hour.",
                )

            # The single-video extraction already contains the stream URL: keep it so the
            # player does not extract the same video a second time.
            stream_url = data.get("url")
            if stream_url:
                self._remember_stream(
                    vid, StreamInfo(url=stream_url, http_headers=data.get("http_headers") or {})
                )

            self._record_success()
            self.resolve_latencies.append((time.perf_counter() - start_time) * 1000.0)
            return (
                Track(
                    video_id=vid,
                    title=data.get("title") or "Unknown Title",
                    artist=data.get("uploader") or data.get("channel") or data.get("artist"),
                    duration_s=duration,
                    thumbnail_url=data.get("thumbnail"),
                    requester_id=requester_id,
                    origin="link",
                ),
                False,
            )

    # ------------------------------------------------------------------ streams

    def _cached_stream(self, video_id: str) -> Optional[StreamInfo]:
        entry = self._stream_cache.get(video_id)
        if entry is None:
            return None
        info, expires_at = entry
        if time.time() < expires_at:
            return info
        self._stream_cache.pop(video_id, None)
        return None

    def _remember_stream(self, video_id: str, info: StreamInfo) -> StreamInfo:
        info.expires_at = stream_expiry(info.url)
        if len(self._stream_cache) >= STREAM_CACHE_MAX:
            now = time.time()
            for key in [k for k, (_i, exp) in self._stream_cache.items() if exp <= now]:
                self._stream_cache.pop(key, None)
            while len(self._stream_cache) >= STREAM_CACHE_MAX:
                self._stream_cache.pop(next(iter(self._stream_cache)))
        self._stream_cache[video_id] = (info, info.expires_at)
        return info

    def invalidate_stream(self, video_id: str) -> None:
        """Forget a cached stream URL (called before a retry)."""
        self._stream_cache.pop(video_id, None)

    @staticmethod
    def _stream_profiles() -> list[Optional[dict[str, Any]]]:
        if FAST_STREAM_EXTRACTOR_ARGS:
            return [FAST_STREAM_EXTRACTOR_ARGS, None]
        return [None]

    async def get_stream(self, track: Track, *, background: bool = False) -> StreamInfo:
        """Fetch a playable stream URL, sharing one extraction between all callers."""
        cached = self._cached_stream(track.video_id)
        if cached is not None:
            logger.debug("Stream cache hit for track %s", track.video_id)
            return cached

        task = self._inflight.get(track.video_id)
        if task is None:
            task = asyncio.get_running_loop().create_task(self._extract_stream(track, background))
            self._inflight[track.video_id] = task
            task.add_done_callback(_swallow_task_exception)
            task.add_done_callback(lambda _t, vid=track.video_id: self._inflight.pop(vid, None))
        # shield: one caller timing out or being cancelled must not kill the shared extraction
        return await asyncio.shield(task)

    async def _extract_stream(self, track: Track, background: bool) -> StreamInfo:
        semaphore = self._background_semaphore if background else self._semaphore
        async with semaphore:
            cached = self._cached_stream(track.video_id)
            if cached is not None:
                return cached

            failure: Optional[Exception] = None
            data: dict[str, Any] = {}
            with StageTimer("stream_extract", video=track.video_id, background=background):
                for profile in self._stream_profiles():
                    opts = self._get_base_opts()
                    opts["noplaylist"] = True
                    if profile is not None:
                        opts["extractor_args"] = profile
                    try:
                        data = await asyncio.wait_for(
                            asyncio.to_thread(self._extract_sync, opts, track.url),
                            timeout=self.timeout_seconds,
                        )
                    except asyncio.TimeoutError:
                        failure = ExtractionTimeoutError()
                        continue
                    except Exception as exc:
                        failure = exc
                        continue
                    if data.get("url"):
                        failure = None
                        break
                    failure = BotCheckError(
                        "Failed to acquire direct audio stream format from YouTube.",
                        user_hint="YouTube may have throttled or format-restricted this track.",
                    )

            if failure is not None:
                mapped = self._map_and_record_error(failure, f"stream '{track.video_id}'")
                if mapped is failure:
                    raise mapped
                raise mapped from failure

            self._record_success()
            info = StreamInfo(url=data["url"], http_headers=data.get("http_headers") or {})
            return self._remember_stream(track.video_id, info)

    def get_average_latency(self) -> Optional[float]:
        """Return rolling average resolution latency in milliseconds, or None if no data."""
        if not self.resolve_latencies:
            return None
        return sum(self.resolve_latencies) / len(self.resolve_latencies)
```

- [ ] **Step 5: Apply the Phase 1 decision**

Read `docs/perf/phase1-findings.md`, section "Decision for Task 6". If it says to set `FAST_STREAM_EXTRACTOR_ARGS`, replace `FAST_STREAM_EXTRACTOR_ARGS: Optional[dict[str, Any]] = None` in `ytdlp.py` with the exact `extractor_args` object from `docs/perf/extract-matrix.json` for the chosen profile (for example `{"youtube": {"player_client": ["android_vr"], "player_skip": ["webpage"]}}`). Otherwise leave it `None`. The default profile stays as the automatic fallback either way.

- [ ] **Step 6: Run the full suite and lint**

Run: `.venv/Scripts/python.exe -m pytest -q && .venv/Scripts/python.exe -m ruff format src tests && .venv/Scripts/python.exe -m ruff check .`
Expected: all pass (existing resolver, matcher and player tests still green, `3 xfailed`), ruff clean. If an existing test breaks because it inspects `_stream_cache` or `search_candidates`, fix the test to the new behaviour rather than the resolver, and mention it in the commit body.

- [ ] **Step 7: Commit**

```bash
git add src tests
git commit -m "perf(resolver): flat search fallback, single-flight streams, priority slots, real URL expiry" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 7: Fast search via ytmusicapi

**Files:**
- Create: `src/yokai/music/search.py`
- Test: `tests/test_search.py`

**Interfaces:**
- Consumes: `yokai.music.models.Track`; `ytmusicapi.YTMusic` (`search(query, filter=..., limit=...)`, verified result fields: `videoId`, `title`, `artists[{name}]`, `duration_seconds`, `thumbnails[{url}]`, `isAvailable`, `resultType`).
- Produces: `YtMusicSearcher(client: YTMusic | None = None, max_track_seconds: int = 3600)` with `async search(query: str, requester_id: int, limit: int = 5) -> list[Track]` (songs first, then videos, `origin="search"`, cached 10 minutes, returns `[]` on any client error) and `async warm_up() -> None`. `YtDlpResolver(searcher=...)` from Task 6 already consumes it.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_search.py`:

```python
"""Tests for the YouTube Music-backed searcher and its resolver integration."""

from __future__ import annotations

from typing import Any, Optional
from unittest.mock import patch

import pytest
import yt_dlp

from yokai.errors import NotFoundError
from yokai.music.resolvers.ytdlp import YtDlpResolver
from yokai.music.search import YtMusicSearcher


def song(video_id: str, title: str = "Song", duration: Optional[int] = 200, **extra: Any) -> dict:
    item: dict[str, Any] = {
        "resultType": "song",
        "videoId": video_id,
        "title": title,
        "artists": [{"name": "Artist A"}, {"name": "Artist B"}],
        "duration_seconds": duration,
        "thumbnails": [{"url": "https://img/small"}, {"url": "https://img/large"}],
    }
    item.update(extra)
    return item


class FakeYTMusic:
    def __init__(
        self,
        songs: Optional[list[dict]] = None,
        videos: Optional[list[dict]] = None,
        fail: bool = False,
    ) -> None:
        self.songs = songs or []
        self.videos = videos or []
        self.fail = fail
        self.calls: list[tuple[str, Optional[str], int]] = []

    def search(self, query: str, filter: Optional[str] = None, limit: int = 20) -> list[dict]:
        self.calls.append((query, filter, limit))
        if self.fail:
            raise RuntimeError("ytmusic down")
        return list(self.songs if filter == "songs" else self.videos)


@pytest.mark.asyncio
async def test_search_prefers_songs_and_maps_fields() -> None:
    client = FakeYTMusic(songs=[song("vid_song1", "Never Gonna")], videos=[song("vid_video1")])
    searcher = YtMusicSearcher(client=client)  # type: ignore[arg-type]
    tracks = await searcher.search("never gonna", requester_id=42)
    assert [t.video_id for t in tracks] == ["vid_song1"]
    track = tracks[0]
    assert track.title == "Never Gonna"
    assert track.artist == "Artist A, Artist B"
    assert track.duration_s == 200
    assert track.thumbnail_url == "https://img/large"
    assert track.requester_id == 42
    assert track.origin == "search"
    assert [c[1] for c in client.calls] == ["songs"]


@pytest.mark.asyncio
async def test_search_falls_back_to_videos_when_no_songs() -> None:
    client = FakeYTMusic(songs=[], videos=[song("vid_video1")])
    tracks = await YtMusicSearcher(client=client).search("q", 1)  # type: ignore[arg-type]
    assert [t.video_id for t in tracks] == ["vid_video1"]
    assert [c[1] for c in client.calls] == ["songs", "videos"]


@pytest.mark.asyncio
async def test_search_skips_unusable_results() -> None:
    client = FakeYTMusic(
        songs=[
            song("vid_ok1234", "Good"),
            {"resultType": "song", "title": "no id", "duration_seconds": 100},
            song("vid_nodur1", duration=None),
            song("vid_zero01", duration=0),
            song("vid_toolong", duration=99999),
            song("vid_unavail", isAvailable=False),
        ]
    )
    tracks = await YtMusicSearcher(client=client, max_track_seconds=3600).search("q", 1)  # type: ignore[arg-type]
    assert [t.video_id for t in tracks] == ["vid_ok1234"]


@pytest.mark.asyncio
async def test_search_cache_returns_copies_for_new_requester() -> None:
    client = FakeYTMusic(songs=[song("vid_cache1")])
    searcher = YtMusicSearcher(client=client)  # type: ignore[arg-type]
    first = await searcher.search("Same Query", requester_id=1)
    second = await searcher.search("same query", requester_id=2)
    assert len(client.calls) == 1
    assert first[0].requester_id == 1
    assert second[0].requester_id == 2
    assert first[0] is not second[0]


@pytest.mark.asyncio
async def test_search_swallows_client_errors() -> None:
    searcher = YtMusicSearcher(client=FakeYTMusic(fail=True))  # type: ignore[arg-type]
    assert await searcher.search("anything", requester_id=1) == []


@pytest.mark.asyncio
async def test_empty_results_are_not_cached() -> None:
    client = FakeYTMusic()
    searcher = YtMusicSearcher(client=client)  # type: ignore[arg-type]
    await searcher.search("nothing here", 1)
    await searcher.search("nothing here", 1)
    assert len(client.calls) == 4  # songs + videos, twice


@pytest.mark.asyncio
async def test_resolver_uses_searcher_without_touching_ytdlp() -> None:
    client = FakeYTMusic(songs=[song("vid_fast123", "Fast")])
    resolver = YtDlpResolver(searcher=YtMusicSearcher(client=client))  # type: ignore[arg-type]
    with patch.object(yt_dlp.YoutubeDL, "extract_info", side_effect=AssertionError("yt-dlp used")):
        track = await resolver.resolve_query("fast", requester_id=9)
    assert track.video_id == "vid_fast123"
    assert resolver.get_average_latency() is not None


@pytest.mark.asyncio
async def test_resolver_falls_back_when_search_returns_nothing() -> None:
    resolver = YtDlpResolver(searcher=YtMusicSearcher(client=FakeYTMusic()))  # type: ignore[arg-type]
    flat = {"entries": [{"id": "flat_vid1", "title": "Flat", "uploader": "U", "duration": 120}]}
    with patch.object(yt_dlp.YoutubeDL, "extract_info", return_value=flat):
        track = await resolver.resolve_query("obscure", requester_id=3)
    assert track.video_id == "flat_vid1"


@pytest.mark.asyncio
async def test_resolver_raises_not_found_when_nothing_usable_anywhere() -> None:
    resolver = YtDlpResolver(searcher=YtMusicSearcher(client=FakeYTMusic(fail=True)))  # type: ignore[arg-type]
    only_live = {"entries": [{"id": "live1234", "title": "Live", "is_live": True, "duration": 0}]}
    with patch.object(yt_dlp.YoutubeDL, "extract_info", return_value=only_live):
        with pytest.raises(NotFoundError):
            await resolver.resolve_query("live only", requester_id=3)
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_search.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'yokai.music.search'`).

- [ ] **Step 3: Implement `src/yokai/music/search.py`**

```python
"""Fast YouTube Music-backed search used by /play (and later the /search picker).

`ytmusicapi` answers in about 0.4 s once warm, while a yt-dlp `ytsearch5` takes about 10 s because
it fully extracts every result. Songs are preferred (clean audio releases); plain videos are the
fallback for things that are not on YouTube Music as songs.
"""

from __future__ import annotations

import asyncio
import collections
import dataclasses
import logging
import time
from typing import Any, Optional

import ytmusicapi

from yokai.music.models import Track

logger = logging.getLogger(__name__)

CACHE_TTL_S = 600.0
CACHE_MAX_ENTRIES = 128
SEARCH_TIMEOUT_S = 8.0
SEARCH_FILTERS = ("songs", "videos")


class YtMusicSearcher:
    """Searches YouTube Music and returns lightweight `Track` records (no stream URLs)."""

    def __init__(
        self,
        client: Optional[ytmusicapi.YTMusic] = None,
        max_track_seconds: int = 3600,
    ) -> None:
        self._client = client or ytmusicapi.YTMusic()
        self._max_track_seconds = max_track_seconds
        self._cache: collections.OrderedDict[str, tuple[float, list[Track]]] = (
            collections.OrderedDict()
        )

    async def search(self, query: str, requester_id: int, limit: int = 5) -> list[Track]:
        """Return up to `limit` playable tracks. Never raises: errors yield an empty list."""
        key = f"{limit}:{query.strip().lower()}"
        cached = self._cache_get(key)
        if cached is not None:
            return [dataclasses.replace(t, requester_id=requester_id) for t in cached]

        tracks: list[Track] = []
        for search_filter in SEARCH_FILTERS:
            try:
                raw = await asyncio.wait_for(
                    asyncio.to_thread(self._client.search, query, filter=search_filter, limit=limit),
                    timeout=SEARCH_TIMEOUT_S,
                )
            except Exception as exc:  # ytmusicapi raises many unrelated types
                logger.warning("YouTube Music %s search failed for %r: %s", search_filter, query, exc)
                continue
            tracks = self._to_tracks(raw, requester_id)
            if tracks:
                break

        if tracks:
            self._cache_put(key, tracks)
        return tracks

    async def warm_up(self) -> None:
        """Pay ytmusicapi's slow first call (about 1.5 s) at startup instead of on a user's /play."""
        try:
            await asyncio.wait_for(
                asyncio.to_thread(self._client.search, "warm up", filter="songs", limit=1),
                timeout=SEARCH_TIMEOUT_S,
            )
        except Exception as exc:
            logger.debug("Search warm-up failed: %s", exc)

    def _to_tracks(self, raw: Any, requester_id: int) -> list[Track]:
        tracks: list[Track] = []
        if not isinstance(raw, list):
            return tracks
        for item in raw:
            if not isinstance(item, dict):
                continue
            video_id = item.get("videoId")
            if not video_id or item.get("isAvailable") is False:
                continue
            duration = item.get("duration_seconds")
            if not isinstance(duration, int) or duration <= 0 or duration > self._max_track_seconds:
                continue
            artists = item.get("artists") or []
            names = [a["name"] for a in artists if isinstance(a, dict) and a.get("name")]
            thumbs = item.get("thumbnails")
            thumbnail: Optional[str] = None
            if isinstance(thumbs, list) and thumbs and isinstance(thumbs[-1], dict):
                thumbnail = thumbs[-1].get("url")
            tracks.append(
                Track(
                    video_id=str(video_id),
                    title=str(item.get("title") or "Unknown Title"),
                    artist=", ".join(names) or None,
                    duration_s=duration,
                    thumbnail_url=thumbnail,
                    requester_id=requester_id,
                    origin="search",
                )
            )
        return tracks

    def _cache_get(self, key: str) -> Optional[list[Track]]:
        entry = self._cache.get(key)
        if entry is None:
            return None
        expires_at, tracks = entry
        if time.monotonic() >= expires_at:
            del self._cache[key]
            return None
        self._cache.move_to_end(key)
        return tracks

    def _cache_put(self, key: str, tracks: list[Track]) -> None:
        self._cache[key] = (time.monotonic() + CACHE_TTL_S, tracks)
        self._cache.move_to_end(key)
        while len(self._cache) > CACHE_MAX_ENTRIES:
            self._cache.popitem(last=False)
```

- [ ] **Step 4: Verify and commit**

Run: `.venv/Scripts/python.exe -m pytest -q && .venv/Scripts/python.exe -m ruff format src tests && .venv/Scripts/python.exe -m ruff check .`
Expected: all pass (`3 xfailed`), ruff clean.

```bash
git add src tests
git commit -m "feat(search): add ytmusicapi-backed searcher and wire it into the resolver" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 8: Local audio pre-buffer

**Files:**
- Create: `src/yokai/music/buffer.py`
- Test: `tests/test_buffer.py`

**Interfaces:**
- Consumes: `yokai.music.models.StreamInfo`, `Track`.
- Produces: `AudioBuffer(directory: Path, max_bytes: int, downloader: Callable[[str, Path], Awaitable[None]] | None = None, ffmpeg: str = "ffmpeg")` with `enabled: bool`, `async start()`, `get(video_id) -> Path | None` (only finished files, refreshes LRU time), `schedule(track, stream) -> None` (idempotent, needs a running loop), `protect(video_ids: Iterable[str]) -> None`, `async wait_idle()`, `async clear()`. Files are `<video_id>.mka` (Matroska, stream copied without re-encoding).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_buffer.py`:

```python
"""Tests for the local audio pre-buffer."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Optional

import pytest

from yokai.music.buffer import AudioBuffer
from yokai.music.models import StreamInfo, Track


def make_downloader(size: int = 1000, delay: float = 0.0, calls: Optional[list[str]] = None):
    async def download(url: str, dest: Path) -> None:
        if calls is not None:
            calls.append(url)
        if delay:
            await asyncio.sleep(delay)
        dest.write_bytes(b"x" * size)

    return download


def track(video_id: str) -> Track:
    return Track(video_id=video_id, title="T", duration_s=100)


@pytest.mark.asyncio
async def test_schedule_downloads_and_get_returns_finished_file(tmp_path: Path) -> None:
    calls: list[str] = []
    buffer = AudioBuffer(tmp_path / "cache", 10_000, downloader=make_downloader(calls=calls))
    buffer.schedule(track("aaaaaaaa"), StreamInfo(url="http://s/1"))
    buffer.schedule(track("aaaaaaaa"), StreamInfo(url="http://s/1"))  # single-flight
    await buffer.wait_idle()
    path = buffer.get("aaaaaaaa")
    assert path is not None and path.read_bytes() == b"x" * 1000
    assert calls == ["http://s/1"]
    assert not list((tmp_path / "cache").glob("*.part"))


@pytest.mark.asyncio
async def test_get_ignores_unfinished_downloads(tmp_path: Path) -> None:
    buffer = AudioBuffer(tmp_path, 10_000, downloader=make_downloader(delay=0.2))
    buffer.schedule(track("aaaaaaaa"), StreamInfo(url="http://s/1"))
    await asyncio.sleep(0.05)
    assert buffer.get("aaaaaaaa") is None
    await buffer.wait_idle()
    assert buffer.get("aaaaaaaa") is not None


@pytest.mark.asyncio
async def test_unsafe_video_ids_are_never_buffered(tmp_path: Path) -> None:
    calls: list[str] = []
    buffer = AudioBuffer(tmp_path, 10_000, downloader=make_downloader(calls=calls))
    for bad in ["sp:abc123", "../evil", "..\\evil", "", "a" * 200, "has space", "ab"]:
        buffer.schedule(track(bad), StreamInfo(url="http://s/x"))
        assert buffer.get(bad) is None
    await buffer.wait_idle()
    assert calls == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_download_failure_leaves_no_files(tmp_path: Path) -> None:
    async def failing(url: str, dest: Path) -> None:
        dest.write_bytes(b"partial")
        raise OSError("disk full")

    buffer = AudioBuffer(tmp_path, 10_000, downloader=failing)
    buffer.schedule(track("aaaaaaaa"), StreamInfo(url="http://s/1"))
    await buffer.wait_idle()  # must not raise
    assert buffer.get("aaaaaaaa") is None
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_disabled_buffer_does_nothing(tmp_path: Path) -> None:
    calls: list[str] = []
    buffer = AudioBuffer(tmp_path, 0, downloader=make_downloader(calls=calls))
    assert buffer.enabled is False
    buffer.schedule(track("aaaaaaaa"), StreamInfo(url="http://s/1"))
    await buffer.wait_idle()
    assert calls == []


def _write(path: Path, size: int, mtime: float) -> None:
    path.write_bytes(b"x" * size)
    os.utime(path, (mtime, mtime))


@pytest.mark.asyncio
async def test_eviction_removes_oldest_first(tmp_path: Path) -> None:
    buffer = AudioBuffer(tmp_path, 2500)
    _write(tmp_path / "aaaaaaaa.mka", 1000, 100)
    _write(tmp_path / "bbbbbbbb.mka", 1000, 200)
    _write(tmp_path / "cccccccc.mka", 1000, 300)
    buffer._evict()
    assert sorted(p.name for p in tmp_path.glob("*.mka")) == ["bbbbbbbb.mka", "cccccccc.mka"]


@pytest.mark.asyncio
async def test_eviction_skips_protected_ids(tmp_path: Path) -> None:
    buffer = AudioBuffer(tmp_path, 2500)
    _write(tmp_path / "aaaaaaaa.mka", 1000, 100)
    _write(tmp_path / "bbbbbbbb.mka", 1000, 200)
    _write(tmp_path / "cccccccc.mka", 1000, 300)
    buffer.protect({"aaaaaaaa"})
    buffer._evict()
    assert sorted(p.name for p in tmp_path.glob("*.mka")) == ["aaaaaaaa.mka", "cccccccc.mka"]


@pytest.mark.asyncio
async def test_start_and_clear_wipe_the_directory(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "stale.mka").write_bytes(b"old")
    buffer = AudioBuffer(cache, 10_000, downloader=make_downloader(delay=5.0))
    await buffer.start()
    assert list(cache.iterdir()) == []

    buffer.schedule(track("aaaaaaaa"), StreamInfo(url="http://s/1"))
    await asyncio.sleep(0.05)
    await buffer.clear()  # cancels the in-flight download
    assert list(cache.iterdir()) == []
    assert buffer.get("aaaaaaaa") is None
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_buffer.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'yokai.music.buffer'`).

- [ ] **Step 3: Implement `src/yokai/music/buffer.py`**

```python
"""Local audio pre-buffer: upcoming tracks are downloaded so track changes are gapless.

While track N plays, the next one or two tracks are copied (not re-encoded) into `<video_id>.mka`
files. The player then hands FFmpeg a local file, which starts in a fraction of a second and never
stalls on the network. A buffer failure never blocks playback: the player falls back to the URL.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Awaitable, Callable, Iterable, Optional

from yokai.music.models import StreamInfo, Track

logger = logging.getLogger(__name__)

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{6,32}$")
DOWNLOAD_TIMEOUT_S = 120.0

Downloader = Callable[[str, Path], Awaitable[None]]


async def ffmpeg_download(url: str, dest: Path, ffmpeg: str = "ffmpeg") -> None:
    """Copy the audio stream at `url` into `dest` (Matroska container) without re-encoding."""
    proc = await asyncio.create_subprocess_exec(
        ffmpeg,
        "-nostdin",
        "-loglevel",
        "error",
        "-y",
        "-reconnect",
        "1",
        "-reconnect_streamed",
        "1",
        "-reconnect_delay_max",
        "5",
        "-i",
        url,
        "-vn",
        "-c:a",
        "copy",
        "-f",
        "matroska",
        str(dest),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=DOWNLOAD_TIMEOUT_S)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        proc.kill()
        await proc.wait()
        raise
    if proc.returncode != 0:
        detail = stderr.decode(errors="replace")[:200]
        raise RuntimeError(f"ffmpeg exited {proc.returncode}: {detail}")


class AudioBuffer:
    """Bounded on-disk cache of upcoming tracks, evicted least-recently-used first."""

    def __init__(
        self,
        directory: Path,
        max_bytes: int,
        downloader: Optional[Downloader] = None,
        ffmpeg: str = "ffmpeg",
    ) -> None:
        self.directory = directory
        self.max_bytes = max_bytes
        self._downloader: Downloader = downloader or functools.partial(ffmpeg_download, ffmpeg=ffmpeg)
        self._semaphore = asyncio.Semaphore(1)
        self._inflight: dict[str, asyncio.Task[Optional[Path]]] = {}
        self._protected: set[str] = set()

    @property
    def enabled(self) -> bool:
        return self.max_bytes > 0

    async def start(self) -> None:
        """Wipe leftovers from a previous run and make sure the directory exists."""
        await asyncio.to_thread(self._reset_directory)

    def _reset_directory(self) -> None:
        shutil.rmtree(self.directory, ignore_errors=True)
        self.directory.mkdir(parents=True, exist_ok=True)

    def path_for(self, video_id: str) -> Optional[Path]:
        """Destination path for a video, or None if the ID is not a safe YouTube-style ID."""
        if not _SAFE_ID.match(video_id):
            return None
        return self.directory / f"{video_id}.mka"

    def get(self, video_id: str) -> Optional[Path]:
        """Return the finished file for a video (and mark it recently used), or None."""
        path = self.path_for(video_id)
        if path is None or not path.is_file():
            return None
        try:
            os.utime(path)
        except OSError:
            return None
        return path

    def protect(self, video_ids: Iterable[str]) -> None:
        """Never evict these videos (the current and upcoming tracks)."""
        self._protected = set(video_ids)

    def schedule(self, track: Track, stream: StreamInfo) -> None:
        """Start downloading a track in the background. Idempotent. Needs a running loop."""
        if not self.enabled:
            return
        video_id = track.video_id
        if self.path_for(video_id) is None or video_id in self._inflight:
            return
        if self.get(video_id) is not None:
            return
        task = asyncio.get_running_loop().create_task(self._download(video_id, stream.url))
        self._inflight[video_id] = task
        task.add_done_callback(lambda _t, vid=video_id: self._inflight.pop(vid, None))

    async def _download(self, video_id: str, url: str) -> Optional[Path]:
        dest = self.path_for(video_id)
        if dest is None:
            return None
        part = dest.with_suffix(".part")
        async with self._semaphore:
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                await self._downloader(url, part)
                os.replace(part, dest)
            except asyncio.CancelledError:
                part.unlink(missing_ok=True)
                raise
            except Exception as exc:  # any failure just means "no buffer for this track"
                logger.warning("Audio pre-buffer failed for %s: %s", video_id, exc)
                part.unlink(missing_ok=True)
                return None
        self._evict()
        return dest

    def _evict(self) -> None:
        """Delete least-recently-used finished files until the cache fits `max_bytes`."""
        try:
            files = [p for p in self.directory.glob("*.mka") if p.is_file()]
            sizes = {p: p.stat().st_size for p in files}
            total = sum(sizes.values())
            for path in sorted(files, key=lambda p: p.stat().st_mtime_ns):
                if total <= self.max_bytes:
                    break
                if path.stem in self._protected:
                    continue
                path.unlink(missing_ok=True)
                total -= sizes[path]
        except OSError as exc:
            logger.warning("Audio buffer eviction failed: %s", exc)

    async def wait_idle(self) -> None:
        """Wait until no downloads are in flight (used by tests and the harness)."""
        while self._inflight:
            await asyncio.gather(*list(self._inflight.values()), return_exceptions=True)
            await asyncio.sleep(0)

    async def clear(self) -> None:
        """Cancel downloads and delete every buffered file (called on /stop)."""
        tasks = list(self._inflight.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._inflight.clear()
        await asyncio.to_thread(self._reset_directory)
```

- [ ] **Step 4: Verify and commit**

Run: `.venv/Scripts/python.exe -m pytest -q && .venv/Scripts/python.exe -m ruff format src tests && .venv/Scripts/python.exe -m ruff check .`
Expected: all pass, ruff clean.

```bash
git add src tests
git commit -m "feat(buffer): add bounded on-disk audio pre-buffer with safe IDs and LRU eviction" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 9: Rebuild `GuildPlayer`

The largest task. The public surface used by the cogs and views is preserved: `state`, `queue`, `current_track`, `voice_client`, `text_channel`, `resolver`, `volume`, `elapsed_seconds`, `is_playing`, `is_paused`, `connect`, `play_next`, `skip`, `stop`, `pause`, `resume`, `set_volume`, `maybe_prefetch`, `on_voice_member_update`. New: `buffer` constructor argument, `warm_stream(track)`.

Design invariants (each is tested):
1. Every playback intent increments `_generation`. Voice `after` callbacks carry the generation they were created in, and `_on_source_finished` ignores any callback whose generation is stale. Our own `voice_client.stop()` therefore can never advance the queue or double-record.
2. At most one `_advance_task` is alive. `_begin_advance()` bumps the generation, cancels the old task, pops the next track synchronously (so `current_track`/`state` are correct immediately), and starts the new worker. There is no lock.
3. Every await in the load path has a timeout (`STREAM_ATTEMPT_TIMEOUT_S` per attempt). A watchdog covers anything else: LOADING for more than `LOAD_WATCHDOG_S`, or "PLAYING" while the voice client is silent.
4. A track that ends early (error, or natural end more than `PREMATURE_END_MARGIN_S` before its known duration) is resumed at the elapsed offset with `-ss`, at most `MAX_RESUME_ATTEMPTS` times, then treated as an error.
5. About `PREBUILD_LEAD_S` seconds before a track ends, the next track's FFmpeg process is started ahead of time so the switch is instant.

**Files:**
- Replace: `src/yokai/music/player.py`
- Replace: `tests/test_player_reliability.py`
- Modify: `tests/test_player.py`

**Interfaces:**
- Consumes: `AudioBuffer` (Task 8: `get`, `schedule`, `protect`, `clear`), `UnmatchedTrackError` and `MusicQueue.push_front` (Task 5), `Resolver.get_stream(track, *, background=False)` and `Resolver.invalidate_stream` (Task 6), `StageTimer` (Task 1).
- Produces: `GuildPlayer(guild_id, bot, resolver, buffer: AudioBuffer | None = None)`; `warm_stream(track: Track) -> None`; `_watchdog_tick(now: float) -> None` (sync, unit-testable).

- [ ] **Step 1: Update the existing player tests for the new behaviour**

In `tests/test_player.py`:

1. In `FakeVoiceClient`, add this method after `stop`:

```python
    def finish(self, error: Optional[Exception] = None) -> None:
        """Simulate the audio source ending on its own (natural end or error)."""
        self._playing = False
        self._paused = False
        if self._after:
            callback = self._after
            self._after = None
            callback(error)
```

and add `from typing import Optional` to the imports at the top.

2. In `test_player_state_transitions_and_controls`, replace `assert resolver.stream_call_count == 1` with:

```python
    assert resolver.streamed_ids[0] == "t1"  # prefetch of t2 may already have run
```

3. In `test_player_prefetch_cache_hit`, no change is needed. In `test_maybe_prefetch`, no change is needed.

- [ ] **Step 2: Replace `tests/test_player_reliability.py` with the final suite**

```python
"""Reliability tests for GuildPlayer: races, loop modes, timeouts, resume, gapless, buffer."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.test_player import DummyAudioSource, FakeResolver, FakeVoiceClient
from yokai.errors import BotCheckError
from yokai.music import player as player_module
from yokai.music.buffer import AudioBuffer
from yokai.music.models import LoopMode, PlayerState, StreamInfo, Track
from yokai.music.player import GuildPlayer


class RecordingSource(DummyAudioSource):
    def __init__(self) -> None:
        self.cleaned = False

    def cleanup(self) -> None:
        self.cleaned = True


@pytest.fixture
def ffmpeg():
    """Replace FFmpegPCMAudio with a recorder that returns silent dummy sources."""
    created: list[RecordingSource] = []

    def factory(*_args: object, **_kwargs: object) -> RecordingSource:
        source = RecordingSource()
        created.append(source)
        return source

    with patch("discord.FFmpegPCMAudio", side_effect=factory) as mock:
        mock.created = created
        yield mock


def make_bot() -> MagicMock:
    bot = MagicMock()
    bot.config.max_queue_size = 50
    bot.config.ffmpeg_path = None
    bot.config.alone_disconnect_seconds = 120
    bot.config.idle_disconnect_seconds = 300
    bot.loop = asyncio.get_running_loop()
    bot.presence = AsyncMock()
    bot.db = AsyncMock()
    return bot


def make_player(
    resolver: Optional[FakeResolver] = None,
) -> tuple[GuildPlayer, FakeVoiceClient, MagicMock]:
    bot = make_bot()
    player = GuildPlayer(guild_id=1, bot=bot, resolver=resolver or FakeResolver())
    vc = FakeVoiceClient()
    player.voice_client = vc
    return player, vc, bot


def make_track(video_id: str, duration_s: int = 180) -> Track:
    return Track(video_id=video_id, title=f"Title {video_id}", duration_s=duration_s, requester_id=1)


async def settle() -> None:
    await asyncio.sleep(0.05)


def age_playback(player: GuildPlayer, seconds: float) -> None:
    """Pretend the current track has been playing for `seconds`."""
    player._track_start_time = time.monotonic() - seconds


def outcomes(bot: MagicMock, video_id: str) -> list[str]:
    return [
        call.kwargs["outcome"]
        for call in bot.db.record_play_event.await_args_list
        if call.kwargs["video_id"] == video_id
    ]


class HangingResolver(FakeResolver):
    """Stream lookups never finish (a stuck yt-dlp call)."""

    async def get_stream(self, track: Track, *, background: bool = False) -> StreamInfo:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


class SelectiveResolver(FakeResolver):
    """Hangs or fails for chosen video IDs, works for the rest."""

    def __init__(self, hang: frozenset[str] = frozenset(), fail: frozenset[str] = frozenset()):
        super().__init__()
        self.hang = hang
        self.fail = fail

    async def get_stream(self, track: Track, *, background: bool = False) -> StreamInfo:
        if track.video_id in self.hang:
            await asyncio.Event().wait()
        if track.video_id in self.fail:
            raise BotCheckError()
        return await super().get_stream(track, background=background)


# --------------------------------------------------------------- skip / stop races


async def test_skip_works_while_stream_load_is_stuck(ffmpeg) -> None:
    player, _vc, _bot = make_player(HangingResolver())
    player.queue.add(make_track("stuck"))
    player.queue.add(make_track("next"))

    load = asyncio.create_task(player.play_next())
    await settle()
    assert player.state == PlayerState.LOADING

    skipped = await asyncio.wait_for(player.skip(), timeout=1.0)
    assert skipped is not None and skipped.video_id == "stuck"
    await settle()
    assert player.current_track is not None and player.current_track.video_id == "next"

    await player.stop()
    await asyncio.wait_for(load, timeout=1.0)


async def test_skip_records_a_single_play_event(ffmpeg) -> None:
    player, _vc, bot = make_player()
    player.queue.add(make_track("t1"))
    player.queue.add(make_track("t2"))
    await player.play_next()
    await player.skip()
    await settle()
    assert outcomes(bot, "t1") == ["skipped"]
    assert player.current_track is not None and player.current_track.video_id == "t2"


async def test_stop_leaves_queue_empty_even_with_loop_track(ffmpeg) -> None:
    player, _vc, bot = make_player()
    player.queue.loop_mode = LoopMode.TRACK
    player.queue.add(make_track("t1"))
    await player.play_next()
    await player.stop()
    await settle()
    assert len(player.queue) == 0
    assert player.state == PlayerState.IDLE
    assert player.current_track is None
    assert outcomes(bot, "t1") == ["stopped"]


async def test_double_skip_advances_twice_and_goes_idle(ffmpeg) -> None:
    player, _vc, bot = make_player()
    player.queue.add(make_track("t1"))
    player.queue.add(make_track("t2"))
    await player.play_next()

    first = await player.skip()
    second = await player.skip()
    await settle()

    assert first is not None and first.video_id == "t1"
    assert second is not None and second.video_id == "t2"
    assert player.state == PlayerState.IDLE
    assert outcomes(bot, "t1") == ["skipped"]
    assert outcomes(bot, "t2") == ["skipped"]
    assert await player.skip() is None  # nothing left: no error


async def test_concurrent_play_next_starts_one_worker(ffmpeg) -> None:
    player, _vc, _bot = make_player()
    player.queue.add(make_track("t1"))
    player.queue.add(make_track("t2"))
    await asyncio.gather(player.play_next(), player.play_next())
    assert len(ffmpeg.created) == 1
    assert player.current_track is not None and player.current_track.video_id == "t1"


async def test_stale_voice_callback_is_ignored(ffmpeg) -> None:
    player, vc, _bot = make_player()
    for name in ("t1", "t2", "t3"):
        player.queue.add(make_track(name))
    await player.play_next()
    stale_after = vc._after

    await player.skip()
    await settle()
    assert player.current_track is not None and player.current_track.video_id == "t2"

    assert stale_after is not None
    stale_after(None)  # the old source "ends" again after we already moved on
    await settle()
    assert player.current_track is not None and player.current_track.video_id == "t2"
    assert [t.video_id for t in player.queue.upcoming] == ["t3"]


# ------------------------------------------------------------- natural end / loops


async def test_natural_end_records_completed_and_plays_next(ffmpeg) -> None:
    player, vc, bot = make_player()
    player.queue.add(make_track("t1"))
    player.queue.add(make_track("t2"))
    await player.play_next()

    age_playback(player, 180)
    vc.finish()
    await settle()

    assert outcomes(bot, "t1") == ["completed"]
    assert player.current_track is not None and player.current_track.video_id == "t2"
    assert player.state == PlayerState.PLAYING


async def test_loop_track_replays_before_other_tracks(ffmpeg) -> None:
    player, vc, _bot = make_player()
    player.queue.loop_mode = LoopMode.TRACK
    player.queue.add(make_track("t1"))
    player.queue.add(make_track("t2"))
    await player.play_next()

    age_playback(player, 180)
    vc.finish()
    await settle()

    assert player.current_track is not None and player.current_track.video_id == "t1"
    assert [t.video_id for t in player.queue.upcoming] == ["t2"]


async def test_loop_queue_moves_finished_track_to_the_end(ffmpeg) -> None:
    player, vc, _bot = make_player()
    player.queue.loop_mode = LoopMode.QUEUE
    player.queue.add(make_track("t1"))
    player.queue.add(make_track("t2"))
    await player.play_next()

    age_playback(player, 180)
    vc.finish()
    await settle()

    assert player.current_track is not None and player.current_track.video_id == "t2"
    assert [t.video_id for t in player.queue.upcoming] == ["t1"]


async def test_skip_in_loop_track_moves_on(ffmpeg) -> None:
    player, _vc, _bot = make_player()
    player.queue.loop_mode = LoopMode.TRACK
    player.queue.add(make_track("t1"))
    player.queue.add(make_track("t2"))
    await player.play_next()
    await player.skip()
    await settle()
    assert player.current_track is not None and player.current_track.video_id == "t2"


# ------------------------------------------------------------- resume and failures


async def test_premature_end_resumes_at_the_elapsed_offset(ffmpeg) -> None:
    resolver = FakeResolver()
    player, vc, bot = make_player(resolver)
    player.queue.add(make_track("t1"))
    await player.play_next()

    age_playback(player, 20)
    vc.finish()
    await settle()

    assert resolver.invalidated == ["t1"]
    assert "-ss 20" in ffmpeg.call_args_list[-1].kwargs["before_options"]
    assert outcomes(bot, "t1") == []  # not finished yet, so nothing recorded
    assert player.current_track is not None and player.current_track.video_id == "t1"
    assert player.state == PlayerState.PLAYING
    assert player.elapsed_seconds >= 20


async def test_resume_attempts_are_bounded(ffmpeg) -> None:
    player, vc, bot = make_player()
    player.queue.loop_mode = LoopMode.TRACK  # a failing track must NOT be re-queued
    player.queue.add(make_track("t1"))
    player.queue.add(make_track("t2"))
    await player.play_next()

    for _ in range(3):  # 2 resumes, then give up
        vc.finish(RuntimeError("stream died"))
        await settle()

    assert outcomes(bot, "t1") == ["error"]
    assert player.current_track is not None and player.current_track.video_id == "t2"
    assert "t1" not in [t.video_id for t in player.queue.upcoming]


async def test_load_failure_skips_to_next_and_notifies_once(ffmpeg) -> None:
    resolver = SelectiveResolver(fail=frozenset({"bad1", "bad2"}))
    player, _vc, bot = make_player(resolver)
    player.text_channel = MagicMock()
    player.text_channel.send = AsyncMock()
    for name in ("bad1", "bad2", "good"):
        player.queue.add(make_track(name))

    await player.play_next()
    await settle()

    assert player.current_track is not None and player.current_track.video_id == "good"
    assert player.state == PlayerState.PLAYING
    assert outcomes(bot, "bad1") == ["error"]
    assert outcomes(bot, "bad2") == ["error"]
    assert player.text_channel.send.await_count == 1
    assert resolver.invalidated.count("bad1") >= 1  # retried once with a fresh URL


async def test_stuck_load_times_out_and_moves_on(
    ffmpeg, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(player_module, "STREAM_ATTEMPT_TIMEOUT_S", 0.05)
    player, _vc, bot = make_player(SelectiveResolver(hang=frozenset({"slow"})))
    player.queue.add(make_track("slow"))
    player.queue.add(make_track("fast"))

    await asyncio.wait_for(player.play_next(), timeout=3.0)

    assert player.current_track is not None and player.current_track.video_id == "fast"
    assert player.state == PlayerState.PLAYING
    await settle()
    assert outcomes(bot, "slow") == ["error"]


# ------------------------------------------------------------------------ watchdog


async def test_watchdog_recovers_a_silent_voice_client(ffmpeg) -> None:
    player, vc, _bot = make_player()
    player.queue.add(make_track("t1"))
    await player.play_next()
    assert len(ffmpeg.created) == 1

    vc._playing = False  # the voice player died without calling `after`
    player._watchdog_tick(time.monotonic())
    assert len(ffmpeg.created) == 1  # one silent tick is tolerated
    player._watchdog_tick(time.monotonic())
    await settle()

    assert len(ffmpeg.created) == 2  # resumed the same track
    assert player.state == PlayerState.PLAYING
    assert player.current_track is not None and player.current_track.video_id == "t1"


async def test_watchdog_abandons_a_load_stuck_past_the_limit(ffmpeg) -> None:
    player, _vc, bot = make_player(HangingResolver())
    player.queue.add(make_track("stuck"))
    player.queue.add(make_track("next"))
    asyncio.create_task(player.play_next())
    await settle()
    assert player.state == PlayerState.LOADING

    player._loading_since = time.monotonic() - (player_module.LOAD_WATCHDOG_S + 5)
    player._watchdog_tick(time.monotonic())
    await settle()

    assert player.current_track is not None and player.current_track.video_id == "next"
    assert outcomes(bot, "stuck") == ["error"]
    await player.stop()


# ------------------------------------------------------------------------- gapless


async def test_next_source_is_prebuilt_and_used_without_a_gap(ffmpeg) -> None:
    player, vc, _bot = make_player()
    player.queue.add(make_track("t1", duration_s=100))
    player.queue.add(make_track("t2", duration_s=100))
    await player.play_next()
    assert len(ffmpeg.created) == 1

    age_playback(player, 95)  # 5 s left, inside PREBUILD_LEAD_S
    player._watchdog_tick(time.monotonic())
    await settle()
    assert player._prepared is not None and player._prepared.track.video_id == "t2"
    assert len(ffmpeg.created) == 2

    age_playback(player, 100)
    vc.finish()
    await settle()

    assert player.current_track is not None and player.current_track.video_id == "t2"
    assert len(ffmpeg.created) == 2  # the pre-built process was reused, none spawned at the switch


async def test_stale_prepared_source_is_discarded(ffmpeg) -> None:
    player, _vc, _bot = make_player()
    player.queue.add(make_track("t1", duration_s=100))
    player.queue.add(make_track("t2", duration_s=100))
    await player.play_next()
    age_playback(player, 95)
    player._watchdog_tick(time.monotonic())
    await settle()
    prebuilt = ffmpeg.created[1]
    assert prebuilt.cleaned is False

    player.queue.clear()  # /clear while a pre-built FFmpeg process is waiting
    player._watchdog_tick(time.monotonic())

    assert player._prepared is None
    assert prebuilt.cleaned is True


# ---------------------------------------------------------------------- the buffer


async def test_buffered_file_is_played_from_disk(ffmpeg, tmp_path: Path) -> None:
    async def write_file(_url: str, dest: Path) -> None:
        dest.write_bytes(b"audio")

    buffer = AudioBuffer(tmp_path / "cache", 10_000_000, downloader=write_file)
    player, vc, _bot = make_player()
    player.buffer = buffer
    player.queue.add(make_track("track_a1"))
    player.queue.add(make_track("track_b2"))
    await player.play_next()

    assert player._prefetch_task is not None
    await player._prefetch_task
    await buffer.wait_idle()
    expected = buffer.get("track_b2")
    assert expected is not None

    age_playback(player, 180)
    vc.finish()
    await settle()

    last_call = ffmpeg.call_args_list[-1]
    assert last_call.args[0] == str(expected)
    assert last_call.kwargs["before_options"] is None  # no network reconnect flags for a file


async def test_buffer_failure_does_not_block_playback(ffmpeg, tmp_path: Path) -> None:
    async def failing(_url: str, _dest: Path) -> None:
        raise OSError("disk full")

    buffer = AudioBuffer(tmp_path / "cache", 10_000_000, downloader=failing)
    player, vc, _bot = make_player()
    player.buffer = buffer
    player.queue.add(make_track("track_a1"))
    player.queue.add(make_track("track_b2"))
    await player.play_next()
    assert player._prefetch_task is not None
    await player._prefetch_task
    await buffer.wait_idle()

    age_playback(player, 180)
    vc.finish()
    await settle()

    assert player.current_track is not None and player.current_track.video_id == "track_b2"
    assert ffmpeg.call_args_list[-1].args[0].startswith("http://stream.test/")


async def test_stop_clears_the_buffer(ffmpeg, tmp_path: Path) -> None:
    buffer = AudioBuffer(tmp_path / "cache", 10_000_000)
    buffer.clear = AsyncMock()  # type: ignore[method-assign]
    player, _vc, _bot = make_player()
    player.buffer = buffer
    player.queue.add(make_track("t1"))
    await player.play_next()
    await player.stop()
    buffer.clear.assert_awaited_once()
```

- [ ] **Step 3: Run the new tests to confirm they fail against the old player**

Run: `.venv/Scripts/python.exe -m pytest tests/test_player_reliability.py -q`
Expected: many FAIL/ERROR (the old `GuildPlayer` has no `buffer`, `_watchdog_tick`, `_prepared`, or generation logic). This is the red state.

- [ ] **Step 4: Replace `src/yokai/music/player.py`**

```python
"""Guild audio player: generation-tagged state machine, watchdog and gapless pre-spawn.

How races are avoided (see docs/superpowers/plans/2026-09-24-music-core-plan.md, Task 9):

* Every playback intent (start, skip, stop, resume) increments `_generation`. The voice
  `after` callback carries the generation it was created in, and a callback from an older
  generation is ignored, so our own `voice_client.stop()` can never advance the queue twice.
* At most one `_advance_task` is alive. It is cancellable, so skip and stop always work even
  when a stream lookup is stuck. There is no lock.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Coroutine, Optional

import discord

from yokai.errors import UnmatchedTrackError, VoiceError, YokaiError
from yokai.music.buffer import AudioBuffer
from yokai.music.models import LoopMode, MusicQueue, PlayerState, StreamInfo, Track
from yokai.music.resolvers.base import Resolver
from yokai.music.timing import StageTimer

if TYPE_CHECKING:
    from yokai.bot import YokaiBot

logger = logging.getLogger(__name__)

STREAM_ATTEMPT_TIMEOUT_S = 20.0  # one stream lookup; a track gets two attempts
LOAD_WATCHDOG_S = 60.0  # backstop: LOADING this long means something is wedged
WATCHDOG_INTERVAL_S = 2.0
WATCHDOG_SILENT_TICKS = 2  # consecutive "PLAYING but voice idle" ticks before recovery
PREBUILD_LEAD_S = 8  # start the next FFmpeg this many seconds before the track ends
PREMATURE_END_MARGIN_S = 10  # ending this far before the known duration counts as cut off
MAX_RESUME_ATTEMPTS = 2
PREFETCH_DEPTH = 2  # how many upcoming tracks get their stream URL and buffer file ready
FFMPEG_BEFORE_OPTIONS = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"


@dataclass(slots=True)
class _Prepared:
    """An FFmpeg-backed source built ahead of time for a specific queued track."""

    track: Track
    source: discord.PCMVolumeTransformer


class GuildPlayer:
    """Manages audio playback, voice connection, queue, and timers for a single guild."""

    def __init__(
        self,
        guild_id: int,
        bot: YokaiBot,
        resolver: Resolver,
        buffer: Optional[AudioBuffer] = None,
    ) -> None:
        self.guild_id = guild_id
        self.bot = bot
        self.resolver = resolver
        self.buffer = buffer
        self.queue = MusicQueue(max_size=bot.config.max_queue_size)

        self.state: PlayerState = PlayerState.IDLE
        self.current_track: Optional[Track] = None
        self.voice_client: Optional[discord.VoiceClient] = None
        self.text_channel: Optional[discord.abc.Messageable] = None

        # Playback volume (0.0 to 1.0)
        self.volume: float = 0.5
        self._current_source: Optional[discord.PCMVolumeTransformer] = None

        # Elapsed timing (monotonic clock minus paused time, plus a resume offset)
        self._track_start_time: float = 0.0
        self._paused_duration: float = 0.0
        self._pause_start_time: float = 0.0
        self._track_offset: int = 0

        # Concurrency control
        self._generation: int = 0
        self._advance_task: Optional[asyncio.Task[None]] = None
        self._watchdog_task: Optional[asyncio.Task[None]] = None
        self._background: set[asyncio.Task[Any]] = set()
        self._loading_since: float = 0.0
        self._silent_ticks: int = 0
        self._resume_attempts: int = 0
        self._last_finish_at: Optional[float] = None
        self._failure_notified: bool = False

        # Gapless: an FFmpeg source already started for the head of the queue
        self._prepared: Optional[_Prepared] = None
        self._prebuilding: bool = False

        # Prefetch cache: (video_id, StreamInfo) for the head of the queue
        self._prefetched: Optional[tuple[str, StreamInfo]] = None
        self._prefetch_task: Optional[asyncio.Task[None]] = None

        # Disconnect timers
        self._alone_timer: Optional[asyncio.TimerHandle] = None
        self._idle_timer: Optional[asyncio.TimerHandle] = None

    # ------------------------------------------------------------------ properties

    @property
    def is_playing(self) -> bool:
        return self.state == PlayerState.PLAYING and self.voice_client is not None

    @property
    def is_paused(self) -> bool:
        return self.state == PlayerState.PAUSED

    @property
    def elapsed_seconds(self) -> int:
        """Elapsed playback time in seconds, accounting for pauses and resume offsets."""
        if self.state in (PlayerState.IDLE, PlayerState.LOADING):
            return 0
        now = self._pause_start_time if self.state == PlayerState.PAUSED else time.monotonic()
        elapsed = now - self._track_start_time - self._paused_duration
        return max(0, int(elapsed) + self._track_offset)

    # ------------------------------------------------------------------ connection

    async def connect(self, channel: discord.VoiceChannel) -> discord.VoiceClient:
        """Connect to the specified voice channel or move to it if already connected."""
        self._cancel_alone_timer()
        self._cancel_idle_timer()

        if self.voice_client is not None and self.voice_client.is_connected():
            if self.voice_client.channel.id != channel.id:
                logger.info(
                    "Moving voice connection from #%s to #%s",
                    self.voice_client.channel.name,
                    channel.name,
                )
                await self.voice_client.move_to(channel)
            return self.voice_client

        logger.info("Connecting to voice channel #%s (%d)", channel.name, channel.id)
        try:
            with StageTimer("voice_connect", channel=channel.id):
                self.voice_client = await channel.connect(reconnect=True, timeout=20.0)
            await self.bot.presence.set_active()
        except Exception as exc:
            logger.error("Failed to connect to voice channel #%s: %s", channel.name, exc)
            raise VoiceError(f"Could not connect to voice channel #{channel.name}: {exc}") from exc

        self._ensure_watchdog()
        # Leave again if nothing ever gets queued; starting playback cancels this timer.
        self._start_idle_timer()
        return self.voice_client

    # --------------------------------------------------------------------- helpers

    def _spawn(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        """Run a coroutine in the background, keeping a reference until it finishes."""
        task = asyncio.get_running_loop().create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        return task

    @staticmethod
    def _discard_source(source: discord.AudioSource) -> None:
        try:
            source.cleanup()
        except Exception as exc:
            logger.debug("Error cleaning up audio source: %s", exc)

    # ------------------------------------------------------------ starting playback

    async def play_next(self) -> None:
        """Start playback if idle, or wait for the advance already in flight."""
        task = self._advance_task
        if task is not None and not task.done():
            await asyncio.wait({task})
            return
        if self.state in (PlayerState.PLAYING, PlayerState.PAUSED):
            return
        await asyncio.wait({self._begin_advance()})

    def _pop_next_track(self) -> Optional[Track]:
        """Pop the next track and make it current (synchronously, so state is never stale)."""
        track = self.queue.pop_next()
        self.current_track = track
        self._resume_attempts = 0
        if track is not None:
            self.state = PlayerState.LOADING
            self._loading_since = time.monotonic()
            self._silent_ticks = 0
        return track

    def _begin_advance(self, resume: Optional[tuple[Track, int]] = None) -> asyncio.Task[None]:
        """Invalidate in-flight work and start the single worker that plays the next track."""
        self._generation += 1
        generation = self._generation
        if self._advance_task is not None and not self._advance_task.done():
            self._advance_task.cancel()
        self._cancel_idle_timer()

        if resume is not None:
            track: Optional[Track] = resume[0]
            start_s = resume[1]
        else:
            track = self._pop_next_track()
            start_s = 0
            if track is None:
                self._enter_idle()

        self._advance_task = asyncio.get_running_loop().create_task(
            self._advance_worker(generation, track, start_s)
        )
        return self._advance_task

    def _enter_idle(self) -> None:
        logger.info("Queue is empty. Player entering IDLE state.")
        self.state = PlayerState.IDLE
        self.current_track = None
        self._current_source = None
        self._start_idle_timer()

    async def _advance_worker(
        self, generation: int, track: Optional[Track], start_s: int
    ) -> None:
        """Load `track` (or, on failure, the following ones) and start playing it."""
        source: Optional[discord.PCMVolumeTransformer] = None
        try:
            while track is not None and generation == self._generation:
                try:
                    with StageTimer("track_load", video=track.video_id, resume_s=start_s):
                        source = self._take_prepared(track) if start_s == 0 else None
                        if source is None:
                            source = await self._prepare_source(track, start_s)
                except Exception as exc:
                    if generation != self._generation:
                        return
                    await self._on_load_failed(track, exc)
                    if generation != self._generation:
                        return
                    track, start_s = self._pop_next_track(), 0
                    if track is None:
                        self._enter_idle()
                    continue

                if generation != self._generation:
                    return  # superseded while loading; `finally` discards the source
                ready, source = source, None
                self._play_source(generation, track, ready, start_s)
                return
        finally:
            if source is not None:
                self._discard_source(source)

    async def _ensure_matched(self, track: Track) -> None:
        """Lazily match an imported Spotify track to a YouTube video just before it is needed."""
        if not (track.is_pending_match and track.spotify_meta):
            return
        matcher = getattr(self.bot, "matcher", None)
        matched = (
            await matcher.match_track(track.spotify_meta, track.requester_id) if matcher else None
        )
        if matched is None:
            raise UnmatchedTrackError(track.spotify_meta.title)
        track.video_id = matched.video_id
        track.title = matched.title
        track.artist = matched.artist
        track.duration_s = matched.duration_s
        track.thumbnail_url = matched.thumbnail_url
        track.origin = "import"
        track.is_pending_match = False

    async def _stream_target(self, track: Track) -> tuple[str, bool]:
        """Return (FFmpeg input, is_local_file): buffer file, prefetched URL, or fresh URL."""
        if self.buffer is not None:
            path = self.buffer.get(track.video_id)
            if path is not None:
                return str(path), True
        prefetched = self._prefetched
        if prefetched is not None and prefetched[0] == track.video_id:
            self._prefetched = None
            expires_at = prefetched[1].expires_at
            if expires_at is None or expires_at > time.time():
                return prefetched[1].url, False
        info = await asyncio.wait_for(
            self.resolver.get_stream(track), timeout=STREAM_ATTEMPT_TIMEOUT_S
        )
        return info.url, False

    def _build_source(
        self, target: str, is_file: bool, start_s: int
    ) -> discord.PCMVolumeTransformer:
        ffmpeg_exec = str(self.bot.config.ffmpeg_path) if self.bot.config.ffmpeg_path else "ffmpeg"
        before = "" if is_file else FFMPEG_BEFORE_OPTIONS
        if start_s > 0:
            before = f"{before} -ss {start_s}".strip()
        source = discord.FFmpegPCMAudio(
            target,
            executable=ffmpeg_exec,
            before_options=before or None,
            options="-vn",
        )
        return discord.PCMVolumeTransformer(source, volume=self.volume)

    async def _prepare_source(self, track: Track, start_s: int) -> discord.PCMVolumeTransformer:
        """Match if needed, find a stream, start FFmpeg. Two attempts, each time-limited."""
        await self._ensure_matched(track)
        error: Exception = RuntimeError("no stream attempt was made")
        for attempt in (1, 2):
            try:
                target, is_file = await self._stream_target(track)
                return self._build_source(target, is_file, start_s)
            except Exception as exc:
                error = exc
                logger.warning("Stream attempt %d failed for %s: %s", attempt, track.video_id, exc)
                self._prefetched = None
                self.resolver.invalidate_stream(track.video_id)
        raise error

    def _take_prepared(self, track: Track) -> Optional[discord.PCMVolumeTransformer]:
        prepared, self._prepared = self._prepared, None
        if prepared is None:
            return None
        if prepared.track is track:
            return prepared.source
        self._discard_source(prepared.source)
        return None

    def _play_source(
        self,
        generation: int,
        track: Track,
        source: discord.PCMVolumeTransformer,
        start_s: int,
    ) -> None:
        vc = self.voice_client
        if vc is None or not vc.is_connected():
            logger.warning("Voice client disconnected right before play()")
            self._discard_source(source)
            self._enter_idle()
            return

        loop = asyncio.get_running_loop()

        def _after(error: Optional[Exception]) -> None:
            try:
                loop.call_soon_threadsafe(self._on_source_finished, generation, track, error)
            except RuntimeError:
                pass  # event loop already closed (shutdown)

        self._current_source = source
        self._track_start_time = time.monotonic()
        self._paused_duration = 0.0
        self._pause_start_time = 0.0
        self._track_offset = start_s
        vc.play(source, after=_after)
        self.state = PlayerState.PLAYING
        self._silent_ticks = 0
        self._failure_notified = False

        if self._last_finish_at is not None:
            gap_ms = (time.perf_counter() - self._last_finish_at) * 1000.0
            logger.info("stage=track_gap ms=%.0f status=ok video=%s", gap_ms, track.video_id)
            self._last_finish_at = None
        logger.info("Playback started: %s (%s)", track.title, track.video_id)
        self._trigger_prefetch()

    # ----------------------------------------------------------- track end handling

    def _retire_current(self, outcome: str, listened: Optional[int] = None) -> Optional[Track]:
        """Record how the current track ended and apply loop rules. Synchronous."""
        track = self.current_track
        if track is None:
            return None
        if listened is None:
            listened = self.elapsed_seconds
        self.current_track = None
        self._current_source = None
        self._spawn(self._record_event(track, outcome=outcome, listened_s=listened))
        self.queue.record_history(track)

        mode = self.queue.loop_mode
        if mode == LoopMode.TRACK and outcome == "completed":
            self.queue.push_front(track)
        elif mode == LoopMode.QUEUE and outcome in ("completed", "skipped"):
            self.queue.add(track)
        return track

    def _on_source_finished(
        self, generation: int, track: Track, error: Optional[BaseException]
    ) -> None:
        """Voice `after` callback, delivered on the event loop. Ignores stale generations."""
        if generation != self._generation or self.current_track is not track:
            return

        listened = self.elapsed_seconds
        premature = (
            error is None
            and track.duration_s > 0
            and listened < track.duration_s - PREMATURE_END_MARGIN_S
        )
        if error is not None:
            logger.error("Track %s ended with error: %s", track.video_id, error)

        if (error is not None or premature) and self._resume_attempts < MAX_RESUME_ATTEMPTS:
            self._resume_attempts += 1
            logger.warning(
                "Track %s ended early at %ds of %ds; resuming (attempt %d).",
                track.video_id,
                listened,
                track.duration_s,
                self._resume_attempts,
            )
            self.resolver.invalidate_stream(track.video_id)
            self._generation += 1
            generation = self._generation
            self.state = PlayerState.LOADING
            self._loading_since = time.monotonic()
            if self._advance_task is not None and not self._advance_task.done():
                self._advance_task.cancel()
            self._advance_task = asyncio.get_running_loop().create_task(
                self._advance_worker(generation, track, listened)
            )
            return

        self._last_finish_at = time.perf_counter()
        outcome = "error" if error is not None or premature else "completed"
        self._retire_current(outcome, listened=listened)
        self._begin_advance()

    async def _on_load_failed(self, track: Track, exc: BaseException) -> None:
        logger.error("Could not load %s (%s): %s", track.title, track.video_id, exc)
        self._retire_current("error", listened=0)
        await self._notify_failure(track, exc)

    async def _notify_failure(self, track: Track, exc: BaseException) -> None:
        """Tell the channel once per failure burst (a successful track resets the burst)."""
        if self._failure_notified or self.text_channel is None:
            return
        self._failure_notified = True

        from yokai.ui.embeds import EmbedFactory

        reason = (
            exc.message if isinstance(exc, YokaiError) else "The audio stream could not be loaded."
        )
        description = f"Skipping **{track.title}**. {reason}"
        if getattr(self.resolver, "is_degraded", False) is True:
            description += "\nYouTube is giving me trouble. The owner can check `/diag`."
        embed = EmbedFactory.warning(
            title="Track Skipped", description=description, context="Playback"
        )
        try:
            await self.text_channel.send(embed=embed)
        except discord.HTTPException as send_err:
            logger.debug("Could not send failure notice: %s", send_err)

    async def _record_event(self, track: Track, outcome: str, listened_s: int) -> None:
        """Log playback event to SQLite play_events table (YouTube-derived fields only)."""
        if track.is_pending_match or track.video_id.startswith("sp:"):
            logger.debug(
                "Skipping play_events logging for unmatched/Spotify entity %s", track.video_id
            )
            return
        try:
            await self.bot.db.record_play_event(
                user_id=track.requester_id,
                video_id=track.video_id,
                title=track.title,
                artist=track.artist,
                duration_s=track.duration_s,
                origin=track.origin,
                outcome=outcome,
                listened_s=listened_s,
            )
        except Exception as exc:
            logger.error("Failed to record play event for %s: %s", track.video_id, exc)

    # ------------------------------------------------------------ prefetch / buffer

    def warm_stream(self, track: Track) -> None:
        """Start extracting a track's stream URL now so playback can join the work in flight."""
        if track.is_pending_match:
            return

        async def _warm() -> None:
            try:
                await self.resolver.get_stream(track)
            except Exception as exc:
                logger.debug("Stream warm-up failed for %s: %s", track.video_id, exc)

        self._spawn(_warm())

    def maybe_prefetch(self) -> None:
        """Trigger prefetching of upcoming tracks if playback is currently active."""
        if self.is_playing:
            self._trigger_prefetch()

    def _trigger_prefetch(self) -> None:
        upcoming = list(self.queue.upcoming[:PREFETCH_DEPTH])
        if not upcoming:
            return
        if self._prefetch_task is not None and not self._prefetch_task.done():
            self._prefetch_task.cancel()
        self._prefetch_task = asyncio.get_running_loop().create_task(self._do_prefetch(upcoming))

    async def _do_prefetch(self, tracks: list[Track]) -> None:
        if self.buffer is not None:
            keep = {t.video_id for t in tracks}
            if self.current_track is not None:
                keep.add(self.current_track.video_id)
            self.buffer.protect(keep)
        for index, track in enumerate(tracks):
            try:
                await self._ensure_matched(track)
                info = await self.resolver.get_stream(track, background=True)
            except Exception as exc:
                logger.debug("Prefetch failed for %s: %s", track.video_id, exc)
                continue
            if index == 0:
                self._prefetched = (track.video_id, info)
            if self.buffer is not None:
                self.buffer.schedule(track, info)
            logger.debug("Prefetched %s", track.video_id)

    def _maybe_prebuild_next(self) -> None:
        """Start the next track's FFmpeg process shortly before the current one ends."""
        track = self.current_track
        if (
            track is None
            or track.duration_s <= 0
            or self._prebuilding
            or self._prepared is not None
            or self.queue.loop_mode == LoopMode.TRACK
        ):
            return
        if track.duration_s - self.elapsed_seconds > PREBUILD_LEAD_S:
            return
        upcoming = self.queue.peek_next()
        if upcoming is None:
            return
        self._prebuilding = True
        self._spawn(self._prebuild_next(upcoming, self._generation))

    async def _prebuild_next(self, upcoming: Track, generation: int) -> None:
        try:
            source = await self._prepare_source(upcoming, 0)
        except Exception as exc:
            logger.debug("Pre-build of %s failed: %s", upcoming.video_id, exc)
            return
        finally:
            self._prebuilding = False
        if (
            generation != self._generation
            or self.queue.peek_next() is not upcoming
            or self._prepared is not None
        ):
            self._discard_source(source)
            return
        self._prepared = _Prepared(upcoming, source)

    # --------------------------------------------------------------------- watchdog

    def _ensure_watchdog(self) -> None:
        if self._watchdog_task is None or self._watchdog_task.done():
            self._watchdog_task = asyncio.get_running_loop().create_task(self._watchdog_loop())

    async def _watchdog_loop(self) -> None:
        while True:
            await asyncio.sleep(WATCHDOG_INTERVAL_S)
            try:
                self._watchdog_tick(time.monotonic())
            except Exception:
                logger.exception("Player watchdog tick failed")

    def _watchdog_tick(self, now: float) -> None:
        """One synchronous health check: stuck loads, silent voice, stale/pre-built sources."""
        if self.state == PlayerState.LOADING and now - self._loading_since > LOAD_WATCHDOG_S:
            logger.error("Loading exceeded %.0fs; abandoning the current track.", LOAD_WATCHDOG_S)
            self._retire_current("error", listened=0)
            self._begin_advance()
            return

        if self._prepared is not None and self._prepared.track is not self.queue.peek_next():
            stale, self._prepared = self._prepared, None
            self._discard_source(stale.source)

        vc = self.voice_client
        track = self.current_track
        if self.state != PlayerState.PLAYING or vc is None or track is None:
            self._silent_ticks = 0
            return

        if not vc.is_playing() and not vc.is_paused():
            self._silent_ticks += 1
            if self._silent_ticks >= WATCHDOG_SILENT_TICKS:
                self._silent_ticks = 0
                logger.warning("Voice went silent during %s; recovering.", track.video_id)
                self._on_source_finished(
                    self._generation, track, RuntimeError("voice client went silent")
                )
            return

        self._silent_ticks = 0
        self._maybe_prebuild_next()

    # --------------------------------------------------------------------- controls

    def pause(self) -> bool:
        """Pause playback."""
        if self.state == PlayerState.PLAYING and self.voice_client:
            self.voice_client.pause()
            self.state = PlayerState.PAUSED
            self._pause_start_time = time.monotonic()
            return True
        return False

    def resume(self) -> bool:
        """Resume playback."""
        if self.state == PlayerState.PAUSED and self.voice_client:
            self.voice_client.resume()
            self.state = PlayerState.PLAYING
            self._paused_duration += time.monotonic() - self._pause_start_time
            return True
        return False

    async def skip(self) -> Optional[Track]:
        """Skip the current track (even one that is still loading) and return it."""
        skipped = self._retire_current("skipped")
        if skipped is None:
            return None
        vc = self.voice_client
        if vc is not None and (vc.is_playing() or vc.is_paused()):
            vc.stop()  # its `after` callback is stale by the time it runs: _begin_advance bumps
        self._begin_advance()
        return skipped

    async def stop(self) -> None:
        """Stop playback, clear the queue, disconnect voice, wipe the buffer, reset presence."""
        logger.info("Stopping player and clearing queue.")
        self._generation += 1  # every pending callback and worker is now stale
        if self._advance_task is not None and not self._advance_task.done():
            self._advance_task.cancel()
        self._advance_task = None

        track = self.current_track
        if track is not None:
            self._spawn(self._record_event(track, outcome="stopped", listened_s=self.elapsed_seconds))
        self.current_track = None
        self._current_source = None
        self.queue.clear()
        self.state = PlayerState.IDLE

        self._prefetched = None
        if self._prefetch_task is not None and not self._prefetch_task.done():
            self._prefetch_task.cancel()
        prepared, self._prepared = self._prepared, None
        if prepared is not None:
            self._discard_source(prepared.source)
        self._prebuilding = False

        self._cancel_alone_timer()
        self._cancel_idle_timer()
        if self._watchdog_task is not None and not self._watchdog_task.done():
            self._watchdog_task.cancel()
        self._watchdog_task = None

        vc, self.voice_client = self.voice_client, None
        if vc is not None:
            if vc.is_playing() or vc.is_paused():
                vc.stop()
            if vc.is_connected():
                await vc.disconnect(force=True)

        if self.buffer is not None:
            await self.buffer.clear()
        await self.bot.presence.set_idle()

    def set_volume(self, level: int) -> int:
        """Set volume level (0-100) and adjust active and pre-built transformers."""
        clamped = min(max(level, 0), 100)
        self.volume = clamped / 100.0
        if self._current_source:
            self._current_source.volume = self.volume
        if self._prepared is not None:
            self._prepared.source.volume = self.volume
        return clamped

    # ------------------------------------------------------------ voice occupancy

    def on_voice_member_update(self) -> None:
        """Evaluate channel occupancy to start or cancel alone disconnect timer."""
        if not self.voice_client or not self.voice_client.is_connected():
            return

        channel = self.voice_client.channel
        if not isinstance(channel, discord.VoiceChannel):
            return

        humans = [m for m in channel.members if not m.bot]
        if not humans:
            self._start_alone_timer()
        else:
            self._cancel_alone_timer()

    def _start_alone_timer(self) -> None:
        """Start alone disconnect timer if not already running."""
        if self._alone_timer is None:
            delay = float(self.bot.config.alone_disconnect_seconds)
            logger.info(
                "Alone in voice channel #%s. Disconnect timer set (%ds).",
                self.voice_client.channel.name if self.voice_client else "?",
                delay,
            )
            self._alone_timer = self.bot.loop.call_later(
                delay,
                lambda: asyncio.create_task(self._on_alone_timeout()),
            )

    def _cancel_alone_timer(self) -> None:
        if self._alone_timer:
            self._alone_timer.cancel()
            self._alone_timer = None

    async def _on_alone_timeout(self) -> None:
        logger.info("Alone timer expired. Auto-leaving voice channel.")
        self._alone_timer = None
        await self.stop()

    def _start_idle_timer(self) -> None:
        """Start idle disconnect timer if queue is empty."""
        if self._idle_timer is None and self.queue.is_empty:
            delay = float(self.bot.config.idle_disconnect_seconds)
            logger.info("Player idle with empty queue. Disconnect timer set (%ds).", delay)
            self._idle_timer = self.bot.loop.call_later(
                delay,
                lambda: asyncio.create_task(self._on_idle_timeout()),
            )

    def _cancel_idle_timer(self) -> None:
        if self._idle_timer:
            self._idle_timer.cancel()
            self._idle_timer = None

    async def _on_idle_timeout(self) -> None:
        logger.info("Idle timer expired. Auto-leaving voice channel.")
        self._idle_timer = None
        await self.stop()
```

- [ ] **Step 5: Run the player tests, then the whole suite**

Run: `.venv/Scripts/python.exe -m pytest tests/test_player.py tests/test_player_reliability.py tests/test_recommend.py -q`
Expected: all pass. If one fails, fix `player.py`, not the test, unless the test itself contradicts an invariant listed above (then stop and report to the owner).

Run: `.venv/Scripts/python.exe -m pytest -q && .venv/Scripts/python.exe -m ruff format src tests && .venv/Scripts/python.exe -m ruff check .`
Expected: everything passes with no xfails left, ruff clean. `tests/test_code_standards.py` must stay green (the failure notice uses `send(embed=...)`, not a raw string).

- [ ] **Step 6: Commit**

```bash
git add src tests
git commit -m "fix(player): rebuild GuildPlayer around generation-tagged callbacks" -m "Skip and stop now work while a load is stuck, never double-record events, and never re-queue in loop mode. Adds resume-on-cut-out, a watchdog, and gapless pre-built sources." -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 10: Wire everything into the bot and make `/play` concurrent

**Files:**
- Modify: `src/yokai/bot.py`
- Modify: `src/yokai/cogs/music.py`
- Create: `tests/test_bot_wiring.py`

**Interfaces:**
- Consumes: `YtMusicSearcher` (Task 7), `AudioBuffer` (Task 8), `GuildPlayer(..., buffer=)` and `warm_stream` (Task 9), `Config.audio_buffer_mb/dir` (Task 5).
- Produces: `YokaiBot.searcher`, `YokaiBot.audio_buffer`; `/play` joins voice while it resolves the query.

- [ ] **Step 1: Write the failing test**

Create `tests/test_bot_wiring.py`:

```python
"""Tests that the bot wires the searcher, buffer and player together."""

from __future__ import annotations

from pathlib import Path

import pytest

from yokai.bot import YokaiBot
from yokai.config import Config
from yokai.health import HealthReport


def make_bot(**overrides: object) -> YokaiBot:
    config = Config(
        discord_token="fake_token",
        guild_id=123,
        owner_id=456,
        db_path=":memory:",  # type: ignore[arg-type]
        audio_buffer_dir=overrides.pop("audio_buffer_dir", Path("data/test-cache")),  # type: ignore[arg-type]
        **overrides,  # type: ignore[arg-type]
    )
    health = HealthReport(ffmpeg_found=True, ffmpeg_path="C:/ffmpeg/bin/ffmpeg.exe")
    return YokaiBot(config, health_report=health)


@pytest.mark.asyncio
async def test_resolver_uses_the_searcher() -> None:
    bot = make_bot()
    assert bot.resolver.searcher is bot.searcher


@pytest.mark.asyncio
async def test_player_gets_the_shared_buffer() -> None:
    bot = make_bot()
    player = bot.get_player(123)
    assert player.buffer is bot.audio_buffer
    assert player.resolver is bot.resolver


@pytest.mark.asyncio
async def test_buffer_can_be_disabled_by_config() -> None:
    bot = make_bot(audio_buffer_mb=0)
    assert bot.audio_buffer.enabled is False
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_bot_wiring.py -q`
Expected: FAIL (`AttributeError: 'YokaiBot' object has no attribute 'searcher'`).

- [ ] **Step 3: Edit `src/yokai/bot.py`**

1. Add `import asyncio` to the top imports, and these two imports alongside the other `yokai.music` imports:

```python
from yokai.music.buffer import AudioBuffer
from yokai.music.search import YtMusicSearcher
```

2. Replace the `self.resolver = YtDlpResolver(...)` block (four lines inside `__init__`) with:

```python
        self.searcher = YtMusicSearcher(max_track_seconds=config.max_track_seconds)
        self.resolver = YtDlpResolver(
            deno_path=config.deno_path,
            cookies_file=config.ytdlp_cookies_file,
            max_track_seconds=config.max_track_seconds,
            searcher=self.searcher,
        )
        self.audio_buffer = AudioBuffer(
            directory=config.audio_buffer_dir,
            max_bytes=config.audio_buffer_mb * 1024 * 1024,
            ffmpeg=str(config.ffmpeg_path) if config.ffmpeg_path else (self.health.ffmpeg_path or "ffmpeg"),
        )
        self._warmup_task: Optional[asyncio.Task[None]] = None
```

3. In `get_player`, change the `GuildPlayer(...)` call to:

```python
            self.players[guild_id] = GuildPlayer(
                guild_id=guild_id,
                bot=self,
                resolver=self.resolver,
                buffer=self.audio_buffer,
            )
```

4. In `setup_hook`, right after `await self.db.initialize()` add:

```python
        await self.audio_buffer.start()
        # ytmusicapi's first call is slow (about 1.5 s); pay it now, not on someone's /play
        self._warmup_task = asyncio.create_task(self.searcher.warm_up())
```

- [ ] **Step 4: Edit `src/yokai/cogs/music.py`**

1. Add `import asyncio` and `import time` to the top imports (before `import logging`), and add this helper below `logger = logging.getLogger(__name__)`:

```python
def _observe_task(task: asyncio.Task[object]) -> None:
    """Retrieve a background task's exception so asyncio does not log it as never retrieved."""
    if not task.cancelled() and task.exception() is not None:
        logger.debug("Background task failed: %s", task.exception())
```

2. In `play()`, replace this block:

```python
        await interaction.response.defer(thinking=True)
        avatar_url = str(self.bot.user.display_avatar.url) if self.bot.user else None

        # 1. Connect to caller's voice channel
        await player.connect(caller_vc)
        player.text_channel = interaction.channel

        # 2. Classify input
        classified = classify_input(query)

        if classified.kind == InputKind.UNSUPPORTED:
            err_embed = EmbedFactory.error(
                message=classified.error_message or "Unsupported input.",
                user_hint=classified.user_hint,
                bot_avatar_url=avatar_url,
            )
            await send(interaction, embed=err_embed, ephemeral=True)
            return
```

with:

```python
        await interaction.response.defer(thinking=True)
        avatar_url = str(self.bot.user.display_avatar.url) if self.bot.user else None
        request_started = time.perf_counter()

        # 1. Classify first: unsupported input must not make the bot join voice
        classified = classify_input(query)

        if classified.kind == InputKind.UNSUPPORTED:
            err_embed = EmbedFactory.error(
                message=classified.error_message or "Unsupported input.",
                user_hint=classified.user_hint,
                bot_avatar_url=avatar_url,
            )
            await send(interaction, embed=err_embed, ephemeral=True)
            return

        # 2. Join voice WHILE the query resolves (joining takes about as long as a search)
        player.text_channel = interaction.channel
        connect_task = asyncio.create_task(player.connect(caller_vc))
        connect_task.add_done_callback(_observe_task)
```

3. Replace the line `        # 4. Enqueue and start playback` (immediately above `if not is_playlist:`) with:

```python
        # 4. Start extracting the stream now, then wait for the voice connection
        if not is_playlist and player.state == PlayerState.IDLE:
            player.warm_stream(tracks_to_queue[0])
        await connect_task

        # 5. Enqueue and start playback
```

4. In the single-track idle branch, after `await player.play_next()` (the one directly under `if player.state == PlayerState.IDLE:` in the `if not is_playlist:` block), add:

```python
                logger.info(
                    "stage=play_to_audio ms=%.0f status=ok kind=single",
                    (time.perf_counter() - request_started) * 1000.0,
                )
```

- [ ] **Step 5: Run everything**

Run: `.venv/Scripts/python.exe -m pytest -q && .venv/Scripts/python.exe -m ruff format src tests && .venv/Scripts/python.exe -m ruff check .`
Expected: all pass (including `test_code_standards`), ruff clean. Also run `.venv/Scripts/python.exe -c "import yokai.bot, yokai.cogs.music"` to confirm the modules import.

- [ ] **Step 6: Commit**

```bash
git add src tests
git commit -m "feat(bot): wire searcher and buffer; join voice while /play resolves" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 11: Re-measure, update docs, checkpoint, decision gate

**Files:**
- Modify: `scripts/bench_music.py`
- Modify: `README.md`, `AGENTS.md`, `SPEC.md`, `QA.md`
- Create: `docs/perf/after-phase2.json` (generated), `docs/perf/phase2-results.md`

- [ ] **Step 1: Point the harness at the new stack and add the buffered scenarios**

In `scripts/bench_music.py`:

1. Add these imports: `import tempfile` and

```python
from yokai.music.buffer import AudioBuffer
from yokai.music.models import Track
from yokai.music.search import YtMusicSearcher
```

2. Replace `build_resolver` with:

```python
def build_resolver(deno_path: Optional[str]) -> YtDlpResolver:
    """Fresh resolver so no stream cache carries over between runs (same wiring as bot.py)."""
    return YtDlpResolver(
        deno_path=Path(deno_path) if deno_path else None,
        searcher=YtMusicSearcher(),
    )
```

3. Add this function above `run_once`:

```python
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
```

4. In `run_once`, after computing `frame_ms` and before the `return`, add a warm re-run and the buffered scenario, and include them in the result:

```python
    cold_total = (time.perf_counter() - started) * 1000.0

    warm_started = time.perf_counter()
    warm_track = await resolver.resolve_query(query, requester_id=0)  # search cache hit
    await resolver.get_stream(warm_track)  # stream cache hit
    warm_ms = (time.perf_counter() - warm_started) * 1000.0

    buffered = await buffered_once(resolver, track, ffmpeg)
```

and change the return to:

```python
    return {
        "search": (searched - started) * 1000.0,
        "stream": (streamed - searched) * 1000.0,
        "ffmpeg": frame_ms,
        "cold_play": cold_total,
        "warm_play": warm_ms,
        **buffered,
    }
```

5. Add a `--compare` option: in `main()` add `parser.add_argument("--compare", default=None, help="baseline JSON to diff against")`, and at the end of `main_async` (after the target verdict) add:

```python
    if args.compare:
        baseline = json.loads(Path(args.compare).read_text(encoding="utf-8"))["summary"]
        out(f"\n{'scenario':<12}{'baseline p50':>14}{'now p50':>10}{'change':>10}")
        for name, stats in summary.items():
            if name in baseline:
                before = baseline[name]["p50"]
                delta = (stats["p50"] - before) / before * 100.0 if before else 0.0
                out(f"{name:<12}{before:>14.0f}{stats['p50']:>10.0f}{delta:>9.0f}%")
```

- [ ] **Step 2: Run the after-measurement**

Run: `.venv/Scripts/python.exe scripts/bench_music.py --runs 5 --label after-phase2 --compare docs/perf/baseline.json`
Expected: the table now includes `warm_play`, `buffer_download`, `buffered_first_frame`; `search` p50 well under 1000 ms; a comparison table against the baseline; a PASS/FAIL line for the 1500 ms cold-play target.

- [ ] **Step 3: Write `docs/perf/phase2-results.md`**

Record from the output (no blank fields): the after-vs-baseline table (`search`, `stream`, `ffmpeg`, `cold_play`, `warm_play`, `buffer_download`, `buffered_first_frame`), the target verdict, and which `FAST_STREAM_EXTRACTOR_ARGS` value shipped (from Task 6 Step 5).

**Decision gate:** if `cold_play` p50 is above 1500 ms, do NOT try more tricks on your own. Add a section "Cold-path breakdown" with the p50 of each stage and the remaining gap, and list these options for the owner with their cost: (a) accept about the measured p50 (`/play` on anything already searched or queued is near-instant); (b) a direct innertube stream resolver with a keep-alive connection, behind the `Resolver` interface (a new component with real maintenance cost, would need approval); (c) a PO-token provider, which spec section 7.3 lists as a later fallback. The owner chooses; the next plan is written accordingly.

- [ ] **Step 4: Update the docs**

1. `.env.example`: already updated in Task 5.
2. `README.md`: in the configuration code block, after the line `RECOMMEND_COUNT=8`, add:

```
AUDIO_BUFFER_MB=500
AUDIO_BUFFER_DIR=data/cache
```

   and add one bullet to the "Features" list (match the surrounding bullet style) saying: "Fast search through YouTube Music, a local pre-buffer for gapless track changes, and self-healing playback (stuck loads time out, cut-off tracks resume)."
3. `AGENTS.md`: replace the "Playback:" line under "Locked decisions" with:
   `- Playback: yt-dlp used in-process (Python API) plus FFmpeg. All audio comes from YouTube. Free-text search goes through ytmusicapi first (yt-dlp flat search is the fallback). Upcoming tracks are pre-buffered to disk with FFmpeg (AUDIO_BUFFER_MB, 0 disables). No Lavalink. No Spotify streaming.`
4. `SPEC.md`:
   - Section 7.2, the "Plain text" row: replace with `| Plain text | Search: ytmusicapi (songs, then videos), falling back to a flat yt-dlp ytsearch5; take the first non-live result within MAX_TRACK_SECONDS. |`
   - Section 7.6: replace the bullet "Prefetch the next stream URL while the current track plays." with: "Prefetch the next two tracks' stream URLs while the current track plays, download them into the local audio buffer (AUDIO_BUFFER_MB), and start the next FFmpeg process about 8 seconds before the current track ends so the switch is gapless. Playback races are prevented with generation-tagged voice callbacks; a watchdog recovers stuck loads and silent voice clients; a track that ends early is resumed at its offset (at most twice)."
   - Section 11 environment table: add rows `| AUDIO_BUFFER_MB | 500 | Pre-buffer size cap in MB (0 disables) |` and `| AUDIO_BUFFER_DIR | data/cache | Pre-buffer folder (wiped on startup and /stop) |`.
5. `QA.md`: read the QA execution matrix at the top and add rows in the same format for: (a) `/play` a song by name and note time until audio (target under about 2 s); (b) `/play` two tracks and confirm no audible gap when the first ends; (c) `/skip` during a slow load (press it while "thinking"); (d) `/skip` twice rapidly with three tracks queued; (e) `/stop` while looping a track, then `/play` a new one and confirm the old one does not return; (f) disconnect Wi-Fi for 5 s mid-track and confirm playback resumes or skips cleanly; (g) delete `data/cache` contents mid-play and confirm playback continues. Update the "Automated Test Baseline" numbers to the current `pytest` count.

- [ ] **Step 7: Final verification, commit, tag**

Run: `.venv/Scripts/python.exe -m pytest -q && .venv/Scripts/python.exe -m ruff check . && .venv/Scripts/python.exe -m ruff format --check .`
Expected: all tests pass, no xfails, ruff and format clean.

```bash
git add scripts docs README.md AGENTS.md SPEC.md QA.md
git commit -m "docs(perf): record Phase 2 results; update docs for search, buffer and player changes" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
git tag -a checkpoint/phase2-music-core -m "Phase 2 complete: fast search, buffer, rebuilt player"
```

- [ ] **Step 8: STOP for owner review**

Report: before/after table, the target verdict (and the decision gate options if missed), the list of bugs fixed, and what is NOT verified because it needs real Discord voice: gapless switching by ear, skip during a real slow load, resume after a real network drop, and the voice connect running in parallel with search. Point the owner to the new QA rows. Do not start Phase 3 until they approve.
