"""Create the report and visual branches after chapter guide review."""
from __future__ import annotations
import domains
from ...... import graphs

from langchain_core.runnables import RunnableConfig
from langgraph.types import Send


def prepare_parallel_stages(_state: graphs.distill.book_distillation.states.DistillRunState) -> dict:
    """Create a checkpoint boundary before the report and visual branches fan out."""
    return {}


def dispatch_parallel_stages(
    state: graphs.distill.book_distillation.states.DistillRunState,
    config: RunnableConfig,
) -> list[Send]:
    """Dispatch chapter visuals alongside the appropriate report pipeline entry."""
    refs = state.get("reviewed_chapters", [])
    if not refs:
        raise ValueError("chapter coverage review produced no guide artifacts")
    size = domains.bd.distill.ai.params.FANOUT_CHAPTER_BATCH_SIZE
    identity = domains.bd.distill.ai.domain.graph_identity(state)
    configurable = config.get("configurable") or {}
    replay_target_node = configurable.get("distill_replay_target")
    if replay_target_node not in domains.bd.distill.ai.params.GRAPH_NODES:
        replay_target_node = None
    if replay_target_node in {
        "build_chapter_visuals",
        "synthesize_book",
        "research_context",
        "write_decision_brief",
    }:
        report_entry_node = "book_report"
    else:
        report_entry_node = "book_retrieval"
    report_payload = {
        **identity,
        "report_chapters": refs,
        "synthesized_chapters": refs,
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
    return [Send(report_entry_node, report_payload), *visual_tasks]
