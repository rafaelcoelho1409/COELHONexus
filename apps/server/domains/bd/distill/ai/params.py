from __future__ import annotations

import os

JSON_TYPE = "application/json"
MARKDOWN_TYPE = "text/markdown; charset=utf-8"
MAX_SOURCE_UNIT_CHARS = 9000
MAX_LEARNING_UNITS_PER_CHUNK = 8
MAX_CHAPTER_UNITS_PER_REQUEST = 8
MAX_BOOK_SYNTHESIS_BATCH_SIZE = 8
MAX_BOOK_SYNTHESIS_CONTEXT_CITATION_IDS = 12
BOOK_CITATION_FIELDS = frozenset({
    "central_thesis_learning_unit_ids",
    "learning_unit_ids",
    "overview_learning_unit_ids",
    "recommendation_learning_unit_ids",
})
# Chapter synthesis can return a large structured guide. Keep one chapter request
# in flight per run; the shared Chat adapter also enforces a cross-worker limit.
CHAPTER_SYNTHESIS_CONCURRENCY = 1
CHAPTER_SYNTHESIS_SETTLE_CAP_S = 130.0
# The DeepAgent applies ChapterSynthesis response_format and gets one bounded
# correction invocation if its final structured response is still invalid.
CHAPTER_STRUCTURED_OUTPUT_ATTEMPTS = 2
# One graph-owned retry for transient provider errors after the first request.
CHAPTER_SYNTHESIS_NODE_RETRY_ATTEMPTS = 2
# Kept as the explicit zero-nested-provider-retry contract for graph-bound synthesis.
CHAPTER_SYNTHESIS_PROVIDER_ATTEMPTS = 1
CHAPTER_VISUAL_REPAIR_ATTEMPTS = 1
BOOK_AGENT_RESPONSE_ATTEMPTS = 4
BOOK_SYNTHESIS_MAX_TOKENS = 3800
BOOK_SYNTHESIS_TIMEOUT_S = 240.0
RESEARCH_ENABLED = os.environ.get("BD_DISTILL_RESEARCH_ENABLED", "true").casefold() in {"1", "true", "yes"}
# Keep LLM fan-out bounded; high parallelism caused repeated upstream 504s on long book runs.
GRAPH_CONCURRENCY = 4
# Keep extraction batches compact; chapter work gets its own graph task for per-chapter scheduling and progress.
FANOUT_UNIT_BATCH_SIZE = 8
FANOUT_CHAPTER_BATCH_SIZE = 1
# Provider-backed graph nodes retry at the graph boundary; inner calls make one provider attempt.
AGENT_CALL_ATTEMPTS = 1
AGENT_CALL_RETRY_WAIT_BASE_S = 5.0
# Optional external research catches failures and falls back inside its node, so it owns a small retry loop.
OPTIONAL_RESEARCH_PROVIDER_ATTEMPTS = 3
# Chapter coverage review is optional; report a visible fallback rather than
# holding downstream report stages open through repeated provider retries.
COVERAGE_REVIEW_HEARTBEAT_S = 15.0
COVERAGE_REVIEW_CONCURRENCY = 2
COVERAGE_REVIEW_MAX_CORRECTIONS = 2
COVERAGE_REVIEW_AREAS = frozenset({"coverage", "evidence", "consistency", "clarity"})
COVERAGE_CORRECTOR_RESPONSE_ATTEMPTS = 2
RUN_EXECUTION_LOCK_LEASE_S = 300
RUN_EXECUTION_LOCK_RENEW_S = 30
RUN_EXECUTION_LOCK_RETRY_S = 15
TRANSIENT_NODE_RETRY_ATTEMPTS = 3
TRANSIENT_NODE_RETRY_INITIAL_INTERVAL_S = 5.0
TRANSIENT_NODE_RETRY_MAX_INTERVAL_S = 30.0
# Shared graph node ID for compilation, graph metadata, and checkpoint/replay routing.
PARALLEL_DISPATCH_NODE = "dispatch_parallel_stages"
RETRIEVAL_SOURCE_NODES = ("elasticsearch", "qdrant", "neo4j", "web_search")
RETRIEVAL_RESULT_KEY_BY_SOURCE = {
    "elasticsearch": "elasticsearch_result",
    "qdrant": "qdrant_result",
    "neo4j": "neo4j_result",
    "web_search": "web_search_result",
}
RETRIEVAL_QUERY_MAX_CHARS = 1200
RETRIEVAL_PROMPT_EVIDENCE_MAX_ITEMS = 8
RETRIEVAL_PROMPT_EVIDENCE_MAX_CHARS = 1200
LEGACY_GRAPH_NODE_ID_ALIASES = {
    "search_mcp": "web_search",
    "report_pipeline": "prepare_retrieval",
}
REPORT_STAGE_NODES = frozenset({
    "prepare_retrieval",
    "elasticsearch",
    "qdrant",
    "neo4j",
    "web_search",
    "fuse_evidence",
    "synthesize_book",
    "research_context",
    "write_decision_brief",
})
PARALLEL_STAGE_REPLAY_NODES = REPORT_STAGE_NODES | {"build_chapter_visuals"}

# Nodes exposed by the root LangGraph. The leaf stages live inside their subgraphs.
LANGGRAPH_NODES = (
    "unit_extraction",
    "chapter_guides",
    PARALLEL_DISPATCH_NODE,
    "build_chapter_visuals",
    "book_retrieval",
    "book_report",
    "publish_report",
)
LANGGRAPH_EDGES = (
    ("unit_extraction", "chapter_guides"),
    ("chapter_guides", PARALLEL_DISPATCH_NODE),
    (PARALLEL_DISPATCH_NODE, "build_chapter_visuals"),
    (PARALLEL_DISPATCH_NODE, "book_retrieval"),
    (PARALLEL_DISPATCH_NODE, "book_report"),
    ("book_retrieval", "book_report"),
    ("book_report", "publish_report"),
    ("build_chapter_visuals", "publish_report"),
)
SUBGRAPH_NODE_MEMBERS = {
    "unit_extraction": ("prepare_units", "extract_unit", "verify_unit"),
    "chapter_guides": ("prepare_chapters", "synthesize_chapter", "review_chapter_coverage"),
    "book_retrieval": ("prepare_retrieval", *RETRIEVAL_SOURCE_NODES, "fuse_evidence"),
    "book_report": ("synthesize_book", "research_context", "write_decision_brief"),
}
SUBGRAPH_BY_GRAPH_NODE = {
    graph_node: subgraph
    for subgraph, graph_nodes in SUBGRAPH_NODE_MEMBERS.items()
    for graph_node in graph_nodes
}

GRAPH_NODES = (
    "prepare_units",
    "extract_unit",
    "verify_unit",
    "prepare_chapters",
    "synthesize_chapter",
    "review_chapter_coverage",
    "build_chapter_visuals",
    "prepare_retrieval",
    "elasticsearch",
    "qdrant",
    "neo4j",
    "web_search",
    "fuse_evidence",
    "synthesize_book",
    "research_context",
    "write_decision_brief",
    "publish_report",
)
GRAPH_EDGES = (
    ("prepare_units", "extract_unit"),
    ("extract_unit", "verify_unit"),
    ("verify_unit", "prepare_chapters"),
    ("prepare_chapters", "synthesize_chapter"),
    ("synthesize_chapter", "review_chapter_coverage"),
    ("review_chapter_coverage", "build_chapter_visuals"),
    ("review_chapter_coverage", "prepare_retrieval"),
    ("prepare_retrieval", "elasticsearch"),
    ("prepare_retrieval", "qdrant"),
    ("prepare_retrieval", "neo4j"),
    ("prepare_retrieval", "web_search"),
    ("elasticsearch", "fuse_evidence"),
    ("qdrant", "fuse_evidence"),
    ("neo4j", "fuse_evidence"),
    ("web_search", "fuse_evidence"),
    ("fuse_evidence", "synthesize_book"),
    ("synthesize_book", "research_context"),
    ("research_context", "write_decision_brief"),
    ("build_chapter_visuals", "publish_report"),
    ("write_decision_brief", "publish_report"),
)
LLM_COUNTER_NODES_BY_GRAPH_NODE = {
    "prepare_units": (),
    "extract_unit": ("learning_extractor",),
    "verify_unit": ("evidence_verifier",),
    "prepare_chapters": (),
    "synthesize_chapter": ("chapter_synthesizer",),
    "review_chapter_coverage": ("report_reviewer", "chapter_coverage_corrector"),
    "build_chapter_visuals": ("visual_artifact_builder",),
    "prepare_retrieval": (),
    "elasticsearch": ("distill_retrieval_elasticsearch",),
    "qdrant": ("distill_retrieval_qdrant",),
    "neo4j": ("distill_retrieval_neo4j",),
    "web_search": ("distill_retrieval_web_search",),
    "fuse_evidence": (),
    "synthesize_book": ("book_synthesizer",),
    "write_decision_brief": ("decision_brief_writer",),
    "research_context": ("research_analyst",),
    "publish_report": ("report_reviewer",),
}


def _reachable_nodes(start: str) -> tuple[str, ...]:
    reachable = {start}
    pending = [start]
    while pending:
        source = pending.pop()
        for edge_source, target in GRAPH_EDGES:
            if edge_source == source and target not in reachable:
                reachable.add(target)
                pending.append(target)
    return tuple(node for node in GRAPH_NODES if node in reachable)


# Replay checks all transitive dependencies, then resets only this node and its actual descendants.
REPLAY_DESCENDANTS = {node: _reachable_nodes(node) for node in GRAPH_NODES}
REPLAY_PREDECESSORS = {
    node: tuple(
        candidate
        for candidate in GRAPH_NODES
        if node in REPLAY_DESCENDANTS[candidate] and candidate != node
    )
    for node in GRAPH_NODES
}
TASK_NAME = "domains.bd.distill.task.run_distill"
TERMINAL_STATUSES = frozenset({"done", "failed", "cancelled"})
