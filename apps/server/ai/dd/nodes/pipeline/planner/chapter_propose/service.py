"""chapter_propose steps — the nodes of its subgraph: prepare, a section analyst per section, the architect, finalize.

The documentation's chapters are proposed by a small team of deep agents:
- One `section_analyst` per URL directory (for a corpus of SHARD_CORPUS_MIN pages or more) sketches the chapters its section
  supports, from the page summaries only, in parallel and each with a context of its own.
- The `chapter_architect` merges the sketches into the outline. It reads the corpus as read-only files (`/corpus/`), checks the
  outline with `score_outline` (pages that match no chapter, the largest chapter's share, the distance from the target) and has
  `plan_critic` review it through two lenses before answering.
- A score gate sends the architect back, with the pages its outline left uncovered, while the outline scores below WEAK_SCORE
  (at most ARCHITECT_ROUNDS attempts); the best outline seen wins. If no attempt produced one, the deterministic fallback built
  from the structural seeds ships, so the pipeline never returns zero chapters."""
from __future__ import annotations
import domains
from . import domain, errors, params, versions
from .. import shared
from ..... import agents, backends, graphs, tools

import json
import logging
import time

from deepagents.backends import CompositeBackend, StateBackend
from langgraph.runtime import Runtime


logger = logging.getLogger(__name__)

Context = graphs.pipeline.planner.context.PlannerContext
STEP = "chapter_propose"


def section_target(n_pages: int) -> int:
    """About how many chapters a section of `n_pages` pages needs (at least one, at most what an analyst may sketch)."""
    return max(1, min(agents.pipeline.planner.params.SECTION_PROPOSALS_MAX, round(n_pages / params.PROPOSALS_DIVISOR)))


async def prepare(state: graphs.pipeline.planner.chapter_propose.state.ProposeState, runtime: Runtime[Context]) -> dict:
    context = runtime.context
    blobs = context.blobs
    slug = state.get("framework_slug")
    relevant_files = state.get("relevant_files") or state.get("raw_files") or []
    distill_ref = state.get("doc_distill_ref")
    if not slug or not relevant_files:
        return {"finished": True, "chapter_proposals_ref": None, "propose_stats": {"skipped": "no_files"}}

    started = time.monotonic()
    n = len(relevant_files)
    distillates = await domains.dd.pipeline.storage.service.load_distillates(blobs, slug) if distill_ref else {}
    manifest = domain.manifest_hash(
        slug = slug, source_keys = relevant_files, distill_ref = distill_ref,
        distill_fp = domain.fingerprint(distillates) if distillates else "",
    )
    keys = domains.dd.pipeline.storage.keys
    vkey, lkey = keys.proposals_versioned_key(slug, manifest), keys.proposals_latest_key(slug)
    if await blobs.exists(vkey) and await blobs.exists(lkey):
        try:
            cached = json.loads(await blobs.read_text(vkey))
            wall_ms = shared.service.elapsed_ms(started)
            stats = {
                "n_files": n,
                "n_proposals": len(cached.get("proposals") or []),
                "cache_hit": True,
                "wall_ms": wall_ms,
                "manifest_hash": manifest,
                "titles": [p.get("title") for p in cached.get("proposals") or []],
            }
            shared.service.emit(STEP, "done", cache_hit = True, n_proposals = stats["n_proposals"], wall_ms = wall_ms)
            return {"finished": True, "chapter_proposals_ref": lkey, "propose_stats": stats}
        except Exception:
            pass

    shared.service.emit(STEP, "start", n_files = n, distill_available = bool(distill_ref))
    # Only reached past the cache check, so a fully cached re-plan never pays the wait.
    await shared.service.settle(STEP, params.SETTLE_DELAY_S)

    bodies_all = await shared.service.read_all(blobs, relevant_files)
    bodies = {k: (b or "")[:params.BODY_CHARS_PER_DOC * 2] for k, b in zip(relevant_files, bodies_all)}
    seeds = domain.extract_structural_seeds(source_keys = relevant_files, bodies_by_key = bodies)

    # The docs' own structure (parent pages in navigation order, with page counts): best-effort, "" without a manifest or multi-page families.
    entries: list[dict] = []
    source_structure = ""
    try:
        entries = ((await domains.dd.ingestion.storage.service.read_framework_manifest(blobs, slug)) or {}).get("entries") or []
        wanted = set(relevant_files)
        source_structure = domain.build_source_structure([e for e in entries if e.get("key") in wanted])
    except Exception as error:
        logger.info(f"[chapter_propose] {slug}: source structure unavailable ({type(error).__name__})")

    target = domain.target_chapters_for_n_docs(n)
    shards = domain.shard_sections(entries, relevant_files)
    titles = {e["key"]: e.get("title") or "" for e in entries if e.get("key")}
    context.scratch[STEP] = {
        "slug": slug,
        "distillates": distillates,
        "bodies": bodies,
        "seeds": seeds,
        "source_structure": source_structure,
        "titles": titles,
        "doc_texts": [domain.doc_match_text(k, distillates.get(k), bodies.get(k, "")) for k in relevant_files],
    }
    shared.service.emit(
        STEP, "sampling",
        n_samples = max(1, len(shards)), n_sections = len(shards), target_chapters = target, n_docs = n,
        n_heading_seeds = len(seeds.get("headings") or []), n_namespace_seeds = len(seeds.get("namespaces") or []),
    )
    logger.info(f"[chapter_propose] {slug}: {n} pages, target {target} chapters, {len(shards)} section(s) for the analysts")
    return {
        "finished": False,
        "manifest_hash": manifest,
        "shards": shards,
        "target": target,
        "attempt": 0,
        "architect_errors": [],
        "started": started,
    }


async def analyze_section(state: graphs.pipeline.planner.chapter_propose.state.SectionInput, runtime: Runtime[Context]) -> dict:
    """One section analyst: the chapters its section supports, from the page summaries. A failed analyst costs its section's sketch only."""
    context = runtime.context
    data = context.scratch[STEP]
    keys = state["keys"]
    pages = []
    for key in keys:
        distillate = data["distillates"].get(key) or {}
        pages.append({
            "key": key,
            "title": data["titles"].get(key) or key.rsplit("/", 1)[-1],
            "summary": distillate.get("summary") or "",
            "key_terms": distillate.get("key_terms") or [],
            "role": distillate.get("role") or "",
        })
    seeds = domain.extract_structural_seeds(source_keys = keys, bodies_by_key = data["bodies"])
    request = json.dumps({
        "task": "Propose the chapters this one section of the documentation supports.",
        "framework": state["framework_slug"], "section": state["section"], "n_pages": len(keys),
        "target": state["target"], "pages": pages, "seeds": seeds,
    }, ensure_ascii = False)
    try:
        answer = await agents.service.ask(
            context.agents.section_analyst(),
            agents.pipeline.planner.schemas.SectionProposals,
            request,
            attempts = params.ANALYST_ATTEMPTS,
            wait_base_s = params.ANALYST_RETRY_WAIT_S,
        )
    except Exception as error:
        logger.warning(f"[chapter_propose] section {state['section']!r}: analyst failed ({type(error).__name__}: {str(error)[:160]})")
        return {"sketches": [{"section": state["section"], "n_pages": len(keys), "proposals": None, "error": f"{type(error).__name__}: {str(error)[:120]}"}]}
    return {"sketches": [{
        "section": state["section"], "n_pages": len(keys), "proposals": [p.model_dump() for p in answer.proposals],
    }]}


def _feedback(outline: dict, labels: list[str], target: int) -> str:
    """What the architect is told when its outline scored below the bar: the numbers, and the pages no chapter covers."""
    scored = outline["scored"]
    lines = [
        f"Your previous outline scored {scored['score']}: {scored['unmatched_pct']}% of the pages match no chapter, the largest chapter "
        f"holds {scored['largest_share_pct']}% of the pages, and it has {scored['n']} chapters against a target of {target}.",
    ]
    unmatched = [labels[i] for i in scored["unmatched"][:params.FEEDBACK_MAX_PAGES]]
    if unmatched:
        lines.append("Pages that match no chapter (read a few, then cover their topic): " + ", ".join(unmatched) + ".")
    return " ".join(lines)


async def architect(state: graphs.pipeline.planner.chapter_propose.state.ProposeState, runtime: Runtime[Context]) -> dict:
    """One architect attempt: the corpus as read-only files, the sketches as a working file, the score tool bound to this run's pages."""
    context = runtime.context
    blobs = context.blobs
    data = context.scratch[STEP]
    slug = data["slug"]
    relevant_files = state.get("relevant_files") or state.get("raw_files") or []
    target = state["target"]
    attempt = (state.get("attempt") or 0) + 1
    sketches = state.get("sketches") or []

    docs = []
    for idx, key in enumerate(relevant_files):
        distillate = data["distillates"].get(key) or {}
        docs.append(backends.corpus.entities.CorpusDoc(
            idx = idx,
            path = backends.corpus.domain.doc_path(idx, key),
            key = key,
            title = data["titles"].get(key) or key.rsplit("/", 1)[-1],
            role = distillate.get("role") or "",
            summary = distillate.get("summary") or "",
            terms = tuple(distillate.get("key_terms") or []),
        ))
    labels = [backends.corpus.params.ROUTE.rstrip("/") + doc.path for doc in docs]
    backend = CompositeBackend(
        default = StateBackend(),
        routes = {backends.corpus.params.ROUTE: backends.corpus.service.CorpusBackend(docs, blobs.read_text)},
    )
    score = lambda outline: domain.score_outline(outline, data["doc_texts"], target)
    role = context.agents.chapter_architect(
        backend = backend,
        tools = [tools.pipeline.planner.outline.service.score_outline_tool(score, labels, target)],
    )

    seeds = data["seeds"]
    structure = (
        "SOURCE STRUCTURE (the documentation's own navigation order; pages that share a parent page are ONE family; "
        "site sections are URL directories):\n" + (data["source_structure"] or "(none: a flat site or a single-URL dump)")
        + "\n\nRECURRING HEADINGS (appear in at least 2 pages): " + (", ".join(seeds.get("headings") or []) or "(none)")
        + "\nFILE-TREE NAMESPACES: " + (", ".join(seeds.get("namespaces") or []) or "(none)") + "\n"
    )
    files = {
        "/work/structure.md": agents.service.file(structure),
        "/work/sketches.json": agents.service.file(json.dumps(sketches, ensure_ascii = False, indent = 2)),
    }
    previous = [o for o in state.get("outlines") or []]
    feedback = _feedback(max(previous, key = lambda o: o["scored"]["score"]), labels, target) if previous else ""
    request = json.dumps({
        "task": "Design the final chapter outline of this framework's documentation.",
        "framework": slug,
        "n_docs": len(relevant_files),
        "target": {"target": target, "min": params.PROPOSALS_MIN, "max": params.PROPOSALS_MAX},
        "feedback": feedback,
        "sketches": [
            {"section": s["section"], "n_pages": s["n_pages"], "chapters": [p["title"] for p in s["proposals"] or []]}
            for s in sketches
        ],
    }, ensure_ascii = False)

    errors_so_far = list(state.get("architect_errors") or [])
    try:
        answer = await agents.service.ask(
            role,
            agents.pipeline.planner.schemas.ChapterProposalList,
            request,
            attempts = params.ARCHITECT_ATTEMPTS,
            wait_base_s = params.ARCHITECT_RETRY_WAIT_S,
            files = files,
        )
    except Exception as error:
        message = f"attempt {attempt}: {type(error).__name__}: {str(error)[:160]}"
        logger.warning(f"[chapter_propose] {slug}: architect failed ({message})")
        return {"attempt": attempt, "architect_errors": errors_so_far + [message]}
    proposals = [p.model_dump() for p in answer.proposals]
    scored = domain.score_outline(proposals, data["doc_texts"], target)
    logger.info(
        f"[chapter_propose] {slug}: architect attempt {attempt}: {scored['n']} chapters, score {scored['score']} "
        f"(unmatched {scored['unmatched_pct']}%, largest chapter {scored['largest_share_pct']}%)"
    )
    return {"attempt": attempt, "outlines": [{"attempt": attempt, "proposals": proposals, "scored": scored}]}


async def finalize(state: graphs.pipeline.planner.chapter_propose.state.ProposeState, runtime: Runtime[Context]) -> dict:
    context = runtime.context
    blobs = context.blobs
    data = context.scratch[STEP]
    slug = data["slug"]
    relevant_files = state.get("relevant_files") or state.get("raw_files") or []
    n = len(relevant_files)
    manifest = state["manifest_hash"]
    target = state["target"]
    outlines = state.get("outlines") or []
    sketches = state.get("sketches") or []

    fallback_used = not outlines
    if outlines:
        best = max(outlines, key = lambda o: o["scored"]["score"])
        proposals, scored = best["proposals"], best["scored"]
    else:
        # No attempt produced an outline: never return zero chapters (a silent failure downstream); build them from the structural seeds.
        logger.warning(f"[chapter_propose] {slug}: no architect attempt produced an outline ({state.get('architect_errors')}) — deterministic fallback ({target} chapters)")
        proposals = [p.model_dump() for p in domain.build_fallback_proposals(slug, data["seeds"], target, n).proposals]
        scored = domain.score_outline(proposals, data["doc_texts"], target)

    keys = domains.dd.pipeline.storage.keys
    lkey = keys.proposals_latest_key(slug)
    candidate_scores = [{k: v for k, v in o["scored"].items() if k not in {"unmatched", "chapter_sizes"}} for o in outlines]
    await domains.dd.pipeline.storage.service.write_blob(
        blobs,
        keys.proposals_versioned_key(slug, manifest),
        lkey,
        {
            "prompt_version":   versions.PROMPT_VERSION,
            "framework_slug":   slug,
            "manifest_hash":    manifest,
            "n_samples_valid":  0 if fallback_used else 1,
            "n_samples_total":  1,
            "sample_failures":  (state.get("architect_errors") or [])[:20],
            "sketches":         [{"section": s["section"], "n_pages": s["n_pages"], "ok": s["proposals"] is not None} for s in sketches],
            "source_structure": data["source_structure"],
            "chosen_idx":       0,
            "candidate_scores": candidate_scores,
            "fallback_used":    fallback_used,
            "seeds":            data["seeds"],
            "proposals":        proposals,
        },
    )
    wall_ms = shared.service.elapsed_ms(state.get("started") or time.monotonic())
    titles = [p["title"] for p in proposals]
    stats = {
        "n_files": n,
        "n_proposals": len(proposals),
        "n_samples_valid": 0 if fallback_used else 1,
        "n_samples_total": 1,
        "n_sections": len(state.get("shards") or []),
        "n_sketches_valid": sum(1 for s in sketches if s["proposals"] is not None),
        "architect_rounds": state.get("attempt") or 0,
        "score": scored["score"],
        "unmatched_pct": scored["unmatched_pct"],
        "largest_share_pct": scored["largest_share_pct"],
        "sample_failures": (state.get("architect_errors") or [])[:20],
        "has_source_structure": bool(data["source_structure"]),
        "chosen_idx": 0,
        "candidate_scores": candidate_scores,
        "cache_hit": False,
        "wall_ms": wall_ms,
        "manifest_hash": manifest,
        "titles": titles,
        "fallback_used": fallback_used,
    }
    shared.service.emit(STEP, "done", cache_hit = False, n_proposals = len(proposals), wall_ms = wall_ms, titles = titles)

    # DD_PLANNER_ABORT_ON_DEGRADE: a fallback on a large corpus is a near-certain predictor of a throwaway plan (generic titles, a
    # lopsided catch-all bucket). The artifact is persisted above, so /resume still works once the pool recovers.
    if fallback_used and params.ABORT_ON_DEGRADE_ENABLED and n >= params.ABORT_MIN_DOCS:
        raise errors.PlannerDegradedAbort(
            f"chapter_propose fell back to deterministic seed titles on {n} docs — aborting before chapter_assign "
            f"(DD_PLANNER_ABORT_ON_DEGRADE). Retry when the LLM pool recovers; the proposals artifact is saved."
        )
    return {"chapter_proposals_ref": lkey, "propose_stats": stats}
