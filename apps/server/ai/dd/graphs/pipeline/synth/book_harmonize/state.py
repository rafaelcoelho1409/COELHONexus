"""State of the book_harmonize subgraph."""
from __future__ import annotations

import operator
from typing import Annotated, Optional, TypedDict


class HarmonizeState(TypedDict, total=False):
    framework_slug: str
    framework_name: str
    chapter_ids: list[str]
    thread_id: str
    result: Optional[dict]

    started: float
    finished: bool                  # answered without the steps below (too few chapters, or no claims at all)
    canonical_terms: list[dict]
    patch_todo: list[dict]          # {chapter_id, paragraph, violations}: one paragraph edit each
    extracted: Annotated[list[dict], operator.add]         # {chapter_id, claims, terms}
    detections: Annotated[list[dict], operator.add]        # {chapter_id, violations, summary}
    edits: Annotated[list[dict], operator.add]             # {chapter_id, paragraph, text | None}
