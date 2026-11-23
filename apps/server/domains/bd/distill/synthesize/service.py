"""Prepare chapter groups and load persisted learning-unit evidence."""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

import domains


async def read_learning_units(
    storage: Any,
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


async def _write_chapter_input(
    storage: Any,
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
    source_figures: list[dict[str, Any]],
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
        "source_figures": source_figures,
    }
    if await storage.exists(input_key):
        existing = json.loads(await storage.read_text(input_key))
        legacy_record = {
            key: value for key, value in record.items() if key not in {"unit_sources", "source_figures"}
        }
        legacy_record_with_units = {key: value for key, value in record.items() if key != "source_figures"}
        if existing not in (record, legacy_record, legacy_record_with_units):
            raise ValueError("an immutable chapter-input key has conflicting learning units")
    else:
        await storage.write(input_key, domains.bd.distill.domain.json_body(record), content_type = domains.bd.distill.params.JSON_TYPE)
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


async def prepare_chapters(
    storage: Any,
    state: dict[str, Any],
    on_unit_progress: Callable[[str, str, int], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    refs = []
    completed_units = state.get("completed_units", [])
    total_units = len(completed_units)
    for ref in completed_units:
        if on_unit_progress is not None:
            await on_unit_progress(ref["unit_id"], "running", total_units)
        artifact_key = domains.bd.distill.keys.learning_unit_key(state["book_id"], state["run_id"], ref["unit_id"])
        artifact = json.loads(await storage.read_text(artifact_key))
        unit_ids = [item["learning_unit_id"] for item in artifact.get("lessons", [])]
        if unit_ids:
            refs.append({**ref, "learning_unit_ids": unit_ids})
        if on_unit_progress is not None:
            await on_unit_progress(ref["unit_id"], "done", total_units)
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
                "section_path": chapter.get("section_path")
                if isinstance(chapter.get("section_path"), list)
                and all(isinstance(item, str) for item in chapter["section_path"])
                and chapter["section_path"]
                else [title],
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
        source_figures = [
            {"figure_name": figure_name, "page_number": page["page_number"]}
            for page in state.get("source_pages", [])
            if group["first_page"] <= page.get("page_number", 0) <= group["last_page"]
            for figure_name in page.get("figures", [])
            if isinstance(figure_name, str) and figure_name
        ][:12]
        selected: list[str] = []
        selected_refs_by_unit: dict[str, dict[str, Any]] = {}
        batch_index = 0
        for ref in section_refs:
            for unit_id in ref.get("learning_unit_ids", []):
                selected.append(unit_id)
                selected_refs_by_unit[unit_id] = ref
                if len(selected) == domains.bd.distill.params.MAX_CHAPTER_UNITS_PER_REQUEST:
                    subsection_paths = domains.bd.distill.synthesize.domain.subsection_paths(selected, selected_refs_by_unit)
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
                        source_figures,
                    ))
                    selected = []
                    selected_refs_by_unit = {}
                    batch_index += 1
        if selected:
            subsection_paths = domains.bd.distill.synthesize.domain.subsection_paths(selected, selected_refs_by_unit)
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
                source_figures,
            ))
    if not chapter_refs:
        raise ValueError("no source-grounded learning units were extracted from the completed book")
    return {"chapter_refs": chapter_refs}


async def synthesize_chapter_batch(
    storage: Any,
    state: dict[str, Any],
    synthesize_chapter: Callable[
        [dict[str, Any], dict[str, dict[str, Any]] | None],
        Awaitable[dict[str, Any]],
    ],
) -> dict[str, Any]:
    """Load a fan-out batch's evidence and aggregate per-chapter completion results."""
    chapter_batch = state.get("chapter_batch", [])
    pending_refs = []
    for ref in chapter_batch:
        if not await storage.exists(ref["artifact_key"]):
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
            storage,
            state["book_id"],
            state["run_id"],
            unit_ids,
            unit_sources,
        )
        learning_units_by_id = dict(zip(unit_ids, units, strict = True))

    synthesized = []
    for ref in chapter_batch:
        output = await synthesize_chapter(
            {**state, "chapter_ref": ref},
            learning_units_by_id,
        )
        synthesized.extend(output.get("synthesized_chapters", []))
    return {"synthesized_chapters": synthesized}
