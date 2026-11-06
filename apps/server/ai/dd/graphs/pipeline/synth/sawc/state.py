"""State of the sawc subgraph."""
from __future__ import annotations

import operator
from typing import Annotated, NotRequired, Optional, TypedDict


class WriteTask(TypedDict):
    """One section for a writer to write (or write again)."""

    sid: str
    budget: NotRequired[int]        # vault characters in the writer's request (a late retry uses a lighter bank)


class WriteResult(TypedDict):
    sid: str
    attempts: int
    placeholder: bool


class ExtendResult(TypedDict):
    sid: str
    added: int
    outcome: str                    # 'ok' | 'failed' (every call raised: worth a salvage pass) | 'empty' (answered, nothing valid)


class SawcState(TypedDict, total=False):
    framework_slug: str
    chapter_id: str
    thread_id: str
    synth_mode: str
    # Read from the chapter's state: the CoRefine loop's bookkeeping.
    refine_iter: Optional[int]
    checklist_stats: Optional[dict]
    best_seen_score: Optional[float]
    best_seen_sawc_path: Optional[str]
    # What the node answers.
    sawc_path: Optional[str]
    sawc_stats: Optional[dict]
    prev_checklist_score: Optional[float]
    status: Optional[str]
    error: Optional[str]

    finished: bool                  # answered without the steps below (a failure, or the stage's blob is current)
    started: float
    manifest_hash: str
    versioned_key: str
    latest_key: str
    n_stages: int
    stages: list[list[str]]         # section ids per stage, in reading order
    stage_pos: int                  # the next stage to write; == len(stages) is the late retry
    phase: str                      # 'stage' | 'late'
    todo: list[WriteTask]
    n_drafts: int                   # writer runs so far (every attempt of every section)
    carried_prev_score: Optional[float]
    incoming_best_score: Optional[float]
    incoming_best_path: Optional[str]
    refine_iter_now: int

    # Coverage top-up: rounds 1..TOPUP_ROUNDS, then a salvage pass and one more round for what the salvage gained.
    topup_cursor: int
    topup_done: bool
    extends_seen: int               # how many of `extends` earlier rounds already accounted for
    topup_exhausted: bool
    topup_failed: list[str]
    topup_empty: list[str]
    topup_gained_late: list[str]
    topup_added: int
    topup_touched: list[str]
    extend_todo: list[str]

    writes: Annotated[list[WriteResult], operator.add]
    extends: Annotated[list[ExtendResult], operator.add]
