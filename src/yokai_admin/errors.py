"""Typed errors for the admin panel."""

from __future__ import annotations


class PanelError(Exception):
    """Base class for admin panel errors."""


class PanelConfigError(PanelError):
    """Raised when panel configuration is invalid."""


class PanelDbError(PanelError):
    """Raised when the bot's database cannot be used by the panel."""


class ModuleConfigError(PanelError):
    """Raised when the module registry is invalid (for example a duplicate id)."""
