"""Synthesize a source-grounded chapter learning guide."""
from __future__ import annotations
import domains
from . import service
from .... import graphs

from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from langgraph.types import Send


def prepare_parallel_stages(_state: graphs.distill.states.DistillRunState) -> dict:
    """Create a checkpoint boundary before the visual and report branches fan out."""
    return {}


def dispatch_parallel_stages(
    state: graphs.distill.states.DistillRunState,
    config: RunnableConfig,
) -> list[Send]:
    """Start the visual and report subgraphs together after coverage review."""
    refs = state.get("reviewed_chapters", [])
    if not refs:
        raise ValueError("chapter coverage review produced no guide artifacts")
    size = domains.bd.distill.ai.params.FANOUT_CHAPTER_BATCH_SIZE
    identity = domains.bd.distill.ai.domain.graph_identity(state)
    configurable = config.get("configurable") or {}
    replay_target_node = configurable.get("distill_replay_target")
    if replay_target_node not in domains.bd.distill.ai.params.GRAPH_NODES:
        replay_target_node = None
    report_payload = {
        **identity,
        "report_chapters": refs,
        "replay_target_node": replay_target_node,
    }
    title = state.get("title")
    if isinstance(title, str) and title:
        report_payload["title"] = title
    for key in ("book_synthesis_key", "research_context_key", "decision_brief_key"):
        value = state.get(key)
        if isinstance(value, str) and value:
            report_payload[key] = value
    visual_tasks = [
        Send(
            "build_chapter_visuals",
            {
                **identity,
                "chapter_batch": refs[index:index + size],
                "replay_target_node": replay_target_node,
            },
        )
        for index in range(0, len(refs), size)
    ]
    if not visual_tasks:
        visual_tasks.append(Send(
            "build_chapter_visuals",
            {**identity, "chapter_batch": [], "replay_target_node": replay_target_node},
        ))
    # Queue the report branch first so it can start promptly when a book has many chapters.
    return [
        Send("report_pipeline", report_payload),
        *visual_tasks,
    ]


async def synthesize_chapter(
    graph_state: graphs.distill.states.DistillRunState,
    runtime: Runtime[graphs.distill.entities.DistillDependencies],
) -> dict:
    dependencies = runtime.context

    async def synthesize_one(
        chapter_state: dict,
        learning_units_by_id: dict[str, dict] | None,
    ) -> dict:
        chapter_id = chapter_state["chapter_ref"]["chapter_id"]
        await graphs.service.report_node_item_progress(
            dependencies.progress_callback,
            "synthesize_chapter",
            chapter_id,
            "running",
        )
        result = await service.synthesize_chapter(
            dependencies,
            chapter_state,
            learning_units_by_id = learning_units_by_id,
        )
        await graphs.service.report_node_item_progress(
            dependencies.progress_callback,
            "synthesize_chapter",
            chapter_id,
            "done",
        )
        return result

    return await graphs.service.run_node(
        dependencies.progress_callback,
        "synthesize_chapter",
        lambda: domains.bd.distill.ai.synthesize.service.synthesize_chapter_batch(
            dependencies.storage,
            graph_state,
            synthesize_one,
        ),
        controls = dependencies.controls,
    )
