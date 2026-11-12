"""State of the chapter_propose subgraph."""
from __future__ import annotations
from . import entities

import operator
import typing


class ProposeState(typing.TypedDict, total = False):
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
    sketches: typing.Annotated[list[entities.Sketch], operator.add]
    outlines: typing.Annotated[list[entities.Outline], operator.add]
