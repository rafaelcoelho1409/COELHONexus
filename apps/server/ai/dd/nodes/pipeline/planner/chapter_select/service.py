"""chapter_select steps — the nodes of its subgraph: prepare (the deterministic selection), a curator agent per oversize chapter, apply, finalize.

Selection is deterministic: greedy coverage → prune → consolidation (anchored-family harmonization included) → Foundations chapter
(`domain.select_pipeline`, shared with the offline replay). The one LLM step is the split of an oversize chapter (over the
corpus-share cap or the code-block cap): a chapter_curator agent groups the chapter's units with the clustering tool, names the
groups and checks its split with the validation tool before answering. That step is fail-soft: any error leaves the chapter exactly as
the deterministic pipeline produced it. The parts of a split that are still over the code-block cap are split again, up to SPLIT_MAX_ROUNDS."""
from __future__ import annotations
import domains
from . import domain, params, versions
from .. import page_cards
from ..... import agents, graphs, skills, tools

import json
import logging
import time

from langgraph.runtime import Runtime


logger = logging.getLogger(__name__)

Context = graphs.pipeline.planner.entities.PlannerContext


async def prepare(state: graphs.pipeline.planner.chapter_select.states.SelectState, runtime: Runtime[Context]) -> dict:
    context = runtime.context
    blobs = context.blobs
    slug = state.get("framework_slug")
    proposals_ref = state.get("chapter_proposals_ref")
    assignments_ref = state.get("chapter_doc_assignments_ref")
    planner_version = state.get("planner_version") or "v1"
    tutorial_mode = graphs.pipeline.params.TUTORIAL_MODE or planner_version == "v2"
    if not slug or not proposals_ref or not assignments_ref:
        return {"finished": True, "chapter_plan_ref": None, "select_stats": {"skipped": "missing_inputs"}}

    started = time.monotonic()
    manifest = domain.manifest_hash(
        slug = slug, proposals_ref = proposals_ref, assignments_ref = assignments_ref,
        tutorial = tutorial_mode, planner_version = planner_version,
    )
    if planner_version == "v2":
        try:
            blob = json.loads(await blobs.read_text(proposals_ref)) or {}
            assignment_payload = json.loads(await blobs.read_text(assignments_ref)) or {}
            assignments = assignment_payload.get("assignments") or {}
        except Exception as error:
            logger.warning(f"[chapter_select] {slug}: versioned v2 inputs unreadable ({type(error).__name__}: {error})")
            return {"finished": True, "chapter_plan_ref": None, "select_stats": {"skipped": "unreadable_v2_inputs"}}
    else:
        blob = await domains.dd.pipeline.storage.service.load_proposals(blobs, slug)
        assignments = await domains.dd.pipeline.storage.service.load_assignments(blobs, slug)
    proposals = blob.get("proposals") or []
    best_code_by_page = (blob.get("best_code_by_page") or {}) if planner_version == "v2" else None
    seeds = blob.get("seeds") or {}
    if not proposals:
        return {"finished": True, "chapter_plan_ref": None, "select_stats": {"skipped": "no_proposals"}}
    if not assignments:
        return {"finished": True, "chapter_plan_ref": None, "select_stats": {"skipped": "no_assignments"}}

    # Manifest (best-effort): parent-page family, title and source order per doc. Without it the pipeline degrades to per-doc placement.
    entries: list[dict] = []
    try:
        entries = ((await domains.dd.ingestion.storage.service.read_framework_manifest(blobs, slug)) or {}).get("entries") or []
    except Exception as error:
        logger.warning(f"[chapter_select] {slug}: manifest unavailable for family consolidation ({type(error).__name__}: {error}) — per-doc fallback only")
    doc_family = {e["key"]: (e.get("url") or "").split("#", 1)[0] for e in entries if e.get("key")}
    doc_title = {e["key"]: e.get("title") or "" for e in entries if e.get("key")}
    key_idx = {e["key"]: int(e.get("idx") or 0) for e in entries if e.get("key")}
    anchored_family = domain.anchored_family_map(entries)
    key_dir = domain.page_dirs(entries)
    card_payload = {}
    if planner_version == "v2" and state.get("page_cards_ref"):
        try:
            card_payload = json.loads(await blobs.read_text(state["page_cards_ref"])) or {}
            distillates = {
                key: page_cards.domain.to_distillate(card)
                for key, card in (card_payload.get("cards") or {}).items()
                if isinstance(card, dict) and card.get("keep")
            }
        except Exception as error:
            logger.warning(f"[chapter_select] {slug}: page cards unavailable for selector metadata ({type(error).__name__}: {error})")
            distillates = {}
    else:
        try:
            distillates = await domains.dd.pipeline.storage.service.load_distillates(blobs, slug)
        except Exception:
            distillates = {}

    graphs.service.emit(
        params.STEP, "start",
        n_proposals = len(proposals), n_docs = len(assignments), n_pinned = len(domain.detect_pinned_indices(proposals, seeds)),
    )
    if planner_version == "v2":
        # V2 chapter groups are already designed and assigned. Keep those boundaries
        # intact; the legacy selector's Foundation and directory consolidation rules
        # were designed for independent per-page scores and can undo an architect's plan.
        owners: dict[str, tuple[int, float]] = {}
        for key, rows in assignments.items():
            valid = []
            for row in rows or []:
                try:
                    chapter_idx = int(row["chapter_idx"])
                    confidence = float(row.get("confidence") or 0.0)
                except (KeyError, TypeError, ValueError):
                    continue
                if 0 <= chapter_idx < len(proposals):
                    valid.append((chapter_idx, confidence))
            if valid:
                owners[key] = max(valid, key = lambda item: (item[1], -item[0]))

        card_by_key = {}
        try:
            card_by_key = card_payload.get("cards") or {}
        except UnboundLocalError:
            pass
        all_keys = set(assignments) | set(state.get("relevant_files") or [])
        members: list[list[str]] = [[] for _ in proposals]
        for key, (chapter_idx, _confidence) in owners.items():
            members[chapter_idx].append(key)
        uncovered = sorted(all_keys - set(owners), key = lambda key: (key_idx.get(key, 1 << 30), key))
        for key in uncovered:
            card = card_by_key.get(key) or {}
            doc_text = " ".join([
                doc_title.get(key, ""), (distillates.get(key) or {}).get("summary", ""),
                " ".join((distillates.get(key) or {}).get("key_terms") or []),
                card.get("topic") or "",
            ])
            doc_tokens = domain._tokens(doc_text)
            target = max(
                range(len(proposals)),
                key = lambda index: (
                    len(doc_tokens & domain._slot_tokens(proposals[index])),
                    -len(members[index]), -index,
                ),
            )
            members[target].append(key)

        out_chapters = []
        for index, proposal in enumerate(proposals):
            keys = sorted(set(members[index]), key = lambda key: (key_idx.get(key, 1 << 30), key))
            if not keys:
                continue
            out_chapters.append({
                **proposal,
                "member_doc_keys": keys,
                "n_member_docs": len(keys),
                "order": len(out_chapters) + 1,
                "source_proposal_idx": index,
                "origin": proposal.get("origin") or "v2_candidate",
                "pinned": False,
            })
        placed = {key for chapter in out_chapters for key in chapter["member_doc_keys"]}
        result = {
            "stats": {
                "n_total_docs": len(all_keys),
                "n_assigned_docs": len(placed),
                "n_unplaced_final": len(all_keys - placed),
                "n_v2_uncovered_pages_repaired": len(uncovered),
                "n_chapters_preserved": len(out_chapters),
            },
            "pinned": set(),
            "pruned": [],
            "orphan_protected": [],
            "kept": list(range(len(out_chapters))),
            "out_chapters": out_chapters,
        }
    else:
        result = domain.select_pipeline(
            proposals = proposals, assignments = assignments, seeds = seeds, doc_family = doc_family, doc_title = doc_title,
            distillates = distillates, key_idx = key_idx, anchored_family = anchored_family, key_dir = key_dir,
        )
    consolidation = result["stats"]
    if consolidation["n_unplaced_final"]:
        logger.warning(f"[chapter_select] {slug}: {consolidation['n_unplaced_final']} doc(s) still unplaced after consolidation — they will be missing from the plan")
    if any(consolidation.get(k) for k in (
        "n_family_consolidated", "n_family_harmonized", "n_rehomed_to_family", "n_rescued_chapters", "n_gap_chapters",
        "n_leftover_placed", "n_leftover_appendix", "n_leftover_dir_owned", "n_foundations",
    )):
        logger.info(f"[chapter_select] {slug}: placement consolidation {consolidation}")

    out_chapters = result["out_chapters"]
    doc_blocks: dict[str, int] = {}
    try:
        member_keys = [k for c in out_chapters for k in c["member_doc_keys"]]
        bodies = await domains.dd.pipeline.storage.service.read_all(blobs, member_keys)
        doc_blocks = {k: domain.code_block_count(b) for k, b in zip(member_keys, bodies) if b}
    except Exception as error:
        logger.info(f"[chapter_select] {slug}: code-block counts unavailable, pages decide the split alone ({type(error).__name__}: {error})")

    max_blocks, blocks_per_part = domain.block_limits(tutorial_mode)
    plans = [
        {"pos": pos, "n_parts": k}
        for pos, k in domain.plan_splits(out_chapters, doc_blocks = doc_blocks, max_blocks = max_blocks, blocks_per_part = blocks_per_part)
    ]
    if plans:
        # The preceding node, chapter_assign, just burst the Rotator's pool: wait for it to recover before the curators.
        try:
            await graphs.service.settle(params.STEP, params.SPLIT_SETTLE_CAP_S)
        except Exception as error:
            logger.info(f"[chapter_select] settle wait skipped ({type(error).__name__}: {error})")

    context.scratch[params.STEP] = {
        "slug": slug,
        "planner_version": planner_version,
        "relevant_files": list(state.get("relevant_files") or []),
        "tutorial_mode": tutorial_mode,
        "proposals": proposals,
        "best_code_by_page": best_code_by_page,
        "assignments": assignments,
        "entries": entries,
        "chapters": out_chapters,
        "result": result,
        "consolidation": consolidation,
        "anchored_family": anchored_family,
        "doc_title": doc_title,
        "distillates": distillates,
        "key_idx": key_idx,
        "key_dir": key_dir,
        "doc_blocks": doc_blocks,
        "splits": [],
        "n_split_applied": 0,
    }
    return {"finished": False, "manifest_hash": manifest, "plans": plans, "round": 1, "started": started}


async def curate_chapter(state: graphs.pipeline.planner.chapter_select.entities.CurateInput, runtime: Runtime[Context]) -> dict:
    """Split one oversize chapter with a curator agent. Fail-soft: any problem leaves the chapter as it is."""
    context = runtime.context
    data = context.scratch[params.STEP]
    pos, k, round_ = state["pos"], state["n_parts"], state["round"]
    chapter = data["chapters"][pos]
    title = chapter["title"]
    try:
        units = domain.build_split_units(
            chapter["member_doc_keys"], anchored_family = data["anchored_family"], doc_title = data["doc_title"],
            distillates = data["distillates"], key_idx = data["key_idx"], key_dir = data["key_dir"],
        )
        if len(units) < 2:
            return {"outcomes": [{"round": round_, "pos": pos, "chapters": None, "reason": "fewer than 2 units"}]}
        other_titles = [c["title"] for c in data["chapters"] if c["title"] != title]
        taken = {t.casefold() for t in other_titles}
        texts = [domain.unit_text(u, data["distillates"]) for u in units]
        total = sum(len(u["keys"]) for u in units)
        max_parts = max(k, params.SPLIT_MAX_PARTS)

        def cluster(n_parts: int) -> list[list[int]] | None:
            return domain.cluster_units(units, texts, n_parts, domain.split_floor(total, n_parts))

        def check(groups: list[dict]) -> tuple[bool, str]:
            return domain.validate_split(domain.normalize_split(groups, units), units, taken, max_parts = max_parts)

        curator = context.agents.chapter_curator(tools = [
            tools.pipeline.planner.split.tool.cluster_units_tool(cluster, lambda ids: domain.describe_units(units, ids)),
            tools.pipeline.planner.split.tool.validate_split_tool(check),
        ])
        plan = await agents.service.ask(
            curator,
            agents.pipeline.planner.schemas.SplitPlan,
            domain.split_request(state["framework_slug"], chapter, units, k, other_titles),
            attempts = params.CURATOR_ATTEMPTS,
            wait_base_s = params.CURATOR_RETRY_WAIT_S,
            files = skills.service.files_for("chapter_curator"),
        )
        parts = domain.normalize_split([g.model_dump() for g in plan.groups], units)
        ok, reason = domain.validate_split(parts, units, taken, max_parts = max_parts)
        if ok:
            return {"outcomes": [{"round": round_, "pos": pos, "chapters": domain.apply_split(
                chapter, parts, units, data.get("best_code_by_page"),
            )}]}
        logger.info(f"[chapter_select] curator's split of {title!r} rejected: {reason}")

        # The curator could not produce a valid split: the computed clusters, named from their own words, still make one.
        floor = domain.split_floor(total, k)
        groups = domain.cluster_units(units, texts, k, floor)
        if groups:
            names = domain.fallback_group_titles(groups, texts, taken)
            parts = [{**name, "unit_ids": g} for name, g in zip(names, groups)]
            ok, reason = domain.validate_split(parts, units, taken, max_parts = len(groups), floor = floor)
            if ok:
                logger.info(f"[chapter_select] {title!r}: groups named from their words (the curator could not)")
                return {"outcomes": [{"round": round_, "pos": pos, "chapters": domain.apply_split(
                    chapter, parts, units, data.get("best_code_by_page"),
                )}]}
        return {"outcomes": [{"round": round_, "pos": pos, "chapters": None, "reason": reason}]}
    except Exception as error:
        logger.warning(f"[chapter_select] split of {title!r} skipped ({type(error).__name__}: {str(error)[:160]})")
        return {"outcomes": [{"round": round_, "pos": pos, "chapters": None, "reason": f"{type(error).__name__}: {str(error)[:120]}"}]}


def apply(state: graphs.pipeline.planner.chapter_select.states.SelectState, runtime: Runtime[Context]) -> dict:
    """Replace each split chapter by its parts, then plan the next round (parts still over the code-block cap)."""
    data = runtime.context.scratch[params.STEP]
    round_ = state.get("round") or 1
    sizes = {item["pos"]: item["n_parts"] for item in state.get("plans") or []}
    outcomes = [o for o in state.get("outcomes") or [] if o["round"] == round_]
    replaced = {o["pos"]: o["chapters"] for o in outcomes if o["chapters"]}
    split_title_aliases = {
        data["chapters"][pos]["title"]: parts[0]["title"]
        for pos, parts in replaced.items() if parts
    }
    for outcome in sorted(outcomes, key = lambda o: o["pos"]):
        chapter = data["chapters"][outcome["pos"]]
        data["splits"].append({
            "title": chapter["title"], "n_docs": len(chapter["member_doc_keys"]),
            "n_parts": sizes.get(outcome["pos"]), "applied": bool(outcome["chapters"]),
        })
    plans: list[dict] = []
    if replaced:
        rebuilt: list[dict] = []
        for i, chapter in enumerate(data["chapters"]):
            rebuilt.extend(replaced.get(i, [chapter]))
        aliases = {old.casefold(): new for old, new in split_title_aliases.items()}
        for chapter in rebuilt:
            chapter["prerequisites"] = [
                aliases.get(str(name).casefold(), name)
                for name in (chapter.get("prerequisites") or [])
            ]
        data["chapters"] = domain._renumber(rebuilt)
        data["n_split_applied"] += len(replaced)
        if round_ < params.SPLIT_MAX_ROUNDS:
            # Parts of this split that are still over the code-block cap are split again (the page rules do not apply to them).
            max_blocks, blocks_per_part = domain.block_limits(data["tutorial_mode"])
            plans = [
                {"pos": pos, "n_parts": k}
                for pos, k in domain.plan_splits(
                    data["chapters"], doc_blocks = data["doc_blocks"], blocks_only = True, max_blocks = max_blocks, blocks_per_part = blocks_per_part,
                )
            ]
    return {"plans": plans, "round": round_ + 1}


async def finalize(state: graphs.pipeline.planner.chapter_select.states.SelectState, runtime: Runtime[Context]) -> dict:
    context = runtime.context
    blobs = context.blobs
    data = context.scratch[params.STEP]
    slug = data["slug"]
    manifest = state["manifest_hash"]
    proposals = data["proposals"]
    assignments = data["assignments"]
    result = data["result"]
    out_chapters = data["chapters"]
    entries = data["entries"]
    consolidation = {**data["consolidation"], "n_split_planned": len(data["splits"]), "n_split_applied": data["n_split_applied"]}
    pinned, pruned, orphan_protected, kept = result["pinned"], result["pruned"], result["orphan_protected"], result["kept"]
    if data["splits"]:
        logger.info(f"[chapter_select] {slug}: oversize split {data['splits']}")

    if data.get("planner_version") != "v2":
        try:
            out_chapters, cat_stats = domain.harmonize_catalog_dirs(out_chapters, domain.page_dirs(entries), assignments)
            consolidation = {**consolidation, **cat_stats}
            if cat_stats["n_catalog_rehomed"]:
                logger.info(f"[chapter_select] {slug}: catalog directories pulled together {cat_stats}")
        except Exception as error:
            logger.warning(f"[chapter_select] {slug}: catalog harmonization skipped ({type(error).__name__}: {error})")
        try:
            out_chapters, mm_stats = domain.merge_micro_chapters(out_chapters, domain.page_dirs(entries))
            consolidation = {**consolidation, **mm_stats}
            if mm_stats["n_micro_merged"]:
                logger.info(f"[chapter_select] {slug}: micro-chapters merged into their directory's chapter {mm_stats}")
        except Exception as error:
            logger.warning(f"[chapter_select] {slug}: micro-chapter merge skipped ({type(error).__name__}: {error})")

    doc_to_chapter = {k: i for i, c in enumerate(out_chapters) for k in c["member_doc_keys"]}
    n_assigned_docs = len(doc_to_chapter)
    n_total_docs = len(set(assignments) | (
        set(data.get("relevant_files") or []) if data.get("planner_version") == "v2" else set()
    ))
    coverage = n_assigned_docs / n_total_docs if n_total_docs else 0.0

    select_payload = {
        "prompt_version":     versions.PROMPT_VERSION,
        "framework_slug":     slug,
        "manifest_hash":      manifest,
        "selected_indices":   kept,
        "splits":             data["splits"],
        "pruned_indices":     pruned,
        "pinned_indices":     sorted(pinned),
        "orphan_protected":   orphan_protected,
        "n_proposals_in":     len(proposals),
        "n_chapters_out":     len(out_chapters),
        "n_orphan_protected": len(orphan_protected),
        "consolidation":      consolidation,
        "n_assigned_docs":    n_assigned_docs,
        "n_total_docs":       n_total_docs,
        "coverage_fraction":  coverage,
        "chapters":           out_chapters,
    }
    plan_payload = {
        "prompt_version": versions.PROMPT_VERSION,
        "framework_slug": slug,
        "manifest_hash":  manifest,
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
                    **{
                        name: c[name]
                        for name in (
                            "objective", "prerequisites", "kind", "capstone_ability", "best_code_ids",
                            "capstone_reason", "synth_eligible", "synth_exclusion_reason", "n_best_code", "reading_minutes",
                            "tutorial_minutes",
                        )
                        if name in c
                    },
                }
                for c in out_chapters
            ],
        },
        "n_clusters_in":  len(proposals),
        "n_chapters_out": len(out_chapters),
        "n_repairs":      0,
        "forced_repair":  False,
        "source":         "planner_v2_chapter_design" if data.get("planner_version") == "v2" else "llm_first_chapter_select_v1",
    }
    keys = domains.dd.pipeline.storage.keys
    plan_lkey = keys.chapter_plan_latest_key(slug)
    await domains.dd.pipeline.storage.service.write_blob(
        blobs, keys.select_versioned_key(slug, manifest), keys.select_latest_key(slug), select_payload,
    )
    await domains.dd.pipeline.storage.service.write_blob(
        blobs, keys.chapter_plan_versioned_key(slug, manifest), plan_lkey, plan_payload,
    )

    wall_ms = graphs.service.elapsed_ms(state.get("started") or time.monotonic())
    stats = {
        "n_proposals_in":     len(proposals),
        "n_chapters_out":     len(out_chapters),
        "n_pruned":           len(pruned),
        "consolidation":      consolidation,
        "n_orphan_protected": len(orphan_protected),
        "n_assigned_docs":    n_assigned_docs,
        "n_total_docs":       n_total_docs,
        "coverage_fraction":  coverage,
        "wall_ms":            wall_ms,
        "manifest_hash":      manifest,
        "chapter_titles":     [c["title"] for c in out_chapters],
        "chapter_sizes":      [c["n_member_docs"] for c in out_chapters],
    }
    if orphan_protected:
        logger.info(f"[chapter_select] {slug}: orphan-protected {len(orphan_protected)} small chapter(s) whose members had no alternative above-threshold chapter: {orphan_protected}")
    graphs.service.emit(
        params.STEP, "done",
        n_chapters = len(out_chapters), n_pruned = len(pruned), n_orphan_protected = len(orphan_protected),
        coverage = coverage, wall_ms = wall_ms, titles = stats["chapter_titles"],
    )
    return {"chapter_plan_ref": plan_lkey, "select_stats": stats}
