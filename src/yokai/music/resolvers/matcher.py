"""Spotify to YouTube track matching engine with pure scoring and multi-source candidate lookup."""

from __future__ import annotations

import asyncio
import difflib
import logging
import re
from typing import Any, Optional

import ytmusicapi

from yokai.music.models import Track
from yokai.music.resolvers.ytdlp import YtDlpResolver
from yokai.music.spotify.models import SpotifyTrackMeta
from yokai.storage.db import Database

logger = logging.getLogger(__name__)

ACCEPTANCE_THRESHOLD = 0.65

TRAP_KEYWORDS = [
    "live",
    "cover",
    "remix",
    "karaoke",
    "instrumental",
    "sped up",
    "slowed",
    "8d",
    "reverb",
    "acoustic",
]


def normalize_title(title: str) -> str:
    """Normalize a song title for fuzzy matching."""
    t = title.lower()

    # 1. Strip parenthesized or bracketed feature credits: (feat. ...), [ft. ...]
    t = re.sub(r"[\(\[\{]\s*(?:feat\.?|ft\.?)\s+[^)\]\}]+[\)\]\}]", "", t)
    t = re.sub(r"\b(?:feat\.?|ft\.?)\s+[^\-\(\[]+", "", t)

    # 2. Strip remastered / anniversary / deluxe / bonus tags (with optional year prefix/suffix)
    t = re.sub(
        r"[\(\[\{]\s*.*?(?:remaster(?:ed)?|anniversary|deluxe|bonus|expanded|edition|version)[^)\]\}]*[\)\]\}]",
        "",
        t,
    )
    t = re.sub(
        r"\s*-\s*.*?(?:remaster(?:ed)?|anniversary|deluxe|bonus|expanded|edition|version).*$",
        "",
        t,
    )

    # 3. Strip common YouTube video/audio descriptor tags
    t = re.sub(
        r"[\(\[\{]\s*.*?(?:official\s+)?(?:music\s+)?(?:video|audio|mv|visualizer|lyric\s+video|lyrics).*?[\)\]\}]",
        "",
        t,
    )
    t = re.sub(
        r"\b(?:official\s+)?(?:music\s+)?(?:video|audio|mv|visualizer|lyric\s+video|lyrics)\b",
        "",
        t,
    )

    # 4. Strip special punctuation (preserves unicode word characters like Kanji/Cyrillic)
    t = re.sub(r"[^\w\s]", " ", t)

    # 5. Collapse consecutive whitespace
    return re.sub(r"\s+", " ", t).strip()


def score_candidate(
    spotify_track: SpotifyTrackMeta,
    candidate_title: str,
    candidate_artist: Optional[str],
    candidate_duration_s: Optional[int],
) -> float:
    """Compute confidence score [0.0 - 1.0] for a candidate against a Spotify track."""
    # 1. Duration check
    dur_score = 0.5
    if (
        candidate_duration_s is not None
        and candidate_duration_s > 0
        and spotify_track.duration_s > 0
    ):
        diff = abs(candidate_duration_s - spotify_track.duration_s)
        if diff > 15:
            # Immediate rejection: duration difference > 15 seconds
            return 0.0
        if diff <= 3:
            dur_score = 1.0
        else:
            # 3 < diff <= 15: linear degradation
            dur_score = 1.0 - (0.05 * (diff - 3))

    # 2. Title similarity with optional artist prefix stripping
    norm_sp = normalize_title(spotify_track.title)
    norm_cand = normalize_title(candidate_title)

    title_sim = difflib.SequenceMatcher(None, norm_sp, norm_cand).ratio()

    # Try stripping artist name from candidate title if artist was matched
    cand_art_stripped = norm_cand
    for art in spotify_track.artists:
        norm_art = normalize_title(art)
        if norm_art:
            cand_art_stripped = re.sub(rf"\b{re.escape(norm_art)}\b", "", cand_art_stripped)
    cand_art_stripped = re.sub(r"\s+", " ", cand_art_stripped).strip()

    if cand_art_stripped:
        stripped_sim = difflib.SequenceMatcher(None, norm_sp, cand_art_stripped).ratio()
        title_sim = max(title_sim, stripped_sim)

    # Check substring inclusion
    if norm_sp and (norm_sp in norm_cand or (cand_art_stripped and norm_sp in cand_art_stripped)):
        title_sim = max(title_sim, 0.90)

    # 3. Artist overlap
    artist_score = 0.0
    cand_art_norm = (candidate_artist or "").lower()
    cand_title_norm = candidate_title.lower()

    if spotify_track.artists:
        for art in spotify_track.artists:
            art_clean = art.lower().strip()
            if art_clean and (art_clean in cand_art_norm or art_clean in cand_title_norm):
                artist_score = 1.0
                break
    else:
        artist_score = 0.5

    # 4. Base composite score
    base_score = (title_sim * 0.55) + (artist_score * 0.25) + (dur_score * 0.20)

    # 5. Trap keyword penalties
    sp_full = f"{spotify_track.title} {spotify_track.album_name or ''}".lower()
    cand_full = candidate_title.lower()

    penalty = 0.0
    for kw in TRAP_KEYWORDS:
        # If keyword exists in candidate but not in original Spotify track
        if re.search(rf"\b{re.escape(kw)}\b", cand_full) and not re.search(
            rf"\b{re.escape(kw)}\b", sp_full
        ):
            penalty += 0.4

    return max(0.0, min(1.0, base_score - penalty))


class TrackMatcher:
    """Matches Spotify track metadata to YouTube video streams."""

    def __init__(
        self,
        ytdlp_resolver: YtDlpResolver,
        db: Optional[Database] = None,
        threshold: float = ACCEPTANCE_THRESHOLD,
    ) -> None:
        self.ytdlp = ytdlp_resolver
        self.db = db
        self.threshold = threshold
        self._ytmusic = ytmusicapi.YTMusic()

    async def match_track(
        self, spotify_track: SpotifyTrackMeta, requester_id: int
    ) -> Optional[Track]:
        """Find the best YouTube track match for a Spotify track.

        Checks SQLite match cache first. If absent, queries ytmusicapi songs,
        falling back to yt-dlp search if scores fall below threshold.
        """
        # 1. Fast path: check functional SQLite cache
        if self.db is not None:
            cached = await self.db.get_spotify_match(spotify_track.spotify_id)
            if cached:
                cached_vid, cached_conf = cached
                logger.debug(
                    "Spotify match cache hit: %s -> %s (conf: %.2f)",
                    spotify_track.spotify_id,
                    cached_vid,
                    cached_conf,
                )
                return Track(
                    video_id=cached_vid,
                    title=spotify_track.title,
                    artist=spotify_track.artist_summary,
                    duration_s=spotify_track.duration_s,
                    thumbnail_url=spotify_track.thumbnail_url,
                    requester_id=requester_id,
                    origin="import",
                    spotify_id=spotify_track.spotify_id,
                )

        query = spotify_track.search_query
        best_candidate: Optional[tuple[str, str, Optional[str], int, float, Optional[str]]] = None

        # 2. Query ytmusicapi (songs filter)
        try:
            ytm_results = await asyncio.to_thread(self._ytmusic_search, query)
            for item in ytm_results:
                vid = item.get("videoId")
                if not vid:
                    continue
                title = item.get("title", "")
                artists = item.get("artists", [])
                artist_name = ", ".join(
                    a.get("name", "") for a in artists if isinstance(a, dict) and a.get("name")
                )
                dur_s = item.get("duration_seconds")
                thumb = None
                thumbs = item.get("thumbnails", [])
                if thumbs and isinstance(thumbs, list) and isinstance(thumbs[-1], dict):
                    thumb = thumbs[-1].get("url")

                score = score_candidate(spotify_track, title, artist_name, dur_s)
                if best_candidate is None or score > best_candidate[4]:
                    best_candidate = (vid, title, artist_name, dur_s or 0, score, thumb)
        except Exception as exc:
            logger.warning("ytmusicapi candidate search failed for %s: %s", query, exc)

        # 3. Fallback to yt-dlp search if ytmusicapi candidate didn't meet threshold
        if best_candidate is None or best_candidate[4] < self.threshold:
            try:
                ytdlp_candidates = await self._ytdlp_search(query)
                for entry in ytdlp_candidates:
                    vid = entry.get("id")
                    if not vid:
                        continue
                    title = entry.get("title", "")
                    uploader = entry.get("uploader") or entry.get("channel")
                    dur_s = entry.get("duration")
                    thumb = entry.get("thumbnail")

                    score = score_candidate(spotify_track, title, uploader, dur_s)
                    if best_candidate is None or score > best_candidate[4]:
                        best_candidate = (vid, title, uploader, dur_s or 0, score, thumb)
            except Exception as exc:
                logger.warning("yt-dlp candidate search failed for %s: %s", query, exc)

        # 4. Evaluate best score
        if best_candidate and best_candidate[4] >= self.threshold:
            vid, title, artist_name, dur_s, score, thumb = best_candidate
            logger.info(
                "Matched Spotify '%s' -> YouTube '%s' (%s, score: %.2f)",
                spotify_track.title,
                title,
                vid,
                score,
            )

            # Cache match in SQLite (functional cache only)
            if self.db is not None:
                await self.db.set_spotify_match(spotify_track.spotify_id, vid, score)

            return Track(
                video_id=vid,
                title=title or spotify_track.title,
                artist=artist_name or spotify_track.artist_summary,
                duration_s=dur_s or spotify_track.duration_s,
                thumbnail_url=thumb or spotify_track.thumbnail_url,
                requester_id=requester_id,
                origin="import",
                spotify_id=spotify_track.spotify_id,
            )

        logger.warning(
            "Failed to find confident match for Spotify track '%s' (best score: %.2f)",
            spotify_track.title,
            best_candidate[4] if best_candidate else 0.0,
        )
        return None

    def _ytmusic_search(self, query: str) -> list[dict[str, Any]]:
        """Synchronous search against ytmusicapi for song results."""
        return self._ytmusic.search(query, filter="songs", limit=5)

    async def _ytdlp_search(self, query: str) -> list[dict[str, Any]]:
        """Query yt-dlp using ytsearch5."""
        return await self.ytdlp.search_candidates(query, limit=5)
