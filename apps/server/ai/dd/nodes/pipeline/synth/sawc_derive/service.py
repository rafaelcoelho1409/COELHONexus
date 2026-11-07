"""sawc_derive steps — the nodes of its subgraph: prepare, one code_deriver per thin subtopic, finalize.

A code block that is only a bare signature teaches nothing, so a code_deriver agent expands it into a complete runnable example and rewrites the explanation
that goes with it. The agent checks its code with `check_code` (it parses; its body is inside the length band) before answering, so a bad example is
repaired inside the run, not discarded after N samples. The chapter draft is updated in place with the derived code."""
from __future__ import annotations
import domains
from . import domain, keys, params, schemas
from ..... import agents, graphs, tools

import json
import logging
import time

from langgraph.runtime import Runtime


logger = logging.getLogger(__name__)

Context = graphs.pipeline.synth.entities.SynthContext


def skipped(reason: str, started: float, **extra) -> dict:
    return {"finished": True, "derive_stats": {"skipped": reason, "wall_ms": graphs.service.elapsed_ms(started), **extra}}


async def prepare(state: graphs.pipeline.synth.sawc_derive.states.DeriveState, runtime: Runtime[Context]) -> dict:
    context = runtime.context
    blobs = context.blobs
    slug = state.get("framework_slug")
    chapter_id = state.get("chapter_id")
    started = time.monotonic()
    if not slug or not chapter_id:
        return skipped("no_slug_or_chapter_id", started)

    sawc_key = keys.sawc_latest_key(slug, chapter_id)
    if not await blobs.exists(sawc_key):
        # sawc_write failed or hasn't run — nothing to do.
        graphs.service.emit(params.STEP, "skipped", chapter_id = chapter_id, reason = "sawc_latest_missing")
        return skipped("sawc_latest_missing", started)
    sawc_payload = await domains.dd.pipeline.storage.service.load_json(blobs, sawc_key)
    if sawc_payload is None:
        graphs.service.emit(params.STEP, "skipped", chapter_id = chapter_id, reason = "sawc_unreadable")
        return skipped("sawc_unreadable", started)

    sections: list[dict] = sawc_payload.get("sections") or []
    chapter_title = sawc_payload.get("chapter_title") or sawc_payload.get("chapter_id") or chapter_id
    framework = sawc_payload.get("framework_slug") or slug
    n_subtopics_total = sum(len(s.get("subtopics") or []) for s in sections)
    enabled = params.enabled()
    graphs.service.emit(params.STEP, "start", chapter_id = chapter_id, enabled = enabled, n_subtopics_total = n_subtopics_total)

    if not enabled:
        attempts = [
            schemas.DeriveAttempt(
                section_id = str(s.get("section_id") or ""), subheading = str(st.get("subheading") or ""), code_ref_hash = str(st.get("code_ref_hash") or ""),
                original_chars = 0, original_lines = 0, decision = "disabled",
            )
            for s in sections for st in (s.get("subtopics") or [])
        ]
        stats = schemas.DeriveStats(
            chapter_id = chapter_id, framework_slug = framework, enabled = False, n_subtopics_total = n_subtopics_total, n_candidates_thin = 0, n_promoted = 0,
            n_rejected_ast = 0, n_rejected_len = 0, n_rotator_fail = 0, wall_ms = graphs.service.elapsed_ms(started), attempts = attempts,
        )
        await blobs.write(keys.derive_latest_key(slug, chapter_id), json.dumps(stats.model_dump(), indent = 2), content_type = "application/json")
        graphs.service.emit(params.STEP, "done", chapter_id = chapter_id, **stats.model_dump(exclude = {"attempts"}))
        return {"finished": True, "derive_stats": stats.model_dump()}

    # Verbatim subtopics anchored to a vault block: candidates when the block is too thin to teach.
    candidates: list[tuple[dict, dict, str]] = []
    for section in sections:
        for st in section.get("subtopics") or []:
            if isinstance(st, dict) and st.get("code_source") != "derived" and st.get("code_ref_hash"):
                candidates.append((section, st, st["code_ref_hash"]))
    # The vault of every source of the chapter (the digest's list), not only the ones the draft cites: a thin block can sit in a page the draft never cites.
    digest = await domains.dd.pipeline.storage.service.load_json(blobs, keys.digest_latest_key(slug, chapter_id)) or {}
    source_keys = sorted({k for k in (digest.get("source_keys") or []) if k} or {
        c.get("source_key") for s in sections for c in (s.get("citations") or []) if c.get("source_key")
    })
    vault, _, _ = await domains.dd.ingestion.vault.service.load_rich(blobs, slug, source_keys)
    thin: list[tuple[dict, dict, str, str]] = []
    for section, st, h in candidates:
        entry = vault.get(h)
        body = domain.fence_inner(entry.fence_text or "") if entry is not None else ""
        if body and domain.is_thin_block(body):
            thin.append((section, st, h, body))
    thin = thin[:params.MAX_DERIVES_PER_CHAPTER]
    graphs.service.emit(params.STEP, "candidates_identified", n_candidates_thin = len(thin), n_subtopics_total = n_subtopics_total, vault_entries_loaded = len(vault))

    context.scratch[params.STEP] = {
        "payload": sawc_payload, "sawc_key": sawc_key, "thin": thin, "candidates": candidates, "vault": vault,
        "framework": framework, "chapter_title": chapter_title, "n_subtopics_total": n_subtopics_total,
    }
    return {"finished": False, "started": started, "n_candidates": len(thin)}


def sanitize_explanation(text: str) -> str | None:
    """The regenerated explanation, or None when it is outside the band or carries a fence (the old one stays)."""
    explanation = (text or "").strip()
    words = explanation.split()
    if not (params.EXPLANATION_MIN_WORDS <= len(words) <= params.EXPLANATION_MAX_WORDS):
        return None
    if "```" in explanation or "<code-ref" in explanation or "<code id" in explanation:
        return None
    return explanation


def derive_issues(code: str) -> list[str]:
    """What is wrong with a derived example: it must parse and sit inside the length band."""
    body = domain.parse_code_block(code or "") or (code or "").strip()
    if not body:
        return ["The code is empty."]
    if not domain.python_ast_valid(body):
        return ["The code does not parse as valid Python (check imports, brackets, indentation)."]
    n_lines = sum(1 for line in body.splitlines() if line.strip())
    issues = []
    if not (params.DERIVED_MIN_LINES <= n_lines <= params.DERIVED_MAX_LINES):
        issues.append(f"The example has {n_lines} non-blank lines; it must have {params.DERIVED_MIN_LINES} to {params.DERIVED_MAX_LINES}.")
    if not (params.DERIVED_MIN_CHARS <= len(body) <= params.DERIVED_MAX_CHARS):
        issues.append(f"The example has {len(body)} characters; it must have {params.DERIVED_MIN_CHARS} to {params.DERIVED_MAX_CHARS}.")
    return issues


async def derive_subtopic(state: dict, runtime: Runtime[Context]) -> dict:
    """Derive one subtopic's example. Mutates the subtopic of the chapter draft in place on success; returns the attempt either way."""
    context = runtime.context
    data = context.scratch[params.STEP]
    section, subtopic, _hash, original = data["thin"][state["index"]]
    meta = {
        "section_id": str(section.get("section_id") or ""),
        "subheading": str(subtopic.get("subheading") or ""),
        "code_ref_hash": str(subtopic.get("code_ref_hash") or ""),
        "original_chars": len(original or ""),
        "original_lines": (original or "").count("\n") + 1,
    }
    t0 = time.monotonic()

    async def check(candidate: dict) -> list[str]:
        return derive_issues(candidate.get("code") or "")

    role = context.agents.code_deriver(tools = [tools.pipeline.synth.check.tool.check_code_tool(check)])
    request = {
        "framework": data["framework"],
        "chapter": data["chapter_title"],
        "section": str(section.get("heading") or ""),
        "subheading": meta["subheading"],
        "lead_in": str(subtopic.get("explanation") or ""),
        "lang": "python",
        "reference": original.strip(),
    }
    decision, body, explanation = "rotator_fail", "", ""
    try:
        answer = await agents.service.ask(
            role, agents.pipeline.synth.schemas.DerivedCodeAnswer, json.dumps(request, ensure_ascii = False),
            attempts = params.ASK_ATTEMPTS, wait_base_s = params.RETRY_WAIT_S,
        )
        body = domain.parse_code_block(answer.code or "") or (answer.code or "").strip()
        explanation = answer.explanation
        chosen, _scores = domain.rank_mpsc_samples([body])
        if chosen is not None:
            decision = "promoted"
        else:
            decision = "rejected_ast" if not domain.python_ast_valid(body) else "rejected_len"
    except Exception as error:
        logger.debug(f"[sawc_derive] derive failed: {type(error).__name__}: {error}")

    wall_ms = graphs.service.elapsed_ms(t0)
    if decision != "promoted":
        return {"attempts": [schemas.DeriveAttempt(decision = decision, n_samples_tried = 1, n_samples_valid = 0, wall_ms = wall_ms, **meta).model_dump()]}
    subtopic["code_source"] = "derived"
    subtopic["derived_code"] = body
    # Derived code is richer than the original signature: the regenerated explanation keeps prose and code aligned.
    fresh = sanitize_explanation(explanation)
    if fresh:
        subtopic["explanation"] = fresh
    return {"attempts": [schemas.DeriveAttempt(
        decision = "promoted", derived_chars = len(body), derived_lines = sum(1 for ln in body.splitlines() if ln.strip()),
        n_samples_tried = 1, n_samples_valid = 1, chosen_sample_idx = 0, wall_ms = wall_ms, **meta,
    ).model_dump()]}


async def finalize(state: graphs.pipeline.synth.sawc_derive.states.DeriveState, runtime: Runtime[Context]) -> dict:
    context = runtime.context
    data = context.scratch[params.STEP]
    slug, chapter_id = state["framework_slug"], state["chapter_id"]
    results = [schemas.DeriveAttempt.model_validate(a) for a in state.get("attempts") or []]
    thin_ids = {id(st) for _, st, _, _ in data["thin"]}
    skipped_attempts = []
    for section, st, h in data["candidates"]:
        if id(st) in thin_ids:
            continue
        entry = data["vault"].get(h)
        body = (entry.fence_text or "") if entry is not None else ""
        skipped_attempts.append(schemas.DeriveAttempt(
            section_id = str(section.get("section_id") or ""), subheading = str(st.get("subheading") or ""), code_ref_hash = h,
            original_chars = len(body), original_lines = (body.count("\n") + 1) if body else 0, decision = "skipped_thin",
        ))
    n_promoted = sum(1 for a in results if a.decision == "promoted")
    # Mutate the draft in place if any derive promoted: the latest pointer AND the iteration's versioned blob, because render ships the best-seen iteration by its versioned key.
    if n_promoted:
        body = json.dumps(data["payload"], indent = 2, ensure_ascii = False)
        manifest = data["payload"].get("sawc_manifest_hash") or ""
        targets = [data["sawc_key"]] + ([keys.sawc_versioned_key(slug, chapter_id, manifest)] if manifest else [])
        for key in targets:
            try:
                await context.blobs.write(key, body, content_type = "application/json")
            except Exception as error:
                logger.error(f"[sawc_derive] failed to persist the mutated draft at {key!r}: {type(error).__name__}: {error}")
    started = state.get("started") or time.monotonic()
    stats = schemas.DeriveStats(
        chapter_id = chapter_id,
        framework_slug = data["framework"],
        enabled = True,
        n_subtopics_total = data["n_subtopics_total"],
        n_candidates_thin = len(data["thin"]),
        n_promoted = n_promoted,
        n_rejected_ast = sum(1 for a in results if a.decision == "rejected_ast"),
        n_rejected_len = sum(1 for a in results if a.decision == "rejected_len"),
        n_rotator_fail = sum(1 for a in results if a.decision == "rotator_fail"),
        wall_ms = graphs.service.elapsed_ms(started),
        attempts = list(results) + skipped_attempts,
    )
    try:
        await context.blobs.write(keys.derive_latest_key(slug, chapter_id), json.dumps(stats.model_dump(), indent = 2), content_type = "application/json")
    except Exception as error:
        logger.warning(f"[sawc_derive] failed to persist derive-latest: {type(error).__name__}: {error}")
    graphs.service.emit(
        params.STEP, "done",
        chapter_id = chapter_id, n_subtopics_total = stats.n_subtopics_total, n_candidates_thin = stats.n_candidates_thin, n_promoted = n_promoted,
        n_rejected_ast = stats.n_rejected_ast, n_rejected_len = stats.n_rejected_len, n_rotator_fail = stats.n_rotator_fail, wall_ms = stats.wall_ms,
    )
    logger.info(
        f"[sawc_derive] {slug}/{chapter_id}: {n_promoted}/{stats.n_candidates_thin} promoted "
        f"(ast_fail={stats.n_rejected_ast}, len_fail={stats.n_rejected_len}, rotator_fail={stats.n_rotator_fail}); {stats.wall_ms} ms"
    )
    return {"derive_stats": stats.model_dump()}
