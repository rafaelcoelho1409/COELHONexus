"""Page extraction: a slice of a PDF's pages in, markdown + headings + counts + cropped figures + the structured document out.
`service` (the Docling wrapper) is not imported here: it loads torch and the layout models, so API and non-book worker tasks keep
their lighter import path. Import `domains.bd.ingestion.extract.service` only when extracting."""
from __future__ import annotations
from . import config, domain, entities, params


__all__ = ["config", "domain", "entities", "params"]
