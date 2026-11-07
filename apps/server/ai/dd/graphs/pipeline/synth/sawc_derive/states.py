"""State of the sawc_derive subgraph."""
from __future__ import annotations

import operator
from typing import Annotated, Optional, TypedDict


class DeriveState(TypedDict, total = False):
    framework_slug: str
    chapter_id: str
    thread_id: str
    derive_stats: Optional[dict]

    finished: bool                  # answered without the steps below (disabled, nothing to derive, or a failure)
    started: float
    n_candidates: int
    attempts: Annotated[list[dict], operator.add]     # one DeriveAttempt (as a dict) per derived subtopic
