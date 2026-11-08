"""Bounded Qdrant indexing limits for published Learning Guides."""
from __future__ import annotations

COLLECTION_PREFIX = "bd-distill"
CHUNK_SIZE = 1800
CHUNK_OVERLAP = 240
EMBED_BATCH_SIZE = 50
SPARSE_MODEL_NAME = "Qdrant/bm25"
INDEX_LOCK_TIMEOUT_S = 21_600
STALE_JOB_AFTER_S = 1800
TASK_NAME = "domains.bd.distill.rag.task.index_distill_run"
