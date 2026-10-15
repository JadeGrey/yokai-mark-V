"""Turn combat events into short markdown log lines."""

from __future__ import annotations

from typing import Callable

from yokai.rpg.combat.models import Event
from yokai.textutil import escape_md


def _name(text: str) -> str:
    return f"**{escape_md(text)}**"


def _crit(event: Event) -> str:
    return " (crit)" if event.crit else ""


def _attack(e: Event) -> str:
    return f"{_name(e.actor)} strikes {_name(e.target)} for **{e.amount}**{_crit(e)}"


def _magic(e: Event) -> str:
    return f"{_name(e.actor)} casts at {_name(e.target)} for **{e.amount}**{_crit(e)}"


def _skill_damage(e: Event) -> str:
    return (
        f"{_name(e.actor)} uses *{escape_md(e.label)}* on {_name(e.target)} "
        f"for **{e.amount}**{_crit(e)}"
    )


def _dot_apply(e: Event) -> str:
    return (
        f"{_name(e.actor)} afflicts {_name(e.target)} with *{escape_md(e.label)}* "
        f"(**{e.amount}** per round)"
    )


def _dot_tick(e: Event) -> str:
    return f"{_name(e.target)} takes **{e.amount}** from *{escape_md(e.label)}*"


def _heal(e: Event) -> str:
    return (
        f"{_name(e.actor)} heals {_name(e.target)} for **{e.amount}** with *{escape_md(e.label)}*"
    )


def _buff(e: Event) -> str:
    return f"{_name(e.actor)} empowers {_name(e.target)} with *{escape_md(e.label)}*"


def _debuff(e: Event) -> str:
    return f"{_name(e.actor)} weakens {_name(e.target)} with *{escape_md(e.label)}*"


def _cleanse(e: Event) -> str:
    return f"{_name(e.actor)} cleanses {_name(e.target)} with *{escape_md(e.label)}*"


_FORMATTERS: dict[str, Callable[[Event], str]] = {
    "attack": _attack,
    "magic": _magic,
    "skill_damage": _skill_damage,
    "dot_apply": _dot_apply,
    "dot_tick": _dot_tick,
    "heal": _heal,
    "buff": _buff,
    "debuff": _debuff,
    "cleanse": _cleanse,
    "defend": lambda e: f"{_name(e.actor)} braces for impact",
    "downed": lambda e: f"{_name(e.target)} is down",
    "stage_cleared": lambda e: f"Stage {e.amount} cleared",
    "stage_start": lambda e: f"Stage {e.amount} begins",
    "timeout": lambda e: "The fight dragged on too long",
    "won": lambda e: "The party stands victorious",
    "lost": lambda e: "The party has fallen",
}


def describe(event: Event) -> str:
    """One markdown line for the fight log. Unknown event kinds produce an empty string."""
    formatter = _FORMATTERS.get(event.kind)
    return formatter(event) if formatter else ""


def describe_all(events: list[Event]) -> list[str]:
    """Log lines for a list of events, skipping any that have no text."""
    return [line for line in (describe(e) for e in events) if line]
