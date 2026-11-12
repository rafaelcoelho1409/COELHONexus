"""State of the checklist subgraph."""
from __future__ import annotations
from . import entities

import operator
import typing


class ChecklistState(typing.TypedDict, total = False):
    framework_slug: str
    chapter_id: str
    thread_id: str
    # Read from the chapter's state.
    sawc_stats: typing.Optional[dict]
    best_seen_score: typing.Optional[float]
    best_seen_pregate: typing.Optional[int]
    best_seen_sawc_path: typing.Optional[str]
    consecutive_infra_degraded: typing.Optional[int]
    # What the node answers.
    checklist_path: typing.Optional[str]
    checklist_stats: typing.Optional[dict]
    status: typing.Optional[str]
    error: typing.Optional[str]

    finished: bool                  # answered without the steps below (a failure, or the stage's blob is current)
    started: float
    manifest_hash: str
    versioned_key: str
    latest_key: str
    chapter_title: str
    sawc_manifest_hash: str
    digest_manifest_hash: str
    pre_results: list[dict]         # the deterministic gates' criteria
    judge: typing.Optional[entities.JudgeRun]       # the first judge run
    opinions: typing.Annotated[list[entities.JudgeRun], operator.add]      # the second-opinion runs
    merged: typing.Optional[list[dict]]    # the per-criterion majority of the runs (when there were opinions)
