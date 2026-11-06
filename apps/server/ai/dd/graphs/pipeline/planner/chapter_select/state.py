"""State of the chapter_select subgraph."""
from __future__ import annotations

import operator
from typing import Annotated, NotRequired, TypedDict


class SplitOutcome(TypedDict):
    """What the curator did with one oversize chapter: the chapters that replace it, or None (the chapter stays as it is)."""

    round: int
    pos: int
    chapters: list[dict] | None
    reason: NotRequired[str]


class CurateInput(TypedDict):
    """What one `curate_chapter` branch is sent: the chapter's position in the plan and how many parts to carve."""

    pos: int
    n_parts: int
    round: int
    framework_slug: str


class SplitPlanItem(TypedDict):
    pos: int
    n_parts: int


class SelectState(TypedDict, total=False):
    framework_slug: str
    thread_id: str
    chapter_proposals_ref: str | None
    chapter_doc_assignments_ref: str | None
    chapter_plan_ref: str | None
    select_stats: dict

    finished: bool                  # answered without the steps below (missing inputs)
    manifest_hash: str
    round: int
    plans: list[SplitPlanItem]      # chapters the next round of curators splits
    started: float
    outcomes: Annotated[list[SplitOutcome], operator.add]
