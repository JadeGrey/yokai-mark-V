"""Spotify metadata subsystem for Yokai."""

from __future__ import annotations

from yokai.music.spotify.base import SpotifyProvider
from yokai.music.spotify.models import SpotifyCollection, SpotifyTrackMeta
from yokai.music.spotify.official import OfficialSpotifyProvider
from yokai.music.spotify.orchestrator import SpotifyOrchestrator
from yokai.music.spotify.scraper import ScraperSpotifyProvider
from yokai.music.spotify.urls import parse_spotify_uri_or_url, resolve_spotify_link

__all__ = [
    "OfficialSpotifyProvider",
    "ScraperSpotifyProvider",
    "SpotifyCollection",
    "SpotifyOrchestrator",
    "SpotifyProvider",
    "SpotifyTrackMeta",
    "parse_spotify_uri_or_url",
    "resolve_spotify_link",
]
