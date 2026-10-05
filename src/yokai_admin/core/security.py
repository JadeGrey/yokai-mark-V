"""Loopback-only access control: Host and Origin checks, session gate, CSRF and headers."""

from __future__ import annotations

import html
import secrets

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.datastructures import MutableHeaders
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from yokai_admin.core.auth import CSRF_HEADER, SESSION_COOKIE, SESSION_TTL_S, AuthState

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; font-src 'self'; "
    "img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'self'; "
    "frame-ancestors 'none'"
)
_STATIC_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Resource-Policy": "same-origin",
}
_LOCKED_DEFAULT = (
    "Open the panel with the link printed in the console where you started it. "
    "The link works once; restart the panel to get a new one."
)


def locked_page(message: str = _LOCKED_DEFAULT) -> str:
    """A self-contained page for requests without a session (no module templates involved)."""
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<link rel="stylesheet" href="/static/panel.css"><link rel="icon" href="data:,">'
        "<title>Panel locked - Yokai admin</title></head>"
        f'<body><main class="locked"><h1>Panel locked</h1><p>{html.escape(message)}</p>'
        "</main></body></html>"
    )


def _text_response(status: int, message: str) -> Response:
    return HTMLResponse(locked_page(message), status_code=status)


class SecurityMiddleware:
    """Pure ASGI (not BaseHTTPMiddleware) so streaming responses such as a log tail still work."""

    def __init__(self, app: ASGIApp, auth: AuthState, port: int) -> None:
        self.app = app
        self.auth = auth
        self.allowed_origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":  # the panel uses no websockets
            await send({"type": "websocket.close", "code": 1008})
            return

        path: str = scope["path"]
        method: str = scope["method"]
        send = self._with_headers(send, path)
        request = Request(scope)

        if method not in SAFE_METHODS:
            origin = request.headers.get("origin")
            if origin is not None and origin not in self.allowed_origins:
                await _text_response(403, "Cross-origin request blocked.")(scope, receive, send)
                return

        if path == "/auth" or (path.startswith("/static/") and method in SAFE_METHODS):
            await self.app(scope, receive, send)
            return

        session = self.auth.lookup(request.cookies.get(SESSION_COOKIE))
        if session is None:
            await _text_response(401, _LOCKED_DEFAULT)(scope, receive, send)
            return

        if method not in SAFE_METHODS:
            supplied = request.headers.get(CSRF_HEADER, "")
            if not secrets.compare_digest(supplied.encode(), session.csrf.encode()):
                message = "The security token is missing or wrong. Reload the page and try again."
                await _text_response(403, message)(scope, receive, send)
                return

        scope.setdefault("state", {})["session"] = session
        await self.app(scope, receive, send)

    @staticmethod
    def _with_headers(send: Send, path: str) -> Send:
        async def wrapped(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in _STATIC_HEADERS.items():
                    headers[name] = value
                if not path.startswith("/static/"):
                    headers["Cache-Control"] = "no-store"
            await send(message)

        return wrapped


def install_security(app: FastAPI, auth: AuthState, port: int) -> None:
    """Add the /auth route and the middleware stack (Host check outermost, then the gate)."""

    @app.get("/auth", include_in_schema=False)
    def exchange_token(token: str = "") -> Response:
        session = auth.exchange(token)
        if session is None:
            return _text_response(
                401, "That link is no longer valid. Restart the panel to get a new one."
            )
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            SESSION_COOKIE,
            session.id,
            max_age=SESSION_TTL_S,
            httponly=True,
            samesite="strict",
            path="/",
        )
        return response

    app.add_middleware(SecurityMiddleware, auth=auth, port=port)
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"], www_redirect=False
    )
