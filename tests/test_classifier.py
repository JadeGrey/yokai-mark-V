"""Tests for input classification, URL security allowlist, and sanitization."""

from __future__ import annotations

from yokai.music.classifier import InputKind, classify_input


def test_classify_empty_input() -> None:
    res = classify_input("")
    assert res.kind == InputKind.UNSUPPORTED
    assert res.error_message is not None

    res = classify_input("   \n\t  ")
    assert res.kind == InputKind.UNSUPPORTED


def test_classify_search_query() -> None:
    res = classify_input("radiohead karma police")
    assert res.kind == InputKind.SEARCH_QUERY
    assert res.clean_target == "radiohead karma police"

    # Query length cap (200 chars)
    long_query = "word " * 60
    res_long = classify_input(long_query)
    assert res_long.kind == InputKind.SEARCH_QUERY
    assert len(res_long.clean_target) <= 200


def test_classify_youtube_watch_urls() -> None:
    # Standard watch URL
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    res = classify_input(url)
    assert res.kind == InputKind.YOUTUBE_TRACK
    assert res.extracted_id == "dQw4w9WgXcQ"
    assert res.clean_target == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    # Watch URL with list= and index= params stripped
    url_with_list = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL12345&index=3"
    res = classify_input(url_with_list)
    assert res.kind == InputKind.YOUTUBE_TRACK
    assert res.extracted_id == "dQw4w9WgXcQ"
    assert res.clean_target == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    # Music subdomain
    music_url = "https://music.youtube.com/watch?v=dQw4w9WgXcQ"
    res = classify_input(music_url)
    assert res.kind == InputKind.YOUTUBE_TRACK
    assert res.extracted_id == "dQw4w9WgXcQ"


def test_classify_youtube_short_and_shorts_urls() -> None:
    # youtu.be
    short_url = "https://youtu.be/dQw4w9WgXcQ"
    res = classify_input(short_url)
    assert res.kind == InputKind.YOUTUBE_TRACK
    assert res.extracted_id == "dQw4w9WgXcQ"
    assert res.clean_target == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    # /shorts/
    shorts_url = "https://www.youtube.com/shorts/dQw4w9WgXcQ?feature=share"
    res = classify_input(shorts_url)
    assert res.kind == InputKind.YOUTUBE_TRACK
    assert res.extracted_id == "dQw4w9WgXcQ"


def test_classify_youtube_playlist_urls() -> None:
    # Standard playlist
    pl_url = "https://www.youtube.com/playlist?list=PLFgquLnL59alCl_2TQvOiD5Vgm1hCaGSI"
    res = classify_input(pl_url)
    assert res.kind == InputKind.YOUTUBE_PLAYLIST
    assert res.extracted_id == "PLFgquLnL59alCl_2TQvOiD5Vgm1hCaGSI"

    # Missing list param
    bad_pl = "https://www.youtube.com/playlist"
    res = classify_input(bad_pl)
    assert res.kind == InputKind.UNSUPPORTED
    assert "missing the 'list' parameter" in (res.error_message or "")

    # Auto-mix playlist (RD...)
    rd_url = "https://www.youtube.com/playlist?list=RDMM123456"
    res = classify_input(rd_url)
    assert res.kind == InputKind.UNSUPPORTED
    assert "Auto-generated YouTube mix" in (res.error_message or "")


def test_classify_spotify_urls() -> None:
    # Track
    sp_track = "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT"
    res = classify_input(sp_track)
    assert res.kind == InputKind.SPOTIFY_TRACK
    assert res.extracted_id == "4cOdK2wGLETKBW3PvgPWqT"

    # Track with locale prefix
    sp_locale = "https://open.spotify.com/intl-de/track/4cOdK2wGLETKBW3PvgPWqT"
    res = classify_input(sp_locale)
    assert res.kind == InputKind.SPOTIFY_TRACK
    assert res.extracted_id == "4cOdK2wGLETKBW3PvgPWqT"

    # Album
    sp_album = "https://open.spotify.com/album/4cOdK2wGLETKBW3PvgPWqT"
    res = classify_input(sp_album)
    assert res.kind == InputKind.SPOTIFY_ALBUM

    # Playlist
    sp_playlist = "https://open.spotify.com/playlist/4cOdK2wGLETKBW3PvgPWqT"
    res = classify_input(sp_playlist)
    assert res.kind == InputKind.SPOTIFY_PLAYLIST

    # Spotify URI
    uri = "spotify:track:4cOdK2wGLETKBW3PvgPWqT"
    res = classify_input(uri)
    assert res.kind == InputKind.SPOTIFY_TRACK
    assert res.extracted_id == "4cOdK2wGLETKBW3PvgPWqT"

    # Rejected Spotify links (artist, show, episode)
    res_artist = classify_input("https://open.spotify.com/artist/4cOdK2wGLETKBW3PvgPWqT")
    assert res_artist.kind == InputKind.UNSUPPORTED
    assert "artist links are not supported" in (res_artist.error_message or "")


def test_classify_unallowed_hosts() -> None:
    res = classify_input("https://soundcloud.com/artist/track")
    assert res.kind == InputKind.UNSUPPORTED
    assert "Links from 'soundcloud.com' are not permitted" in (res.error_message or "")

    res_evil = classify_input("https://malicious-site.com/video.mp4")
    assert res_evil.kind == InputKind.UNSUPPORTED
