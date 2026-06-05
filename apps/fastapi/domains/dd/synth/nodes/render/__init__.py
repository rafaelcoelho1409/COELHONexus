"""render — Materialize + audit + persist library (subpackage)."""

from .domain import (
    build_section_context,
    compute_audit,
    dedupe_and_align_sections,
    hash_block,
    load_render_payload,
    merge_vault_entries,
    render_chapter_md,
    sha256_bytes,
)
from .keys import (
    artifact_key,
    latest_blob_key,
    mgsr_latest_key,
    planner_latest_key,
    sawc_latest_key,
    source_key_to_vault_key,
    versioned_blob_key,
)
from .params import VAULT_HASH_LEN
from .patterns import SENTINEL_RE
from .prompts import (
    CHAPTER_MD_TEMPLATE,
    CHALLENGES_MD_TEMPLATE,
    JINJA_ENV,
)
from .schemas import (
    ArtifactName,
    AuditResult,
    CodeRefResolution,
    RenderedArtifact,
    RenderResult,
)
from .service import render_audit_write_run
from .versions import (
    HASH_ALGO,
    RENDER_SCHEMA_VERSION,
    RENDER_TEMPLATE_VERSION,
)


__all__ = [
    "ArtifactName",
    "AuditResult",
    "CHAPTER_MD_TEMPLATE",
    "CHALLENGES_MD_TEMPLATE",
    "CodeRefResolution",
    "HASH_ALGO",
    "JINJA_ENV",
    "RENDER_SCHEMA_VERSION",
    "RENDER_TEMPLATE_VERSION",
    "RenderResult",
    "RenderedArtifact",
    "SENTINEL_RE",
    "VAULT_HASH_LEN",
    "artifact_key",
    "build_section_context",
    "compute_audit",
    "dedupe_and_align_sections",
    "hash_block",
    "latest_blob_key",
    "load_render_payload",
    "merge_vault_entries",
    "mgsr_latest_key",
    "planner_latest_key",
    "render_audit_write_run",
    "render_chapter_md",
    "sawc_latest_key",
    "sha256_bytes",
    "source_key_to_vault_key",
    "versioned_blob_key",
]
