"""Render + audit + persist orchestrator for one chapter. Zero LLM calls.

Pure deterministic transform + cryptographic vault round-trip audit.
The byte-exact guarantee (vs arXiv 2601.03640's literal-payload failure
mode) holds because the LLM NEVER copies code — vault sentinels hide
fenced blocks until materialization, which happens here.
"""
from __future__ import annotations

import json
import logging
import time

from ....ingestion.storage import get_storage
from ...runtime.progress import emit_progress
from ...state import SynthState

from .domain import (
    build_section_context,
    compute_audit,
    compute_manifest_hash,
    merge_vault_entries,
    render_challenges_md,
    render_chapter_md,
    render_flashcards_json,
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
from .schemas import CodeRefResolution, RenderedArtifact, RenderResult
from .versions import RENDER_TEMPLATE_VERSION


logger = logging.getLogger(__name__)


async def _load_per_source_vaults(
    minio,
    slug: str,
    source_keys: list[str],
) -> tuple[dict[str, str], int, int]:
    """Load + merge per-source vault manifests.
    Returns (merged_vault, n_loaded, n_skipped_missing)."""
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


async def _verify_cache_hit_artifacts(
    minio,
    slug: str,
    chapter_id: str,
    artifacts: list[dict],
) -> bool:
    """Cache hit only valid when all 3 content artifacts also exist —
    defense against partial-write crash state."""
    for art in artifacts:
        key = art.get("minio_key") or ""
        if not key or not await minio.exists(key):
            return False
    return True


async def render_audit_write_run(state: SynthState) -> dict:
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

    sawc_key = sawc_latest_key(slug, chapter_id)
    mgsr_key = mgsr_latest_key(slug, chapter_id)

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

    # v1 doesn't loop; abort cleanly if mgsr didn't halt (shouldn't happen).
    mgsr_decision = (mgsr or {}).get("decision") or {}
    if not mgsr_decision.get("halt", True):
        return {
            "chapter_path":  "",
            "chapter_stats": {
                "skipped":     "mgsr_not_halted",
                "halt_reason": mgsr_decision.get("halt_reason"),
                "wall_ms":     int((time.monotonic() - t0) * 1000),
            },
            "status": "failed",
            "error":  (
                "mgsr_replan says halt = false (v2 loop required); v1 "
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
        chapter_id = chapter_id,
        chapter_title = chapter_title,
        n_sections = len(sections),
        n_challenges = len(challenges),
        n_flashcards = len(flashcards),
        mgsr_halt = mgsr_decision.get("halt", True),
        mgsr_halt_reason = mgsr_decision.get("halt_reason", "?"),
    )

    manifest_hash = compute_manifest_hash(
        sawc_manifest_hash = sawc_manifest_hash,
        mgsr_manifest_hash = mgsr_manifest_hash,
    )
    versioned_key = versioned_blob_key(slug, chapter_id, manifest_hash)
    latest_key    = latest_blob_key(slug, chapter_id)

    if await minio.exists(versioned_key) and await minio.exists(latest_key):
        try:
            cached_text = await minio.read_text(versioned_key)
            cached = json.loads(cached_text)
            arts = cached.get("artifacts") or []
            if await _verify_cache_hit_artifacts(minio, slug, chapter_id, arts):
                audit = cached.get("audit") or {}
                elapsed = int((time.monotonic() - t0) * 1000)
                readme_key = artifact_key(slug, chapter_id, "README.md")
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
                    audit_passed = stats["audit_passed"],
                    n_artifacts = stats["n_artifacts"],
                    n_code_refs = stats["n_code_refs"],
                    n_resolved = stats["n_resolved"],
                    n_missing = stats["n_missing"],
                    n_byte_drift = stats["n_byte_drift"],
                    sentinels_in_output = stats["sentinels_in_output"],
                    rendered_chars = stats["rendered_chars"],
                    wall_ms = elapsed, cache_hit = True,
                )
                logger.info(
                    f"[render_audit_write] {slug}/{chapter_id}: CACHE HIT — "
                    f"audit_passed = {stats['audit_passed']}, "
                    f"refs = {stats['n_resolved']}/{stats['n_code_refs']}, "
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

    plan_key = planner_latest_key(slug)
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

    vault, n_loaded, n_skipped = await _load_per_source_vaults(
        minio, slug, source_keys,
    )
    await emit_progress(
        thread_id, "render_audit_write", "inputs_loaded",
        n_sources = len(source_keys),
        n_vault_files_loaded = n_loaded,
        n_vault_files_skipped = n_skipped,
        n_vault_entries = len(vault),
    )

    resolution_log: list[CodeRefResolution] = []
    sections_ctx = [
        build_section_context(
            s, vault = vault, resolution_log = resolution_log,
        )
        for s in sections
    ]
    # v2 cookbook (matches RenderResult schema): subtopics replaced
    # legacy paragraphs. RenderResult declares `n_subtopics_total`;
    # passing the legacy `n_paragraphs_total` name to it raises
    # `pydantic.ValidationError: n_subtopics_total field required`.
    n_subtopics_total = sum(len(s.get("subtopics") or []) for s in sections)
    n_citations_total = sum(len(s.get("citations") or []) for s in sections)

    chapter_md     = render_chapter_md(chapter_title, sections_ctx)
    challenges_md  = render_challenges_md(chapter_title, challenges)
    flashcards_str = render_flashcards_json(flashcards)

    # Audit AFTER rendering — sentinels_in_output is measured on the rendered MD.
    audit = compute_audit(
        resolution_log = resolution_log,
        vault = vault,
        rendered_chapter_md = chapter_md,
    )

    await emit_progress(
        thread_id, "render_audit_write", "rendered",
        chapter_chars = len(chapter_md),
        n_sections_rendered = len(sections_ctx),
        n_code_refs_resolved = audit.n_resolved,
        n_code_refs_missing = len(audit.n_missing),
        n_code_refs_drift = len(audit.n_byte_drift),
        sentinels_in_output = audit.sentinels_in_output,
        audit_passed = audit.audit_passed,
    )

    readme_key      = artifact_key(slug, chapter_id, "README.md")
    challenges_key  = artifact_key(slug, chapter_id, "challenges.md")
    flashcards_key  = artifact_key(slug, chapter_id, "flashcards.json")

    await minio.write(
        readme_key, chapter_md,
        content_type = "text/markdown; charset=utf-8",
    )
    await minio.write(
        challenges_key, challenges_md,
        content_type = "text/markdown; charset=utf-8",
    )
    await minio.write(
        flashcards_key, flashcards_str,
        content_type = "application/json",
    )

    artifacts = [
        RenderedArtifact(
            name = "README.md",
            minio_key = readme_key,
            size_bytes = len(chapter_md.encode("utf-8")),
            sha256 = sha256_bytes(chapter_md),
        ),
        RenderedArtifact(
            name = "challenges.md",
            minio_key = challenges_key,
            size_bytes = len(challenges_md.encode("utf-8")),
            sha256 = sha256_bytes(challenges_md),
        ),
        RenderedArtifact(
            name = "flashcards.json",
            minio_key = flashcards_key,
            size_bytes = len(flashcards_str.encode("utf-8")),
            sha256 = sha256_bytes(flashcards_str),
        ),
    ]

    await emit_progress(
        thread_id, "render_audit_write", "artifacts_written",
        n_artifacts = len(artifacts),
        total_bytes = sum(a.size_bytes for a in artifacts),
        artifact_names = [a.name for a in artifacts],
    )

    elapsed = int((time.monotonic() - t0) * 1000)
    result = RenderResult(
        chapter_id = chapter_id,
        chapter_title = chapter_title,
        framework_slug = slug,
        artifacts = artifacts,
        audit = audit,
        rendered_chars = len(chapter_md),
        n_sections = len(sections),
        n_subtopics_total = n_subtopics_total,
        n_citations_total = n_citations_total,
        sawc_manifest_hash = sawc_manifest_hash,
        mgsr_manifest_hash = mgsr_manifest_hash,
        render_manifest_hash = manifest_hash,
        wall_ms = elapsed,
        # Persist the per-chapter synth thread so the Study chapter strip
        # can re-open the chapter's LangGraph canvas after a page refresh.
        # The schema declares this field with a `""` default; without
        # passing it explicitly, the chapters API returns thread_id=None
        # and clicking a (done) chapter cell falls into the "no thread"
        # branch — graph nodes never repaint.
        thread_id = thread_id,
    )
    payload = result.model_dump()
    blob_bytes = json.dumps(payload, indent = 2, ensure_ascii = False)
    await minio.write(
        versioned_key, blob_bytes, content_type = "application/json",
    )
    await minio.write(
        latest_key, blob_bytes, content_type = "application/json",
    )

    stats = {
        "audit_passed":          audit.audit_passed,
        "n_artifacts":           len(artifacts),
        "n_code_refs":           audit.n_code_refs_referenced,
        "n_resolved":            audit.n_resolved,
        "n_missing":             len(audit.n_missing),
        "n_byte_drift":          len(audit.n_byte_drift),
        "n_orphan_unused":       len(audit.n_orphan_unused),
        "sentinels_in_output":   audit.sentinels_in_output,
        "rendered_chars":        len(chapter_md),
        "n_sections":            len(sections),
        "n_subtopics_total":    n_subtopics_total,
        "n_citations_total":     n_citations_total,
        "n_vault_files_loaded":  n_loaded,
        "n_vault_files_skipped": n_skipped,
        "n_vault_entries":       len(vault),
        "wall_ms":               elapsed,
        "store_path":            latest_key,
        "versioned_path":        versioned_key,
        "readme_path":           readme_key,
        "manifest_hash":         manifest_hash,
        "cache_hit":             False,
        "template_version":      RENDER_TEMPLATE_VERSION,
    }
    await emit_progress(
        thread_id, "render_audit_write", "done",
        audit_passed = audit.audit_passed,
        n_artifacts = len(artifacts),
        n_code_refs = audit.n_code_refs_referenced,
        n_resolved = audit.n_resolved,
        n_missing = len(audit.n_missing),
        n_byte_drift = len(audit.n_byte_drift),
        sentinels_in_output = audit.sentinels_in_output,
        rendered_chars = len(chapter_md),
        wall_ms = elapsed,
    )
    logger.info(
        f"[render_audit_write] {slug}/{chapter_id}: "
        f"audit_passed = {audit.audit_passed}, "
        f"{audit.n_resolved}/{audit.n_code_refs_referenced} code_refs "
        f"resolved, {len(audit.n_missing)} missing, "
        f"{len(audit.n_byte_drift)} drift, "
        f"{audit.sentinels_in_output} sentinels left; "
        f"3 artifacts written ({sum(a.size_bytes for a in artifacts)} bytes); "
        f"{elapsed} ms"
    )
    state_status = "audit_failed" if not audit.audit_passed else None
    state_patch = {"chapter_path": readme_key, "chapter_stats": stats}
    if state_status:
        state_patch["status"] = state_status
        state_patch["error"] = (
            f"render audit failed: missing = {len(audit.n_missing)} "
            f"drift = {len(audit.n_byte_drift)} "
            f"unresolved_sentinels = {audit.sentinels_in_output}"
        )
    return state_patch
