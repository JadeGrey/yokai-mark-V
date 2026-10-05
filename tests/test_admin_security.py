"""Tests for the panel's access control: Host, one-time token, session, Origin and CSRF."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from starlette.staticfiles import StaticFiles
from starlette.testclient import TestClient

from yokai_admin.core.auth import CSRF_HEADER, SESSION_COOKIE, SESSION_TTL_S, AuthState
from yokai_admin.core.security import install_security

PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"
TOKEN = "t0ken-for-tests"


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def make_app(tmp_path: Path, clock: Clock | None = None) -> tuple[FastAPI, AuthState]:
    auth = AuthState(token=TOKEN, clock=clock or Clock())
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    (tmp_path / "x.css").write_text("body{}")
    app.mount("/static", StaticFiles(directory=tmp_path), name="static")

    @app.get("/")
    def home(request: Request) -> dict[str, str]:
        return {"csrf": request.state.session.csrf}

    @app.api_route("/mutate", methods=["POST", "PUT", "PATCH", "DELETE"])
    def mutate() -> dict[str, bool]:
        return {"ok": True}

    install_security(app, auth, PORT)
    return app, auth


def logged_in(app: FastAPI) -> tuple[TestClient, str]:
    client = TestClient(app, base_url=BASE)
    response = client.get(f"/auth?token={TOKEN}", follow_redirects=False)
    assert response.status_code == 303
    return client, client.get("/").json()["csrf"]


def test_the_token_is_random_per_instance_and_long() -> None:
    first, second = AuthState(), AuthState()
    assert first.token != second.token and len(first.token) >= 32


def test_unauthenticated_requests_get_the_locked_page(tmp_path: Path) -> None:
    app, _ = make_app(tmp_path)
    response = TestClient(app, base_url=BASE).get("/")
    assert response.status_code == 401
    assert "link" in response.text and "set-cookie" not in response.headers


def test_static_files_are_served_without_a_session(tmp_path: Path) -> None:
    app, _ = make_app(tmp_path)
    assert TestClient(app, base_url=BASE).get("/static/x.css").status_code == 200


def test_a_wrong_token_is_refused_and_sets_no_cookie(tmp_path: Path) -> None:
    app, _ = make_app(tmp_path)
    response = TestClient(app, base_url=BASE).get("/auth?token=wrong", follow_redirects=False)
    assert response.status_code == 401 and "set-cookie" not in response.headers


def test_a_good_token_sets_an_httponly_strict_cookie(tmp_path: Path) -> None:
    app, _ = make_app(tmp_path)
    response = TestClient(app, base_url=BASE).get(f"/auth?token={TOKEN}", follow_redirects=False)
    cookie = response.headers["set-cookie"].lower()
    assert response.status_code == 303 and response.headers["location"] == "/"
    assert SESSION_COOKIE in cookie and "httponly" in cookie and "samesite=strict" in cookie


def test_the_token_works_only_once(tmp_path: Path) -> None:
    """Review Focus 5: a used link is dead, even from another browser."""
    app, _ = make_app(tmp_path)
    logged_in(app)
    again = TestClient(app, base_url=BASE).get(f"/auth?token={TOKEN}", follow_redirects=False)
    assert again.status_code == 401


def test_an_unknown_cookie_is_refused(tmp_path: Path) -> None:
    app, _ = make_app(tmp_path)
    client = TestClient(app, base_url=BASE, cookies={SESSION_COOKIE: "forged"})
    assert client.get("/").status_code == 401


def test_a_session_expires(tmp_path: Path) -> None:
    clock = Clock()
    app, _ = make_app(tmp_path, clock)
    client, _ = logged_in(app)
    clock.now += SESSION_TTL_S + 1
    assert client.get("/").status_code == 401


def test_a_non_loopback_host_is_refused_even_with_a_valid_session(tmp_path: Path) -> None:
    """Review Focus 1: DNS rebinding sends a loopback connection with a foreign Host header."""
    app, _ = make_app(tmp_path)
    client, _ = logged_in(app)
    assert client.get("/", headers={"host": "evil.example:8765"}).status_code == 400
    assert client.get("/", headers={"host": "localhost:8765"}).status_code == 200


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_unsafe_methods_need_the_csrf_header(tmp_path: Path, method: str) -> None:
    """Review Focus 2: every state-changing request is CSRF-protected by default."""
    app, _ = make_app(tmp_path)
    client, csrf = logged_in(app)
    assert getattr(client, method)("/mutate").status_code == 403
    assert getattr(client, method)("/mutate", headers={CSRF_HEADER: "wrong"}).status_code == 403
    assert getattr(client, method)("/mutate", headers={CSRF_HEADER: csrf}).status_code == 200


def test_a_foreign_origin_is_refused_even_with_the_right_csrf_token(tmp_path: Path) -> None:
    app, _ = make_app(tmp_path)
    client, csrf = logged_in(app)
    headers = {CSRF_HEADER: csrf, "origin": "http://evil.example"}
    assert client.post("/mutate", headers=headers).status_code == 403
    good = {CSRF_HEADER: csrf, "origin": BASE}
    assert client.post("/mutate", headers=good).status_code == 200


def test_unsafe_requests_without_a_session_are_refused(tmp_path: Path) -> None:
    app, _ = make_app(tmp_path)
    assert TestClient(app, base_url=BASE).post("/mutate").status_code == 401


def test_api_docs_are_not_exposed(tmp_path: Path) -> None:
    app, _ = make_app(tmp_path)
    client, _ = logged_in(app)
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_security_headers_are_on_every_response_and_pages_are_not_cached(tmp_path: Path) -> None:
    app, _ = make_app(tmp_path)
    client, _ = logged_in(app)
    page = client.get("/")
    csp = page.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "script-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert page.headers["x-content-type-options"] == "nosniff"
    assert page.headers["referrer-policy"] == "no-referrer"
    assert page.headers["cache-control"] == "no-store"
    assert "cache-control" not in client.get("/static/x.css").headers
    assert "content-security-policy" in client.get("/static/x.css").headers
