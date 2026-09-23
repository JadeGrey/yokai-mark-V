"""Tests for SQLite database migrations, WAL mode, and repository operations."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from yokai.storage.db import Database


@pytest.mark.asyncio
async def test_database_lifecycle_and_migrations() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "test_yokai.db"
        db = Database(db_path)

        await db.initialize()

        # Check user_version
        async with db.conn.execute("PRAGMA user_version;") as cursor:
            row = await cursor.fetchone()
            assert row is not None and row[0] == 1

        # Check ping
        latency = await db.ping()
        assert latency > 0.0

        await db.close()


@pytest.mark.asyncio
async def test_command_logging_and_usage_stats() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "test_yokai.db"
        db = Database(db_path)
        await db.initialize()

        # Log commands
        await db.log_command(user_id=101, command="play")
        await db.log_command(user_id=101, command="play")
        await db.log_command(user_id=101, command="skip")
        await db.log_command(user_id=202, command="play")

        # Record plays
        await db.record_play_event(
            user_id=101,
            video_id="vid1",
            title="Song 1",
            artist="Artist 1",
            duration_s=200,
            origin="search",
            outcome="completed",
            listened_s=200,
        )

        top_users, top_commands = await db.get_usage_stats()

        # User 101 has 3 commands, 1 play event
        assert len(top_users) == 2
        assert top_users[0][0] == 101
        assert top_users[0][1] == 3
        assert top_users[0][2] == 1

        # User 202 has 1 command, 0 play events
        assert top_users[1][0] == 202
        assert top_users[1][1] == 1
        assert top_users[1][2] == 0

        # Top commands: play (3), skip (1)
        assert len(top_commands) == 2
        assert top_commands[0] == ("play", 3)
        assert top_commands[1] == ("skip", 1)

        await db.close()


@pytest.mark.asyncio
async def test_spotify_cache_and_recent_plays() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "test_yokai.db"
        db = Database(db_path)
        await db.initialize()

        # Test Spotify match cache
        assert await db.get_spotify_match("spot_123") is None
        await db.set_spotify_match("spot_123", "yt_vid_999", 0.95)
        match = await db.get_spotify_match("spot_123")
        assert match is not None
        assert match[0] == "yt_vid_999"
        assert match[1] == 0.95

        # Test recent completed track
        await db.record_play_event(
            user_id=555,
            video_id="vid_completed",
            title="Completed Song",
            artist="Singer",
            duration_s=180,
            origin="link",
            outcome="completed",
            listened_s=180,
        )
        await db.record_play_event(
            user_id=555,
            video_id="vid_skipped",
            title="Skipped Song",
            artist="Singer",
            duration_s=180,
            origin="link",
            outcome="skipped",
            listened_s=20,
        )

        recent = await db.get_recent_completed_track(555)
        assert recent is not None
        assert recent[0] == "vid_completed"
        assert recent[1] == "Completed Song"

        recent_ids = await db.get_recent_played_video_ids(limit=10)
        assert "vid_completed" in recent_ids
        assert "vid_skipped" in recent_ids

        await db.close()
