"""State of the chapter_select subgraph."""
from __future__ import annotations
from . import entities

import operator
from typing import Annotated, TypedDict


class SelectState(TypedDict, total = False):
    framework_slug: str
    thread_id: str
    chapter_proposals_ref: str | None
    chapter_doc_assignments_ref: str | None
    chapter_plan_ref: str | None
    select_stats: dict

    finished: bool                  # answered without the steps below (missing inputs)
    manifest_hash: str
    round: int
    plans: list[entities.SplitPlanItem]      # chapters the next round of curators splits
    started: float
    outcomes: Annotated[list[entities.SplitOutcome], operator.add]
