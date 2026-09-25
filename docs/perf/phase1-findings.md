# Phase 1 findings (measured 2026-09-25T02:24Z on b32e6bd)

## Baseline latency (docs/perf/baseline.json, n = 5, yt-dlp 2026.08.19)
| scenario | p50 ms | p95 ms |
|---|---|---|
| search | 8809 | 10244 |
| stream | 1970 | 2504 |
| ffmpeg | 305 | 414 |
| cold_play | 11221 | 12765 |

cold_play p50 target of 1500 ms: FAIL (0 failed runs out of 5).

## Where the stream extraction time goes (`--breakdown`)
```
  0.19s  [youtube] Extracting URL: https://www.youtube.com/watch?v=lYBUbBu4W08
  0.19s  [youtube] lYBUbBu4W08: Downloading webpage
  1.55s  [youtube] lYBUbBu4W08: Downloading visionos player API JSON
  1.72s  [youtube] lYBUbBu4W08: Downloading m3u8 information
```
The watch-page download accounts for about 1.36 s of the 1.72 s total (0.19 s to 1.55 s).

## Fastest usable extraction profile (docs/perf/extract-matrix.json)
- Profile: client=android+skip_webpage, median 1555 ms, format 18 (muxed), plays without headers: yes
- Default profile median for comparison: 2016 ms (format 251, audio-only)
- Difference: 461 ms, below the 500 ms threshold.
- Decision for Task 6: leave FAST_STREAM_EXTRACTOR_ARGS = None

Other usable profiles: client=android 1597 ms (fmt 18, muxed), client=visionos 2048 ms (fmt 251, audio-only).
Most other client profiles failed in this run (format not available, bot check, or "page needs to be reloaded").

## Reproduced bugs
Three strict-xfail tests in tests/test_player_reliability.py reproduce: skip hangs while a load is stuck, skip records two play events, stop leaves the queue non-empty in loop-track mode.
