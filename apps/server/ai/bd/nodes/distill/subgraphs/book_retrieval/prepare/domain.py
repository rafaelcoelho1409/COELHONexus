"""Build deterministic retrieval queries and replay inputs."""
from __future__ import annotations

from typing import Any


_PERSISTED_FIELDS = (
    "query",
    "include_web",
    "elasticsearch_result",
    "qdrant_result",
    "neo4j_result",
    "web_search_result",
    "evidence",
    "source_status",
    "source_queries",
)


def prepare_retrieval_query(
    state: dict[str, Any],
    *,
    research_enabled: bool,
    max_query_chars: int,
    source_names: tuple[str, ...],
    persisted_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create the bounded book query and source selection for this run."""
    title = str(state.get("title") or "Book").strip()
    section_titles = []
    for chapter in state.get("report_chapters", []):
        if not isinstance(chapter, dict):
            continue
        path = chapter.get("section_path")
        if isinstance(path, list):
            label = " / ".join(str(part).strip() for part in path if str(part).strip())
            if label and label not in section_titles:
                section_titles.append(label)

    query = (
        f"Book: {title}. Retrieve passages that best explain its central thesis, recurring principles, "
        "and connections across chapters."
    )
    if section_titles:
        query += f" Chapter map: {'; '.join(section_titles[:16])}."

    output = {
        "query": query[:max_query_chars],
        "include_web": bool(research_enabled),
    }
    replay_target = state.get("replay_target_node")
    replay_targets = {*source_names, "fuse_evidence"}
    if replay_target in replay_targets:
        if isinstance(persisted_context, dict):
            output.update({
                key: persisted_context[key]
                for key in _PERSISTED_FIELDS
                if key in persisted_context
            })
        output["retrieval_sources_to_run"] = (
            [replay_target] if replay_target in source_names else []
        )
    else:
        output["retrieval_sources_to_run"] = list(source_names)
    return output
