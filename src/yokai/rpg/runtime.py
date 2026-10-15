"""The RPG's loaded content and store, bundled so the bot and cogs share one object."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from yokai.rpg.content import Content, load_content
from yokai.rpg.store import RpgStore

if TYPE_CHECKING:
    from yokai.storage.db import Database


@dataclass(frozen=True, slots=True)
class RpgRuntime:
    """Everything the RPG cogs need. Created once at startup when RPG_ENABLED is true."""

    content: Content
    store: RpgStore

    @classmethod
    def create(cls, db: Database, content_dir: Path | str) -> RpgRuntime:
        """Load and validate the content files, then bind the store. Raises ContentError."""
        return cls(content=load_content(content_dir), store=RpgStore(db))
