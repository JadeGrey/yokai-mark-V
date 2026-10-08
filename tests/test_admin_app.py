"""Tests for the app factory: routing, shell, nav, module isolation and headers."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.admin_helpers import panel_client
from yokai_admin.errors import ModuleConfigError

H = "tests.admin_helpers"


def test_the_root_redirects_to_the_first_module(tmp_path: Path) -> None:
    with panel_client(tmp_path, [f"{H}:ALPHA", f"{H}:BETA"]) as (client, _):
        response = client.get("/", follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"] == "/m/beta"


def test_module_routers_mount_under_m_id(tmp_path: Path) -> None:
    with panel_client(tmp_path, [f"{H}:ALPHA"]) as (client, _):
        assert client.get("/m/alpha").json() == {"module": "alpha"}


def test_a_new_modules_routes_are_locked_without_a_session(tmp_path: Path) -> None:
    """Review Focus 2: a module cannot forget auth; the gate covers every route."""
    with panel_client(tmp_path, [f"{H}:ALPHA"], login=False) as (client, _):
        assert client.get("/m/alpha").status_code == 401
        assert client.post("/m/alpha").status_code == 401


def test_the_shell_lists_modules_in_order_and_marks_the_active_one(tmp_path: Path) -> None:
    with panel_client(tmp_path, [f"{H}:ALPHA", f"{H}:DEMO"]) as (client, csrf):
        page = client.get("/m/demo").text
        assert page.index("Demo") < page.index("Alpha")  # nav_order 5 before 20
        assert re.search(r'<a href="/m/demo" aria-current="page">Demo</a>', page)
        assert '<a href="/m/alpha">Alpha</a>' in page
        assert f'"X-CSRF-Token": "{csrf}"' in page  # HTMX sends it on every request
        assert "htmx.min.js" in page and "panel.css" in page and "/static/panel.js" in page


def test_hostile_text_in_a_page_and_in_a_module_path_is_escaped(tmp_path: Path) -> None:
    """Review Focus 3: nothing user- or config-supplied is rendered as HTML."""
    evil_path = f"{H}:<img src=x onerror=alert(1)>"
    with panel_client(tmp_path, [f"{H}:DEMO", evil_path]) as (client, _):
        page = client.get("/m/demo", params={"m": "<script>alert(1)</script>"}).text
        assert "<script>alert(1)</script>" not in page
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
        assert "<img src=x" not in page and "&lt;img src=x" in page


def test_a_failed_module_shows_in_the_nav_while_the_others_work(tmp_path: Path) -> None:
    with panel_client(tmp_path, [f"{H}:DEMO", f"{H}:MISSING"]) as (client, _):
        page = client.get("/m/demo").text
        assert "Module failed to load" in page and f"{H}:MISSING" in page
        assert client.get("/m/demo").status_code == 200


def test_duplicate_module_ids_stop_startup(tmp_path: Path) -> None:
    with pytest.raises(ModuleConfigError):
        with panel_client(tmp_path, [f"{H}:ALPHA", f"{H}:ALPHA_AGAIN"]):
            pass


def test_with_no_loadable_module_the_root_explains_what_to_do(tmp_path: Path) -> None:
    with panel_client(tmp_path, ["tests.no_such_module:X"]) as (client, _):
        response = client.get("/")
        assert response.status_code == 200 and "No modules loaded" in response.text
        assert "tests.no_such_module:X" in response.text


def test_api_docs_are_disabled(tmp_path: Path) -> None:
    with panel_client(tmp_path, [f"{H}:ALPHA"]) as (client, _):
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404


def test_static_assets_are_served(tmp_path: Path) -> None:
    with panel_client(tmp_path, [f"{H}:ALPHA"], login=False) as (client, _):
        css = client.get("/static/panel.css")
        assert css.status_code == 200 and "text/css" in css.headers["content-type"]


def test_the_shell_has_a_hidden_request_failed_notice(tmp_path: Path) -> None:
    with panel_client(tmp_path, [f"{H}:DEMO"]) as (client, _):
        page = client.get("/m/demo").text
    tag = page.split('id="request-error"')[1].split(">")[0]
    assert "hidden" in tag and "That request failed" in page


def test_number_filters_are_available_to_every_template(tmp_path: Path) -> None:
    with panel_client(tmp_path, [f"{H}:DEMO"]) as (client, _):
        env = client.app.state.templates.env  # type: ignore[attr-defined]
    assert env.filters["number"](1500) == "1,500" and env.filters["signed"](-3) == "-3"
    assert env.filters["age"](300) == "5 minutes"
