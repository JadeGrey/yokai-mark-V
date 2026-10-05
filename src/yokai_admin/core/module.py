"""The contract every admin module implements, and the loader that isolates broken ones."""

from __future__ import annotations

import importlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, Optional, Sequence

from fastapi import APIRouter

from yokai_admin.core.settings import PanelSettings
from yokai_admin.errors import ModuleConfigError

logger = logging.getLogger(__name__)

TileStatus = Literal["neutral", "ok", "warn", "error"]
_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_TILE_ERROR = "Could not read this tile."


@dataclass(frozen=True, slots=True)
class Tile:
    """A small status readout a module contributes to the Dashboard."""

    title: str
    value: str
    status: TileStatus = "neutral"
    detail: str = ""


@dataclass(frozen=True, slots=True)
class TileContext:
    """What a tile callable may use: panel settings and the current Unix time."""

    settings: PanelSettings
    now: int


@dataclass(frozen=True, slots=True)
class AdminModule:
    """One feature of the panel: a router mounted at /m/<id>, optional templates and tile."""

    id: str
    title: str
    router: APIRouter
    nav_order: int = 100
    templates_dir: Optional[Path] = None
    tile: Optional[Callable[[TileContext], Tile]] = None

    def __post_init__(self) -> None:
        if not _ID_RE.match(self.id):
            raise ModuleConfigError(
                f"Invalid module id {self.id!r}: use 1-32 lowercase letters, digits or hyphens, "
                "starting with a letter."
            )


@dataclass(frozen=True, slots=True)
class ModuleSlot:
    """A configured module path and what came of loading it."""

    path: str
    module: Optional[AdminModule]
    error: Optional[str] = None


@dataclass(frozen=True, slots=True)
class TileResult:
    """One Dashboard row: a tile, or the reason it could not be read."""

    module_id: str
    module_title: str
    tile: Optional[Tile]
    error: Optional[str]


def _import_module_object(path: str) -> AdminModule:
    module_name, separator, attribute = path.partition(":")
    if not separator or not module_name or not attribute:
        raise ModuleConfigError(f"Module path {path!r} must look like 'package.module:NAME'.")
    candidate = getattr(importlib.import_module(module_name), attribute)
    if not isinstance(candidate, AdminModule):
        raise TypeError(f"{path} is a {type(candidate).__name__}, not an AdminModule.")
    return candidate


def load_modules(paths: Sequence[str]) -> list[ModuleSlot]:
    """Import every configured module; a failure becomes an errored slot, not a crash.

    Duplicate module ids are a configuration mistake and raise `ModuleConfigError`.
    """
    slots: list[ModuleSlot] = []
    seen: dict[str, str] = {}
    for path in paths:
        try:
            module = _import_module_object(path)
        # Isolation boundary: importing a module runs arbitrary code, and one broken module
        # must never stop the panel or hide the others.
        except Exception as exc:
            logger.error("Admin module %s failed to load: %s", path, exc)
            slots.append(ModuleSlot(path=path, module=None, error=f"{type(exc).__name__}: {exc}"))
            continue
        if module.id in seen:
            raise ModuleConfigError(
                f"Duplicate module id {module.id!r} (from {seen[module.id]} and {path})."
            )
        seen[module.id] = path
        slots.append(ModuleSlot(path=path, module=module))

    def sort_key(slot: ModuleSlot) -> tuple[int, int, str]:
        if slot.module is None:
            return (1, 0, slot.path)
        return (0, slot.module.nav_order, slot.module.id)

    return sorted(slots, key=sort_key)


def render_tiles(slots: Sequence[ModuleSlot], ctx: TileContext) -> list[TileResult]:
    """Call every module's tile callable; one that raises yields an error row instead."""
    results: list[TileResult] = []
    for slot in slots:
        module = slot.module
        if module is None or module.tile is None:
            continue
        try:
            results.append(TileResult(module.id, module.title, module.tile(ctx), None))
        # Same isolation boundary: a tile is module code and must not break the Dashboard.
        except Exception as exc:
            logger.error("Tile for module %s failed: %s", module.id, exc)
            results.append(TileResult(module.id, module.title, None, _TILE_ERROR))
    return results
