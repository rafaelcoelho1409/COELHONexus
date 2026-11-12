"""State of the doc_distill subgraph."""
from __future__ import annotations
from . import entities

import operator
import typing


class DistillState(typing.TypedDict, total = False):
    framework_slug: str
    thread_id: str
    raw_files: list[str]
    relevant_files: list[str]
    doc_distill_ref: str | None
    doc_distill_stats: dict

    finished: bool                  # answered without the steps below (no pages, or the node's blob is current)
    manifest_hash: str
    digests: dict[str, str]         # page key → digest of (prompt version, key, stored size)
    cached: dict[str, dict]         # page key → distillate stored by an earlier run
    todo: list[str]                 # page keys still to distill
    started: float
    results: typing.Annotated[list[entities.DistillResult], operator.add]
