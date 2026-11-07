"""mgsr_replan steps — the nodes of its subgraph: prepare, review (the chapter_reviewer agent), finalize.

A chapter that passed its checklist is not reviewed. A chapter that failed is read by a reviewer agent that opens the failing sections with `inspect_section`
and names the ones to write again, with what to fix in each (or says no rewrite would help). The decision is written to the blob `sawc_write` reads on the
next iteration; the CoRefine route (`route_after_mgsr` in this stage's `node.py`) still decides from the checklist alone whether there is a next iteration."""
from __future__ import annotations
import domains
from . import domain, keys, params, schemas, versions
from ..... import agents, graphs, skills, tools

import json
import logging
import time

from langgraph.runtime import Runtime


logger = logging.getLogger(__name__)

Context = graphs.pipeline.synth.entities.SynthContext


async def prepare(state: graphs.pipeline.synth.mgsr.states.MgsrState, runtime: Runtime[Context]) -> dict:
    context = runtime.context
    blobs = context.blobs
    slug = state.get("framework_slug")
    chapter_id = state.get("chapter_id")
    started = time.monotonic()
    if not slug or not chapter_id:
        return {"finished": True, **graphs.pipeline.synth.service.failure(
            "mgsr_path", "mgsr_stats", "no_slug_or_chapter_id", "framework_slug or chapter_id missing from SynthState", started,
        )}

    checklist_key = keys.checklist_latest_key(slug, chapter_id)
    outline_key = keys.outline_latest_key(slug, chapter_id)
    if not await blobs.exists(checklist_key):
        return {"finished": True, **graphs.pipeline.synth.service.failure(
            "mgsr_path", "mgsr_stats", "checklist_not_found", f"checklist {checklist_key!r} not in MinIO — run checklist_eval first", started, checklist_key = checklist_key,
        )}
    if not await blobs.exists(outline_key):
        return {"finished": True, **graphs.pipeline.synth.service.failure(
            "mgsr_path", "mgsr_stats", "outline_not_found", f"outline {outline_key!r} not in MinIO — run outline_sdp first", started, outline_key = outline_key,
        )}
    checklist = await domains.dd.pipeline.storage.service.load_json(blobs, checklist_key)
    outline_payload = await domains.dd.pipeline.storage.service.load_json(blobs, outline_key)
    if checklist is None or outline_payload is None:
        return {"finished": True, **graphs.pipeline.synth.service.failure("mgsr_path", "mgsr_stats", "inputs_unreadable", "checklist/outline unreadable", started)}

    chapter_title = checklist.get("chapter_title") or outline_payload.get("chapter_title") or chapter_id
    pass_rate = float(checklist.get("pass_rate", 0.0))
    chapter_passed = bool(checklist.get("chapter_passed", False))
    domains.dd.pipeline.observability.service.record_score("mgsr.pass_rate", pass_rate, comment = f"chapter={chapter_id}")
    domains.dd.pipeline.observability.service.record_score("mgsr.chapter_passed", 1.0 if chapter_passed else 0.0, comment = f"chapter={chapter_id}")
    failed_names = [c.get("name", "?") for c in (checklist.get("criteria") or []) if not c.get("passed", False)]
    n_failed = len(checklist.get("failed_feedback") or [])
    checklist_manifest_hash = checklist.get("checklist_manifest_hash") or ""
    outline_manifest_hash = outline_payload.get("manifest_hash") or ""
    graphs.service.emit(
        params.STEP, "start", chapter_id = chapter_id, chapter_title = chapter_title, pass_rate = pass_rate, chapter_passed = chapter_passed, n_failed_criteria = n_failed,
    )

    manifest_hash = domain.compute_manifest_hash(checklist_manifest_hash = checklist_manifest_hash, outline_manifest_hash = outline_manifest_hash)
    versioned_key = keys.versioned_blob_key(slug, chapter_id, manifest_hash)
    latest_key = keys.latest_blob_key(slug, chapter_id)
    if await blobs.exists(versioned_key) and await blobs.exists(latest_key):
        try:
            cached = json.loads(await blobs.read_text(versioned_key))
            decision = (cached or {}).get("decision") or {}
            elapsed = graphs.service.elapsed_ms(started)
            stats = {
                "halt": decision.get("halt", True),
                "halt_reason": decision.get("halt_reason", "?"),
                "confidence": decision.get("confidence", 0.0),
                "n_revisions": len(decision.get("revisions") or []),
                "chapter_passed": cached.get("checklist_chapter_passed", False),
                "pass_rate": cached.get("checklist_pass_rate", 0.0),
                "wall_ms": elapsed,
                "store_path": latest_key,
                "versioned_path": versioned_key,
                "manifest_hash": manifest_hash,
                "cache_hit": True,
                "prompt_version": cached.get("prompt_version"),
            }
            graphs.service.emit(
                params.STEP, "done", halt = stats["halt"], halt_reason = stats["halt_reason"], n_revisions = stats["n_revisions"], confidence = stats["confidence"],
                wall_ms = elapsed, cache_hit = True,
            )
            logger.info(f"[mgsr_replan] {slug}/{chapter_id}: CACHE HIT — halt={stats['halt']} reason={stats['halt_reason']!r} revisions={stats['n_revisions']} {elapsed} ms")
            return {"finished": True, "mgsr_path": latest_key, "mgsr_stats": stats}
        except Exception as error:
            logger.warning(f"[mgsr_replan] {slug}/{chapter_id}: cached blob {versioned_key!r} unreadable ({type(error).__name__}: {error}); recomputing")

    trivial = domain.is_trivial_pass(checklist)
    sawc = await domains.dd.pipeline.storage.service.load_json(blobs, keys.sawc_latest_key(slug, chapter_id)) if not trivial else None
    context.scratch[params.STEP] = {"checklist": checklist, "sections": (sawc or {}).get("sections") or [], "outline_sections": (outline_payload.get("outline") or {}).get("sections") or []}
    result = {
        "finished": False,
        "trivial": trivial,
        "started": started,
        "manifest_hash": manifest_hash,
        "versioned_key": versioned_key,
        "latest_key": latest_key,
        "checklist_manifest_hash": checklist_manifest_hash,
        "outline_manifest_hash": outline_manifest_hash,
        "chapter_title": chapter_title,
        "pass_rate": pass_rate,
        "chapter_passed": chapter_passed,
        "failed_names": failed_names,
        "n_failed": n_failed,
    }
    if trivial:
        graphs.service.emit(params.STEP, "trivial_pass", pass_rate = pass_rate)
        result["decision"] = domain.build_trivial_pass_decision(pass_rate).model_dump()
    return result


async def review(state: graphs.pipeline.synth.mgsr.states.MgsrState, runtime: Runtime[Context]) -> dict:
    """The reviewer reads the failing sections and names the ones to write again; if it cannot answer, the loop rewrites the whole chapter."""
    context = runtime.context
    data = context.scratch[params.STEP]
    sections = data["sections"]
    by_id = {s.get("section_id"): s for s in sections}
    failed = [{"name": c.get("name"), "feedback": c.get("feedback")} for c in data["checklist"].get("criteria") or [] if not c.get("passed", False)]

    def read(section_id: str) -> str | None:
        section = by_id.get(section_id)
        return None if section is None else domain.section_text(section)

    role = context.agents.chapter_reviewer(tools = [tools.pipeline.synth.view.tool.inspect_section_tool(read)])
    request = {
        "framework": state["framework_slug"],
        "chapter_id": state["chapter_id"],
        "chapter_title": state["chapter_title"],
        "iteration": int(state.get("refine_iter") or 1),
        "pass_rate": state["pass_rate"],
        "failed": failed,
        "sections": domain.section_index(sections),
    }
    t0 = time.monotonic()
    try:
        answer = await agents.service.ask(
            role, agents.pipeline.synth.schemas.RevisionAnswer, json.dumps(request, ensure_ascii = False),
            attempts = params.ASK_ATTEMPTS, wait_base_s = params.RETRY_WAIT_S,
            files = skills.service.files_for("chapter_reviewer"),
        )
    except Exception as error:
        logger.warning(f"[mgsr_replan] {state['framework_slug']}/{state['chapter_id']}: the reviewer failed ({type(error).__name__}: {str(error)[:160]}); the chapter is rewritten whole")
        decision = domain.fallback_decision(f"{type(error).__name__}")
        graphs.service.emit(params.STEP, "review_done", ok = False, halt = decision.halt, halt_reason = decision.halt_reason, n_revisions = 0, wall_ms = graphs.service.elapsed_ms(t0))
        return {"decision": decision.model_dump()}
    known = {s.get("section_id") for s in data["outline_sections"]} | set(by_id)
    decision, dropped = domain.reviewer_decision(
        halt = answer.halt, confidence = answer.confidence, rationale = answer.rationale,
        revisions = [item.model_dump() for item in answer.revisions], known_section_ids = {str(k) for k in known if k},
    )
    if dropped:
        logger.info(f"[mgsr_replan] {state['framework_slug']}/{state['chapter_id']}: dropped {len(dropped)} revision(s): {dropped[:3]}")
    graphs.service.emit(
        params.STEP, "review_done",
        ok = True, halt = decision.halt, halt_reason = decision.halt_reason, n_revisions = len(decision.revisions), confidence = decision.confidence,
        wall_ms = graphs.service.elapsed_ms(t0),
    )
    return {"decision": decision.model_dump()}


async def finalize(state: graphs.pipeline.synth.mgsr.states.MgsrState, runtime: Runtime[Context]) -> dict:
    context = runtime.context
    slug, chapter_id = state["framework_slug"], state["chapter_id"]
    decision = schemas.MGSRDecision.model_validate(state["decision"])
    elapsed = graphs.service.elapsed_ms(state["started"])
    replan = schemas.MGSRReplan(
        chapter_id = chapter_id,
        chapter_title = state["chapter_title"],
        framework_slug = slug,
        iteration = int(state.get("refine_iter") or 0),
        decision = decision,
        wall_ms = elapsed,
        checklist_pass_rate = state["pass_rate"],
        checklist_chapter_passed = state["chapter_passed"],
        n_failed_criteria = state["n_failed"],
        failed_criteria_names = state["failed_names"],
    )
    payload = replan.model_dump()
    payload["checklist_manifest_hash"] = state["checklist_manifest_hash"]
    payload["outline_manifest_hash"] = state["outline_manifest_hash"]
    payload["mgsr_manifest_hash"] = state["manifest_hash"]
    body = json.dumps(payload, indent = 2, ensure_ascii = False)
    await context.blobs.write(state["versioned_key"], body, content_type = "application/json")
    await context.blobs.write(state["latest_key"], body, content_type = "application/json")

    stats = {
        "halt": decision.halt,
        "halt_reason": decision.halt_reason,
        "confidence": decision.confidence,
        "n_revisions": len(decision.revisions),
        "revisions": [r.model_dump() for r in decision.revisions],
        "chapter_passed": state["chapter_passed"],
        "pass_rate": state["pass_rate"],
        "n_failed_criteria": state["n_failed"],
        "wall_ms": elapsed,
        "store_path": state["latest_key"],
        "versioned_path": state["versioned_key"],
        "manifest_hash": state["manifest_hash"],
        "cache_hit": False,
        "prompt_version": versions.MGSR_PROMPT_VERSION,
        "trivial_pass": bool(state.get("trivial")),
    }
    graphs.service.emit(
        params.STEP, "done", halt = decision.halt, halt_reason = decision.halt_reason, n_revisions = len(decision.revisions), confidence = decision.confidence, wall_ms = elapsed,
    )
    logger.info(
        f"[mgsr_replan] {slug}/{chapter_id}: halt={decision.halt} reason={decision.halt_reason!r} revisions={len(decision.revisions)} "
        f"(pass_rate={state['pass_rate']:.2%}, {state['n_failed']} failed criteria); {elapsed} ms"
    )
    return {"mgsr_path": state["latest_key"], "mgsr_stats": stats}
