"""Unit tests for Spotify URL parsing, providers, fallback orchestration, and circuit breaking."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from yokai.errors import SpotifyResolutionError, UnavailableError, UnsupportedError
from yokai.music.spotify.base import SpotifyProvider
from yokai.music.spotify.models import SpotifyCollection, SpotifyTrackMeta
from yokai.music.spotify.official import OfficialSpotifyProvider
from yokai.music.spotify.orchestrator import SpotifyOrchestrator
from yokai.music.spotify.scraper import ScraperSpotifyProvider
from yokai.music.spotify.urls import parse_spotify_uri_or_url, resolve_spotify_link


def test_parse_spotify_uri_and_url() -> None:
    # 1. URIs
    kind, ident = parse_spotify_uri_or_url("spotify:track:4cOdK2wGLETKBW3PvgPWqT")
    assert kind == "track"
    assert ident == "4cOdK2wGLETKBW3PvgPWqT"

    kind, ident = parse_spotify_uri_or_url("spotify:album:1ATL5GLyef8vy3xsYJM9rm")
    assert kind == "album"
    assert ident == "1ATL5GLyef8vy3xsYJM9rm"

    kind, ident = parse_spotify_uri_or_url("spotify:playlist:37i9dQZF1DXcBWIGoYBM5M")
    assert kind == "playlist"
    assert ident == "37i9dQZF1DXcBWIGoYBM5M"

    # 2. URLs with and without international prefixes
    kind, ident = parse_spotify_uri_or_url(
        "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT?si=abc"
    )
    assert kind == "track"
    assert ident == "4cOdK2wGLETKBW3PvgPWqT"

    kind, ident = parse_spotify_uri_or_url(
        "https://open.spotify.com/intl-ja/album/1ATL5GLyef8vy3xsYJM9rm"
    )
    assert kind == "album"
    assert ident == "1ATL5GLyef8vy3xsYJM9rm"

    kind, ident = parse_spotify_uri_or_url(
        "https://open.spotify.com/intl-pt-br/playlist/37i9dQZF1DXcBWIGoYBM5M"
    )
    assert kind == "playlist"
    assert ident == "37i9dQZF1DXcBWIGoYBM5M"

    # 3. Unsupported entities
    with pytest.raises(UnsupportedError, match="artist links are not supported"):
        parse_spotify_uri_or_url("spotify:artist:06HL4z0CvFAxyc27GXpf02")

    with pytest.raises(UnsupportedError, match="show links are not supported"):
        parse_spotify_uri_or_url("https://open.spotify.com/show/4rOoJ6Egrf8K2IrywzwOMk")

    with pytest.raises(UnsupportedError, match="episode links are not supported"):
        parse_spotify_uri_or_url("https://open.spotify.com/episode/5125nnFaei2eP8iY3jGg3X")


@pytest.mark.asyncio
async def test_resolve_spotify_link() -> None:
    mock_client = AsyncMock(spec=httpx.AsyncClient)

    # Valid redirect to open.spotify.com
    mock_client.get.return_value = httpx.Response(
        200,
        request=httpx.Request("GET", "https://open.spotify.com/track/123"),
    )
    res = await resolve_spotify_link("https://spotify.link/abc", client=mock_client)
    assert res == "https://open.spotify.com/track/123"

    # Invalid redirect to malicious or foreign domain
    mock_client.get.return_value = httpx.Response(
        200,
        request=httpx.Request("GET", "https://malicious-site.com/track/123"),
    )
    with pytest.raises(UnsupportedError, match="unauthorized host"):
        await resolve_spotify_link("https://spotify.link/bad", client=mock_client)


class FakeSpotifyProvider(SpotifyProvider):
    def __init__(self, name: str, fails: bool = False, healthy: bool = True) -> None:
        self._name = name
        self.fails = fails
        self._healthy = healthy
        self.call_count = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_healthy(self) -> bool:
        return self._healthy

    async def get_track(self, spotify_id: str) -> SpotifyTrackMeta:
        self.call_count += 1
        if self.fails:
            raise UnavailableError(f"{self.name} failed")
        return SpotifyTrackMeta(
            spotify_id=spotify_id,
            title=f"{self.name} Track",
            artists=["Artist 1"],
            duration_s=200,
        )

    async def get_album(self, album_id: str) -> SpotifyCollection:
        self.call_count += 1
        if self.fails:
            raise UnavailableError(f"{self.name} failed")
        return SpotifyCollection(
            name=f"{self.name} Album",
            tracks=[
                SpotifyTrackMeta(
                    spotify_id="t1",
                    title="Track 1",
                    artists=["Artist 1"],
                    duration_s=200,
                )
            ],
            total=1,
            partial=False,
        )

    async def get_playlist(self, playlist_id: str, max_tracks: int = 100) -> SpotifyCollection:
        self.call_count += 1
        if self.fails:
            raise UnavailableError(f"{self.name} failed")
        return SpotifyCollection(
            name=f"{self.name} Playlist",
            tracks=[
                SpotifyTrackMeta(
                    spotify_id="t1",
                    title="Track 1",
                    artists=["Artist 1"],
                    duration_s=200,
                )
            ],
            total=10,
            partial=True,
        )


@pytest.mark.asyncio
async def test_orchestrator_official_first_and_fallback() -> None:
    official = FakeSpotifyProvider("official", fails=False)
    scraper = FakeSpotifyProvider("scraper", fails=False)
    orchestrator = SpotifyOrchestrator(official_provider=official, scraper_provider=scraper)

    # 1. Normal case: official provider succeeds
    track = await orchestrator.resolve_track("sp_123")
    assert track.title == "official Track"
    assert official.call_count == 1
    assert scraper.call_count == 0

    # 2. Official fails: fallback to scraper
    official.fails = True
    track = await orchestrator.resolve_track("sp_123")
    assert track.title == "scraper Track"
    assert official.call_count == 2
    assert scraper.call_count == 1

    # 3. Both fail: raises SpotifyResolutionError
    scraper.fails = True
    with pytest.raises(SpotifyResolutionError):
        await orchestrator.resolve_track("sp_123")


@pytest.mark.asyncio
async def test_orchestrator_circuit_breaker() -> None:
    official = FakeSpotifyProvider("official", fails=True)
    scraper = FakeSpotifyProvider("scraper", fails=False)
    orchestrator = SpotifyOrchestrator(official_provider=official, scraper_provider=scraper)

    # Trigger 3 failures on official
    for _ in range(3):
        res = await orchestrator.resolve_track("sp_1")
        assert res.title == "scraper Track"

    assert official.call_count == 3
    assert not orchestrator.breakers["official"].is_available

    # 4th call: official circuit is open -> skipped completely!
    res = await orchestrator.resolve_track("sp_1")
    assert res.title == "scraper Track"
    assert official.call_count == 3  # Did not attempt official!

    # Cooldown expires: half-open probe
    orchestrator.breakers["official"].open_until = time.time() - 1.0
    assert orchestrator.breakers["official"].is_available


@pytest.mark.asyncio
async def test_official_provider_endpoints_and_auto_disable() -> None:
    mock_client = AsyncMock(spec=httpx.AsyncClient)

    # 1. Token auth success
    req_post = httpx.Request("POST", "https://accounts.spotify.com/api/token")
    mock_client.post.return_value = httpx.Response(
        200,
        json={"access_token": "mock_token", "expires_in": 3600},
        request=req_post,
    )

    # 2. Track endpoint
    req_get = httpx.Request("GET", "https://api.spotify.com/v1/tracks/trk_1")
    mock_client.get.return_value = httpx.Response(
        200,
        json={
            "id": "trk_1",
            "name": "Song Name",
            "duration_ms": 185000,
            "artists": [{"name": "Lead Artist"}],
            "album": {"name": "Album Name", "images": [{"url": "https://img.spotify.com/1.jpg"}]},
            "explicit": False,
        },
        request=req_get,
    )

    provider = OfficialSpotifyProvider("id", "secret", client=mock_client)
    track = await provider.get_track("trk_1")
    assert track.spotify_id == "trk_1"
    assert track.title == "Song Name"
    assert track.primary_artist == "Lead Artist"
    assert track.duration_s == 185
    assert track.album_name == "Album Name"
    assert track.thumbnail_url == "https://img.spotify.com/1.jpg"

    # 3. Auth failure auto-disables provider
    mock_client.post.return_value = httpx.Response(401, text="Invalid Client", request=req_post)
    provider._access_token = None
    with pytest.raises(UnavailableError):
        await provider.get_track("trk_2")
    assert not provider.is_healthy


@pytest.mark.asyncio
async def test_scraper_provider_caching() -> None:
    provider = ScraperSpotifyProvider(enabled=True)

    fake_track = MagicMock()
    fake_track.id = "sc_1"
    fake_track.name = "Scraped Song"
    fake_artist = MagicMock()
    fake_artist.name = "Scraped Artist"
    fake_track.artists = [fake_artist]
    fake_track.duration_ms = 120000
    fake_track.album = None
    fake_track.images = []
    fake_track.explicit = False

    with patch.object(provider, "_sync_get_track", return_value=fake_track) as mock_sync:
        # First call fetches via sync
        t1 = await provider.get_track("sc_1")
        assert t1.title == "Scraped Song"
        assert mock_sync.call_count == 1

        # Second call hits 24h memory cache
        t2 = await provider.get_track("sc_1")
        assert t2.title == "Scraped Song"
        assert mock_sync.call_count == 1
