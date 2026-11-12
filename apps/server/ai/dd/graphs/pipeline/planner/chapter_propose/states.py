"""State of the chapter_propose subgraph."""
from __future__ import annotations
from . import entities

import operator
from typing import Annotated, TypedDict


class ProposeState(TypedDict, total = False):
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
    sketches: Annotated[list[entities.Sketch], operator.add]
    outlines: Annotated[list[entities.Outline], operator.add]
