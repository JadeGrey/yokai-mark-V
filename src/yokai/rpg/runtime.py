"""The RPG's loaded content and store, bundled so the bot and cogs share one object."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from yokai.rpg.content import Content, load_content
from yokai.rpg.gear_store import GearStore
from yokai.rpg.store import RpgStore
from yokai.rpg.trade import TradeSession

if TYPE_CHECKING:
    from yokai.storage.db import Database


@dataclass(frozen=True, slots=True)
class RpgRuntime:
    """Everything the RPG cogs need. Created once at startup when RPG_ENABLED is true.

    `active_users` is the set of members who are mid-fight (not persisted: a restart clears it,
    and the store refunds the attempts of fights that were left open). `tasks` keeps running
    fight tasks alive. `trading` maps each member with an open trade to that session.
    """

    content: Content
    store: RpgStore
    gear: GearStore
    active_users: set[int] = field(default_factory=set)
    tasks: set[asyncio.Task[None]] = field(default_factory=set)
    trading: dict[int, TradeSession] = field(default_factory=dict)

    @classmethod
    def create(cls, db: Database, content_dir: Path | str) -> RpgRuntime:
        """Load and validate the content files, then bind the store. Raises ContentError."""
        return cls(content=load_content(content_dir), store=RpgStore(db), gear=GearStore(db))
