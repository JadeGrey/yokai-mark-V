"""Tests for starting the panel: launch URL, preflight, console-only logging, a real server."""

from __future__ import annotations

import logging
import os
import socket
import sqlite3
import threading
import time
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import uvicorn
from starlette.testclient import TestClient

from tests.admin_helpers import seed_database
from yokai_admin.__main__ import main
from yokai_admin.core.settings import HOST, PanelSettings
from yokai_admin.errors import PanelDbError
from yokai_admin.launch import auth_url, build_server, preflight, run


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind((HOST, 0))
        return int(sock.getsockname()[1])


def test_the_launch_url_carries_the_port_and_token() -> None:
    assert auth_url(8765, "abc") == "http://127.0.0.1:8765/auth?token=abc"


def test_building_the_server_does_not_open_the_browser(tmp_path: Path) -> None:
    opened: list[str] = []
    settings = PanelSettings(db_path=tmp_path / "x.db", log_dir=tmp_path, port=8765)
    server, url = build_server(settings, opener=opened.append)
    with TestClient(server.config.app, base_url="http://127.0.0.1:8765") as client:
        assert opened == []  # opening happens only once the port is really bound
        path = url.replace("http://127.0.0.1:8765", "")
        assert client.get(path, follow_redirects=False).status_code == 303  # the URL logs in


def test_the_panel_never_creates_a_file_log() -> None:
    """Review Focus 5: the single-use URL goes to the console only.

    The root logger cannot be inspected here (pytest's own logging plugin installs a null file
    handler on it), so the claim is checked at the source: nothing in the panel configures a
    file handler or a logging dictConfig that could add one.
    """
    files = Path("src/yokai_admin").rglob("*.py")
    sources = "\n".join(path.read_text(encoding="utf-8") for path in files)
    assert "FileHandler" not in sources
    assert "logging.config" not in sources and "basicConfig" not in sources


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
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # patch.dict restores os.environ afterwards, so whatever main() loads from the owner's real
    # .env cannot leak into other tests.
    env = {"DB_PATH": str(tmp_path / "missing.db"), "LOG_DIR": str(tmp_path)}
    with patch.dict(os.environ, env, clear=True):
        with caplog.at_level(logging.ERROR):
            assert main() == 1
    assert "not found" in caplog.text


def test_main_exits_1_on_a_bad_port(caplog: pytest.LogCaptureFixture) -> None:
    with patch.dict(os.environ, {"ADMIN_PORT": "80"}, clear=True):
        with caplog.at_level(logging.ERROR):
            assert main() == 1
    assert "ADMIN_PORT" in caplog.text


def test_run_returns_1_with_a_hint_and_opens_nothing_when_the_port_is_taken(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Review Focus 5: a busy port is a friendly exit, and the browser is never sent to a link
    whose server never started."""
    seed_database(tmp_path / "yokai.db")
    opened: list[str] = []
    with socket.socket() as blocker:
        blocker.bind((HOST, 0))
        blocker.listen()
        port = int(blocker.getsockname()[1])
        settings = PanelSettings(db_path=tmp_path / "yokai.db", log_dir=tmp_path, port=port)
        with caplog.at_level(logging.INFO):
            assert run(settings, opener=opened.append) == 1
    assert opened == []
    assert "Admin panel ready" not in caplog.text
    assert "ADMIN_PORT" in caplog.text and "(1)" not in caplog.text


def test_ctrl_c_stops_the_panel_quietly(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A normal stop is not an error: no traceback, exit code 0."""
    settings = PanelSettings(db_path=tmp_path / "x.db", log_dir=tmp_path, port=_free_port())
    with patch.object(uvicorn.Server, "run", side_effect=KeyboardInterrupt):
        with caplog.at_level(logging.INFO):
            assert run(settings, opener=None) == 0
    assert "stopped" in caplog.text


def test_a_real_server_announces_after_binding_logs_in_and_serves_the_dashboard(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    db = tmp_path / "yokai.db"
    seed_database(db, heartbeat_ts=int(time.time()))
    port = _free_port()
    settings = PanelSettings(db_path=db, log_dir=tmp_path / "logs", port=port)
    opened: list[str] = []
    server, url = build_server(settings, opener=opened.append)
    thread = threading.Thread(target=server.run, daemon=True)
    with caplog.at_level(logging.INFO, logger="yokai_admin"):
        thread.start()
        try:
            for _ in range(200):
                if server.started and opened:
                    break
                time.sleep(0.05)
            assert server.started
            assert opened == [url]  # opened once, only after the port was bound
            assert url in caplog.text  # the single-use link is printed to the console log
            with httpx.Client(follow_redirects=True) as client:
                page = client.get(url)
                assert page.status_code == 200 and ">Online<" in page.text
                assert page.headers["content-security-policy"].startswith("default-src 'none'")
            assert httpx.get(url).status_code == 401  # the link is single-use
            assert httpx.get(f"http://{HOST}:{port}/").status_code == 401  # no cookie, no panel
        finally:
            server.should_exit = True
            thread.join(timeout=10)
