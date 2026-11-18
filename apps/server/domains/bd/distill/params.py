from __future__ import annotations

import os

JSON_TYPE = "application/json"
HTML_TYPE = "text/html; charset=utf-8"
PDF_TYPE = "application/pdf"
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
# Chapter synthesis can return a large structured guide and routinely outlives the
# shared chat adapter's 30-second default. Match the long-running Distill stage to
# the Rotator's supported wall budget while limiting its simultaneous requests.
CHAPTER_SYNTHESIS_CONCURRENCY = 2
CHAPTER_SYNTHESIS_TIMEOUT_S = 240.0
CHAPTER_SYNTHESIS_SETTLE_CAP_S = 130.0
CHAPTER_AGENT_RESPONSE_ATTEMPTS = 3
CHAPTER_CITATION_REPAIR_ATTEMPTS = 1
CHAPTER_VISUAL_REPAIR_ATTEMPTS = 1
BOOK_AGENT_RESPONSE_ATTEMPTS = 3
BOOK_SYNTHESIS_MAX_TOKENS = 3800
BOOK_SYNTHESIS_TIMEOUT_S = 240.0
RESEARCH_ENABLED = os.environ.get("BD_DISTILL_RESEARCH_ENABLED", "false").casefold() in {"1", "true", "yes"}
# Keep LLM fan-out bounded; high parallelism caused repeated upstream 504s on long book runs.
GRAPH_CONCURRENCY = 4
FANOUT_UNIT_BATCH_SIZE = 8
FANOUT_CHAPTER_BATCH_SIZE = 4
# Retry transient provider failures at the graph node boundary as well as within each model call.
AGENT_CALL_ATTEMPTS = 3
AGENT_CALL_RETRY_WAIT_BASE_S = 5.0
TRANSIENT_NODE_RETRY_ATTEMPTS = 3
TRANSIENT_NODE_RETRY_INITIAL_INTERVAL_S = 5.0
TRANSIENT_NODE_RETRY_MAX_INTERVAL_S = 30.0
GRAPH_NODES = (
    "prepare_units",
    "extract_unit",
    "verify_unit",
    "prepare_chapters",
    "synthesize_chapter",
    "build_chapter_visuals",
    "synthesize_book",
    "write_decision_brief",
    "research_context",
    "publish_report",
)
LLM_COUNTER_NODES_BY_GRAPH_NODE = {
    "prepare_units": (),
    "extract_unit": ("learning_extractor",),
    "verify_unit": ("evidence_verifier",),
    "prepare_chapters": (),
    "synthesize_chapter": ("chapter_synthesizer",),
    "build_chapter_visuals": ("visual_artifact_builder",),
    "synthesize_book": ("book_synthesizer",),
    "write_decision_brief": ("decision_brief_writer",),
    "research_context": ("research_analyst",),
    "publish_report": ("report_reviewer",),
}
REPLAY_DESCENDANTS = {node: GRAPH_NODES[index:] for index, node in enumerate(GRAPH_NODES)}
TASK_NAME = "domains.bd.distill.task.run_distill"
TERMINAL_STATUSES = frozenset({"done", "failed", "cancelled"})
