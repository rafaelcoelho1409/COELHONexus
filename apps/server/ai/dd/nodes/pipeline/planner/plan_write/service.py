"""plan_write I/O shell — persists the final plan blob (versioned + latest pointer).

A deterministic node, no model: load the selected outline, apply the pedagogical order, sanitize, write the versioned blob and the
latest pointer. The plan's identity follows the CONTENT of the outline and the order it is built from, not the constant "latest" path:
hashing the ref alone made every re-plan of a slug hit this cache and return the PREVIOUS plan."""
from __future__ import annotations
import domains
from . import domain, versions
from .. import order_chapters
from ..... import graphs

import asyncio
import hashlib
import json
import logging
import time
import datetime
import typing

import numpy as np


logger = logging.getLogger(__name__)


async def add_reading_time(
    blobs: typing.Any, slug: str, chapters: list[dict], *, exclude_reference_appendix: bool = False,
) -> None:
    """Tutorial mode: each chapter records the minutes its pages take to read, the best code it must teach and the minutes its tutorial is estimated to take
    (`reading_minutes`, `n_best_code`, `tutorial_minutes`). The figures are for the reader and the review of the plan; a chapter whose pages cannot be read just has none."""
    for chapter in chapters:
        try:
            stats = await domains.dd.pipeline.ledger.service.chapter_stats(blobs, slug, list(chapter.get("sources") or []))
            if exclude_reference_appendix and (
                chapter.get("kind") == "appendix" or chapter.get("role") == "appendix"
            ):
                stats["tutorial_minutes"] = 0.0
                stats["n_best_code"] = max(
                    int(chapter.get("n_best_code") or 0),
                    len(chapter.get("best_code_ids") or []),
                )
            chapter.update(stats)
        except Exception as error:
            logger.warning(f"[plan_write] {slug}: no reading time for {chapter.get('id')!r} ({type(error).__name__}: {error})")


async def persist_plan(blobs: typing.Any, *, versioned_key: str, latest_key: str, plan: dict) -> None:
    """Hash-keyed versioned blob + mutable latest pointer (no S3 symlink), written together."""
    body = json.dumps(plan, indent = 2, ensure_ascii = False)
    await asyncio.gather(
        blobs.write(versioned_key, body, content_type = "application/json"),
        blobs.write(latest_key, body, content_type = "application/json"),
    )


async def plan_write_run(state: graphs.pipeline.planner.states.PlannerState, blobs: typing.Any) -> dict:
    """Final-plan persist: load outline → pedagogical reorder → sanitize → versioned + latest."""
    slug = state.get("framework_slug")
    chapter_plan_ref = state.get("chapter_plan_ref") or ""
    if not slug or not chapter_plan_ref:
        return {"plan_path": "", "status": "done"}

    t0 = time.monotonic()
    outline_text: str | None = None
    try:
        outline_text = await blobs.read_text(chapter_plan_ref)
    except Exception:
        pass
    plan_identity = (
        f"{chapter_plan_ref}#{hashlib.sha256((outline_text or '').encode('utf-8')).hexdigest()[:16]}"
        f"|order={state.get('chapter_order_ref') or ''}"
    )
    planner_version = state.get("planner_version") or "v1"
    is_v2 = planner_version == "v2"
    if is_v2:
        plan_identity += f"|page_cards={state.get('page_cards_ref') or ''}"
    schema_version = "2.1-planner-v2-source-fingerprints" if is_v2 else versions.SCHEMA_VERSION
    manifest_hash = domain.compute_manifest_hash(
        f"{plan_identity}|planner=v2" if is_v2 else plan_identity, schema_version,
    )
    versioned_key = domains.dd.pipeline.storage.keys.plan_versioned_key(slug, manifest_hash)
    latest_key = domains.dd.pipeline.storage.keys.plan_latest_key(slug)

    # Unconditional `start` so the UI shows running even on a cache hit.
    graphs.service.emit("plan_write", "start", manifest_hash = manifest_hash)

    exists_v, exists_l = await asyncio.gather(blobs.exists(versioned_key), blobs.exists(latest_key))
    if exists_v and exists_l:
        try:
            latest = json.loads(await blobs.read_text(latest_key)) or {}
            if latest.get("manifest_hash") == manifest_hash:
                chapters = latest.get("chapters") or []
                latest_stats = latest.get("stats") or {}
                n_sources = sum(len(c.get("sources") or []) for c in chapters)
                elapsed = graphs.service.elapsed_ms(t0)
                stats = {
                    "n_chapters":     len(chapters),
                    "n_sources":      n_sources,
                    "n_unassigned":   latest_stats.get("n_unassigned", len(latest.get("unassigned") or [])),
                    "n_dropped":      latest_stats.get("n_dropped", 0),
                    "wall_ms":        elapsed,
                    "store_path":     latest_key,
                    "versioned_path": versioned_key,
                    "manifest_hash":  manifest_hash,
                    "cache_hit":      True,
                    "plan":           latest,
                }
                health = latest_stats.get("pipeline_health") or {}
                graphs.service.emit(
                    "plan_write", "done",
                    n_chapters = len(chapters), n_sources = n_sources,
                    n_unassigned = stats["n_unassigned"], n_dropped = stats["n_dropped"],
                    wall_ms = elapsed, cache_hit = True,
                )
                logger.info(f"[plan_write] {slug}: CACHE HIT — {len(chapters)} chapters, {n_sources} sources, {elapsed} ms")
                return {
                    "plan_path": latest_key,
                    "plan_write_stats": stats,
                    "status": "needs_review" if health.get("degraded") else "done",
                }
        except Exception as error:
            logger.warning(f"[plan_write] {slug}: cached latest unreadable ({type(error).__name__}: {error}); regenerating")

    if outline_text is None:
        outline_text = await blobs.read_text(chapter_plan_ref)
    outline = domain.load_outline(outline_text)

    cluster_keys: list[str] = []
    refined_assignments_list: list[int] = []
    for synth_cid, ch in enumerate((outline or {}).get("chapters") or []):
        if isinstance(ch, dict):
            ch["member_cluster_ids"] = [synth_cid]
        for key in (ch or {}).get("member_doc_keys") or []:
            cluster_keys.append(key)
            refined_assignments_list.append(synth_cid)
    refined_assignments = np.array(refined_assignments_list, dtype = np.int64)

    graphs.service.emit(
        "plan_write", "loaded",
        n_chapters_in = len((outline or {}).get("chapters") or []),
        n_clusters = len({int(c) for c in refined_assignments if int(c) >= 0}),
        n_docs = len(cluster_keys),
    )

    order_ref = state.get("chapter_order_ref") or ""
    raw_chapters = (outline or {}).get("chapters") or []
    reorder_applied = False
    if order_ref and raw_chapters:
        try:
            order = order_chapters.domain.load_chapter_order(await blobs.read_text(order_ref))
            if order is not None and len(order) == len(raw_chapters):
                reordered = [raw_chapters[i] for i in order]
                for new_pos, ch in enumerate(reordered):
                    if isinstance(ch, dict):
                        ch["order"] = new_pos + 1
                raw_chapters = reordered
                reorder_applied = True
                graphs.service.emit("plan_write", "reordered", order = order)
                logger.info(f"[plan_write] {slug}: applied pedagogical order from {order_ref!r}: {order}")
            else:
                logger.warning(
                    f"[plan_write] {slug}: chapter_order_ref {order_ref!r} length mismatch "
                    f"(got {len(order) if order else 'None'}, expected {len(raw_chapters)}); identity order kept"
                )
        except Exception as error:
            logger.warning(
                f"[plan_write] {slug}: chapter_order_ref {order_ref!r} unreadable "
                f"({type(error).__name__}: {error}); identity order kept"
            )

    prerequisite_order_stats = {}
    if is_v2 and raw_chapters:
        order, prerequisite_order_stats = order_chapters.domain.respect_prerequisites(
            list(range(len(raw_chapters))), raw_chapters,
        )
        if order != list(range(len(raw_chapters))):
            raw_chapters = [raw_chapters[index] for index in order]
            reorder_applied = True
            logger.warning(f"[plan_write] {slug}: repaired prerequisite order after loading the outline: {order}")
        for new_pos, chapter in enumerate(raw_chapters):
            if isinstance(chapter, dict):
                chapter["order"] = new_pos + 1

    cluster_to_keys = domain.build_cluster_to_keys(refined_assignments, cluster_keys)
    chapters, n_dropped = domain.sanitize_chapters(
        raw_chapters, cluster_to_keys, preserve_metadata = is_v2, stable_identity = is_v2,
    )
    if graphs.pipeline.params.TUTORIAL_MODE or is_v2:
        await add_reading_time(blobs, slug, chapters, exclude_reference_appendix = is_v2)
    if is_v2:
        by_title: dict[str, str] = {}
        for chapter in chapters:
            by_title.setdefault(" ".join(chapter["title"].casefold().split()), chapter["id"])
        for chapter in chapters:
            titles = list(chapter.get("prerequisites") or [])
            ids = []
            resolved_titles = []
            for title in titles:
                key = " ".join(str(title).casefold().split())
                dependency = by_title.get(key)
                if dependency and dependency != chapter["id"] and dependency not in ids:
                    ids.append(dependency)
                    resolved_titles.append(next((item["title"] for item in chapters if item["id"] == dependency), str(title)))
            chapter["prerequisite_titles"] = resolved_titles
            chapter["prerequisites"] = ids
    n_sources_total = sum(len(c["sources"]) for c in chapters)
    unassigned_keys = sorted(set(cluster_keys) - {k for c in chapters for k in c["sources"]})
    graphs.service.emit(
        "plan_write", "sanitized",
        n_chapters = len(chapters), n_dropped = n_dropped,
        n_sources = n_sources_total, n_unassigned = len(unassigned_keys),
    )

    reading = domains.dd.pipeline.ledger.domain.reading_plan(chapters) if (graphs.pipeline.params.TUTORIAL_MODE or is_v2) else None
    if reading and reading["over_budget"]:
        logger.warning(f"[plan_write] {slug}: chapters over the reading budget: {reading['over_budget']}")
    page_card_payload = {}
    if is_v2 and state.get("page_cards_ref"):
        try:
            page_card_payload = json.loads(await blobs.read_text(state["page_cards_ref"])) or {}
        except Exception as error:
            logger.warning(f"[plan_write] {slug}: page-card provenance unavailable ({type(error).__name__}: {error})")
    if is_v2:
        card_rows = page_card_payload.get("cards") or {}
        if isinstance(card_rows, dict):
            for chapter in chapters:
                chapter["source_content_digests"] = {
                    source: card_rows[source]["content_digest"]
                    for source in chapter.get("sources") or []
                    if isinstance(card_rows.get(source), dict)
                    and isinstance(card_rows[source].get("content_digest"), str)
                    and card_rows[source]["content_digest"]
                }
    health = domain.pipeline_health(state)
    if is_v2:
        missing_digests = sum(
            len(chapter.get("sources") or []) - len(chapter.get("source_content_digests") or {})
            for chapter in chapters
        )
        health["chapter_source_digests_missing"] = missing_digests
        health["degraded"] = bool(health.get("degraded") or missing_digests)
    plan = {
        "schema_version": schema_version,
        **({"planner_version": planner_version} if is_v2 else {}),
        "framework_slug": slug,
        "manifest_hash":  manifest_hash,
        "generated_at":   datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "chapters":       chapters,
        "unassigned":     unassigned_keys,
        "provenance": {
            "chapter_plan_ref":  chapter_plan_ref,
            **({"page_cards_ref": state.get("page_cards_ref")} if is_v2 else {}),
            **({
                "chapter_proposals_ref": state.get("chapter_proposals_ref"),
                "chapter_doc_assignments_ref": state.get("chapter_doc_assignments_ref"),
                "chapter_order_ref": state.get("chapter_order_ref"),
            } if is_v2 else {}),
            "prompt_versions":   {"plan_write": versions.PROMPT_VERSION},
            "corpus_doc_count":  len(cluster_keys),
            "chapter_count":     len({int(c) for c in refined_assignments if int(c) >= 0}),
        },
        "stats": {
            "n_chapters":   len(chapters),
            "n_sources":    n_sources_total,
            "n_unassigned": len(unassigned_keys),
            "n_dropped":    n_dropped,
            "pipeline_health": health,
            **({"reading": reading} if reading else {}),
            **({
                "page_cards": {
                    key: value for key, value in (state.get("page_cards_stats") or {}).items()
                    if key != "dropped_pages"
                },
                "dropped_pages": page_card_payload.get("dropped_pages") or {},
                "chapter_design": state.get("chapter_design_stats") or {},
                "order": state.get("order_chapters_stats") or {},
                "prerequisite_order_repairs": prerequisite_order_stats,
            } if is_v2 else {}),
        },
    }
    await persist_plan(blobs, versioned_key = versioned_key, latest_key = latest_key, plan = plan)

    elapsed = graphs.service.elapsed_ms(t0)
    stats = {
        "n_chapters":      len(chapters),
        "n_sources":       n_sources_total,
        "n_unassigned":    len(unassigned_keys),
        "n_dropped":       n_dropped,
        "wall_ms":         elapsed,
        "store_path":      latest_key,
        "versioned_path":  versioned_key,
        "manifest_hash":   manifest_hash,
        "cache_hit":       False,
        "reorder_applied": reorder_applied,
        "plan":            plan,
    }
    graphs.service.emit(
        "plan_write", "done",
        n_chapters = len(chapters), n_sources = n_sources_total,
        n_unassigned = len(unassigned_keys), n_dropped = n_dropped, wall_ms = elapsed,
    )
    sizes = [len(c["sources"]) for c in chapters]
    logger.info(
        f"[plan_write] {slug}: {len(chapters)} chapters, {n_sources_total} sources, {n_dropped} dropped, "
        f"{len(unassigned_keys)} unassigned; chapter sizes max={max(sizes) if sizes else 0} min={min(sizes) if sizes else 0}; "
        f"wrote {latest_key} + {versioned_key} in {elapsed} ms"
    )
    return {
        "plan_path": latest_key,
        "plan_write_stats": stats,
        "status": "needs_review" if health.get("degraded") else "done",
    }
