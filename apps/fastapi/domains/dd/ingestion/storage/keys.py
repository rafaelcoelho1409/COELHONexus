"""Key builders: ingestion/ (normalized), ingestion-raw/ (reversibility across normalizer bumps), synth-vault/ (wipeable without affecting ingestion)."""
from __future__ import annotations

from .params import SNAPSHOTS_SUBDIR


def framework_prefix(framework_slug: str) -> str:
    return f"ingestion/{framework_slug.strip().strip('/')}/"


def manifest_key(framework_slug: str) -> str:
    return f"{framework_prefix(framework_slug)}manifest.json"


def page_key(framework_slug: str, idx: int, slug: str) -> str:
    """Zero-padded ordinal makes alphabetical MinIO listing equal document order
    — every downstream consumer (inspect UI, synth) relies on this."""
    safe_slug = (slug or "page").strip().strip("/")[:80]
    return f"{framework_prefix(framework_slug)}pages/{idx:04d}-{safe_slug}.md"


def raw_prefix(framework_slug: str) -> str:
    return f"ingestion-raw/{framework_slug.strip().strip('/')}/"


def raw_page_key(framework_slug: str, idx: int, slug: str) -> str:
    safe_slug = (slug or "page").strip().strip("/")[:80]
    return f"{raw_prefix(framework_slug)}pages/{idx:04d}-{safe_slug}.md"


def vault_prefix(framework_slug: str) -> str:
    return f"synth-vault/{framework_slug.strip().strip('/')}/"


def vault_manifest_key(framework_slug: str, idx: int, slug: str) -> str:
    safe_slug = (slug or "page").strip().strip("/")[:80]
    return f"{vault_prefix(framework_slug)}pages/{idx:04d}-{safe_slug}.vault.json"


def vault_sentinelized_key(framework_slug: str, idx: int, slug: str) -> str:
    safe_slug = (slug or "page").strip().strip("/")[:80]
    return f"{vault_prefix(framework_slug)}pages/{idx:04d}-{safe_slug}.sentinelized.md"


def live_manifest_key(run_id: str) -> str:
    return f"dd:runs:{run_id}:manifest"


# Content-addressed (`{sha256[:16]}.{ext}`) → auto-dedup across pages + reingest.
def artifact_key(framework_slug: str, name: str) -> str:
    safe_name = (name or "").strip().strip("/").replace("..", "")[:120]
    return f"{framework_prefix(framework_slug)}artifacts/{safe_name}"


def snapshot_prefix(framework_slug: str, ts: str) -> str:
    return f"{framework_prefix(framework_slug)}{SNAPSHOTS_SUBDIR}{ts}/"
