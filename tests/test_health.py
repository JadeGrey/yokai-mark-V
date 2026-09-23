"""Tests for startup health check and diagnostics."""

from __future__ import annotations

from unittest.mock import patch

from yokai.health import check_health


def test_check_health_all_found() -> None:
    with (
        patch("shutil.which", return_value="C:\\path\\to\\bin.exe"),
        patch(
            "subprocess.run",
            return_value=type("Proc", (), {"stdout": "mock_tool version 1.0\n", "stderr": ""})(),
        ),
    ):
        report = check_health()
        assert report.davey_available is True
        assert report.ffmpeg_found is True
        assert report.deno_found is True
        assert report.ytdlp_available is True
        assert report.voice_capable is True


def test_check_health_ffmpeg_missing() -> None:
    def fake_which(cmd: str) -> str | None:
        if cmd == "ffmpeg":
            return None
        return "C:\\path\\to\\bin.exe"

    with (
        patch("shutil.which", side_effect=fake_which),
        patch(
            "subprocess.run",
            return_value=type("Proc", (), {"stdout": "version 1.0\n", "stderr": ""})(),
        ),
    ):
        report = check_health()
        assert report.ffmpeg_found is False
        assert report.voice_capable is False
        assert any("FFmpeg was not found" in w for w in report.warnings)
