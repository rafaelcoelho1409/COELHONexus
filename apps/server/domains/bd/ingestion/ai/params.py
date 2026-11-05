from __future__ import annotations


PAGE_AUDIT_CONCURRENCY = 4
PAGE_REPAIR_CONCURRENCY = 4
MAX_METADATA_SOURCE_PAGES = 3
MAX_METADATA_SOURCE_CHARS = 12000
MAX_PAGE_MARKDOWN_CHARS = 30000
REPAIR_CONTEXT_CHARS_PER_ISSUE = 1800
MAX_REPAIR_CONTEXT_CHARS = 10000
AUTO_APPLY_CONFIDENCE = 0.98
TASK_NAME = "domains.bd.ingestion.ai.task.run_ingestion_ai"
AUTO_RUN_ID_PREFIX = "auto-"
GRAPH_NODES = (
    "audit_batch",
    "plan_repairs",
    "repair_pages",
    "normalize_metadata",
    "publish",
)
