"""mcp tools — one package per tool family, each exposing `service.register(mcp)`."""
from __future__ import annotations
from . import system


__all__ = ["system"]
