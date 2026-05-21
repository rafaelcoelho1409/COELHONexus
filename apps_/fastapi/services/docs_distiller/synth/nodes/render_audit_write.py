"""render_audit_write — Materialize chapter markdown + audit + persist.

Step 9 of the synth pipeline (final node per
`docs/SYNTH-ARCHITECTURE-SOTA-2026-05-18.md` + the render_audit_write
deep research report). The ONLY synth node with ZERO LLM calls.

WHAT IT DOES (per chapter):

  1. Loads upstream blobs:
       - sawc-latest.json    (ChapterDraft — sections + paragraphs +
                                 code_refs + citations + challenges +
                                 flashcards)
       - mgsr-latest.json    (halt decision)
       - planner plan-latest (chapter.sources list)
  2. If `mgsr.decision.halt == false` → abort (v1 doesn't loop; mgsr
     should have halted). State patch with status=failed.
  3. Loads per-source vault manifests from
     `synth-vault/{slug}/pages/{idx}-{slug}.vault.json` for each
     source_key in the chapter's plan. Best-effort: missing vaults
     (empty source pages) are silently treated as empty.
  4. Merges all per-source vaults into a unified `{hash: fence_text}`
     map. Logs collisions (shouldn't happen — vault.py salts).
  5. Pre-processes each section into Jinja context:
       - Materializes code_refs via vault lookup
       - Re-hashes materialized text → byte_drift detection
       - Derives source_basename from citation.source_key
  6. Renders 3 artifacts via Jinja2 inline templates:
       - README.md   (full chapter markdown)
       - challenges.md (numbered active-recall questions)
       - flashcards.json (Q/A pairs as JSON array)
  7. Round-trip audit:
       - n_missing  — code refs not in any vault
       - n_byte_drift — re-hash mismatch
       - sentinels_in_output — `<code-ref hash=.../>` left in markdown
       - audit_passed = all empty + no sentinels left
  8. Writes 4 MinIO blobs:
       - synth/{slug}/{chapter_id}/README.md
       - synth/{slug}/{chapter_id}/challenges.md
       - synth/{slug}/{chapter_id}/flashcards.json
       - synth/{slug}/{chapter_id}/render-latest.json  (RenderResult)
     Plus the versioned manifest copy:
       - synth/{slug}/{chapter_id}/render/{manifest_hash}.json
  9. Returns state patch with `chapter_path` (the README.md key) +
     `chapter_stats`.

CACHING — content-addressed:

  versioned: synth/{slug}/{chapter_id}/render/{manifest_hash}.json
  latest:    synth/{slug}/{chapter_id}/render-latest.json

  Manifest hash includes:
    sawc_manifest_hash
    mgsr_manifest_hash
    template_version
    schema_version

  Cache hit returns immediately — the 3 content artifacts stay where
  they are (already written; we verify their existence + sizes).

FAIL-SOFT BEHAVIOR:

  - Vault file missing for one source: log warning, treat as empty.
    Audit will surface unresolved code_refs as `missing` if any.
  - Vault file malformed JSON: log warning, treat as empty for that
    source only.
  - Audit detects byte_drift or missing refs: persist with
    `audit_passed=false`, return state with `status=audit_failed`.
    Operator sees the failure; v2 will trigger structured retry.
  - mgsr says halt=false: abort cleanly. v1 doesn't loop yet; this
    case won't occur with the current trivial-pass behavior.

ZERO LLM CALLS

  Pure transform + cryptographic verification. Wall: ~50-500ms per
  chapter, dominated by MinIO reads of per-source vaults. $0 forever.
"""
from __future__ import annotations

import json
import logging
import time
from hashlib import sha256
from typing import Optional

from services.docs_distiller.ingestion.storage_minio import get_storage

from ..observability.spans import traced
from ..progress import emit_progress
from ..render import (
    RENDER_SCHEMA_VERSION,
    RENDER_TEMPLATE_VERSION,
    AuditResult,
    CodeRefResolution,
    RenderResult,
    RenderedArtifact,
    build_section_context,
    compute_audit,
    merge_vault_entries,
    render_challenges_md,
    render_chapter_md,
    render_flashcards_json,
    sha256_bytes,
    source_key_to_vault_key,
)
from ..state import SynthState


logger = logging.getLogger(__name__)


# =============================================================================
# Constants
# =============================================================================
_BLOB_PREFIX = "synth"


# =============================================================================
# Blob keys
# =============================================================================
def _versioned_blob_key(slug: str, chapter_id: str, manifest_hash: str) -> str:
    return f"{_BLOB_PREFIX}/{slug}/{chapter_id}/render/{manifest_hash}.json"


def _latest_blob_key(slug: str, chapter_id: str) -> str:
    return f"{_BLOB_PREFIX}/{slug}/{chapter_id}/render-latest.json"


def _artifact_key(slug: str, chapter_id: str, artifact_name: str) -> str:
    return f"{_BLOB_PREFIX}/{slug}/{chapter_id}/{artifact_name}"


def _sawc_latest_key(slug: str, chapter_id: str) -> str:
    return f"{_BLOB_PREFIX}/{slug}/{chapter_id}/sawc-latest.json"


def _mgsr_latest_key(slug: str, chapter_id: str) -> str:
    return f"{_BLOB_PREFIX}/{slug}/{chapter_id}/mgsr-latest.json"


def _planner_latest_key(slug: str) -> str:
    return f"planner/{slug}/plan-latest.json"


# =============================================================================
# Manifest hash
# =============================================================================
def _compute_manifest_hash(
    *,
    sawc_manifest_hash: str,
    mgsr_manifest_hash: str,
) -> str:
    payload = (
        f"sawc={sawc_manifest_hash}|"
        f"mgsr={mgsr_manifest_hash}|"
        f"template={RENDER_TEMPLATE_VERSION}|"
        f"schema={RENDER_SCHEMA_VERSION}"
    )
    return sha256(payload.encode("utf-8")).hexdigest()[:16]


# =============================================================================
# Vault loader
# =============================================================================
async def _load_per_source_vaults(
    minio,
    slug: str,
    source_keys: list[str],
) -> tuple[dict[str, str], int, int]:
    """Load and parse per-source vault manifests. Returns
    (merged_vault, n_loaded, n_skipped_missing).

    `merged_vault` maps `hash → fence_text` across all sources. Empty
    sources contribute nothing; missing vault files contribute nothing
    (but increment n_skipped_missing).
    """
    manifests: list[dict] = []
    n_skipped = 0
    for source_key in source_keys:
        vault_key = source_key_to_vault_key(source_key, slug)
        if not await minio.exists(vault_key):
            n_skipped += 1
            continue
        try:
            text = await minio.read_text(vault_key)
            manifests.append(json.loads(text))
        except Exception as e:
            n_skipped += 1
            logger.warning(
                f"[render_audit_write] vault {vault_key!r} unreadable: "
                f"{type(e).__name__}: {e} — skipping"
            )
    merged = merge_vault_entries(manifests)
    return merged, len(manifests), n_skipped


# =============================================================================
# Cache fast-path verifier
# =============================================================================
async def _verify_cache_hit_artifacts(
    minio,
    slug: str,
    chapter_id: str,
    artifacts: list[dict],
) -> bool:
    """Cache hit is only valid if all 3 content artifacts ALSO exist.
    Defense against partial-write states (e.g. crash between writes)."""
    for art in artifacts:
        key = art.get("minio_key") or ""
        if not key or not await minio.exists(key):
            return False
    return True


# =============================================================================
# The node
# =============================================================================
@traced("render_audit_write")
async def render_audit_write(state: SynthState) -> dict:
    """Render + audit + persist for one chapter. Zero LLM calls."""
    slug = state.get("framework_slug")
    chapter_id = state.get("chapter_id")
    thread_id = state.get("thread_id") or ""

    if not slug or not chapter_id:
        return {
            "chapter_path":  "",
            "chapter_stats": {
                "skipped": "no_slug_or_chapter_id", "wall_ms": 0,
            },
            "status": "failed",
            "error":  "framework_slug or chapter_id missing from SynthState",
        }

    t0 = time.monotonic()
    minio = get_storage()

    # ── Load sawc + mgsr ───────────────────────────────────────────────
    sawc_key = _sawc_latest_key(slug, chapter_id)
    mgsr_key = _mgsr_latest_key(slug, chapter_id)

    if not await minio.exists(sawc_key):
        return {
            "chapter_path":  "",
            "chapter_stats": {
                "skipped":  "sawc_not_found",
                "sawc_key": sawc_key,
                "wall_ms":  int((time.monotonic() - t0) * 1000),
            },
            "status": "failed",
            "error":  f"sawc {sawc_key!r} not in MinIO — run sawc_write first",
        }
    if not await minio.exists(mgsr_key):
        return {
            "chapter_path":  "",
            "chapter_stats": {
                "skipped":  "mgsr_not_found",
                "mgsr_key": mgsr_key,
                "wall_ms":  int((time.monotonic() - t0) * 1000),
            },
            "status": "failed",
            "error":  f"mgsr {mgsr_key!r} not in MinIO — run mgsr_replan first",
        }

    try:
        sawc_text = await minio.read_text(sawc_key)
        sawc = json.loads(sawc_text)
        mgsr_text = await minio.read_text(mgsr_key)
        mgsr = json.loads(mgsr_text)
    except Exception as e:
        return {
            "chapter_path":  "",
            "chapter_stats": {
                "skipped": "inputs_unreadable",
                "wall_ms": int((time.monotonic() - t0) * 1000),
            },
            "status": "failed",
            "error":  f"sawc/mgsr unreadable: {type(e).__name__}: {e}",
        }

    # ── Confirm mgsr says halt=true ────────────────────────────────────
    mgsr_decision = (mgsr or {}).get("decision") or {}
    if not mgsr_decision.get("halt", True):
        # v1 doesn't loop yet — abort cleanly. Should never happen in
        # practice with the trivial-pass behavior.
        return {
            "chapter_path":  "",
            "chapter_stats": {
                "skipped": "mgsr_not_halted",
                "halt_reason": mgsr_decision.get("halt_reason"),
                "wall_ms": int((time.monotonic() - t0) * 1000),
            },
            "status": "failed",
            "error":  (
                "mgsr_replan says halt=false (v2 loop required); v1 "
                "doesn't loop back to sawc yet"
            ),
        }

    chapter_title = sawc.get("chapter_title") or chapter_id
    sections = sawc.get("sections") or []
    challenges = sawc.get("challenges") or []
    flashcards = sawc.get("flashcards") or []
    sawc_manifest_hash = sawc.get("sawc_manifest_hash") or ""
    mgsr_manifest_hash = mgsr.get("mgsr_manifest_hash") or ""

    await emit_progress(
        thread_id, "render_audit_write", "start",
        chapter_id=chapter_id,
        chapter_title=chapter_title,
        n_sections=len(sections),
        n_challenges=len(challenges),
        n_flashcards=len(flashcards),
        mgsr_halt=mgsr_decision.get("halt", True),
        mgsr_halt_reason=mgsr_decision.get("halt_reason", "?"),
    )

    # ── Cache fast-path ────────────────────────────────────────────────
    manifest_hash = _compute_manifest_hash(
        sawc_manifest_hash=sawc_manifest_hash,
        mgsr_manifest_hash=mgsr_manifest_hash,
    )
    versioned_key = _versioned_blob_key(slug, chapter_id, manifest_hash)
    latest_key    = _latest_blob_key(slug, chapter_id)

    if await minio.exists(versioned_key) and await minio.exists(latest_key):
        try:
            cached_text = await minio.read_text(versioned_key)
            cached = json.loads(cached_text)
            arts = cached.get("artifacts") or []
            if await _verify_cache_hit_artifacts(minio, slug, chapter_id, arts):
                audit = cached.get("audit") or {}
                elapsed = int((time.monotonic() - t0) * 1000)
                readme_key = _artifact_key(slug, chapter_id, "README.md")
                stats = {
                    "audit_passed":         audit.get("audit_passed", False),
                    "n_artifacts":          len(arts),
                    "n_code_refs":          audit.get("n_code_refs_referenced", 0),
                    "n_resolved":           audit.get("n_resolved", 0),
                    "n_missing":            len(audit.get("n_missing") or []),
                    "n_byte_drift":         len(audit.get("n_byte_drift") or []),
                    "sentinels_in_output":  audit.get("sentinels_in_output", 0),
                    "rendered_chars":       cached.get("rendered_chars", 0),
                    "wall_ms":              elapsed,
                    "store_path":           latest_key,
                    "versioned_path":       versioned_key,
                    "readme_path":          readme_key,
                    "manifest_hash":        manifest_hash,
                    "cache_hit":            True,
                    "template_version":     cached.get("template_version"),
                }
                await emit_progress(
                    thread_id, "render_audit_write", "done",
                    audit_passed=stats["audit_passed"],
                    n_artifacts=stats["n_artifacts"],
                    n_code_refs=stats["n_code_refs"],
                    n_resolved=stats["n_resolved"],
                    n_missing=stats["n_missing"],
                    n_byte_drift=stats["n_byte_drift"],
                    sentinels_in_output=stats["sentinels_in_output"],
                    rendered_chars=stats["rendered_chars"],
                    wall_ms=elapsed, cache_hit=True,
                )
                logger.info(
                    f"[render_audit_write] {slug}/{chapter_id}: CACHE HIT — "
                    f"audit_passed={stats['audit_passed']}, "
                    f"refs={stats['n_resolved']}/{stats['n_code_refs']}, "
                    f"{elapsed} ms"
                )
                return {"chapter_path": readme_key, "chapter_stats": stats}
            else:
                logger.warning(
                    f"[render_audit_write] cached render_result exists but "
                    f"artifacts missing — re-rendering"
                )
        except Exception as e:
            logger.warning(
                f"[render_audit_write] {slug}/{chapter_id}: cached blob "
                f"{versioned_key!r} unreadable ({type(e).__name__}: {e}); "
                f"recomputing"
            )

    # ── Load planner plan → chapter sources ────────────────────────────
    plan_key = _planner_latest_key(slug)
    source_keys: list[str] = []
    if await minio.exists(plan_key):
        try:
            plan_text = await minio.read_text(plan_key)
            plan = json.loads(plan_text)
            for ch in (plan.get("chapters") or []):
                if (ch or {}).get("id") == chapter_id:
                    source_keys = sorted(ch.get("sources") or [])
                    break
        except Exception as e:
            logger.warning(
                f"[render_audit_write] plan {plan_key!r} unreadable: "
                f"{type(e).__name__}: {e}"
            )

    # ── Load + merge per-source vaults ─────────────────────────────────
    vault, n_loaded, n_skipped = await _load_per_source_vaults(
        minio, slug, source_keys,
    )
    await emit_progress(
        thread_id, "render_audit_write", "inputs_loaded",
        n_sources=len(source_keys),
        n_vault_files_loaded=n_loaded,
        n_vault_files_skipped=n_skipped,
        n_vault_entries=len(vault),
    )

    # ── Render: pre-process sections + render 3 artifacts ──────────────
    resolution_log: list[CodeRefResolution] = []
    sections_ctx = [
        build_section_context(
            s, vault=vault, resolution_log=resolution_log,
        )
        for s in sections
    ]
    n_paragraphs_total = sum(len(s.get("paragraphs") or []) for s in sections)
    n_citations_total = sum(len(s.get("citations") or []) for s in sections)

    chapter_md     = render_chapter_md(chapter_title, sections_ctx)
    challenges_md  = render_challenges_md(chapter_title, challenges)
    flashcards_str = render_flashcards_json(flashcards)

    # ── Audit (after rendering, so sentinels_in_output is measurable) ─
    audit = compute_audit(
        resolution_log=resolution_log,
        vault=vault,
        rendered_chapter_md=chapter_md,
    )

    await emit_progress(
        thread_id, "render_audit_write", "rendered",
        chapter_chars=len(chapter_md),
        n_sections_rendered=len(sections_ctx),
        n_code_refs_resolved=audit.n_resolved,
        n_code_refs_missing=len(audit.n_missing),
        n_code_refs_drift=len(audit.n_byte_drift),
        sentinels_in_output=audit.sentinels_in_output,
        audit_passed=audit.audit_passed,
    )

    # ── Write 3 content artifacts to MinIO ─────────────────────────────
    readme_key      = _artifact_key(slug, chapter_id, "README.md")
    challenges_key  = _artifact_key(slug, chapter_id, "challenges.md")
    flashcards_key  = _artifact_key(slug, chapter_id, "flashcards.json")

    await minio.write(readme_key, chapter_md,
                      content_type="text/markdown; charset=utf-8")
    await minio.write(challenges_key, challenges_md,
                      content_type="text/markdown; charset=utf-8")
    await minio.write(flashcards_key, flashcards_str,
                      content_type="application/json")

    artifacts = [
        RenderedArtifact(
            name="README.md",
            minio_key=readme_key,
            size_bytes=len(chapter_md.encode("utf-8")),
            sha256=sha256_bytes(chapter_md),
        ),
        RenderedArtifact(
            name="challenges.md",
            minio_key=challenges_key,
            size_bytes=len(challenges_md.encode("utf-8")),
            sha256=sha256_bytes(challenges_md),
        ),
        RenderedArtifact(
            name="flashcards.json",
            minio_key=flashcards_key,
            size_bytes=len(flashcards_str.encode("utf-8")),
            sha256=sha256_bytes(flashcards_str),
        ),
    ]

    await emit_progress(
        thread_id, "render_audit_write", "artifacts_written",
        n_artifacts=len(artifacts),
        total_bytes=sum(a.size_bytes for a in artifacts),
        artifact_names=[a.name for a in artifacts],
    )

    # ── Persist RenderResult metadata blob ─────────────────────────────
    elapsed = int((time.monotonic() - t0) * 1000)
    result = RenderResult(
        chapter_id=chapter_id,
        chapter_title=chapter_title,
        framework_slug=slug,
        artifacts=artifacts,
        audit=audit,
        rendered_chars=len(chapter_md),
        n_sections=len(sections),
        n_paragraphs_total=n_paragraphs_total,
        n_citations_total=n_citations_total,
        sawc_manifest_hash=sawc_manifest_hash,
        mgsr_manifest_hash=mgsr_manifest_hash,
        render_manifest_hash=manifest_hash,
        wall_ms=elapsed,
        thread_id=thread_id,
    )
    payload = result.model_dump()
    blob_bytes = json.dumps(payload, indent=2, ensure_ascii=False)
    await minio.write(
        versioned_key, blob_bytes, content_type="application/json",
    )
    await minio.write(
        latest_key, blob_bytes, content_type="application/json",
    )

    stats = {
        "audit_passed":         audit.audit_passed,
        "n_artifacts":          len(artifacts),
        "n_code_refs":          audit.n_code_refs_referenced,
        "n_resolved":           audit.n_resolved,
        "n_missing":            len(audit.n_missing),
        "n_byte_drift":         len(audit.n_byte_drift),
        "n_orphan_unused":      len(audit.n_orphan_unused),
        "sentinels_in_output":  audit.sentinels_in_output,
        "rendered_chars":       len(chapter_md),
        "n_sections":           len(sections),
        "n_paragraphs_total":   n_paragraphs_total,
        "n_citations_total":    n_citations_total,
        "n_vault_files_loaded": n_loaded,
        "n_vault_files_skipped": n_skipped,
        "n_vault_entries":      len(vault),
        "wall_ms":              elapsed,
        "store_path":           latest_key,
        "versioned_path":       versioned_key,
        "readme_path":          readme_key,
        "manifest_hash":        manifest_hash,
        "cache_hit":            False,
        "template_version":     RENDER_TEMPLATE_VERSION,
    }
    await emit_progress(
        thread_id, "render_audit_write", "done",
        audit_passed=audit.audit_passed,
        n_artifacts=len(artifacts),
        n_code_refs=audit.n_code_refs_referenced,
        n_resolved=audit.n_resolved,
        n_missing=len(audit.n_missing),
        n_byte_drift=len(audit.n_byte_drift),
        sentinels_in_output=audit.sentinels_in_output,
        rendered_chars=len(chapter_md),
        wall_ms=elapsed,
    )
    logger.info(
        f"[render_audit_write] {slug}/{chapter_id}: "
        f"audit_passed={audit.audit_passed}, "
        f"{audit.n_resolved}/{audit.n_code_refs_referenced} code_refs "
        f"resolved, {len(audit.n_missing)} missing, "
        f"{len(audit.n_byte_drift)} drift, "
        f"{audit.sentinels_in_output} sentinels left; "
        f"3 artifacts written ({sum(a.size_bytes for a in artifacts)} bytes); "
        f"{elapsed} ms"
    )
    # If audit failed, surface via state.status so the operator sees it
    state_status = "audit_failed" if not audit.audit_passed else None
    state_patch = {"chapter_path": readme_key, "chapter_stats": stats}
    if state_status:
        state_patch["status"] = state_status
        state_patch["error"] = (
            f"render audit failed: missing={len(audit.n_missing)} "
            f"drift={len(audit.n_byte_drift)} "
            f"unresolved_sentinels={audit.sentinels_in_output}"
        )
    return state_patch


# =============================================================================
# Convenience loader for downstream tooling
# =============================================================================
def load_render_payload(text: str) -> dict:
    """Parse the persisted render-latest.json blob. Operator-facing
    downstream tools (Study UI, export) consume this to discover the
    artifact keys + audit verdict."""
    return json.loads(text)
