"""Step 4 — digest_construct: LangGraph node shell.

LLM-assigned source-to-section routing (LLMxMapReduce-V3 arXiv
2510.10890 + IterSurvey arXiv 2510.21900 paper-card schema). Replaces
the deprecated Phase B cosine routing.

All orchestration lives in service.digest_construct_run.

State writes:
  digest_path  — MinIO key of the ChapterDigest blob (latest pointer)
  digest_stats — coverage stats + counts + cache_hit
"""
from __future__ import annotations

from ..observability import traced
from ..state import SynthState

from .service import digest_construct_run


@traced("digest_construct")
async def digest_construct(state: SynthState) -> dict:
    return await digest_construct_run(state)
