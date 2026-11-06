"""corpus_normalize — Pydantic schemas (LLM/storage boundary validation)."""
from __future__ import annotations
from . import versions

from pydantic import BaseModel, Field



class NormalizeStats(BaseModel):
    """Counts of what each pass touched. Surfaced on the ingestion UI +
    emitted as Langfuse OTel attrs so per-framework noise floors are
    visible in observability."""
    fence_meta_stripped:        int  = 0
    boundary_markers_stripped:  int  = 0
    orphan_tags_stripped:       int  = 0
    container_admonitions:      int  = 0
    permalink_debris_stripped:  int  = 0
    nested_fences_hoisted:      int  = 0
    empty_media_stripped:       int  = 0
    agent_banner_stripped:      int  = 0
    license_header_stripped:    int  = 0
    agent_footer_stripped:      int  = 0
    heading_anchors_stripped:   int  = 0
    img_tags_rewritten:         int  = 0
    code_annotations_stripped:  int  = 0
    code_copy_lines_stripped:   int  = 0
    toc_tables_stripped:        int  = 0
    viewcode_links_stripped:    int  = 0
    trailing_rules_stripped:    int  = 0
    title_promoted:             bool = False
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
    frontmatter: dict           = Field(default_factory = dict)
    stats:       NormalizeStats
    version:     int            = versions.NORMALIZER_VERSION
