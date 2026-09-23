"""Spotify URL and URI parsing and shortlink redirect resolution."""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse

import httpx

from yokai.errors import UnsupportedError

# Match spotify:track:<id>, spotify:album:<id>, spotify:playlist:<id>, etc.
_SPOTIFY_URI_PATTERN = re.compile(
    r"^spotify:(track|album|playlist|artist|show|episode):([a-zA-Z0-9]+)$",
    re.IGNORECASE,
)

# Supported entities
SUPPORTED_ENTITIES = {"track", "album", "playlist"}
UNSUPPORTED_ENTITIES = {"artist", "show", "episode"}


def parse_spotify_uri_or_url(input_str: str) -> tuple[str, str]:
    """Parse a Spotify URI or URL into (entity_type, entity_id).

    Raises:
        UnsupportedError: If the entity type is unsupported (e.g. artist, podcast)
                          or the URL structure is not recognized.
    """
    text = input_str.strip()

    # 1. URI format: spotify:track:id
    uri_match = _SPOTIFY_URI_PATTERN.match(text)
    if uri_match:
        entity_type = uri_match.group(1).lower()
        entity_id = uri_match.group(2)
        if entity_type in UNSUPPORTED_ENTITIES:
            raise UnsupportedError(
                f"Spotify {entity_type} links are not supported.",
                user_hint="Yokai accepts Spotify track, album, and playlist links.",
            )
        if entity_type in SUPPORTED_ENTITIES:
            return entity_type, entity_id
        raise UnsupportedError(f"Unsupported Spotify URI type: '{entity_type}'.")

    # 2. Web URL format: https://open.spotify.com/...
    try:
        parsed = urlparse(text)
    except Exception as exc:
        raise UnsupportedError("Malformed Spotify URL.") from exc

    hostname = (parsed.hostname or "").lower()
    if hostname != "open.spotify.com":
        raise UnsupportedError(
            f"Host '{hostname}' is not open.spotify.com.",
            user_hint="Please provide a valid open.spotify.com link.",
        )

    # Strip locale prefix if present, e.g. /intl-de/track/... -> /track/...
    path = re.sub(r"^/intl-[a-z]{2,3}(?:-[a-z0-9]+)?/", "/", parsed.path)
    parts = [p for p in path.split("/") if p]

    if len(parts) >= 2:
        entity_type = parts[0].lower()
        # ID might have query parameters or trailing garbage already stripped by urlparse
        entity_id = parts[1]

        if entity_type in UNSUPPORTED_ENTITIES:
            raise UnsupportedError(
                f"Spotify {entity_type} links are not supported.",
                user_hint="Yokai accepts Spotify track, album, and playlist links.",
            )
        if entity_type in SUPPORTED_ENTITIES:
            return entity_type, entity_id

    raise UnsupportedError(
        "Could not recognize a track, album, or playlist in this Spotify URL.",
        user_hint="Paste a link to a Spotify track, album, or playlist.",
    )


async def resolve_spotify_link(
    url: str,
    client: Optional[httpx.AsyncClient] = None,
    timeout: float = 10.0,
) -> str:
    """Resolve a spotify.link shortlink by following HTTP redirects.

    Guarantees that the final landing destination is open.spotify.com.

    Raises:
        UnsupportedError: If the redirect lands outside open.spotify.com or fails.
    """
    close_client = False
    if client is None:
        client = httpx.AsyncClient(follow_redirects=True, timeout=timeout)
        close_client = True

    try:
        # Fetch with redirects followed
        resp = await client.get(url)
        final_url = str(resp.url)
        parsed = urlparse(final_url)
        hostname = (parsed.hostname or "").lower()

        if hostname != "open.spotify.com":
            raise UnsupportedError(
                f"Spotify shortlink redirected to unauthorized host '{hostname}'.",
                user_hint="Only links redirecting to open.spotify.com are allowed.",
            )

        return final_url
    except UnsupportedError:
        raise
    except Exception as exc:
        raise UnsupportedError(
            f"Failed to resolve Spotify shortlink: {exc}",
            user_hint="Try copying the full open.spotify.com link from your browser or app.",
        ) from exc
    finally:
        if close_client:
            await client.aclose()
