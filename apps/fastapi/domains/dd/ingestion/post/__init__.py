"""Post-ingest normalization."""
from .constants import (
    MONOLITH_SPLIT_THRESHOLD_BYTES,
    SPLIT_MIN_SECTION_BYTES,
    _SOURCE_LINE_RE,
    _SOURCE_MIN_MARKERS,
)
from .service import (
    _slugify_heading,
    _split_by_source_markers,
    _split_markdown_by_headings,
    _summary,
    apply_to_store,
    dedup_pages,
    split_monolith,
)

__all__ = [
    # constants
    "MONOLITH_SPLIT_THRESHOLD_BYTES",
    "SPLIT_MIN_SECTION_BYTES",
    "_SOURCE_LINE_RE",
    "_SOURCE_MIN_MARKERS",
    # functions
    "_split_markdown_by_headings",
    "_slugify_heading",
    "_split_by_source_markers",
    "split_monolith",
    "dedup_pages",
    "apply_to_store",
    "_summary",
]
