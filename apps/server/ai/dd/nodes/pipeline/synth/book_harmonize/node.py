"""book_harmonize: the study's cross-chapter coherence pass, run by the study after its chapters are rendered (it is not a node of a chapter's graph)."""
from __future__ import annotations
import domains
from . import domain, params, service
from ..... import graphs

import logging


logger = logging.getLogger(__name__)


async def harmonize_book(context: graphs.pipeline.synth.context.SynthContext, *, framework_slug: str, framework_name: str, chapters: list[dict]) -> dict:
    """Run the harmonize subgraph over `chapters` (`{chapter_id, title, prose}` each) and return its result: counts and, per chapter, whether and how it was patched."""
    context.scratch["book_harmonize"] = {
        "chapters": {chapter["chapter_id"]: chapter for chapter in chapters},
        "framework_name": framework_name,
    }
    result = await graphs.pipeline.synth.book_harmonize.graph.build().ainvoke(
        {"framework_slug": framework_slug, "framework_name": framework_name},
        {"max_concurrency": params.PER_CHAPTER_CONCURRENCY},
        context = context,
    )
    return result.get("result") or {}


async def run_pass(context: graphs.pipeline.synth.context.SynthContext, *, slug: str, study_thread_id: str, chapter_ids: list[str]) -> dict:
    """The study's pass over its chapters: loads each README, runs the harmonize subgraph, overwrites the validated patches. A content-addressed cache skips the
    work on identical inputs. Returns the telemetry dict (counts, or `skipped` and why)."""
    with domains.dd.pipeline.observability.service.workflow_span(
        "dd.synth.node.book_harmonize",
        workflow = "dd_synth", kind = "workflow_node", node = "book_harmonize", stage = "synth",
        attributes = {
            "synth.node": "book_harmonize",
            "synth.framework_slug": slug,
            "synth.thread_id": study_thread_id,
            "synth.chapter_count": len(chapter_ids),
        },
    ):
        with domains.settings.llm_counter.service.llm_context(ns = "dd", run_id = study_thread_id, node_id = "book_harmonize", stage = "synth"):
            return await _run_pass(context, slug = slug, study_thread_id = study_thread_id, chapter_ids = chapter_ids)


async def _run_pass(context: graphs.pipeline.synth.context.SynthContext, *, slug: str, study_thread_id: str, chapter_ids: list[str]) -> dict:
    blobs = context.blobs
    progress = domains.dd.pipeline.runtime.progress.service
    entry = domains.dd.catalog.service.index_by_slug().get(slug, {})
    framework_name = entry.get("name") or entry.get("slug") or slug

    chapters, missing, audit_failed = await service.load_chapters(blobs, slug, chapter_ids)
    if len(chapters) < params.MIN_CHAPTERS:
        await progress.emit_progress(study_thread_id, "study", "book_harmonize_skipped", reason = "fewer_than_2_rendered_chapters", n_rendered = len(chapters))
        return {
            "skipped": "fewer_than_2_rendered_chapters",
            "n_rendered_chapters": len(chapters),
            "missing_chapters": missing,
            "audit_failed_chapters": audit_failed,
        }

    manifest_hash = domain.compute_harmonize_manifest_hash(chapters)
    cached = await service.read_cached(blobs, slug, manifest_hash)
    if cached is not None:
        await _announce_done(progress, study_thread_id, cached, n_overwritten = cached.get("n_chapters_overwritten", 0), cache_hit = True)
        logger.info(f"[book_harmonize] {slug}: CACHE HIT — manifest_hash = {manifest_hash}")
        return cached

    await progress.emit_progress(study_thread_id, "study", "book_harmonize_start", n_chapters = len(chapters))
    try:
        result = await harmonize_book(context, framework_slug = slug, framework_name = framework_name, chapters = chapters)
    except Exception as error:
        logger.warning(f"[book_harmonize] {slug}: harmonize_book crashed ({type(error).__name__}: {error})")
        return {"skipped": f"crash: {type(error).__name__}", "error": str(error)[:240]}

    n_overwritten = await service.overwrite_chapters(blobs, slug, result)
    payload = {**result, "manifest_hash": manifest_hash, "n_chapters_overwritten": n_overwritten, "cache_hit": False}
    await service.store_result(blobs, slug, manifest_hash, payload)
    await _announce_done(progress, study_thread_id, payload, n_overwritten = n_overwritten, cache_hit = False)
    return payload


async def _announce_done(progress, study_thread_id: str, payload: dict, *, n_overwritten: int, cache_hit: bool) -> None:
    await progress.emit_progress(
        study_thread_id, "study", "book_harmonize_done",
        n_chapters = payload.get("n_chapters", 0), n_atomic_claims = payload.get("n_atomic_claims", 0), n_canonical_terms = payload.get("n_canonical_terms", 0),
        n_chapters_with_issues = payload.get("n_chapters_with_issues", 0), n_chapters_patched = payload.get("n_chapters_patched", 0),
        n_chapters_overwritten = n_overwritten, elapsed_ms = payload.get("elapsed_ms", 0), cache_hit = cache_hit,
    )
