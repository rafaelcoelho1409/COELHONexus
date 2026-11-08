"""YCS Source feature: YouTube discovery and metadata search.

The feature package exposes its subdomains lazily so importing one YCS
feature does not load unrelated integration stacks.
"""
from __future__ import annotations

from typing import TYPE_CHECKING
import importlib


if TYPE_CHECKING:
    from . import content


__all__ = [
    "content",
]


def __getattr__(name: str):
    if name in __all__:
        module = importlib.import_module(f"{__name__}.{name}")
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
