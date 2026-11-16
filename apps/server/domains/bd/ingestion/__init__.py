"""Book ingestion — entry: runtime.dispatch.service.run(book_id, run_id); a PDF is read for its outline, then extracted in page slices."""
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

from . import errors, extract, outline, params, runtime, storage


if TYPE_CHECKING:
    from . import ai


__all__ = ["ai", "errors", "extract", "outline", "params", "runtime", "storage"]


def __getattr__(name: str):
    if name == "ai":
        module = importlib.import_module(f"{__name__}.ai")
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
