"""Chapter and whole-book synthesis over independently verified learning units."""
from __future__ import annotations
import domains
from . import domain
from .... import graphs, prompts

import json
import logging
from typing import Any


logger = logging.getLogger(__name__)

def _json_body(value: Any) -> str:
    return json.dumps(value, ensure_ascii = False, separators = (",", ":"), sort_keys = True)


async def _synthesize_chapter_with_llm(
    dependencies: graphs.distill.entities.DistillDependencies,
    run_id: str,
    prompt: str,
) -> domains.bd.distill.schemas.ChapterSynthesis:
    controls = dependencies.controls
    async with controls.chapter_synthesis_semaphore:
        async with controls.chapter_settle_lock:
            if not controls.chapter_pool_settled:
                domains.settings.chat.service.refresh_endpoint()
                endpoint = domains.settings.chat.service.ENDPOINT
                if domains.settings.chat.domain.rotator_health_url(endpoint.base_url):
                    settled = await domains.settings.chat.service.wait_for_rotator_settle(
                        domains.bd.distill.params.CHAPTER_SYNTHESIS_SETTLE_CAP_S,
                    )
                    logger.info("[bd-distill] chapter synthesis Rotator settle: %s", settled)
                controls.chapter_pool_settled = True
        try:
            with domains.settings.llm_counter.service.llm_context(
                ns = "bd", run_id = run_id, node_id = "chapter_synthesizer", stage = "distill",
            ):
                synthesis, _ = await domains.settings.chat.service.chat_structured_async(
                    prompt,
                    domains.bd.distill.schemas.ChapterSynthesis,
                    max_tokens = 7000,
                    temperature = 0.0,
                    timeout_s = domains.bd.distill.params.CHAPTER_SYNTHESIS_TIMEOUT_S,
                    strict = False,
                    rotator_task = "bd_distill_chapter_synthesizer",
                )
        except Exception as error:
            if domains.settings.chat.domain.is_transient_provider_error(error):
                async with controls.chapter_settle_lock:
                    controls.chapter_pool_settled = False
            raise
    return synthesis


async def read_learning_units(
    storage,
    book_id: str,
    run_id: str,
    unit_ids: list[str],
    unit_sources: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Resolve lesson IDs through their persisted source-chunk artifacts.

    Older chapter checkpoints only carry lesson IDs, so unresolved IDs are
    recovered by scanning the run's immutable source-chunk artifacts once.
    """
    requested_ids = list(dict.fromkeys(unit_ids))
    wanted = set(requested_ids)
    records_by_id: dict[str, dict[str, Any]] = {}
    source_by_lesson = {
        item["learning_unit_id"]: item["source_unit_id"]
        for item in unit_sources or []
        if isinstance(item, dict)
        and isinstance(item.get("learning_unit_id"), str)
        and isinstance(item.get("source_unit_id"), str)
        and item["learning_unit_id"] in wanted
    }
    loaded_source_keys: set[str] = set()

    async def load_source_artifact(key: str) -> None:
        artifact = json.loads(await storage.read_text(key))
        for lesson in artifact.get("lessons", []):
            lesson_id = lesson.get("learning_unit_id") if isinstance(lesson, dict) else None
            if lesson_id in wanted:
                records_by_id[lesson_id] = {
                    **lesson,
                    "source_page_number": artifact.get("page_number"),
                    "section_path": artifact.get("section_path", []),
                }

    for source_unit_id in dict.fromkeys(source_by_lesson.values()):
        source_key = domains.bd.distill.keys.learning_unit_key(book_id, run_id, source_unit_id)
        loaded_source_keys.add(source_key)
        await load_source_artifact(source_key)

    missing_ids = wanted - records_by_id.keys()
    if missing_ids:
        prefix = f"{domains.bd.distill.keys.run_prefix(book_id, run_id)}learning-units/"
        for key in await storage.list(prefix):
            if key in loaded_source_keys:
                continue
            await load_source_artifact(key)
            missing_ids = wanted - records_by_id.keys()
            if not missing_ids:
                break

    if missing_ids:
        missing = ", ".join(sorted(missing_ids)[:5])
        raise ValueError(f"chapter learning units have no persisted source artifact: {missing}")
    return [records_by_id[unit_id] for unit_id in requested_ids]


async def prepare_chapters(
    dependencies: graphs.distill.entities.DistillDependencies,
    state: dict[str, Any],
) -> dict[str, Any]:
    storage = dependencies.storage
    refs = []
    for ref in state.get("completed_units", []):
        artifact_key = domains.bd.distill.keys.learning_unit_key(state["book_id"], state["run_id"], ref["unit_id"])
        artifact = json.loads(await storage.read_text(artifact_key))
        unit_ids = [item["learning_unit_id"] for item in artifact.get("lessons", [])]
        if unit_ids:
            refs.append({**ref, "learning_unit_ids": unit_ids})
    refs.sort(key = lambda ref: (ref["page_number"], ref["char_start"], ref["unit_id"]))
    source_chapters = state.get("source_chapters", [])
    groups: list[dict[str, Any]] = []
    assigned_unit_ids: set[str] = set()
    if isinstance(source_chapters, list):
        for chapter in sorted(
            (item for item in source_chapters if isinstance(item, dict)),
            key = lambda item: item.get("first_page", 0),
        ):
            title = chapter.get("title")
            first_page = chapter.get("first_page")
            last_page = chapter.get("last_page")
            if (
                not isinstance(title, str)
                or type(first_page) is not int
                or type(last_page) is not int
                or first_page < 1
                or last_page < first_page
            ):
                continue
            selected_refs = [
                ref for ref in refs
                if first_page <= ref["page_number"] <= last_page and ref["unit_id"] not in assigned_unit_ids
            ]
            if not selected_refs:
                continue
            groups.append({
                "title": title,
                "first_page": first_page,
                "last_page": last_page,
                "section_path": [title],
                "refs": selected_refs,
            })
            assigned_unit_ids.update(ref["unit_id"] for ref in selected_refs)

    unassigned_by_root: dict[str, list[dict[str, Any]]] = {}
    for ref in refs:
        if ref["unit_id"] in assigned_unit_ids:
            continue
        path = [item.strip() for item in ref.get("section_path", []) if item.strip()]
        title = path[0] if path else "Unassigned material"
        unassigned_by_root.setdefault(title, []).append(ref)
    for title, section_refs in sorted(
        unassigned_by_root.items(), key = lambda item: min(ref["page_number"] for ref in item[1]),
    ):
        groups.append({
            "title": title,
            "first_page": min(ref["page_number"] for ref in section_refs),
            "last_page": max(ref["page_number"] for ref in section_refs),
            "section_path": [title],
            "refs": section_refs,
        })

    chapter_refs = []
    for group in sorted(groups, key = lambda item: (item["first_page"], item["last_page"])):
        title = group["title"]
        section_path = tuple(group["section_path"])
        section_refs = group["refs"]
        selected: list[str] = []
        selected_refs_by_unit: dict[str, dict[str, Any]] = {}
        batch_index = 0
        for ref in section_refs:
            for unit_id in ref.get("learning_unit_ids", []):
                selected.append(unit_id)
                selected_refs_by_unit[unit_id] = ref
                if len(selected) == domains.bd.distill.params.MAX_CHAPTER_UNITS_PER_REQUEST:
                    subsection_paths = domain.subsection_paths(selected, selected_refs_by_unit)
                    chapter_refs.append(await _write_chapter_input(
                        storage, state["book_id"], state["run_id"], title, section_path,
                        group["first_page"], group["last_page"], subsection_paths, batch_index, selected,
                        [
                            {
                                "learning_unit_id": unit_id,
                                "source_unit_id": selected_refs_by_unit[unit_id]["unit_id"],
                            }
                            for unit_id in selected
                        ],
                    ))
                    selected = []
                    selected_refs_by_unit = {}
                    batch_index += 1
        if selected:
            subsection_paths = domain.subsection_paths(selected, selected_refs_by_unit)
            chapter_refs.append(await _write_chapter_input(
                storage, state["book_id"], state["run_id"], title, section_path,
                group["first_page"], group["last_page"], subsection_paths, batch_index, selected,
                [
                    {
                        "learning_unit_id": unit_id,
                        "source_unit_id": selected_refs_by_unit[unit_id]["unit_id"],
                    }
                    for unit_id in selected
                ],
            ))
    if not chapter_refs:
        raise ValueError("no source-grounded learning units were extracted from the completed book")
    await graphs.service.report(
        dependencies.progress_callback,
        kind = "chapter_plan",
        total_chapters = len(chapter_refs),
    )
    return {"chapter_refs": chapter_refs}


async def _write_chapter_input(
    storage,
    book_id: str,
    run_id: str,
    title: str,
    section_path: tuple[str, ...],
    first_page: int,
    last_page: int,
    subsection_paths: list[list[str]],
    batch: int,
    unit_ids: list[str],
    unit_sources: list[dict[str, str]],
) -> dict[str, Any]:
    structure_id = domains.bd.distill.domain.stable_id(
        title, str(first_page), str(last_page),
    )
    chapter_id = domains.bd.distill.domain.stable_id(structure_id, str(batch), *unit_ids)
    input_key = domains.bd.distill.keys.chapter_input_key(book_id, run_id, chapter_id)
    record = {
        "chapter_id": chapter_id,
        "structure_id": structure_id,
        "section_path": list(section_path),
        "first_page": first_page,
        "last_page": last_page,
        "subsection_paths": subsection_paths,
        "part_index": batch,
        "title": title if batch == 0 else f"{title} — Part {batch + 1}",
        "unit_ids": list(unit_ids),
        "unit_sources": unit_sources,
    }
    if await storage.exists(input_key):
        existing = json.loads(await storage.read_text(input_key))
        legacy_record = {key: value for key, value in record.items() if key != "unit_sources"}
        if existing != record and existing != legacy_record:
            raise ValueError("an immutable chapter-input key has conflicting learning units")
    else:
        await storage.write(input_key, _json_body(record), content_type = domains.bd.distill.params.JSON_TYPE)
    return {
        "chapter_id": chapter_id,
        "artifact_key": domains.bd.distill.keys.chapter_key(book_id, run_id, chapter_id),
        "section_path": list(section_path),
        "part_index": batch,
        "first_page": first_page,
        "last_page": last_page,
        "unit_ids": list(unit_ids),
        "unit_sources": unit_sources,
        "input_key": input_key,
    }


async def synthesize_chapter(
    dependencies: graphs.distill.entities.DistillDependencies,
    state: dict[str, Any],
    learning_units_by_id: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    storage = dependencies.storage
    ref = state["chapter_ref"]
    if await storage.exists(ref["artifact_key"]):
        artifact = json.loads(await storage.read_text(ref["artifact_key"]))
        if artifact.get("unit_ids") != ref["unit_ids"]:
            raise ValueError("an immutable chapter report key has conflicting learning-unit IDs")
        await graphs.service.report(
            dependencies.progress_callback,
            kind = "chapter",
            status = "done",
            chapter_id = ref["chapter_id"],
        )
        return {"completed_chapters": [ref]}

    chapter_input = json.loads(await storage.read_text(ref["input_key"]))
    if learning_units_by_id is None:
        units = await read_learning_units(
            storage,
            state["book_id"],
            state["run_id"],
            ref["unit_ids"],
            ref.get("unit_sources") or chapter_input.get("unit_sources"),
        )
    else:
        missing_ids = set(ref["unit_ids"]) - learning_units_by_id.keys()
        if missing_ids:
            raise ValueError("chapter synthesis did not receive all of its persisted learning units")
        units = [learning_units_by_id[unit_id] for unit_id in ref["unit_ids"]]
    prompt = (
        prompts.distill.chapter_synthesizer.prompts.SYSTEM_PROMPT
        + "\n\nWrite this section's learning guide. "
        + "Every generated lesson, overview, question, diagram node/edge, and equation "
        + "must use only the available learning_unit_ids.\n"
        + json.dumps({"section": chapter_input, "learning_units": units}, ensure_ascii = False)
    )
    synthesis = await _synthesize_chapter_with_llm(dependencies, state["run_id"], prompt)
    allowed_ids = set(ref["unit_ids"])
    referenced_ids = domain.chapter_required_text_ids(synthesis)
    if referenced_ids - allowed_ids:
        raise ValueError("chapter synthesis referenced a learning unit outside its input section")

    visual_issues = domain.visual_evidence_issues(synthesis, units)
    for _ in range(domains.bd.distill.params.CHAPTER_VISUAL_REPAIR_ATTEMPTS):
        if not visual_issues:
            break
        repair_prompt = (
            prompt
            + "\n\nCorrection required: the previous response included visual evidence that did not "
            + "match the cited learning units. Return a complete corrected chapter guide. "
            + "Copy each visual evidence quote and page number from the supplied learning-unit "
            + "evidence exactly, using no more than eight quotes per visual item; omit any diagram, "
            + "equation, or chart that cannot meet this requirement. "
            + "Validation findings: "
            + json.dumps([
                {"kind": kind, "title": title, "reason": reason}
                for kind, _, title, reason in visual_issues
            ], ensure_ascii = False)
            + "\nPrevious response: "
            + json.dumps(synthesis.model_dump(mode = "json"), ensure_ascii = False)
        )
        synthesis = await _synthesize_chapter_with_llm(dependencies, state["run_id"], repair_prompt)
        referenced_ids = domain.chapter_required_text_ids(synthesis)
        if referenced_ids - allowed_ids:
            raise ValueError("chapter synthesis referenced a learning unit outside its input section")
        visual_issues = domain.visual_evidence_issues(synthesis, units)

    visual_review_flags: list[str] = []
    if visual_issues:
        synthesis, visual_review_flags = domain.omit_unsupported_visuals(synthesis, visual_issues)
        logger.warning(
            "[bd-distill] omitted unsupported chapter visuals chapter=%s items=%s",
            ref["chapter_id"],
            visual_review_flags,
        )
    artifact = {
        "chapter_id": ref["chapter_id"],
        "title": chapter_input["title"],
        "section_path": ref["section_path"],
        "unit_ids": ref["unit_ids"],
        "synthesis": synthesis.model_dump(mode = "json"),
        "visual_review_flags": visual_review_flags,
    }
    await storage.write(ref["artifact_key"], _json_body(artifact), content_type = domains.bd.distill.params.JSON_TYPE)
    await graphs.service.report(
        dependencies.progress_callback,
        kind = "chapter",
        status = "done",
        chapter_id = ref["chapter_id"],
    )
    return {"completed_chapters": [ref]}


async def synthesize_book(
    dependencies: graphs.distill.entities.DistillDependencies,
    state: dict[str, Any],
) -> dict[str, Any]:
    storage = dependencies.storage
    chapter_summaries = []
    ordered_refs = sorted(
        state.get("completed_chapters", []),
        key = lambda ref: (ref.get("first_page", 0), ref["part_index"]),
    )
    for ref in ordered_refs:
        artifact = json.loads(await storage.read_text(ref["artifact_key"]))
        chapter = domains.bd.distill.schemas.ChapterSynthesis.model_validate(artifact["synthesis"])
        chapter_summaries.append({
            "chapter_id": ref["chapter_id"],
            "title": artifact["title"],
            "section_path": artifact["section_path"],
            "first_page": artifact.get("first_page", 0),
            "last_page": artifact.get("last_page", 0),
            "unit_ids": artifact["unit_ids"],
            "summary": {
                "overview": chapter.overview[:1600],
                "overview_attribution": chapter.overview_attribution,
                "lessons": [
                    {
                        "title": lesson.title,
                        "explanation": lesson.explanation[:600],
                        "attribution": lesson.attribution,
                        "learning_unit_ids": lesson.learning_unit_ids,
                    }
                    for lesson in chapter.lessons[:8]
                ],
                "open_questions": [
                    {
                        "title": question.title,
                        "explanation": question.explanation[:400],
                        "learning_unit_ids": question.learning_unit_ids,
                    }
                    for question in chapter.open_questions[:4]
                ],
            },
        })
    if not chapter_summaries:
        raise ValueError("book synthesis has no completed chapter guides")

    current = chapter_summaries
    level = 0
    while True:
        batches = [
            current[index:index + domains.bd.distill.params.MAX_BOOK_SYNTHESIS_BATCH_SIZE]
            for index in range(0, len(current), domains.bd.distill.params.MAX_BOOK_SYNTHESIS_BATCH_SIZE)
        ]
        next_level = []
        for batch_index, batch in enumerate(batches):
            key = domains.bd.distill.keys.synthesis_key(state["book_id"], state["run_id"], level, batch_index)
            if await storage.exists(key):
                record = json.loads(await storage.read_text(key))
                if record.get("inputs") != [item.get("chapter_id", item.get("synthesis_id")) for item in batch]:
                    raise ValueError("a persisted whole-book synthesis batch has different inputs")
                synthesis = domains.bd.distill.schemas.BookSynthesis.model_validate(record["synthesis"])
                self_ids = set(record["unit_ids"])
                referenced_ids = set(synthesis.central_thesis_learning_unit_ids)
                for item in [*synthesis.key_takeaways, *synthesis.cross_chapter_connections, *synthesis.limitations]:
                    referenced_ids.update(item.learning_unit_ids)
                if referenced_ids - self_ids:
                    raise ValueError("a persisted whole-book synthesis contains unresolved source IDs")
                next_level.append({**record, "summary": domain.book_summary(synthesis)})
                continue

            unit_ids = sorted({unit_id for item in batch for unit_id in item["unit_ids"]})
            synthesis_scope = "whole_book" if len(batches) == 1 else "partial_book_batch"
            prompt = (
                prompts.distill.book_synthesizer.prompts.SYSTEM_PROMPT
                + "\n\nSynthesize the provided section guides. Every central thesis, takeaway, connection, and limitation must cite supplied learning-unit IDs.\n"
                + json.dumps({"scope": synthesis_scope, "section_guides": batch}, ensure_ascii = False)
            )
            with domains.settings.llm_counter.service.llm_context(
                ns = "bd", run_id = state["run_id"], node_id = "book_synthesizer", stage = "distill",
            ):
                synthesis, _ = await domains.settings.chat.service.chat_structured_async(
                    prompt,
                    domains.bd.distill.schemas.BookSynthesis,
                    max_tokens = 6000,
                    temperature = 0.0,
                    timeout_s = domains.bd.distill.params.BOOK_SYNTHESIS_TIMEOUT_S,
                    strict = False,
                    rotator_task = "bd_distill_book_synthesizer",
                )
            referenced_ids = set(synthesis.central_thesis_learning_unit_ids)
            for item in [*synthesis.key_takeaways, *synthesis.cross_chapter_connections, *synthesis.limitations]:
                referenced_ids.update(item.learning_unit_ids)
            if referenced_ids - set(unit_ids):
                raise ValueError("whole-book synthesis referenced a learning unit outside its supplied guides")
            record = {
                "synthesis_id": f"{level}-{batch_index}",
                "inputs": [item.get("chapter_id", item.get("synthesis_id")) for item in batch],
                "unit_ids": unit_ids,
                "synthesis": synthesis.model_dump(mode = "json"),
            }
            await storage.write(key, _json_body(record), content_type = domains.bd.distill.params.JSON_TYPE)
            next_level.append({**record, "summary": domain.book_summary(synthesis)})
        if len(next_level) == 1:
            return {"book_synthesis_key": domains.bd.distill.keys.synthesis_key(
                state["book_id"], state["run_id"], level, 0,
            )}
        current = next_level
        level += 1
        if level > 8:
            raise ValueError("whole-book synthesis exceeded the hierarchy depth limit")


async def synthesize_chapter_batch(
    dependencies: graphs.distill.entities.DistillDependencies,
    state: dict[str, Any],
) -> dict[str, Any]:
    """Synthesize every chapter assigned to one fan-out graph invocation."""
    chapter_batch = state.get("chapter_batch", [])
    pending_refs = []
    for ref in chapter_batch:
        if not await dependencies.storage.exists(ref["artifact_key"]):
            pending_refs.append(ref)

    learning_units_by_id = None
    if pending_refs:
        unit_ids = list(dict.fromkeys(
            unit_id
            for ref in pending_refs
            for unit_id in ref["unit_ids"]
        ))
        unit_sources = [
            source
            for ref in pending_refs
            for source in ref.get("unit_sources", [])
        ]
        units = await read_learning_units(
            dependencies.storage,
            state["book_id"],
            state["run_id"],
            unit_ids,
            unit_sources,
        )
        learning_units_by_id = dict(zip(unit_ids, units, strict = True))

    completed = []
    for ref in chapter_batch:
        output = await synthesize_chapter(
            dependencies,
            {**state, "chapter_ref": ref},
            learning_units_by_id = learning_units_by_id,
        )
        completed.extend(output.get("completed_chapters", []))
    return {"completed_chapters": completed}
