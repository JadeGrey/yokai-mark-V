"""One-time launch token, in-memory sessions and per-session CSRF tokens."""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

SESSION_COOKIE = "yokai_admin_session"
CSRF_HEADER = "x-csrf-token"
SESSION_TTL_S = 12 * 3600


@dataclass(slots=True)
class Session:
    """A logged-in browser: its cookie id, its CSRF token and when it ends."""

    id: str
    csrf: str
    expires_at: float


class AuthState:
    """Holds the launch token (valid for exactly one exchange) and the live sessions."""

    def __init__(
        self,
        token: Optional[str] = None,
        clock: Callable[[], float] = time.time,
        ttl_s: int = SESSION_TTL_S,
    ) -> None:
        self._token = token or secrets.token_urlsafe(32)
        self._clock = clock
        self._ttl_s = ttl_s
        self._token_used = False
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    @property
    def token(self) -> str:
        return self._token

    def exchange(self, candidate: str) -> Optional[Session]:
        """Trade the launch token for a session. Works once; wrong or reused tokens get None."""
        with self._lock:
            if self._token_used:
                return None
            if not secrets.compare_digest(candidate.encode(), self._token.encode()):
                return None
            self._token_used = True
            session = Session(
                id=secrets.token_urlsafe(32),
                csrf=secrets.token_urlsafe(32),
                expires_at=self._clock() + self._ttl_s,
            )
            self._sessions[session.id] = session
            return session

    def lookup(self, session_id: Optional[str]) -> Optional[Session]:
        """Return the live session for a cookie value, dropping it if it has expired."""
        if not session_id:
            return None
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            if self._clock() >= session.expires_at:
                del self._sessions[session_id]
                return None
            return session
