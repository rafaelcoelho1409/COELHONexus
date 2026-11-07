"""State of the checklist subgraph."""
from __future__ import annotations
from . import entities

import operator
from typing import Annotated, Optional, TypedDict


class ChecklistState(TypedDict, total = False):
    framework_slug: str
    chapter_id: str
    thread_id: str
    # Read from the chapter's state.
    sawc_stats: Optional[dict]
    best_seen_score: Optional[float]
    best_seen_pregate: Optional[int]
    best_seen_sawc_path: Optional[str]
    consecutive_infra_degraded: Optional[int]
    # What the node answers.
    checklist_path: Optional[str]
    checklist_stats: Optional[dict]
    status: Optional[str]
    error: Optional[str]

    finished: bool                  # answered without the steps below (a failure, or the stage's blob is current)
    started: float
    manifest_hash: str
    versioned_key: str
    latest_key: str
    chapter_title: str
    sawc_manifest_hash: str
    digest_manifest_hash: str
    pre_results: list[dict]         # the deterministic gates' criteria
    judge: Optional[entities.JudgeRun]       # the first judge run
    opinions: Annotated[list[entities.JudgeRun], operator.add]      # the second-opinion runs
    merged: Optional[list[dict]]    # the per-criterion majority of the runs (when there were opinions)
