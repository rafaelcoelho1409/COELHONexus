from __future__ import annotations

import os

DISTILL_EXECUTION_ENABLED = os.environ.get("BD_DISTILL_EXECUTION_ENABLED", "false").strip().lower() in {
    "1",
    "true",
    "yes",
}

JSON_TYPE = "application/json"
HTML_TYPE = "text/html; charset=utf-8"
PDF_TYPE = "application/pdf"
MARKDOWN_TYPE = "text/markdown; charset=utf-8"
MAX_SOURCE_UNIT_CHARS = 9000
MAX_LEARNING_UNITS_PER_CHUNK = 8
MAX_CHAPTER_UNITS_PER_REQUEST = 8
MAX_BOOK_SYNTHESIS_BATCH_SIZE = 8
GRAPH_CONCURRENCY = 8
FANOUT_UNIT_BATCH_SIZE = 16
FANOUT_CHAPTER_BATCH_SIZE = 4
GRAPH_NODES = ("prepare_units", "extract_unit", "prepare_chapters", "synthesize_chapter", "synthesize_book", "publish_report")
TASK_NAME = "domains.bd.distill.task.run_distill"
TERMINAL_STATUSES = frozenset({"done", "failed", "cancelled"})
