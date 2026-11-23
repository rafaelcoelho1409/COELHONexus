"""Thin LangGraph shell for bounded chapter coverage review."""
from __future__ import annotations

import asyncio

import domains
from . import service
from .... import graphs

from langgraph.runtime import Runtime


async def review_chapter_coverage(
    graph_state: graphs.distill.states.DistillRunState,
    runtime: Runtime[graphs.distill.entities.DistillDependencies],
) -> dict:
    dependencies = runtime.context

    async def execute() -> dict:
        refs_by_chapter: dict[str, dict] = {}
        for ref in graph_state.get("synthesized_chapters", []):
            if (
                not isinstance(ref, dict)
                or not isinstance(ref.get("chapter_id"), str)
                or not ref["chapter_id"]
            ):
                raise ValueError("chapter synthesis produced an invalid chapter reference")
            previous = refs_by_chapter.get(ref["chapter_id"])
            if previous is not None and (
                previous.get("artifact_key") != ref.get("artifact_key")
                or previous.get("input_key") != ref.get("input_key")
            ):
                raise ValueError(f"chapter {ref['chapter_id']!r} has conflicting synthesis references")
            refs_by_chapter.setdefault(ref["chapter_id"], ref)
        refs = sorted(
            refs_by_chapter.values(),
            key = lambda ref: (ref.get("first_page", 0), ref.get("part_index", 0), ref["chapter_id"]),
        )
        if not refs:
            raise ValueError("chapter synthesis produced no guide artifacts to review")
        total_count = len(refs)
        await graphs.service.report_node_work_plan(
            dependencies.progress_callback,
            "review_chapter_coverage",
            total_count,
        )
        semaphore = asyncio.Semaphore(
            domains.bd.distill.params.COVERAGE_REVIEW_CONCURRENCY,
        )

        async def review_one(ref: dict) -> list[dict]:
            async with semaphore:
                await graphs.service.report_node_item_progress(
                    dependencies.progress_callback,
                    "review_chapter_coverage",
                    ref["chapter_id"],
                    "running",
                    total_count = total_count,
                )
                result = await service.review_chapter_coverage(
                    dependencies,
                    {**graph_state, "chapter_ref": ref},
                )
                await graphs.service.report_node_item_progress(
                    dependencies.progress_callback,
                    "review_chapter_coverage",
                    ref["chapter_id"],
                    "done",
                    total_count = total_count,
                )
                return result.get("reviewed_chapters", [])

        reviewed_batches = await asyncio.gather(*(review_one(ref) for ref in refs))
        reviewed = []
        for batch in reviewed_batches:
            reviewed.extend(batch)
        return {"reviewed_chapters": reviewed}

    return await graphs.service.run_node(
        dependencies.progress_callback,
        "review_chapter_coverage",
        execute,
        controls = dependencies.controls,
    )
