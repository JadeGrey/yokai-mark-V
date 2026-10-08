"""No view or modal may reuse a name discord.py uses internally.

discord.py calls private methods on views it keeps in its view store (for example `_refresh`
when a message with a view is edited). A view method with the same name but another signature
raises inside the gateway handler and takes the whole bot down, and no unit test that bypasses
the view store notices. This scan catches the clash statically.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from typing import Iterator

import discord
import pytest

import yokai.ui

# Names a subclass is meant to define or that discord.py generates for it.
ALLOWED = {
    "interaction_check",
    "on_timeout",
    "on_error",
    "on_submit",
    "__view_children_items__",
    "__modal_children_items__",
}


def _ui_classes() -> Iterator[type]:
    for info in pkgutil.iter_modules(yokai.ui.__path__):
        module = importlib.import_module(f"yokai.ui.{info.name}")
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if cls.__module__ != module.__name__:
                continue
            if issubclass(cls, (discord.ui.View, discord.ui.Modal)):
                yield cls


@pytest.mark.parametrize("cls", sorted(_ui_classes(), key=lambda c: c.__qualname__))
def test_a_view_does_not_shadow_a_discord_internal(cls: type) -> None:
    base = discord.ui.Modal if issubclass(cls, discord.ui.Modal) else discord.ui.View
    inherited = set(dir(base))
    shadowed = sorted(
        name
        for name in vars(cls)
        if name in inherited and name not in ALLOWED and not name.startswith("__")
    )
    assert shadowed == [], f"{cls.__qualname__} shadows discord.py internals: {shadowed}"


def test_the_scan_finds_the_views_it_is_meant_to_protect() -> None:
    names = {cls.__qualname__ for cls in _ui_classes()}
    assert {"LobbyView", "PlayView", "DuelView", "StakeModal"} <= names
