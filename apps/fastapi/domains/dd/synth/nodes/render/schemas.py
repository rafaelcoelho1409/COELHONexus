"""render — Pydantic schemas (LLM/storage boundary validation)."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from .versions import RENDER_SCHEMA_VERSION, RENDER_TEMPLATE_VERSION


ArtifactName = Literal["README.md"]


class RenderedArtifact(BaseModel):
    """One persisted MinIO blob produced by this node."""
    name:       ArtifactName
    minio_key:  str
    size_bytes: int
    sha256:     str    # full 64-char SHA-256 of the bytes (NOT the 16-hex
                       # vault-hash prefix). For audit/provenance.


class CodeRefResolution(BaseModel):
    """Per-code-ref audit detail. tier: 'verbatim'=byte-exact, 'derived'=AI-generated+AST-valid, 'hallucinated'=missing/drift/AST-fail."""
    hash:                str
    found_in_vault:      bool
    source_key:          Optional[str] = None
    byte_drift:          bool = False
    materialized_chars:  int = 0
    section_id:          str = ""        # which section referenced it
    tier:                Literal["verbatim", "derived", "hallucinated"] = "verbatim"


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

    # 3-tier classification counts.
    n_verbatim:             int = 0
    n_derived:              int = 0
    n_hallucinated:         int = 0

    # Per-ref details for downstream debugging. Cap to keep blob small.
    resolution_details:     list[CodeRefResolution] = Field(
        default_factory = list,
    )


class RenderResult(BaseModel):
    """Full render result — persisted as render-latest.json."""
    schema_version:        str = RENDER_SCHEMA_VERSION
    template_version:      str = RENDER_TEMPLATE_VERSION
    chapter_id:            str
    chapter_title:         str
    framework_slug:        str
    artifacts:             list[RenderedArtifact]   # 1 entry (README.md)
    audit:                 AuditResult
    rendered_chars:        int                       # README.md size
    n_sections:            int
    n_subtopics_total:     int        # v2 cookbook: replaces n_paragraphs_total
    n_citations_total:     int
    sawc_manifest_hash:    str
    mgsr_manifest_hash:    str
    render_manifest_hash:  str
    wall_ms:               int
    # Persisted so the Study chapter strip can re-open the LangGraph canvas after a page refresh.
    thread_id:             str = ""
