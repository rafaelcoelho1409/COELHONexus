"""Versioned object keys for Docs Distiller's ``coelhonexus`` bucket."""
from __future__ import annotations
from . import params


def framework_prefix(framework_slug: str) -> str:
    return f"dd/{framework_slug.strip().strip('/')}/"


def library_prefix() -> str:
    return "dd/"


def benchmark_report_prefix(round_id: str) -> str:
    return f"dd-benchmarks/{round_id.strip().strip('/')}/"


def benchmark_report_key(round_id: str, run_id: str) -> str:
    return f"{benchmark_report_prefix(round_id)}{run_id}.json"


def ingestion_prefix(framework_slug: str) -> str:
    return f"{framework_prefix(framework_slug)}ingestion/"


def manifest_key(framework_slug: str) -> str:
    return f"{ingestion_prefix(framework_slug)}manifest.json"


def page_key(framework_slug: str, idx: int, slug: str, run_id: str = "") -> str:
    """Mutable working key retained for legacy callers; published pages are immutable agentic run objects."""
    safe_slug = (slug or "page").strip().strip("/")[:80]
    return f"{ingestion_prefix(framework_slug)}pages/{idx:04d}-{safe_slug}.md"


def stage_prefix(framework_slug: str, run_id: str) -> str:
    return f"{ingestion_prefix(framework_slug)}staging/{run_id}/"


def stage_page_key(framework_slug: str, run_id: str, idx: int, slug: str) -> str:
    safe_slug = (slug or "page").strip().strip("/")[:80]
    return f"{stage_prefix(framework_slug, run_id)}pages/{idx:04d}-{safe_slug}.md"


def raw_prefix(framework_slug: str, run_id: str | None = None) -> str:
    base = f"{ingestion_prefix(framework_slug)}raw/"
    return f"{base}{run_id}/" if run_id else base


def raw_tier_prefix(framework_slug: str, run_id: str, tier: str) -> str:
    safe_tier = (tier or "unknown").strip().strip("/")[:32]
    return f"{raw_prefix(framework_slug, run_id)}{safe_tier}/"


def raw_page_key(framework_slug: str, idx: int, slug: str, run_id: str = "", tier: str = "") -> str:
    safe_slug = (slug or "page").strip().strip("/")[:80]
    prefix = raw_tier_prefix(framework_slug, run_id, tier) if run_id and tier else raw_prefix(framework_slug, run_id or None)
    return f"{prefix}pages/{idx:04d}-{safe_slug}.md"


def agentic_input_key(framework_slug: str, run_id: str, idx: int, slug: str) -> str:
    safe_slug = (slug or "page").strip().strip("/")[:80]
    return f"{agentic_prefix(framework_slug, run_id)}inputs/pages/{idx:04d}-{safe_slug}.md"


def raw_capture_key(framework_slug: str, run_id: str | None = None, tier: str = "") -> str:
    if run_id and tier:
        return f"{raw_tier_prefix(framework_slug, run_id, tier)}capture.json"
    if run_id:
        return f"{raw_prefix(framework_slug, run_id)}capture.json"
    return f"{raw_prefix(framework_slug)}capture-latest.json"


def raw_file_key(framework_slug: str, name: str, run_id: str = "", tier: str = "") -> str:
    safe_name = (name or "file").strip().strip("/").replace("..", "")[:120]
    prefix = raw_tier_prefix(framework_slug, run_id, tier) if run_id and tier else raw_prefix(framework_slug, run_id or None)
    return f"{prefix}files/{safe_name}"


def agentic_prefix(framework_slug: str, run_id: str) -> str:
    return f"{ingestion_prefix(framework_slug)}agentic/{run_id}/"


def agentic_page_key(framework_slug: str, run_id: str, idx: int, slug: str) -> str:
    safe_slug = (slug or "page").strip().strip("/")[:80]
    return f"{agentic_prefix(framework_slug, run_id)}pages/{idx:04d}-{safe_slug}.md"


def agentic_assessment_key(framework_slug: str, run_id: str, idx: int) -> str:
    return f"{agentic_prefix(framework_slug, run_id)}assessments/{idx:04d}.json"


def agentic_audit_key(framework_slug: str, run_id: str, idx: int) -> str:
    return f"{agentic_prefix(framework_slug, run_id)}audit/{idx:04d}.json"


def agentic_manifest_key(framework_slug: str, run_id: str) -> str:
    return f"{agentic_prefix(framework_slug, run_id)}manifest.json"


def vault_prefix(framework_slug: str, run_id: str = "") -> str:
    if run_id:
        return f"{agentic_prefix(framework_slug, run_id)}vault/"
    return f"{ingestion_prefix(framework_slug)}vault/"


def vault_manifest_key(framework_slug: str, idx: int, slug: str, run_id: str = "") -> str:
    safe_slug = (slug or "page").strip().strip("/")[:80]
    return f"{vault_prefix(framework_slug, run_id)}pages/{idx:04d}-{safe_slug}.vault.json"


def vault_sentinelized_key(framework_slug: str, idx: int, slug: str, run_id: str = "") -> str:
    safe_slug = (slug or "page").strip().strip("/")[:80]
    return f"{vault_prefix(framework_slug, run_id)}pages/{idx:04d}-{safe_slug}.sentinelized.md"


def run_id_from_page_key(page_key: str) -> str:
    """Extract the immutable agentic run segment from a published page key."""
    parts = page_key.strip("/").split("/")
    for index in range(len(parts) - 2):
        if parts[index:index + 2] == ["ingestion", "agentic"]:
            return parts[index + 2]
    return ""


def live_manifest_key(run_id: str) -> str:
    return f"coelhonexus:dd:runs:{run_id}:manifest"


def artifact_key(framework_slug: str, name: str) -> str:
    safe_name = (name or "").strip().strip("/").replace("..", "")[:120]
    return f"{ingestion_prefix(framework_slug)}artifacts/{safe_name}"


def snapshot_prefix(framework_slug: str, ts: str) -> str:
    return f"{ingestion_prefix(framework_slug)}{params.SNAPSHOTS_SUBDIR}{ts}/"


def framework_parts(framework_slug: str) -> tuple[tuple[str, str], ...]:
    return (("ingestion", ingestion_prefix(framework_slug)),)
