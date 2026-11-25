"""FastMCP application entrypoint mounted by the server process."""
from __future__ import annotations

from typing import TYPE_CHECKING
import importlib


if TYPE_CHECKING:
    from . import server


__all__ = ["server"]


def __getattr__(name: str):
    """Load the mounted FastMCP server only when a caller accesses it."""
    if name in __all__:
        module = importlib.import_module(f"{__name__}.{name}")
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
