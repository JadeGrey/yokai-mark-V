"""In-memory registry enforcing one active game per member."""

from __future__ import annotations

from yokai.errors import AlreadyInGameError


class ActiveGames:
    """Tracks which members are mid-game. Deliberately not persisted: a restart clears it."""

    def __init__(self) -> None:
        self._users: set[int] = set()

    def claim(self, user_id: int) -> None:
        if user_id in self._users:
            raise AlreadyInGameError()
        self._users.add(user_id)

    def release(self, user_id: int) -> None:
        self._users.discard(user_id)

    def is_active(self, user_id: int) -> bool:
        return user_id in self._users
