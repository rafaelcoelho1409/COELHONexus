"""outline_sdp steps — the nodes of its subgraph: prepare, draft (the chapter_outliner agent), finalize.

The outliner reads an index of the chapter's sources (and any page it wants in full) and answers the whole outline; a `validate_outline` tool runs the
stage's own structural rules and a scope-overlap check on every draft, so the outline is repaired inside the agent's run. What the tool still finds wrong in
the best answer is the brief of one more run. The deterministic passes that always followed (flatten a deep prerequisite chain, trim to the section cap)
and the heuristic fallback outline are unchanged."""
from __future__ import annotations
import domains
from . import domain, keys, params, schemas, versions
from .. import shared
from ..... import agents, backends, graphs, tools

import json
import logging
import time

import numpy as np
from langgraph.runtime import Runtime


logger = logging.getLogger(__name__)

Context = graphs.pipeline.synth.context.SynthContext
STEP = "outline_sdp"


async def semantic_h2_duplicates(outline: schemas.ChapterOutline) -> list[str]:
    """Issues for scope-duplicate H2 pairs (embedding cosine OR lexical overlap). Fail-soft: the lexical pass still runs when the embedder is unavailable."""
    sections = outline.sections
    if len(sections) <= 1:
        return []
    words = [domain.scope_words(f"{s.heading} {s.description}") for s in sections]
    sim = None
    try:
        embeddings, _model = await domains.settings.embeddings.service.embed_texts_async([f"{s.heading}\n{s.description}" for s in sections])
        embs = np.array(embeddings, dtype = np.float32)
        norms = np.linalg.norm(embs, axis = 1, keepdims = True)
        norms[norms == 0] = 1.0
        normed = embs / norms
        sim = normed @ normed.T
    except Exception as error:
        logger.warning(f"[outline_sdp] semantic H2 dedup embed/cosine failed: {type(error).__name__}: {error} — falling back to the lexical scope check")

    flagged: list[tuple[str, str, float, str]] = []
    for i in range(len(sections)):
        for j in range(i + 1, len(sections)):
            cos = float(sim[i, j]) if sim is not None else 0.0
            wi, wj = words[i], words[j]
            jac = (len(wi & wj) / len(wi | wj)) if (wi or wj) else 0.0
            if cos >= params.SEMANTIC_H2_DEDUP_THRESHOLD or jac >= params.SCOPE_LEXICAL_JACCARD:
                kind = "cosine" if cos >= params.SEMANTIC_H2_DEDUP_THRESHOLD else "lexical"
                flagged.append((sections[i].heading, sections[j].heading, max(cos, jac), kind))
    if not flagged:
        return []
    pairs = [f"{a!r} ↔ {b!r} ({s:.0%} {kind})" for a, b, s, kind in flagged[:3]]
    suffix = f", +{len(flagged) - 3} more pairs" if len(flagged) > 3 else ""
    return [
        f"Scope-duplicate H2 section pairs detected ({len(flagged)} pair(s); embedding cosine ≥ {params.SEMANTIC_H2_DEDUP_THRESHOLD:.0%} OR content-word "
        f"overlap ≥ {params.SCOPE_LEXICAL_JACCARD:.0%}): {', '.join(pairs)}{suffix}. These sections cover the SAME scope (same APIs / examples) with "
        f"different wording — MERGE each pair into ONE section under a unified heading, OR re-scope one to a genuinely distinct capability. Overlapping "
        f"sections make the writer recycle code; the renderer then strips the duplicates, leaving hollow 'see other section' sections."
    ]


async def outline_issues(
    outline: schemas.ChapterOutline, *, n_sources: int, n_blocks: int, bodies: list[str],
) -> tuple[schemas.OutlineDAG, list[str]]:
    """The DAG of an outline and everything the stage's rules find wrong in it (structure, then scope overlap)."""
    dag = domain.derive_dag(outline.sections)
    _, issues = domain.validate_outline_structure(outline, dag, n_sources = n_sources, n_blocks = n_blocks)
    issues = [issue + domain.floor_repair_hint(bodies) if domain.is_floor_issue(issue) else issue for issue in issues]
    return dag, issues + await semantic_h2_duplicates(outline)


async def prepare(state: graphs.pipeline.synth.outline.state.OutlineState, runtime: Runtime[Context]) -> dict:
    context = runtime.context
    blobs = context.blobs
    slug = state.get("framework_slug")
    chapter_id = state.get("chapter_id")
    started = time.monotonic()
    if not slug or not chapter_id:
        return {"finished": True, **shared.service.failure(
            "outline_path", "outline_stats", "no_slug_or_chapter_id", "framework_slug or chapter_id missing from SynthState", started,
        )}

    plan_key = domains.dd.pipeline.storage.keys.plan_latest_key(slug)
    plan = await shared.service.load_json(blobs, plan_key)
    if plan is None:
        return {"finished": True, **shared.service.failure(
            "outline_path", "outline_stats", "plan_not_found", f"planner plan {plan_key!r} not in MinIO (or unreadable); run planner first", started,
            plan_key = plan_key,
        )}
    chapter = domain.find_chapter(plan, chapter_id)
    if chapter is None:
        return {"finished": True, **shared.service.failure(
            "outline_path", "outline_stats", "chapter_not_in_plan", f"chapter {chapter_id!r} not in plan-latest.json", started,
            known_ids = [c.get("id") for c in (plan.get("chapters") or [])],
        )}
    chapter_title = chapter.get("title") or chapter_id
    chapter_description = chapter.get("description") or ""
    sources = sorted(chapter.get("sources") or [])
    if not sources:
        return {"finished": True, **shared.service.failure(
            "outline_path", "outline_stats", "no_sources", f"chapter {chapter_id!r} has zero sources in plan", started,
        )}

    shared.service.emit(STEP, "start", chapter_id = chapter_id, chapter_title = chapter_title, n_sources = len(sources))

    # Each source is already corpus_normalized and vault-built by ingestion: just read them.
    bodies = list(await blobs.read_many(sources))
    pairs = [(key, body) for key, body in zip(sources, bodies) if body]
    if not pairs:
        return {"finished": True, **shared.service.failure(
            "outline_path", "outline_stats", "source_bodies_empty", "all source bodies came back empty", started,
        )}
    texts = [body for _, body in pairs]
    sources_concat_md, truncated = domain.concat_sources(texts)
    n_vault_hashes = domain.count_code_blocks(texts)
    shared.service.emit(
        STEP, "sources_loaded",
        n_sources = len(sources), n_bodies = len(pairs), bytes = len(sources_concat_md), truncated = truncated, n_vault_hashes = n_vault_hashes,
    )

    manifest_hash = domain.compute_manifest_hash(
        sources = sources, sources_bytes = len(sources_concat_md), chapter_title = chapter_title, chapter_description = chapter_description,
    )
    versioned_key = keys.versioned_blob_key(slug, chapter_id, manifest_hash)
    latest_key = keys.latest_blob_key(slug, chapter_id)

    if await blobs.exists(versioned_key) and await blobs.exists(latest_key):
        try:
            cached = json.loads(await blobs.read_text(versioned_key))
            outline_dict = (cached or {}).get("outline") or {}
            dag_dict = (cached or {}).get("dag") or {}
            if domain.is_heuristic_fallback_outline(outline_dict):
                # A prior run's drafts all failed and shipped the heuristic fallback; serving it from cache would turn a transient outage into a permanent bad outline.
                logger.info(f"[outline_sdp] {slug}/{chapter_id}: cached outline is a heuristic fallback — ignoring cache, drafting again")
            else:
                elapsed = shared.service.elapsed_ms(started)
                stats = {
                    "n_sections": len(outline_dict.get("sections") or []),
                    "max_stage": int(dag_dict.get("max_stage", 0)),
                    "n_stages": len(dag_dict.get("stages") or {}),
                    "n_removed_edges": len(dag_dict.get("removed_edges") or []),
                    "wall_ms": elapsed,
                    "store_path": latest_key,
                    "versioned_path": versioned_key,
                    "manifest_hash": manifest_hash,
                    "cache_hit": True,
                    "prompt_version": cached.get("prompt_version"),
                }
                shared.service.emit(STEP, "done", n_sections = stats["n_sections"], max_stage = stats["max_stage"], wall_ms = elapsed, cache_hit = True)
                logger.info(f"[outline_sdp] {slug}/{chapter_id}: CACHE HIT — {stats['n_sections']} sections, max_stage = {stats['max_stage']}, {elapsed} ms")
                return {"finished": True, "outline_path": latest_key, "outline_stats": stats}
        except Exception as error:
            logger.warning(f"[outline_sdp] {slug}/{chapter_id}: cached blob {versioned_key!r} unreadable ({type(error).__name__}: {error}); recomputing")

    context.scratch[STEP] = {"pairs": pairs, "texts": texts, "concat": sources_concat_md}
    # Adaptive cap (not fixed 8): a fixed cap pushed the model to over-section small chapters; the right count up front rarely needs trimming.
    return {
        "finished": False,
        "started": started,
        "manifest_hash": manifest_hash,
        "versioned_key": versioned_key,
        "latest_key": latest_key,
        "sources": sources,
        "chapter_title": chapter_title,
        "chapter_description": chapter_description,
        "n_vault_hashes": n_vault_hashes,
        "truncated": truncated,
        "adaptive_target": params.max_h2_for_n_sources(len(sources), n_vault_hashes),
        "attempt": 0,
        "half": False,
        "feedback": [],
        "best": None,
        "last_error": "",
        "n_checks": 0,
    }


def source_files(pairs: list[tuple[str, str]]) -> dict[str, str]:
    """The chapter's sources as the outliner's `/sources/` files."""
    return {domain.source_path(n, key): body for n, (key, body) in enumerate(pairs, start = 1)}


async def draft(state: graphs.pipeline.synth.outline.state.OutlineState, runtime: Runtime[Context]) -> dict:
    """One outliner run. Returns the new best outline (by `accept_repair`) and what is still wrong with it."""
    context = runtime.context
    data = context.scratch[STEP]
    attempt = state.get("attempt", 0) + 1
    half = state.get("half", False)
    n_sources = len(state["sources"])
    n_blocks = state["n_vault_hashes"]
    texts = data["texts"]
    counts = {"checks": 0}

    async def check(candidate: dict) -> list[str]:
        outline, error = domain.try_parse_outline(candidate)
        if outline is None:
            counts["checks"] += 1
            return [f"The outline does not parse: {error}"]
        _, issues = await outline_issues(outline, n_sources = n_sources, n_blocks = n_blocks, bodies = texts)
        counts["checks"] += 1 if issues else 0
        return issues

    files = source_files(data["pairs"])
    route = agents.pipeline.synth.params.SOURCES_ROUTE
    index = domain.source_index([key for key, _ in data["pairs"]], [body for _, body in data["pairs"]])
    for entry in index:
        entry["path"] = route.rstrip("/") + entry["path"]
        if half:
            entry["excerpt"] = ""
            entry["headings"] = entry["headings"][:params.INDEX_HEADINGS // 2]
    role = context.agents.chapter_outliner(
        backend = backends.files.service.mounted(files, route),
        tools = [tools.pipeline.synth.check.service.validate_outline_tool(check)],
    )
    best = state.get("best")
    request = {
        "framework": state["framework_slug"],
        "chapter_id": state["chapter_id"],
        "chapter_title": state["chapter_title"],
        "chapter_description": state["chapter_description"],
        "target_sections": state["adaptive_target"],
        "n_code_blocks": n_blocks,
        "feedback": state.get("feedback") or [],
        "sources": index,
    }
    if best is not None:
        request["previous_outline"] = best["outline"]
    n_checks = state.get("n_checks", 0)
    try:
        answer = await agents.service.ask(
            role,
            agents.pipeline.synth.schemas.OutlineAnswer,
            json.dumps(request, ensure_ascii = False),
            attempts = params.ASK_ATTEMPTS,
            wait_base_s = params.RETRY_WAIT_S,
            check = lambda a: bool(a.sections),
        )
    except Exception as error:
        logger.warning(f"[outline_sdp] {state['framework_slug']}/{state['chapter_id']}: outliner run {attempt} failed: {type(error).__name__}: {str(error)[:200]}")
        shared.service.emit(STEP, "draft_done", attempt = attempt, ok = False, error = f"{type(error).__name__}: {str(error)[:120]}")
        return {
            "attempt": attempt,
            "half": half or domain.is_context_overflow_error(error),
            "last_error": f"{type(error).__name__}: {str(error)[:200]}",
            "n_checks": n_checks + counts["checks"],
        }

    outline, error = domain.try_parse_outline(answer.model_dump())
    if outline is None:
        logger.info(f"[outline_sdp] {state['framework_slug']}/{state['chapter_id']}: outliner run {attempt} answered an invalid outline — {error}")
        shared.service.emit(STEP, "draft_done", attempt = attempt, ok = False, error = "invalid_outline")
        return {
            "attempt": attempt,
            "feedback": [f"Your previous answer was rejected: {error}"],
            "last_error": f"pydantic: {error}",
            "n_checks": n_checks + counts["checks"],
        }

    _, issues = await outline_issues(outline, n_sources = n_sources, n_blocks = n_blocks, bodies = texts)
    candidate = {"outline": outline.model_dump(), "issues": issues}
    accepted = best is None or domain.accept_repair(best["issues"], issues, params.REPAIR_FLOOR_SLACK)
    shared.service.emit(
        STEP, "draft_done",
        attempt = attempt, ok = True, n_sections = len(outline.sections), n_violations = len(issues), accepted = accepted,
    )
    logger.info(
        f"[outline_sdp] {state['framework_slug']}/{state['chapter_id']}: outliner run {attempt}: {len(outline.sections)} sections, "
        f"{len(issues)} issue(s) — {'kept' if accepted else 'rejected'}"
    )
    kept = candidate if accepted else best
    return {
        "attempt": attempt,
        "best": kept,
        "feedback": list(kept["issues"]) if kept else [],
        "last_error": "",
        "n_checks": n_checks + counts["checks"],
    }


async def flatten_if_deep(
    outline: schemas.ChapterOutline, dag: schemas.OutlineDAG, issues: list[str], *, n_sources: int, n_blocks: int, bodies: list[str],
) -> tuple[schemas.ChapterOutline, schemas.OutlineDAG, list[str]]:
    """An over-deep prerequisite chain is flattened deterministically (a model rarely does it); the flat outline is kept when it leaves no more issues."""
    if dag.max_stage <= params.MAX_STAGE_DEPTH:
        return outline, dag, issues
    flat = domain.flatten_prerequisites(outline, dag, params.MAX_STAGE_DEPTH)
    flat_dag, flat_issues = await outline_issues(flat, n_sources = n_sources, n_blocks = n_blocks, bodies = bodies)
    if len(flat_issues) > len(issues):
        return outline, dag, issues
    logger.info(f"[outline_sdp] flattened the prerequisite chain (depth {dag.max_stage} → {flat_dag.max_stage}, {len(issues)} → {len(flat_issues)} issue(s))")
    return flat, flat_dag, flat_issues


def hard_trim(outline: schemas.ChapterOutline, dag: schemas.OutlineDAG, cap: int) -> schemas.ChapterOutline:
    """Keep the first `cap` sections in topological order (lower stage first, orphans last) and drop prerequisites that point at a dropped section."""
    by_id = {s.section_id: s for s in outline.sections}
    ordered: list[schemas.OutlineSection] = []
    for stage in sorted(dag.stages):
        ordered.extend(by_id[sid] for sid in dag.stages[stage] if sid in by_id)
    seen = {s.section_id for s in ordered}
    ordered.extend(s for s in outline.sections if s.section_id not in seen)
    kept = ordered[:cap]
    kept_ids = {s.section_id for s in kept}
    for section in kept:
        section.prerequisites = [p for p in section.prerequisites if p in kept_ids]
    return outline.model_copy(update = {"sections": kept})


async def finalize(state: graphs.pipeline.synth.outline.state.OutlineState, runtime: Runtime[Context]) -> dict:
    """The best outline (else the heuristic fallback), flattened and trimmed, written under its content hash and as the latest pointer."""
    context = runtime.context
    data = context.scratch[STEP]
    slug = state["framework_slug"]
    chapter_id = state["chapter_id"]
    sources = state["sources"]
    n_blocks = state["n_vault_hashes"]
    texts = data["texts"]
    target = state["adaptive_target"]

    best = state.get("best")
    if best is not None:
        outline = schemas.ChapterOutline.model_validate(best["outline"])
        dag = domain.derive_dag(outline.sections)
        issues = list(best["issues"])
    else:
        logger.warning(
            f"[outline_sdp] {slug}/{chapter_id}: the outliner gave no usable outline in {state.get('attempt', 0)} run(s) ({state.get('last_error')}); "
            f"emitting the heuristic fallback outline"
        )
        outline = domain.heuristic_fallback_outline(data["concat"], max_sections = target)
        dag = domain.derive_dag(outline.sections)
        issues = ["heuristic_fallback"]

    outline, dag, issues = await flatten_if_deep(outline, dag, issues, n_sources = len(sources), n_blocks = n_blocks, bodies = texts)

    # HARD-TRIM: the model ignores the soft cap signal (a chapter shipped 30 H2 at cap 12; another 20 at cap 14): trim to the adaptive cap directly.
    cap = params.max_h2_for_n_sources(len(sources), n_blocks)
    if len(outline.sections) > cap:
        n_before = len(outline.sections)
        outline = hard_trim(outline, dag, cap)
        dag = domain.derive_dag(outline.sections)
        logger.warning(f"[outline_sdp] {slug}/{chapter_id}: HARD-TRIM outline {n_before} → {len(outline.sections)} sections (adaptive_cap = {cap}, n_sources = {len(sources)})")
        domains.dd.pipeline.observability.service.record_bucket_split_overflow(framework = slug, sections_dropped = max(n_before - len(outline.sections), 0))
        # Re-validate after the trim so downstream sees the violations that remain, not the pre-trim ones.
        dag, issues = await outline_issues(outline, n_sources = len(sources), n_blocks = n_blocks, bodies = texts)
        shared.service.emit(STEP, "hard_trimmed", n_before = n_before, n_after = len(outline.sections), adaptive_cap = cap, n_sources = len(sources))

    payload = domain.serialize_outline_with_dag(outline, dag)
    payload.update({
        "framework_slug": slug,
        "chapter_id": chapter_id,
        "chapter_title": state["chapter_title"],
        "manifest_hash": state["manifest_hash"],
        "source_keys": sources,
        "n_vault_hashes": n_blocks,
        "truncated": state["truncated"],
        "n_repairs": max(state.get("attempt", 1) - 1, 0) + state.get("n_checks", 0),
        "final_violations": issues,
    })
    body = json.dumps(payload, indent = 2, ensure_ascii = False)
    await context.blobs.write(state["versioned_key"], body, content_type = "application/json")
    await context.blobs.write(state["latest_key"], body, content_type = "application/json")

    elapsed = shared.service.elapsed_ms(state["started"])
    stats = {
        "n_sections": len(outline.sections),
        "max_stage": dag.max_stage,
        "n_stages": len(dag.stages),
        "n_removed_edges": len(dag.removed_edges),
        "n_repairs": payload["n_repairs"],
        "n_violations": len(issues),
        "violations": issues,
        "n_samples": state.get("attempt", 0),
        "n_vault_hashes": n_blocks,
        "truncated": state["truncated"],
        "wall_ms": elapsed,
        "store_path": state["latest_key"],
        "versioned_path": state["versioned_key"],
        "manifest_hash": state["manifest_hash"],
        "cache_hit": False,
        "prompt_version": versions.OUTLINE_PROMPT_VERSION,
    }
    shared.service.emit(
        STEP, "done",
        n_sections = stats["n_sections"], max_stage = stats["max_stage"], n_repairs = stats["n_repairs"], n_violations = stats["n_violations"], wall_ms = elapsed,
    )
    logger.info(
        f"[outline_sdp] {slug}/{chapter_id}: {stats['n_sections']} sections, max_stage = {stats['max_stage']}, n_stages = {stats['n_stages']}, "
        f"n_repairs = {stats['n_repairs']}, violations = {len(issues)}, {elapsed} ms"
    )
    return {"outline_path": state["latest_key"], "outline_stats": stats}
