"""Spotify orchestrator managing provider fallback, circuit breaking, and logging."""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from yokai.errors import NotFoundError, SpotifyResolutionError
from yokai.music.spotify.base import SpotifyProvider
from yokai.music.spotify.models import SpotifyCollection, SpotifyTrackMeta

logger = logging.getLogger(__name__)

CIRCUIT_FAILURE_THRESHOLD = 3
CIRCUIT_COOLDOWN_SECONDS = 600.0  # 10 minutes


class CircuitBreaker:
    """Tracks provider failures and opens circuit after repeated consecutive errors."""

    def __init__(
        self,
        failure_threshold: int = CIRCUIT_FAILURE_THRESHOLD,
        cooldown: float = CIRCUIT_COOLDOWN_SECONDS,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.cooldown = cooldown
        self.failure_count: int = 0
        self.open_until: float = 0.0

    @property
    def is_available(self) -> bool:
        if self.open_until > 0:
            if time.time() < self.open_until:
                return False
            # Cooldown expired: half-open probe
            self.open_until = 0.0
            self.failure_count = 0
        return True

    def record_success(self) -> None:
        self.failure_count = 0
        self.open_until = 0.0

    def record_failure(self) -> None:
        self.failure_count += 1
        if self.failure_count >= self.failure_threshold:
            self.open_until = time.time() + self.cooldown
            logger.warning(
                "Circuit breaker tripped: tripping provider for %.0f seconds after %d failures",
                self.cooldown,
                self.failure_count,
            )


class SpotifyOrchestrator:
    """Coordinates Spotify providers with official-first fallback and circuit breakers."""

    def __init__(
        self,
        official_provider: Optional[SpotifyProvider] = None,
        scraper_provider: Optional[SpotifyProvider] = None,
    ) -> None:
        self.official = official_provider
        self.scraper = scraper_provider
        self.breakers: dict[str, CircuitBreaker] = {
            "official": CircuitBreaker(),
            "scraper": CircuitBreaker(),
        }

    def get_provider_status(self) -> dict[str, Any]:
        """Summary of provider availability and circuit breaker states for /diag."""
        return {
            "official_configured": self.official is not None and self.official.is_healthy,
            "official_circuit_available": self.breakers["official"].is_available,
            "official_failures": self.breakers["official"].failure_count,
            "scraper_enabled": self.scraper is not None and self.scraper.is_healthy,
            "scraper_circuit_available": self.breakers["scraper"].is_available,
            "scraper_failures": self.breakers["scraper"].failure_count,
        }

    async def resolve_track(self, spotify_id: str) -> SpotifyTrackMeta:
        """Resolve a track trying official first, falling back to scraper."""
        providers = self._get_eligible_providers()
        last_error: Optional[Exception] = None

        for provider in providers:
            p_name = provider.name
            try:
                meta = await provider.get_track(spotify_id)
                self.breakers[p_name].record_success()
                logger.info("Resolved Spotify track %s via %s provider", spotify_id, p_name)
                return meta
            except NotFoundError:
                # 404 is a definitive not-found, no fallback needed
                self.breakers[p_name].record_success()
                raise
            except Exception as exc:
                self.breakers[p_name].record_failure()
                last_error = exc
                logger.warning("Provider %s failed resolving track %s: %s", p_name, spotify_id, exc)

        raise SpotifyResolutionError(
            message=f"Could not retrieve Spotify track metadata ({last_error}).",
            user_hint="Try pasting a YouTube link or searching with song title and artist.",
        )

    async def resolve_album(self, album_id: str) -> SpotifyCollection:
        """Resolve an album trying official first, falling back to scraper."""
        providers = self._get_eligible_providers()
        last_error: Optional[Exception] = None

        for provider in providers:
            p_name = provider.name
            try:
                col = await provider.get_album(album_id)
                self.breakers[p_name].record_success()
                logger.info(
                    "Resolved Spotify album %s via %s provider (%d tracks)",
                    album_id,
                    p_name,
                    col.count,
                )
                return col
            except NotFoundError:
                self.breakers[p_name].record_success()
                raise
            except Exception as exc:
                self.breakers[p_name].record_failure()
                last_error = exc
                logger.warning("Provider %s failed resolving album %s: %s", p_name, album_id, exc)

        raise SpotifyResolutionError(
            message=f"Could not retrieve Spotify album metadata ({last_error}).",
            user_hint="Try pasting a YouTube playlist link or song titles.",
        )

    async def resolve_playlist(self, playlist_id: str, max_tracks: int = 100) -> SpotifyCollection:
        """Resolve a playlist trying official first, falling back to scraper."""
        providers = self._get_eligible_providers()
        last_error: Optional[Exception] = None

        for provider in providers:
            p_name = provider.name
            try:
                col = await provider.get_playlist(playlist_id, max_tracks=max_tracks)
                self.breakers[p_name].record_success()
                logger.info(
                    "Resolved Spotify playlist %s via %s provider (%d tracks)",
                    playlist_id,
                    p_name,
                    col.count,
                )
                return col
            except NotFoundError:
                self.breakers[p_name].record_success()
                raise
            except Exception as exc:
                self.breakers[p_name].record_failure()
                last_error = exc
                logger.warning(
                    "Provider %s failed resolving playlist %s: %s", p_name, playlist_id, exc
                )

        raise SpotifyResolutionError(
            message=f"Could not retrieve Spotify playlist metadata ({last_error}).",
            user_hint="Try pasting a YouTube playlist link or song titles.",
        )

    def _get_eligible_providers(self) -> list[SpotifyProvider]:
        """Return available providers ordered by preference (official first, then scraper)."""
        candidates: list[SpotifyProvider] = []

        if self.official and self.official.is_healthy and self.breakers["official"].is_available:
            candidates.append(self.official)

        if self.scraper and self.scraper.is_healthy and self.breakers["scraper"].is_available:
            candidates.append(self.scraper)

        if not candidates:
            logger.warning("No Spotify providers are currently available or healthy.")

        return candidates

    async def close(self) -> None:
        """Close provider resources."""
        if self.official:
            await self.official.close()
        if self.scraper:
            await self.scraper.close()
