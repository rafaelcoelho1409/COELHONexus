"""Page extraction: a slice of a PDF's pages in, markdown + headings + counts + cropped figures + the structured document out.
`service` (the Docling wrapper) is not imported here: it loads torch and the layout models, so API and non-book worker tasks keep
their lighter import path. Access the `service` attribute only when extracting."""
from __future__ import annotations

import importlib

from . import config, domain, entities, params

__all__ = ["config", "domain", "entities", "params"]


def __getattr__(name: str):
    if name == "service":
        module = importlib.import_module(f"{__name__}.service")
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
