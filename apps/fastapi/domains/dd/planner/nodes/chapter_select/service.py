"""chapter_select I/O shell — persist the 4-blob output (select-specific +
reduce-compatible plan, each versioned + latest) + the chapter_select_run
orchestration.

SOTA Sept 2026: deterministic selection (greedy coverage → prune →
consolidation incl. anchored-family harmonization → Foundations chapter, all in
`domain.select_pipeline`, shared with the offline replay) plus ONE optional LLM
step: an oversize chapter (> params.SPLIT_MAX_SHARE of the corpus) is split by a
single structured call over family/doc units. That step is fail-soft — any
error leaves the chapter exactly as the deterministic pipeline produced it.
Parallel I/O (gather proposals/assignments/seeds, concurrent 4-blob writes).
"""
from __future__ import annotations
import domains
from . import domain, keys, params, prompts, schemas, versions

import asyncio
import json
import logging
import time


logger = logging.getLogger(__name__)


async def _split_by_llm(
    framework: str, chapter: dict, units: list[dict], n_parts: int, other_titles: list[str],
) -> list[dict] | None:
    """Fallback partition: one structured LLM call (+ one repair) partitioning `units` into
    `n_parts` cohesive chapters. None on any failure → caller keeps the chapter."""
    repair = ""
    for attempt in range(2):
        prompt = prompts.build_split_prompt(
            framework = framework, title = chapter["title"],
            description = chapter.get("description") or "", units = units,
            n_parts = n_parts, other_titles = other_titles, repair = repair,
        )
        try:
            plan, _meta = await domains.settings.chat.service.chat_structured_async(
                prompt, schemas.SplitPlan,
                max_tokens = params.SPLIT_MAX_TOKENS, temperature = 0.2 if attempt == 0 else 0.0,
                timeout_s = params.SPLIT_TIMEOUT_S,
            )
        except Exception as e:
            logger.warning(
                f"[chapter_select] split of {chapter['title']!r} attempt {attempt + 1}: "
                f"{type(e).__name__}: {str(e)[:160]}"
            )
            repair = f"output could not be parsed ({type(e).__name__}); return strictly valid JSON"
            continue
        parts = domain.normalize_split([g.model_dump() for g in plan.groups], units)
        ok, reason = domain.validate_split(
            parts, units, {t.casefold() for t in other_titles},
            max_parts = max(n_parts, params.SPLIT_MAX_PARTS),
        )
        if ok:
            return domain.apply_split(chapter, parts, units)
        logger.info(f"[chapter_select] split of {chapter['title']!r} rejected: {reason}")
        repair = reason
    return None


async def _split_by_clusters(
    framework: str, chapter: dict, units: list[dict], n_parts: int, other_titles: list[str],
    distillates: dict[str, dict],
) -> list[dict] | None:
    """Computed partition (domain.cluster_units) + one LLM call that only NAMES the groups
    (deterministic word-based titles when the model cannot). None when the pages do not
    cluster into 2+ groups of at least the size floor."""
    floor = domain.split_floor(sum(len(u["keys"]) for u in units), n_parts)
    texts = [domain.unit_text(u, distillates) for u in units]
    groups = domain.cluster_units(units, texts, n_parts, floor)
    if not groups:
        return None
    taken = {t.casefold() for t in other_titles}
    repair = ""
    for attempt in range(2):
        prompt = prompts.build_name_prompt(
            framework = framework, title = chapter["title"], groups = groups, units = units,
            other_titles = other_titles, repair = repair,
        )
        try:
            named, _meta = await domains.settings.chat.service.chat_structured_async(
                prompt, schemas.GroupNames,
                max_tokens = params.SPLIT_NAME_MAX_TOKENS, temperature = 0.2 if attempt == 0 else 0.0,
                timeout_s = params.SPLIT_TIMEOUT_S,
            )
        except Exception as e:
            logger.warning(
                f"[chapter_select] naming {len(groups)} groups of {chapter['title']!r} "
                f"attempt {attempt + 1}: {type(e).__name__}: {str(e)[:160]}"
            )
            repair = f"output could not be parsed ({type(e).__name__}); return strictly valid JSON"
            continue
        by_id = {g.id: g for g in named.groups}
        if set(by_id) != set(range(len(groups))) or len(named.groups) != len(groups):
            repair = f"return exactly one entry for each group id 0..{len(groups) - 1}"
            continue
        parts = [
            {"title": by_id[i].title, "description": by_id[i].description, "unit_ids": g}
            for i, g in enumerate(groups)
        ]
        ok, reason = domain.validate_split(parts, units, taken, max_parts = len(groups))
        if ok:
            return domain.apply_split(chapter, parts, units)
        logger.info(f"[chapter_select] names for {chapter['title']!r} rejected: {reason}")
        repair = reason
    parts = [
        {**name, "unit_ids": g}
        for name, g in zip(domain.fallback_group_titles(groups, texts, taken), groups)
    ]
    ok, reason = domain.validate_split(parts, units, taken, max_parts = len(groups))
    if not ok:
        logger.info(f"[chapter_select] fallback names for {chapter['title']!r} rejected: {reason}")
        return None
    logger.info(f"[chapter_select] {chapter['title']!r}: groups named from their words (model could not)")
    return domain.apply_split(chapter, parts, units)


async def _split_one(
    framework: str, chapter: dict, units: list[dict], n_parts: int, other_titles: list[str],
    distillates: dict[str, dict],
) -> list[dict] | None:
    """Split one chapter: computed clusters named by the LLM; the model-proposed partition
    (with repair) only when the pages do not cluster."""
    parts = await _split_by_clusters(framework, chapter, units, n_parts, other_titles, distillates)
    if parts:
        return parts
    return await _split_by_llm(framework, chapter, units, n_parts, other_titles)


async def split_oversize_chapters(
    framework: str,
    out_chapters: list[dict],
    *,
    anchored_family: dict[str, str],
    doc_title: dict[str, str],
    distillates: dict[str, dict],
    key_idx: dict[str, int],
    key_dir: dict[str, tuple] | None = None,
) -> tuple[list[dict], dict]:
    """Split every chapter over the share cap (see domain.plan_splits). Fail-soft
    per chapter; splits run concurrently after one Rotator-recovery wait (the
    preceding node, chapter_assign, just burst the pool)."""
    plans = domain.plan_splits(out_chapters)
    stats = {"n_split_planned": len(plans), "n_split_applied": 0, "splits": []}
    if not plans:
        return out_chapters, stats
    try:
        await domains.settings.chat.service.wait_for_rotator_settle(params.SPLIT_SETTLE_CAP_S)
    except Exception as e:
        logger.info(f"[chapter_select] settle wait skipped ({type(e).__name__}: {e})")
    titles = [c["title"] for c in out_chapters]

    async def _one(pos: int, k: int):
        ch = out_chapters[pos]
        units = domain.build_split_units(
            ch["member_doc_keys"], anchored_family = anchored_family, doc_title = doc_title,
            distillates = distillates, key_idx = key_idx, key_dir = key_dir,
        )
        if len(units) < 2:
            return pos, None
        return pos, await _split_one(
            framework, ch, units, k, [t for t in titles if t != ch["title"]], distillates,
        )

    results = await asyncio.gather(*[_one(pos, k) for pos, k in plans], return_exceptions = True)
    replaced: dict[int, list[dict]] = {}
    for (pos, k), res in zip(plans, results):
        parts = None if isinstance(res, Exception) else res[1]
        stats["splits"].append({
            "title": out_chapters[pos]["title"], "n_docs": len(out_chapters[pos]["member_doc_keys"]),
            "n_parts": k, "applied": bool(parts),
        })
        if parts:
            replaced[pos] = parts
    if not replaced:
        return out_chapters, stats
    new_chapters: list[dict] = []
    for i, c in enumerate(out_chapters):
        new_chapters.extend(replaced.get(i, [c]))
    stats["n_split_applied"] = len(replaced)
    return domain._renumber(new_chapters), stats


async def persist_select_outputs(
    minio,
    *,
    slug: str,
    manifest: str,
    select_payload: dict,
    plan_payload: dict,
) -> tuple[str, str]:
    """Write select + plan blobs (versioned + latest) concurrently.
    Returns (plan_latest_key, plan_versioned_key). SOTA: 4 writes via
    gather on shared S3 client (vs 4 sequential) — ~4× for MinIO 4-blob."""
    select_blob = json.dumps(
        select_payload, indent = 2, ensure_ascii = False,
    )
    plan_blob = json.dumps(plan_payload, indent = 2, ensure_ascii = False)

    vkey_select = keys.select_versioned_key(slug, manifest)
    lkey_select = keys.select_latest_key(slug)
    plan_vkey = keys.chapter_plan_versioned_key(slug, manifest)
    plan_lkey = keys.chapter_plan_latest_key(slug)

    # Concurrent writes — shared client per chunk in storage.service handles
    # pooling; gather here removes sequential 4× RTT.
    await asyncio.gather(
        minio.write(vkey_select, select_blob, content_type = "application/json"),
        minio.write(lkey_select, select_blob, content_type = "application/json"),
        minio.write(plan_vkey, plan_blob, content_type = "application/json"),
        minio.write(plan_lkey, plan_blob, content_type = "application/json"),
    )

    return plan_lkey, plan_vkey


async def chapter_select_run(state: domains.dd.planner.state.PlannerState) -> dict:
    """Pure-algorithm node: load → greedy-coverage (with pin override) →
    prune (<MIN_DOCS unless pinned) → re-assign survivors → persist."""
    slug = state.get("framework_slug")
    thread_id = state.get("thread_id") or ""
    proposals_ref = state.get("chapter_proposals_ref")
    assignments_ref = state.get("chapter_doc_assignments_ref")

    if not slug or not proposals_ref or not assignments_ref:
        return {
            "chapter_plan_ref": None,
            "select_stats": {"skipped": "missing_inputs"},
        }

    t0 = time.monotonic()
    minio = domains.dd.ingestion.storage.service.get_storage()

    manifest = domain.manifest_hash(
        slug = slug,
        proposals_ref = proposals_ref,
        assignments_ref = assignments_ref,
    )

    # Parallel load: proposals + assignments + seeds in one gather (3× RTT → 1×)
    # Pure I/O, no LLM — old built-in never touched rotator here, so SOTA is
    # just I/O concurrency, not pooling.
    async def _load_seeds() -> dict:
        try:
            txt = await minio.read_text(proposals_ref)
            return (json.loads(txt) or {}).get("seeds") or {}
        except Exception:
            return {}

    proposals_obj, assignments, seeds = await asyncio.gather(
        domains.dd.planner.nodes.chapter_propose.service.load_proposals(minio, slug),
        domains.dd.planner.nodes.chapter_assign.service.load_assignments(minio, slug),
        _load_seeds(),
    )
    if proposals_obj is None or not proposals_obj.proposals:
        return {
            "chapter_plan_ref": None,
            "select_stats": {"skipped": "no_proposals"},
        }
    proposals = [p.model_dump() for p in proposals_obj.proposals]
    if not assignments:
        return {
            "chapter_plan_ref": None,
            "select_stats": {"skipped": "no_assignments"},
        }

    # Manifest (best-effort): parent-page family, title and source order per doc.
    # Without it the pipeline degrades to per-doc placement (no families/order).
    entries: list[dict] = []
    try:
        manifest_obj = await domains.dd.ingestion.storage.service.read_framework_manifest(minio, slug)
        entries = (manifest_obj or {}).get("entries") or []
    except Exception as e:
        logger.warning(
            f"[chapter_select] {slug}: manifest unavailable for family "
            f"consolidation ({type(e).__name__}: {e}) — per-doc fallback only"
        )
    doc_family = {e["key"]: (e.get("url") or "").split("#", 1)[0] for e in entries if e.get("key")}
    doc_title = {e["key"]: e.get("title") or "" for e in entries if e.get("key")}
    key_idx = {e["key"]: int(e.get("idx") or 0) for e in entries if e.get("key")}
    anchored_family = domain.anchored_family_map(entries)
    try:
        distillates = await domains.dd.planner.nodes.doc_distill.service.load_distillates(minio, slug)
    except Exception:
        distillates = {}

    await domains.dd.planner.runtime.progress.service.emit_progress(
        thread_id, "chapter_select", "start",
        n_proposals = len(proposals),
        n_docs = len(assignments),
        n_pinned = len(domain.detect_pinned_indices(proposals, seeds)),
    )

    result = domain.select_pipeline(
        proposals = proposals, assignments = assignments, seeds = seeds,
        doc_family = doc_family, doc_title = doc_title, distillates = distillates,
        key_idx = key_idx, anchored_family = anchored_family,
    )
    pinned = result["pinned"]
    pruned = result["pruned"]
    orphan_protected = result["orphan_protected"]
    kept = result["kept"]
    out_chapters = result["out_chapters"]
    consolidation = result["stats"]
    if consolidation["n_unplaced_final"]:
        logger.warning(
            f"[chapter_select] {slug}: {consolidation['n_unplaced_final']} doc(s) "
            f"still unplaced after consolidation — they will be missing from the plan"
        )
    if any(consolidation.get(k) for k in (
        "n_family_consolidated", "n_family_harmonized", "n_rehomed_to_family",
        "n_rescued_chapters", "n_gap_chapters", "n_leftover_placed", "n_foundations",
    )):
        logger.info(f"[chapter_select] {slug}: placement consolidation {consolidation}")

    # Oversize split — the one LLM step; fail-soft (chapter kept as-is on any error).
    split_stats = {"n_split_planned": 0, "n_split_applied": 0, "splits": []}
    try:
        out_chapters, split_stats = await split_oversize_chapters(
            slug, out_chapters, anchored_family = anchored_family, doc_title = doc_title,
            distillates = distillates, key_idx = key_idx, key_dir = domain.page_dirs(entries),
        )
    except Exception as e:
        logger.warning(f"[chapter_select] {slug}: oversize split skipped ({type(e).__name__}: {e})")
    consolidation = {**consolidation, **{k: v for k, v in split_stats.items() if k != "splits"}}
    if split_stats["splits"]:
        logger.info(f"[chapter_select] {slug}: oversize split {split_stats['splits']}")
    doc_to_chapter = {k: i for i, c in enumerate(out_chapters) for k in c["member_doc_keys"]}

    n_assigned_docs = len(doc_to_chapter)
    n_total_docs = len(assignments)

    select_payload = {
        "prompt_version":        versions.PROMPT_VERSION,
        "framework_slug":        slug,
        "manifest_hash":         manifest,
        "selected_indices":      kept,
        "splits":                split_stats["splits"],
        "pruned_indices":        pruned,
        "pinned_indices":        sorted(pinned),
        "orphan_protected":      orphan_protected,
        "n_proposals_in":        len(proposals),
        "n_chapters_out":        len(out_chapters),
        "n_orphan_protected":    len(orphan_protected),
        "consolidation":         consolidation,
        "n_assigned_docs":       n_assigned_docs,
        "n_total_docs":          n_total_docs,
        "coverage_fraction":     (
            n_assigned_docs / n_total_docs if n_total_docs else 0.0
        ),
        "chapters":              out_chapters,
    }
    plan_payload = {
        "prompt_version":  versions.PROMPT_VERSION,
        "framework_slug":  slug,
        "manifest_hash":   manifest,
        "outline": {
            "chapters": [
                {
                    "title":              c["title"],
                    "description":        c["description"],
                    "member_cluster_ids": [],   # n/a — LLM-first path
                    "member_doc_keys":    c["member_doc_keys"],
                    "order":              c["order"],
                    "origin":             c.get("origin"),
                    "role":               c.get("role"),
                }
                for c in out_chapters
            ],
        },
        "n_clusters_in":   len(proposals),
        "n_chapters_out":  len(out_chapters),
        "n_repairs":       0,
        "forced_repair":   False,
        "source":          "llm_first_chapter_select_v1",
    }

    plan_lkey, _plan_vkey = await persist_select_outputs(
        minio,
        slug = slug,
        manifest = manifest,
        select_payload = select_payload,
        plan_payload = plan_payload,
    )

    wall_ms = int((time.monotonic() - t0) * 1000)
    stats = {
        "n_proposals_in":     len(proposals),
        "n_chapters_out":     len(out_chapters),
        "n_pruned":           len(pruned),
        "consolidation":      consolidation,
        "n_orphan_protected": len(orphan_protected),
        "n_assigned_docs":    n_assigned_docs,
        "n_total_docs":       n_total_docs,
        "coverage_fraction":  (
            n_assigned_docs / n_total_docs if n_total_docs else 0.0
        ),
        "wall_ms":            wall_ms,
        "manifest_hash":      manifest,
        "chapter_titles":     [c["title"] for c in out_chapters],
        "chapter_sizes":      [c["n_member_docs"] for c in out_chapters],
    }
    if orphan_protected:
        logger.info(
            f"[chapter_select] {slug}: orphan-protected "
            f"{len(orphan_protected)} small chapter(s) whose members had "
            f"no alternative above-threshold chapter: {orphan_protected}"
        )
    await domains.dd.planner.runtime.progress.service.emit_progress(
        thread_id, "chapter_select", "done",
        n_chapters = len(out_chapters),
        n_pruned = len(pruned),
        n_orphan_protected = len(orphan_protected),
        coverage = stats["coverage_fraction"],
        wall_ms = wall_ms,
        titles = stats["chapter_titles"],
    )
    return {"chapter_plan_ref": plan_lkey, "select_stats": stats}
