"""State of the chapter_select subgraph."""
from __future__ import annotations
from . import entities

import operator
import typing


class SelectState(typing.TypedDict, total = False):
    framework_slug: str
    thread_id: str
    chapter_proposals_ref: str | None
    chapter_doc_assignments_ref: str | None
    planner_version: str
    page_cards_ref: str | None
    relevant_files: list[str]
    chapter_plan_ref: str | None
    select_stats: dict

    finished: bool                  # answered without the steps below (missing inputs)
    manifest_hash: str
    round: int
    plans: list[entities.SplitPlanItem]      # chapters the next round of curators splits
    started: float
    outcomes: typing.Annotated[list[entities.SplitOutcome], operator.add]
