"""Key-shape constants and pure key-building helpers.

All MinIO key paths live here so every module references a single source
of truth for the object layout.
"""
from __future__ import annotations


# =============================================================================
# Key shape (per-framework, NOT per-run)
# =============================================================================
def framework_prefix(framework_slug: str) -> str:
    return f"ingestion/{framework_slug.strip().strip('/')}/"


def manifest_key(framework_slug: str) -> str:
    return f"{framework_prefix(framework_slug)}manifest.json"


def page_key(framework_slug: str, idx: int, slug: str) -> str:
    """Zero-padded ordinal makes alphabetical MinIO listing equal document
    order, which is what every downstream consumer (inspect UI, synth)
    expects."""
    safe_slug = (slug or "page").strip().strip("/")[:80]
    return f"{framework_prefix(framework_slug)}pages/{idx:04d}-{safe_slug}.md"


# Raw-page key — preserves the un-normalized markdown so the
# `corpus_normalize` pass remains reversible and backfills are safe
# across normalizer version bumps. See SYNTH-ARCHITECTURE-SOTA doc
# §"Reversibility decision" — `ingestion/...` holds the normalized
# body (what every consumer reads); `ingestion-raw/...` holds the
# original. 90-day lifecycle policy on the raw prefix is acceptable
# once the normalizer stabilizes.
def raw_prefix(framework_slug: str) -> str:
    return f"ingestion-raw/{framework_slug.strip().strip('/')}/"


def raw_page_key(framework_slug: str, idx: int, slug: str) -> str:
    safe_slug = (slug or "page").strip().strip("/")[:80]
    return f"{raw_prefix(framework_slug)}pages/{idx:04d}-{safe_slug}.md"


# Vault keys — written alongside each ingested page (per
# docs/SYNTH-ARCHITECTURE-SOTA-2026-05-18.md step 5). The synth pipeline
# reads these to feed sentinelized text to the LLM + byte-exact restore
# code blocks at render time. The original `ingestion/{slug}/pages/...`
# files are UNTOUCHED — file viewers (Step 2 + Step 5 drawer) keep
# showing real, readable code. Vault blobs live under a separate
# `synth-vault/{slug}/...` prefix so a wipe-vault operation never
# touches user-visible ingestion outputs.
def vault_prefix(framework_slug: str) -> str:
    return f"synth-vault/{framework_slug.strip().strip('/')}/"


def vault_manifest_key(framework_slug: str, idx: int, slug: str) -> str:
    safe_slug = (slug or "page").strip().strip("/")[:80]
    return (
        f"{vault_prefix(framework_slug)}pages/"
        f"{idx:04d}-{safe_slug}.vault.json"
    )


def vault_sentinelized_key(framework_slug: str, idx: int, slug: str) -> str:
    safe_slug = (slug or "page").strip().strip("/")[:80]
    return (
        f"{vault_prefix(framework_slug)}pages/"
        f"{idx:04d}-{safe_slug}.sentinelized.md"
    )


def live_manifest_key(run_id: str) -> str:
    return f"dd:runs:{run_id}:manifest"


_TTL_S = 7200
