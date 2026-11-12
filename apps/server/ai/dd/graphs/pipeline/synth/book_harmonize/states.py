"""State of the book_harmonize subgraph."""
from __future__ import annotations

import operator
import typing


class HarmonizeState(typing.TypedDict, total = False):
    framework_slug: str
    framework_name: str
    chapter_ids: list[str]
    thread_id: str
    result: typing.Optional[dict]

    started: float
    finished: bool                  # answered without the steps below (too few chapters, or no claims at all)
    canonical_terms: list[dict]
    patch_todo: list[dict]          # {chapter_id, paragraph, violations}: one paragraph edit each
    extracted: typing.Annotated[list[dict], operator.add]         # {chapter_id, claims, terms}
    detections: typing.Annotated[list[dict], operator.add]        # {chapter_id, violations, summary}
    edits: typing.Annotated[list[dict], operator.add]             # {chapter_id, paragraph, text | None}
