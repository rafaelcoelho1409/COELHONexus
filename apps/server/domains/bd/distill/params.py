from __future__ import annotations

JSON_TYPE = "application/json"
HTML_TYPE = "text/html; charset=utf-8"
PDF_TYPE = "application/pdf"
MARKDOWN_TYPE = "text/markdown; charset=utf-8"
MAX_SOURCE_UNIT_CHARS = 9000
MAX_LEARNING_UNITS_PER_CHUNK = 8
MAX_CHAPTER_UNITS_PER_REQUEST = 8
MAX_BOOK_SYNTHESIS_BATCH_SIZE = 8
# Chapter synthesis can return a large structured guide and routinely outlives the
# shared chat adapter's 30-second default. Match the long-running Distill stage to
# the Rotator's supported wall budget while limiting its simultaneous requests.
CHAPTER_SYNTHESIS_CONCURRENCY = 2
CHAPTER_SYNTHESIS_TIMEOUT_S = 240.0
CHAPTER_SYNTHESIS_SETTLE_CAP_S = 130.0
CHAPTER_SCHEMA_REPAIR_ATTEMPTS = 1
CHAPTER_TEXT_FALLBACK_ATTEMPTS = 2
CHAPTER_CITATION_REPAIR_ATTEMPTS = 1
CHAPTER_VISUAL_REPAIR_ATTEMPTS = 1
BOOK_SCHEMA_REPAIR_ATTEMPTS = 1
BOOK_TEXT_FALLBACK_ATTEMPTS = 2
BOOK_SYNTHESIS_TIMEOUT_S = 240.0
# Keep LLM fan-out bounded; high parallelism caused repeated upstream 504s on long book runs.
GRAPH_CONCURRENCY = 4
FANOUT_UNIT_BATCH_SIZE = 8
FANOUT_CHAPTER_BATCH_SIZE = 4
# Retry transient provider failures at the graph node boundary as well as within each model call.
TRANSIENT_NODE_RETRY_ATTEMPTS = 3
TRANSIENT_NODE_RETRY_INITIAL_INTERVAL_S = 5.0
TRANSIENT_NODE_RETRY_MAX_INTERVAL_S = 30.0
GRAPH_NODES = ("prepare_units", "extract_unit", "prepare_chapters", "synthesize_chapter", "synthesize_book", "publish_report")
TASK_NAME = "domains.bd.distill.task.run_distill"
TERMINAL_STATUSES = frozenset({"done", "failed", "cancelled"})
