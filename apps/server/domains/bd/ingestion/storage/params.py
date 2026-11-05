from __future__ import annotations


MARKDOWN_TYPE = "text/markdown"
JSON_TYPE = "application/json"
PDF_TYPE = "application/pdf"
PDF_MAGIC = b"%PDF-"
EPUB_TYPE = "application/epub+zip"
EPUB_MIMETYPE = b"application/epub+zip"

# A book's manifest is written compact; the library lists this many manifests at a time.
MANIFEST_JSON_SEPARATORS = (",", ":")
LIBRARY_READ_CONCURRENCY = 16

# Where a book is in its life, as the manifest says it.
STATUS_QUEUED = "queued"
STATUS_EXTRACTING = "extracting"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"
STATUS_READY = "ready"
