"""corpus_normalize — Pydantic schema types."""
from __future__ import annotations

from pydantic import BaseModel, Field

from .constants import _NORMALIZER_VERSION


# ── Pydantic schema ───────────────────────────────────────────────────

class NormalizeStats(BaseModel):
    """Counts of what each pass touched. Surfaced on the ingestion UI
    + emitted as Langfuse OTel attrs so per-framework noise floors are
    visible in observability."""
    fence_meta_stripped:        int  = 0
    boundary_markers_stripped:  int  = 0
    orphan_tags_stripped:       int  = 0
    container_admonitions:      int  = 0
    frontmatter_extracted:      bool = False
    html_entities_decoded:      int  = 0
    blank_lines_collapsed:      int  = 0
    trailing_ws_lines:          int  = 0
    zero_width_chars_stripped:  int  = 0
    input_bytes:                int  = 0
    output_bytes:                int = 0


class NormalizedDoc(BaseModel):
    """Output of normalize_doc. `body` REPLACES the raw markdown at
    `ingestion/{slug}/pages/{key}`. `frontmatter` is merged into the
    manifest entry's metadata (not re-emitted into the body)."""
    body:        str
    frontmatter: dict           = Field(default_factory=dict)
    stats:       NormalizeStats
    version:     int            = _NORMALIZER_VERSION
