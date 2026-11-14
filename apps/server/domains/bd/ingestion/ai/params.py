from __future__ import annotations


PAGE_AUDIT_CONCURRENCY = 16
PAGE_REPAIR_CONCURRENCY = 8
MAX_METADATA_SOURCE_PAGES = 3
MAX_METADATA_SOURCE_CHARS = 12000
MAX_PAGE_MARKDOWN_CHARS = 30000
REPAIR_CONTEXT_CHARS_PER_ISSUE = 1800
MAX_REPAIR_CONTEXT_CHARS = 10000
AUTO_APPLY_CONFIDENCE = 0.98
SPACING_REPAIR_CONFIDENCE = 0.8
METADATA_ACCEPT_CONFIDENCE = 0.85
# A specialist checks its patches itself (`check_patches`), so a second full attempt only covers a failed or unreadable answer.
SPECIALIST_ATTEMPTS = 2
PAGE_AUDIT_ATTEMPTS = 3
# LLM counters of a one-page audit run outside a book run (the auditor evaluation) are billed to this id.
PAGE_AUDIT_EVALUATION_RUN_ID = "page-audit-evaluation"
AGENT_MODEL_CALL_LIMIT = 4
# Each tool call costs one model turn: one batched check (twice, if a patch is fixed), a lookup, then the structured answer.
# 1.12 checked patches one by one and a page with eight URL repairs ran out of turns (6 pages in its first run).
SPECIALIST_MODEL_CALL_LIMIT = 8
MAX_TERM_VARIANTS = 8
MAX_TERM_CHARS = 80
EDGE_LINES_MAX = 8
EDGE_LINE_CHARS = 300
# Findings a second reader checks before they reach review: these codes are judgement calls, and so is low confidence.
VERIFIED_CODES = ("structure", "other")
VERIFY_BELOW_CONFIDENCE = 0.8
MAX_VERIFIER_CONTEXT_CHARS = 3000
# Publication check: a page may lose no more letters and digits than this (the larger of the two) between source and output.
VERIFY_LOSS_MIN_CHARS = 12
VERIFY_LOSS_RATIO = 0.01
VERIFY_CONCURRENCY = 16
CONTENT_CLASSIFICATION_CONCURRENCY = 8
PROCESSED_BOOK_READ_CONCURRENCY = 16
CONTENTS_RECONCILIATION_BATCH_SIZE = 40
CONTENT_CLASSIFICATION_EXCERPT_CHARS = 8000
CONTENT_EXCLUSION_CONFIDENCE = 0.94
TASK_NAME = "domains.bd.ingestion.ai.task.run_ingestion_ai"
AUTO_RUN_ID_PREFIX = "auto-"
GRAPH_NODES = (
    "audit_batch",
    "plan_repairs",
    "repair_pages",
    "normalize_metadata",
    "classify_content",
    "materialize_processed_book",
    "reconcile_contents",
    "publish",
)
