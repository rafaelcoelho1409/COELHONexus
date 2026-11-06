"""State of the chapter_propose subgraph."""
from __future__ import annotations

import operator
from typing import Annotated, NotRequired, TypedDict


class Sketch(TypedDict):
    """What one section analyst proposed for its section of the documentation (`proposals` is None when it failed)."""

    section: str
    n_pages: int
    proposals: list[dict] | None
    error: NotRequired[str]


class Outline(TypedDict):
    """One architect attempt: the chapters it proposed and the corpus's evidence score for them."""

    attempt: int
    proposals: list[dict]
    scored: dict


class SectionInput(TypedDict):
    """What one `analyze_section` branch is sent."""

    section: str
    keys: list[str]
    target: int
    framework_slug: str


class ProposeState(TypedDict, total=False):
    framework_slug: str
    thread_id: str
    raw_files: list[str]
    relevant_files: list[str]
    doc_distill_ref: str | None
    chapter_proposals_ref: str | None
    propose_stats: dict

    finished: bool                  # answered without the steps below (no pages, or the node's blob is current)
    manifest_hash: str
    shards: list[dict]              # [{"label", "keys"}]: the sections the analysts read; empty = the architect reads the corpus
    target: int
    attempt: int                    # architect attempts made so far
    architect_errors: list[str]
    started: float
    sketches: Annotated[list[Sketch], operator.add]
    outlines: Annotated[list[Outline], operator.add]
