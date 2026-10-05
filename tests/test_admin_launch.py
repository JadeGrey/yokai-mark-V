"""Tests for starting the panel: launch URL, preflight, console-only logging, a real server."""

from __future__ import annotations

import logging
import socket
import sqlite3
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from starlette.testclient import TestClient

from tests.admin_helpers import seed_database
from yokai_admin.__main__ import main
from yokai_admin.core.settings import HOST, PanelSettings
from yokai_admin.errors import PanelDbError
from yokai_admin.launch import auth_url, build_panel, preflight, run


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind((HOST, 0))
        return int(sock.getsockname()[1])


def test_the_launch_url_carries_the_port_and_token() -> None:
    assert auth_url(8765, "abc") == "http://127.0.0.1:8765/auth?token=abc"


def test_building_the_panel_opens_the_browser_once_at_startup(tmp_path: Path) -> None:
    opened: list[str] = []
    settings = PanelSettings(db_path=tmp_path / "x.db", log_dir=tmp_path, port=8765)
    app, url = build_panel(settings, opener=opened.append)
    assert opened == []  # nothing happens until the server starts
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        assert opened == [url]
        path = url.replace("http://127.0.0.1:8765", "")
        assert client.get(path, follow_redirects=False).status_code == 303  # it really logs in


def test_the_token_url_is_logged_but_the_panel_never_creates_a_file_log(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Review Focus 5: the single-use URL goes to the console only.

    The root logger cannot be inspected here (pytest's own logging plugin installs a null file
    handler on it), so the claim is checked at the source: nothing in the panel configures a
    file handler or a logging dictConfig that could add one.
    """
    settings = PanelSettings(db_path=tmp_path / "x.db", log_dir=tmp_path, port=8765)
    app, url = build_panel(settings, opener=lambda _url: None)
    with caplog.at_level(logging.INFO, logger="yokai_admin"):
        with TestClient(app, base_url="http://127.0.0.1:8765"):
            pass
    assert url in caplog.text

    files = Path("src/yokai_admin").rglob("*.py")
    sources = "\n".join(path.read_text(encoding="utf-8") for path in files)
    assert "FileHandler" not in sources
    assert "logging.config" not in sources and "filename=" not in sources


def test_preflight_names_the_problem_for_a_missing_or_old_database(tmp_path: Path) -> None:
    with pytest.raises(PanelDbError, match="not found"):
        preflight(PanelSettings(db_path=tmp_path / "none.db", log_dir=tmp_path))

    old = tmp_path / "old.db"
    raw = sqlite3.connect(old)
    raw.execute("PRAGMA user_version = 3;")
    raw.execute("CREATE TABLE t (x INTEGER);")
    raw.commit()
    raw.close()
    with pytest.raises(PanelDbError, match="v3"):
        preflight(PanelSettings(db_path=old, log_dir=tmp_path))


def test_main_exits_1_with_a_message_when_the_database_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("DB_PATH", str(tmp_path / "missing.db"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path))
    with caplog.at_level(logging.ERROR):
        assert main() == 1
    assert "not found" in caplog.text


def test_main_exits_1_on_a_bad_port(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("ADMIN_PORT", "80")
    with caplog.at_level(logging.ERROR):
        assert main() == 1
    assert "ADMIN_PORT" in caplog.text


def test_run_returns_1_with_a_hint_when_the_port_is_taken(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Review Focus 5: a busy port is a friendly exit, not a traceback or a hang."""
    seed_database(tmp_path / "yokai.db")
    with socket.socket() as blocker:
        blocker.bind((HOST, 0))
        blocker.listen()
        port = int(blocker.getsockname()[1])
        settings = PanelSettings(db_path=tmp_path / "yokai.db", log_dir=tmp_path, port=port)
        with caplog.at_level(logging.ERROR):
            assert run(settings, open_browser=False) == 1
    assert "ADMIN_PORT" in caplog.text


def test_a_real_server_logs_in_with_the_launch_url_and_serves_the_dashboard(
    tmp_path: Path,
) -> None:
    db = tmp_path / "yokai.db"
    seed_database(db, heartbeat_ts=int(time.time()))
    port = _free_port()
    settings = PanelSettings(db_path=db, log_dir=tmp_path / "logs", port=port)
    app, url = build_panel(settings, opener=lambda _url: None)
    config = uvicorn.Config(
        app, host=HOST, port=port, log_config=None, log_level="warning", access_log=False
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(200):
            if server.started:
                break
            time.sleep(0.05)
        assert server.started
        with httpx.Client(follow_redirects=True) as client:
            page = client.get(url)
            assert page.status_code == 200 and ">Online<" in page.text
            assert page.headers["content-security-policy"].startswith("default-src 'none'")
        assert httpx.get(url).status_code == 401  # the link is single-use
        assert httpx.get(f"http://{HOST}:{port}/").status_code == 401  # no cookie, no panel
    finally:
        server.should_exit = True
        thread.join(timeout=10)
