"""Book ingestion — entry: runtime.dispatch.service.run(book_id, run_id); a PDF is read for its outline, then extracted in page slices."""
from __future__ import annotations
from . import errors, extract, outline, params, runtime, storage
