"""State of the chapter_assign subgraph."""
from __future__ import annotations
from . import entities

import operator
from typing import Annotated, TypedDict


class AssignState(TypedDict, total = False):
    framework_slug: str
    thread_id: str
    raw_files: list[str]
    relevant_files: list[str]
    chapter_proposals_ref: str | None
    chapter_doc_assignments_ref: str | None
    assign_stats: dict

    finished: bool                  # answered without the steps below (missing inputs, or the node's blob is current)
    manifest_hash: str
    n_proposals: int
    digests: dict[str, str]         # page key → digest of everything its scores depend on
    paths: dict[str, str]           # page key → URL path (evidence of its topic)
    cached: dict[str, list[dict]]   # page key → scores stored by an earlier run
    todo: list[str]                 # page keys still to score
    sweep: list[str]                # pages that ended on the lexical fallback, scored once more after a settle
    swept: bool
    started: float
    results: Annotated[list[entities.ScoreResult], operator.add]
