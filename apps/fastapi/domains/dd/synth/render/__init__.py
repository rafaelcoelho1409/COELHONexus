"""render — Materialize + audit + persist library (subpackage)."""

from .constants import (
    CHAPTER_MD_TEMPLATE,
    CHALLENGES_MD_TEMPLATE,
    RENDER_SCHEMA_VERSION,
    RENDER_TEMPLATE_VERSION,
    _HASH_ALGO,
    _JINJA_ENV,
    _SENTINEL_RE,
    _VAULT_HASH_LEN,
)
from .service import (
    _basename,
    _hash_block,
    build_section_context,
    compute_audit,
    merge_vault_entries,
    render_challenges_md,
    render_chapter_md,
    render_flashcards_json,
    sha256_bytes,
    source_key_to_vault_key,
)
from .types import (
    ArtifactName,
    AuditResult,
    CodeRefResolution,
    RenderResult,
    RenderedArtifact,
)

__all__ = [
    # constants
    "CHAPTER_MD_TEMPLATE",
    "CHALLENGES_MD_TEMPLATE",
    "RENDER_SCHEMA_VERSION",
    "RENDER_TEMPLATE_VERSION",
    "_HASH_ALGO",
    "_JINJA_ENV",
    "_SENTINEL_RE",
    "_VAULT_HASH_LEN",
    # types
    "ArtifactName",
    "AuditResult",
    "CodeRefResolution",
    "RenderResult",
    "RenderedArtifact",
    # service
    "_basename",
    "_hash_block",
    "build_section_context",
    "compute_audit",
    "merge_vault_entries",
    "render_challenges_md",
    "render_chapter_md",
    "render_flashcards_json",
    "sha256_bytes",
    "source_key_to_vault_key",
]
