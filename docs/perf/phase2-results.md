# Phase 2 results (music core)

Measured with `scripts/bench_music.py --runs 5 --label after-phase2 --compare docs/perf/baseline.json`
on commit 5fe4e2a, yt-dlp 2026.08.19, Deno on PATH, 0 failed runs out of 5.
Raw samples: `docs/perf/after-phase2.json`. Baseline: `docs/perf/baseline.json` (Phase 1, commit b32e6bd).

## Before and after (p50 ms, n = 5)

| scenario | baseline p50 | after p50 | change | after p95 |
|---|---|---|---|---|
| search | 8809 | 549 | -94% | 642 |
| stream | 1970 | 1902 | -3% | 2581 |
| ffmpeg | 305 | 255 | -16% | 278 |
| cold_play | 11221 | 2714 | -76% | 3452 |
| warm_play | n/a (new) | 0.13 | n/a | 0.17 |
| buffer_download | n/a (new) | 735 | n/a | 1496 |
| buffered_first_frame | n/a (new) | 43 | n/a | 45 |

Scenario meaning: `cold_play` is search + stream URL + first FFmpeg PCM frame on a fresh resolver
(a first-time `/play`). `warm_play` is the same query and track again with search and stream cached
(a repeat or already-prefetched track). `buffer_download` is the time to pull a stream into the
on-disk buffer (it happens in the background while the previous track plays).
`buffered_first_frame` is FFmpeg's first PCM frame when reading from the buffered file instead of a URL.

## Target verdict

Target: `cold_play` p50 <= 1500 ms. Measured 2714 ms. **FAIL** (1214 ms over).

Shipped `FAST_STREAM_EXTRACTOR_ARGS`: `None` (defaults only), as decided in Phase 1 because the fastest
usable profile beat the default by only 461 ms, below the 500 ms threshold.

## Measurement notes

- The search number assumes a warmed searcher, as in the bot (`searcher.warm_up()` in `setup_hook`).
  The harness creates one `YtMusicSearcher`, warms it once, and shares it across runs (a fresh
  `YtDlpResolver` is built per run, so the stream cache is empty each run). ytmusicapi's first call
  costs about 1.5 s, which the bot pays once at startup, not per `/play`.
- The searcher keeps its own query cache across runs. The 7 queries cycle, so with 5 runs none repeat.
  With more than 7 runs queries would repeat and hit the search cache, making `search` look faster than
  a truly new query.
- `warm_play` is sub-millisecond because both steps are in-memory cache hits; it is a floor, not a
  realistic figure for a track whose stream URL has expired.
- The same command was run twice. The first run (cold_play p50 2789 ms, search p50 636 ms, stream p50
  1738 ms) was done before the `--compare` table was wired correctly in the harness and was overwritten
  by the second run reported here. Both runs agree within about 3 percent on cold_play.

## Cold-path breakdown (decision gate: target missed)

p50 per stage of a first-time `/play`:

| stage | p50 ms | share of cold_play |
|---|---|---|
| search (ytmusicapi, warmed) | 549 | 20% |
| stream URL extraction (yt-dlp) | 1902 | 70% |
| first FFmpeg frame | 255 | 9% |
| sum of stage p50s | 2706 | (measured cold_play p50 is 2714) |

Remaining gap to the 1500 ms target: 1214 ms. Stream extraction alone (1902 ms) is above the whole
target, so no change to search or FFmpeg can reach 1500 ms; only a faster stream resolver can.
Phase 1 already showed this: extraction is dominated by yt-dlp's watch-page download and player API
round trips (about 1.7 s with defaults), and the faster client profile saved only about 0.46 s.

What the numbers say about different kinds of play:

- Queued or repeat tracks are fast. A track that is already prefetched or buffered starts in about
  43 ms (`buffered_first_frame`, FFmpeg reading the local file), and a cached search plus stream is
  effectively instant (`warm_play` 0.13 ms). The buffer download (735 ms p50) happens while the
  previous track is still playing, so track-to-track changes do not pay the cold cost.
- A first-time `/play` of something new, with nothing playing, costs about 2.7 s (p95 3.5 s). That is
  a 76% improvement over the 11.2 s baseline, almost all from the search fix (8809 ms to 549 ms).

Options for the owner (the owner chooses; the next plan is written accordingly):

- (a) Accept about 2.7 s p50 for a first-time `/play`. Cost: none. Anything already searched, queued,
  or prefetched is near-instant, and the bot joins voice while the search resolves, which should hide part of the
  wait (not measured here; it needs real Discord voice). Target of 1500 ms is not met for first-time plays.
- (b) A direct innertube stream resolver with a keep-alive connection, behind the `Resolver`
  interface. Cost: a new component with real maintenance cost (it must track YouTube's client and
  signature changes that yt-dlp currently absorbs for us), and it needs approval before it is started.
  It is the only option that targets the 1.9 s stream stage.
- (c) A PO-token provider, which spec section 7.3 lists as a later fallback. Cost: an extra moving
  part to run and keep alive; it addresses bot-check failures more than latency, so it is not expected
  to close the 1.2 s gap by itself.
