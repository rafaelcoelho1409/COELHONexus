"""State of the sawc subgraph."""
from __future__ import annotations
from . import entities

import operator
import typing


class SawcState(typing.TypedDict, total = False):
    framework_slug: str
    chapter_id: str
    thread_id: str
    synth_mode: str
    # Read from the chapter's state: the CoRefine loop's bookkeeping.
    refine_iter: typing.Optional[int]
    checklist_stats: typing.Optional[dict]
    best_seen_score: typing.Optional[float]
    best_seen_sawc_path: typing.Optional[str]
    # What the node answers.
    sawc_path: typing.Optional[str]
    sawc_stats: typing.Optional[dict]
    prev_checklist_score: typing.Optional[float]
    status: typing.Optional[str]
    error: typing.Optional[str]

    finished: bool                  # answered without the steps below (a failure, or the stage's blob is current)
    started: float
    manifest_hash: str
    versioned_key: str
    latest_key: str
    n_stages: int
    stages: list[list[str]]         # section ids per stage, in reading order
    stage_pos: int                  # the next stage to write; == len(stages) is the late retry
    phase: str                      # 'stage' | 'late'
    todo: list[entities.WriteTask]
    n_drafts: int                   # writer runs so far (every attempt of every section)
    carried_prev_score: typing.Optional[float]
    incoming_best_score: typing.Optional[float]
    incoming_best_path: typing.Optional[str]
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

    writes: typing.Annotated[list[entities.WriteResult], operator.add]
    extends: typing.Annotated[list[entities.ExtendResult], operator.add]
