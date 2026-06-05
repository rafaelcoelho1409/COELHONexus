"""Step 5 — sawc_write: LangGraph node shell.

Stage-Aware Writer-Critic — for each outline stage, fans out N drafts per
section (best-of-N), runs a critic-picker, and writes the chosen draft +
section memory. v2 cookbook schema (subtopics with 1:1 prose↔code).

All orchestration lives in service.sawc_write_run.

State writes:
  sawc_path  — MinIO key of the ChapterDraft (latest pointer)
  sawc_stats — observability dict (writer/critic deployments + counts)
"""
from __future__ import annotations

from ..observability import traced
from ..state import SynthState

from .service import sawc_write_run


@traced("sawc_write")
async def sawc_write(state: SynthState) -> dict:
    return await sawc_write_run(state)
