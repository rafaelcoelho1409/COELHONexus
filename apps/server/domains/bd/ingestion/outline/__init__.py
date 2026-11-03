"""A book's PDF outline: read from the file, cleaned into a tree, and used to place each page in its chapter.
`service` (the PDF reader) is not imported here: it is only needed while extracting a book. Import
`domains.bd.ingestion.outline.service` only when extracting."""
from __future__ import annotations
from . import domain, entities, params


__all__ = ["domain", "entities", "params"]
