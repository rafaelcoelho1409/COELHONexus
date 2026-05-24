"""render — Pydantic schemas + type aliases."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from .constants import RENDER_SCHEMA_VERSION, RENDER_TEMPLATE_VERSION


ArtifactName = Literal["README.md", "challenges.md", "flashcards.json"]


# =============================================================================
# Pydantic schemas — persisted side
# =============================================================================
class RenderedArtifact(BaseModel):
    """One persisted MinIO blob produced by this node."""
    name:       ArtifactName
    minio_key:  str
    size_bytes: int
    sha256:     str    # full 64-char SHA-256 of the bytes (NOT the 16-hex
                       # vault-hash prefix). For audit/provenance.


class CodeRefResolution(BaseModel):
    """Per-code-ref audit detail. Useful for debugging vault drift."""
    hash:                str
    found_in_vault:      bool
    source_key:          Optional[str] = None
    byte_drift:          bool = False
    materialized_chars:  int = 0
    section_id:          str = ""        # which section referenced it


class AuditResult(BaseModel):
    """Round-trip audit summary. The `audit_passed` flag is the
    chapter-level go/no-go signal."""
    n_code_refs_referenced: int          # union across all sections
    n_resolved:             int          # found in some source vault
    n_missing:              list[str]    # referenced but not in any vault
    n_orphan_unused:        list[str]    # in vault but no section referenced
    n_byte_drift:           list[str]    # re-hash != vault hash
    sentinels_in_output:    int          # MUST be 0 — defense in depth
    audit_passed:           bool

    # Per-ref details for downstream debugging. Cap to keep blob small.
    resolution_details:     list[CodeRefResolution] = Field(default_factory=list)


class RenderResult(BaseModel):
    """Full render result — what gets persisted as render-latest.json.

    The three CONTENT artifacts (README.md / challenges.md /
    flashcards.json) live alongside this metadata blob in the same
    chapter prefix."""
    schema_version:        str = RENDER_SCHEMA_VERSION
    template_version:      str = RENDER_TEMPLATE_VERSION
    chapter_id:            str
    chapter_title:         str
    framework_slug:        str
    artifacts:             list[RenderedArtifact]   # 3 entries
    audit:                 AuditResult
    rendered_chars:        int                       # README.md size
    n_sections:            int
    n_subtopics_total:     int        # v2 cookbook: replaces n_paragraphs_total
    n_citations_total:     int
    sawc_manifest_hash:    str
    mgsr_manifest_hash:    str
    render_manifest_hash:  str
    wall_ms:               int
    # The synth thread that produced this render. Persisted so the Study
    # chapter strip can re-open the chapter's LangGraph canvas (node
    # statuses) after a page refresh — the per-run thread_id is otherwise
    # ephemeral. Optional/defaulted so pre-existing blobs still parse.
    thread_id:             str = ""
