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
        completed = []
        for ref in state.get("chapter_batch", []):
            output = await service.synthesize_chapter(dependencies, {**state, "chapter_ref": ref})
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
