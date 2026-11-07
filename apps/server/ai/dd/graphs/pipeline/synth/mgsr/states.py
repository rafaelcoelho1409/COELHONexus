"""State of the mgsr subgraph."""
from __future__ import annotations

from typing import Optional, TypedDict


class MgsrState(TypedDict, total = False):
    framework_slug: str
    chapter_id: str
    thread_id: str
    refine_iter: Optional[int]
    mgsr_path: Optional[str]
    mgsr_stats: Optional[dict]
    status: Optional[str]
    error: Optional[str]

    finished: bool                  # answered without the steps below (a failure, or the stage's blob is current)
    trivial: bool                   # the chapter passed: no review
    started: float
    manifest_hash: str
    versioned_key: str
    latest_key: str
    checklist_manifest_hash: str
    outline_manifest_hash: str
    chapter_title: str
    pass_rate: float
    chapter_passed: bool
    failed_names: list[str]
    n_failed: int
    decision: Optional[dict]
    deployment: Optional[str]
