"""Tests for the module contract: loading, ordering, isolation and tiles."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import APIRouter

from yokai_admin.core.module import AdminModule, TileContext, load_modules, render_tiles
from yokai_admin.core.settings import PanelSettings
from yokai_admin.errors import ModuleConfigError

H = "tests.admin_helpers"
CTX = TileContext(settings=PanelSettings(db_path=Path("x.db"), log_dir=Path("logs")), now=1000)


def test_loaded_modules_are_sorted_by_nav_order() -> None:
    slots = load_modules([f"{H}:ALPHA", f"{H}:BETA"])
    assert [slot.module.id for slot in slots if slot.module] == ["beta", "alpha"]


def test_duplicate_ids_fail_fast() -> None:
    with pytest.raises(ModuleConfigError, match="alpha"):
        load_modules([f"{H}:ALPHA", f"{H}:ALPHA_AGAIN"])


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (f"{H}:MISSING", "AttributeError"),
        ("tests.no_such_module:X", "ModuleNotFoundError"),
        (f"{H}:NOT_A_MODULE", "TypeError"),
        ("no-colon-here", "ModuleConfigError"),
    ],
)
def test_a_broken_module_becomes_an_errored_slot_and_the_rest_still_load(
    path: str, expected: str
) -> None:
    slots = load_modules([path, f"{H}:BETA"])
    broken = [slot for slot in slots if slot.module is None]
    assert len(broken) == 1 and expected in (broken[0].error or "")
    assert broken[0].path == path
    assert [slot.module.id for slot in slots if slot.module] == ["beta"]
    assert slots[-1] is broken[0]  # errored slots sort last


@pytest.mark.parametrize("bad_id", ["", "Alpha", "a b", "a/b", "1abc", "x" * 40])
def test_module_ids_are_validated(bad_id: str) -> None:
    with pytest.raises(ModuleConfigError):
        AdminModule(id=bad_id, title="T", router=APIRouter())


def test_a_failing_tile_is_isolated_and_modules_without_tiles_are_skipped() -> None:
    slots = load_modules([f"{H}:ALPHA", f"{H}:BROKEN_TILE", f"{H}:BETA"])
    results = {result.module_id: result for result in render_tiles(slots, CTX)}
    assert set(results) == {"alpha", "broken-tile"}  # beta has no tile
    assert results["alpha"].tile is not None and results["alpha"].tile.value == "3 tracks"
    assert results["broken-tile"].tile is None
    assert results["broken-tile"].error == "Could not read this tile."
