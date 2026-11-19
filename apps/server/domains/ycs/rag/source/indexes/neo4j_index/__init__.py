"""LLM-extracted YouTube transcript knowledge graph projection."""
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import domain, params, patterns, schemas, service


__all__ = ["domain", "params", "patterns", "schemas", "service"]


def __getattr__(name: str):
    if name in __all__:
        module = importlib.import_module(f"{__name__}.{name}")
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
