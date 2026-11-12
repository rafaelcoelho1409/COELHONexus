"""State of the digest subgraph."""
from __future__ import annotations
from . import entities

import operator
from typing import Annotated, Optional, TypedDict


class DigestState(TypedDict, total = False):
    framework_slug: str
    chapter_id: str
    thread_id: str
    digest_path: Optional[str]
    digest_stats: Optional[dict]
    status: Optional[str]
    error: Optional[str]

    finished: bool                  # answered without the steps below (a failure, or the stage's blob is current)
    started: float
    manifest_hash: str
    versioned_key: str
    latest_key: str
    outline_manifest_hash: str
    chapter_title: str
    items: list[entities.DigestItem]
    todo: list[entities.DigestItem]          # items still to route (the first wave, then the ones that failed)
    wave: int
    results: Annotated[list[entities.DigestResult], operator.add]
