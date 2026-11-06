"""State of the chapter_assign subgraph."""
from __future__ import annotations

import operator
from typing import Annotated, TypedDict


class ScoreResult(TypedDict):
    """One page's chapter confidences (`scores` is None for a page with no content at all); `fallback` marks a lexical stand-in."""

    key: str
    scores: list[dict] | None
    fallback: bool
    round: int


class ScoreInput(TypedDict):
    """What one `score_page` branch is sent."""

    key: str
    digest: str
    framework_slug: str
    doc_path: str
    round: int
    total: int


class AssignState(TypedDict, total=False):
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
    results: Annotated[list[ScoreResult], operator.add]
