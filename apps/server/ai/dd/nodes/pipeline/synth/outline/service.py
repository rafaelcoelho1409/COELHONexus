"""outline_sdp steps — the nodes of its subgraph: prepare, draft (the chapter_outliner agent), finalize.

The outliner reads an index of the chapter's sources (and any page it wants in full) and answers the whole outline; a `validate_outline` tool runs the
stage's own structural rules and a scope-overlap check on every draft, so the outline is repaired inside the agent's run. What the tool still finds wrong in
the best answer is the brief of one more run. The deterministic passes that always followed (flatten a deep prerequisite chain, trim to the section cap)
and the heuristic fallback outline are unchanged."""
from __future__ import annotations
import domains
from . import domain, keys, params, schemas, versions
from ..... import agents, backends, graphs, skills, tools

import json
import logging
import time
import hashlib

from langgraph.runtime import Runtime
from numpy import array, float32, linalg


logger = logging.getLogger(__name__)

Context = graphs.pipeline.synth.entities.SynthContext


async def semantic_h2_duplicates(outline: schemas.ChapterOutline) -> list[str]:
    """Issues for scope-duplicate H2 pairs (embedding cosine OR lexical overlap). Fail-soft: the lexical pass still runs when the embedder is unavailable."""
    sections = outline.sections
    if len(sections) <= 1:
        return []
    words = [domain.scope_words(f"{s.heading} {s.description}") for s in sections]
    sim = None
    try:
        embeddings, _model = await domains.settings.embeddings.service.embed_texts_async([f"{s.heading}\n{s.description}" for s in sections])
        embs = array(embeddings, dtype = float32)
        norms = linalg.norm(embs, axis = 1, keepdims = True)
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


async def prepare(state: graphs.pipeline.synth.outline.states.OutlineState, runtime: Runtime[Context]) -> dict:
    context = runtime.context
    blobs = context.blobs
    slug = state.get("framework_slug")
    chapter_id = state.get("chapter_id")
    started = time.monotonic()
    if not slug or not chapter_id:
        return {"finished": True, **graphs.pipeline.synth.service.failure(
            "outline_path", "outline_stats", "no_slug_or_chapter_id", "framework_slug or chapter_id missing from SynthState", started,
        )}

    plan_key = domains.dd.pipeline.storage.keys.plan_latest_key(slug)
    plan = await domains.dd.pipeline.storage.service.load_json(blobs, plan_key)
    if plan is None:
        return {"finished": True, **graphs.pipeline.synth.service.failure(
            "outline_path", "outline_stats", "plan_not_found", f"planner plan {plan_key!r} not in MinIO (or unreadable); run planner first", started,
            plan_key = plan_key,
        )}
    chapter = domain.find_chapter(plan, chapter_id)
    if chapter is None:
        return {"finished": True, **graphs.pipeline.synth.service.failure(
            "outline_path", "outline_stats", "chapter_not_in_plan", f"chapter {chapter_id!r} not in plan-latest.json", started,
            known_ids = [c.get("id") for c in (plan.get("chapters") or [])],
        )}
    chapter_title = chapter.get("title") or chapter_id
    chapter_brief = [chapter.get("description") or ""]
    if chapter.get("objective"):
        chapter_brief.append(f"Learning objective: {chapter['objective']}")
    if chapter.get("key_concepts"):
        chapter_brief.append("Core concepts: " + "; ".join(str(value) for value in chapter["key_concepts"] if value))
    if chapter.get("prerequisite_titles"):
        chapter_brief.append("Prerequisites: " + "; ".join(str(value) for value in chapter["prerequisite_titles"] if value))
    elif chapter.get("prerequisites"):
        chapter_brief.append("Prerequisite chapter ids: " + "; ".join(str(value) for value in chapter["prerequisites"] if value))
    chapter_description = "\n".join(value for value in chapter_brief if value)
    sources = sorted(chapter.get("sources") or [])
    if not sources:
        return {"finished": True, **graphs.pipeline.synth.service.failure(
            "outline_path", "outline_stats", "no_sources", f"chapter {chapter_id!r} has zero sources in plan", started,
        )}

    graphs.service.emit(params.STEP, "start", chapter_id = chapter_id, chapter_title = chapter_title, n_sources = len(sources))

    # Each source is already corpus_normalized and vault-built by ingestion: just read them.
    bodies = list(await blobs.read_many(sources))
    pairs = [(key, body) for key, body in zip(sources, bodies) if isinstance(body, str) and body.strip()]
    if len(pairs) != len(sources):
        available = {key for key, _ in pairs}
        missing_sources = [key for key in sources if key not in available]
        return {"finished": True, **graphs.pipeline.synth.service.failure(
            "outline_path", "outline_stats", "source_bodies_incomplete", "one or more planned source bodies are missing or empty; refusing to synthesize a partial chapter", started,
            missing_sources = missing_sources,
        )}
    texts = [body for _, body in pairs]
    source_digests = {key: hashlib.sha256(body.encode("utf-8")).hexdigest() for key, body in pairs}
    sources_concat_md, truncated = domain.concat_sources(texts)
    n_vault_hashes = domain.count_code_blocks(texts)
    graphs.service.emit(
        params.STEP, "sources_loaded",
        n_sources = len(sources), n_bodies = len(pairs), bytes = len(sources_concat_md), truncated = truncated, n_vault_hashes = n_vault_hashes,
    )

    tutorial = graphs.pipeline.synth.params.tutorial_mode()
    manifest_hash = domain.compute_manifest_hash(
        sources = sources, sources_bytes = len(sources_concat_md), source_digests = source_digests,
        chapter_title = chapter_title, chapter_description = chapter_description, tutorial = tutorial,
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
                elapsed = graphs.service.elapsed_ms(started)
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
                graphs.service.emit(params.STEP, "done", n_sections = stats["n_sections"], max_stage = stats["max_stage"], wall_ms = elapsed, cache_hit = True)
                logger.info(f"[outline_sdp] {slug}/{chapter_id}: CACHE HIT — {stats['n_sections']} sections, max_stage = {stats['max_stage']}, {elapsed} ms")
                return {"finished": True, "outline_path": latest_key, "outline_stats": stats}
        except Exception as error:
            logger.warning(f"[outline_sdp] {slug}/{chapter_id}: cached blob {versioned_key!r} unreadable ({type(error).__name__}: {error}); recomputing")

    context.scratch[params.STEP] = {"pairs": pairs, "texts": texts, "concat": sources_concat_md}
    if tutorial:
        # What the chapter has to teach: the best code of its pages, each example once, in the order the pages introduce them.
        context.scratch[params.STEP]["ledger"] = await domains.dd.pipeline.ledger.service.build(blobs, slug, [key for key, _ in pairs])
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


async def draft(state: graphs.pipeline.synth.outline.states.OutlineState, runtime: Runtime[Context]) -> dict:
    """One outliner run. Returns the new best outline (by `accept_repair`) and what is still wrong with it."""
    context = runtime.context
    data = context.scratch[params.STEP]
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
    ledger = data.get("ledger")
    role = context.agents.chapter_outliner(
        backend = backends.files.service.mounted(files, route),
        tools = [tools.pipeline.synth.check.tool.validate_outline_tool(check), *(tools.pipeline.synth.ledger.tool.ledger_tools(ledger) if ledger else [])],
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
    if ledger:
        request["tutorial"] = {
            "goal": "a concise complete tutorial: group related best code examples into focused steps, preserve their learning order, and end in an application that combines them (added later; do not plan it)",
            "best_code": domains.dd.pipeline.ledger.domain.summary(ledger),
            "n_best_code": len(ledger.must_teach),
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
            files = skills.service.files_for("chapter_outliner"),
            check = lambda a: bool(a.sections),
        )
    except Exception as error:
        logger.warning(f"[outline_sdp] {state['framework_slug']}/{state['chapter_id']}: outliner run {attempt} failed: {type(error).__name__}: {str(error)[:200]}")
        graphs.service.emit(params.STEP, "draft_done", attempt = attempt, ok = False, error = f"{type(error).__name__}: {str(error)[:120]}")
        return {
            "attempt": attempt,
            "half": half or domain.is_context_overflow_error(error),
            "last_error": f"{type(error).__name__}: {str(error)[:200]}",
            "n_checks": n_checks + counts["checks"],
        }

    outline, error = domain.try_parse_outline(answer.model_dump())
    if outline is None:
        logger.info(f"[outline_sdp] {state['framework_slug']}/{state['chapter_id']}: outliner run {attempt} answered an invalid outline — {error}")
        graphs.service.emit(params.STEP, "draft_done", attempt = attempt, ok = False, error = "invalid_outline")
        return {
            "attempt": attempt,
            "feedback": [f"Your previous answer was rejected: {error}"],
            "last_error": f"pydantic: {error}",
            "n_checks": n_checks + counts["checks"],
        }

    _, issues = await outline_issues(outline, n_sources = n_sources, n_blocks = n_blocks, bodies = texts)
    candidate = {"outline": outline.model_dump(), "issues": issues}
    accepted = best is None or domain.accept_repair(best["issues"], issues, params.REPAIR_FLOOR_SLACK)
    graphs.service.emit(
        params.STEP, "draft_done",
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
    """Flatten an over-deep prerequisite chain and keep the refreshed validation result."""
    if dag.max_stage <= params.MAX_STAGE_DEPTH:
        return outline, dag, issues
    fallback_issue = "heuristic_fallback" in issues
    flat = domain.flatten_prerequisites(outline, dag, params.MAX_STAGE_DEPTH)
    flat_dag, flat_issues = await outline_issues(flat, n_sources = n_sources, n_blocks = n_blocks, bodies = bodies)
    if flat_dag.max_stage >= dag.max_stage:
        return outline, dag, issues
    if fallback_issue:
        flat_issues.insert(0, "heuristic_fallback")
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


async def finalize(state: graphs.pipeline.synth.outline.states.OutlineState, runtime: Runtime[Context]) -> dict:
    """The best outline (else the heuristic fallback), flattened and trimmed, written under its content hash and as the latest pointer."""
    context = runtime.context
    data = context.scratch[params.STEP]
    slug = state["framework_slug"]
    chapter_id = state["chapter_id"]
    sources = state["sources"]
    n_blocks = state["n_vault_hashes"]
    texts = data["texts"]
    target = state["adaptive_target"]
    tutorial = graphs.pipeline.synth.params.tutorial_mode()

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

    cap = params.max_h2_for_n_sources(len(sources), n_blocks)
    if tutorial and data.get("ledger") is not None:
        previous_section_count = len(outline.sections)
        fallback_issue = "heuristic_fallback" in issues
        outline = assign_tutorial_steps(outline, data["ledger"], max_sections = cap)
        dag = domain.derive_dag(outline.sections)
        if len(outline.sections) != previous_section_count:
            dag, issues = await outline_issues(
                outline, n_sources = len(sources), n_blocks = n_blocks, bodies = texts,
            )
            if fallback_issue:
                issues.insert(0, "heuristic_fallback")
        logger.info(f"[outline_sdp] {slug}/{chapter_id}: assigned {sum(len(section.code_refs) for section in outline.sections)} canonical code example(s) to tutorial steps")

    outline, dag, issues = await flatten_if_deep(outline, dag, issues, n_sources = len(sources), n_blocks = n_blocks, bodies = texts)

    # HARD-TRIM: the model ignores the soft cap signal (a chapter shipped 30 H2 at cap 12; another 20 at cap 14): trim to the adaptive cap directly.
    if len(outline.sections) > cap:
        n_before = len(outline.sections)
        fallback_issue = "heuristic_fallback" in issues
        outline = hard_trim(outline, dag, cap)
        if tutorial and data.get("ledger") is not None:
            # The cap can remove model-proposed sections; reassign every canonical
            # code family to the retained sections before validating/persisting.
            outline = assign_tutorial_steps(outline, data["ledger"], max_sections = cap)
        dag = domain.derive_dag(outline.sections)
        logger.warning(f"[outline_sdp] {slug}/{chapter_id}: HARD-TRIM outline {n_before} → {len(outline.sections)} sections (adaptive_cap = {cap}, n_sources = {len(sources)})")
        domains.dd.pipeline.observability.service.record_bucket_split_overflow(framework = slug, sections_dropped = max(n_before - len(outline.sections), 0))
        # Re-validate after the trim so downstream sees the violations that remain, not the pre-trim ones.
        dag, issues = await outline_issues(outline, n_sources = len(sources), n_blocks = n_blocks, bodies = texts)
        if fallback_issue:
            issues.insert(0, "heuristic_fallback")
        graphs.service.emit(params.STEP, "hard_trimmed", n_before = n_before, n_after = len(outline.sections), adaptive_cap = cap, n_sources = len(sources))

    payload = domain.serialize_outline_with_dag(outline, dag)
    payload["prompt_version"] = versions.prompt_version(tutorial)
    payload["schema_version"] = versions.schema_version(tutorial)
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

    elapsed = graphs.service.elapsed_ms(state["started"])
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
        "prompt_version": versions.prompt_version(tutorial),
    }
    graphs.service.emit(
        params.STEP, "done",
        n_sections = stats["n_sections"], max_stage = stats["max_stage"], n_repairs = stats["n_repairs"], n_violations = stats["n_violations"], wall_ms = elapsed,
    )
    logger.info(
        f"[outline_sdp] {slug}/{chapter_id}: {stats['n_sections']} sections, max_stage = {stats['max_stage']}, n_stages = {stats['n_stages']}, "
        f"n_repairs = {stats['n_repairs']}, violations = {len(issues)}, {elapsed} ms"
    )
    return {"outline_path": state["latest_key"], "outline_stats": stats}


def _step_heading(block, index: int) -> str:
    """A readable fallback heading when the outliner did not create enough code steps."""
    name = next(iter(block.defines), "")
    words = [word for word in name.replace("_", " ").replace("-", " ").split() if word]
    if not words:
        words = [block.lang.title() if block.lang else "Code", "Example"]
    if len(words) == 1:
        words.append("Example")
    return " ".join(words[:8]) or f"Code Example {index}"


def assign_tutorial_steps(
    outline: schemas.ChapterOutline,
    ledger,
    *,
    max_sections: int | None = None,
) -> schemas.ChapterOutline:
    """Group every canonical code example into a focused section, exactly once."""
    hashes = [digest for digest in ledger.must_teach if digest in ledger.blocks]
    if not hashes:
        return outline

    sections = [section.model_copy(deep = True) for section in outline.sections]
    cap = min(params.SECTIONS_MAX, max(params.SECTIONS_MIN, max_sections or len(sections) or params.SECTIONS_MIN))
    required = (len(hashes) + params.TUTORIAL_CODE_EXAMPLES_PER_SECTION - 1) // params.TUTORIAL_CODE_EXAMPLES_PER_SECTION
    desired = min(cap, max(min(len(sections), cap), required, params.SECTIONS_MIN))
    if len(sections) > cap:
        sections = hard_trim(outline.model_copy(update = {"sections": sections}), domain.derive_dag(sections), cap).sections

    used_ids = {section.section_id for section in sections}
    while len(sections) < desired:
        index = len(sections) + 1
        digest = hashes[min(index - 1, len(hashes) - 1)]
        block = ledger.blocks[digest]
        heading = _step_heading(block, index)
        if any(section.heading.casefold() == heading.casefold() for section in sections):
            heading_words = heading.split()[:6]
            heading = " ".join([*heading_words, "Patterns"])
        suffix = 2
        while any(section.heading.casefold() == heading.casefold() for section in sections):
            heading = f"Code Pattern {index} {suffix}"
            suffix += 1
        section_number = 1
        while f"s{section_number}" in used_ids:
            section_number += 1
        section_id = f"s{section_number}"
        used_ids.add(section_id)
        sections.append(schemas.OutlineSection(
            section_id = section_id,
            heading = heading,
            description = f"Teach the related {block.lang or 'code'} examples for {heading}, using their source explanations and exact code references.",
            prerequisites = [],
            needs_code = True,
            code_refs = [],
        ))

    # Keep any useful model assignments as semantic anchors. Assign every
    # remaining example to the section with the closest page/topic evidence,
    # with a balancing term so one broad page cannot create a huge code bank.
    hash_order = {digest: index for index, digest in enumerate(hashes)}
    assignments: list[list[str]] = [[] for _ in sections]
    claimed: set[str] = set()
    seed_pages: list[set[str]] = [set() for _ in sections]
    section_terms = [
        domain.scope_words(f"{section.heading} {section.description}")
        for section in sections
    ]
    for index, section in enumerate(sections):
        for digest in section.code_refs:
            if (
                digest in hash_order
                and digest not in claimed
                and len(assignments[index]) < params.TUTORIAL_CODE_EXAMPLES_PER_SECTION
            ):
                assignments[index].append(digest)
                claimed.add(digest)
                seed_pages[index].update(ledger.blocks[digest].pages)

    def block_terms(digest: str) -> set[str]:
        block = ledger.blocks[digest]
        page_names = " ".join(page.rsplit("/", 1)[-1].replace("-", " ").replace("_", " ") for page in block.pages)
        names = " ".join([*block.defines, *block.imports, page_names]).replace("_", " ").replace(".", " ")
        return domain.scope_words(names)

    for digest in hashes:
        if digest in claimed:
            continue
        block = ledger.blocks[digest]
        pages = set(block.pages)
        terms = block_terms(digest)
        scores: dict[int, tuple[float, int, int]] = {}
        for index, assigned in enumerate(assignments):
            if len(assigned) >= params.TUTORIAL_CODE_EXAMPLES_PER_SECTION:
                continue
            shared_pages = len(pages & seed_pages[index]) / max(len(pages), 1)
            overlap = len(terms & section_terms[index]) / max(len(terms | section_terms[index]), 1)
            balance_cost = len(assigned) / max(1, (len(hashes) / len(sections)))
            scores[index] = (4.0 * shared_pages + overlap - 0.25 * balance_cost, -len(assigned), -index)
        eligible = list(scores)
        if not eligible:
            # The section-count floor normally guarantees room; keep a safe
            # fallback if a caller supplies a lower cap than the code bank needs.
            chosen = min(range(len(sections)), key = lambda index: (len(assignments[index]), index))
        else:
            chosen = max(eligible, key = lambda index: scores[index])
        assignments[chosen].append(digest)
        seed_pages[chosen].update(pages)
        claimed.add(digest)

    for index, section in enumerate(sections):
        section.code_refs = sorted(assignments[index], key = hash_order.__getitem__)
        section.needs_code = bool(section.code_refs)
    return outline.model_copy(update = {"sections": sections})
