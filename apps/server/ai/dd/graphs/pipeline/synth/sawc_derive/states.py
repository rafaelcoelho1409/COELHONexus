"""State of the sawc_derive subgraph."""
from __future__ import annotations

import operator
import typing


class DeriveState(typing.TypedDict, total = False):
    framework_slug: str
    chapter_id: str
    thread_id: str
    derive_stats: typing.Optional[dict]

    finished: bool                  # answered without the steps below (disabled, nothing to derive, or a failure)
    started: float
    n_candidates: int
    attempts: typing.Annotated[list[dict], operator.add]     # one DeriveAttempt (as a dict) per derived subtopic
