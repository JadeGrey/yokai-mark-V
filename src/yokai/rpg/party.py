"""Party lobby state and eligibility: who may join a mission, and when it may start.

Pure logic: no Discord objects, no database. The lobby view calls these with fresh characters
at join time and again at start time, so rules live in one place and are easy to test.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Optional

from yokai.errors import (
    AlreadyFightingError,
    AlreadyInPartyError,
    MissionRankError,
    NoAttemptsError,
    NoCharacterError,
    NotInPartyError,
    PartyFullError,
    PartyTooSmallError,
    YokaiError,
)
from yokai.rpg import guild
from yokai.rpg.content import Content
from yokai.rpg.models import (
    MAX_PARTY,
    NO_VOICE,
    Character,
    Mission,
    StreakRules,
    VoiceRules,
)


@dataclass(frozen=True, slots=True)
class PartyMember:
    """A lobby seat: enough to draw the lobby message."""

    user_id: int
    name: str
    rank: str
    level: int


@dataclass(slots=True)
class Lobby:
    """A mission, its leader and the members who have taken a seat (the leader is first)."""

    mission: Mission
    leader_id: int
    members: dict[int, PartyMember] = field(default_factory=dict)

    @property
    def size(self) -> int:
        return len(self.members)

    @property
    def is_full(self) -> bool:
        return self.size >= MAX_PARTY

    def add(self, member: PartyMember) -> None:
        if member.user_id in self.members:
            raise AlreadyInPartyError()
        if self.is_full:
            raise PartyFullError(MAX_PARTY)
        self.members[member.user_id] = member

    def remove(self, user_id: int) -> PartyMember:
        member = self.members.pop(user_id, None)
        if member is None:
            raise NotInPartyError()
        return member

    def check_ready(self) -> None:
        """Raise PartyTooSmallError if the mission needs more members than are seated."""
        if self.size < self.mission.min_party:
            raise PartyTooSmallError(self.mission.min_party, self.size)


def check_member(
    content: Content,
    character: Character,
    mission: Mission,
    *,
    today: str,
    base_attempts: int,
    streak_rules: StreakRules,
    voice_rules: VoiceRules = NO_VOICE,
    resets_ts: int,
    fighting: Collection[int],
) -> None:
    """Raise a YokaiError if this member may not take the mission right now."""
    if not guild.can_enter(content, character.rank, mission):
        raise MissionRankError(mission.rank, character.rank)
    status = guild.attempt_status(character, today, base_attempts, streak_rules, voice_rules)
    if mission.attempt_cost > status.remaining:
        raise NoAttemptsError(status.remaining, mission.attempt_cost, resets_ts)
    if character.user_id in fighting:
        raise AlreadyFightingError()


def screen_members(
    content: Content,
    characters: dict[int, Optional[Character]],
    mission: Mission,
    *,
    today: str,
    base_attempts: int,
    streak_rules: StreakRules,
    voice_rules: VoiceRules = NO_VOICE,
    resets_ts: int,
    fighting: Collection[int],
) -> tuple[list[Character], dict[int, YokaiError]]:
    """Split lobby members into those still eligible and those who are not (with the reason).

    A missing character (reset by an admin while waiting) counts as ineligible.
    """
    eligible: list[Character] = []
    rejected: dict[int, YokaiError] = {}
    for user_id, character in characters.items():
        if character is None:
            rejected[user_id] = NoCharacterError(own=False)
            continue
        try:
            check_member(
                content,
                character,
                mission,
                today=today,
                base_attempts=base_attempts,
                streak_rules=streak_rules,
                voice_rules=voice_rules,
                resets_ts=resets_ts,
                fighting=fighting,
            )
        except YokaiError as err:
            rejected[user_id] = err
        else:
            eligible.append(character)
    return eligible, rejected
