"""Book Distiller RAG indexing, separated into workflow and data access."""
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import ai, embedding_migration, source


__all__ = ["ai", "embedding_migration", "source"]


def __getattr__(name: str):
    if name in __all__:
        module = importlib.import_module(f"{__name__}.{name}")
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
