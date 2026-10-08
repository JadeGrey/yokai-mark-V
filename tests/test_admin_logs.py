"""Tests for the Logs module through the real app."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

from tests.admin_helpers import panel_client, post

LOGS = "yokai_admin.modules.logs:MODULE"
HX = {"HX-Request": "true"}
BOT = (
    "2026-09-24 19:48:57 [INFO] yokai.bot: Ready\n"
    "2026-09-24 19:48:58 [ERROR] yokai.bot: Unexpected error\n"
    "Traceback (most recent call last):\n"
    '  File "tree.py", line 1138, in wrapper\n'
    "ValueError: boom\n"
    "2026-09-24 19:49:00 [WARNING] yokai.music: Slow\n"
    "2026-09-24 19:49:01 [DEBUG] yokai.music: detail\n"
)


class Setup:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.dir = tmp_path / "logs"
        self.dir.mkdir()
        self.current = self.dir / "yokai.log"
        self.current.write_bytes(BOT.encode())

    def client(self, **kwargs: object):  # type: ignore[no-untyped-def]
        return panel_client(self.tmp, [LOGS], **kwargs)

    def append(self, text: str) -> None:
        with self.current.open("ab") as handle:
            handle.write(text.encode())

    def pos(self) -> tuple[int, int]:
        return self.current.stat().st_size, self.current.stat().st_ino


@pytest.fixture
def s(tmp_path: Path) -> Setup:
    return Setup(tmp_path)


def test_the_list_shows_files_with_links_and_the_nav_entry(s: Setup) -> None:
    (s.dir / "yokai.log.1").write_bytes(b"older\n")
    (s.dir / "supervisor.log").write_bytes(b"[2026-09-30 23:38:19] [INFO] hi\n")
    (s.dir / "notes.txt").write_text("not a log")
    with s.client() as (client, _):
        page = client.get("/m/logs").text
    assert 'aria-current="page">Logs</a>' in page
    for name in ("yokai.log", "yokai.log.1", "supervisor.log"):
        assert name in page
    assert "notes.txt" not in page
    assert 'href="/m/logs/view?file=yokai.log"' in page
    assert 'href="/m/logs/download?file=yokai.log.1"' in page
    assert 'href="/m/logs/live"' in page


def test_the_viewer_shows_newest_first_with_tracebacks_together(s: Setup) -> None:
    with s.client() as (client, _):
        page = client.get("/m/logs/view", params={"file": "yokai.log"}).text
    assert page.index("detail") < page.index("Slow") < page.index("Unexpected error")
    assert page.index("Unexpected error") < page.index("Ready")
    entry = re.search(r'<article class="log-entry"[^>]*data-level="ERROR".*?</article>', page, re.S)
    assert entry and "Traceback" in entry.group(0) and "ValueError: boom" in entry.group(0)


def test_level_filter_search_and_paging(s: Setup) -> None:
    lines = "".join(
        f"2026-01-01 00:{i // 60:02d}:{i % 60:02d} [INFO] m: item {i}\n" for i in range(150)
    )
    s.current.write_bytes(lines.encode())
    with s.client() as (client, _):
        first = client.get("/m/logs/view", params={"file": "yokai.log"}).text
        second = client.get("/m/logs/view", params={"file": "yokai.log", "page": "2"}).text
        found = client.get("/m/logs/view", params={"file": "yokai.log", "q": "ITEM 7"}).text
        none = client.get("/m/logs/view", params={"file": "yokai.log", "level": "ERROR"}).text
        junk = client.get("/m/logs/view", params={"file": "yokai.log", "page": "9" * 5000})
    assert "item 149" in first and "item 49" not in first and "Older" in first
    assert "item 49" in second and "Newer" in second and "Older" not in second
    assert "item 7<" in found and "item 6<" not in found
    assert "No entries match" in none and junk.status_code == 200
    assert 'name="q"' in first and '<option value="ERROR"' in first


def test_hostile_log_text_is_escaped_everywhere(s: Setup) -> None:
    """Review Focus 2."""
    evil = "<script>alert(1)</script><img src=x onerror=alert(2)>"
    s.append(f"2026-09-24 19:50:00 [ERROR] x: {evil}\n")
    with s.client() as (client, _):
        viewer = client.get("/m/logs/view", params={"file": "yokai.log", "q": evil}).text
        everything = client.get("/m/logs/view", params={"file": "yokai.log"}).text
        live = client.get("/m/logs/live").text
        pos, ino = s.pos()
        s.append(f"2026-09-24 19:50:01 [INFO] y: {evil}\n")
        poll = client.get("/m/logs/live/poll", params={"pos": pos, "ino": ino}, headers=HX).text
    for page in (viewer, everything, live, poll):
        assert "<script>alert" not in page and "<img src=x" not in page
    assert "&lt;script&gt;alert(1)" in everything and "&lt;img src=x" in poll


@pytest.mark.parametrize(
    "name",
    [
        "..%5Cyokai.log",
        "..%2Fyokai.log",
        "C%3A%5Cx.log",
        "yokai.log%3Astream",
        "nope.log",
        "",
        "x" * 3000,
    ],
)
def test_hostile_file_names_never_open_anything_else(s: Setup, name: str) -> None:
    """Review Focus 1."""
    (s.tmp / "yokai.log").write_text("OUTSIDE-THE-LOG-FOLDER")
    with s.client() as (client, _):
        view = client.get(f"/m/logs/view?file={name}")
        download = client.get(f"/m/logs/download?file={name}")
    for response in (view, download):
        assert response.status_code == 200
        assert "OUTSIDE-THE-LOG-FOLDER" not in response.text and "attachment" not in str(
            response.headers
        )
        assert "no log file with that name" in response.text.lower()


def test_download_returns_exactly_the_listed_file(s: Setup) -> None:
    (s.dir / "yokai.log.2").write_bytes(b"rotated bytes \xff\xfe\n")
    with s.client() as (client, _):
        ok = client.get("/m/logs/download", params={"file": "yokai.log.2"})
        current = client.get("/m/logs/download", params={"file": "yokai.log"})
    assert ok.content == b"rotated bytes \xff\xfe\n"
    assert "attachment" in ok.headers["content-disposition"]
    assert "yokai.log.2" in ok.headers["content-disposition"]
    assert ok.headers["content-type"].startswith("text/plain")
    assert ok.headers["x-content-type-options"] == "nosniff"
    assert current.content == BOT.encode()


def test_the_live_page_shows_the_tail_newest_first_and_a_running_poller(s: Setup) -> None:
    with s.client() as (client, _):
        page = client.get("/m/logs/live").text
    assert page.index("detail") < page.index("Ready")
    pos, ino = s.pos()
    assert f'hx-get="/m/logs/live/poll?pos={pos}&amp;ino={ino}"' in page
    assert 'hx-trigger="every 2s"' in page and 'id="tail-lines"' in page


def test_polling_adds_only_new_complete_lines_newest_first(s: Setup) -> None:
    with s.client() as (client, _):
        pos, ino = s.pos()
        quiet = client.get("/m/logs/live/poll", params={"pos": pos, "ino": ino}, headers=HX)
        assert "log-line" not in quiet.text and 'id="tail-poller"' in quiet.text
        s.append("first new\nsecond new\nhalf a li")
        got = client.get("/m/logs/live/poll", params={"pos": pos, "ino": ino}, headers=HX).text
        assert 'hx-swap-oob="afterbegin"' in got and 'id="tail-lines"' in got
        assert got.index("second new") < got.index("first new") and "half a li" not in got
        new_pos = int(re.search(r"poll\?pos=(\d+)", got).group(1))
        s.append("ne\n")
        rest = client.get("/m/logs/live/poll", params={"pos": new_pos, "ino": ino}, headers=HX).text
    assert "half a line" in rest and "first new" not in rest


def test_a_rotated_log_restarts_with_a_marker(s: Setup) -> None:
    with s.client() as (client, _):
        pos, ino = s.pos()
        os.replace(s.current, s.dir / "yokai.log.1")
        s.current.write_bytes(b"fresh start\n")
        page = client.get("/m/logs/live/poll", params={"pos": pos, "ino": ino}, headers=HX).text
    assert "fresh start" in page and "rotated" in page.lower()


def test_pause_and_resume(s: Setup) -> None:
    pos, ino = s.pos()
    with s.client() as (client, _):
        paused = client.get("/m/logs/live/pause", params={"pos": pos, "ino": ino}, headers=HX).text
    assert "Paused" in paused and "every 2s" not in paused
    assert f'hx-get="/m/logs/live/poll?pos={pos}&amp;ino={ino}"' in paused and "Resume" in paused


@pytest.mark.parametrize(
    ("pos", "ino"),
    [("-1", "5"), ("abc", "5"), ("9" * 5000, "1"), ("1", "9" * 5000), ("", ""), ("1.5", "0x1")],
)
def test_tampered_poll_parameters_restart_cleanly(s: Setup, pos: str, ino: str) -> None:
    """Review Focus 4."""
    with s.client() as (client, _):
        response = client.get("/m/logs/live/poll", params={"pos": pos, "ino": ino}, headers=HX)
    assert response.status_code == 200 and 'id="tail-poller"' in response.text


def test_missing_folder_and_empty_folder_answer_with_messages(tmp_path: Path) -> None:
    """Review Focus 6."""
    with panel_client(tmp_path, [LOGS]) as (client, _):
        assert "No log files" in client.get("/m/logs").text
        assert (
            "no log file with that name" in client.get("/m/logs/view?file=yokai.log").text.lower()
        )
        live = client.get("/m/logs/live")
        poll = client.get("/m/logs/live/poll?pos=0&ino=0", headers=HX)
    assert live.status_code == 200 and "No current log" in live.text
    assert poll.status_code == 200 and 'id="tail-poller"' in poll.text


def test_a_file_that_vanishes_after_listing_is_a_message_not_a_500(
    s: Setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    with s.client() as (client, _):
        router = sys.modules["yokai_admin.modules.logs.router"]
        real = router.find_log_file

        def vanishing(log_dir: Path, name: str):  # type: ignore[no-untyped-def]
            found = real(log_dir, name)
            found.path.unlink()
            return found

        monkeypatch.setattr(router, "find_log_file", vanishing)
        view = client.get("/m/logs/view", params={"file": "yokai.log"})
    assert view.status_code == 200 and "could not be read" in view.text


def test_every_route_is_read_only_and_needs_a_session(s: Setup) -> None:
    """Review Focus 5."""
    paths = [
        "/m/logs",
        "/m/logs/view?file=yokai.log",
        "/m/logs/live",
        "/m/logs/live/poll?pos=0&ino=0",
        "/m/logs/live/pause?pos=0&ino=0",
        "/m/logs/download?file=yokai.log",
    ]
    before = s.current.read_bytes()
    with s.client(login=False) as (client, _):
        assert all(client.get(p).status_code == 401 for p in paths)
    with s.client() as (client, csrf):
        for path in paths:
            assert client.post(path.split("?")[0]).status_code == 403  # no CSRF token
            assert post(client, csrf, path.split("?")[0]).status_code == 405
    assert s.current.read_bytes() == before and sorted(p.name for p in s.dir.iterdir()) == [
        "yokai.log"
    ]


def test_the_dashboard_tile_counts_log_files(s: Setup) -> None:
    (s.dir / "yokai.log.1").write_bytes(b"x" * 2048)
    paths = ["yokai_admin.modules.dashboard:MODULE", LOGS]
    with panel_client(s.tmp, paths) as (client, _):
        page = client.get("/m/dashboard").text
    assert "Logs" in page and "2 files" in page


def test_download_is_bounded_and_survives_a_vanishing_file(
    s: Setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review: FileResponse could 500 on a vanished file and break when the file grew."""
    router = sys.modules["yokai_admin.modules.logs.router"]
    with s.client() as (client, _):
        monkeypatch.setattr(router, "MAX_DOWNLOAD_BYTES", 10)
        big = client.get("/m/logs/download", params={"file": "yokai.log"})
        monkeypatch.setattr(router, "MAX_DOWNLOAD_BYTES", 10**9)
        real = router.find_log_file

        def vanishing(log_dir: Path, name: str):  # type: ignore[no-untyped-def]
            found = real(log_dir, name)
            found.path.unlink()
            return found

        monkeypatch.setattr(router, "find_log_file", vanishing)
        gone = client.get("/m/logs/download", params={"file": "yokai.log"})
    for response in (big, gone):
        assert response.status_code == 200 and "attachment" not in str(response.headers)
    assert "too large" in big.text and "could not be read" in gone.text


def test_a_very_long_file_id_is_not_mistaken_for_tampering(s: Setup) -> None:
    """Review: ReFS file IDs can have more than 20 digits."""
    router = sys.modules["yokai_admin.modules.logs.router"]
    assert router._digits("9" * 39, limit=40) == int("9" * 39)
    assert router._digits("9" * 41, limit=40) is None
    with s.client() as (client, _):
        pos, _ino = s.pos()
        response = client.get("/m/logs/live/poll", params={"pos": pos, "ino": "9" * 30}, headers=HX)
    assert response.status_code == 200 and "rotated" in response.text.lower()


def test_no_file_yet_then_a_file_is_not_called_a_rotation(s: Setup) -> None:
    """Review: the poller carries ino=0 while no log exists; the first read is no rotation."""
    with s.client() as (client, _):
        response = client.get("/m/logs/live/poll", params={"pos": "0", "ino": "0"}, headers=HX)
    assert "Ready" in response.text and "rotated" not in response.text.lower()


def test_pause_cancels_an_in_flight_poll(s: Setup) -> None:
    with s.client() as (client, _):
        page = client.get("/m/logs/live").text
    assert 'hx-sync="#tail-poller:replace"' in page
