"""checklist_eval — schema + prompt cache-invalidation markers.

Bundle 9 (2026-05-25): position-bias mitigation via per-chapter
deterministic shuffle of criterion order. Prompt body varies per chapter
so systematic primacy/recency bias averages out across the corpus
without breaking caching (order is reproducible from chapter_id).
"""
from __future__ import annotations


CHECKLIST_SCHEMA_VERSION = "1.0"
CHECKLIST_PROMPT_VERSION = "v2-2026-05-25"
