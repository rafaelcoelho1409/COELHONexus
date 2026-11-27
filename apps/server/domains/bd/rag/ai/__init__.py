"""Run lifecycle and application services for BD Agentic RAG."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import (
        domain,
        errors,
        keys,
        params,
        progress,
        publication,
        replay,
        retrieval,
        runs,
        task,
        versions,
    )


__all__ = [
    "domain",
    "errors",
    "keys",
    "params",
    "progress",
    "publication",
    "replay",
    "retrieval",
    "runs",
    "task",
    "versions",
]


def __getattr__(name: str):
    if name in __all__:
        module = importlib.import_module(f"{__name__}.{name}")
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
