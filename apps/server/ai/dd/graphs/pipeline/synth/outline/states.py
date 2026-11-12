"""State of the outline subgraph."""
from __future__ import annotations
from . import entities

from typing import Optional, TypedDict


class OutlineState(TypedDict, total = False):
    framework_slug: str
    chapter_id: str
    thread_id: str
    outline_path: Optional[str]
    outline_stats: Optional[dict]
    status: Optional[str]
    error: Optional[str]

    finished: bool                  # answered without the steps below (a failure, or the stage's blob is current)
    started: float
    manifest_hash: str
    versioned_key: str
    latest_key: str
    sources: list[str]
    chapter_title: str
    chapter_description: str
    n_vault_hashes: int
    truncated: bool
    adaptive_target: int
    attempt: int                    # outliner runs so far
    half: bool                      # the next run sees a halved source index (the last one overflowed the context)
    feedback: list[str]             # what the best outline so far still gets wrong: the next run's brief
    best: Optional[entities.Candidate]
    last_error: str
    n_checks: int                   # validate_outline calls that reported issues, summed over the runs
