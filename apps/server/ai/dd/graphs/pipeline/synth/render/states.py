"""State of the render subgraph."""
from __future__ import annotations

import operator
import typing


class RenderState(typing.TypedDict, total = False):
    framework_slug: str
    chapter_id: str
    thread_id: str
    best_seen_sawc_path: typing.Optional[str]
    chapter_path: typing.Optional[str]
    chapter_stats: typing.Optional[dict]
    status: typing.Optional[str]
    error: typing.Optional[str]

    finished: bool                  # answered without the steps below (a failure, or the stage's blob is current)
    started: float
    manifest_hash: str
    versioned_key: str
    latest_key: str
    sawc_manifest_hash: str
    mgsr_manifest_hash: str
    chapter_title: str
    todo: list[str]                 # vault hashes whose code is still to be normalized
    normalized: typing.Annotated[list[dict], operator.add]
