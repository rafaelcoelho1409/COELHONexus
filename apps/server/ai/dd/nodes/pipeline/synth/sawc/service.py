"""sawc_write steps — the nodes of its subgraph: prepare, then per stage of the outline's DAG one section_writer per section in parallel, one late retry,
the coverage and source top-up rounds, finalize.

A section is written by a section_writer agent that sees the digest's contributions, the section's code bank (and reads any block in full from `/vault/`)
and checks its draft with `check_section`, the validator that judges the final answer. Memory flows between stages as before: the sections of a stage
see the compressed sections of the stages before it. Every written section is stored on its own, so a resumed chapter writes only what is missing; and on
a RETHINK iteration the reviewer's revisions rewrite only the sections it named, the others come back from the iteration before. All the deterministic
passes (alignment realignment, stripping, top-ups, de-duplication, reordering) are the stage's own and unchanged."""
from __future__ import annotations
import domains
from . import domain, keys, params, schemas, versions
from ..... import agents, backends, graphs, tools

import asyncio
import json
import logging
import time

from langgraph.runtime import Runtime


logger = logging.getLogger(__name__)

Context = graphs.pipeline.synth.entities.SynthContext


def _vault():
    return domains.dd.ingestion.vault


async def prepare(state: graphs.pipeline.synth.sawc.states.SawcState, runtime: Runtime[Context]) -> dict:
    context = runtime.context
    blobs = context.blobs
    slug = state.get("framework_slug")
    chapter_id = state.get("chapter_id")
    started = time.monotonic()
    if not slug or not chapter_id:
        return {"finished": True, **graphs.pipeline.synth.service.failure(
            "sawc_path", "sawc_stats", "no_slug_or_chapter_id", "framework_slug or chapter_id missing from SynthState", started,
        )}

    outline_key = keys.outline_latest_key(slug, chapter_id)
    digest_key = keys.digest_latest_key(slug, chapter_id)
    if not await blobs.exists(outline_key):
        return {"finished": True, **graphs.pipeline.synth.service.failure(
            "sawc_path", "sawc_stats", "outline_not_found", f"outline {outline_key!r} not in MinIO — run outline_sdp first", started, outline_key = outline_key,
        )}
    if not await blobs.exists(digest_key):
        return {"finished": True, **graphs.pipeline.synth.service.failure(
            "sawc_path", "sawc_stats", "digest_not_found", f"digest {digest_key!r} not in MinIO — run digest_construct first", started, digest_key = digest_key,
        )}
    outline_payload = await domains.dd.pipeline.storage.service.load_json(blobs, outline_key)
    digest_payload = await domains.dd.pipeline.storage.service.load_json(blobs, digest_key)
    if outline_payload is None or digest_payload is None:
        return {"finished": True, **graphs.pipeline.synth.service.failure(
            "sawc_path", "sawc_stats", "inputs_unreadable", "outline/digest unreadable", started,
        )}

    outline_sections = (outline_payload.get("outline") or {}).get("sections") or []
    stages_raw = (outline_payload.get("dag") or {}).get("stages") or {}
    chapter_title = outline_payload.get("chapter_title") or chapter_id
    outline_manifest_hash = outline_payload.get("manifest_hash") or ""
    per_section_index: dict[str, list[dict]] = digest_payload.get("per_section") or {}
    # Cross-section vault-hash uniqueness: the digest allows one hash for several sections; without the dedup CLI corpora recycle 3-5 H2s.
    n_deduped, n_removed = domain.dedupe_vault_hashes_across_sections(per_section_index)
    if n_deduped:
        logger.info(f"[sawc_write] {slug}/{chapter_id}: cross-section dedup — {n_deduped} hashes claimed by several sections; removed {n_removed} duplicate code_ref entries")
    valid_source_keys = sorted({s.get("source_key", "") for s in (digest_payload.get("per_source") or []) if s.get("source_key")})
    digest_manifest_hash = digest_payload.get("digest_manifest_hash") or ""
    if not outline_sections or not stages_raw:
        return {"finished": True, **graphs.pipeline.synth.service.failure(
            "sawc_path", "sawc_stats", "empty_outline_or_stages",
            f"outline has {len(outline_sections)} sections, dag has {len(stages_raw)} stages — both must be >0", started,
            n_sections = len(outline_sections), n_stages = len(stages_raw),
        )}

    sections_by_id = {s["section_id"]: s for s in outline_sections}
    stages = {int(k): list(v) for k, v in stages_raw.items()}
    # Merged-away sections: their contributions were re-tagged to the winner; writing them produces a hollow shell that bank padding back-fills with canonical code.
    merged_away = set((digest_payload.get("merged_sections") or {}).keys())
    if merged_away:
        stages = {k: [sid for sid in v if sid not in merged_away] for k, v in stages.items()}
        stages = {k: v for k, v in stages.items() if v}
        logger.info(f"[sawc_write] {slug}/{chapter_id}: skipping {len(merged_away)} digest-merged section(s) {sorted(merged_away)}")
    ordered_stages = [stages[k] for k in sorted(stages)]
    n_sections = sum(len(v) for v in ordered_stages)
    graphs.service.emit(
        params.STEP, "start",
        chapter_id = chapter_id, chapter_title = chapter_title, n_stages = len(ordered_stages), n_sections = n_sections, n_total_drafts = n_sections,
    )

    # The CoRefine loop's bookkeeping. Each sawc_write bumps the counter; it is part of the manifest hash so iterations do not cache-hit each other.
    incoming_refine_iter = int(state.get("refine_iter") or 0)
    refine_iter = incoming_refine_iter + 1
    failed_feedback = (state.get("checklist_stats") or {}).get("failed_feedback") or None if incoming_refine_iter > 0 else None
    incoming_best_score = state.get("best_seen_score")
    incoming_best_path = state.get("best_seen_sawc_path")
    # The score of the iteration that just finished must be captured here, before checklist_eval overwrites checklist_stats: plateau detection compares against it.
    carried_prev_score = (state.get("checklist_stats") or {}).get("pass_rate")
    if incoming_refine_iter > 0 and carried_prev_score is not None:
        prev_key = keys.versioned_blob_key(
            slug, chapter_id,
            domain.compute_manifest_hash(outline_manifest_hash = outline_manifest_hash, digest_manifest_hash = digest_manifest_hash, refine_iter = incoming_refine_iter),
        )
        if incoming_best_score is None or carried_prev_score > incoming_best_score:
            if incoming_best_score is not None:
                logger.info(f"[sawc_write] {slug}/{chapter_id}: new best-seen iteration {incoming_refine_iter} (score {carried_prev_score:.2%} > prior best {incoming_best_score:.2%})")
            incoming_best_score = carried_prev_score
            incoming_best_path = prev_key

    manifest_hash = domain.compute_manifest_hash(
        outline_manifest_hash = outline_manifest_hash, digest_manifest_hash = digest_manifest_hash, refine_iter = refine_iter,
    )
    versioned_key = keys.versioned_blob_key(slug, chapter_id, manifest_hash)
    latest_key = keys.latest_blob_key(slug, chapter_id)

    def carried_patch() -> dict:
        patch = {"refine_iter": refine_iter}
        if incoming_best_path:
            patch["best_seen_sawc_path"] = incoming_best_path
        if incoming_best_score is not None:
            patch["best_seen_score"] = incoming_best_score
        if carried_prev_score is not None:
            patch["prev_checklist_score"] = carried_prev_score
        return patch

    if await blobs.exists(versioned_key) and await blobs.exists(latest_key):
        try:
            cached = json.loads(await blobs.read_text(versioned_key))
            cov = (cached or {}).get("coverage_stats") or {}
            elapsed = graphs.service.elapsed_ms(started)
            stats = {
                "n_sections": cov.get("n_sections", 0),
                "n_completed": cov.get("n_sections_completed", 0),
                "n_fallback": cov.get("n_sections_fallback", 0),
                "n_repairs": cov.get("n_repairs", 0),
                "n_stages": cov.get("n_stages", 0),
                "n_total_drafts_fired": cov.get("n_total_drafts_fired", 0),
                "n_picker_fallbacks": cov.get("n_picker_fallbacks", 0),
                "wall_ms": elapsed,
                "store_path": latest_key,
                "versioned_path": versioned_key,
                "manifest_hash": manifest_hash,
                "cache_hit": True,
                "prompt_version": cached.get("prompt_version"),
            }
            graphs.service.emit(
                params.STEP, "done",
                n_sections = stats["n_sections"], n_completed = stats["n_completed"], n_fallback = stats["n_fallback"], n_repairs = stats["n_repairs"],
                total_drafts_fired = stats["n_total_drafts_fired"], wall_ms = elapsed, cache_hit = True,
            )
            logger.info(f"[sawc_write] {slug}/{chapter_id}: CACHE HIT — {stats['n_completed']}/{stats['n_sections']} sections, {stats['n_repairs']} repairs, {elapsed} ms")
            return {"finished": True, "sawc_path": latest_key, "sawc_stats": stats, **carried_patch()}
        except Exception as error:
            logger.warning(f"[sawc_write] {slug}/{chapter_id}: cached blob {versioned_key!r} unreadable ({type(error).__name__}: {error}); recomputing")

    # Visible Vault (arXiv 2601.03640): the writer sees actual code bodies; render substitutes by hash, so the shipped code stays byte-exact.
    vault_rich, n_loaded, n_skipped = await domains.dd.ingestion.vault.service.load_rich(blobs, slug, valid_source_keys)
    logger.info(f"[sawc_write] {slug}/{chapter_id}: visible vault loaded — {len(vault_rich)} entries across {n_loaded} sources (skipped {n_skipped})")

    # A RETHINK iteration rewrites only the sections the reviewer named; the others come back from the iteration before.
    completed: dict[str, schemas.Section] = {}
    feedback_for: dict[str, list[str]] = {}
    revisions = await revisions_of(blobs, slug, chapter_id, incoming_refine_iter, set(sections_by_id) - merged_away)
    if revisions:
        previous = await domains.dd.pipeline.storage.service.load_json(blobs, latest_key)
        if previous:
            for raw in previous.get("sections") or []:
                sid = raw.get("section_id")
                if sid in sections_by_id and sid not in revisions and sid not in merged_away:
                    try:
                        completed[sid] = schemas.Section.model_validate(raw)
                    except Exception:
                        continue
            feedback_for = {sid: [instruction] for sid, instruction in revisions.items()}
            logger.info(
                f"[sawc_write] {slug}/{chapter_id}: iteration {refine_iter} rewrites {sorted(revisions)} and reuses {len(completed)} section(s) of the iteration before"
            )
            graphs.service.emit(params.STEP, "revising", n_revised = len(revisions), n_reused = len(completed), section_ids = sorted(revisions))

    context.scratch[params.STEP] = {
        "slug": slug,
        "chapter_id": chapter_id,
        "chapter_title": chapter_title,
        "outline_sections": outline_sections,
        "sections_by_id": sections_by_id,
        "per_section_index": per_section_index,
        "valid_source_keys": valid_source_keys,
        "vault_rich": vault_rich,
        "completed": completed,
        "memory": [],
        "used": set(),
        "snapshot": {"memory": [], "used": set()},
        "prior_feedback": failed_feedback,
        "feedback_for": feedback_for,
        "outline_manifest_hash": outline_manifest_hash,
        "digest_manifest_hash": digest_manifest_hash,
        "writer_walls": {},
        "stage_started": started,
        "topup": {},
        "extend_sem": asyncio.Semaphore(params.TOPUP_CONCURRENCY),
    }
    return {
        "finished": False,
        "started": started,
        "manifest_hash": manifest_hash,
        "versioned_key": versioned_key,
        "latest_key": latest_key,
        "n_stages": len(ordered_stages),
        "stages": ordered_stages,
        "stage_pos": 0,
        "phase": "stage",
        "todo": [],
        "n_drafts": 0,
        "refine_iter_now": refine_iter,
        "carried_prev_score": carried_prev_score,
        "incoming_best_score": incoming_best_score,
        "incoming_best_path": incoming_best_path,
        "topup_cursor": 0,
        "topup_done": not (params.TOPUP_ENABLED and vault_rich),
        "extends_seen": 0,
        "topup_exhausted": False,
        "topup_failed": [],
        "topup_empty": [],
        "topup_gained_late": [],
        "topup_added": 0,
        "topup_touched": [],
    }


async def revisions_of(blobs, slug: str, chapter_id: str, incoming_refine_iter: int, known: set[str]) -> dict[str, str]:
    """`{section id: instruction}` the chapter reviewer asked to rewrite after the iteration that just failed (empty on the first iteration, or when it asked for a whole-chapter rewrite)."""
    if incoming_refine_iter <= 0:
        return {}
    blob = await domains.dd.pipeline.storage.service.load_json(blobs, keys.mgsr_latest_key(slug, chapter_id))
    out: dict[str, str] = {}
    for item in ((blob or {}).get("decision") or {}).get("revisions") or []:
        if isinstance(item, dict) and item.get("section_id") in known and (item.get("instruction") or "").strip():
            out[item["section_id"]] = item["instruction"].strip()
    return out


# ---- a section ---------------------------------------------------------------------------------------------------------------------------

def section_inputs(sid: str, data: dict) -> dict:
    """What a section is written from: its contributions, its code bank (the routed blocks, padded when too few), its citation sources and its mode."""
    vault = _vault()
    per_section_index = data["per_section_index"]
    vault_rich = data["vault_rich"]
    outline_sec = data["sections_by_id"][sid]
    contributions = per_section_index.get(sid) or []
    allowed: set[str] = {h for c in contributions for h in (c.get("code_refs") or [])}
    # Gate prose_mode on the pre-pad count: padding would pull stray hashes into a section that has no code of its own.
    n_routed = len(allowed)
    claimed_elsewhere = domain.hashes_claimed_elsewhere(per_section_index, sid)
    # Chapter-wide blocks the digest never routed here but whose vocabulary this section's own text covers keep a section of a multi-topic code page out of prose mode.
    if vault_rich and n_routed < params.BANK_MIN_SIZE:
        scored = vault.domain.rank_hashes_by_relevance(
            [h for h in vault_rich if h not in allowed and h not in claimed_elsewhere], vault_rich, domain.section_relevance_query(outline_sec, contributions),
        )
        relevant = [h for h, score in scored if score >= params.RELEVANT_PAD_MIN_SCORE]
        if n_routed + len(relevant) >= params.RELEVANT_PAD_MIN_BLOCKS:
            relevant = relevant[:params.BANK_PAD_TO - n_routed]
            allowed.update(relevant)
            n_routed += len(relevant)
            if relevant:
                logger.info(f"[sawc_write] {sid}: digest routed few/no code blocks; {len(relevant)} chapter-wide block(s) matched this section by relevance → bank {len(allowed)}")
    if vault_rich and len(allowed) < params.BANK_MIN_SIZE:
        ranked = vault.domain.rank_hashes_by_pedagogy(list(vault_rich.keys()), vault_rich)
        needed = params.BANK_PAD_TO - len(allowed)
        pads = [h for h in ranked if h not in allowed and h not in claimed_elsewhere][:needed]
        if len(allowed) + len(pads) < params.BANK_SHARE_BELOW:        # nothing unclaimed left: share rather than starve
            pads += [h for h in ranked if h not in allowed and h not in pads][:needed - len(pads)]
        if pads:
            allowed.update(pads)
            logger.info(f"[sawc_write] {sid}: digest-routed bank had {len(allowed) - len(pads)} hashes < {params.BANK_MIN_SIZE}; padded with {len(pads)} pedagogically-ranked chapter-wide hashes")
    allowed_hashes = vault.domain.rank_hashes_by_pedagogy(sorted(allowed), vault_rich) if vault_rich else sorted(allowed)
    # Citations: only the sources the digest routed to THIS section (not chapter-wide), to prevent cross-section drift; zero-routed sections fall back to chapter-wide.
    section_sources = sorted({c.get("source_key", "") for c in contributions if c.get("source_key")})
    citation_fallback = not section_sources
    if citation_fallback:
        section_sources = data["valid_source_keys"]
        logger.info(f"[sawc_write] {sid}: digest routed 0 sources to this section; falling back to chapter-wide ({len(section_sources)} sources) for citations")
    prose_mode = n_routed == 0
    return {
        "outline_sec": outline_sec,
        "contributions": contributions,
        "allowed_hashes": allowed_hashes,
        "section_sources": section_sources,
        "citation_fallback": citation_fallback,
        "prose_mode": prose_mode,
        # 1-2 code blocks (fewer than SUBTOPICS_MIN): mixed mode anchors a subtopic to each block and fills the rest with prose.
        "mixed_mode": (not prose_mode) and len(allowed_hashes) < params.SUBTOPICS_MIN,
        "n_primary": sum(1 for c in contributions if c.get("relevance") == "primary"),
    }


def finish_draft(draft, inputs: dict, data: dict):
    """The deterministic passes every draft goes through after the agent: realign API subheadings and code to what their prose says, strip derived code that does not
    parse and code their prose does not describe, cap padded prose, and never ship an invented hash."""
    allowed = set(inputs["allowed_hashes"])
    vault_rich = data["vault_rich"]
    sid = inputs["outline_sec"]["section_id"]
    draft, n_realigned = domain.realign_api_subheadings(draft, vault_rich, allowed)
    draft, n_bad_derived = domain.strip_invalid_derived(draft)
    if n_bad_derived:
        logger.warning(f"[sawc_write] {sid}: {n_bad_derived} writer-authored derived code block(s) that do not parse as Python stripped (prose kept)")
    draft, n_overlap = domain.realign_code_by_overlap(draft, vault_rich, allowed)
    draft, n_misaligned = domain.strip_misaligned_code(draft, vault_rich)
    if n_misaligned:
        logger.info(f"[sawc_write] {sid}: {n_misaligned} code block(s) sharing no identifier with their prose turned into prose subtopics")
    if inputs["prose_mode"]:
        draft, n_capped = domain.cap_prose_subtopics(draft, sum(len(c.get("key_facts") or []) for c in inputs["contributions"]))
        if n_capped:
            logger.info(f"[sawc_write] {sid}: {n_capped} padded prose subtopic(s) beyond the section's key facts dropped")
    if n_overlap or n_realigned:
        logger.info(f"[sawc_write] {sid}: {n_overlap + n_realigned} subtopic(s) realigned to the block they describe")
    draft, n_invented = domain.drop_unknown_code_refs(draft, allowed)
    if n_invented:
        logger.warning(f"[sawc_write] {sid}: {n_invented} code_ref_hash(es) not in the allowed bank survived the checks — converted to prose subtopic(s)")
    return draft


async def write_one(context: Context, sid: str, budget: int | None) -> dict:
    """Write one section: the writer agent (again with its hard issues, when it left some), then the deterministic passes. Returns the stage's result row."""
    data = context.scratch[params.STEP]
    slug, chapter_id = data["slug"], data["chapter_id"]
    outline_sec = data["sections_by_id"].get(sid)
    if not outline_sec:
        logger.warning(f"[sawc_write] section_id {sid!r} in stages but not in outline.sections — emitting placeholder")
        data["completed"][sid] = domain.placeholder_section(section_id = sid, heading = sid, n_repairs = 0, deployment_writer = None)
        return {"sid": sid, "attempts": 0, "placeholder": True}

    t0 = time.monotonic()
    inputs = section_inputs(sid, data)
    heading = outline_sec.get("heading") or sid
    snapshot = data["snapshot"]
    allowed = set(inputs["allowed_hashes"])
    sources = set(inputs["section_sources"])
    vault_rich = data["vault_rich"]
    feedback = list(data["feedback_for"].get(sid) or data["prior_feedback"] or [])

    def request(feedback_now: list[str], budget_now: int | None, previous: dict | None) -> dict:
        built = domain.writer_request(
            framework = slug, chapter_id = chapter_id, chapter_title = data["chapter_title"], section_id = sid, section_heading = heading,
            section_description = outline_sec.get("description") or "", section_prerequisites = outline_sec.get("prerequisites") or [],
            contributions = inputs["contributions"], allowed_hashes = inputs["allowed_hashes"], valid_source_keys = inputs["section_sources"],
            memory = snapshot["memory"], n_primary_contribs = inputs["n_primary"], vault_rich = vault_rich,
            prose_mode = inputs["prose_mode"], mixed_mode = inputs["mixed_mode"], already_shown_hashes = set(snapshot["used"]),
            vault_char_budget = budget_now, prior_feedback = feedback_now,
        )
        if previous is not None:
            built["previous_draft"] = previous
        return built

    first = request(feedback, budget, None)
    item = domains.dd.pipeline.storage.domain.digest(versions.SAWC_PROMPT_VERSION, sid, json.dumps(first, sort_keys = True, ensure_ascii = False))
    stored = await domains.dd.pipeline.storage.service.read_chapter_items(context.blobs, slug, chapter_id, params.STEP, versions.SAWC_PROMPT_VERSION, [item])
    if item in stored:
        data["completed"][sid] = schemas.Section.model_validate(stored[item])
        graphs.service.emit(params.STEP, "section_done", section_id = sid, n_subtopics = len(data["completed"][sid].subtopics), n_citations = len(data["completed"][sid].citations), cached = True)
        return {"sid": sid, "attempts": 0, "placeholder": False}

    def check_issues(candidate: dict) -> tuple[object, list[str]]:
        draft, error = domain.try_parse_draft(candidate, expected_heading = heading)
        if draft is None:
            return None, [f"The section does not parse: {error}"]
        return draft, domain.validate_section_against_inputs(
            draft, expected_heading = heading, allowed_hashes = allowed, valid_source_keys = sources, vault_rich = vault_rich,
        )

    best: tuple[object, list[str]] | None = None
    errors: list[str] = []
    n_repairs = 0
    attempts = 0
    files = domain.vault_files(inputs["allowed_hashes"], vault_rich)
    for attempt in range(1, params.WRITER_ATTEMPTS + 1):
        attempts = attempt
        counts = {"bad": 0}

        async def check(candidate: dict) -> list[str]:
            _, issues = check_issues(candidate)
            hard = domain.hard_issues(issues)
            counts["bad"] += 1 if hard else 0
            return [issue if issue in hard else f"soft (may stay): {issue}" for issue in issues]

        role = context.agents.section_writer(
            backend = backends.files.service.mounted(files, agents.pipeline.synth.params.VAULT_ROUTE),
            tools = [tools.pipeline.synth.check.tool.check_section_tool(check)],
        )
        body = request(feedback, budget, best[0].model_dump() if best is not None and attempt > 1 else None)
        try:
            answer = await agents.service.ask(
                role,
                agents.pipeline.synth.schemas.SectionAnswer,
                json.dumps(body, ensure_ascii = False),
                attempts = params.ASK_ATTEMPTS,
                wait_base_s = params.RETRY_WAIT_S,
                check = lambda a: bool(a.subtopics),
            )
        except Exception as error:
            tag = "context_overflow" if domain.is_context_overflow_error(error) else type(error).__name__
            errors.append(tag)
            logger.warning(f"[sawc_write] {sid} run {attempt}: {tag}: {str(error)[:160]}")
            if tag == "context_overflow":
                budget = (budget or params.MAX_VAULT_CHARS_TOTAL) // 2
            n_repairs += counts["bad"]
            continue
        n_repairs += counts["bad"]
        draft, issues = check_issues(answer.model_dump())
        if draft is None:
            errors.append("pydantic_fail")
            feedback = [f"Your previous answer was rejected: {issues[0]}"]
            continue
        hard = domain.hard_issues(issues)
        if best is None or len(hard) < len(domain.hard_issues(best[1])):
            best = (draft, issues)
        if not hard:
            break
        feedback = hard

    wall_ms = graphs.service.elapsed_ms(t0)
    if best is None:
        summary = ",".join(errors) or "unknown"
        logger.warning(f"[sawc_write] {sid}: every writer run failed ({summary}) — emitting placeholder")
        graphs.service.emit(params.STEP, "section_done", section_id = sid, n_subtopics = 0, n_citations = 0, total_explanation_chars = 0, n_repairs = n_repairs, wall_ms = wall_ms, fallback = "placeholder")
        if data["completed"].get(sid) is None:       # a late retry that fails again leaves the placeholder it had
            data["completed"][sid] = domain.placeholder_section(section_id = sid, heading = heading, n_repairs = n_repairs, deployment_writer = None, error_tags = errors)
        return {"sid": sid, "attempts": attempts, "placeholder": True}

    draft = finish_draft(best[0], inputs, data)
    issues = domain.validate_section_against_inputs(
        draft, expected_heading = heading, allowed_hashes = allowed, valid_source_keys = sources, vault_rich = vault_rich,
    )
    section = schemas.Section(
        section_id = sid,
        heading = draft.heading,
        intro = draft.intro,
        subtopics = draft.subtopics,
        citations = draft.citations,
        wall_ms = wall_ms,
        n_drafts_tried = attempts,
        n_repairs = n_repairs,
        chosen_draft_idx = 0,
        structural_score = domain.score_draft_structural(
            draft, expected_heading = heading, allowed_hashes = allowed, valid_source_keys = sources, n_primary_contribs = inputs["n_primary"], vault_rich = vault_rich,
        ),
        issues = issues + (["citation_fallback"] if inputs["citation_fallback"] else []),
    )
    data["completed"][sid] = section
    try:
        await domains.dd.pipeline.storage.service.write_chapter_item(
            context.blobs, slug, chapter_id, params.STEP, versions.SAWC_PROMPT_VERSION, item, section.model_dump(),
        )
    except Exception as error:
        logger.warning(f"[sawc_write] section {sid} not stored ({type(error).__name__}: {error})")
    graphs.service.emit(
        params.STEP, "section_done",
        section_id = sid, n_subtopics = len(section.subtopics), n_citations = len(section.citations),
        total_explanation_chars = sum(len(st.explanation) for st in section.subtopics), n_repairs = n_repairs, wall_ms = wall_ms,
    )
    return {"sid": sid, "attempts": attempts, "placeholder": False}


async def start_stage(state: graphs.pipeline.synth.sawc.states.SawcState, runtime: Runtime[Context]) -> dict:
    """Open the next stage: the sections of a stage all see the memory and the printed hashes of the stages before it. After the last stage, the late retry."""
    data = runtime.context.scratch[params.STEP]
    pos = state.get("stage_pos", 0)
    stages = state["stages"]
    data["snapshot"] = {"memory": [m.model_dump() for m in data["memory"]], "used": set(data["used"])}
    if pos < len(stages):
        sids = stages[pos]
        graphs.service.emit(params.STEP, "stage_start", stage_idx = pos, n_sections_in_stage = len(sids), section_ids = sids)
        data["stage_started"] = time.monotonic()
        return {"phase": "stage", "todo": [{"sid": sid} for sid in sids if sid not in data["completed"]]}
    # Late retry: a section whose runs ALL failed (a 504 storm while its stage ran) gets one more go now that the others are done and the pool has recovered, with a lighter vault.
    lost = [sid for sid in data["sections_by_id"] if (sec := data["completed"].get(sid)) is not None and "placeholder" in (sec.issues or [])]
    if not params.LATE_RETRY_ENABLED or not lost:
        return {"phase": "late", "todo": []}
    logger.info(f"[sawc_write] {data['slug']}/{data['chapter_id']}: {len(lost)} section(s) lost every run {lost} — one late retry at a {params.LATE_RETRY_VAULT_CHARS}-char vault budget")
    return {"phase": "late", "todo": [{"sid": sid, "budget": params.LATE_RETRY_VAULT_CHARS} for sid in lost]}


async def write_section(state: dict, runtime: Runtime[Context]) -> dict:
    task = state["task"]
    result = await write_one(runtime.context, task["sid"], task.get("budget"))
    return {"writes": [result]}


def remember(data: dict, sid: str) -> None:
    """Add a finished section to the memory ledger and to the printed hashes, replacing the entry it had (a late retry)."""
    section = data["completed"][sid]
    try:
        entry = domain.extract_memory_entry(section, section_contributions = data["per_section_index"].get(sid) or [], section_heading = section.heading)
        data["memory"][:] = [m for m in data["memory"] if m.section_id != sid] + [entry]
    except Exception as error:
        logger.warning(f"[sawc_write] memory extract failed for {sid}: {type(error).__name__}: {error}")
    for st in section.subtopics or []:
        if st.code_ref_hash:
            data["used"].add(st.code_ref_hash)


async def finish_stage(state: graphs.pipeline.synth.sawc.states.SawcState, runtime: Runtime[Context]) -> dict:
    data = runtime.context.scratch[params.STEP]
    pos = state.get("stage_pos", 0)
    if state.get("phase") == "late":
        recovered = [t["sid"] for t in state.get("todo") or [] if "placeholder" not in (data["completed"][t["sid"]].issues or [])]
        for sid in recovered:
            remember(data, sid)
            logger.info(f"[sawc_write] {sid}: recovered by the late retry ({len(data['completed'][sid].subtopics)} subtopics)")
        # Copies of a block another section prints go BEFORE the top-ups: the freed room is refilled from blocks and facts nobody states yet instead of staying as a prose echo.
        sections, n_repeated = domain.drop_repeated_code_blocks(ordered_sections(data), data["vault_rich"])
        for section in sections:
            data["completed"][section.section_id] = section
        if n_repeated:
            logger.info(f"[sawc_write] {data['slug']}/{data['chapter_id']}: {n_repeated} subtopic(s) repeating a code block printed in an earlier section dropped or stripped")
        return {"todo": []}
    sids = state["stages"][pos]
    n_done = 0
    n_failed = 0
    for sid in sids:
        if data["completed"].get(sid) is None:
            data["completed"][sid] = domain.placeholder_section(
                section_id = sid, heading = data["sections_by_id"].get(sid, {}).get("heading", sid), n_repairs = 0, deployment_writer = None,
            )
        if "placeholder" in (data["completed"][sid].issues or []):
            n_failed += 1
        else:
            n_done += 1
        remember(data, sid)
    graphs.service.emit(
        params.STEP, "stage_done", stage_idx = pos, n_completed = n_done, n_failed = n_failed, wall_ms = graphs.service.elapsed_ms(data["stage_started"]),
    )
    return {"stage_pos": pos + 1, "todo": []}


# ---- top-ups -----------------------------------------------------------------------------------------------------------------------------

def ordered_sections(data: dict) -> list[schemas.Section]:
    return [data["completed"][s["section_id"]] for s in data["outline_sections"] if data["completed"].get(s["section_id"]) is not None]


async def plan_coverage(state: graphs.pipeline.synth.sawc.states.SawcState, runtime: Runtime[Context]) -> dict:
    """The next coverage round that has work: code blocks routed to a section but not printed get an extra call (rounds 1-2, a salvage pass for sections whose
    calls all failed, and one more round for what the salvage gained). A block that is a near-copy of a printed one counts as covered."""
    data = runtime.context.scratch[params.STEP]
    sections = ordered_sections(data)
    steps: list = [*range(1, params.TOPUP_ROUNDS + 1), "salvage", "after-salvage"]
    cursor = state.get("topup_cursor", 0)
    exhausted = state.get("topup_exhausted", False)
    failed, empty, gained_late = set(state.get("topup_failed") or []), set(state.get("topup_empty") or []), set(state.get("topup_gained_late") or [])
    vault_rich = data["vault_rich"]
    if state.get("topup_done"):
        return {"topup_done": True, "extend_todo": []}
    while cursor < len(steps):
        step = steps[cursor]
        if step == "after-salvage":
            if not gained_late:
                break
            ids = set(gained_late)
        elif step == "salvage":
            # A section whose calls all failed (capacity, 504s) is not a verdict on the section: ask again once the other sections are done.
            if not failed:
                break
            ids, failed = set(failed), set()
        elif exhausted:
            cursor += 1
            continue
        else:
            ids = {s.section_id for s in sections} - failed - empty
        unused = domain.unused_routed_hashes(data["per_section_index"], sections)
        printed = sorted({st.code_ref_hash for sec in sections for st in sec.subtopics if st.code_ref_hash})
        dups = domain.near_duplicate_hashes(
            sorted({h for hs in unused.values() for h in hs}), printed, {h: domain._fence_body(vault_rich.get(h)) for h in vault_rich},
        )
        if dups:
            unused = {sid: [h for h in hs if h not in dups] for sid, hs in unused.items()}
            unused = {sid: hs for sid, hs in unused.items() if hs}
        data["topup"] = {"unused": unused, "required": domain.dependency_blocks(sections, unused, vault_rich), "step": step}
        todo = [s.section_id for s in sections if s.section_id in ids]
        return {
            "topup_cursor": cursor, "extend_todo": todo, "topup_failed": sorted(failed), "topup_empty": sorted(empty), "topup_exhausted": exhausted,
        }
    return {"topup_done": True, "extend_todo": []}


async def extend_coverage(state: dict, runtime: Runtime[Context]) -> dict:
    """Extra subtopics for one section's unused routed code blocks (the coverage_extender agent), merged when they validate."""
    context = runtime.context
    data = context.scratch[params.STEP]
    sid = state["sid"]
    sec = data["completed"][sid]
    unused, required = data["topup"]["unused"], data["topup"]["required"]
    need = required.get(sid) or []
    candidates = need + [h for h in unused.get(sid) or [] if h not in need]
    n_new = min(len(candidates), params.TOPUP_SECTION_MAX - len(sec.subtopics), params.TOPUP_MAX_NEW)
    if (len(candidates) < params.TOPUP_MIN_UNUSED and not need) or n_new < 1:
        return {"extends": [{"sid": sid, "added": 0, "outcome": "ok"}]}
    contributions = data["per_section_index"].get(sid) or []
    facts = [f for c in contributions for f in (c.get("key_facts") or [])]
    sources = list({c["source_key"]: (c["source_key"], c.get("summary") or "") for c in contributions if c.get("source_key")}.values())
    vault_rich = data["vault_rich"]
    request = domain.coverage_request(
        framework = data["slug"], chapter_title = data["chapter_title"], section_heading = sec.heading,
        section_description = (data["sections_by_id"].get(sid) or {}).get("description") or "",
        existing_subheadings = [st.subheading for st in sec.subtopics],
        blocks = [(h, domain._fence_body(vault_rich.get(h))) for h in candidates if domain._fence_body(vault_rich.get(h))],
        facts = facts, sources = sources, max_new = n_new, required = set(need),
    )
    raised = 0
    for attempt in range(1, params.TOPUP_ATTEMPTS + 1):
        async with data["extend_sem"]:
            try:
                answer = await agents.service.ask(
                    context.agents.coverage_extender(), agents.pipeline.synth.schemas.ExtensionAnswer, json.dumps(request, ensure_ascii = False),
                    attempts = params.ASK_ATTEMPTS, wait_base_s = params.RETRY_WAIT_S,
                )
            except Exception as error:
                raised += 1
                logger.info(f"[sawc_write] {data['chapter_id']}/{sid}: coverage top-up call {attempt}/{params.TOPUP_ATTEMPTS} failed ({type(error).__name__})")
                continue
        rejects: dict[str, int] = {}
        subs, cites = domain.parse_topup(
            answer.model_dump(), unused = set(candidates), vault_rich = vault_rich,
            valid_source_keys = {x[0] for x in sources} | {c.source_key for c in sec.citations},
            existing_subheads = {st.subheading for st in sec.subtopics}, max_new = n_new, rejects = rejects,
        )
        if not subs:
            logger.info(f"[sawc_write] {data['chapter_id']}/{sid}: coverage top-up call {attempt}/{params.TOPUP_ATTEMPTS} held no valid subtopic (rejected: {rejects or 'nothing proposed'})")
            continue
        have = {(c.source_key, c.claim) for c in sec.citations}
        merged = list(sec.citations) + [c for c in cites if (c.source_key, c.claim) not in have]
        data["completed"][sid] = sec.model_copy(update = {"subtopics": list(sec.subtopics) + subs, "citations": merged[:params.TOPUP_CITATIONS_MAX]})
        return {"extends": [{"sid": sid, "added": len(subs), "outcome": "ok"}]}
    # Every call raising (capacity, 504s) is salvageable later; a model that answered without a valid subtopic is not asked again.
    return {"extends": [{"sid": sid, "added": 0, "outcome": "failed" if raised == params.TOPUP_ATTEMPTS else "empty"}]}


async def merge_coverage(state: graphs.pipeline.synth.sawc.states.SawcState, runtime: Runtime[Context]) -> dict:
    data = runtime.context.scratch[params.STEP]
    steps: list = [*range(1, params.TOPUP_ROUNDS + 1), "salvage", "after-salvage"]
    cursor = state.get("topup_cursor", 0)
    step = steps[cursor]
    seen = state.get("extends_seen", 0)
    results = (state.get("extends") or [])[seen:]
    failed, empty = set(state.get("topup_failed") or []), set(state.get("topup_empty") or [])
    for result in results:
        if result["outcome"] == "failed":
            failed.add(result["sid"])
        elif result["outcome"] == "empty":
            empty.add(result["sid"])
    added = sum(r["added"] for r in results)
    touched = set(state.get("topup_touched") or []) | {r["sid"] for r in results if r["added"]}
    gained_late = {r["sid"] for r in results if r["added"]} if step == "salvage" else set(state.get("topup_gained_late") or [])
    if step == "salvage":
        logger.info(f"[sawc_write] {data['chapter_id']}: coverage top-up salvage pass over {len(results)} section(s) that had failed: +{added} subtopic(s)")
    elif isinstance(step, int) and step > 1 and added:
        logger.info(f"[sawc_write] {data['chapter_id']}: coverage top-up round {step} added {added} subtopic(s)")
    return {
        "topup_cursor": cursor + 1,
        "extends_seen": seen + len(results),
        "topup_failed": sorted(failed),
        "topup_empty": sorted(empty),
        "topup_gained_late": sorted(gained_late),
        "topup_exhausted": state.get("topup_exhausted", False) or (not added and step not in ("salvage", "after-salvage")),
        "topup_added": state.get("topup_added", 0) + added,
        "topup_touched": sorted(touched),
        "extend_todo": [],
    }


async def plan_sources(state: graphs.pipeline.synth.sawc.states.SawcState, runtime: Runtime[Context]) -> dict:
    """One call per section that was routed sources the chapter never cites: prose subtopics grounded in their key facts, and for cited sources the prose leaves unsaid."""
    data = runtime.context.scratch[params.STEP]
    if not params.TOPUP_SRC_ENABLED:
        return {"extend_todo": []}
    sections = ordered_sections(data)
    pending = domain.source_gaps(data["per_section_index"], sections, domain.chapter_prose_tokens(sections))
    data["source_gaps"] = pending
    return {"extend_todo": [sec.section_id for sec in sections if pending.get(sec.section_id)], "topup_done": True}


async def extend_sources(state: dict, runtime: Runtime[Context]) -> dict:
    context = runtime.context
    data = context.scratch[params.STEP]
    sid = state["sid"]
    sec = data["completed"][sid]
    sources = data["source_gaps"].get(sid) or []
    n_new = min(params.TOPUP_SRC_MAX_NEW, params.TOPUP_SECTION_MAX + params.TOPUP_SRC_EXTRA - len(sec.subtopics))
    if not sources or n_new < 1:
        return {"extends": [{"sid": sid, "added": 0, "outcome": "ok"}]}
    request = domain.source_request(
        framework = data["slug"], chapter_title = data["chapter_title"], section_heading = sec.heading,
        section_description = (data["sections_by_id"].get(sid) or {}).get("description") or "",
        existing_subheadings = [f"{st.subheading} — {st.explanation[:160]}" for st in sec.subtopics], sources = sources, max_new = n_new,
    )
    for attempt in range(1, params.TOPUP_ATTEMPTS + 1):
        async with data["extend_sem"]:
            try:
                answer = await agents.service.ask(
                    context.agents.source_extender(), agents.pipeline.synth.schemas.SourceExtensionAnswer, json.dumps(request, ensure_ascii = False),
                    attempts = params.ASK_ATTEMPTS, wait_base_s = params.RETRY_WAIT_S,
                )
            except Exception as error:
                logger.info(f"[sawc_write] {data['chapter_id']}/{sid}: source top-up call {attempt}/{params.TOPUP_ATTEMPTS} failed ({type(error).__name__})")
                continue
        rejects: dict[str, int] = {}
        subs, cites = domain.parse_source_topup(
            answer.model_dump(), sources = sources, existing_subheads = {st.subheading for st in sec.subtopics}, max_new = n_new, rejects = rejects,
        )
        if not subs:
            logger.info(f"[sawc_write] {data['chapter_id']}/{sid}: source top-up call {attempt}/{params.TOPUP_ATTEMPTS} held no valid subtopic (rejected: {rejects or 'nothing proposed'})")
            continue
        have = {(c.source_key, c.claim) for c in sec.citations}
        merged = list(sec.citations) + [c for c in cites if (c.source_key, c.claim) not in have]
        data["completed"][sid] = sec.model_copy(update = {"subtopics": list(sec.subtopics) + subs, "citations": merged[:params.TOPUP_CITATIONS_MAX]})
        return {"extends": [{"sid": sid, "added": len(subs), "outcome": "ok"}]}
    return {"extends": [{"sid": sid, "added": 0, "outcome": "empty"}]}


# ---- finalize ----------------------------------------------------------------------------------------------------------------------------

async def finalize(state: graphs.pipeline.synth.sawc.states.SawcState, runtime: Runtime[Context]) -> dict:
    context = runtime.context
    data = context.scratch[params.STEP]
    slug, chapter_id = data["slug"], data["chapter_id"]
    vault_rich = data["vault_rich"]
    final_sections = ordered_sections(data)
    n_topup = sum(r["added"] for r in state.get("extends") or [])
    if n_topup:
        logger.info(f"[sawc_write] {slug}/{chapter_id}: top-ups added {n_topup} subtopic(s)")
    final_sections, n_ordered = domain.order_by_code_dependencies(final_sections, vault_rich)
    if n_ordered:
        logger.info(f"[sawc_write] {slug}/{chapter_id}: {n_ordered} section(s) reordered so a block that defines a helper comes before the blocks that call it")
    final_sections, n_echo = domain.drop_prose_echoes(final_sections)
    if n_echo:
        logger.info(f"[sawc_write] {slug}/{chapter_id}: {n_echo} prose subtopic(s) repeating another subtopic of the chapter dropped")

    n_drafts = sum(w["attempts"] for w in state.get("writes") or [])
    coverage = domain.compute_sawc_stats(
        sections = final_sections, n_stages = state["n_stages"], n_total_drafts_fired = n_drafts, n_critic_picks = 0, n_picker_fallbacks = 0,
    )
    # WHY a section went fully empty, aggregated like the digest's error_breakdown.
    error_breakdown: dict[str, int] = {}
    for section in final_sections:
        for tag in section.issues or []:
            if tag.startswith("draft_fail:"):
                kind = tag.split(":", 1)[1] or "unknown"
                error_breakdown[kind] = error_breakdown.get(kind, 0) + 1

    chapter_draft = schemas.ChapterDraft(
        chapter_id = chapter_id,
        chapter_title = data["chapter_title"],
        framework_slug = slug,
        sections = final_sections,
        memory_final = data["memory"],
        coverage_stats = coverage,
    )
    payload = chapter_draft.model_dump()
    payload["outline_manifest_hash"] = data["outline_manifest_hash"]
    payload["digest_manifest_hash"] = data["digest_manifest_hash"]
    payload["sawc_manifest_hash"] = state["manifest_hash"]
    body = json.dumps(payload, indent = 2, ensure_ascii = False)
    await context.blobs.write(state["versioned_key"], body, content_type = "application/json")
    await context.blobs.write(state["latest_key"], body, content_type = "application/json")

    elapsed = graphs.service.elapsed_ms(state["started"])
    stats = {
        "n_sections": coverage.n_sections,
        "n_completed": coverage.n_sections_completed,
        "n_fallback": coverage.n_sections_fallback,
        "n_citation_fallback": coverage.n_sections_citation_fallback,
        "n_stages": coverage.n_stages,
        "n_total_drafts_fired": coverage.n_total_drafts_fired,
        "n_critic_picks": coverage.n_critic_picks,
        "n_picker_fallbacks": coverage.n_picker_fallbacks,
        "n_repairs": coverage.n_repairs,
        "total_subtopics": coverage.total_subtopics,
        "total_citations": coverage.total_citations,
        "avg_subtopics_per_section": coverage.avg_subtopics_per_section,
        "avg_explanation_words": coverage.avg_explanation_words,
        "error_breakdown": error_breakdown,
        "wall_ms": elapsed,
        "store_path": state["latest_key"],
        "versioned_path": state["versioned_key"],
        "manifest_hash": state["manifest_hash"],
        "cache_hit": False,
        "prompt_version": versions.SAWC_PROMPT_VERSION,
    }
    graphs.service.emit(
        params.STEP, "done",
        n_sections = stats["n_sections"], n_completed = stats["n_completed"], n_fallback = stats["n_fallback"], n_citation_fallback = stats["n_citation_fallback"],
        n_repairs = stats["n_repairs"], total_drafts_fired = stats["n_total_drafts_fired"], wall_ms = elapsed,
    )
    logger.info(
        f"[sawc_write] {slug}/{chapter_id}: {stats['n_completed']}/{stats['n_sections']} sections written, {stats['n_fallback']} fallbacks "
        f"(failed = {dict(sorted(error_breakdown.items()))}), {stats['n_citation_fallback']} citation-fallback (chapter-wide), {stats['n_repairs']} repairs, "
        f"{stats['n_total_drafts_fired']} writer runs, refine_iter = {state['refine_iter_now']}, {elapsed} ms"
    )
    # Best-seen: the iteration that just finished was compared in `prepare`; this forwards the running record, or this call's own versioned key on the first iteration.
    patch = {"sawc_path": state["latest_key"], "sawc_stats": stats, "refine_iter": state["refine_iter_now"]}
    patch["best_seen_sawc_path"] = state.get("incoming_best_path") or state["versioned_key"]
    if state.get("incoming_best_score") is not None:
        patch["best_seen_score"] = state["incoming_best_score"]
    if state.get("carried_prev_score") is not None:
        patch["prev_checklist_score"] = state["carried_prev_score"]
    return patch
