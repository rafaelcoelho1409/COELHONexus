"""Build candidate partitions, ask the architect to choose and refine one, then reconcile it into selector-compatible artifacts."""
from __future__ import annotations
import domains
from . import domain, params, versions
from ..... import agents, graphs, skills

import asyncio
import json
import logging
import time
from collections import Counter, defaultdict
from hashlib import sha256

from langgraph.runtime import Runtime


logger = logging.getLogger(__name__)
Context = graphs.pipeline.planner.entities.PlannerContext


def _page_order(cards: list[dict]) -> dict[str, int]:
    return {card["key"]: i for i, card in enumerate(cards)}


def _model_partitions(candidates: dict) -> list[dict]:
    """Do not repeat full page cards or code facts in every candidate partition sent to the model."""
    return [
        {
            "id": partition["id"],
            "method": partition["method"],
            "metrics": partition["metrics"],
            "groups": [
                {
                    "group_id": group["group_id"],
                    "kind": group["kind"],
                    "n_pages": group["n_pages"],
                    "reading_minutes": group["reading_minutes"],
                    "n_best_code": group["n_best_code"],
                    "topics": group["topics"],
                    "page_keys": [page["key"] for page in group["pages"]],
                }
                for group in partition["groups"]
            ],
        }
        for partition in candidates.get("partitions") or []
    ]


def _answer_issues(answer: dict | None, candidates: dict, cards: list[dict]) -> list[str]:
    partitions = {item["id"]: item for item in candidates.get("partitions") or []}
    selected = partitions.get((answer or {}).get("candidate_partition_id"))
    if not selected:
        return ["Choose one of the supplied candidate partition ids."]
    groups = {group["group_id"]: group for group in selected["groups"]}
    counts: dict[str, int] = defaultdict(int)
    issues = []
    titles = []
    for chapter in (answer or {}).get("chapters") or []:
        title = " ".join(str(chapter.get("title") or "").casefold().split())
        if title:
            titles.append(title)
        for group_id in chapter.get("group_ids") or []:
            counts[group_id] += 1
    omitted = sorted(group_id for group_id in groups if counts[group_id] == 0)
    repeated = sorted(group_id for group_id, count in counts.items() if count > 1)
    unknown = sorted(group_id for group_id in counts if group_id not in groups)
    if omitted:
        issues.append(f"Assign every group exactly once; omitted: {omitted[:12]}.")
    if repeated:
        issues.append(f"Each group may appear once; repeated: {repeated[:12]}.")
    if unknown:
        issues.append(f"Use only supplied group ids; unknown: {unknown[:12]}.")
    duplicate_titles = sorted(title for title, count in Counter(titles).items() if count > 1)
    if duplicate_titles:
        issues.append(f"Give each chapter a unique title so prerequisite references resolve: {duplicate_titles[:8]}.")
    chapters = (answer or {}).get("chapters") or []
    tutorial_chapters = [chapter for chapter in chapters if chapter.get("kind") != "appendix"]
    suggested_count = int(candidates.get("target_chapters") or 0)
    if (
        suggested_count > 0
        and len(tutorial_chapters) > max(3, suggested_count * params.CHAPTER_COUNT_REVIEW_RATIO)
    ):
        issues.append(
            f"This plan has {len(tutorial_chapters)} tutorial chapters against a soft estimate of "
            f"{suggested_count}. Recheck for compatible objectives that can be merged across source files; "
            "keep a larger count only where distinct learner abilities or non-composable code paths require it. "
            "This is a review signal, not a hard maximum."
        )
    excluded_without_reason = [
        chapter.get("title") or "(untitled)"
        for chapter in chapters
        if chapter.get("synth_eligible", True) is False
        and not " ".join(str(chapter.get("synth_exclusion_reason") or "").split())
    ]
    if excluded_without_reason:
        issues.append(f"Explain every Synth exclusion with a specific reason: {excluded_without_reason[:8]}.")
    eligible_code = {
        fact.get("hash")
        for chapter in chapters
        if chapter.get("synth_eligible", True) is not False
        for group_id in chapter.get("group_ids") or []
        for fact in groups.get(group_id, {}).get("best_code") or []
        if fact.get("hash")
    }
    excluded_code = {
        fact.get("hash")
        for chapter in chapters
        if chapter.get("synth_eligible", True) is False
        for group_id in chapter.get("group_ids") or []
        for fact in groups.get(group_id, {}).get("best_code") or []
        if fact.get("hash")
    }
    uncovered_code = sorted(excluded_code - eligible_code)
    if uncovered_code:
        issues.append(
            "Keep every unique best-code family in a Synth-eligible chapter; exclusions may repeat only code taught elsewhere. "
            f"Uncovered families: {uncovered_code[:8]}."
        )
    # Preview the DAG repair; a cycle is a design error worth one targeted re-ask.
    _, stats = domain.reconcile_design(answer, candidates, cards)
    if stats.get("n_prerequisite_cycle_edges_dropped"):
        issues.append("Remove prerequisite cycles; keep only real dependencies and use exact chapter titles.")
    if stats.get("n_synth_concept_exclusion_overrides"):
        issues.append(
            "Do not exclude a chapter with a source-card teaching point that is not taught by another eligible chapter; "
            "keep it eligible unless the learning content is genuinely redundant."
        )
    return issues


def _code_ledger_facts(ledger, page_order: dict[str, int], tutorial_keys: set[str]) -> tuple[dict[str, int], dict[str, list[dict]]]:
    count_by_page: dict[str, int] = defaultdict(int)
    facts_by_page: dict[str, list[dict]] = defaultdict(list)
    for digest in ledger.must_teach:
        block = ledger.blocks[digest]
        owners = sorted((key for key in block.pages if key in tutorial_keys), key = lambda key: page_order.get(key, 1 << 30))
        if not owners:
            continue
        owner = owners[0]
        count_by_page[owner] += 1
        facts_by_page[owner].append({
            "hash": digest,
            "page_key": owner,
            "language": block.lang,
            "lines": block.lines,
            "defines": list(block.defines[:4]),
            "imports": list(block.imports[:4]),
            "pages": len(block.pages),
        })
    return dict(count_by_page), dict(facts_by_page)


async def _embed(context: Context, cards: list[dict]) -> tuple[list, str]:
    texts = [domain.card_text(card) for card in cards]
    if context.embed_texts and texts:
        try:
            vectors = []
            model = ""
            for start in range(0, len(texts), params.EMBED_BATCH):
                batch, model = await context.embed_texts(texts[start:start + params.EMBED_BATCH])
                vectors.extend(batch)
            normalized = domain.normalize_embeddings(vectors, len(cards))
            if normalized is not None:
                return normalized, model
            logger.warning("[chapter_design] embedding count or dimensions did not match the cards; using local TF-IDF")
        except Exception as error:
            logger.warning(f"[chapter_design] embeddings unavailable; using local TF-IDF ({type(error).__name__}: {str(error)[:140]})")
    return domain.local_tfidf(cards), "local-tfidf"


async def _ask_designer(context: Context, payload: str, *, attempts: int = params.ATTEMPTS):
    return await agents.service.ask(
        context.agents.chapter_designer(),
        agents.pipeline.planner.chapter_designer.schemas.ChapterDesignAnswer,
        payload,
        attempts = attempts,
        wait_base_s = params.RETRY_WAIT_S,
        files = skills.service.files_for("chapter_designer"),
    )


async def chapter_design_run(state: graphs.pipeline.planner.states.PlannerState, runtime: Runtime[Context]) -> dict:
    context = runtime.context
    blobs = context.blobs
    slug = state.get("framework_slug") or ""
    card_ref = state.get("page_cards_ref") or ""
    relevant_files = list(state.get("relevant_files") or [])
    if not slug or not card_ref:
        return {
            "chapter_proposals_ref": None, "chapter_doc_assignments_ref": None,
            "chapter_design_stats": {"skipped": "missing_page_cards"},
        }
    started = time.monotonic()
    graphs.service.emit(params.STEP, "start", n_pages = len(relevant_files))
    try:
        card_payload = json.loads(await blobs.read_text(card_ref)) or {}
        all_cards = card_payload.get("cards") or {}
    except Exception as error:
        raise RuntimeError(f"Planner v2 cannot read page-card artifact {card_ref!r}: {type(error).__name__}: {error}") from error
    cards = [dict(all_cards[key]) for key in relevant_files if isinstance(all_cards.get(key), dict)]
    if not cards:
        return {
            "chapter_proposals_ref": None, "chapter_doc_assignments_ref": None,
            "chapter_design_stats": {"skipped": "no_kept_pages"},
        }

    page_order = _page_order(cards)
    tutorial_keys = {card["key"] for card in cards if card.get("kind") != "reference"}
    try:
        ledger = await domains.dd.pipeline.ledger.service.build(blobs, slug, [key for key in relevant_files if key in tutorial_keys])
        must_teach_by_page, best_code_by_page = _code_ledger_facts(ledger, page_order, tutorial_keys)
        total_minutes = domains.dd.pipeline.ledger.domain.tutorial_minutes(ledger)
    except Exception as error:
        logger.warning(f"[chapter_design] {slug}: code ledger unavailable ({type(error).__name__}: {str(error)[:160]})")
        must_teach_by_page, best_code_by_page = {}, {}
        ledger = None
        total_minutes = 0.0
    for card in cards:
        card["n_best_code"] = must_teach_by_page.get(card["key"], 0)
        card["best_code"] = best_code_by_page.get(card["key"], [])
        code_lines = sum(int(fact.get("lines") or 0) for fact in card["best_code"])
        card["tutorial_example_minutes"] = domains.dd.pipeline.ledger.domain.reading_minutes(
            domains.dd.pipeline.ledger.params.WORDS_PER_STEP * card["n_best_code"],
            code_lines,
        )
    vectors, embedding_model = await _embed(context, cards)
    candidates = domain.build_candidate_partitions(
        cards, vectors, total_minutes = total_minutes,
        must_teach_by_page = must_teach_by_page,
        best_code_by_page = best_code_by_page,
        chapter_overhead_minutes = domains.dd.pipeline.ledger.domain.reading_minutes(
            domains.dd.pipeline.ledger.params.INTRO_WORDS,
            domains.dd.pipeline.ledger.params.CAPSTONE_LINES_ESTIMATE,
        ),
    )
    if not candidates.get("partitions"):
        raise RuntimeError(f"Planner v2 produced no candidate partition for {slug!r}")

    request_data = {
        "task": "Choose and refine the chapter grouping for this documentation corpus.",
        "framework": slug,
        "count_policy": {
            "unique_tutorial_minutes": candidates["unique_tutorial_minutes"],
            "target_minutes": params.TARGET_MINUTES,
            "suggested_tutorial_chapters": candidates["target_chapters"],
            "chapter_count_basis": candidates["target_basis"],
            "maximum_minutes_soft": params.MAX_MINUTES,
            "quality_precedes_reading_time": True,
            "chapter_count_is_soft_hint_only": True,
            "chapter_setup_and_navigation_overhead_matters": True,
            "no_fixed_chapter_count_maximum": True,
        },
        "cards": [
            {
                "key": card["key"],
                "title": card.get("title") or "",
                "kind": card.get("kind"),
                "topic": card.get("topic"),
                "summary": card.get("summary"),
                "teaches": card.get("teaches") or [],
                "prerequisites": card.get("prerequisites") or [],
                "n_best_code": int(card.get("n_best_code") or 0),
            }
            for card in cards
        ],
        "best_code_inventory": list({
            fact["hash"]: {
                key: fact[key] for key in ("hash", "page_key", "language", "lines", "defines", "imports", "pages")
                if key in fact
            }
            for page_facts in best_code_by_page.values()
            for fact in page_facts
        }.values()),
        "candidate_partitions": _model_partitions(candidates),
    }
    request = json.dumps(request_data, ensure_ascii = False)
    design_answer = None
    designer_errors = []
    designer_review_flags = []
    if not (state.get("page_cards_stats") or {}).get("n_fallback_cards", 0):
        await graphs.service.settle(params.STEP, params.SETTLE_DELAY_S)
    try:
        answer = await _ask_designer(context, request)
        design_answer = answer.model_dump()
        designer_review_flags = _answer_issues(design_answer, candidates, cards)
        if designer_review_flags:
            repair_request = request + "\n\nYour previous design needs these repairs:\n- " + "\n- ".join(designer_review_flags)
            try:
                answer = await _ask_designer(context, repair_request)
                design_answer = answer.model_dump()
                designer_review_flags = _answer_issues(design_answer, candidates, cards)
            except Exception as error:
                designer_errors.append(f"repair: {type(error).__name__}: {str(error)[:160]}")
    except Exception as error:
        designer_errors.append(f"design: {type(error).__name__}: {str(error)[:160]}")
        logger.warning(f"[chapter_design] {slug}: architect fallback ({type(error).__name__}: {str(error)[:180]})")

    chapters, reconciliation = domain.reconcile_design(design_answer, candidates, cards)
    if not chapters:
        # The highest-scoring deterministic partition remains a complete, auditable fallback.
        fallback_answer = None
        chapters, reconciliation = domain.reconcile_design(fallback_answer, candidates, cards)
        reconciliation["fallback_used"] = True
        reconciliation["fallback_reason"] = "designer returned no usable chapters"
    if not chapters:
        raise RuntimeError(f"Planner v2 could not form any chapter groups for {slug!r}")

    assignments: dict[str, list[dict]] = {}
    proposals = []
    for chapter_index, chapter in enumerate(chapters):
        proposal = {
            "title": chapter["title"],
            "description": chapter["description"] or chapter["objective"],
            "key_concepts": chapter.get("key_concepts") or [],
            "objective": chapter.get("objective") or "",
            "prerequisites": chapter.get("prerequisites") or [],
            "kind": chapter.get("kind") or "tutorial",
            "role": chapter.get("role"),
            "capstone_ability": bool(chapter.get("capstone_ability")),
            "capstone_reason": chapter.get("capstone_reason") or "",
            "synth_eligible": bool(chapter.get("synth_eligible", True)),
            "synth_exclusion_reason": chapter.get("synth_exclusion_reason") or "",
            "n_best_code": int(chapter.get("n_best_code") or 0),
            "best_code_ids": sorted({
                fact["hash"]
                for key in chapter.get("member_doc_keys") or []
                for fact in best_code_by_page.get(key, [])
                if fact.get("hash")
            }),
            "reading_minutes": float(chapter.get("reading_minutes") or 0.0),
            "tutorial_minutes": float(chapter.get("tutorial_minutes") or 0.0),
            "origin": "v2_candidate",
        }
        proposals.append(proposal)
        for key in chapter.get("member_doc_keys") or []:
            assignments[key] = [{"chapter_idx": chapter_index, "confidence": 1.0}]

    missing_pages = sorted(set(relevant_files) - set(assignments))
    if missing_pages:
        # A final page-accounting repair attaches each uncovered page to the nearest title/card by lexical evidence.
        card_by_key = {card["key"]: card for card in cards}
        for key in missing_pages:
            page = card_by_key.get(key) or {}
            text = domain.tokens(domain.card_text(page))
            best_idx = max(
                range(len(proposals)),
                key = lambda index: len(set(text) & set(domain.tokens(" ".join([
                    proposals[index]["title"], proposals[index]["description"], *proposals[index]["key_concepts"],
                ])))),
            )
            assignments[key] = [{"chapter_idx": best_idx, "confidence": 0.2}]
            proposals[best_idx]["n_best_code"] += int(page.get("n_best_code") or 0)
            proposals[best_idx]["best_code_ids"] = sorted(set(
                proposals[best_idx].get("best_code_ids") or []
            ) | {
                fact["hash"] for fact in best_code_by_page.get(key, []) if fact.get("hash")
            })
            proposals[best_idx]["reading_minutes"] += float(page.get("reading_minutes") or 0.0)

    manifest = sha256(json.dumps({
        "cards": card_ref, "candidate": candidates, "design": design_answer,
        "version": versions.PROMPT_VERSION,
    }, sort_keys = True, ensure_ascii = False).encode("utf-8")).hexdigest()[:20]
    proposal_payload = {
        "prompt_version": versions.PROMPT_VERSION,
        "planner_version": "v2",
        "manifest_hash": manifest,
        "proposals": proposals,
        "best_code_by_page": {
            key: [fact["hash"] for fact in facts if fact.get("hash")]
            for key, facts in best_code_by_page.items()
        },
        "candidate_metrics": candidates,
        "designer_errors": designer_errors,
        "designer_review_flags": designer_review_flags,
    }
    assignment_payload = {
        "prompt_version": versions.PROMPT_VERSION,
        "planner_version": "v2",
        "manifest_hash": manifest,
        "assignments": assignments,
    }
    storage_keys = domains.dd.pipeline.storage.keys
    proposal_v = storage_keys.proposals_versioned_key(slug, manifest)
    assignment_v = storage_keys.assignments_versioned_key(slug, manifest)
    await asyncio.gather(
        domains.dd.pipeline.storage.service.write_blob(
            blobs, proposal_v, storage_keys.proposals_latest_key(slug), proposal_payload,
        ),
        domains.dd.pipeline.storage.service.write_blob(
            blobs, assignment_v, storage_keys.assignments_latest_key(slug), assignment_payload,
        ),
    )
    n_assigned = len(assignments)
    stats = {
        **reconciliation,
        "planner_version": "v2",
        "manifest_hash": manifest,
        "n_pages_in": len(relevant_files),
        "n_pages_assigned": n_assigned,
        "n_unassigned": len(set(relevant_files) - set(assignments)),
        "n_chapters": len(proposals),
        "n_best_code": len(ledger.must_teach) if ledger else 0,
        "ledger_available": ledger is not None,
        "unique_tutorial_minutes": round(total_minutes, 1),
        "suggested_chapters": candidates["target_chapters"],
        "synth_eligible_chapters": sum(bool(chapter.get("synth_eligible", True)) for chapter in proposals),
        "synth_excluded_chapters": sum(not bool(chapter.get("synth_eligible", True)) for chapter in proposals),
        "chapter_count_basis": candidates["target_basis"],
        "embedding_model": embedding_model,
        "n_partitions_scored": len(candidates["partitions"]),
        "candidate_scores": [
            {"id": item["id"], **item["metrics"]} for item in candidates["partitions"]
        ],
        "designer_errors": designer_errors,
        "designer_review_flags": designer_review_flags,
        "wall_ms": graphs.service.elapsed_ms(started),
        "chapter_titles": [chapter["title"] for chapter in chapters],
    }
    graphs.service.emit(
        params.STEP, "done",
        n_chapters = len(proposals), n_pages = n_assigned, n_best_code = stats["n_best_code"],
        n_unassigned = stats["n_unassigned"], wall_ms = stats["wall_ms"],
    )
    logger.info(
        f"[chapter_design] {slug}: {len(proposals)} chapters, {n_assigned}/{len(relevant_files)} pages, "
        f"{stats['n_best_code']} best-code examples, {embedding_model}, {stats['wall_ms']} ms"
    )
    return {
        "planner_version": "v2",
        "chapter_proposals_ref": proposal_v,
        "chapter_doc_assignments_ref": assignment_v,
        "chapter_design_stats": stats,
        "propose_stats": {
            "planner_version": "v2",
            "n_proposals": len(proposals),
            "titles": stats["chapter_titles"],
            "fallback_used": bool(reconciliation.get("fallback_used")),
            "review_flags": designer_review_flags,
        },
        "assign_stats": {
            "n_docs": len(relevant_files),
            "n_assigned": n_assigned,
            "n_fallback": 0,
            "n_rescued": stats["n_missing_groups_repaired"],
        },
    }
