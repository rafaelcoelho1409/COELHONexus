"""Bounded Qdrant indexing limits for published Learning Guides."""
from __future__ import annotations

import os


RAG_EXECUTION_ENABLED = os.environ.get("BD_RAG_EXECUTION_ENABLED", "false").strip().lower() in {
    "1",
    "true",
    "yes",
}
COLLECTION_PREFIX = "bd-rag"
CHUNK_SIZE = 1800
CHUNK_OVERLAP = 240
EMBED_BATCH_SIZE = 50
SPARSE_MODEL_NAME = "Qdrant/bm25"
GRAPH_WRITE_BATCH_SIZE = 500
GRAPH_EVIDENCE_MAX_ITEMS = 80
GRAPH_CHAPTER_CONTEXT_MAX_CHARS = 24000
INDEX_LOCK_TIMEOUT_S = 21_600
STALE_JOB_AFTER_S = 1800
TASK_NAME = "domains.bd.rag.task.index_book"
