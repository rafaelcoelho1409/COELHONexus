"""State of the render subgraph."""
from __future__ import annotations

import operator
from typing import Annotated, Optional, TypedDict


class RenderState(TypedDict, total=False):
    framework_slug: str
    chapter_id: str
    thread_id: str
    best_seen_sawc_path: Optional[str]
    chapter_path: Optional[str]
    chapter_stats: Optional[dict]
    status: Optional[str]
    error: Optional[str]

    finished: bool                  # answered without the steps below (a failure, or the stage's blob is current)
    started: float
    manifest_hash: str
    versioned_key: str
    latest_key: str
    sawc_manifest_hash: str
    mgsr_manifest_hash: str
    chapter_title: str
    todo: list[str]                 # vault hashes whose code is still to be normalized
    normalized: Annotated[list[dict], operator.add]
