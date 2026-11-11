"""Thin graph wrappers for chapter and whole-book synthesis."""
from __future__ import annotations

from typing import Any

from .... import graphs
from . import service


async def prepare_chapters(dependencies: graphs.distill.entities.DistillDependencies, state: dict[str, Any]) -> dict:
    return await graphs.service.run_node(
        dependencies.progress_callback,
        "prepare_chapters",
        lambda: service.prepare_chapters(dependencies, state),
    )


async def synthesize_chapter(dependencies: graphs.distill.entities.DistillDependencies, state: dict[str, Any]) -> dict:
    async def _synthesize_batch() -> dict:
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
            units = await service.read_learning_units(
                dependencies.storage,
                state["book_id"],
                state["run_id"],
                unit_ids,
                unit_sources,
            )
            learning_units_by_id = dict(zip(unit_ids, units, strict = True))

        completed = []
        for ref in chapter_batch:
            output = await service.synthesize_chapter(
                dependencies,
                {**state, "chapter_ref": ref},
                learning_units_by_id = learning_units_by_id,
            )
            completed.extend(output.get("completed_chapters", []))
        return {"completed_chapters": completed}

    return await graphs.service.run_node(
        dependencies.progress_callback,
        "synthesize_chapter",
        _synthesize_batch,
    )


async def synthesize_book(dependencies: graphs.distill.entities.DistillDependencies, state: dict[str, Any]) -> dict:
    return await graphs.service.run_node(
        dependencies.progress_callback,
        "synthesize_book",
        lambda: service.synthesize_book(dependencies, state),
    )
