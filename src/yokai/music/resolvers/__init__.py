"""Resolvers package exports."""

from __future__ import annotations

from yokai.music.resolvers.base import Resolver
from yokai.music.resolvers.ytdlp import YtDlpResolver

__all__ = ["Resolver", "YtDlpResolver"]
