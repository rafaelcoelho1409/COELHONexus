"""Step 3 — outline_sdp: LangGraph node shell.

SurveyGen-I Structure-Driven Planner (arXiv 2508.14317 §3.1). One LLM
call per chapter produces a ChapterOutline with typed prerequisites,
from which a DAG is derived deterministically (longest-path stage
indices for downstream parallelization).

All orchestration lives in service.outline_sdp_run.

State writes:
  outline_path  — MinIO key of the ChapterOutline + OutlineDAG blob
  outline_stats — counts + DAG shape + cache_hit + wall_ms
"""
from __future__ import annotations

from ..observability import traced
from ..state import SynthState

from .service import outline_sdp_run


@traced("outline_sdp")
async def outline_sdp(state: SynthState) -> dict:
    return await outline_sdp_run(state)
