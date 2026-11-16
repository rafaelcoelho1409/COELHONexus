from __future__ import annotations


PAGE_AUDIT_CONCURRENCY = 16
PAGE_REPAIR_CONCURRENCY = 8
MAX_METADATA_SOURCE_PAGES = 3
MAX_METADATA_SOURCE_CHARS = 12000
MAX_PAGE_MARKDOWN_CHARS = 30000
REPAIR_CONTEXT_CHARS_PER_ISSUE = 1800
MAX_REPAIR_CONTEXT_CHARS = 10000
AUTO_APPLY_CONFIDENCE = 0.98
VERIFIED_PATCH_MIN_CONFIDENCE = 0.9
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
PROCESSED_BOOK_READ_CONCURRENCY = 16
CONTENTS_RECONCILIATION_BATCH_SIZE = 20
CONTENTS_RECONCILIATION_MIN_BATCH_SIZE = 10
AGENTIC_CANCEL_POLL_INTERVAL_SECONDS = 1.0
SOURCE_PDF_VERIFY_MAX_BYTES = 48 * 1024 * 1024
SOURCE_PAGE_IMAGE_SCALE = 1.6
SOURCE_PAGE_IMAGE_MAX_DIMENSION = 1800
SOURCE_PAGE_IMAGE_JPEG_QUALITY = 82
VISUAL_ENRICHMENT_CONCURRENCY = 4
VISUAL_FIGURE_MAX_BYTES = 8 * 1024 * 1024
VISUAL_CONTEXT_CHARS = 1600
TASK_NAME = "domains.bd.ingestion.ai.task.run_ingestion_ai"
AUTO_RUN_ID_PREFIX = "auto-"
GRAPH_NODES = (
    "audit_batch",
    "classify_content",
    "plan_repairs",
    "repair_pages",
    "normalize_metadata",
    "enrich_visuals",
    "materialize_processed_book",
    "reconcile_contents",
    "publish",
)
REPLAY_DESCENDANTS = {
    node: GRAPH_NODES[index:]
    for index, node in enumerate(GRAPH_NODES)
}
